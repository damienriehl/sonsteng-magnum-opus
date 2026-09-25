"""Offline daemon boundary tests, using real locks, state, git and child scripts."""
import datetime
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import direct_apply_daemon as dad


def script(root, name, body):
    target = root / "tools" / name
    target.parent.mkdir(exist_ok=True)
    target.write_text(body)


def test_rebuild_and_history_execute_real_local_scripts_in_order(tmp_path):
    script(tmp_path, "build_site.py", "from pathlib import Path\nPath('site-built').write_text('done')\nprint('rebuilt')\n")
    script(tmp_path, "build_history.py", "from pathlib import Path\nassert Path('site-built').read_text() == 'done'\nPath('history-built').write_text('ready')\nprint('history ready')\n")
    bundle = tmp_path / "app/worker/scripts/bundle-editor-data.mjs"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("import fs from 'node:fs'; if(fs.readFileSync('history-built','utf8')!=='ready')throw Error('order'); fs.writeFileSync('bundle-built','done'); console.log('bundled');")
    assert dad.rebuild(str(tmp_path)) == (True, "rebuilt\n")
    assert dad.regen_history(str(tmp_path)) == (True, "history ready\nbundled\n")
    assert (tmp_path / "bundle-built").read_text() == "done"


def test_history_failure_stops_before_node_and_preserves_diagnostic_tail(tmp_path):
    script(tmp_path, "build_history.py", "import sys\nprint('x'*2000+'failure')\nsys.exit(3)\n")
    ok, tail = dad.regen_history(str(tmp_path))
    assert not ok and len(tail) == 1500 and tail.endswith("failure\n")


def test_apply_adapter_passes_batch_and_configuration_to_real_fixture(tmp_path):
    script(tmp_path, "apply_suggestions.py", "import os,sys,json\nprint(json.dumps({'args':sys.argv[1:],'deploy':os.environ['APPLY_DEPLOY'],'base':os.environ['EDIT_API_BASE'],'token':os.environ['EDIT_SERVICE_TOKEN']}))\n")
    rc, tail = dad.run_apply_engine("batch;literal", api_base="offline.invalid", token="fixture-only", repo_root=str(tmp_path))
    assert rc == 0
    assert json.loads(tail) == {"args": ["--batch-id", "batch;literal", "--base-url", "offline.invalid"], "deploy":"1", "base":"offline.invalid", "token":"fixture-only"}


def test_editorial_and_scoped_execute_fixture_exit_codes(tmp_path):
    script(tmp_path, "editorial_pass.py", "import sys\nassert sys.argv[1:] == ['--batch-id','batch literal']\nsys.exit(7)\n")
    script(tmp_path, "editor_scoped_drafts.py", "import sys\nprint('draft rejected')\nsys.exit(4)\n")
    assert dad.dispatch_editorial("batch literal", repo_root=str(tmp_path)) == 7
    assert dad.dispatch_scoped_drafts(repo_root=str(tmp_path)) == (False, "draft rejected\n")


@pytest.mark.parametrize("error", [OSError("fixture missing"), subprocess.TimeoutExpired("fixture", 1)])
def test_scoped_process_start_errors_are_nonfatal(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(dad.subprocess, "run", fail)
    ok, detail = dad.dispatch_scoped_drafts(repo_root="/nonexistent-fixture")
    assert not ok and str(error) in detail


def test_real_daemon_lock_excludes_second_holder_and_releases_on_exception(tmp_path):
    path = str(tmp_path / "locks/daemon.lock")
    with pytest.raises(RuntimeError, match="fixture exit"):
        with dad.daemon_lock(path):
            with pytest.raises(dad.DaemonError, match="another apply-daemon"):
                with dad.daemon_lock(path):
                    pytest.fail("second lock acquired")
            raise RuntimeError("fixture exit")
    with dad.daemon_lock(path):
        assert Path(path).read_text().startswith("pid=")


def test_real_git_restore_preserves_source_and_untracked_files(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, text=True, stdout=subprocess.PIPE).stdout
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "site").mkdir()
    (tmp_path / "site/page.html").write_text("original site")
    (tmp_path / "source.txt").write_text("original source")
    git("add", "site", "source.txt")
    git("commit", "-qm", "fixture")
    (tmp_path / "site/page.html").write_text("generated churn")
    (tmp_path / "source.txt").write_text("precious edit")
    (tmp_path / "site/new.html").write_text("untracked")
    assert dad.restore_regenerable_site(str(tmp_path))
    assert (tmp_path / "site/page.html").read_text() == "original site"
    assert (tmp_path / "source.txt").read_text() == "precious edit"
    assert (tmp_path / "site/new.html").read_text() == "untracked"


@pytest.mark.parametrize("value", ["broken json", ""])
def test_corrupt_state_recovers_to_empty(tmp_path, value):
    path = tmp_path / "state.json"
    path.write_text(value)
    assert dad.load_state(str(path)) == {}


@pytest.mark.parametrize("timestamp", [None, "bad-date", ""])
def test_invalid_editorial_timestamp_is_not_due(timestamp):
    assert not dad.should_run_editorial({"batch_reviewed":False,"last_applied_ts":timestamp}, datetime.datetime(2026, 9, 19))


def test_editorial_threshold_inclusive_with_naive_clock():
    state = {"batch_reviewed":False,"last_applied_ts":"2026-09-19T00:00:00+00:00"}
    assert dad.should_run_editorial(state, datetime.datetime(2026,9,19,0,30))
    assert not dad.should_run_editorial(state, datetime.datetime(2026,9,19,0,29,59))


@pytest.mark.parametrize("candidate", [None, [], {"status":"unknown"}, {"status":"clean","filed":True}, {"status":"clean","stale_count":-1}, {"status":"clean","model_count":1000001}])
def test_consistency_rejects_unbounded_checker_results(candidate):
    sha, result = dad._legacy_u18_result(dad.LegacyU18Hooks(lambda:"a"*40, lambda _:candidate))
    assert sha == "a"*40
    assert result == {"status":"checker-error","stale_count":0,"model_count":0,"filed":0}


@pytest.mark.parametrize("frontier", [42,"A"*40,"a"*39])
def test_invalid_frontier_never_invokes_checker(frontier):
    def forbidden(_):
        pytest.fail("checker must not run")
    sha, result = dad._legacy_u18_result(dad.LegacyU18Hooks(lambda:frontier, forbidden))
    assert sha is None and result["status"] == "bad-revision"


@pytest.mark.parametrize("operation", ["resolve", "record", "heartbeat"])
def test_missing_api_configuration_never_attempts_transport(monkeypatch, operation):
    def forbidden(*args, **kwargs):
        pytest.fail("transport must not run")
    monkeypatch.setattr(dad.urllib.request, "urlopen", forbidden)
    call = {"resolve":lambda:dad.resolve_revert_request(None,None,"r","failed"), "record":lambda:dad.record_revert_mutation(None,None,{}), "heartbeat":lambda:dad.post_heartbeat(None,None,ok=True,applied=0,ts="now")}[operation]
    assert call() == {"sent":False,"reason":"no_api_base"}


@pytest.mark.parametrize("operation", ["review", "reverts", "resolve", "record", "heartbeat"])
@pytest.mark.parametrize("kind", ["http", "unreachable"])
def test_http_failures_obey_fatal_review_best_effort_other_contract(monkeypatch, operation, kind):
    error = urllib.error.HTTPError("https://fixture.invalid",404,"missing",{},io.BytesIO(b"missing endpoint")) if kind == "http" else urllib.error.URLError("offline fixture")
    def fail(request, timeout):
        assert request.get_header("User-agent") == dad.SERVICE_USER_AGENT
        raise error
    monkeypatch.setattr(dad.urllib.request, "urlopen", fail)
    call = {"review":lambda:dad.fetch_review("https://fixture.invalid/",None), "reverts":lambda:dad.fetch_revert_requests("https://fixture.invalid",None), "resolve":lambda:dad.resolve_revert_request("https://fixture.invalid",None,"r","failed"), "record":lambda:dad.record_revert_mutation("https://fixture.invalid",None,{}), "heartbeat":lambda:dad.post_heartbeat("https://fixture.invalid",None,ok=True,applied=0,ts="now")}[operation]
    if operation == "review":
        with pytest.raises(dad.DaemonError, match="GET /review"):
            call()
    elif operation == "reverts":
        assert call() == []
    else:
        result = call()
        assert result["sent"] is False
        if operation == "heartbeat" and kind == "http":
            assert result["tolerated"] is True


def test_quiet_tick_dispatches_real_editorial_and_persists_closed_window(tmp_path):
    script(tmp_path, "editorial_pass.py", "import sys\nfrom pathlib import Path\nPath('reviewed-batch').write_text(sys.argv[2])\n")
    path = str(tmp_path / "cache/state.json")
    dad.save_state(path, {"batch_reviewed":False,"last_batch_id":"batch-fixture","last_applied_ts":"2026-09-19T00:00:00+00:00"})
    result = dad.run(api_base=None, token=None, state_path=path,
        now=datetime.datetime(2026,9,19,1,tzinfo=datetime.timezone.utc),
        fetch=lambda:[], editorial=lambda batch:dad.dispatch_editorial(batch,repo_root=str(tmp_path)),
        out=io.StringIO())
    assert result.reason == "no_accepted" and result.editorial_due
    assert result.heartbeat == {"sent":False,"reason":"no_api_base"}
    assert (tmp_path / "reviewed-batch").read_text() == "batch-fixture"
    state = dad.load_state(path)
    assert state["batch_reviewed"] is True
    assert state["last_editorial_ts"] == state["last_run_ts"]
    assert not Path(path + ".tmp").exists()


def test_quiet_dry_run_preserves_due_state_and_skips_optional_mutations(tmp_path):
    path = str(tmp_path / "state.json")
    dad.save_state(path,{"batch_reviewed":False,"last_applied_ts":"2026-09-19T00:00:00+00:00"})
    before = Path(path).read_bytes()
    def forbidden(*args):
        pytest.fail("dry run called mutation")
    result = dad.run(api_base=None,token=None,state_path=path,dry_run=True,
        now=datetime.datetime(2026,9,19,1,tzinfo=datetime.timezone.utc),fetch=lambda:[],
        editorial=forbidden, do_scoped=forbidden, fetch_reverts=forbidden, out=io.StringIO())
    assert result.editorial_due and result.reason == "no_accepted"
    assert Path(path).read_bytes() == before


def test_optional_scoped_and_revert_fetch_exceptions_do_not_block_quiet_tick(tmp_path):
    def fail():
        raise RuntimeError("fixture failure")
    result = dad.run(api_base=None,token=None,state_path=str(tmp_path / "state.json"),
        fetch=lambda:[], do_scoped=fail, fetch_reverts=fail, out=io.StringIO())
    assert result.reason == "no_accepted"
    assert ("scoped_drafts",False) in result.steps
    assert dad.load_state(str(tmp_path / "state.json"))["last_run_ts"]


@pytest.mark.parametrize("guard", [lambda _:None, lambda _:(_ for _ in ()).throw(RuntimeError("private failure"))])
def test_invalid_guard_preserves_retry_state_and_never_runs_engine(tmp_path, guard):
    path = str(tmp_path / "state.json")
    dad.save_state(path,{"last_batch_id":"previous"})
    before = Path(path).read_bytes()
    notices = []
    def forbidden(*args):
        pytest.fail("mutation started without authorized checkout")
    result = dad.run(api_base=None,token=None,state_path=path,
        fetch=lambda:[{"id":"accepted-fixture","status":"accepted"}],
        deploy_guard=guard, deploy_refusal_notify=notices.append,
        apply_engine=forbidden,clean_site=forbidden,out=io.StringIO())
    assert result.reason == "deploy_refused"
    assert len(notices) == 1 and not notices[0].deployable
    assert Path(path).read_bytes() == before
    assert "private failure" not in str(result.steps)


@pytest.mark.parametrize("configuration", [
    {"behind":-1}, {"behind":True}, {"behind":"1"},
    {"failure_reason":"unsupported"},
    {"behind":0,"failure_reason":"fetch_failed","fetch_rc":1},
    {"failure_reason":"fetch_failed","fetch_rc":0},
    {"failure_reason":"fetch_failed","fetch_rc":True},
    {"fetch_rc":1}, {"fetch_stderr":"private"},
])
def test_checkout_status_rejects_inconsistent_metadata(configuration):
    with pytest.raises(ValueError):
        dad.DeployCheckoutStatus(**configuration)


@pytest.mark.parametrize("payload,expected", [({"items":[{"id":"first"}]},[{"id":"first"}]), ({"suggestions":[{"id":"legacy"}]},[{"id":"legacy"}]), ({},[])])
def test_review_decodes_supported_wire_shapes(monkeypatch,payload,expected):
    requests = []
    def opener(request,timeout):
        requests.append(request)
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(dad.urllib.request,"urlopen",opener)
    assert dad.fetch_review("https://fixture.invalid/","fixture-bearer") == expected
    assert requests[0].full_url == "https://fixture.invalid/review"
    assert requests[0].get_header("Authorization") == "Bearer fixture-bearer"


@pytest.mark.parametrize("count", [0,1,21])
def test_failure_notifications_bound_identifiers_and_use_high_priority(count):
    calls = []
    identifiers = ["fixture-%02d" % n for n in range(count)]
    dad.notify_failure(identifiers,topic_resolver=lambda:"fixture",publish=lambda *args,**kw:calls.append((args,kw)))
    args,kw = calls[0]
    assert kw["priority"] == "high"
    for identifier in identifiers[:20]:
        assert identifier in args[2]
    if count == 21:
        assert "fixture-20" not in args[2] and "+1 more" in args[2]
    if count == 0:
        assert "(none)" in args[2]


def test_notification_transport_failure_is_nonfatal_and_clean_consistency_is_silent():
    def fail(*args,**kwargs):
        raise RuntimeError("transport fixture")
    dad.notify_failure(["fixture"],topic_resolver=fail)
    dad.notify_consistency("flagged","batch",topic_resolver=lambda:"fixture",publish=fail)
    def forbidden(*args,**kwargs):
        pytest.fail("clean checks should not notify")
    dad.notify_consistency("clean","batch",topic_resolver=forbidden,publish=forbidden)


def test_revert_record_failure_stops_before_any_publication(tmp_path):
    notices=[]
    def forbidden(*args):
        pytest.fail("journal persistence must precede publication and review")
    result=dad.run(api_base=None,token=None,state_path=str(tmp_path/"state.json"),
        fetch=forbidden,fetch_reverts=lambda:[{"id":"revert-fixture"}],
        deploy_guard=lambda _:dad.DeployCheckoutStatus(0),
        revert_exec=lambda _: (True,{"id":"revert-fixture"}),
        revert_record=lambda *args:{"sent":False},do_rebuild=forbidden,
        do_deploy=forbidden,do_deploy_worker=forbidden,notify=notices.append,out=io.StringIO())
    assert result.reason == "revert_record_failed"
    assert notices == [["revert-fixture"]]
    assert not (tmp_path/"state.json").exists()
