#!/usr/bin/env python3
"""Emit one text-free, read-only receipt proving all Day Zero queues empty."""
from __future__ import annotations

import argparse
import datetime
import email.utils
import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import ssl
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
MAX_CLOCK_SKEW_SECONDS = 300
TIMER_UNIT = "sonsteng-prod-release.timer"
APPLY_TIMER_UNIT = "sonsteng-apply.timer"
USER_AGENT = "sonsteng-queue-proof/1.0"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
TLS_12_CIPHER_POLICY = "ECDHE+AESGCM:ECDHE+CHACHA20"
MACHINE_ID_PATH = "/etc/machine-id"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WINDOW_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
WINDOW_NONCE_RE = re.compile(r"[0-9a-f]{64}")
WINDOW_PHASES = ("opening", "closing")
CONTENT_LENGTH_RE = re.compile(r"(?:0|[1-9][0-9]*)")
GIT_OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}")
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


class _ProofInterrupted(BaseException):
    """Carry bounded partial proof state out to the receipt writer."""

    def __init__(self, receipt, returncode):
        super().__init__()
        self.receipt = receipt
        self.returncode = returncode


class _SignalInterrupted(BaseException):
    """Convert a terminating signal into a receipted interruption."""

    def __init__(self, signum):
        super().__init__()
        self.signum = signum


def _interrupt_returncode(exception):
    if isinstance(exception, _SignalInterrupted):
        return 128 + exception.signum
    if isinstance(exception, KeyboardInterrupt):
        return 128 + signal.SIGINT
    return 1


def _raise_signal_interruption(signum, _frame):
    raise _SignalInterrupted(signum)


def _install_interrupt_handlers():
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, _raise_signal_interruption)
    return previous


def _restore_interrupt_handlers(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _block_interrupt_signals():
    return signal.pthread_sigmask(
        signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
    )


def _restore_interrupt_mask(previous):
    if previous is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class _ProofArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise _ArgumentsInvalid from None

    def exit(self, _status=0, _message=None):
        raise _ArgumentsInvalid from None

    def print_help(self, file=None):
        # Proof invocations have a one-receipt stdout contract, including help.
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _read_system_ca_bundle():
    try:
        raw = pathlib.Path(SYSTEM_CA_BUNDLE).read_bytes()
        cadata = raw.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise ProofError("https-client-unavailable") from exc
    if not raw:
        raise ProofError("https-client-unavailable")
    return raw, cadata


def _production_context(ca_bundle=None):
    """Build an explicit TLS context without environment-selected trust inputs."""
    try:
        if ca_bundle is None:
            _raw, cadata = _read_system_ca_bundle()
        else:
            _raw, cadata = ca_bundle
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers(TLS_12_CIPHER_POLICY)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.keylog_filename = None
        context.load_verify_locations(cadata=cadata)
        return context
    except Exception as exc:
        raise ProofError("https-client-unavailable") from exc


def _production_opener():
    """Build an HTTPS stack with no authority inherited from the environment."""
    context = _production_context()
    try:
        # Other standard handlers are unreachable because every requested URL
        # is constructed from the HTTPS-only allowlist and redirects are denied.
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
        ).open
    except Exception as exc:
        raise ProofError("https-client-unavailable") from exc


def _assert_clean_process_environment(environment=None, *, isolated=None):
    """Require the exact environment and isolated mode of the sanctioned launcher."""
    names = os.environ if environment is None else environment
    if set(names) != {"LC_ALL"} or names.get("LC_ALL") != "C":
        raise ProofError("environment-hostile")
    if isolated is None:
        isolated = sys.flags.isolated == 1
    if isolated is not True:
        raise ProofError("runtime-isolation-required")


def _protected_env(
    path, required_keys, *, missing_ok=False, reject_unknown=False
):
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
        if key not in required_keys and reject_unknown:
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


def _window_nonce_digest(path):
    try:
        values = _protected_env(
            path,
            {"QUEUE_PROOF_WINDOW_NONCE"},
            reject_unknown=True,
        )
    except ProofError as exc:
        if str(exc) in {"environment-unavailable", "environment-malformed"}:
            raise ProofError("window-nonce-unavailable") from exc
        raise
    nonce = values["QUEUE_PROOF_WINDOW_NONCE"]
    if not WINDOW_NONCE_RE.fullmatch(nonce):
        raise ProofError("fence-assertion-invalid")
    return hashlib.sha256(
        b"queue-proof-window-nonce\0" + nonce.encode("ascii")
    ).hexdigest()


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


def _server_date(headers):
    values = [
        value
        for name, value in headers
        if isinstance(name, str) and name.casefold() == "date"
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise ProofError("http-server-date-invalid")
    try:
        observed = email.utils.parsedate_to_datetime(values[0])
        if observed is None or observed.tzinfo is None:
            raise ValueError
        utc_observed = observed.astimezone(datetime.timezone.utc).replace(
            microsecond=0
        )
        return utc_observed
    except (TypeError, ValueError, OverflowError):
        raise ProofError("http-server-date-invalid") from None


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
        server_date = _server_date(headers)
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
    return raw, server_date


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
    raw, server_date = _read_response(manager)
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
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return payload, server_date, hashlib.sha256(canonical).hexdigest()


def _tls_handshake(ledger_host, context):
    """Perform one authenticated TLS handshake without an HTTP request."""
    try:
        with socket.create_connection(
            (ledger_host, 443), timeout=TIMEOUT_SECONDS
        ) as transport:
            with context.wrap_socket(
                transport, server_hostname=ledger_host
            ) as connection:
                protocol = connection.version()
                cipher = connection.cipher()
    except Exception as exc:
        raise ProofError("https-handshake-unavailable") from exc
    if (
        protocol not in {"TLSv1.2", "TLSv1.3"}
        or not isinstance(cipher, tuple)
        or len(cipher) != 3
        or not isinstance(cipher[0], str)
        or not cipher[0]
        or not isinstance(cipher[2], int)
        or isinstance(cipher[2], bool)
        or cipher[2] <= 0
    ):
        raise ProofError("https-handshake-invalid")
    return {
        "cipher": cipher[0],
        "protocol": protocol,
        "secret_bits": cipher[2],
    }


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


def _timer_state(unit, run, systemctl_path=SYSTEMCTL_PATH):
    """Read timer state without mutating the unit."""
    if systemctl_path is None:
        return {"active": None, "available": False, "enabled": None}
    environment = {
        "LC_ALL": "C",
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
    }
    try:
        enabled = run(
            [systemctl_path, "--user", "is-enabled", unit],
            capture_output=True,
            encoding="utf-8",
            env=environment,
            errors="strict",
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        active = run(
            [systemctl_path, "--user", "is-active", unit],
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


def timer_state(run=subprocess.run, systemctl_path=SYSTEMCTL_PATH):
    """Read the production release timer state without mutating the unit."""
    return _timer_state(TIMER_UNIT, run, systemctl_path)


def _utc_observation(utc_now):
    try:
        observed = utc_now()
        if not isinstance(observed, datetime.datetime) or observed.tzinfo is None:
            raise ProofError("timestamp-unavailable")
        utc_observed = observed.astimezone(datetime.timezone.utc).replace(
            microsecond=0
        )
        return utc_observed
    except ProofError:
        raise
    except Exception as exc:
        raise ProofError("timestamp-unavailable") from exc


def _utc_timestamp(observed):
    return observed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _http_date(observed):
    return email.utils.format_datetime(observed, usegmt=True)


def _server_clock_skew_seconds(server_date, local_time):
    try:
        skew = int((server_date - local_time).total_seconds())
    except (OverflowError, TypeError, ValueError) as exc:
        raise ProofError("http-server-date-invalid") from exc
    return skew


def _assert_server_clock_skew(skew):
    if abs(skew) > MAX_CLOCK_SKEW_SECONDS:
        raise ProofError("http-server-date-skew")


def _read_bounded_identity(path, *, pattern):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ProofError("host-identity-unavailable")
            raw = source.read(257)
        value = raw.decode("ascii").strip()
    except ProofError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ProofError("host-identity-unavailable") from exc
    if len(raw) > 256 or not pattern.fullmatch(value):
        raise ProofError("host-identity-unavailable")
    return value.encode("ascii")


def _host_identity():
    machine_id = _read_bounded_identity(
        MACHINE_ID_PATH, pattern=re.compile(r"[0-9a-fA-F]{32}")
    )
    boot_id = _read_bounded_identity(
        BOOT_ID_PATH,
        pattern=re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        ),
    )
    return {
        "boot_id_sha256": hashlib.sha256(b"boot-id\0" + boot_id).hexdigest(),
        "machine_id_sha256": hashlib.sha256(
            b"machine-id\0" + machine_id
        ).hexdigest(),
    }


def _git_blob_oid(raw):
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def _self_blob():
    if globals().get("__cached__") is not None:
        raise ProofError("verifier-bytecode-cached")
    try:
        raw = pathlib.Path(__file__).read_bytes()
    except OSError as exc:
        raise ProofError("verifier-identity-unreadable") from exc
    return _git_blob_oid(raw)


def _release_identity(release_commit, verifier_blob):
    if (
        not GIT_OBJECT_ID_RE.fullmatch(release_commit)
        or not GIT_OBJECT_ID_RE.fullmatch(verifier_blob)
    ):
        raise ProofError("release-identity-invalid")
    measured_blob = _self_blob()
    if measured_blob != verifier_blob:
        raise ProofError("verifier-blob-mismatch")
    return {
        "release_commit": release_commit,
        "verifier_blob": measured_blob,
    }


def _system_ca_bundle_identity(context, raw):
    try:
        stats = context.cert_store_stats()
        ca_count = stats["x509_ca"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ProofError("https-client-unavailable") from exc
    if not raw or not isinstance(ca_count, int) or ca_count <= 0:
        raise ProofError("https-client-unavailable")
    return {
        "path": SYSTEM_CA_BUNDLE,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "x509_ca_count": ca_count,
    }


def _new_receipt(verifier_identity=None):
    if verifier_identity is None:
        verifier_identity = {"release_commit": None, "verifier_blob": None}
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
        "host_identity": None,
        "last_get_utc": None,
        "ledger_host": None,
        "ledger_state_hash": None,
        "preflight": None,
        "publication": "unproved",
        "publication_fallback": None,
        "publication_frontier": None,
        "server_date_skew_seconds": {
            "publication_frontier": None,
            "review": None,
        },
        "server_dates": {"publication_frontier": None, "review": None},
        "timer": {"active": None, "available": False, "enabled": None},
        "verifier_identity": dict(verifier_identity),
    }


def _failed_receipt(proof_error, verifier_identity=None):
    receipt = _new_receipt(verifier_identity)
    receipt["proof_error"] = proof_error
    return receipt


def prove(
    ledger_origin,
    apply_env_file,
    observer_env_file,
    *,
    verifier_identity,
    apply_timer_stopped,
    window_owner,
    window_nonce_file,
    window_phase,
    opener,
    run_systemctl,
    systemctl_path,
    read_host_identity,
    utc_now,
):
    receipt = _new_receipt(verifier_identity)
    try:
        if (
            not apply_timer_stopped
            or window_owner is None
            or window_nonce_file is None
            or window_phase is None
        ):
            raise ProofError("fence-assertion-missing")
        if not WINDOW_OWNER_RE.fullmatch(window_owner):
            raise ProofError("fence-assertion-invalid")
        if window_phase not in WINDOW_PHASES:
            raise ProofError("fence-assertion-invalid")
        window_nonce_sha256 = _window_nonce_digest(window_nonce_file)
        receipt["host_identity"] = dict(read_host_identity())
        apply_timer = _timer_state(
            APPLY_TIMER_UNIT, run_systemctl, systemctl_path
        )
        apply_stopped = (
            apply_timer["available"] is True
            and apply_timer["active"] is False
        )
        receipt["fence"] = {
            "apply_timer": apply_timer,
            "apply_timer_stopped": apply_stopped,
            "proved": apply_stopped,
            "window_nonce_sha256": window_nonce_sha256,
            "window_owner": window_owner,
            "window_phase": window_phase,
        }
        if not apply_stopped:
            raise ProofError("fence-apply-timer-not-stopped")

        timer = timer_state(run_systemctl, systemctl_path)
        receipt["timer"] = timer
        review_url, frontier_url, ledger_host = _api_coordinates(
            ledger_origin
        )
        receipt["ledger_host"] = ledger_host
        apply_env = _protected_env(apply_env_file, {"EDIT_SERVICE_TOKEN"})
        first_get_time = _utc_observation(utc_now)
        receipt["first_get_utc"] = _utc_timestamp(first_get_time)
        receipt["last_get_utc"] = receipt["first_get_utc"]
        review_payload, review_server_time, review_hash = _get_json(
            review_url, apply_env["EDIT_SERVICE_TOKEN"], opener, review=True
        )
        counts = _review_counts(review_payload)
        receipt["server_dates"]["review"] = _http_date(review_server_time)
        receipt["server_date_skew_seconds"]["review"] = (
            _server_clock_skew_seconds(review_server_time, first_get_time)
        )
        _assert_server_clock_skew(
            receipt["server_date_skew_seconds"]["review"]
        )
        receipt["editor_review"] = counts
        receipt["apply"] = {"accepted": counts["accepted"]}

        observer_env = _protected_env(
            observer_env_file,
            {"SONSTENG_PROD_OBSERVER_BEARER"},
            missing_ok=True,
        )
        if observer_env is not None:
            last_get_time = _utc_observation(utc_now)
            receipt["last_get_utc"] = _utc_timestamp(last_get_time)
            (
                frontier_payload,
                frontier_server_time,
                frontier_hash,
            ) = _get_json(
                frontier_url,
                observer_env["SONSTENG_PROD_OBSERVER_BEARER"],
                opener,
            )
            frontier, publication_empty = _frontier_summary(frontier_payload)
            combined_hash = hashlib.sha256(
                b"review\0"
                + bytes.fromhex(review_hash)
                + b"frontier\0"
                + bytes.fromhex(frontier_hash)
            ).hexdigest()
            receipt["server_dates"][
                "publication_frontier"
            ] = _http_date(frontier_server_time)
            receipt["server_date_skew_seconds"][
                "publication_frontier"
            ] = _server_clock_skew_seconds(
                frontier_server_time, last_get_time
            )
            _assert_server_clock_skew(
                receipt["server_date_skew_seconds"]["publication_frontier"]
            )
            if frontier_server_time < review_server_time:
                raise ProofError("http-server-date-nonmonotonic")
            receipt["ledger_state_hash"] = {
                "algorithm": "sha256",
                "combined": combined_hash,
                "publication_frontier": frontier_hash,
                "review": review_hash,
            }
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
    except Exception:
        raise
    except BaseException as exc:
        receipt["proof_error"] = "verifier-interrupted"
        raise _ProofInterrupted(
            receipt, _interrupt_returncode(exc)
        ) from None
    return receipt


def _preflight_receipt(
    verifier_identity, systemctl_path, ledger_origin, tls_handshake
):
    receipt = _new_receipt(verifier_identity)
    if systemctl_path is None:
        raise ProofError("systemctl-unavailable")
    try:
        resolved_systemctl = pathlib.Path(systemctl_path).resolve(strict=True)
    except OSError as exc:
        raise ProofError("systemctl-unavailable") from exc
    if not resolved_systemctl.is_file() or not os.access(resolved_systemctl, os.X_OK):
        raise ProofError("systemctl-unavailable")
    ca_bundle = _read_system_ca_bundle()
    context = _production_context(ca_bundle)
    _review_url, _frontier_url, ledger_host = _api_coordinates(ledger_origin)
    receipt["preflight"] = {
        "https_handshake": tls_handshake(ledger_host, context),
        "ready": True,
        "system_ca_bundle": _system_ca_bundle_identity(context, ca_bundle[0]),
        "systemctl_path": str(resolved_systemctl),
    }
    return receipt


def _run_proof(
    args,
    opener,
    run_systemctl,
    utc_now,
    systemctl_path,
    read_host_identity,
    process_environment,
    isolated,
    tls_handshake,
):
    verifier_identity = None
    try:
        _assert_clean_process_environment(
            process_environment, isolated=isolated
        )
        verifier_identity = _release_identity(
            args.release_commit, args.verifier_blob
        )
        if args.preflight:
            return _preflight_receipt(
                verifier_identity,
                systemctl_path,
                args.ledger_origin,
                tls_handshake,
            )
        if opener is None:
            opener = _production_opener()
        if utc_now is None:
            utc_now = lambda: datetime.datetime.now(datetime.timezone.utc)
        return prove(
            args.ledger_origin,
            args.apply_env_file,
            args.observer_env_file,
            verifier_identity=verifier_identity,
            apply_timer_stopped=args.apply_timer_stopped,
            window_owner=args.window_owner,
            window_nonce_file=args.window_nonce_file,
            window_phase=args.window_phase,
            opener=opener,
            run_systemctl=run_systemctl,
            systemctl_path=systemctl_path,
            read_host_identity=read_host_identity,
            utc_now=utc_now,
        )
    except _ProofInterrupted:
        raise
    except ProofError as exc:
        return _failed_receipt(str(exc), verifier_identity)
    except Exception:
        return _failed_receipt("verifier-defect", verifier_identity)
    except BaseException as exc:
        raise _ProofInterrupted(
            _failed_receipt("verifier-interrupted", verifier_identity),
            _interrupt_returncode(exc),
        ) from None


def _write_all(file_descriptor, payload):
    offset = 0
    view = memoryview(payload)
    while offset < len(view):
        written = os.write(file_descriptor, view[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _open_receipt(path):
    target = pathlib.Path(path)
    if not target.is_absolute():
        raise OSError
    directory_descriptor = os.open(
        target.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    receipt_descriptor = -1
    try:
        receipt_descriptor = os.open(
            target.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchmod(receipt_descriptor, 0o600)
    except BaseException:
        _release_receipt_reservation(
            receipt_descriptor,
            directory_descriptor,
            target.name if receipt_descriptor >= 0 else None,
        )
        raise
    return receipt_descriptor, directory_descriptor, target.name


def _write_receipt(
    file_descriptor, directory_descriptor, filename, payload
):
    _write_all(file_descriptor, payload)
    os.fsync(file_descriptor)
    opened = os.fstat(file_descriptor)
    named = os.stat(
        filename,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or stat.S_IMODE(opened.st_mode) != 0o600
        or stat.S_IMODE(named.st_mode) != 0o600
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OSError
    os.fsync(directory_descriptor)


def _best_effort_diagnostic(message):
    try:
        _write_all(2, (message + "\n").encode("utf-8"))
    except OSError:
        pass


def _release_receipt_reservation(
    receipt_descriptor, directory_descriptor, filename, expected_identity=None
):
    released = True
    if receipt_descriptor >= 0 and expected_identity is None:
        try:
            opened = os.fstat(receipt_descriptor)
            expected_identity = (opened.st_dev, opened.st_ino)
        except OSError:
            released = False
    if receipt_descriptor >= 0:
        try:
            os.close(receipt_descriptor)
        except OSError:
            released = False
    if directory_descriptor < 0:
        return released
    try:
        if filename is not None:
            try:
                named = os.stat(
                    filename,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                if (
                    expected_identity is None
                    or not stat.S_ISREG(named.st_mode)
                    or (named.st_dev, named.st_ino) != expected_identity
                ):
                    released = False
                else:
                    os.unlink(filename, dir_fd=directory_descriptor)
            if released:
                os.fsync(directory_descriptor)
    except OSError:
        released = False
    finally:
        try:
            os.close(directory_descriptor)
        except OSError:
            released = False
    return released


def _serialize_receipt(receipt):
    try:
        return (
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"), False
    except Exception:
        # This literal is deliberately independent of the JSON encoder so an
        # encoder or receipt-shape defect still produces one bounded receipt.
        return (
            b'{"all_queues_empty":false,"apply":{"accepted":null},'
            b'"editor_review":{"accepted":null,"other_non_terminal":null,'
            b'"pending":null},"fence":"unproven","first_get_utc":null,'
            b'"host_identity":null,"last_get_utc":null,"ledger_host":null,'
            b'"ledger_state_hash":null,"preflight":null,'
            b'"proof_error":"verifier-defect","publication":"unproved",'
            b'"publication_fallback":null,"publication_frontier":null,'
            b'"server_date_skew_seconds":{"publication_frontier":null,'
            b'"review":null},"server_dates":{"publication_frontier":null,'
            b'"review":null},"timer":{"active":null,"available":false,'
            b'"enabled":null},"verifier_identity":{"release_commit":null,'
            b'"verifier_blob":null}}\n'
        ), True


def _main(
    argv=None,
    *,
    opener=None,
    run_systemctl=subprocess.run,
    utc_now=None,
    stdout=None,
    systemctl_path=None,
    read_host_identity=None,
    process_environment=None,
    isolated=None,
    tls_handshake=None,
    _invocation_state=None,
):
    if _invocation_state is None:
        _invocation_state = {}
    command_line = list(sys.argv[1:] if argv is None else argv)
    receipt_descriptor = -1
    receipt_directory_descriptor = -1
    receipt_filename = None
    receipt_identity = None
    interrupted_returncode = None
    bootstrap_interrupt_mask = None
    durable_receipt = stdout is None
    if durable_receipt:
        bootstrap = _ProofArgumentParser(add_help=False)
        bootstrap.add_argument("--receipt-path", required=True)
        try:
            bootstrap_args, _unknown = bootstrap.parse_known_args(command_line)
        except _ArgumentsInvalid:
            _best_effort_diagnostic(
                "queue proof receipt file could not be opened"
            )
            return 1
        try:
            bootstrap_interrupt_mask = _block_interrupt_signals()
            (
                receipt_descriptor,
                receipt_directory_descriptor,
                receipt_filename,
            ) = _open_receipt(bootstrap_args.receipt_path)
            opened_receipt = os.fstat(receipt_descriptor)
            receipt_identity = (
                opened_receipt.st_dev,
                opened_receipt.st_ino,
            )
        except OSError:
            _release_receipt_reservation(
                receipt_descriptor,
                receipt_directory_descriptor,
                receipt_filename,
                receipt_identity,
            )
            receipt_descriptor = -1
            receipt_directory_descriptor = -1
            try:
                _restore_interrupt_mask(bootstrap_interrupt_mask)
            finally:
                bootstrap_interrupt_mask = None
            _best_effort_diagnostic(
                "queue proof receipt file could not be opened"
            )
            return 1
        except BaseException:
            _release_receipt_reservation(
                receipt_descriptor,
                receipt_directory_descriptor,
                receipt_filename,
                receipt_identity,
            )
            receipt_descriptor = -1
            receipt_directory_descriptor = -1
            try:
                _restore_interrupt_mask(bootstrap_interrupt_mask)
            finally:
                bootstrap_interrupt_mask = None
            raise
    try:
        _restore_interrupt_mask(bootstrap_interrupt_mask)
        bootstrap_interrupt_mask = None
        parser = _ProofArgumentParser(
            description=(
                "Emit a text-free proof that all Day Zero queues are empty"
            )
        )
        parser.add_argument("--ledger-origin")
        parser.add_argument("--apply-env-file")
        parser.add_argument("--observer-env-file")
        parser.add_argument("--receipt-path", required=durable_receipt)
        parser.add_argument("--release-commit", required=True)
        parser.add_argument("--verifier-blob", required=True)
        parser.add_argument("--preflight", action="store_true")
        parser.add_argument("--apply-timer-stopped", action="store_true")
        parser.add_argument("--window-owner")
        parser.add_argument("--window-nonce-file")
        parser.add_argument("--window-phase", choices=WINDOW_PHASES)
        if systemctl_path is None:
            systemctl_path = SYSTEMCTL_PATH
        if read_host_identity is None:
            read_host_identity = _host_identity
        if tls_handshake is None:
            tls_handshake = _tls_handshake
        try:
            args = parser.parse_args(command_line)
            if args.ledger_origin is None or (
                not args.preflight
                and any(
                value is None
                for value in (
                    args.apply_env_file,
                    args.observer_env_file,
                )
                )
            ):
                raise _ArgumentsInvalid
        except _ArgumentsInvalid:
            receipt = _failed_receipt("arguments-invalid")
        else:
            receipt = _run_proof(
                args, opener, run_systemctl, utc_now,
                systemctl_path, read_host_identity,
                process_environment, isolated, tls_handshake,
            )
        successful = receipt["all_queues_empty"] or (
            isinstance(receipt.get("preflight"), dict)
            and receipt["preflight"].get("ready") is True
        )
    except _ProofInterrupted as interruption:
        receipt = interruption.receipt
        successful = False
        interrupted_returncode = interruption.returncode
    except Exception:
        receipt = _failed_receipt("verifier-defect")
        successful = False
    except BaseException as exc:
        receipt = _failed_receipt("verifier-interrupted")
        successful = False
        interrupted_returncode = _interrupt_returncode(exc)
    stdout_started = False
    final_returncode = (
        interrupted_returncode
        if interrupted_returncode is not None
        else (0 if successful else 1)
    )
    finalization_interrupt_mask = None
    try:
        payload, serialization_failed = _serialize_receipt(receipt)
        if serialization_failed:
            successful = False
            final_returncode = 1
        if durable_receipt:
            finalization_interrupt_mask = _block_interrupt_signals()
            try:
                _write_receipt(
                    receipt_descriptor,
                    receipt_directory_descriptor,
                    receipt_filename,
                    payload,
                )
                os.close(receipt_descriptor)
                receipt_descriptor = -1
            except OSError:
                final_returncode = 1
                _best_effort_diagnostic(
                    "queue proof receipt file could not be written"
                )
                return 1
            try:
                stdout_started = True
                _invocation_state["stdout_started"] = True
                _write_all(1, payload)
            except OSError:
                final_returncode = 1
                _best_effort_diagnostic(
                    "queue proof stdout mirror failed; receipt is at the required path"
                )
                return 1
            if interrupted_returncode is not None:
                if not _release_receipt_reservation(
                    -1,
                    receipt_directory_descriptor,
                    receipt_filename,
                    receipt_identity,
                ):
                    final_returncode = 1
                    _best_effort_diagnostic(
                        "queue proof interrupted receipt could not be released"
                    )
                    return 1
                receipt_directory_descriptor = -1
            else:
                os.close(receipt_directory_descriptor)
                receipt_directory_descriptor = -1
        else:
            try:
                stdout_started = True
                _invocation_state["stdout_started"] = True
                stdout.write(payload.decode("utf-8"))
                stdout.flush()
            except Exception:
                final_returncode = 1
                _best_effort_diagnostic("queue proof receipt sink failed")
                return 1
    except BaseException as exc:
        interrupted_returncode = _interrupt_returncode(exc)
        final_returncode = interrupted_returncode
        receipt["all_queues_empty"] = False
        receipt["proof_error"] = "verifier-interrupted"
        if not stdout_started:
            released = _release_receipt_reservation(
                receipt_descriptor,
                receipt_directory_descriptor,
                receipt_filename,
                receipt_identity,
            )
            receipt_descriptor = -1
            receipt_directory_descriptor = -1
            if not released:
                _best_effort_diagnostic(
                    "queue proof interrupted receipt could not be released"
                )
            payload, _serialization_failed = _serialize_receipt(receipt)
            try:
                if durable_receipt:
                    _write_all(1, payload)
                else:
                    stdout.write(payload.decode("utf-8"))
                    stdout.flush()
            except BaseException:
                _best_effort_diagnostic("queue proof interrupted")
        else:
            _best_effort_diagnostic(
                "queue proof interrupted during receipt mirror; receipt is at the required path"
            )
        return interrupted_returncode
    finally:
        if receipt_descriptor >= 0:
            try:
                os.close(receipt_descriptor)
            except OSError:
                pass
        if receipt_directory_descriptor >= 0:
            try:
                os.close(receipt_directory_descriptor)
            except OSError:
                pass
        _invocation_state["committed_returncode"] = final_returncode
        try:
            _restore_interrupt_mask(finalization_interrupt_mask)
        except _SignalInterrupted:
            # The receipt outcome is already committed. A signal that arrived
            # after this linearization point is a post-completion event.
            pass
    return final_returncode


def main(
    argv=None,
    *,
    opener=None,
    run_systemctl=subprocess.run,
    utc_now=None,
    stdout=None,
    systemctl_path=None,
    read_host_identity=None,
    process_environment=None,
    isolated=None,
    tls_handshake=None,
):
    invocation_state = {
        "stdout_started": False,
        "committed_returncode": None,
    }
    try:
        previous_handlers = _install_interrupt_handlers()
    except (OSError, RuntimeError, ValueError):
        previous_handlers = {}
    try:
        return _main(
            argv,
            opener=opener,
            run_systemctl=run_systemctl,
            utc_now=utc_now,
            stdout=stdout,
            systemctl_path=systemctl_path,
            read_host_identity=read_host_identity,
            process_environment=process_environment,
            isolated=isolated,
            tls_handshake=tls_handshake,
            _invocation_state=invocation_state,
        )
    except BaseException as exc:
        if invocation_state["committed_returncode"] is not None:
            return invocation_state["committed_returncode"]
        payload, _failed = _serialize_receipt(
            _failed_receipt("verifier-interrupted")
        )
        try:
            if stdout is None:
                _write_all(1, payload)
            else:
                stdout.write(payload.decode("utf-8"))
                stdout.flush()
        except BaseException:
            _best_effort_diagnostic("queue proof interrupted")
        return _interrupt_returncode(exc)
    finally:
        try:
            _restore_interrupt_handlers(previous_handlers)
        except BaseException:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
