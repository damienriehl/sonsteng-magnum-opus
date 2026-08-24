#!/usr/bin/env python3
"""Text-free, read-only readiness report for Publisher release review."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import stat
import subprocess

from prod_release_executor import ObserverError, ReleaseObserverHTTP


SHA_RE = re.compile(r"[0-9a-f]{40}")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}")
OBSERVER_ENV_KEY = "SONSTENG_PROD_OBSERVER_BEARER"
TIMER_UNIT = "sonsteng-prod-release.timer"
SAFE_HASH_KEYS = ("base_sha", "candidate_sha", "manifest_hash", "membership_hash",
                  "evidence_hash", "review_receipt_hash", "projection_identity")


def _bounded_id(value):
    return value if isinstance(value, str) and ID_RE.fullmatch(value) else None


def _bounded_hash(value):
    return value if isinstance(value, str) and 1 <= len(value) <= 256 and \
        re.fullmatch(r"[A-Za-z0-9._:-]+", value) else None


def _nonnegative_counts(value):
    if not isinstance(value, dict):
        raise ObserverError("readiness counts malformed")
    result = {}
    for key,item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", key) or \
                not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise ObserverError("readiness counts malformed")
        result[key] = item
    return result


def _release_summary(release):
    if not isinstance(release, dict):
        raise ObserverError("readiness release malformed")
    result = {"id":_bounded_id(release.get("id")),
              "state":_bounded_id(release.get("state"))}
    if not result["id"] or not result["state"]:
        raise ObserverError("readiness release malformed")
    for key in SAFE_HASH_KEYS:
        if release.get(key) is not None:
            value = _bounded_hash(release.get(key))
            if value is None:
                raise ObserverError("readiness release malformed")
            result[key] = value
    return result


def timer_state(run=subprocess.run):
    """Read systemd state only; never start, stop, enable, or disable a unit."""
    try:
        enabled = run(["systemctl","--user","is-enabled",TIMER_UNIT],
            capture_output=True,text=True,timeout=5,check=False).stdout.strip()
        active = run(["systemctl","--user","is-active",TIMER_UNIT],
            capture_output=True,text=True,timeout=5,check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"available":False,"enabled":None,"active":None}
    return {"available":True,"enabled":enabled == "enabled","active":active == "active"}


def inspect_readiness(observer, *, release_enabled, timer):
    """Return only bounded identifiers, hashes, counts, and local safety state."""
    try:
        context = observer.preparation_context()
        audit = observer.audit()
        if audit.get("schema_version") != 1:
            raise ObserverError("readiness audit malformed")
        counts = _nonnegative_counts(audit.get("counts"))
        invariants = _nonnegative_counts(audit.get("invariants"))
        active_rows = audit.get("active_releases")
        batches = context.get("batches", [])
        if not isinstance(active_rows, list) or len(active_rows) > 20 or \
                not isinstance(batches, list) or len(batches) > 1000:
            raise ObserverError("readiness queue malformed")
        releases = []
        for row in active_rows:
            row_summary = _release_summary(row)
            releases.append(_release_summary(observer.get_release(row_summary["id"])))
        context_active = context.get("active_release")
        if context_active is not None:
            context_id = _release_summary(context_active)["id"]
            if context_id not in {item["id"] for item in releases}:
                raise ObserverError("readiness active release mismatch")
        elif releases:
            raise ObserverError("readiness active release mismatch")
        queue = []
        for batch in batches:
            if not isinstance(batch, dict):
                raise ObserverError("readiness queue malformed")
            batch_id = _bounded_id(batch.get("batch_id"))
            commit_sha = batch.get("commit_sha")
            suggestions = batch.get("suggestion_ids", [])
            if not batch_id or not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha) or \
                    not isinstance(suggestions, list):
                raise ObserverError("readiness queue malformed")
            queue.append({"batch_id":batch_id,"commit_sha":commit_sha,
                          "member_count":len(suggestions)})
        base_sha = context.get("base_sha")
        if base_sha is not None and (not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha)):
            raise ObserverError("readiness frontier malformed")
        timer_safe = timer.get("available") is True and timer.get("active") is False and \
            timer.get("enabled") is False
        invariant_safe = all(value == 0 for value in invariants.values())
        if release_enabled:
            reason = "config_on"
        elif not timer_safe:
            reason = "timer_not_proved_off"
        elif not invariant_safe:
            reason = "invariant_failure"
        elif releases:
            reason = "active_release"
        elif not queue:
            reason = "unprepared"
        else:
            reason = "ready_to_prepare"
        return {"ready":reason == "ready_to_prepare","reason":reason,
            "config_off":not release_enabled,"timer":timer,"counts":counts,
            "invariants":invariants,"frontier_sha":base_sha,"queue":queue,
            "queue_count":len(queue),"releases":releases}
    except ObserverError as exc:
        return {"ready":False,"reason":str(exc),"config_off":not release_enabled,
                "timer":timer,"counts":{},"invariants":{},"frontier_sha":None,
                "queue":[],"queue_count":0,"releases":[]}


def load_observer_bearer(path):
    """Load one observer token from an owned, regular 0600 file."""
    target = pathlib.Path(path)
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ObserverError("observer environment file must be owned regular mode 0600")
    values = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ObserverError("observer environment file malformed")
        key,value = line.split("=",1)
        if key != OBSERVER_ENV_KEY or key in values:
            raise ObserverError("observer environment file contains unsupported keys")
        values[key] = value
    token = values.get(OBSERVER_ENV_KEY, "")
    if not token or len(token) > 4096:
        raise ObserverError("observer credential unavailable")
    return token


def read_release_enabled(path):
    """Read only the config-off flag from the protected production env file."""
    target = pathlib.Path(path)
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ObserverError("production environment file must be owned regular mode 0600")
    found = []
    with target.open(encoding="utf-8") as source:
        for raw in source:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            prefix = "SONSTENG_PROD_RELEASE_ENABLED="
            if line.startswith(prefix):
                found.append(line[len(prefix):])
    if len(found) != 1 or found[0] not in {"true","false"}:
        raise ObserverError("production config-off state is ambiguous")
    return found[0] == "true"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Publisher readiness report")
    parser.add_argument("--ledger-url", required=True)
    parser.add_argument("--observer-env-file", required=True)
    parser.add_argument("--prod-env-file", required=True)
    args = parser.parse_args(argv)
    # Refuse ambient mutation authority: the command must use only its dedicated
    # observer file and can construct only the three GETs in ReleaseObserverHTTP.
    if os.environ.get("SONSTENG_PROD_RELEASE_BEARER") or os.environ.get("EDIT_SERVICE_TOKEN"):
        print(json.dumps({"ready":False,"reason":"mutation credential present"},sort_keys=True))
        return 2
    try:
        observer = ReleaseObserverHTTP(args.ledger_url,
                                       load_observer_bearer(args.observer_env_file))
        result = inspect_readiness(observer,
            release_enabled=read_release_enabled(args.prod_env_file),
            timer=timer_state())
    except (ObserverError, OSError, UnicodeError):
        result = {"ready":False,"reason":"observer configuration unavailable"}
    print(json.dumps(result,sort_keys=True,separators=(",",":")))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
