#!/usr/bin/env python3
"""Emit one text-free, read-only receipt proving all Day Zero queues empty."""
from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request


TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ENV_BYTES = 64 * 1024
MAX_FRONTIER_ITEMS = 1000
MAX_OPERATION_COUNT = 100_000
MAX_BOUND_VALUE_BYTES = 256
TIMER_UNIT = "sonsteng-prod-release.timer"
APPLY_TIMER_UNIT = "sonsteng-apply.timer"
USER_AGENT = "sonsteng-queue-proof/1.0"
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WINDOW_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
CONTENT_LENGTH_RE = re.compile(r"(?:0|[1-9][0-9]*)")
ALLOWED_LEDGER_ORIGINS = frozenset(
    {"https://sonsteng-chat.damienriehl.workers.dev"}
)
REVIEW_ENVELOPE_KEYS = frozenset({"ok", "items"})
FRONTIER_ENVELOPE_KEYS = frozenset({"ok", "context"})
OPERATION_FRONTIER_KEYS = frozenset(
    {"pending_operation_count", "blocked_state"}
)
OPERATION_BLOCKED_STATES = frozenset({"unblocked", "blocked"})
ACTIVE_RELEASE_REQUIRED_KEYS = frozenset(
    {
        "id",
        "state",
        "target_batch_id",
        "base_sha",
        "candidate_sha",
        "generator_id",
        "evidence_hash",
        "manifest_hash",
        "membership_hash",
        "schema_version",
    }
)
ACTIVE_RELEASE_V2_KEYS = frozenset(
    {"review_receipt_hash", "projection_identity"}
)
BATCH_KEYS = frozenset(
    {"batch_id", "commit_sha", "generator_id", "member_count"}
)


def _resolve_systemctl_path():
    """Resolve systemctl once from the OS-defined trusted utility path."""
    try:
        system_path = os.confstr("CS_PATH")
    except (AttributeError, OSError, ValueError):
        return None
    if not system_path or any(
        not entry or not os.path.isabs(entry)
        for entry in system_path.split(os.pathsep)
    ):
        return None
    candidate = shutil.which("systemctl", path=system_path)
    if candidate is None:
        return None
    try:
        return str(pathlib.Path(candidate).resolve(strict=True))
    except OSError:
        return None


SYSTEMCTL_PATH = _resolve_systemctl_path()


class ProofError(RuntimeError):
    """A bounded proof failure whose details must never enter the receipt."""


class _ArgumentsInvalid(RuntimeError):
    """A private, bounded command-line parsing failure."""


class _ProofArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise _ArgumentsInvalid from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _protected_env(path, required_keys, *, missing_ok=False):
    """Read required values from an owned, regular, mode-0600 environment file."""
    target = pathlib.Path(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
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


def _response_call(callback):
    """Bound failures from one external response-lifecycle operation."""
    try:
        return callback()
    except (ProofError, AssertionError):
        raise
    except (
        TimeoutError,
        http.client.HTTPException,
        urllib.error.HTTPError,
        urllib.error.URLError,
        ValueError,
        OSError,
    ) as exc:
        raise ProofError("http-unavailable") from exc
    except Exception as exc:
        raise ProofError("http-response-lifecycle") from exc


def _declared_content_length(headers):
    if not isinstance(headers, list) or not headers:
        raise ProofError("http-response-framing-invalid")

    content_lengths = []
    transfer_encodings = []
    content_encodings = []
    content_ranges = []
    for header in headers:
        if not isinstance(header, tuple) or len(header) != 2:
            raise ProofError("http-response-framing-invalid")
        name, value = header
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise ProofError("http-response-framing-invalid")
        normalized_name = name.casefold()
        if normalized_name == "content-length":
            content_lengths.append(value)
        elif normalized_name == "transfer-encoding":
            transfer_encodings.append(value)
        elif normalized_name == "content-encoding":
            content_encodings.append(value)
        elif normalized_name == "content-range":
            content_ranges.append(value)

    # This proof accepts only an identity body with one explicit length. That
    # excludes ambiguous duplicate lengths, chunked framing, and transforms
    # whose wire length would not describe the JSON bytes being validated.
    if (
        transfer_encodings
        or content_encodings
        or content_ranges
        or len(content_lengths) != 1
    ):
        raise ProofError("http-response-framing-invalid")
    value = content_lengths[0]
    if not value or not CONTENT_LENGTH_RE.fullmatch(value):
        raise ProofError("http-response-framing-invalid")
    if len(value) > len(str(MAX_RESPONSE_BYTES)):
        raise ProofError("http-response-too-large")
    declared_length = int(value)
    if declared_length > MAX_RESPONSE_BYTES:
        raise ProofError("http-response-too-large")
    return declared_length


def _read_response(manager):
    response = _response_call(lambda: manager.__enter__())
    try:
        status = _response_call(lambda: response.getcode())
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or status != 200
        ):
            raise ProofError("http-status-invalid")
        headers = _response_call(lambda: list(response.getheaders()))
        declared_length = _declared_content_length(headers)
        if isinstance(response, http.client.HTTPResponse):
            wire_body = response.fp
            if wire_body is None:
                raise ProofError("http-response-framing-invalid")
        else:
            # Injectable test responses have no transport stream. Production
            # responses use HTTPResponse.fp so Content-Length cannot clip the
            # EOF observation.
            wire_body = response
        if isinstance(response, http.client.HTTPResponse):
            chunks = []
            remaining = declared_length
            while remaining:
                chunk = _response_call(lambda: wire_body.read(remaining))
                if not isinstance(chunk, bytes):
                    raise ProofError("http-response-malformed")
                if not chunk:
                    raise ProofError("http-response-framing-invalid")
                if len(chunk) > remaining:
                    raise ProofError("http-response-framing-invalid")
                chunks.append(chunk)
                remaining -= len(chunk)
            eof_probe = _response_call(lambda: wire_body.read(1))
            if not isinstance(eof_probe, bytes):
                raise ProofError("http-response-malformed")
            if eof_probe:
                raise ProofError("http-response-framing-invalid")
            raw = b"".join(chunks)
        else:
            raw = _response_call(
                lambda: wire_body.read(MAX_RESPONSE_BYTES + 1)
            )
        if not isinstance(raw, bytes):
            raise ProofError("http-response-malformed")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProofError("http-response-too-large")
        if len(raw) != declared_length:
            raise ProofError("http-response-framing-invalid")
    except BaseException:
        exception_type, exception, traceback = sys.exc_info()
        try:
            _response_call(
                lambda: manager.__exit__(exception_type, exception, traceback)
            )
        except ProofError:
            # Preserve the original verifier failure or defect. A secondary
            # context-exit failure must not replace it.
            pass
        raise
    else:
        _response_call(lambda: manager.__exit__(None, None, None))
    return raw


def _get_json(url, bearer, opener, *, review=False):
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + bearer,
        "Connection": "close",
        "User-Agent": USER_AGENT,
    }
    if review:
        headers["X-Edit-Request"] = "1"
    request = _response_call(
        lambda: urllib.request.Request(url, method="GET", headers=headers)
    )
    manager = _response_call(
        lambda: opener(request, timeout=TIMEOUT_SECONDS)
    )
    raw = _read_response(manager)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (
        UnicodeError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
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


def _bounded_string(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_BOUND_VALUE_BYTES
    except UnicodeError:
        return False


def _valid_active_release(release):
    if not isinstance(release, dict):
        return False
    schema_version = release.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        return False
    expected_keys = ACTIVE_RELEASE_REQUIRED_KEYS
    if schema_version == 2:
        expected_keys |= ACTIVE_RELEASE_V2_KEYS
    elif schema_version != 1:
        return False
    if set(release) != expected_keys:
        return False
    return all(
        _bounded_string(release[key])
        for key in expected_keys - {"schema_version"}
    )


def _valid_batch(batch):
    return (
        isinstance(batch, dict)
        and set(batch) == BATCH_KEYS
        and all(
            _bounded_string(batch[key])
            for key in ("batch_id", "commit_sha", "generator_id")
        )
        and isinstance(batch["member_count"], int)
        and not isinstance(batch["member_count"], bool)
        and 0 <= batch["member_count"] <= 100_000
    )


def _operation_frontier(context):
    if "operation_frontier" not in context:
        raise ProofError("operation-frontier-missing")
    frontier = context["operation_frontier"]
    if not isinstance(frontier, dict) or set(frontier) != OPERATION_FRONTIER_KEYS:
        raise ProofError("operation-frontier-malformed")
    pending = frontier["pending_operation_count"]
    blocked_state = frontier["blocked_state"]
    if (
        not isinstance(pending, int)
        or isinstance(pending, bool)
        or not 0 <= pending <= MAX_OPERATION_COUNT
        or not isinstance(blocked_state, str)
        or blocked_state not in OPERATION_BLOCKED_STATES
    ):
        raise ProofError("operation-frontier-malformed")
    return {
        "blocked_state": blocked_state,
        "pending_operation_count": pending,
    }


def _frontier_summary(payload):
    if (
        set(payload) != FRONTIER_ENVELOPE_KEYS
        or payload.get("ok") is not True
        or not isinstance(payload.get("context"), dict)
    ):
        raise ProofError("frontier-response-malformed")
    context = payload["context"]
    operation_frontier = _operation_frontier(context)
    if "active_release" not in context or "batches" not in context:
        raise ProofError("frontier-response-malformed")
    batches = context["batches"]
    active_release = context["active_release"]
    if (
        not isinstance(batches, list)
        or len(batches) > MAX_FRONTIER_ITEMS
        or any(not _valid_batch(batch) for batch in batches)
    ):
        raise ProofError("frontier-response-malformed")

    common_keys = {"active_release", "batches", "operation_frontier"}
    if active_release is not None:
        valid_variant = (
            set(context) == common_keys
            and not batches
            and _valid_active_release(active_release)
        )
        reason = "active_release"
    elif "blocked_reason" in context or "blocked_batch_id" in context:
        valid_variant = (
            set(context)
            == common_keys | {"blocked_reason", "blocked_batch_id"}
            and not batches
            and context.get("blocked_reason") == "missing_batch_evidence"
            and _bounded_string(context.get("blocked_batch_id"))
        )
        reason = "blocked"
    else:
        base_sha = context.get("base_sha")
        valid_variant = (
            set(context) == common_keys | {"base_sha"}
            and (base_sha is None or _bounded_string(base_sha))
        )
        reason = "ready_to_prepare" if batches else "unprepared"
    if not valid_variant:
        raise ProofError("frontier-response-malformed")

    releases = [] if active_release is None else [{"present": True}]
    summary = {
        "operation_frontier": operation_frontier,
        "queue_count": len(batches),
        "reason": reason,
        "releases": releases,
    }
    operation_empty = (
        operation_frontier["pending_operation_count"] == 0
        and operation_frontier["blocked_state"] == "unblocked"
    )
    return summary, reason == "unprepared" and operation_empty


def _timer_state(unit, run):
    """Read timer state without mutating the unit."""
    if SYSTEMCTL_PATH is None:
        return {"active": None, "available": False, "enabled": None}
    environment = {
        "LC_ALL": "C",
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
    }
    try:
        enabled = run(
            [SYSTEMCTL_PATH, "--user", "is-enabled", unit],
            capture_output=True,
            encoding="utf-8",
            env=environment,
            errors="strict",
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        active = run(
            [SYSTEMCTL_PATH, "--user", "is-active", unit],
            capture_output=True,
            encoding="utf-8",
            env=environment,
            errors="strict",
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
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


def timer_state(run=subprocess.run):
    """Read the production release timer state without mutating the unit."""
    return _timer_state(TIMER_UNIT, run)


def _utc_timestamp(utc_now):
    try:
        observed = utc_now()
        if not isinstance(observed, datetime.datetime) or observed.tzinfo is None:
            raise ProofError("timestamp-unavailable")
        return (
            observed.astimezone(datetime.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except ProofError:
        raise
    except Exception as exc:
        raise ProofError("timestamp-unavailable") from exc


def _new_receipt():
    return {
        "all_queues_empty": False,
        "apply": {"accepted": None},
        "editor_review": {
            "accepted": None,
            "other_non_terminal": None,
            "pending": None,
        },
        "fence": "unproven",
        "first_get_utc": None,
        "last_get_utc": None,
        "ledger_host": None,
        "publication": "unproved",
        "publication_fallback": None,
        "publication_frontier": None,
        "timer": {"active": None, "available": False, "enabled": None},
    }


def prove(
    ledger_origin,
    apply_env_file,
    observer_env_file,
    *,
    apply_timer_stopped,
    window_owner,
    opener,
    run_systemctl,
    utc_now,
):
    receipt = _new_receipt()
    try:
        if not apply_timer_stopped or window_owner is None:
            raise ProofError("fence-assertion-missing")
        if not WINDOW_OWNER_RE.fullmatch(window_owner):
            raise ProofError("fence-assertion-invalid")
        apply_timer = _timer_state(APPLY_TIMER_UNIT, run_systemctl)
        apply_stopped = (
            apply_timer["available"] is True
            and apply_timer["active"] is False
        )
        receipt["fence"] = {
            "apply_timer": apply_timer,
            "apply_timer_stopped": apply_stopped,
            "proved": apply_stopped,
            "window_owner": window_owner,
        }
        if not apply_stopped:
            raise ProofError("fence-apply-timer-not-stopped")

        timer = timer_state(run_systemctl)
        receipt["timer"] = timer
        review_url, frontier_url, ledger_host = _api_coordinates(
            ledger_origin
        )
        receipt["ledger_host"] = ledger_host
        apply_env = _protected_env(apply_env_file, {"EDIT_SERVICE_TOKEN"})
        receipt["first_get_utc"] = _utc_timestamp(utc_now)
        receipt["last_get_utc"] = receipt["first_get_utc"]
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
            receipt["last_get_utc"] = _utc_timestamp(utc_now)
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
            raise ProofError("environment-unavailable")

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
    utc_now=None,
    stdout=None,
):
    receipt = _new_receipt()
    parser = _ProofArgumentParser(
        description="Emit a text-free proof that all Day Zero queues are empty"
    )
    parser.add_argument("--ledger-origin", required=True)
    parser.add_argument("--apply-env-file", required=True)
    parser.add_argument("--observer-env-file", required=True)
    parser.add_argument("--apply-timer-stopped", action="store_true")
    parser.add_argument("--window-owner")
    try:
        args = parser.parse_args(argv)
    except _ArgumentsInvalid:
        receipt["proof_error"] = "arguments-invalid"
    else:
        if opener is None:
            opener = urllib.request.build_opener(_NoRedirect).open
        if utc_now is None:
            utc_now = lambda: datetime.datetime.now(datetime.timezone.utc)
        receipt = prove(
            args.ledger_origin,
            args.apply_env_file,
            args.observer_env_file,
            apply_timer_stopped=args.apply_timer_stopped,
            window_owner=args.window_owner,
            opener=opener,
            run_systemctl=run_systemctl,
            utc_now=utc_now,
        )
    print(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        file=stdout or sys.stdout,
    )
    return 0 if receipt["all_queues_empty"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
