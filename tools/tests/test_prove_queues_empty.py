import importlib.util
import http.client
import io
import json
import pathlib
from types import SimpleNamespace


TOOLS = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "prove_queues_empty", TOOLS / "prove_queues_empty.py"
)
queues = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queues)


class Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self._body


def write_apply_env(path):
    path.write_text(
        "EDIT_API_BASE=https://ledger.example/edit/v1\n"
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


def injected_opener(review_rows, frontier_context=None):
    calls = []
    frontier_context = frontier_context or {"active_release": None, "batches": []}

    def open_request(request, timeout):
        calls.append((request, timeout))
        if request.full_url.endswith("/review"):
            return Response({"ok": True, "items": review_rows})
        if request.full_url.endswith("/prod/releases/frontier"):
            return Response({"ok": True, "context": frontier_context})
        raise AssertionError("unexpected HTTP request")

    return open_request, calls


def injected_systemctl(*, enabled=False, active=False):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
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


def invoke(
    tmp_path,
    *,
    review_rows,
    observer=True,
    timer_enabled=False,
    frontier_context=None,
):
    apply_env = tmp_path / "apply.env"
    observer_env = tmp_path / "observer.env"
    write_apply_env(apply_env)
    if observer:
        write_observer_env(observer_env)
    opener, http_calls = injected_opener(review_rows, frontier_context)
    systemctl, systemctl_calls = injected_systemctl(enabled=timer_enabled)
    output = io.StringIO()
    argv = [
        "--apply-env-file",
        str(apply_env),
        "--observer-env-file",
        str(observer_env),
    ]
    code = queues.main(
        argv, opener=opener, run_systemctl=systemctl, stdout=output
    )
    return code, json.loads(output.getvalue()), http_calls, systemctl_calls


def test_all_empty_returns_true_and_zero(tmp_path):
    code, receipt, http_calls, systemctl_calls = invoke(
        tmp_path, review_rows=[]
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
        "ledger_host": "ledger.example",
        "publication": "observer-frontier",
        "publication_fallback": None,
        "publication_frontier": {
            "queue_count": 0,
            "reason": "unprepared",
            "releases": [],
        },
        "timer": {"active": False, "available": True, "enabled": False},
    }
    assert [call[0].get_method() for call in http_calls] == ["GET", "GET"]
    assert all(timeout == 20 for _, timeout in http_calls)
    assert all(call[1]["timeout"] == 20 for call in systemctl_calls)
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


def test_other_non_terminal_status_is_counted_without_leaking_row(tmp_path):
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
    cases = [
        (
            {"active_release": {"id": "private-release"}, "batches": []},
            "active_release",
            0,
        ),
        (
            {
                "active_release": None,
                "batches": [{"batch_id": "private-batch"}],
            },
            "ready_to_prepare",
            1,
        ),
        (
            {
                "active_release": None,
                "blocked_reason": "private-blocked-reason",
                "batches": [],
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
        assert "private-blocked-reason" not in serialized


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
            "--apply-env-file",
            str(apply_env),
            "--observer-env-file",
            str(observer_env),
        ],
        opener=lambda _request, timeout: IncompleteResponse({}),
        run_systemctl=injected_systemctl()[0],
        stdout=output,
    )
    receipt = json.loads(output.getvalue())

    assert code == 1
    assert receipt["all_queues_empty"] is False
    assert receipt["proof_error"] == "http-unavailable"
    assert "private partial body" not in json.dumps(receipt)


def test_observer_absent_and_timer_disabled_uses_fallback(tmp_path):
    code, receipt, http_calls, _systemctl_calls = invoke(
        tmp_path, review_rows=[], observer=False
    )

    assert code == 0
    assert receipt["all_queues_empty"] is True
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
