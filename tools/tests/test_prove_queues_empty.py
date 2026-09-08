import datetime
import errno
import hashlib
import http.client
import importlib.util
import io
import json
import os
import pathlib
import re
import signal
import ssl
import stat
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest


TOOLS = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "prove_queues_empty", TOOLS / "prove_queues_empty.py"
)
queues = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queues)
queues.__cached__ = None

LEDGER_ORIGIN = "https://sonsteng-chat.damienriehl.workers.dev"
WINDOW_OWNER = "packet-d-test-window"
RELEASE_COMMIT = "a" * 40
GENERATE_WINDOW_NONCE = object()


def _controlled_claim_statement(claim_id, outcome):
    if claim_id == "loader_boundary":
        with_exec = (
            "runs"
            if outcome["preload_constructor_ran_with_exec_c"]
            else "does not run"
        )
        without_exec = (
            "runs"
            if outcome["preload_constructor_ran_without_exec_c"]
            else "does not run"
        )
        detection = (
            "is detectable"
            if outcome["verifier_detected_missing_exec_c"]
            else "is not detectable"
        )
        reason = (
            ""
            if outcome["verifier_detected_missing_exec_c"]
            else " because the loader has already run"
        )
        return (
            "With the launcher's `exec -c` boundary the preload constructor "
            f"{with_exec}; without `exec -c` it {without_exec}. Removing "
            f"`exec -c` {detection} by the verifier{reason}."
        )
    if claim_id == "launcher_closed_stdout":
        reservation = (
            "after it reserves"
            if outcome["receipt_path_reserved"]
            else "before it reserves"
        )
        return (
            f"The launcher returns {outcome['returncode']} for a closed stdout "
            f"{reservation} a receipt path."
        )
    if claim_id == "verifier_closed_stdout":
        receipt = (
            "preserves"
            if outcome["durable_receipt"]
            else "does not preserve"
        )
        return (
            f"The verifier returns {outcome['returncode']} for a shell-level "
            f"closed stdout and {receipt} the durable receipt."
        )
    if claim_id in {"sigint", "sigterm"}:
        verdict = "false" if not outcome["all_queues_empty"] else "true"
        preservation = (
            "preserves" if outcome["proof_state_preserved"] else "does not preserve"
        )
        reuse = (
            "releases the receipt path for retry"
            if outcome["receipt_path_reusable"]
            else "keeps the receipt path reserved"
        )
        return (
            f"{claim_id.upper()} before receipt finalization returns "
            f"{outcome['returncode']}, mirrors one bounded receipt with "
            f"all_queues_empty {verdict} and proof_error "
            f"{outcome['proof_error']}, {preservation} the established proof "
            f"state, and {reuse}."
        )
    if claim_id == "post_finalization_signal":
        durable = "preserves" if outcome["durable_receipt"] else "does not preserve"
        match = "matches" if outcome["receipt_matches_stdout"] else "differs from"
        return (
            "A signal during or after receipt finalization returns "
            f"{outcome['returncode']}, {durable} the committed receipt, and "
            f"its stdout mirror {match} that receipt."
        )
    if claim_id in {"dev_full", "epipe"}:
        subject = "A /dev/full" if claim_id == "dev_full" else "An EPIPE"
        receipt = (
            "preserves"
            if outcome["durable_receipt"]
            else "does not preserve"
        )
        return (
            f"{subject} stdout returns {outcome['returncode']} and {receipt} "
            "the durable receipt."
        )
    raise AssertionError(f"unrecognized executable claim: {claim_id}")


def runbook_executable_claims(runbook_path=None):
    if runbook_path is None:
        runbook_path = TOOLS.parent / "docs/day-zero-migration-operations.md"
    runbook = pathlib.Path(runbook_path).read_text(encoding="utf-8")
    controlled = runbook.split(
        "<!-- queue-proof-controlled-claims:start -->", 1
    )[1].split("<!-- queue-proof-controlled-claims:end -->", 1)[0]
    marked = controlled.split(
        "<!-- queue-proof-executable-claims:start -->", 1
    )[1].split("<!-- queue-proof-executable-claims:end -->", 1)[0]
    expected_controlled = (
        "<!-- queue-proof-executable-claims:start -->"
        + marked
        + "<!-- queue-proof-executable-claims:end -->"
    )
    assert controlled.strip() == expected_controlled.strip(), (
        "the controlled claims region may contain only the executable contract"
    )
    fenced = textwrap.dedent(marked).strip()
    assert fenced.startswith("```json\n") and fenced.endswith("\n```")
    contract = json.loads(
        fenced.removeprefix("```json\n").removesuffix("\n```")
    )
    assert contract["schema"] == "queue-proof-executable-claims/v1"
    assert set(contract) == {"schema", "claims"}
    assert set(contract["claims"]) == {
        "loader_boundary",
        "launcher_closed_stdout",
        "verifier_closed_stdout",
        "sigint",
        "sigterm",
        "post_finalization_signal",
        "dev_full",
        "epipe",
    }
    outcomes = {}
    for claim_id, claim in contract["claims"].items():
        outcome = {key: value for key, value in claim.items() if key != "statement"}
        assert claim.get("statement") == _controlled_claim_statement(
            claim_id, outcome
        )
        outcomes[claim_id] = outcome
    return outcomes


def test_controlled_claim_contract_rejects_negated_exec_c_claim(tmp_path):
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    true_claim = (
        "Removing `exec -c` is not detectable by the verifier because the "
        "loader has already run."
    )
    false_claim = (
        "Removing `exec -c` is detected by the verifier's own dynamic-loader "
        "check, so the isolation cannot silently be dropped."
    )
    assert runbook.count(true_claim) == 1
    negated_runbook = tmp_path / "negated-runbook.md"
    negated_runbook.write_text(
        runbook.replace(true_claim, false_claim), encoding="utf-8"
    )

    with pytest.raises(AssertionError):
        runbook_executable_claims(negated_runbook)


def git_blob_oid(raw):
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw,
        usedforsecurity=False,
    ).hexdigest()


def sha256_hex(raw):
    return hashlib.sha256(raw).hexdigest()


def window_nonce_for(path):
    return sha256_hex(
        b"queue-proof-test-window\0" + os.fsencode(path)
    )


def window_nonce_digest_for(path):
    return sha256_hex(
        b"queue-proof-window-nonce\0"
        + window_nonce_for(path).encode("ascii")
    )


def write_window_nonce(path):
    nonce = window_nonce_for(path)
    path.write_text(
        f"QUEUE_PROOF_WINDOW_NONCE={nonce}\n", encoding="ascii"
    )
    path.chmod(0o600)
    return window_nonce_digest_for(path)


def successful_handshake(_ledger_host, _context):
    return {
        "cipher": "TLS_AES_256_GCM_SHA384",
        "protocol": "TLSv1.3",
        "secret_bits": 256,
    }


def window_fence_args(tmp_path, *, phase="opening"):
    nonce_file = tmp_path / "direct-window-nonce.env"
    write_window_nonce(nonce_file)
    return [
        "--apply-timer-stopped",
        "--window-owner",
        WINDOW_OWNER,
        "--window-nonce-file",
        str(nonce_file),
        "--window-phase",
        phase,
    ]


def call_main(argv, **kwargs):
    kwargs.setdefault("process_environment", {"LC_ALL": "C"})
    kwargs.setdefault("isolated", True)
    kwargs.setdefault("tls_handshake", successful_handshake)
    return queues.main(argv, **kwargs)


def ledger_hashes(review_payload, frontier_payload):
    review_hash = sha256_hex(
        json.dumps(
            review_payload, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    )
    frontier_hash = sha256_hex(
        json.dumps(
            frontier_payload, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    )
    combined_hash = sha256_hex(
        b"review\0"
        + bytes.fromhex(review_hash)
        + b"frontier\0"
        + bytes.fromhex(frontier_hash)
    )
    return {
        "algorithm": "sha256",
        "combined": combined_hash,
        "publication_frontier": frontier_hash,
        "review": review_hash,
    }


VERIFIER_BLOB = git_blob_oid((TOOLS / "prove_queues_empty.py").read_bytes())
HOST_IDENTITY = {
    "boot_id_sha256": "c" * 64,
    "machine_id_sha256": "d" * 64,
}
EMPTY_OPERATION_FRONTIER = {
    "blocked_state": "unblocked",
    "pending_operation_count": 0,
}
EMPTY_FRONTIER = {
    "active_release": None,
    "base_sha": None,
    "batches": [],
    "operation_frontier": EMPTY_OPERATION_FRONTIER,
}


def release_identity_args(
    release_commit=RELEASE_COMMIT, verifier_blob=VERIFIER_BLOB
):
    return [
        "--release-commit",
        release_commit,
        "--verifier-blob",
        verifier_blob,
    ]


def preflight_args():
    return [
        *release_identity_args(),
        "--ledger-origin",
        LEDGER_ORIGIN,
        "--preflight",
    ]


class Response:
    def __init__(
        self,
        payload,
        *,
        status=200,
        headers=None,
        server_date="Mon, 07 Sep 2026 15:00:00 GMT",
    ):
        self._body = json.dumps(payload).encode("utf-8")
        self._status = status
        self._headers = (
            [
                ("Content-Length", str(len(self._body))),
                ("Date", server_date),
            ]
            if headers is None
            else headers
        )
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        self.read_calls += 1
        return self._body

    def getcode(self):
        return self._status

    def getheaders(self):
        return list(self._headers)


class RawResponse(Response):
    def __init__(self, body, *, status=200, headers=None):
        self._body = body
        self._status = status
        self._headers = (
            [
                ("Content-Length", str(len(self._body))),
                ("Date", "Mon, 07 Sep 2026 15:00:00 GMT"),
            ]
            if headers is None
            else headers
        )
        self.read_calls = 0


class CountingWireBody(io.BytesIO):
    def __init__(self, wire_bytes):
        super().__init__(wire_bytes)
        self.read_calls = 0

    def read(self, amount=-1):
        self.read_calls += 1
        return super().read(amount)


class BytesSocket:
    def __init__(self, wire_bytes):
        self.stream = CountingWireBody(wire_bytes)

    def makefile(self, *_args, **_kwargs):
        return self.stream


class TimeoutInsteadOfEof(io.RawIOBase):
    def __init__(self, body):
        self._body = body

    def read(self, _amount=-1):
        if self._body is not None:
            body, self._body = self._body, None
            return body
        raise TimeoutError("private response remained open")

    def readable(self):
        return True


def real_http_response(body, *, declared_length, extra_headers=()):
    encoded_extra_headers = b"".join(
        f"{name}: {value}\r\n".encode("ascii")
        for name, value in extra_headers
    )
    wire = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Date: Mon, 07 Sep 2026 15:00:00 GMT\r\n"
        + f"Content-Length: {declared_length}\r\n".encode("ascii")
        + encoded_extra_headers
        + b"\r\n"
        + body
    )
    socket = BytesSocket(wire)
    response = http.client.HTTPResponse(socket)
    response.begin()
    socket.stream.read_calls = 0
    return response, socket.stream


def write_apply_env(path):
    path.write_text(
        "EDIT_API_BASE=https://wrong-host.example/edit/v1\n"
        "EDIT_SERVICE_TOKEN=admin-secret\n"
        "APPLY_DEPLOY_BRANCH=main\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def write_observer_env(path):
    path.write_text(
        "SONSTENG_PROD_OBSERVER_BEARER=observer-secret\n", encoding="utf-8"
    )
    path.chmod(0o600)


def injected_opener(review_rows, frontier_context=None, *, event_log=None):
    calls = []
    if frontier_context is None:
        frontier_context = EMPTY_FRONTIER

    def open_request(request, timeout):
        calls.append((request, timeout))
        if request.full_url.endswith("/review"):
            if event_log is not None:
                event_log.append("GET(review)")
            return Response({"ok": True, "items": review_rows})
        if request.full_url.endswith("/prod/releases/frontier"):
            if event_log is not None:
                event_log.append("GET(frontier)")
            return Response(
                {"ok": True, "context": frontier_context},
                server_date="Mon, 07 Sep 2026 15:00:01 GMT",
            )
        raise AssertionError("unexpected HTTP request")

    return open_request, calls


def injected_systemctl(
    *,
    production_enabled=False,
    production_active=False,
    apply_enabled=True,
    apply_active=False,
):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        is_apply = queues.APPLY_TIMER_UNIT in argv
        enabled = apply_enabled if is_apply else production_enabled
        active = apply_active if is_apply else production_active
        if "is-enabled" in argv:
            return SimpleNamespace(
                stdout="enabled\n" if enabled else "disabled\n",
                returncode=0 if enabled else 1,
            )
        return SimpleNamespace(
            stdout="active\n" if active else "inactive\n",
            returncode=0 if active else 3,
        )

    return run, calls


def run_main(
    tmp_path,
    *,
    opener,
    observer=True,
    timer_enabled=False,
    apply_timer_enabled=True,
    apply_timer_active=False,
    fence=True,
    apply_timer_asserted=True,
    window_owner=WINDOW_OWNER,
    window_nonce=GENERATE_WINDOW_NONCE,
    window_phase="opening",
    ledger_origin=LEDGER_ORIGIN,
    event_log=None,
    raw_output=False,
    run_systemctl=None,
    read_host_identity=None,
    process_environment=None,
):
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    if observer:
        write_observer_env(observer_env)
    nonce_file = tmp_path / "window-nonce.env"
    expected_nonce_digest = None
    if window_nonce is not None:
        if window_nonce is GENERATE_WINDOW_NONCE:
            expected_nonce_digest = write_window_nonce(nonce_file)
        else:
            nonce_file.write_text(
                f"QUEUE_PROOF_WINDOW_NONCE={window_nonce}\n",
                encoding="ascii",
            )
            nonce_file.chmod(0o600)
    if run_systemctl is None:
        systemctl, systemctl_calls = injected_systemctl(
            production_enabled=timer_enabled,
            apply_enabled=apply_timer_enabled,
            apply_active=apply_timer_active,
        )
    else:
        systemctl = run_systemctl
        systemctl_calls = []
    timestamps = iter(
        [
            datetime.datetime(2026, 9, 7, 15, 0, 0, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 9, 7, 15, 0, 1, tzinfo=datetime.timezone.utc),
        ]
    )
    output = io.StringIO()
    argv = [
        *release_identity_args(),
        "--ledger-origin",
        ledger_origin,
        "--apply-env-file",
        str(apply_env),
        "--observer-env-file",
        str(observer_env),
    ]
    if fence and apply_timer_asserted:
        argv.append("--apply-timer-stopped")
    if fence and window_owner is not None:
        argv.extend(["--window-owner", window_owner])
    if fence and window_nonce is not None:
        argv.extend(["--window-nonce-file", str(nonce_file)])
    if fence and window_phase is not None:
        argv.extend(["--window-phase", window_phase])

    def utc_now():
        timestamp = next(timestamps)
        if event_log is not None:
            event_log.append(f"clock({timestamp.isoformat()})")
        return timestamp

    if read_host_identity is None:
        read_host_identity = lambda: dict(HOST_IDENTITY)
    code = call_main(
        argv,
        opener=opener,
        run_systemctl=systemctl,
        utc_now=utc_now,
        stdout=output,
        systemctl_path="/usr/bin/systemctl",
        read_host_identity=read_host_identity,
        process_environment=(
            {"LC_ALL": "C"}
            if process_environment is None
            else process_environment
        ),
    )
    serialized = output.getvalue()
    receipt = serialized if raw_output else json.loads(serialized)
    if isinstance(receipt, dict) and expected_nonce_digest is not None:
        assert receipt.get("fence") == "unproven" or receipt["fence"].get(
            "window_nonce_sha256"
        ) == expected_nonce_digest
    return code, receipt, systemctl_calls


def invoke(
    tmp_path,
    *,
    review_rows,
    observer=True,
    timer_enabled=False,
    frontier_context=None,
    event_log=None,
):
    opener, http_calls = injected_opener(
        review_rows, frontier_context, event_log=event_log
    )
    code, receipt, systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        observer=observer,
        timer_enabled=timer_enabled,
        event_log=event_log,
    )
    return code, receipt, http_calls, systemctl_calls


def test_all_empty_returns_true_and_zero(tmp_path):
    event_log = []
    code, receipt, http_calls, systemctl_calls = invoke(
        tmp_path, review_rows=[], event_log=event_log
    )

    assert code == 0
    assert receipt == {
        "all_queues_empty": True,
        "apply": {"accepted": 0},
        "editor_review": {
            "accepted": 0,
            "other_non_terminal": 0,
            "pending": 0,
        },
        "ledger_host": "sonsteng-chat.damienriehl.workers.dev",
        "first_get_utc": "2026-09-07T15:00:00Z",
        "last_get_utc": "2026-09-07T15:00:01Z",
        "server_dates": {
            "publication_frontier": "Mon, 07 Sep 2026 15:00:01 GMT",
            "review": "Mon, 07 Sep 2026 15:00:00 GMT",
        },
        "server_date_skew_seconds": {
            "publication_frontier": 0,
            "review": 0,
        },
        "ledger_state_hash": ledger_hashes(
            {"ok": True, "items": []},
            {"ok": True, "context": EMPTY_FRONTIER},
        ),
        "host_identity": HOST_IDENTITY,
        "fence": {
            "apply_timer": {
                "active": False,
                "available": True,
                "enabled": True,
            },
            "apply_timer_stopped": True,
            "proved": True,
            "window_nonce_sha256": window_nonce_digest_for(
                tmp_path / "window-nonce.env"
            ),
            "window_owner": WINDOW_OWNER,
            "window_phase": "opening",
        },
        "publication": "observer-frontier",
        "preflight": None,
        "publication_fallback": None,
        "publication_frontier": {
            "queue_count": 0,
            "reason": "unprepared",
            "releases": [],
            "operation_frontier": EMPTY_OPERATION_FRONTIER,
        },
        "timer": {"active": False, "available": True, "enabled": False},
        "verifier_identity": {
            "release_commit": RELEASE_COMMIT,
            "verifier_blob": VERIFIER_BLOB,
        },
    }
    assert [call[0].get_method() for call in http_calls] == ["GET", "GET"]
    assert all(call[0].get_header("Connection") == "close" for call in http_calls)
    assert all(timeout == 20 for _, timeout in http_calls)
    assert event_log == [
        "clock(2026-09-07T15:00:00+00:00)",
        "GET(review)",
        "clock(2026-09-07T15:00:01+00:00)",
        "GET(frontier)",
    ]
    assert len(systemctl_calls) == 4
    assert all(call[1]["timeout"] == 20 for call in systemctl_calls)
    assert all(pathlib.Path(call[0][0]).is_absolute() for call in systemctl_calls)
    expected_systemctl_env = {
        "LC_ALL": "C",
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
    }
    assert all(
        call[1]["env"] == expected_systemctl_env for call in systemctl_calls
    )
    serialized = json.dumps(receipt)
    assert "admin-secret" not in serialized
    assert "observer-secret" not in serialized


def test_window_phase_distinguishes_opening_and_closing_without_nonce_leak(
    tmp_path,
):
    opener, _calls = injected_opener([])
    _code, opening, _systemctl_calls = run_main(
        tmp_path, opener=opener, window_phase="opening"
    )
    opener, _calls = injected_opener([])
    _code, closing, _systemctl_calls = run_main(
        tmp_path, opener=opener, window_phase="closing"
    )

    assert opening["ledger_state_hash"] == closing["ledger_state_hash"]
    assert opening["fence"]["window_phase"] == "opening"
    assert closing["fence"]["window_phase"] == "closing"
    assert opening != closing
    serialized = json.dumps([opening, closing])
    assert window_nonce_for(tmp_path / "window-nonce.env") not in serialized


def test_one_pending_row_returns_false_and_one(tmp_path):
    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path,
        review_rows=[
            {
                "id": "private-row-id",
                "status": "pending",
                "new_text": "authored private text",
            }
        ],
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["editor_review"] == {
        "accepted": 0,
        "other_non_terminal": 0,
        "pending": 1,
    }
    assert receipt["apply"] == {"accepted": 0}
    serialized = json.dumps(receipt)
    assert "private-row-id" not in serialized
    assert "authored private text" not in serialized


def test_one_accepted_row_fails_the_apply_queue_without_leaking_row(tmp_path):
    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path,
        review_rows=[
            {
                "id": "private-accepted-id",
                "status": "accepted",
                "new_text": "accepted authored text",
            }
        ],
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["editor_review"]["accepted"] == 1
    assert receipt["apply"] == {"accepted": 1}
    serialized = json.dumps(receipt)
    assert "private-accepted-id" not in serialized
    assert "accepted authored text" not in serialized


def test_unknown_status_is_counted_as_non_terminal_and_returns_false(tmp_path):
    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path,
        review_rows=[
            {
                "id": "private-status-id",
                "status": "unexpected-live-status",
                "new_text": "other authored text",
            }
        ],
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["editor_review"] == {
        "accepted": 0,
        "other_non_terminal": 1,
        "pending": 0,
    }
    assert receipt["apply"] == {"accepted": 0}
    serialized = json.dumps(receipt)
    assert "private-status-id" not in serialized
    assert "unexpected-live-status" not in serialized
    assert "other authored text" not in serialized


def test_nonempty_publication_frontiers_fail_without_leaking_ids(tmp_path):
    active_release = {
        "id": "private-release",
        "state": "prepared",
        "target_batch_id": "private-target",
        "base_sha": "a" * 40,
        "candidate_sha": "b" * 40,
        "generator_id": "private-generator",
        "evidence_hash": "private-evidence",
        "manifest_hash": "private-manifest",
        "membership_hash": "private-membership",
        "schema_version": 1,
    }
    cases = [
        (
            {
                "active_release": active_release,
                "batches": [],
                "operation_frontier": EMPTY_OPERATION_FRONTIER,
            },
            "active_release",
            0,
        ),
        (
            {
                "active_release": None,
                "base_sha": "a" * 40,
                "batches": [
                    {
                        "batch_id": "private-batch",
                        "commit_sha": "b" * 40,
                        "generator_id": "private-generator",
                        "member_count": 1,
                    }
                ],
                "operation_frontier": EMPTY_OPERATION_FRONTIER,
            },
            "ready_to_prepare",
            1,
        ),
        (
            {
                "active_release": None,
                "batches": [],
                "blocked_reason": "missing_batch_evidence",
                "blocked_batch_id": "private-batch",
                "operation_frontier": EMPTY_OPERATION_FRONTIER,
            },
            "blocked",
            0,
        ),
    ]
    for context, reason, queue_count in cases:
        code, receipt, _http_calls, _systemctl_calls = invoke(
            tmp_path, review_rows=[], frontier_context=context
        )
        assert code == 1
        assert receipt["all_queues_empty"] is False
        assert receipt["publication_frontier"]["reason"] == reason
        assert receipt["publication_frontier"]["queue_count"] == queue_count
        serialized = json.dumps(receipt)
        assert "private-release" not in serialized
        assert "private-batch" not in serialized
        assert "missing_batch_evidence" not in serialized


def test_review_non_200_2xx_statuses_fail_closed_before_body_read(tmp_path):
    for status in (201, 202, 203, 206):
        response = Response({"ok": True, "items": []}, status=status)

        code, receipt, _systemctl_calls = run_main(
            tmp_path,
            opener=lambda _request, timeout: response,
        )

        assert code != 0
        assert receipt["all_queues_empty"] is False
        assert receipt["proof_error"] == "http-status-invalid"
        assert response.read_calls == 0


def test_frontier_non_200_2xx_statuses_fail_closed_before_body_read(tmp_path):
    for status in (201, 202, 203, 206):
        review_response = Response({"ok": True, "items": []})
        frontier_response = Response(
            {"ok": True, "context": EMPTY_FRONTIER}, status=status
        )

        def opener(request, timeout):
            if request.full_url.endswith("/review"):
                return review_response
            return frontier_response

        code, receipt, _systemctl_calls = run_main(
            tmp_path,
            opener=opener,
        )

        assert code != 0
        assert receipt["all_queues_empty"] is False
        assert receipt["proof_error"] == "http-status-invalid"
        assert review_response.read_calls == 1
        assert frontier_response.read_calls == 0


def test_review_early_eof_with_real_http_response_returns_one_bounded_receipt(
    tmp_path,
):
    body = json.dumps({"ok": True, "items": []}).encode("utf-8")
    response, _wire_body = real_http_response(
        body, declared_length=len(body) + 64
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return response
        return Response({"ok": True, "context": EMPTY_FRONTIER})

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"


def test_frontier_early_eof_with_real_http_response_returns_one_bounded_receipt(
    tmp_path,
):
    body = json.dumps({"ok": True, "context": EMPTY_FRONTIER}).encode("utf-8")
    review_response = Response({"ok": True, "items": []})
    frontier_response, _wire_body = real_http_response(
        body, declared_length=len(body) + 64
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return review_response
        return frontier_response

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"


def test_review_surplus_wire_bytes_with_real_http_response_fail_closed(tmp_path):
    body = json.dumps({"ok": True, "items": []}).encode("utf-8")
    response, _wire_body = real_http_response(
        body + b'{"status":"pending","private":"row"}',
        declared_length=len(body),
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return response
        return Response({"ok": True, "context": EMPTY_FRONTIER})

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"
    assert "private" not in output


def test_frontier_surplus_wire_bytes_with_real_http_response_fail_closed(tmp_path):
    body = json.dumps({"ok": True, "context": EMPTY_FRONTIER}).encode("utf-8")
    review_response = Response({"ok": True, "items": []})
    frontier_response, _wire_body = real_http_response(
        body + b'{"status":"pending","private":"row"}',
        declared_length=len(body),
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return review_response
        return frontier_response

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"
    assert "private" not in output


def test_review_real_response_timeout_instead_of_eof_fails_closed(tmp_path):
    body = json.dumps({"ok": True, "items": []}).encode("utf-8")
    response, _wire_body = real_http_response(
        body, declared_length=len(body)
    )
    response.fp = TimeoutInsteadOfEof(body)

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return response
        return Response({"ok": True, "context": EMPTY_FRONTIER})

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-unavailable"
    assert "private response remained open" not in output


def test_frontier_real_response_timeout_instead_of_eof_fails_closed(tmp_path):
    review_response = Response({"ok": True, "items": []})
    body = json.dumps({"ok": True, "context": EMPTY_FRONTIER}).encode("utf-8")
    frontier_response, _wire_body = real_http_response(
        body, declared_length=len(body)
    )
    frontier_response.fp = TimeoutInsteadOfEof(body)

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return review_response
        return frontier_response

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-unavailable"
    assert "private response remained open" not in output


def test_review_content_range_fails_before_real_response_body_read(tmp_path):
    body = json.dumps({"ok": True, "items": []}).encode("utf-8")
    response, wire_body = real_http_response(
        body,
        declared_length=len(body),
        extra_headers=[("Content-Range", f"bytes 0-{len(body) - 1}/{len(body) + 64}")],
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return response
        return Response({"ok": True, "context": EMPTY_FRONTIER})

    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"
    assert wire_body.read_calls == 0


def test_frontier_content_range_fails_before_real_response_body_read(tmp_path):
    review_response = Response({"ok": True, "items": []})
    body = json.dumps({"ok": True, "context": EMPTY_FRONTIER}).encode("utf-8")
    frontier_response, wire_body = real_http_response(
        body,
        declared_length=len(body),
        extra_headers=[("Content-Range", f"bytes 0-{len(body) - 1}/{len(body) + 64}")],
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return review_response
        return frontier_response

    code, receipt, _systemctl_calls = run_main(tmp_path, opener=opener)

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"
    assert review_response.read_calls == 1
    assert wire_body.read_calls == 0


def test_real_response_body_counter_observes_production_read_path(tmp_path):
    review_body = json.dumps({"ok": True, "items": []}).encode("utf-8")
    review_response, wire_body = real_http_response(
        review_body, declared_length=len(review_body)
    )

    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return review_response
        return Response({"ok": True, "context": EMPTY_FRONTIER})

    code, receipt, _systemctl_calls = run_main(tmp_path, opener=opener)

    assert code == 0
    assert receipt["all_queues_empty"] is True
    assert wire_body.read_calls == 2


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Content-Length", "24"), ("Content-Length", "24")],
        [("Content-Length", "24"), ("Content-Length", "25")],
        [("Content-Length", "024")],
        [("Content-Length", "-1")],
        [("Content-Length", "+24")],
        [("Content-Length", "24, 24")],
        [("Content-Length", "24"), ("Transfer-Encoding", "chunked")],
        [("Content-Length", "24"), ("Content-Encoding", "gzip")],
    ],
)
def test_response_framing_requires_one_plain_content_length_before_read(
    tmp_path, headers
):
    response = Response({"ok": True, "items": []}, headers=headers)

    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: response,
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-framing-invalid"
    assert response.read_calls == 0


def test_declared_response_too_large_fails_before_body_read(tmp_path):
    response = Response(
        {"ok": True, "items": []},
        headers=[("Content-Length", str(queues.MAX_RESPONSE_BYTES + 1))],
    )

    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: response,
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-too-large"
    assert response.read_calls == 0


def test_response_byte_limit_is_fixed_at_one_mibibyte():
    assert queues.MAX_RESPONSE_BYTES == 1024 * 1024

    response = RawResponse(
        b"",
        headers=[
            ("Content-Length", str(1024 * 1024 + 1)),
            ("Date", "Mon, 07 Sep 2026 15:00:00 GMT"),
        ],
    )
    with pytest.raises(queues.ProofError, match="^http-response-too-large$"):
        queues._read_response(response)
    assert response.read_calls == 0


def test_real_response_rejects_chunk_larger_than_remaining_length():
    body = b'{"ok":true}'

    class OverreadingBody:
        def __init__(self):
            self.read_calls = 0

        def read(self, _amount=-1):
            self.read_calls += 1
            return body + b"surplus" if self.read_calls == 1 else b""

        def flush(self):
            return None

        def close(self):
            return None

    class OverreadingResponse(http.client.HTTPResponse):
        def __init__(self, wire_body):
            self.fp = wire_body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def getheaders(self):
            return [
                ("Content-Length", str(len(body))),
                ("Date", "Mon, 07 Sep 2026 15:00:00 GMT"),
            ]

    wire_body = OverreadingBody()
    with pytest.raises(
        queues.ProofError, match="^http-response-framing-invalid$"
    ):
        queues._read_response(OverreadingResponse(wire_body))
    assert wire_body.read_calls == 1


def test_getcode_failure_returns_one_bounded_receipt(tmp_path):
    class GetcodeFailureResponse(Response):
        def getcode(self):
            raise RuntimeError("private getcode failure detail")

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: GetcodeFailureResponse({}),
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-lifecycle"
    assert "private getcode failure detail" not in output


def test_response_context_exit_failure_returns_one_bounded_receipt(tmp_path):
    class ExitFailureResponse(Response):
        def __exit__(self, *_args):
            raise RuntimeError("private context-exit failure detail")

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: ExitFailureResponse(
            {"ok": True, "items": []}
        ),
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-lifecycle"
    assert "private context-exit failure detail" not in output


def test_unexpected_assertion_emits_one_bounded_defect_receipt(tmp_path):
    class AssertionResponse(Response):
        def getcode(self):
            raise AssertionError("test assertion must escape")

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: AssertionResponse({}),
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert len(output) < 2048
    assert receipt["proof_error"] == "verifier-defect"
    assert "test assertion must escape" not in output


def test_unexpected_type_error_emits_one_bounded_defect_receipt(
    tmp_path, monkeypatch
):
    def verifier_defect(_headers):
        raise TypeError("verifier defect must escape")

    monkeypatch.setattr(queues, "_declared_content_length", verifier_defect)

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: Response({"ok": True, "items": []}),
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert len(output) < 2048
    assert receipt["proof_error"] == "verifier-defect"
    assert "verifier defect must escape" not in output


def test_large_json_integer_returns_one_bounded_receipt(tmp_path):
    raw = b'{"ok":true,"items":[' + b"9" * 5_000 + b"]}"

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: RawResponse(raw),
        raw_output=True,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-malformed"
    assert "Exceeds the limit" not in output


def test_explicit_empty_frontier_context_is_not_replaced_by_default(tmp_path):
    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path,
        review_rows=[],
        frontier_context={},
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "operation-frontier-missing"


def test_incomplete_http_read_returns_one_bounded_receipt(tmp_path):
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    write_observer_env(observer_env)

    class IncompleteResponse(Response):
        def read(self, _size=-1):
            raise http.client.IncompleteRead(b"private partial body", 100)

    output = io.StringIO()
    code = call_main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            *window_fence_args(tmp_path),
        ],
        opener=lambda _request, timeout: IncompleteResponse({}),
        run_systemctl=injected_systemctl()[0],
        utc_now=lambda: datetime.datetime(
            2026, 9, 7, 15, 0, 0, tzinfo=datetime.timezone.utc
        ),
        stdout=output,
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-unavailable"
    assert "private partial body" not in json.dumps(receipt)


def test_deeply_nested_json_returns_one_bounded_receipt(tmp_path, monkeypatch):
    raw = b"[" * 1100 + b"]" * 1100
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    write_observer_env(observer_env)

    def reject_excessive_nesting(*_args, **_kwargs):
        raise RecursionError("private deeply nested response detail")

    monkeypatch.setattr(
        queues,
        "json",
        SimpleNamespace(
            JSONDecodeError=json.JSONDecodeError,
            loads=reject_excessive_nesting,
        ),
    )
    write_window_nonce(tmp_path / "direct-window-nonce.env")
    receipt = queues.prove(
        LEDGER_ORIGIN,
        apply_env,
        observer_env,
        verifier_identity={
            "release_commit": RELEASE_COMMIT,
            "verifier_blob": VERIFIER_BLOB,
        },
        apply_timer_stopped=True,
        window_owner=WINDOW_OWNER,
        window_nonce_file=(tmp_path / "direct-window-nonce.env"),
        window_phase="opening",
        opener=lambda _request, timeout: RawResponse(raw),
        run_systemctl=injected_systemctl()[0],
        systemctl_path="/usr/bin/systemctl",
        read_host_identity=lambda: dict(HOST_IDENTITY),
        utc_now=lambda: datetime.datetime(
            2026, 9, 7, 15, 0, 0, tzinfo=datetime.timezone.utc
        ),
    )

    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-malformed"


def test_clock_provider_exception_returns_one_bounded_receipt(tmp_path):
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    write_observer_env(observer_env)
    output = io.StringIO()

    def unavailable_clock():
        raise RuntimeError("private clock-provider detail")

    code = call_main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            *window_fence_args(tmp_path),
        ],
        opener=lambda _request, timeout: Response({"ok": True, "items": []}),
        run_systemctl=injected_systemctl()[0],
        utc_now=unavailable_clock,
        stdout=output,
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "timestamp-unavailable"
    assert "private clock-provider detail" not in json.dumps(receipt)


def test_fifo_environment_path_fails_before_content_read(tmp_path, monkeypatch):
    fifo = tmp_path / "observer.fifo"
    os.mkfifo(fifo, 0o600)
    original_fdopen = queues.os.fdopen

    class MustNotRead:
        def __init__(self, descriptor, mode):
            self.source = original_fdopen(descriptor, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def fileno(self):
            return self.source.fileno()

        def read(self, *_args):
            raise AssertionError("non-regular environment input was read")

    monkeypatch.setattr(queues.os, "fdopen", MustNotRead)

    with pytest.raises(queues.ProofError, match="^environment-unavailable$"):
        queues._protected_env(fifo, {"SONSTENG_PROD_OBSERVER_BEARER"})


def test_protected_environment_requires_mode_0600(tmp_path):
    environment = tmp_path / "observer.env"
    write_observer_env(environment)
    environment.chmod(0o644)

    with pytest.raises(queues.ProofError, match="^environment-unavailable$"):
        queues._protected_env(
            environment, {"SONSTENG_PROD_OBSERVER_BEARER"}
        )


def test_protected_environment_requires_current_uid(tmp_path, monkeypatch):
    environment = tmp_path / "observer.env"
    write_observer_env(environment)
    current_uid = os.getuid()
    monkeypatch.setattr(queues.os, "getuid", lambda: current_uid + 1)

    with pytest.raises(queues.ProofError, match="^environment-unavailable$"):
        queues._protected_env(
            environment, {"SONSTENG_PROD_OBSERVER_BEARER"}
        )


def test_protected_environment_requires_regular_file():
    with pytest.raises(queues.ProofError, match="^environment-unavailable$"):
        queues._protected_env(
            "/dev/null", {"SONSTENG_PROD_OBSERVER_BEARER"}
        )


def test_protected_environment_refuses_symlink(tmp_path):
    environment = tmp_path / "observer.env"
    write_observer_env(environment)
    linked_environment = tmp_path / "linked-observer.env"
    linked_environment.symlink_to(environment)

    with pytest.raises(queues.ProofError, match="^environment-unavailable$"):
        queues._protected_env(
            linked_environment, {"SONSTENG_PROD_OBSERVER_BEARER"}
        )


@pytest.mark.parametrize("ok", [False, None, "true", 1])
def test_review_rejects_false_empty_envelope(ok):
    with pytest.raises(queues.ProofError, match="^review-rejected$"):
        queues._review_counts({"ok": ok, "items": []})


@pytest.mark.parametrize("ok", [False, None, "true", 1])
def test_frontier_rejects_false_empty_envelope(ok):
    with pytest.raises(
        queues.ProofError, match="^frontier-response-malformed$"
    ):
        queues._frontier_summary({"ok": ok, "context": EMPTY_FRONTIER})


def test_observer_absent_and_timer_disabled_fails_closed(tmp_path):
    code, receipt, http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[], observer=False
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "environment-unavailable"
    assert receipt["publication"] == "observer-env-absent"
    assert receipt["publication_frontier"] is None
    assert (
        receipt["publication_fallback"]
        == "systemd timer disabled and inactive"
    )
    assert receipt["timer"] == {
        "active": False,
        "available": True,
        "enabled": False,
    }
    assert len(http_calls) == 1
    assert http_calls[0][0].full_url.endswith("/review")


def test_observer_absent_and_timer_enabled_returns_false(tmp_path):
    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[], observer=False, timer_enabled=True
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["publication"] == "observer-env-absent"
    assert receipt["publication_fallback"] == "systemd timer not proved off"
    assert receipt["timer"] == {
        "active": False,
        "available": True,
        "enabled": True,
    }


def test_env_host_cannot_redirect_pinned_ledger_calls(tmp_path):
    code, receipt, http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[]
    )

    assert code == 0
    assert receipt["ledger_host"] == "sonsteng-chat.damienriehl.workers.dev"
    assert [call[0].full_url for call in http_calls] == [
        LEDGER_ORIGIN + "/edit/v1/review",
        LEDGER_ORIGIN + "/edit/v1/prod/releases/frontier",
    ]
    assert all("wrong-host.example" not in call[0].full_url for call in http_calls)


def test_disallowed_ledger_origin_fails_before_any_get(tmp_path):
    http_calls = []
    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda request, timeout: http_calls.append((request, timeout)),
        ledger_origin="https://wrong-host.example",
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "ledger-origin-invalid"
    assert http_calls == []


def test_hidden_accepted_row_on_page_two_fails_closed(tmp_path):
    http_calls = []

    def opener(request, timeout):
        http_calls.append((request, timeout))
        if request.full_url.endswith("/review"):
            return Response(
                {
                    "ok": True,
                    "items": [],
                    "has_more": True,
                    "next_cursor": "page-2",
                }
            )
        if request.full_url.endswith("cursor=page-2"):
            return Response(
                {"ok": True, "items": [{"status": "accepted"}]}
            )
        raise AssertionError("unexpected HTTP request")

    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=opener,
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "review-response-malformed"
    assert len(http_calls) == 1


def test_conflicting_review_list_keys_are_a_proof_error(tmp_path):
    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: Response(
            {
                "ok": True,
                "items": [],
                "suggestions": [{"status": "accepted"}],
            }
        ),
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "review-response-malformed"


def test_frontier_omitting_union_required_key_is_a_proof_error(tmp_path):
    for context in (
        {
            "active_release": None,
            "base_sha": None,
            "operation_frontier": EMPTY_OPERATION_FRONTIER,
        },
        {
            "base_sha": None,
            "batches": [],
            "operation_frontier": EMPTY_OPERATION_FRONTIER,
        },
        {
            "active_release": None,
            "batches": [],
            "operation_frontier": EMPTY_OPERATION_FRONTIER,
        },
        {
            "active_release": None,
            "batches": [],
            "blocked_reason": "missing_batch_evidence",
            "operation_frontier": EMPTY_OPERATION_FRONTIER,
        },
    ):
        code, receipt, _http_calls, _systemctl_calls = invoke(
            tmp_path,
            review_rows=[],
            frontier_context=context,
        )

        assert code == 1
        assert receipt["all_queues_empty"] is False
        assert receipt["proof_error"] == "frontier-response-malformed"


def test_duplicate_review_items_are_a_proof_error(tmp_path):
    raw = (
        b'{"ok":true,"items":[{"status":"accepted"}],"items":[]}'
    )
    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: RawResponse(raw),
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-malformed"


def test_duplicate_nested_frontier_key_is_a_proof_error(tmp_path):
    calls = 0

    def opener(request, timeout):
        nonlocal calls
        calls += 1
        if request.full_url.endswith("/review"):
            return Response({"ok": True, "items": []})
        return RawResponse(
            b'{"ok":true,"context":{"active_release":{"id":"private"},'
            b'"active_release":null,"batches":[]}}'
        )

    code, receipt, _systemctl_calls = run_main(tmp_path, opener=opener)

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-response-malformed"
    assert calls == 2
    assert "private" not in json.dumps(receipt)


def test_ledger_state_hash_changes_when_validated_state_changes(tmp_path):
    _code, empty_receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[]
    )
    _code, pending_receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[{"status": "pending"}]
    )

    assert empty_receipt["ledger_state_hash"] != pending_receipt[
        "ledger_state_hash"
    ]


def test_missing_server_date_fails_closed(tmp_path):
    body = json.dumps({"ok": True, "items": []}).encode("utf-8")

    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: RawResponse(
            body, headers=[("Content-Length", str(len(body)))]
        ),
    )

    assert code == 1
    assert receipt["proof_error"] == "http-server-date-invalid"


@pytest.mark.parametrize(
    "date_headers",
    [
        [("Date", "not-an-http-date")],
        [
            ("Date", "Mon, 07 Sep 2026 15:00:00 GMT"),
            ("Date", "Mon, 07 Sep 2026 15:00:01 GMT"),
        ],
    ],
)
def test_malformed_or_duplicate_server_date_fails_closed(
    tmp_path, date_headers
):
    body = json.dumps({"ok": True, "items": []}).encode("utf-8")
    headers = [("Content-Length", str(len(body))), *date_headers]

    code, receipt, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: RawResponse(body, headers=headers),
    )

    assert code == 1
    assert receipt["proof_error"] == "http-server-date-invalid"


@pytest.mark.parametrize(
    "skewed_endpoint",
    [
        pytest.param("review", id="D10-review-clock-skew-call"),
        pytest.param(
            "publication_frontier",
            id="D10-publication-frontier-clock-skew-call",
        ),
    ],
)
def test_server_date_outside_clock_skew_bound_fails_closed(
    tmp_path, skewed_endpoint
):
    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return Response(
                {"ok": True, "items": []},
                server_date=(
                    "Mon, 07 Sep 2026 14:00:00 GMT"
                    if skewed_endpoint == "review"
                    else "Mon, 07 Sep 2026 15:00:00 GMT"
                ),
            )
        return Response(
            {"ok": True, "context": EMPTY_FRONTIER},
            server_date=(
                "Mon, 07 Sep 2026 16:00:01 GMT"
                if skewed_endpoint == "publication_frontier"
                else "Mon, 07 Sep 2026 15:00:01 GMT"
            ),
        )

    code, receipt, _systemctl_calls = run_main(tmp_path, opener=opener)

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-server-date-skew"
    assert (
        abs(receipt["server_date_skew_seconds"][skewed_endpoint]) > 300
    )


@pytest.mark.parametrize("skew", [-300, 300])
def test_server_date_accepts_exact_clock_skew_boundary(skew):
    queues._assert_server_clock_skew(skew)


@pytest.mark.parametrize("skew", [-301, 301])
def test_server_date_refuses_one_second_outside_clock_skew_boundary(skew):
    with pytest.raises(queues.ProofError, match="^http-server-date-skew$"):
        queues._assert_server_clock_skew(skew)


def test_server_date_pair_must_be_monotonic(tmp_path):
    def opener(request, timeout):
        if request.full_url.endswith("/review"):
            return Response(
                {"ok": True, "items": []},
                server_date="Mon, 07 Sep 2026 15:00:01 GMT",
            )
        return Response(
            {"ok": True, "context": EMPTY_FRONTIER},
            server_date="Mon, 07 Sep 2026 15:00:00 GMT",
        )

    code, receipt, _systemctl_calls = run_main(tmp_path, opener=opener)

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-server-date-nonmonotonic"
    assert receipt["server_date_skew_seconds"] == {
        "publication_frontier": -1,
        "review": 1,
    }


def test_host_identity_failure_returns_one_bounded_receipt(tmp_path):
    def unavailable_host_identity():
        raise queues.ProofError("host-identity-unavailable")

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: pytest.fail("network must not run"),
        raw_output=True,
        read_host_identity=unavailable_host_identity,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert len(output) < 2048
    assert receipt["proof_error"] == "host-identity-unavailable"


def test_frontier_variants_reject_unknown_or_incoherent_fields(tmp_path):
    impossible = [
        {**EMPTY_FRONTIER, "unknown": False},
        {
            **EMPTY_FRONTIER,
            "blocked_reason": "missing_batch_evidence",
            "blocked_batch_id": "batch-1",
        },
        {
            **EMPTY_FRONTIER,
            "active_release": {
                "id": "release-1",
                "state": "prepared",
                "target_batch_id": "batch-1",
                "base_sha": "a" * 40,
                "candidate_sha": "b" * 40,
                "generator_id": "generator",
                "evidence_hash": "evidence",
                "manifest_hash": "manifest",
                "membership_hash": "membership",
                "schema_version": 1,
            },
        },
    ]
    for context in impossible:
        code, receipt, _http_calls, _systemctl_calls = invoke(
            tmp_path, review_rows=[], frontier_context=context
        )

        assert code == 1
        assert receipt["proof_error"] == "frontier-response-malformed"


def test_batch_and_active_release_validators_reject_malformed_bounds():
    valid_batch = {
        "batch_id": "batch-1",
        "commit_sha": "a" * 40,
        "generator_id": "generator",
        "member_count": 1,
    }
    valid_release = {
        "id": "release-1",
        "state": "prepared",
        "target_batch_id": "batch-1",
        "base_sha": "a" * 40,
        "candidate_sha": "b" * 40,
        "generator_id": "generator",
        "evidence_hash": "evidence",
        "manifest_hash": "manifest",
        "membership_hash": "membership",
        "schema_version": 1,
    }

    assert queues._valid_batch(valid_batch) is True
    assert queues._valid_batch({**valid_batch, "member_count": True}) is False
    assert queues._valid_batch({**valid_batch, "unknown": "value"}) is False
    assert queues._valid_active_release(valid_release) is True
    assert queues._valid_active_release(
        {**valid_release, "schema_version": 3}
    ) is False
    assert queues._valid_active_release(
        {**valid_release, "evidence_hash": "x" * 257}
    ) is False


def test_frontier_and_review_collection_bounds_fail_closed():
    batch = {
        "batch_id": "batch-1",
        "commit_sha": "a" * 40,
        "generator_id": "generator",
        "member_count": 1,
    }
    oversized_frontier = {
        **EMPTY_FRONTIER,
        "batches": [batch] * (queues.MAX_FRONTIER_ITEMS + 1),
    }

    with pytest.raises(
        queues.ProofError, match="^frontier-response-malformed$"
    ):
        queues._frontier_summary(
            {"ok": True, "context": oversized_frontier}
        )
    with pytest.raises(
        queues.ProofError, match="^review-response-malformed$"
    ):
        queues._review_counts(
            {"ok": True, "items": [{"status": "pending"}] * 100_001}
        )


def test_bounded_frontier_string_limit_is_256_encoded_bytes():
    assert queues.MAX_BOUND_VALUE_BYTES == 256
    assert queues._bounded_string("x" * 256) is True
    assert queues._bounded_string("x" * 257) is False
    assert queues._bounded_string("é" * 128) is True
    assert queues._bounded_string("é" * 129) is False


def test_current_eligible_operation_wire_response_fails_named_closed(tmp_path):
    context = {
        "active_release": None,
        "base_sha": "a" * 40,
        "batches": [],
    }

    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[], frontier_context=context
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "operation-frontier-missing"


def test_current_blocked_operation_wire_response_fails_named_closed(tmp_path):
    # observerPreparationSummary() currently drops both eligible and held/stale
    # operation projections, so their source-faithful observer wires coincide.
    context = {
        "active_release": None,
        "base_sha": "a" * 40,
        "batches": [],
    }

    code, receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[], frontier_context=context
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "operation-frontier-missing"


def test_nonempty_or_blocked_operation_frontier_is_not_empty(tmp_path):
    for operation_frontier in (
        {"pending_operation_count": 1, "blocked_state": "unblocked"},
        {"pending_operation_count": 0, "blocked_state": "blocked"},
    ):
        code, receipt, _http_calls, _systemctl_calls = invoke(
            tmp_path,
            review_rows=[],
            frontier_context={
                **EMPTY_FRONTIER,
                "operation_frontier": operation_frontier,
            },
        )

        assert code == 1
        assert receipt["all_queues_empty"] is False
        assert receipt["publication_frontier"]["operation_frontier"] == operation_frontier


def test_operation_frontier_is_exact_typed_and_bounded(tmp_path):
    malformed = [
        {"pending_operation_count": True, "blocked_state": "unblocked"},
        {"pending_operation_count": -1, "blocked_state": "unblocked"},
        {"pending_operation_count": 100_001, "blocked_state": "unblocked"},
        {"pending_operation_count": 0, "blocked_state": "unknown"},
        {
            "pending_operation_count": 0,
            "blocked_state": "unblocked",
            "extra": False,
        },
    ]
    for operation_frontier in malformed:
        code, receipt, _http_calls, _systemctl_calls = invoke(
            tmp_path,
            review_rows=[],
            frontier_context={
                **EMPTY_FRONTIER,
                "operation_frontier": operation_frontier,
            },
        )

        assert code == 1
        assert receipt["proof_error"] == "operation-frontier-malformed"


def test_missing_fence_assertions_emit_unproven_without_gets(tmp_path):
    cases = [
        {"fence": False},
        {"apply_timer_asserted": False},
        {"window_owner": None},
        {"window_nonce": None},
        {"window_phase": None},
    ]
    for kwargs in cases:
        opener, http_calls = injected_opener([])
        code, receipt, _systemctl_calls = run_main(
            tmp_path, opener=opener, **kwargs
        )

        assert code == 1
        assert receipt["all_queues_empty"] is False
        assert receipt["fence"] == "unproven"
        assert receipt["proof_error"] == "fence-assertion-missing"
        assert receipt["first_get_utc"] is None
        assert receipt["last_get_utc"] is None
        assert http_calls == []


def test_invalid_window_owner_emits_unproven_without_gets(tmp_path):
    opener, http_calls = injected_opener([])

    code, receipt, _systemctl_calls = run_main(
        tmp_path, opener=opener, window_owner="invalid owner"
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["fence"] == "unproven"
    assert receipt["proof_error"] == "fence-assertion-invalid"
    assert receipt["first_get_utc"] is None
    assert receipt["last_get_utc"] is None
    assert http_calls == []


def test_invalid_window_nonce_emits_unproven_without_gets(tmp_path):
    opener, http_calls = injected_opener([])

    code, receipt, _systemctl_calls = run_main(
        tmp_path, opener=opener, window_nonce="not-a-64-hex-nonce"
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["fence"] == "unproven"
    assert receipt["proof_error"] == "fence-assertion-invalid"
    assert http_calls == []


def test_window_nonce_file_refuses_extra_assignments(tmp_path):
    nonce_file = tmp_path / "nonce-with-extra.env"
    nonce_file.write_text(
        f"QUEUE_PROOF_WINDOW_NONCE={window_nonce_for(nonce_file)}\n"
        "EXTRA_VALUE=not-allowed\n",
        encoding="ascii",
    )
    nonce_file.chmod(0o600)

    with pytest.raises(
        queues.ProofError, match="^window-nonce-unavailable$"
    ):
        queues._window_nonce_digest(nonce_file)


def test_apply_timer_must_be_inactive_with_known_enabled_state(tmp_path):
    opener, http_calls = injected_opener([])

    code, receipt, _systemctl_calls = run_main(
        tmp_path, opener=opener, apply_timer_active=True
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "fence-apply-timer-not-stopped"
    assert receipt["fence"] == {
        "apply_timer": {"active": True, "available": True, "enabled": True},
        "apply_timer_stopped": False,
        "proved": False,
        "window_nonce_sha256": window_nonce_digest_for(
            tmp_path / "window-nonce.env"
        ),
        "window_owner": WINDOW_OWNER,
        "window_phase": "opening",
    }
    assert http_calls == []


def test_unavailable_apply_timer_fails_closed_without_gets(tmp_path):
    opener, http_calls = injected_opener([])
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    write_observer_env(observer_env)
    output = io.StringIO()

    def unavailable_systemctl(_argv, **_kwargs):
        raise OSError("systemctl unavailable")

    code = call_main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            *window_fence_args(tmp_path),
        ],
        opener=opener,
        run_systemctl=unavailable_systemctl,
        utc_now=lambda: datetime.datetime(
            2026, 9, 7, 15, 0, 0, tzinfo=datetime.timezone.utc
        ),
        stdout=output,
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "fence-apply-timer-not-stopped"
    assert receipt["fence"] == {
        "apply_timer": {"active": None, "available": False, "enabled": None},
        "apply_timer_stopped": False,
        "proved": False,
        "window_nonce_sha256": window_nonce_digest_for(
            tmp_path / "direct-window-nonce.env"
        ),
        "window_owner": WINDOW_OWNER,
        "window_phase": "opening",
    }
    assert receipt["first_get_utc"] is None
    assert receipt["last_get_utc"] is None
    assert http_calls == []


@pytest.mark.parametrize(
    ("enabled_result", "active_result"),
    [
        ((0, "enabled\n"), (4, "unknown\n")),
        ((4, "unknown\n"), (3, "inactive\n")),
        ((0, ""), (0, "")),
    ],
)
def test_unrecognized_apply_timer_state_cannot_prove_fence(
    tmp_path, enabled_result, active_result
):
    def unrecognized_systemctl(argv, **_kwargs):
        returncode, stdout = (
            enabled_result if "is-enabled" in argv else active_result
        )
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    code, receipt, _calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: pytest.fail("GET must not run"),
        run_systemctl=unrecognized_systemctl,
    )

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "fence-apply-timer-not-stopped"
    assert receipt["fence"]["apply_timer"] == {
        "active": None,
        "available": False,
        "enabled": None,
    }
    assert receipt["fence"]["proved"] is False


def test_timer_state_ignores_inherited_path_systemctl_shim(tmp_path, monkeypatch):
    trusted_systemctl = tmp_path / "trusted-systemctl"
    trusted_systemctl.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = "is-enabled" ]; then\n'
        "  printf 'enabled\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'inactive\\n'\n"
        "exit 3\n",
        encoding="utf-8",
    )
    trusted_systemctl.chmod(0o700)

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim_marker = tmp_path / "path-shim-invoked"
    path_shim = shim_dir / "systemctl"
    path_shim.write_text(
        "#!/bin/sh\n"
        f": > {shim_marker}\n"
        "printf 'enabled\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path_shim.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "private-counterfeit-bus")
    trusted_systemctl_path = str(trusted_systemctl.resolve())
    state = queues._timer_state(
        queues.APPLY_TIMER_UNIT,
        subprocess.run,
        trusted_systemctl_path,
    )

    assert state == {"active": False, "available": True, "enabled": True}
    assert not shim_marker.exists()


def test_systemctl_resolution_uses_cs_path_not_inherited_path(
    tmp_path, monkeypatch
):
    trusted_dir = tmp_path / "trusted-bin"
    trusted_dir.mkdir()
    trusted_systemctl = trusted_dir / "systemctl"
    trusted_systemctl.write_text("trusted\n", encoding="ascii")
    poisoned_dir = tmp_path / "poisoned-bin"
    poisoned_dir.mkdir()
    (poisoned_dir / "systemctl").write_text("poisoned\n", encoding="ascii")
    monkeypatch.setenv("PATH", str(poisoned_dir))
    monkeypatch.setattr(queues.os, "confstr", lambda _name: str(trusted_dir))
    observed_paths = []

    def record_resolution(_name, path=None):
        observed_paths.append(path)
        return str(trusted_systemctl)

    monkeypatch.setattr(queues.shutil, "which", record_resolution)

    assert queues._resolve_systemctl_path() == str(trusted_systemctl.resolve())
    assert observed_paths == [str(trusted_dir)]


def test_production_opener_ignores_proxy_and_tls_environment(
    tmp_path, monkeypatch
):
    empty_ca_file = tmp_path / "attacker-ca-data"
    empty_ca_file.touch()
    empty_ca_dir = tmp_path / "attacker-ca-directory"
    empty_ca_dir.mkdir()
    keylog_file = tmp_path / "tls-keys.log"
    poisoned_environment = {
        "HTTP_PROXY": "http://upper-http.invalid:9443",
        "HTTPS_PROXY": "http://upper-https.invalid:9443",
        "ALL_PROXY": "http://upper-all.invalid:9443",
        "NO_PROXY": "sonsteng-chat.damienriehl.workers.dev",
        "http_proxy": "http://lower-http.invalid:9443",
        "https_proxy": "http://lower-https.invalid:9443",
        "all_proxy": "http://lower-all.invalid:9443",
        "no_proxy": "sonsteng-chat.damienriehl.workers.dev",
        "SSL_CERT_FILE": str(empty_ca_file),
        "SSL_CERT_DIR": str(empty_ca_dir),
        "SSLKEYLOGFILE": str(keylog_file),
    }
    for name, value in poisoned_environment.items():
        monkeypatch.setenv(name, value)

    opener = queues._production_opener()
    handlers = opener.__self__.handlers
    https_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, queues.urllib.request.HTTPSHandler)
    ]
    assert len(https_handlers) == 1
    production_context = https_handlers[0]._context
    default_context = ssl.create_default_context()

    assert production_context.verify_mode == ssl.CERT_REQUIRED
    assert production_context.check_hostname is True
    assert production_context.cert_store_stats()["x509_ca"] > 0
    assert default_context.cert_store_stats()["x509_ca"] == 0
    assert production_context.keylog_filename is None
    assert default_context.keylog_filename == str(keylog_file)
    assert production_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert production_context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED
    assert len(production_context.get_ciphers()) == 9
    assert {
        cipher["name"]
        for cipher in production_context.get_ciphers()
        if cipher["protocol"] == "TLSv1.2"
    } == {
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
    }
    proxy_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, queues.urllib.request.ProxyHandler)
    ]
    assert proxy_handlers == []
    assert any(isinstance(handler, queues._NoRedirect) for handler in handlers)


def test_tls_context_setup_failure_returns_one_bounded_false_receipt(
    tmp_path, monkeypatch
):
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    write_observer_env(observer_env)
    output = io.StringIO()

    def context_failure(_protocol):
        raise OSError("private trust-store setup detail")

    monkeypatch.setattr(queues.ssl, "SSLContext", context_failure)

    code = call_main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            *window_fence_args(tmp_path),
        ],
        run_systemctl=lambda *_args, **_kwargs: pytest.fail(
            "systemctl must not run after TLS setup failure"
        ),
        stdout=output,
    )
    serialized = output.getvalue()
    receipt = json.loads(serialized)

    assert code == 1
    assert serialized.count("\n") == 1
    assert len(serialized) < 2048
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "https-client-unavailable"
    assert "private trust-store setup detail" not in serialized


def test_invalid_release_identity_fails_before_systemctl_or_network(tmp_path):
    output = io.StringIO()

    code = call_main(
        [
            *release_identity_args(release_commit="not-a-reviewed-commit"),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(tmp_path / "missing-apply.env"),
            "--observer-env-file",
            str(tmp_path / "missing-observer.env"),
            *window_fence_args(tmp_path),
        ],
        opener=lambda *_args, **_kwargs: pytest.fail("network must not run"),
        run_systemctl=lambda *_args, **_kwargs: pytest.fail(
            "systemctl must not run"
        ),
        stdout=output,
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "release-identity-invalid"
    assert receipt["verifier_identity"] == {
        "release_commit": None,
        "verifier_blob": None,
    }


@pytest.mark.parametrize("identity_field", ["release_commit", "verifier_blob"])
@pytest.mark.parametrize(
    "invalid_value",
    [pytest.param("a" * 39, id="39-lowercase-hex"),
     pytest.param("a" * 41, id="41-lowercase-hex"),
     pytest.param("A" * 40, id="40-uppercase-hex")],
)
def test_D07_release_identity_requires_exactly_40_lowercase_hex_characters(
    identity_field, invalid_value
):
    identity = {
        "release_commit": RELEASE_COMMIT,
        "verifier_blob": VERIFIER_BLOB,
    }
    identity[identity_field] = invalid_value

    with pytest.raises(
        queues.ProofError, match="^release-identity-invalid$"
    ):
        queues._release_identity(
            identity["release_commit"], identity["verifier_blob"]
        )


def test_release_identity_measures_its_own_git_blob():
    identity = queues._release_identity(RELEASE_COMMIT, VERIFIER_BLOB)

    assert identity == {
        "release_commit": RELEASE_COMMIT,
        "verifier_blob": VERIFIER_BLOB,
    }

    with pytest.raises(queues.ProofError, match="^verifier-blob-mismatch$"):
        queues._release_identity(RELEASE_COMMIT, "0" * 40)


def test_release_identity_refuses_when_own_source_is_unreadable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(queues, "__file__", str(tmp_path / "missing.py"))

    with pytest.raises(
        queues.ProofError, match="^verifier-identity-unreadable$"
    ):
        queues._release_identity(RELEASE_COMMIT, VERIFIER_BLOB)


def test_N02_interrupt_during_proof_retains_established_verifier_identity(
    monkeypatch,
):
    args = SimpleNamespace(
        release_commit=RELEASE_COMMIT,
        verifier_blob=VERIFIER_BLOB,
        preflight=False,
        ledger_origin=LEDGER_ORIGIN,
        apply_env_file=None,
        observer_env_file=None,
        apply_timer_stopped=True,
        window_owner=WINDOW_OWNER,
        window_nonce_file=None,
        window_phase="opening",
    )

    def interrupt_during_proof(*_args, **_kwargs):
        raise queues._SignalInterrupted(signal.SIGINT)

    monkeypatch.setattr(queues, "prove", interrupt_during_proof)

    with pytest.raises(queues._ProofInterrupted) as interrupted:
        queues._run_proof(
            args,
            opener=object(),
            run_systemctl=None,
            utc_now=lambda: None,
            systemctl_path=queues.SYSTEMCTL_PATH,
            read_host_identity=None,
            process_environment={"LC_ALL": "C"},
            isolated=True,
            tls_handshake=None,
        )

    assert interrupted.value.returncode == 130
    assert interrupted.value.receipt["all_queues_empty"] is False
    assert interrupted.value.receipt["proof_error"] == "verifier-interrupted"
    assert interrupted.value.receipt["verifier_identity"] == {
        "release_commit": RELEASE_COMMIT,
        "verifier_blob": VERIFIER_BLOB,
    }


@pytest.mark.parametrize(
    ("source_mode", "expected_error"),
    [
        ("mismatch", "verifier-blob-mismatch"),
        ("unreadable", "verifier-identity-unreadable"),
    ],
)
def test_self_identity_failures_emit_one_bounded_receipt(
    tmp_path, monkeypatch, source_mode, expected_error
):
    if source_mode == "mismatch":
        changed_source = tmp_path / "changed-verifier.py"
        changed_source.write_bytes(
            (TOOLS / "prove_queues_empty.py").read_bytes() + b"\n"
        )
        monkeypatch.setattr(queues, "__file__", str(changed_source))
    else:
        monkeypatch.setattr(queues, "__file__", str(tmp_path / "missing.py"))
    code, serialized, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda *_args, **_kwargs: pytest.fail("network must not run"),
        run_systemctl=lambda *_args, **_kwargs: pytest.fail(
            "systemctl must not run"
        ),
        raw_output=True,
    )
    receipt = json.loads(serialized)

    assert code == 1
    assert serialized.count("\n") == 1
    assert len(serialized) < 2048
    assert receipt["proof_error"] == expected_error


@pytest.mark.parametrize(
    "variable",
    ["LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_ARBITRARY", "OPENSSL_CONF"],
)
def test_hostile_loader_and_crypto_environment_is_refused(
    tmp_path, variable
):
    hostile_environment = {"LC_ALL": "C", variable: "private-hostile-value"}
    code, serialized, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda *_args, **_kwargs: pytest.fail("network must not run"),
        run_systemctl=lambda *_args, **_kwargs: pytest.fail(
            "systemctl must not run"
        ),
        raw_output=True,
        process_environment=hostile_environment,
    )
    receipt = json.loads(serialized)

    assert code == 1
    assert serialized.count("\n") == 1
    assert len(serialized) < 2048
    assert receipt["proof_error"] == "environment-hostile"
    assert "private-hostile-value" not in serialized


def test_preflight_measures_identity_trust_store_and_systemctl():
    expected_bundle = pathlib.Path(queues.SYSTEM_CA_BUNDLE).read_bytes()
    expected_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    expected_context.load_verify_locations(cafile=queues.SYSTEM_CA_BUNDLE)
    output = io.StringIO()

    code = call_main(
        preflight_args(),
        stdout=output,
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    receipt = json.loads(output.getvalue())

    assert code == 0
    assert receipt["all_queues_empty"] is False
    assert receipt["preflight"]["ready"] is True
    assert receipt["preflight"]["https_handshake"] == successful_handshake(
        None, None
    )
    assert receipt["preflight"]["systemctl_path"] == queues.SYSTEMCTL_PATH
    assert receipt["preflight"]["system_ca_bundle"]["path"] == (
        queues.SYSTEM_CA_BUNDLE
    )
    assert receipt["preflight"]["system_ca_bundle"]["sha256"] == (
        hashlib.sha256(expected_bundle).hexdigest()
    )
    assert receipt["preflight"]["system_ca_bundle"]["x509_ca_count"] == (
        expected_context.cert_store_stats()["x509_ca"]
    )
    assert receipt["verifier_identity"]["verifier_blob"] == VERIFIER_BLOB


def test_preflight_refuses_missing_trusted_systemctl(monkeypatch):
    monkeypatch.setattr(queues, "SYSTEMCTL_PATH", None)
    output = io.StringIO()

    code = call_main(
        preflight_args(), stdout=output
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["preflight"] is None
    assert receipt["proof_error"] == "systemctl-unavailable"


def test_preflight_refuses_missing_system_ca_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(
        queues, "SYSTEM_CA_BUNDLE", str(tmp_path / "missing-ca-bundle")
    )
    output = io.StringIO()

    code = call_main(
        preflight_args(), stdout=output
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["preflight"] is None
    assert receipt["proof_error"] == "https-client-unavailable"


def test_preflight_performs_handshake_to_allowlisted_host():
    observed = []

    def handshake(ledger_host, context):
        observed.append((ledger_host, context))
        return successful_handshake(ledger_host, context)

    output = io.StringIO()
    code = call_main(
        preflight_args(),
        stdout=output,
        systemctl_path=queues.SYSTEMCTL_PATH,
        tls_handshake=handshake,
    )
    receipt = json.loads(output.getvalue())

    assert code == 0
    assert len(observed) == 1
    assert observed[0][0] == "sonsteng-chat.damienriehl.workers.dev"
    assert observed[0][1].minimum_version == ssl.TLSVersion.TLSv1_2
    assert observed[0][1].maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED
    assert receipt["preflight"]["https_handshake"]["protocol"] == "TLSv1.3"


def test_preflight_handshake_failure_fails_closed():
    def unavailable_handshake(_ledger_host, _context):
        raise queues.ProofError("https-handshake-unavailable")

    output = io.StringIO()
    code = call_main(
        preflight_args(),
        stdout=output,
        systemctl_path=queues.SYSTEMCTL_PATH,
        tls_handshake=unavailable_handshake,
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["preflight"] is None
    assert receipt["proof_error"] == "https-handshake-unavailable"


@pytest.mark.parametrize("protocol", ["TLSv1.2", "TLSv1.3"])
def test_tls_handshake_uses_allowlisted_destination_and_sni(
    monkeypatch, protocol
):
    observed = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def version(self):
            return protocol

        def cipher(self):
            return ("TLS_AES_256_GCM_SHA384", protocol, 256)

    class Transport:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Context:
        def wrap_socket(self, transport, *, server_hostname):
            observed.append(("wrap", transport, server_hostname))
            return Connection()

    transport = Transport()

    def connect(destination, *, timeout):
        observed.append(("connect", destination, timeout))
        return transport

    monkeypatch.setattr(queues.socket, "create_connection", connect)

    result = queues._tls_handshake(
        "sonsteng-chat.damienriehl.workers.dev", Context()
    )

    assert observed == [
        (
            "connect",
            ("sonsteng-chat.damienriehl.workers.dev", 443),
            queues.TIMEOUT_SECONDS,
        ),
        ("wrap", transport, "sonsteng-chat.damienriehl.workers.dev"),
    ]
    assert result == {
        "cipher": "TLS_AES_256_GCM_SHA384",
        "protocol": protocol,
        "secret_bits": 256,
    }


def test_tls_handshake_maps_transport_failure_to_bounded_error(monkeypatch):
    def unavailable(_destination, *, timeout):
        raise OSError(f"private transport failure after {timeout}")

    monkeypatch.setattr(queues.socket, "create_connection", unavailable)

    with pytest.raises(
        queues.ProofError, match="^https-handshake-unavailable$"
    ):
        queues._tls_handshake(
            "sonsteng-chat.damienriehl.workers.dev", object()
        )


@pytest.mark.parametrize(
    ("protocol", "cipher"),
    [
        ("TLSv1.1", ("TLS_AES_256_GCM_SHA384", "TLSv1.1", 256)),
        ("TLSv1.3", None),
        ("TLSv1.3", ("", "TLSv1.3", 256)),
        ("TLSv1.3", ("TLS_AES_256_GCM_SHA384", "TLSv1.3", True)),
        ("TLSv1.3", ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 0)),
    ],
)
def test_tls_handshake_refuses_malformed_negotiated_values(
    monkeypatch, protocol, cipher
):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def version(self):
            return protocol

        def cipher(self):
            return cipher

    class Transport:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Context:
        def wrap_socket(self, _transport, *, server_hostname):
            assert server_hostname == "sonsteng-chat.damienriehl.workers.dev"
            return Connection()

    monkeypatch.setattr(
        queues.socket,
        "create_connection",
        lambda _destination, *, timeout: Transport(),
    )

    with pytest.raises(
        queues.ProofError, match="^https-handshake-invalid$"
    ):
        queues._tls_handshake(
            "sonsteng-chat.damienriehl.workers.dev", Context()
        )


def test_host_identity_hashes_measured_machine_and_boot_ids(
    tmp_path, monkeypatch
):
    machine_id = b"0123456789abcdef0123456789abcdef\n"
    boot_id = b"12345678-1234-5678-9abc-def012345678\n"
    machine_path = tmp_path / "machine-id"
    boot_path = tmp_path / "boot-id"
    machine_path.write_bytes(machine_id)
    boot_path.write_bytes(boot_id)
    monkeypatch.setattr(queues, "MACHINE_ID_PATH", str(machine_path))
    monkeypatch.setattr(queues, "BOOT_ID_PATH", str(boot_path))

    assert queues._host_identity() == {
        "boot_id_sha256": hashlib.sha256(
            b"boot-id\0" + boot_id.strip()
        ).hexdigest(),
        "machine_id_sha256": hashlib.sha256(
            b"machine-id\0" + machine_id.strip()
        ).hexdigest(),
    }


@pytest.mark.parametrize("content", [b"not-an-id\n", b"a" * 257])
def test_host_identity_refuses_malformed_or_oversized_input(
    tmp_path, monkeypatch, content
):
    machine_path = tmp_path / "machine-id"
    boot_path = tmp_path / "boot-id"
    machine_path.write_bytes(content)
    boot_path.write_text(
        "12345678-1234-5678-9abc-def012345678\n", encoding="ascii"
    )
    monkeypatch.setattr(queues, "MACHINE_ID_PATH", str(machine_path))
    monkeypatch.setattr(queues, "BOOT_ID_PATH", str(boot_path))

    with pytest.raises(
        queues.ProofError, match="^host-identity-unavailable$"
    ):
        queues._host_identity()


def test_host_identity_refuses_symlink_input(tmp_path, monkeypatch):
    real_machine_id = tmp_path / "real-machine-id"
    real_machine_id.write_text("0123456789abcdef0123456789abcdef\n")
    linked_machine_id = tmp_path / "machine-id"
    linked_machine_id.symlink_to(real_machine_id)
    boot_path = tmp_path / "boot-id"
    boot_path.write_text("12345678-1234-5678-9abc-def012345678\n")
    monkeypatch.setattr(queues, "MACHINE_ID_PATH", str(linked_machine_id))
    monkeypatch.setattr(queues, "BOOT_ID_PATH", str(boot_path))

    with pytest.raises(
        queues.ProofError, match="^host-identity-unavailable$"
    ):
        queues._host_identity()


def test_host_identity_refuses_nonregular_input_before_read(
    tmp_path, monkeypatch
):
    identity_path = tmp_path / "identity"
    identity_path.write_text("0123456789abcdef0123456789abcdef\n")
    real_fstat = os.fstat

    def character_device_stat(descriptor):
        measured = real_fstat(descriptor)
        return SimpleNamespace(
            st_mode=stat.S_IFCHR | 0o600,
            st_uid=measured.st_uid,
        )

    monkeypatch.setattr(queues.os, "fstat", character_device_stat)

    with pytest.raises(
        queues.ProofError, match="^host-identity-unavailable$"
    ):
        queues._read_bounded_identity(
            identity_path, pattern=re.compile(r"[0-9a-f]{32}")
        )


def test_trusted_systemctl_is_available_on_test_host():
    assert queues.SYSTEMCTL_PATH is not None, (
        "systemctl must resolve from CS_PATH; this host-dependent requirement "
        "is a failure, never a skip"
    )


def test_documented_invocation_ignores_shell_path_and_cwd_or_refuses(tmp_path):
    executable_claims = runbook_executable_claims()
    loader_claim = executable_claims["loader_boundary"]
    closed_stdout_claim = executable_claims["launcher_closed_stdout"]
    checkout = tmp_path / "trusted-checkout"
    checkout.mkdir()
    verifier = checkout / "tools/prove_queues_empty.py"
    verifier.parent.mkdir()
    verifier.write_bytes((TOOLS / "prove_queues_empty.py").read_bytes())
    git = ["/usr/bin/git", "-C", str(checkout)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "tools/prove_queues_empty.py"], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=Queue Proof Test",
            "-c",
            "user.email=queue-proof-test.invalid",
            "commit",
            "-qm",
            "test fixture",
        ],
        check=True,
    )
    wrong_cwd = tmp_path / "wrong-cwd"
    wrong_cwd.mkdir()
    shim_dir = tmp_path / "poisoned-path"
    shim_dir.mkdir()
    poison_marker = tmp_path / "counterfeit-command-ran"
    loader_marker = tmp_path / "preload-constructor-ran"
    preload_source = tmp_path / "preload.c"
    preload_object = tmp_path / "preload.so"
    preload_source.write_text(
        """
#include <fcntl.h>
#include <unistd.h>

__attribute__((constructor)) static void mark_loader_execution(void) {
    const char *path = <loader-marker>;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0) return;
    (void)write(fd, "x", 1);
    (void)close(fd);
}
""".replace("<loader-marker>", json.dumps(str(loader_marker))),
        encoding="ascii",
    )
    subprocess.run(
        [
            "/usr/bin/cc",
            "-shared",
            "-fPIC",
            "-o",
            str(preload_object),
            str(preload_source),
        ],
        check=True,
    )
    for command_name in ("python3", "git"):
        shim = shim_dir / command_name
        shim.write_text(
            "#!/bin/sh\n"
            ': > "$POISON_MARKER"\n'
            "printf '%s\\n' '{\"all_queues_empty\":true}'\n",
            encoding="utf-8",
        )
        shim.chmod(0o700)

    release_commit = subprocess.run(
        [*git, "rev-parse", "--verify", "HEAD^{commit}"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()
    verifier_blob = subprocess.run(
        [*git, "rev-parse", f"{release_commit}:tools/prove_queues_empty.py"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    marked = runbook.split("<!-- queue-proof-launcher:start -->", 1)[1].split(
        "<!-- queue-proof-launcher:end -->", 1
    )[0]
    fenced = textwrap.dedent(marked).strip()
    assert fenced.startswith("```bash\n") and fenced.endswith("\n```")
    launcher = fenced.removeprefix("```bash\n").removesuffix("\n```")
    receipt_path = tmp_path / "opening-receipt.json"
    nonce_path = tmp_path / "window-nonce.env"
    nonce_digest = write_window_nonce(nonce_path)
    launcher = (
        launcher.replace(
            "/home/damienriehl/.local/share/sonsteng-daemon/checkout",
            str(checkout),
        )
        .replace("<reviewed-release-commit-SHA>", release_commit)
        .replace("<reviewed-verifier-Git-blob-OID>", verifier_blob)
        .replace("<opaque-Packet-D-window-id>", WINDOW_OWNER)
        .replace("<absolute-mode-0600-window-nonce-file>", str(nonce_path))
        .replace("<absolute-opening-receipt-path>.json", str(receipt_path))
        .replace(
            "/home/damienriehl/.config/sonsteng-apply/env",
            str(tmp_path / "missing-apply.env"),
        )
        .replace(
            "/home/damienriehl/.config/sonsteng-release-observer/env",
            str(tmp_path / "missing-observer.env"),
        )
    )
    poison = r'''
python3() { : > "$POISON_MARKER"; }
git() { : > "$POISON_MARKER"; }
command() { : > "$POISON_MARKER"; return 99; }
function /usr/bin/python3 { : > "$POISON_MARKER"; }
function /usr/bin/git { : > "$POISON_MARKER"; }
builtin export LD_PRELOAD=<preload-object> LD_LIBRARY_PATH=/private/loader LD_AUDIT=
builtin export LD_ARBITRARY=private OPENSSL_CONF=/private/openssl.cnf
'''.replace("<preload-object>", str(preload_object))
    environment = {
        "PATH": str(shim_dir),
        "POISON_MARKER": str(poison_marker),
    }

    def invoke_launcher(
        rendered_launcher=launcher, prelude=poison, *, close_stdout=False
    ):
        shell = (
            prelude
            + ("exec 1>&-\n" if close_stdout else "")
            + rendered_launcher
            + '\nexit "$opening_queue_proof_rc"\n'
        )
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", shell],
            cwd=wrong_cwd,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )

    invoked = invoke_launcher()
    assert receipt_path.read_text(encoding="utf-8"), (
        invoked.returncode,
        invoked.stderr,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert invoked.returncode == 1
    assert invoked.stdout == receipt_path.read_text(encoding="utf-8")
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "fence-apply-timer-not-stopped"
    assert receipt["verifier_identity"] == {
        "release_commit": release_commit,
        "verifier_blob": verifier_blob,
    }
    assert receipt["fence"]["window_nonce_sha256"] == nonce_digest
    assert receipt["fence"]["window_phase"] == "opening"
    assert not poison_marker.exists()
    preload_constructor_ran_with_exec_c = loader_marker.exists()
    assert preload_constructor_ran_with_exec_c is False

    receipt_path.unlink()
    closed_stdout = invoke_launcher(close_stdout=True)

    assert {
        "returncode": closed_stdout.returncode,
        "receipt_path_reserved": receipt_path.exists(),
    } == closed_stdout_claim
    assert closed_stdout.stdout == ""
    assert closed_stdout.stderr == ""
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    relative_receipt_launcher = launcher.replace(
        f"run_queue_proof opening '{receipt_path}'",
        "run_queue_proof opening 'relative-receipt.json'",
    )
    relative_receipt = invoke_launcher(relative_receipt_launcher)

    assert relative_receipt.returncode == 68
    assert relative_receipt.stdout == ""
    assert not receipt_path.exists()
    assert not (wrong_cwd / "relative-receipt.json").exists()
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    no_isolated_mode_launcher = launcher.replace(
        "/usr/bin/python3 -I -B", "/usr/bin/python3 -B"
    )
    no_isolated_mode = invoke_launcher(no_isolated_mode_launcher)
    no_isolated_mode_receipt = json.loads(
        receipt_path.read_text(encoding="utf-8")
    )

    assert no_isolated_mode.returncode == 1
    assert no_isolated_mode_receipt["proof_error"] == (
        "runtime-isolation-required"
    )
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    receipt_path.unlink()
    no_inner_env_clear_launcher = launcher.replace(
        "/usr/bin/env -i LC_ALL=C", "/usr/bin/env LC_ALL=C"
    )
    no_inner_env_clear = invoke_launcher(no_inner_env_clear_launcher)
    no_inner_env_clear_receipt = json.loads(
        receipt_path.read_text(encoding="utf-8")
    )

    assert no_inner_env_clear.returncode == 1
    assert no_inner_env_clear_receipt["proof_error"] == "environment-hostile"
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    receipt_path.unlink()
    no_loader_clear_launcher = launcher.replace(
        "builtin exec -c /usr/bin/env QUEUE_PROOF_ENV_SCRUB_REQUIRED=1",
        "builtin exec /usr/bin/env QUEUE_PROOF_ENV_SCRUB_REQUIRED=1",
    )
    no_loader_clear = invoke_launcher(no_loader_clear_launcher)
    no_loader_clear_receipt = json.loads(
        receipt_path.read_text(encoding="utf-8")
    )

    assert no_loader_clear.returncode == 1
    assert {
        "preload_constructor_ran_with_exec_c": (
            preload_constructor_ran_with_exec_c
        ),
        "preload_constructor_ran_without_exec_c": loader_marker.exists(),
        "verifier_detected_missing_exec_c": (
            no_loader_clear_receipt["proof_error"] != receipt["proof_error"]
        ),
    } == loader_claim
    assert not poison_marker.exists()
    receipt_path.unlink()
    loader_marker.unlink()

    wrong_blob_launcher = launcher.replace(verifier_blob, "0" * 40)
    wrong_blob = invoke_launcher(wrong_blob_launcher)

    assert wrong_blob.returncode == 73
    assert wrong_blob.stdout == ""
    assert not receipt_path.exists()
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    verifier.write_bytes(verifier.read_bytes() + b"\n")
    receipt_path.unlink(missing_ok=True)
    dirty_verifier = invoke_launcher()

    assert dirty_verifier.returncode == 74
    assert dirty_verifier.stdout == ""
    assert not receipt_path.exists()
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    receipt_path.unlink(missing_ok=True)
    (checkout / "later-commit.txt").write_text("later\n", encoding="utf-8")
    subprocess.run([*git, "add", "later-commit.txt"], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=Queue Proof Test",
            "-c",
            "user.email=queue-proof-test.invalid",
            "commit",
            "-qm",
            "later fixture commit",
        ],
        check=True,
    )
    wrong_head = invoke_launcher()

    assert wrong_head.returncode == 72
    assert wrong_head.stdout == ""
    assert not receipt_path.exists()
    assert not poison_marker.exists()
    assert not loader_marker.exists()

    receipt_path.unlink(missing_ok=True)
    counterfeit_function = r'''
run_queue_proof() {
  : > "$POISON_MARKER"
  printf '%s\n' '{"all_queues_empty":true}'
}
builtin readonly -f run_queue_proof
'''
    preexisting_function = invoke_launcher(
        prelude=poison + counterfeit_function
    )

    assert preexisting_function.returncode == 69
    assert preexisting_function.stdout == ""
    assert not receipt_path.exists()
    assert not poison_marker.exists()
    assert not loader_marker.exists()


def test_runbook_launcher_keeps_structural_loader_and_path_guards():
    runbook_executable_claims()
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    marked = runbook.split("<!-- queue-proof-launcher:start -->", 1)[1].split(
        "<!-- queue-proof-launcher:end -->", 1
    )[0]
    assert re.search(
        r"builtin exec -c /usr/bin/env QUEUE_PROOF_ENV_SCRUB_REQUIRED=1\s+"
        r"\\\s*/usr/bin/env -i LC_ALL=C",
        marked,
    )
    assert re.search(
        r'case "\$receipt_path" in\s+/\*\) ;;\s+\*\) return 68 ;;',
        marked,
    )
    assert "builtin test -e /proc/self/fd/1 || return 68" in marked


def test_runbook_structurally_requires_absolute_create_new_receipt_path():
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    receipt_path_paragraphs = [
        " ".join(paragraph.split()).casefold()
        for paragraph in re.split(r"\n\s*\n", runbook)
        if "receipt path" in paragraph.casefold()
    ]

    assert any(
        "absolute" in paragraph
        and "create-new" in paragraph
        and "0600" in paragraph
        for paragraph in receipt_path_paragraphs
    )


def _documented_receipt_validator():
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    marked = runbook.split(
        "<!-- queue-proof-receipt-validator:start -->", 1
    )[1].split("<!-- queue-proof-receipt-validator:end -->", 1)[0]
    fenced = textwrap.dedent(marked).strip()
    assert fenced.startswith("```bash\n") and fenced.endswith("\n```")
    return fenced.removeprefix("```bash\n").removesuffix("\n```")


def _validate_documented_receipt(validator, path):
    return subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            validator.replace(
                "<absolute-opening-receipt-path>.json", str(path)
            )
            + '\nexit "$opening_receipt_validation_rc"\n',
        ],
        capture_output=True,
        check=False,
    )


_ADD_UNEXPECTED_KEY = object()


def test_documented_receipt_validator_rejects_truncated_or_incomplete_receipt(
    tmp_path,
):
    validator = _documented_receipt_validator()
    valid_path = tmp_path / "valid-receipt.json"
    code, valid_receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[]
    )
    assert code == 0
    valid_path.write_text(json.dumps(valid_receipt), encoding="utf-8")

    accepted = _validate_documented_receipt(validator, valid_path)
    assert accepted.returncode == 0
    assert accepted.stdout == accepted.stderr == b""

    incomplete_receipts = [
        b'{"all_queues_empty":true,',
        json.dumps({**valid_receipt, "all_queues_empty": False}).encode(),
        json.dumps({**valid_receipt, "unexpected": None}).encode(),
        json.dumps(
            {
                key: value
                for key, value in valid_receipt.items()
                if key != "last_get_utc"
            }
        ).encode(),
        json.dumps(
            {**queues._new_receipt(), "all_queues_empty": True}
        ).encode(),
    ]
    for index, payload in enumerate(incomplete_receipts):
        invalid_path = tmp_path / f"invalid-receipt-{index}.json"
        invalid_path.write_bytes(payload)
        rejected = _validate_documented_receipt(validator, invalid_path)
        assert rejected.returncode == 75
        assert rejected.stdout == rejected.stderr == b""


@pytest.mark.parametrize(
    ("property_path", "invalid_value"),
    [
        pytest.param(("all_queues_empty",), False, id="V01-all-queues-empty"),
        pytest.param(
            ("apply",), _ADD_UNEXPECTED_KEY, id="V02-apply-exact-shape"
        ),
        pytest.param(("apply", "accepted"), 1, id="V02-apply-accepted"),
        pytest.param(
            ("editor_review",),
            _ADD_UNEXPECTED_KEY,
            id="V02-review-exact-shape",
        ),
        pytest.param(
            ("editor_review", "pending"), 1, id="V02-review-counts-zero"
        ),
        pytest.param(
            ("fence",), _ADD_UNEXPECTED_KEY, id="V02-fence-exact-shape"
        ),
        pytest.param(
            ("publication_frontier",),
            _ADD_UNEXPECTED_KEY,
            id="V02-frontier-exact-shape",
        ),
        pytest.param(
            ("host_identity",),
            _ADD_UNEXPECTED_KEY,
            id="V02-host-identity-exact-shape",
        ),
        pytest.param(
            ("host_identity", "boot_id_sha256"),
            "g" * 64,
            id="V02-host-identity-digests",
        ),
        pytest.param(
            ("ledger_state_hash",),
            _ADD_UNEXPECTED_KEY,
            id="V02-ledger-hash-exact-shape",
        ),
        pytest.param(
            ("ledger_state_hash", "algorithm"),
            "sha1",
            id="V02-ledger-hash-algorithm",
        ),
        pytest.param(
            ("ledger_state_hash", "combined"),
            "g" * 64,
            id="V02-ledger-hash-digests",
        ),
        pytest.param(
            ("server_dates",),
            _ADD_UNEXPECTED_KEY,
            id="V02-server-dates-exact-shape",
        ),
        pytest.param(
            ("server_dates", "review"), "", id="V02-server-dates-nonempty"
        ),
        pytest.param(
            ("server_date_skew_seconds",),
            _ADD_UNEXPECTED_KEY,
            id="V02-server-skews-exact-shape",
        ),
        pytest.param(
            ("server_date_skew_seconds", "review"),
            True,
            id="V02-server-skews-integer-not-bool",
        ),
        pytest.param(
            ("timer",), _ADD_UNEXPECTED_KEY, id="V03-timer-exact-shape"
        ),
        pytest.param(
            ("timer", "enabled"),
            True,
            id="V03-timer-exact-state",
        ),
        pytest.param(
            ("verifier_identity",),
            _ADD_UNEXPECTED_KEY,
            id="V07-verifier-identity-exact-shape",
        ),
        pytest.param(
            ("verifier_identity", "release_commit"),
            "a" * 39,
            id="V07-release-commit-digest",
        ),
        pytest.param(
            ("fence", "apply_timer"),
            _ADD_UNEXPECTED_KEY,
            id="V08-apply-timer-exact-shape",
        ),
        pytest.param(
            ("fence", "apply_timer", "enabled"),
            False,
            id="V08-apply-timer-exact-state",
        ),
        pytest.param(
            (
                "publication_frontier",
                "operation_frontier",
                "pending_operation_count",
            ),
            1,
            id="V04-operation-pending-count",
        ),
        pytest.param(
            (
                "publication_frontier",
                "operation_frontier",
                "blocked_state",
            ),
            "blocked",
            id="V04-operation-blocked-state",
        ),
        pytest.param(
            ("publication_frontier", "operation_frontier"),
            _ADD_UNEXPECTED_KEY,
            id="V04-operation-frontier-exact-shape",
        ),
        pytest.param(
            ("publication_frontier", "queue_count"),
            1,
            id="V05-frontier-queue-count",
        ),
        pytest.param(
            ("publication_frontier", "reason"), "", id="V05-frontier-reason"
        ),
        pytest.param(
            ("publication_frontier", "releases"),
            [{}],
            id="V05-frontier-releases-empty",
        ),
        pytest.param(
            ("publication",),
            "observer-env-absent",
            id="V06-publication-observer-frontier",
        ),
        pytest.param(
            ("publication_fallback",), {}, id="V06-publication-fallback-none"
        ),
        pytest.param(("preflight",), {}, id="V06-preflight-none"),
        pytest.param(("first_get_utc",), "", id="V06-first-get-nonempty"),
        pytest.param(("last_get_utc",), "", id="V06-last-get-nonempty"),
        pytest.param(
            ("ledger_host",), "example.invalid", id="V06-ledger-host"
        ),
        pytest.param(
            ("verifier_identity", "verifier_blob"),
            "a" * 39,
            id="V07-verifier-blob-digest-length",
        ),
        pytest.param(
            ("verifier_identity", "verifier_blob"),
            "g" * 40,
            id="V07-verifier-blob-digest-format",
        ),
        pytest.param(
            ("fence", "apply_timer_stopped"),
            False,
            id="V08-fence-apply-timer-stopped",
        ),
        pytest.param(
            ("fence", "proved"),
            False,
            id="V08-fence-proved",
        ),
        pytest.param(
            ("fence", "window_nonce_sha256"),
            "g" * 64,
            id="V08-fence-nonce-digest",
        ),
        pytest.param(
            ("fence", "window_owner"), "", id="V08-fence-window-owner"
        ),
        pytest.param(
            ("fence", "window_phase"), "middle", id="V08-fence-window-phase"
        ),
        pytest.param(
            ("verifier_identity", "verifier_blob"),
            "A" * 40,
            id="V09-lowercase-only-hex",
        ),
    ],
)
def test_documented_receipt_validator_rejects_each_acceptance_conjunct(
    tmp_path, property_path, invalid_value
):
    validator = _documented_receipt_validator()
    code, valid_receipt, _http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[]
    )
    assert code == 0

    valid_path = tmp_path / "valid-receipt.json"
    valid_path.write_text(json.dumps(valid_receipt), encoding="utf-8")
    accepted = _validate_documented_receipt(validator, valid_path)
    assert accepted.returncode == 0
    assert accepted.stdout == accepted.stderr == b""

    invalid_receipt = json.loads(json.dumps(valid_receipt))
    parent = invalid_receipt
    for key in property_path[:-1]:
        parent = parent[key]
    if invalid_value is _ADD_UNEXPECTED_KEY:
        parent[property_path[-1]]["unexpected"] = None
    else:
        parent[property_path[-1]] = invalid_value

    invalid_path = tmp_path / "one-fault-receipt.json"
    invalid_path.write_text(json.dumps(invalid_receipt), encoding="utf-8")
    rejected = _validate_documented_receipt(validator, invalid_path)
    assert rejected.returncode == 75
    assert rejected.stdout == rejected.stderr == b""


def test_runbook_records_self_hash_limits_and_load_bearing_script_route():
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(runbook.split())

    assert (
        "the file reachable at `__file__` had the reviewed blob at run time"
        in normalized
    )
    assert "the measurement is a later re-read" in normalized
    assert "It proves nothing when the invoked program is not this program" in normalized
    assert "does not cover interpreter caches" in normalized
    assert "echoes `release_commit` without verifying it" in normalized
    assert (
        "The real binding is the runbook's Git gates plus the supervised transcript"
        in normalized
    )
    assert "-I -B --check-hash-based-pycs always /proc/self/fd/9" in normalized
    assert (
        "A `-m` invocation, import, wrapper, or ordinary path substitution is unsupported"
        in normalized
    )
    assert (
        "`hashlib.sha1(..., usedforsecurity=False)` is ordinary, unhardened SHA-1"
        in normalized
    )
    assert (
        "`usedforsecurity=False` changes policy availability, not the digest or its collision resistance"
        in normalized
    )
    assert (
        "protection against recognized practical SHA-1 collision attacks is supplied by the deployment Git build's `SHA1_DC` collision-detection hardening"
        in normalized
    )
    assert "this is not a claim of general SHA-1 collision resistance" in normalized
    assert (
        "This is a deployment-specific property, not a portable property of Git or Python"
        in normalized
    )
    assert (
        "confirm that `/usr/bin/git version --build-options` reports `SHA-1: SHA1_DC`; otherwise stop"
        in normalized
    )


def test_runbook_keeps_nonce_off_argv_and_defines_verifier_before_use():
    runbook = (TOOLS.parent / "docs/day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )

    assert "--window-nonce " not in runbook
    assert "--window-nonce-file" in runbook
    verifier_definition = (
        'builtin readonly QUEUE_PROOF_VERIFIER="$QUEUE_PROOF_CHECKOUT/'
        'tools/prove_queues_empty.py"'
    )
    assert runbook.index(verifier_definition) < runbook.index(
        '9<"$QUEUE_PROOF_VERIFIER"'
    )


def test_apply_timer_decode_failure_returns_one_bounded_false_receipt(tmp_path):
    base_run, _calls = injected_systemctl()

    def decode_failure(argv, **kwargs):
        if queues.APPLY_TIMER_UNIT in argv:
            raise UnicodeDecodeError(
                "utf-8", b"\xff", 0, 1, "private apply timer bytes"
            )
        return base_run(argv, **kwargs)

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: Response({"ok": True, "items": []}),
        raw_output=True,
        run_systemctl=decode_failure,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert len(output) < 2048
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "fence-apply-timer-not-stopped"
    assert "private apply timer bytes" not in output


def test_production_timer_decode_failure_returns_one_bounded_false_receipt(
    tmp_path,
):
    base_run, _calls = injected_systemctl()

    def decode_failure(argv, **kwargs):
        if queues.TIMER_UNIT in argv:
            raise UnicodeDecodeError(
                "utf-8", b"\xff", 0, 1, "private production timer bytes"
            )
        return base_run(argv, **kwargs)

    code, output, _systemctl_calls = run_main(
        tmp_path,
        opener=lambda _request, timeout: Response({"ok": True, "items": []}),
        observer=False,
        raw_output=True,
        run_systemctl=decode_failure,
    )
    receipt = json.loads(output)

    assert code == 1
    assert output.count("\n") == 1
    assert len(output) < 2048
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "environment-unavailable"
    assert receipt["timer"] == {
        "active": None,
        "available": False,
        "enabled": None,
    }
    assert "private production timer bytes" not in output


def _assert_invalid_arguments_receipt(argv):
    output = io.StringIO()

    code = call_main(argv, stdout=output)
    serialized = output.getvalue()
    receipt = json.loads(serialized)

    assert code == 1
    assert serialized.count("\n") == 1
    assert len(serialized) < 2048
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "arguments-invalid"
    return serialized


def test_missing_required_arguments_return_one_bounded_false_receipt(capsys):
    _assert_invalid_arguments_receipt([])

    assert capsys.readouterr().err == ""


def test_missing_argument_value_returns_one_bounded_false_receipt(capsys):
    _assert_invalid_arguments_receipt(["--ledger-origin"])

    assert capsys.readouterr().err == ""


def test_unknown_argument_returns_one_bounded_false_receipt(capsys):
    serialized = _assert_invalid_arguments_receipt(
        ["--unknown-option", "private-argv-value"]
    )

    assert "private-argv-value" not in serialized
    assert capsys.readouterr().err == ""


def test_help_returns_one_bounded_false_receipt(capsys):
    serialized = _assert_invalid_arguments_receipt(["--help"])

    assert "usage:" not in serialized
    assert capsys.readouterr().err == ""


def test_argument_parser_defect_returns_one_bounded_receipt(monkeypatch):
    def fail_parse(_parser, _argv):
        raise RuntimeError("private parser defect")

    monkeypatch.setattr(queues._ProofArgumentParser, "parse_args", fail_parse)
    output = io.StringIO()

    code = call_main([], stdout=output)
    serialized = output.getvalue()
    receipt = json.loads(serialized)

    assert code == 1
    assert serialized.count("\n") == 1
    assert len(serialized) < 2048
    assert receipt["proof_error"] == "verifier-defect"
    assert "private parser defect" not in serialized


def test_json_encoder_defect_returns_literal_bounded_receipt(monkeypatch):
    def fail_json_encode(*_args, **_kwargs):
        raise RuntimeError("private encoder defect")

    monkeypatch.setattr(queues.json, "dumps", fail_json_encode)
    output = io.StringIO()

    code = call_main(
        preflight_args(), stdout=output
    )
    serialized = output.getvalue()
    receipt = json.loads(serialized)

    assert code == 1
    assert serialized.count("\n") == 1
    assert len(serialized) < 2048
    assert receipt["proof_error"] == "verifier-defect"
    assert "private encoder defect" not in serialized


def test_N15_nonserializable_receipt_fallback_is_false_verifier_defect():
    payload, failed = queues._serialize_receipt({"not_json": object()})
    receipt = json.loads(payload)

    assert failed is True
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "verifier-defect"


def test_standard_stream_reservation_reraises_non_ebadf(monkeypatch):
    real_fstat = queues.os.fstat

    def fail_stdout_fstat(file_descriptor):
        if file_descriptor == 1:
            raise OSError(errno.EIO, "private descriptor failure")
        return real_fstat(file_descriptor)

    monkeypatch.setattr(queues.os, "fstat", fail_stdout_fstat)

    with pytest.raises(OSError) as raised:
        queues._reserve_standard_streams()

    assert raised.value.errno == errno.EIO


def test_receipt_serialization_is_canonical_key_order():
    payload, failed = queues._serialize_receipt({"z": 0, "a": 1})

    assert failed is False
    assert payload == b'{"a":1,"z":0}\n'


def test_write_all_retries_partial_writes(monkeypatch):
    writes = []

    def partial_write(_descriptor, payload):
        amount = min(3, len(payload))
        writes.append(payload[:amount])
        return amount

    monkeypatch.setattr(queues.os, "write", partial_write)

    queues._write_all(123, b"complete receipt")

    assert b"".join(writes) == b"complete receipt"


@pytest.mark.parametrize("written", [0, -1])
def test_write_all_refuses_nonprogressing_write(monkeypatch, written):
    monkeypatch.setattr(
        queues.os, "write", lambda _descriptor, _payload: written
    )

    with pytest.raises(OSError):
        queues._write_all(123, b"receipt")


def test_durable_receipt_write_syncs_file_and_directory(tmp_path, monkeypatch):
    receipt_path = tmp_path / "durable-receipt.json"
    file_descriptor, directory_descriptor, filename = queues._open_receipt(
        str(receipt_path)
    )
    fsync_calls = []
    original_fsync = queues.os.fsync

    def record_fsync(descriptor):
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(queues.os, "fsync", record_fsync)
    try:
        queues._write_receipt(
            file_descriptor,
            directory_descriptor,
            filename,
            b'{"receipt":true}\n',
        )
    finally:
        os.close(file_descriptor)
        os.close(directory_descriptor)

    assert receipt_path.read_bytes() == b'{"receipt":true}\n'
    assert fsync_calls == [file_descriptor, directory_descriptor]
    assert receipt_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "invalid_named_state", ["different-inode", "opened-mode", "named-mode"]
)
def test_durable_receipt_post_write_verifies_same_mode_0600_inode(
    tmp_path, monkeypatch, invalid_named_state
):
    receipt_path = tmp_path / "verified-receipt.json"
    file_descriptor, directory_descriptor, filename = queues._open_receipt(
        str(receipt_path)
    )
    actual = os.fstat(file_descriptor)
    opened_mode = actual.st_mode
    named_mode = actual.st_mode
    named_inode = actual.st_ino
    if invalid_named_state == "different-inode":
        named_inode += 1
    elif invalid_named_state == "opened-mode":
        opened_mode = (opened_mode & ~0o777) | 0o644
    else:
        named_mode = (named_mode & ~0o777) | 0o644
    monkeypatch.setattr(
        queues.os,
        "fstat",
        lambda _descriptor: SimpleNamespace(
            st_mode=opened_mode,
            st_dev=actual.st_dev,
            st_ino=actual.st_ino,
        ),
    )
    monkeypatch.setattr(
        queues.os,
        "stat",
        lambda *_args, **_kwargs: SimpleNamespace(
            st_mode=named_mode,
            st_dev=actual.st_dev,
            st_ino=named_inode,
        ),
    )
    try:
        with pytest.raises(OSError):
            queues._write_receipt(
                file_descriptor,
                directory_descriptor,
                filename,
                b'{"receipt":true}\n',
            )
    finally:
        os.close(file_descriptor)
        os.close(directory_descriptor)


def test_receipt_create_mode_is_0600_before_fchmod(tmp_path, monkeypatch):
    receipt_path = tmp_path / "create-mode-receipt.json"
    monkeypatch.setattr(queues.os, "fchmod", lambda *_args: None)
    previous_umask = os.umask(0)
    try:
        file_descriptor, directory_descriptor, _filename = queues._open_receipt(
            str(receipt_path)
        )
    finally:
        os.umask(previous_umask)
    try:
        assert receipt_path.stat().st_mode & 0o777 == 0o600
    finally:
        os.close(file_descriptor)
        os.close(directory_descriptor)


def test_relative_receipt_path_is_refused_without_creation(
    tmp_path, monkeypatch, capfd
):
    monkeypatch.chdir(tmp_path)

    code = call_main(
        [*preflight_args(), "--receipt-path", "relative-receipt.json"],
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    captured = capfd.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err == "queue proof receipt file could not be opened\n"
    assert not (tmp_path / "relative-receipt.json").exists()


@pytest.mark.parametrize(
    "spelling",
    [
        "trailing-slash",
        "terminal-dot",
        "dot-segment",
        "repeated-separator",
        "parent-segment",
    ],
)
def test_F7_noncanonical_receipt_path_is_refused_without_creation(
    tmp_path, capfd, spelling
):
    normalized_target = tmp_path / "not-the-requested-receipt"
    existing_parent = tmp_path / "existing-parent"
    existing_parent.mkdir()
    receipt_path = {
        "trailing-slash": f"{normalized_target}/",
        "terminal-dot": f"{normalized_target}/.",
        "dot-segment": f"{tmp_path}/./{normalized_target.name}",
        "repeated-separator": f"{tmp_path}//{normalized_target.name}",
        "parent-segment": (
            f"{existing_parent}/../{normalized_target.name}"
        ),
    }[spelling]

    code = call_main(
        [
            *preflight_args(),
            "--receipt-path",
            receipt_path,
        ],
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    captured = capfd.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err == "queue proof receipt file could not be opened\n"
    assert not normalized_target.exists()


def test_N16_durable_invocation_without_receipt_path_is_bounded_refusal(capfd):
    code = call_main(preflight_args(), systemctl_path=queues.SYSTEMCTL_PATH)
    captured = capfd.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err == "queue proof receipt file could not be opened\n"
    assert len(captured.err.encode("utf-8")) < 2048


def test_N14_open_receipt_baseexception_releases_create_new_reservation(
    tmp_path, monkeypatch
):
    receipt_path = tmp_path / "baseexception-reservation.json"

    def interrupt_after_create(_descriptor, _mode):
        raise KeyboardInterrupt

    monkeypatch.setattr(queues.os, "fchmod", interrupt_after_create)

    with pytest.raises(KeyboardInterrupt):
        queues._open_receipt(str(receipt_path))

    assert not receipt_path.exists()


def test_durable_receipt_write_failure_returns_nonzero_diagnostic(
    tmp_path, monkeypatch, capfd
):
    receipt_path = tmp_path / "failed-receipt.json"

    def fail_write(*_args):
        raise OSError("private storage failure")

    monkeypatch.setattr(queues, "_write_receipt", fail_write)

    code = call_main(
        [*preflight_args(), "--receipt-path", str(receipt_path)],
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    captured = capfd.readouterr()

    assert code == 1
    assert captured.out == ""
    assert captured.err == "queue proof receipt file could not be written\n"
    assert receipt_path.read_bytes() == b""
    assert "private storage failure" not in captured.err


def test_interrupt_during_receipt_write_emits_once_and_releases_path(
    tmp_path, monkeypatch, capfd
):
    receipt_path = tmp_path / "write-interrupted-receipt.json"

    def interrupt_write(*_args):
        raise queues._SignalInterrupted(signal.SIGINT)

    monkeypatch.setattr(queues, "_write_receipt", interrupt_write)

    code = call_main(
        [*preflight_args(), "--receipt-path", str(receipt_path)],
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    captured = capfd.readouterr()
    receipt = json.loads(captured.out)

    assert code == 130
    assert captured.out.count("\n") == 1
    assert len(captured.out) < 2048
    assert captured.err == ""
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "verifier-interrupted"
    assert receipt["verifier_identity"] == {
        "release_commit": RELEASE_COMMIT,
        "verifier_blob": git_blob_oid(
            (TOOLS / "prove_queues_empty.py").read_bytes()
        ),
    }
    assert not receipt_path.exists()

    retry = subprocess.run(
        receipt_writer_child(receipt_path),
        capture_output=True,
        check=False,
    )
    assert retry.returncode == 0
    assert retry.stderr == b""
    assert_durable_preflight_receipt(receipt_path)


def test_N13_interrupt_before_stdout_starts_forces_true_receipt_false(
    tmp_path, monkeypatch, capfd
):
    receipt_path = tmp_path / "pre-mirror-interrupted-receipt.json"
    true_receipt = queues._new_receipt(
        {
            "release_commit": RELEASE_COMMIT,
            "verifier_blob": VERIFIER_BLOB,
        }
    )
    true_receipt["all_queues_empty"] = True
    monkeypatch.setattr(
        queues, "_run_proof", lambda *_args, **_kwargs: true_receipt
    )
    real_serialize = queues._serialize_receipt
    serialize_calls = 0

    def interrupt_first_serialization(receipt):
        nonlocal serialize_calls
        serialize_calls += 1
        if serialize_calls == 1:
            assert receipt["all_queues_empty"] is True
            raise queues._SignalInterrupted(signal.SIGINT)
        return real_serialize(receipt)

    monkeypatch.setattr(queues, "_serialize_receipt", interrupt_first_serialization)

    code = call_main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(tmp_path / "unused-apply.env"),
            "--observer-env-file",
            str(tmp_path / "unused-observer.env"),
            "--receipt-path",
            str(receipt_path),
        ],
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    captured = capfd.readouterr()
    interrupted_receipt = json.loads(captured.out)

    assert code == 130
    assert serialize_calls == 2
    assert interrupted_receipt["all_queues_empty"] is False
    assert interrupted_receipt["proof_error"] == "verifier-interrupted"
    assert captured.err == ""
    assert not receipt_path.exists()


def test_N10_interrupt_during_durable_stdout_mirror_preserves_receipt(
    tmp_path, monkeypatch, capfd
):
    receipt_path = tmp_path / "durable-mirror-interrupted-receipt.json"
    real_write_all = queues._write_all
    stdout_attempts = 0

    def interrupt_first_stdout_write(file_descriptor, payload):
        nonlocal stdout_attempts
        if file_descriptor == 1:
            stdout_attempts += 1
            if stdout_attempts == 1:
                raise queues._SignalInterrupted(signal.SIGTERM)
        return real_write_all(file_descriptor, payload)

    monkeypatch.setattr(queues, "_write_all", interrupt_first_stdout_write)

    code = call_main(
        [*preflight_args(), "--receipt-path", str(receipt_path)],
        systemctl_path=queues.SYSTEMCTL_PATH,
    )
    captured = capfd.readouterr()

    assert code == 143
    assert receipt_path.exists()
    assert stdout_attempts == 1
    assert captured.out == ""
    assert captured.err == (
        "queue proof interrupted during receipt mirror; "
        "receipt is at the required path\n"
    )
    assert_durable_preflight_receipt(receipt_path)


def test_signal_after_receipt_creation_is_deferred_until_cleanup_owns_path(
    tmp_path,
):
    receipt_path = tmp_path / "reservation-interrupted-receipt.json"
    completed = subprocess.run(
        signal_hazard_child(receipt_path, "after-receipt-create"),
        capture_output=True,
        check=False,
    )
    receipt = json.loads(completed.stdout)

    assert completed.returncode == 130
    assert completed.stdout.count(b"\n") == 1
    assert len(completed.stdout) < 2048
    assert completed.stderr == b""
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "verifier-interrupted"
    assert not receipt_path.exists()

    retry = subprocess.run(
        receipt_writer_child(receipt_path),
        capture_output=True,
        check=False,
    )
    assert retry.returncode == 0
    assert retry.stderr == b""
    assert_durable_preflight_receipt(receipt_path)


def test_signal_during_committed_stdout_mirror_does_not_split_receipt(
    tmp_path,
):
    claim = runbook_executable_claims()["post_finalization_signal"]
    receipt_path = tmp_path / "mirror-signaled-receipt.json"
    completed = subprocess.run(
        signal_hazard_child(receipt_path, "during-committed-mirror"),
        capture_output=True,
        check=False,
    )
    stdout_receipt = json.loads(completed.stdout)

    assert {
        "returncode": completed.returncode,
        "durable_receipt": receipt_path.exists(),
        "receipt_matches_stdout": receipt_path.read_bytes() == completed.stdout,
    } == claim
    assert completed.stdout.count(b"\n") == 1
    assert completed.stderr == b""
    assert stdout_receipt["preflight"]["ready"] is True
    assert_durable_preflight_receipt(receipt_path)


def test_signal_after_inner_main_commit_does_not_append_receipt(
    tmp_path,
):
    claim = runbook_executable_claims()["post_finalization_signal"]
    receipt_path = tmp_path / "post-commit-signaled-receipt.json"
    completed = subprocess.run(
        signal_hazard_child(receipt_path, "after-inner-main-commit"),
        capture_output=True,
        check=False,
    )

    assert {
        "returncode": completed.returncode,
        "durable_receipt": receipt_path.exists(),
        "receipt_matches_stdout": receipt_path.read_bytes() == completed.stdout,
    } == claim
    assert completed.stdout.count(b"\n") == 1
    assert completed.stderr == b""
    assert json.loads(completed.stdout)["preflight"]["ready"] is True
    assert_durable_preflight_receipt(receipt_path)


def test_interrupted_cleanup_never_unlinks_a_replacement_inode(tmp_path):
    receipt_path = tmp_path / "reserved-receipt.json"
    moved_path = tmp_path / "original-reservation.json"
    receipt_descriptor, directory_descriptor, filename = queues._open_receipt(
        receipt_path
    )
    opened = os.fstat(receipt_descriptor)
    expected_identity = (opened.st_dev, opened.st_ino)
    receipt_path.rename(moved_path)
    receipt_path.write_bytes(b"replacement evidence\n")
    receipt_path.chmod(0o600)

    released = queues._release_receipt_reservation(
        receipt_descriptor,
        directory_descriptor,
        filename,
        expected_identity,
    )

    assert released is False
    assert receipt_path.read_bytes() == b"replacement evidence\n"
    assert moved_path.exists()


def test_runtime_requires_only_allowlisted_environment_and_isolated_mode():
    queues._assert_clean_process_environment(
        {"LC_ALL": "C"}, isolated=True
    )

    with pytest.raises(queues.ProofError, match="^environment-hostile$"):
        queues._assert_clean_process_environment(
            {"LC_ALL": "C", "UNEXPECTED": "value"}, isolated=True
        )
    with pytest.raises(
        queues.ProofError, match="^runtime-isolation-required$"
    ):
        queues._assert_clean_process_environment(
            {"LC_ALL": "C"}, isolated=False
        )


def test_self_blob_refuses_a_bytecode_cache_route(monkeypatch):
    monkeypatch.setattr(queues, "__cached__", "/tmp/hostile.pyc")

    with pytest.raises(
        queues.ProofError, match="^verifier-bytecode-cached$"
    ):
        queues._self_blob()


def test_tls_context_allows_tls_13():
    context = queues._production_context()

    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED


def test_tls_context_explicitly_disables_key_logging(monkeypatch):
    class Context:
        def __init__(self):
            self.keylog_filename = "inherited-keylog-target"
            self.minimum_version = None
            self.verify_mode = None
            self.check_hostname = None

        def set_ciphers(self, _policy):
            return None

        def load_verify_locations(self, *, cadata):
            assert cadata == "certificate-data"

    context = Context()
    monkeypatch.setattr(queues.ssl, "SSLContext", lambda _protocol: context)

    produced = queues._production_context(
        (b"certificate-data", "certificate-data")
    )

    assert produced is context
    assert produced.keylog_filename is None


def receipt_writer_child(receipt_path, *, before_main=""):
    source_path = TOOLS / "prove_queues_empty.py"
    child = f"""
import hashlib
import importlib.util
import os
import pathlib
import signal

source_path = pathlib.Path({str(source_path)!r})
spec = importlib.util.spec_from_file_location("queue_receipt_child", source_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.__cached__ = None
raw = source_path.read_bytes()
blob = hashlib.sha1(
    f"blob {{len(raw)}}\\0".encode("ascii") + raw,
    usedforsecurity=False,
).hexdigest()
{before_main}
raise SystemExit(module.main(
    [
        "--release-commit", "a" * 40,
        "--verifier-blob", blob,
        "--ledger-origin", {LEDGER_ORIGIN!r},
        "--preflight",
        "--receipt-path", {str(receipt_path)!r},
    ],
    systemctl_path="/usr/bin/systemctl",
    process_environment={{"LC_ALL": "C"}},
    isolated=True,
    tls_handshake=lambda _host, _context: {{
        "cipher": "TLS_AES_256_GCM_SHA384",
        "protocol": "TLSv1.3",
        "secret_bits": 256,
    }},
))
"""
    return [sys.executable, "-c", child]


def signal_hazard_child(receipt_path, scenario):
    setups = {
        "after-receipt-create": """
    real_fchmod = module.os.fchmod
    signaled = False

    def signal_after_create(file_descriptor, mode):
        global signaled
        real_fchmod(file_descriptor, mode)
        if not signaled:
            signaled = True
            os.kill(os.getpid(), signal.SIGINT)

    module.os.fchmod = signal_after_create
""",
        "during-committed-mirror": """
    real_write_all = module._write_all
    signaled = False

    def write_then_signal(file_descriptor, payload):
        global signaled
        real_write_all(file_descriptor, payload)
        if file_descriptor == 1 and not signaled:
            signaled = True
            os.kill(os.getpid(), signal.SIGTERM)

    module._write_all = write_then_signal
""",
        "after-inner-main-commit": """
    real_main = module._main

    def signal_after_commit(*args, **kwargs):
        result = real_main(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGINT)
        return result

    module._main = signal_after_commit
""",
    }
    try:
        before_main = textwrap.dedent(setups[scenario])
    except KeyError:
        raise AssertionError(f"unknown signal hazard scenario: {scenario}") from None
    return receipt_writer_child(receipt_path, before_main=before_main)


def interrupted_receipt_writer_child(tmp_path, receipt_path):
    source_path = TOOLS / "prove_queues_empty.py"
    apply_env = tmp_path / "interrupt-apply.env"
    observer_env = tmp_path / "interrupt-observer.env"
    nonce_file = tmp_path / "interrupt-window-nonce.env"
    write_apply_env(apply_env)
    write_observer_env(observer_env)
    write_window_nonce(nonce_file)
    child = f"""
import datetime
import hashlib
import importlib.util
import os
import pathlib
import signal

source_path = pathlib.Path({str(source_path)!r})
spec = importlib.util.spec_from_file_location("queue_interrupt_child", source_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.__cached__ = None
raw = source_path.read_bytes()
blob = hashlib.sha1(
    f"blob {{len(raw)}}\\0".encode("ascii") + raw,
    usedforsecurity=False,
).hexdigest()

def block_in_first_get(_request, timeout):
    os.write(2, b"ready\\n")
    while True:
        signal.pause()

def systemctl(argv, **_kwargs):
    if "is-enabled" in argv:
        return type("Result", (), {{"returncode": 0, "stdout": "enabled\\n"}})()
    return type("Result", (), {{"returncode": 3, "stdout": "inactive\\n"}})()

raise SystemExit(module.main(
    [
        "--release-commit", "a" * 40,
        "--verifier-blob", blob,
        "--ledger-origin", {LEDGER_ORIGIN!r},
        "--apply-env-file", {str(apply_env)!r},
        "--observer-env-file", {str(observer_env)!r},
        "--apply-timer-stopped",
        "--window-owner", {WINDOW_OWNER!r},
        "--window-nonce-file", {str(nonce_file)!r},
        "--window-phase", "opening",
        "--receipt-path", {str(receipt_path)!r},
    ],
    opener=block_in_first_get,
    run_systemctl=systemctl,
    utc_now=lambda: datetime.datetime(
        2026, 9, 7, 15, 0, 0, tzinfo=datetime.timezone.utc
    ),
    systemctl_path="/usr/bin/systemctl",
    read_host_identity=lambda: {{
        "boot_id_sha256": "c" * 64,
        "machine_id_sha256": "d" * 64,
    }},
    process_environment={{"LC_ALL": "C"}},
    isolated=True,
))
"""
    child_path = tmp_path / "interrupt-child.py"
    child_path.write_text(child, encoding="utf-8")
    return [
        "/bin/bash",
        "--noprofile",
        "--norc",
        "-c",
        (
            "exec -c /usr/bin/env QUEUE_PROOF_ENV_SCRUB_REQUIRED=1 "
            "/usr/bin/env -i LC_ALL=C PYTHONDONTWRITEBYTECODE=1 "
            "/usr/bin/python3 -I -B --check-hash-based-pycs always \"$1\""
        ),
        "queue-proof-interrupt-launcher",
        str(child_path),
    ]


def assert_durable_preflight_receipt(receipt_path):
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["preflight"]["ready"] is True
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_full_stdout_returns_nonzero_but_preserves_durable_receipt(tmp_path):
    claim = runbook_executable_claims()["dev_full"]
    receipt_path = tmp_path / "full-stdout-receipt.json"
    with open("/dev/full", "wb") as full_sink:
        completed = subprocess.run(
            receipt_writer_child(receipt_path),
            stdout=full_sink,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )

    assert {
        "returncode": completed.returncode,
        "durable_receipt": receipt_path.exists(),
        "diagnostic": completed.stderr.decode("utf-8").rstrip("\n"),
    } == claim
    assert_durable_preflight_receipt(receipt_path)


def test_broken_stdout_pipe_returns_nonzero_but_preserves_durable_receipt(
    tmp_path,
):
    claim = runbook_executable_claims()["epipe"]
    receipt_path = tmp_path / "broken-pipe-receipt.json"
    read_descriptor, write_descriptor = os.pipe()
    os.close(read_descriptor)
    try:
        child = subprocess.Popen(
            receipt_writer_child(receipt_path),
            stdout=write_descriptor,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(write_descriptor)
    _stdout, stderr = child.communicate(timeout=5)

    assert {
        "returncode": child.returncode,
        "durable_receipt": receipt_path.exists(),
        "diagnostic": stderr.decode("utf-8").rstrip("\n"),
    } == claim
    assert_durable_preflight_receipt(receipt_path)


def test_shell_closed_stdout_returns_nonzero_but_preserves_durable_receipt(
    tmp_path,
):
    claim = runbook_executable_claims()["verifier_closed_stdout"]
    receipt_path = tmp_path / "closed-stdout-receipt.json"
    child_source = receipt_writer_child(receipt_path)[2]
    completed = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            'exec 1>&-\n/usr/bin/python3 -c "$1"',
            "queue-proof-closed-stdout",
            child_source,
        ],
        stderr=subprocess.PIPE,
        check=False,
    )

    assert {
        "returncode": completed.returncode,
        "durable_receipt": receipt_path.exists(),
        "diagnostic": completed.stderr.decode("utf-8").rstrip("\n"),
    } == claim
    assert_durable_preflight_receipt(receipt_path)


@pytest.mark.parametrize(
    ("interrupt_signal", "claim_id"),
    [(signal.SIGINT, "sigint"), (signal.SIGTERM, "sigterm")],
)
def test_documented_launcher_signal_interrupt_emits_partial_receipt_and_retries(
    tmp_path, interrupt_signal, claim_id
):
    claim = runbook_executable_claims()[claim_id]
    receipt_path = tmp_path / "interrupted-receipt.json"
    child = subprocess.Popen(
        interrupted_receipt_writer_child(tmp_path, receipt_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stderr.readline() == b"ready\n"

    child.send_signal(interrupt_signal)
    stdout, stderr = child.communicate(timeout=5)
    receipt = json.loads(stdout)

    assert stderr == b""
    bounded_receipt = stdout.count(b"\n") == 1 and len(stdout) < 2048
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "verifier-interrupted"
    assert receipt["first_get_utc"] == "2026-09-07T15:00:00Z"
    assert receipt["fence"]["proved"] is True
    assert receipt["verifier_identity"] == {
        "release_commit": RELEASE_COMMIT,
        "verifier_blob": git_blob_oid(
            (TOOLS / "prove_queues_empty.py").read_bytes()
        ),
    }
    assert not receipt_path.exists()

    retry = subprocess.run(
        receipt_writer_child(receipt_path),
        capture_output=True,
        check=False,
    )
    assert {
        "returncode": child.returncode,
        "bounded_receipt": bounded_receipt,
        "all_queues_empty": receipt["all_queues_empty"],
        "proof_error": receipt["proof_error"],
        "proof_state_preserved": (
            receipt["first_get_utc"] == "2026-09-07T15:00:00Z"
            and receipt["fence"]["proved"] is True
            and receipt["verifier_identity"]
            == {
                "release_commit": RELEASE_COMMIT,
                "verifier_blob": git_blob_oid(
                    (TOOLS / "prove_queues_empty.py").read_bytes()
                ),
            }
        ),
        "receipt_path_reusable": retry.returncode == 0,
    } == claim
    assert retry.returncode == 0
    assert retry.stderr == b""
    assert_durable_preflight_receipt(receipt_path)


def test_existing_receipt_path_is_refused_without_overwrite(tmp_path):
    receipt_path = tmp_path / "existing-receipt.json"
    receipt_path.write_text("existing evidence\n", encoding="utf-8")

    completed = subprocess.run(
        receipt_writer_child(receipt_path),
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr == b"queue proof receipt file could not be opened\n"
    assert receipt_path.read_text(encoding="utf-8") == "existing evidence\n"
