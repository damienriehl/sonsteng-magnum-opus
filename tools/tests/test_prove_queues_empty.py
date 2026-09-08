import datetime
import http.client
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import textwrap
from types import SimpleNamespace

import pytest


TOOLS = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "prove_queues_empty", TOOLS / "prove_queues_empty.py"
)
queues = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queues)

LEDGER_ORIGIN = "https://sonsteng-chat.damienriehl.workers.dev"
WINDOW_OWNER = "packet-d-test-window"
RELEASE_COMMIT = "a" * 40
VERIFIER_BLOB = "b" * 40
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


class Response:
    def __init__(self, payload, *, status=200, headers=None):
        self._body = json.dumps(payload).encode("utf-8")
        self._status = status
        self._headers = (
            [("Content-Length", str(len(self._body)))]
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
            [("Content-Length", str(len(self._body)))]
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
            return Response({"ok": True, "context": frontier_context})
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
    ledger_origin=LEDGER_ORIGIN,
    event_log=None,
    raw_output=False,
    run_systemctl=None,
):
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    if observer:
        write_observer_env(observer_env)
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

    def utc_now():
        timestamp = next(timestamps)
        if event_log is not None:
            event_log.append(f"clock({timestamp.isoformat()})")
        return timestamp

    code = queues.main(
        argv,
        opener=opener,
        run_systemctl=systemctl,
        utc_now=utc_now,
        stdout=output,
    )
    serialized = output.getvalue()
    receipt = serialized if raw_output else json.loads(serialized)
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
        "fence": {
            "apply_timer": {
                "active": False,
                "available": True,
                "enabled": True,
            },
            "apply_timer_stopped": True,
            "proved": True,
            "window_owner": WINDOW_OWNER,
        },
        "publication": "observer-frontier",
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


def test_response_lifecycle_does_not_catch_assertion_errors(tmp_path):
    class AssertionResponse(Response):
        def getcode(self):
            raise AssertionError("test assertion must escape")

    with pytest.raises(AssertionError, match="^test assertion must escape$"):
        run_main(
            tmp_path,
            opener=lambda _request, timeout: AssertionResponse({}),
        )


def test_response_lifecycle_does_not_mask_verifier_defects(tmp_path, monkeypatch):
    def verifier_defect(_headers):
        raise TypeError("verifier defect must escape")

    monkeypatch.setattr(queues, "_declared_content_length", verifier_defect)

    with pytest.raises(TypeError, match="^verifier defect must escape$"):
        run_main(
            tmp_path,
            opener=lambda _request, timeout: Response({"ok": True, "items": []}),
        )


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
    code = queues.main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            "--apply-timer-stopped",
            "--window-owner",
            WINDOW_OWNER,
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
        opener=lambda _request, timeout: RawResponse(raw),
        run_systemctl=injected_systemctl()[0],
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

    code = queues.main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            "--apply-timer-stopped",
            "--window-owner",
            WINDOW_OWNER,
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


def test_fifo_environment_path_fails_without_reading(tmp_path):
    fifo = tmp_path / "observer.fifo"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(queues.ProofError, match="^environment-unavailable$"):
        queues._protected_env(fifo, {"SONSTENG_PROD_OBSERVER_BEARER"})


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
        "window_owner": WINDOW_OWNER,
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

    code = queues.main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            "--apply-timer-stopped",
            "--window-owner",
            WINDOW_OWNER,
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
        "window_owner": WINDOW_OWNER,
    }
    assert receipt["first_get_utc"] is None
    assert receipt["last_get_utc"] is None
    assert http_calls == []


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
    monkeypatch.setattr(queues, "SYSTEMCTL_PATH", str(trusted_systemctl.resolve()))

    state = queues._timer_state(queues.APPLY_TIMER_UNIT, subprocess.run)

    assert state == {"active": False, "available": True, "enabled": True}
    assert not shim_marker.exists()


def test_production_opener_ignores_proxy_and_tls_environment(monkeypatch):
    poisoned_environment = {
        "HTTP_PROXY": "http://upper-http.invalid:9443",
        "HTTPS_PROXY": "http://upper-https.invalid:9443",
        "ALL_PROXY": "http://upper-all.invalid:9443",
        "NO_PROXY": "sonsteng-chat.damienriehl.workers.dev",
        "http_proxy": "http://lower-http.invalid:9443",
        "https_proxy": "http://lower-https.invalid:9443",
        "all_proxy": "http://lower-all.invalid:9443",
        "no_proxy": "sonsteng-chat.damienriehl.workers.dev",
        "SSL_CERT_FILE": "/tmp/private-attacker-ca.pem",
        "SSL_CERT_DIR": "/tmp/private-attacker-ca-directory",
        "SSLKEYLOGFILE": "/tmp/private-tls-keys.log",
    }
    for name, value in poisoned_environment.items():
        monkeypatch.setenv(name, value)

    contexts = []

    class FakeContext:
        def __init__(self, protocol):
            self.protocol = protocol
            self.verify_mode = None
            self.check_hostname = False
            self.keylog_filename = "ambient-keylog"
            self.loaded_locations = []
            contexts.append(self)

        def load_verify_locations(self, *, cafile):
            self.loaded_locations.append(cafile)

    handlers = []
    trusted_open = object()
    https_contexts = []

    class RecordingHTTPSHandler:
        def __init__(self, *, context):
            self.context = context
            https_contexts.append(context)

    def capture_build_opener(*configured_handlers):
        handlers.extend(configured_handlers)
        return SimpleNamespace(open=trusted_open)

    monkeypatch.setattr(queues.ssl, "SSLContext", FakeContext)
    monkeypatch.setattr(
        queues.urllib.request, "HTTPSHandler", RecordingHTTPSHandler
    )
    monkeypatch.setattr(
        queues.urllib.request, "build_opener", capture_build_opener
    )

    opener = queues._production_opener()

    assert opener is trusted_open
    assert len(contexts) == 1
    assert contexts[0].protocol == queues.ssl.PROTOCOL_TLS_CLIENT
    assert contexts[0].verify_mode == queues.ssl.CERT_REQUIRED
    assert contexts[0].check_hostname is True
    assert contexts[0].loaded_locations == [queues.APPROVED_CA_BUNDLE]
    assert contexts[0].keylog_filename is None
    proxy_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, queues.urllib.request.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    assert any(isinstance(handler, queues._NoRedirect) for handler in handlers)
    https_handlers = [
        handler for handler in handlers if isinstance(handler, RecordingHTTPSHandler)
    ]
    assert len(https_handlers) == 1
    assert https_contexts == contexts


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

    code = queues.main(
        [
            *release_identity_args(),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
            "--apply-timer-stopped",
            "--window-owner",
            WINDOW_OWNER,
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

    code = queues.main(
        [
            *release_identity_args(release_commit="not-a-reviewed-commit"),
            "--ledger-origin",
            LEDGER_ORIGIN,
            "--apply-env-file",
            str(tmp_path / "missing-apply.env"),
            "--observer-env-file",
            str(tmp_path / "missing-observer.env"),
            "--apply-timer-stopped",
            "--window-owner",
            WINDOW_OWNER,
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


def test_documented_invocation_ignores_shell_path_and_cwd_or_refuses(tmp_path):
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
    launcher = (
        launcher.replace(
            "/home/damienriehl/.local/share/sonsteng-daemon/checkout",
            str(checkout),
        )
        .replace("<reviewed-release-commit-SHA>", release_commit)
        .replace("<reviewed-verifier-Git-blob-OID>", verifier_blob)
        .replace("<opaque-Packet-D-window-id>", WINDOW_OWNER)
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
'''
    environment = {
        "PATH": str(shim_dir),
        "POISON_MARKER": str(poison_marker),
    }

    def invoke_launcher(rendered_launcher=launcher, prelude=poison):
        shell = prelude + rendered_launcher + '\nexit "$opening_queue_proof_rc"\n'
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
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert invoked.returncode == 1
    assert invoked.stdout == ""
    assert receipt["all_queues_empty"] is False
    assert receipt["verifier_identity"] == {
        "release_commit": release_commit,
        "verifier_blob": verifier_blob,
    }
    assert not poison_marker.exists()

    receipt_path.unlink()
    wrong_blob_launcher = launcher.replace(verifier_blob, "0" * 40)
    wrong_blob = invoke_launcher(wrong_blob_launcher)

    assert wrong_blob.returncode == 73
    assert wrong_blob.stdout == ""
    assert receipt_path.read_bytes() == b""
    assert not poison_marker.exists()

    verifier.write_bytes(verifier.read_bytes() + b"\n")
    receipt_path.unlink()
    dirty_verifier = invoke_launcher()

    assert dirty_verifier.returncode == 74
    assert dirty_verifier.stdout == ""
    assert receipt_path.read_bytes() == b""
    assert not poison_marker.exists()

    receipt_path.unlink()
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
    assert receipt_path.read_bytes() == b""
    assert not poison_marker.exists()

    receipt_path.unlink()
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

    code = queues.main(argv, stdout=output)
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


def test_help_preserves_argparse_success_exit(capsys):
    with pytest.raises(SystemExit) as exc_info:
        queues.main(["--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--ledger-origin" in captured.out
    assert captured.err == ""
