#!/usr/bin/env python3
"""Emit one text-free, read-only receipt proving all Day Zero queues empty."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request


TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ENV_BYTES = 64 * 1024
TIMER_UNIT = "sonsteng-prod-release.timer"
USER_AGENT = "sonsteng-queue-proof/1.0"
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ALLOWED_LEDGER_ORIGINS = frozenset(
    {"https://sonsteng-chat.damienriehl.workers.dev"}
)
REVIEW_ENVELOPE_KEYS = frozenset({"ok", "items"})


class ProofError(RuntimeError):
    """A bounded proof failure whose details must never enter the receipt."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _protected_env(path, required_keys, *, missing_ok=False):
    """Read required values from an owned, regular, mode-0600 environment file."""
    target = pathlib.Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ProofError("environment-unavailable") from None
    except OSError as exc:
        raise ProofError("environment-unavailable") from exc
    try:
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ProofError("environment-unavailable")
            raw = source.read(MAX_ENV_BYTES + 1)
    except OSError as exc:
        raise ProofError("environment-unavailable") from exc
    if len(raw) > MAX_ENV_BYTES:
        raise ProofError("environment-unavailable")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ProofError("environment-unavailable") from exc

    found = {}
    for source_line in text.splitlines():
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProofError("environment-malformed")
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_NAME_RE.fullmatch(key):
            raise ProofError("environment-malformed")
        if key in required_keys:
            if key in found:
                raise ProofError("environment-malformed")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            found[key] = value
    if set(found) != set(required_keys) or any(not found[key] for key in required_keys):
        raise ProofError("environment-unavailable")
    return found


def _api_coordinates(ledger_origin):
    if ledger_origin not in ALLOWED_LEDGER_ORIGINS:
        raise ProofError("ledger-origin-invalid")
    api_base = ledger_origin + "/edit/v1"
    return (
        api_base + "/review",
        api_base + "/prod/releases/frontier",
        ledger_origin.removeprefix("https://"),
    )


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProofError("http-response-malformed")
        result[key] = value
    return result


def _get_json(url, bearer, opener, *, review=False):
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + bearer,
        "User-Agent": USER_AGENT,
    }
    if review:
        headers["X-Edit-Request"] = "1"
    try:
        request = urllib.request.Request(url, method="GET", headers=headers)
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (
        TimeoutError,
        http.client.HTTPException,
        urllib.error.HTTPError,
        urllib.error.URLError,
        ValueError,
        OSError,
    ) as exc:
        raise ProofError("http-unavailable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProofError("http-response-too-large")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProofError("http-response-malformed") from exc
    if not isinstance(payload, dict):
        raise ProofError("http-response-malformed")
    return payload


def _review_counts(payload):
    # reviewJsonEndpoint() returns exactly {ok:true,items:[...]}. Its backing
    # listAll() call is unpaginated, so any continuation marker or alternate
    # list key is an incomplete/contradictory response, not a contract variant.
    if set(payload) != REVIEW_ENVELOPE_KEYS:
        raise ProofError("review-response-malformed")
    if payload.get("ok") is not True:
        raise ProofError("review-rejected")
    rows = payload["items"]
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ProofError("review-response-malformed")
    counts = {"pending": 0, "accepted": 0, "other_non_terminal": 0}
    for row in rows:
        if not isinstance(row, dict):
            raise ProofError("review-response-malformed")
        status = row.get("status")
        if not isinstance(status, str) or not status:
            raise ProofError("review-response-malformed")
        if status == "pending":
            counts["pending"] += 1
        elif status == "accepted":
            counts["accepted"] += 1
        else:
            # No unrecognized row can disappear into a clean proof. The live
            # list currently returns only active statuses, but a new/unknown
            # value remains conservatively non-terminal until reviewed.
            counts["other_non_terminal"] += 1
    return counts


def _frontier_summary(payload):
    if payload.get("ok") is not True or not isinstance(payload.get("context"), dict):
        raise ProofError("frontier-response-malformed")
    context = payload["context"]
    if "active_release" not in context or "batches" not in context:
        raise ProofError("frontier-response-malformed")
    batches = context["batches"]
    active_release = context["active_release"]
    if (
        not isinstance(batches, list)
        or len(batches) > 1000
        or any(not isinstance(batch, dict) for batch in batches)
        or (active_release is not None and not isinstance(active_release, dict))
    ):
        raise ProofError("frontier-response-malformed")
    releases = [] if active_release is None else [{"present": True}]
    if active_release is not None:
        reason = "active_release"
    elif context.get("blocked_reason") is not None:
        reason = "blocked"
    elif batches:
        reason = "ready_to_prepare"
    else:
        reason = "unprepared"
    summary = {"queue_count": len(batches), "reason": reason, "releases": releases}
    return summary, reason == "unprepared"


def timer_state(run=subprocess.run):
    """Read timer state without mutating the unit."""
    try:
        enabled = run(
            ["systemctl", "--user", "is-enabled", TIMER_UNIT],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        active = run(
            ["systemctl", "--user", "is-active", TIMER_UNIT],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"active": None, "available": False, "enabled": None}
    enabled_text = enabled.stdout.strip()
    active_text = active.stdout.strip()
    enabled_valid = (enabled.returncode, enabled_text) in {
        (0, "enabled"),
        (1, "disabled"),
    }
    active_valid = (active.returncode, active_text) in {
        (0, "active"),
        (3, "inactive"),
    }
    if not enabled_valid or not active_valid:
        return {"active": None, "available": False, "enabled": None}
    return {
        "active": active_text == "active",
        "available": True,
        "enabled": enabled_text == "enabled",
    }


def prove(
    ledger_origin,
    apply_env_file,
    observer_env_file,
    *,
    opener,
    run_systemctl,
):
    timer = timer_state(run_systemctl)
    receipt = {
        "all_queues_empty": False,
        "apply": {"accepted": None},
        "editor_review": {
            "accepted": None,
            "other_non_terminal": None,
            "pending": None,
        },
        "ledger_host": None,
        "publication": "unproved",
        "publication_fallback": None,
        "publication_frontier": None,
        "timer": timer,
    }
    try:
        review_url, frontier_url, ledger_host = _api_coordinates(
            ledger_origin
        )
        receipt["ledger_host"] = ledger_host
        apply_env = _protected_env(apply_env_file, {"EDIT_SERVICE_TOKEN"})
        review_payload = _get_json(
            review_url, apply_env["EDIT_SERVICE_TOKEN"], opener, review=True
        )
        counts = _review_counts(review_payload)
        receipt["editor_review"] = counts
        receipt["apply"] = {"accepted": counts["accepted"]}

        observer_env = _protected_env(
            observer_env_file,
            {"SONSTENG_PROD_OBSERVER_BEARER"},
            missing_ok=True,
        )
        if observer_env is not None:
            frontier_payload = _get_json(
                frontier_url,
                observer_env["SONSTENG_PROD_OBSERVER_BEARER"],
                opener,
            )
            frontier, publication_empty = _frontier_summary(frontier_payload)
            receipt["publication"] = "observer-frontier"
            receipt["publication_frontier"] = frontier
        else:
            receipt["publication"] = "observer-env-absent"
            timer_off = (
                timer["available"] is True
                and timer["enabled"] is False
                and timer["active"] is False
            )
            receipt["publication_fallback"] = (
                "systemd timer disabled and inactive"
                if timer_off
                else "systemd timer not proved off"
            )
            publication_empty = timer_off

        receipt["all_queues_empty"] = (
            all(value == 0 for value in counts.values()) and publication_empty
        )
    except ProofError as exc:
        receipt["proof_error"] = str(exc)
    return receipt


def main(
    argv=None,
    *,
    opener=None,
    run_systemctl=subprocess.run,
    stdout=None,
):
    parser = argparse.ArgumentParser(
        description="Emit a text-free proof that all Day Zero queues are empty"
    )
    parser.add_argument("--ledger-origin", required=True)
    parser.add_argument("--apply-env-file", required=True)
    parser.add_argument("--observer-env-file", required=True)
    args = parser.parse_args(argv)
    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect).open
    receipt = prove(
        args.ledger_origin,
        args.apply_env_file,
        args.observer_env_file,
        opener=opener,
        run_systemctl=run_systemctl,
    )
    print(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        file=stdout or sys.stdout,
    )
    return 0 if receipt["all_queues_empty"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
