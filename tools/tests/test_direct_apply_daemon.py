#!/usr/bin/env python3
r"""Tests for tools/direct_apply_daemon.py — the home-box apply daemon.

Pure-logic + orchestration tests: NO network, NO subprocess, NO live Worker. The
review fetch, apply-engine invocation, rebuild, deploy, heartbeat, notify, and
editorial dispatch are all injected with fakes; the ordering, no-op, failure, and
crash-idempotency behaviours are exercised directly.

Run:  python3 -m pytest tools/tests/test_direct_apply_daemon.py -q
"""

from __future__ import annotations

import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import direct_apply_daemon as dad  # noqa: E402


def row(id, status="accepted", source_ref="data/matters/m03/interview.json"):
    return {"id": id, "status": status, "source_ref": source_ref}


NOW = datetime.datetime(2026, 7, 19, 18, 0, 0, tzinfo=datetime.timezone.utc)


class Recorder:
    """Records the ordered sequence of daemon side effects for order assertions."""

    def __init__(self, *, rows=None, apply_rc=0, rebuild_ok=True, deploy_ok=True):
        self.rows = rows if rows is not None else []
        self.apply_rc = apply_rc
        self.rebuild_ok = rebuild_ok
        self.deploy_ok = deploy_ok
        self.calls = []
        self.heartbeats = []
        self.notified = None
        self.deploy_refusals = []
        self.editorial_batch = None

    def fetch(self):
        self.calls.append("fetch")
        return self.rows

    def apply_engine(self, batch_id):
        self.calls.append("apply")
        return self.apply_rc, "engine-tail"

    def rebuild(self):
        self.calls.append("rebuild")
        return self.rebuild_ok, "rebuild-tail"

    def deploy(self, branch):
        self.calls.append("deploy:%s" % branch)
        return self.deploy_ok, "deploy-tail"

    def heartbeat(self, ok, applied):
        self.calls.append("heartbeat")
        hb = {"ok": ok, "applied": applied}
        self.heartbeats.append(hb)
        return {"sent": True, "status": 200}

    def notify(self, ids):
        self.calls.append("notify")
        self.notified = list(ids)

    def notify_deploy_refusal(self, status):
        self.calls.append("notify_deploy_refusal")
        self.deploy_refusals.append(status)

    def editorial(self, batch_id):
        self.calls.append("editorial")
        self.editorial_batch = batch_id


def _run(rec, **kw):
    defaults = dict(
        api_base="https://x/edit/v1", token="tok", branch="feat/canonical-docs",
        now=NOW, fetch=rec.fetch, apply_engine=rec.apply_engine,
        do_rebuild=rec.rebuild, do_deploy=rec.deploy, heartbeat=rec.heartbeat,
        notify=rec.notify, editorial=rec.editorial, out=io.StringIO(),
        deploy_guard=lambda _branch: dad.DeployCheckoutStatus(0),
        deploy_refusal_notify=rec.notify_deploy_refusal,
    )
    defaults.update(kw)
    return dad.run(**defaults)


def _git(td, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=td, check=check, capture_output=True, text=True)


def _stale_remote_fixture(root, branch="main"):
    """Local checkout/cache stay at A while a bare upstream advances to B."""
    remote = os.path.join(root, "remote.git")
    seed = os.path.join(root, "seed")
    checkout = os.path.join(root, "checkout")
    writer = os.path.join(root, "writer")
    _git(root, "init", "-q", "--bare", remote)
    _git(root, "init", "-q", seed)
    _git(seed, "config", "user.email", "test@example.invalid")
    _git(seed, "config", "user.name", "Test")
    _git(seed, "commit", "--allow-empty", "-q", "-m", "A")
    _git(seed, "branch", "-m", branch)
    _git(seed, "remote", "add", "origin", remote)
    _git(seed, "push", "-q", "-u", "origin", branch)
    _git(root, "clone", "-q", "--shared", "--branch", branch, remote, checkout)
    _git(root, "clone", "-q", "--shared", "--branch", branch, remote, writer)
    _git(writer, "config", "user.email", "test@example.invalid")
    _git(writer, "config", "user.name", "Test")
    local_a = _git(checkout, "rev-parse", "refs/heads/" + branch).stdout.strip()
    cached_a = _git(
        checkout, "rev-parse", "refs/remotes/origin/" + branch).stdout.strip()
    _git(writer, "commit", "--allow-empty", "-q", "-m", "B")
    remote_b = _git(writer, "rev-parse", "HEAD").stdout.strip()
    _git(writer, "push", "-q", "origin", branch)
    assert local_a == cached_a and remote_b != local_a
    return checkout, remote, local_a, remote_b


class TestAcceptedFilter(unittest.TestCase):
    def test_only_accepted(self):
        rows = [row("aaaaaaaa", "accepted"), row("bbbbbbbb", "pending"),
                row("cccccccc", "in_flight"), row("dddddddd", "applied"),
                row("eeeeeeee", "accepted")]
        self.assertEqual(dad.accepted_ids(rows), ["aaaaaaaa", "eeeeeeee"])

    def test_skips_rows_without_id(self):
        self.assertEqual(dad.accepted_ids([{"status": "accepted"}]), [])


class TestDeployCheckoutGuard(unittest.TestCase):
    def test_stale_cached_upstream_fetches_then_apply_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            checkout, _remote, cached_a, remote_b = _stale_remote_fixture(d)
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(
                rec, state_path=os.path.join(d, "state.json"), branch="main",
                deploy_guard=lambda branch: dad.checkout_deploy_status(
                    checkout, branch=branch))

            self.assertEqual(res.reason, "deploy_refused")
            self.assertEqual(rec.calls, ["fetch", "heartbeat", "notify_deploy_refusal"])
            status = rec.deploy_refusals[0]
            self.assertEqual(status.reason, "behind")
            self.assertEqual(status.behind, 1)
            self.assertFalse(status.deployable)
            self.assertEqual(
                _git(checkout, "rev-parse", "refs/remotes/origin/main").stdout.strip(),
                remote_b)
            self.assertEqual(
                _git(checkout, "rev-parse", "refs/heads/main").stdout.strip(), cached_a)

    def test_stale_cached_upstream_fetch_failure_refuses_apply(self):
        with tempfile.TemporaryDirectory() as d:
            checkout, _remote, cached_a, _remote_b = _stale_remote_fixture(d)
            missing_remote = os.path.join(d, "missing.git")
            _git(checkout, "remote", "set-url", "origin", missing_remote)
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(
                rec, state_path=os.path.join(d, "state.json"), branch="main",
                deploy_guard=lambda branch: dad.checkout_deploy_status(
                    checkout, branch=branch))

            self.assertEqual(res.reason, "deploy_refused")
            status = rec.deploy_refusals[0]
            self.assertEqual(status.reason, "fetch_failed")
            self.assertNotEqual(status.fetch_rc, 0)
            self.assertTrue(status.fetch_stderr)
            self.assertLessEqual(len(status.fetch_stderr), dad.FETCH_STDERR_LIMIT)
            self.assertFalse(status.deployable)
            self.assertNotIn("apply", rec.calls)
            self.assertEqual(
                _git(checkout, "rev-parse", "refs/remotes/origin/main").stdout.strip(),
                cached_a)

    def test_up_to_date_after_fetch_allows_apply(self):
        with tempfile.TemporaryDirectory() as d:
            checkout, _remote, _cached_a, remote_b = _stale_remote_fixture(d)
            _git(checkout, "reset", "-q", "--hard", remote_b)
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(
                rec, state_path=os.path.join(d, "state.json"), branch="main",
                deploy_guard=lambda branch: dad.checkout_deploy_status(
                    checkout, branch=branch))

            self.assertEqual(res.reason, "applied")
            self.assertEqual(rec.deploy_refusals, [])
            self.assertIn("deploy:main", rec.calls)

    def test_env_selected_branch_is_fetched_and_compared_for_apply(self):
        selected = "release/env-selected"
        with tempfile.TemporaryDirectory() as d:
            checkout, remote, cached_a, remote_b = _stale_remote_fixture(
                d, branch=selected)
            remote_name = "deploy-upstream"
            merge_ref = "refs/heads/releases/canonical"
            tracking_ref = "refs/remotes/deploy-upstream/releases/canonical"
            _git(remote, "update-ref", merge_ref, remote_b)
            _git(checkout, "remote", "rename", "origin", remote_name)
            _git(checkout, "config", "branch.%s.merge" % selected, merge_ref)
            _git(checkout, "update-ref", tracking_ref, cached_a)
            git_calls = []
            fetch_calls = []

            def git(args):
                git_calls.append(args)
                return dad._git(args, checkout)

            def fetch(args):
                fetch_calls.append(args)
                return dad._fetch_git(args, checkout)

            rec = Recorder(rows=[row("aaaaaaaa")])
            with mock.patch.dict(
                    os.environ, {dad.ENV_DEPLOY_BRANCH: selected}, clear=True), \
                    mock.patch.object(dad, "run") as run:
                def exercise_selected_branch(**kwargs):
                    self.assertEqual(kwargs["branch"], selected)
                    status = dad.checkout_deploy_status(
                        checkout, branch=kwargs["branch"], git=git, fetch=fetch)
                    rec.notify_deploy_refusal(status)
                    return dad.DaemonResult(
                        0, "batch-1", "deploy_refused", {}, False, [])

                run.side_effect = exercise_selected_branch
                self.assertEqual(dad.main(["--no-lock"]), 1)

            self.assertEqual(fetch_calls, [[
                "fetch", "--no-tags", "--no-prune", remote_name,
                merge_ref + ":" + tracking_ref,
            ]])
            self.assertIn([
                "rev-list", "--count",
                "refs/heads/release/env-selected.."
                "refs/remotes/deploy-upstream/releases/canonical",
            ], git_calls)
            self.assertEqual(rec.deploy_refusals[0].reason, "behind")

    def test_fetch_exceptions_refuse_with_bounded_diagnostics(self):
        cases = (
            (subprocess.TimeoutExpired(
                cmd=["git", "fetch"], timeout=1, stderr=b"timeout-detail"), 124),
            (OSError("transport unavailable"), 127),
        )
        for raised, expected_rc in cases:
            with self.subTest(exception=type(raised).__name__), \
                    tempfile.TemporaryDirectory() as d:
                checkout, _remote, _cached_a, _remote_b = _stale_remote_fixture(d)

                def fetch(_args, error=raised):
                    raise error

                status = dad.checkout_deploy_status(
                    checkout, branch="main", fetch=fetch, fetch_timeout=1)

                self.assertEqual(status.reason, "fetch_failed")
                self.assertEqual(status.fetch_rc, expected_rc)
                self.assertTrue(status.fetch_stderr)
                self.assertLessEqual(len(status.fetch_stderr), dad.FETCH_STDERR_LIMIT)
                self.assertFalse(status.deployable)

        timeout = subprocess.TimeoutExpired(
            cmd=["git", "fetch"], timeout=1, stderr=b"x" * 1000)
        with mock.patch.object(dad.subprocess, "run", side_effect=timeout):
            rc, stderr = dad._fetch_git(["fetch"], "/repo", timeout=1)
        self.assertEqual(rc, 124)
        self.assertLessEqual(len(stderr), dad.FETCH_STDERR_LIMIT)
        self.assertIn("timed out", stderr)

    def test_deployed_branch_current_deploys_as_before(self):
        calls = []

        def git(args):
            calls.append(args)
            if args == ["check-ref-format", "refs/heads/feat/canonical-docs"]:
                return 0, ""
            if args == ["show-ref", "--verify", "--quiet",
                        "refs/heads/feat/canonical-docs"]:
                return 0, ""
            if args == ["config", "--get",
                        "branch.feat/canonical-docs.remote"]:
                return 0, "origin\n"
            if args == ["config", "--get",
                        "branch.feat/canonical-docs.merge"]:
                return 0, "refs/heads/feat/canonical-docs\n"
            if args == ["for-each-ref", "--format=%(upstream)",
                        "refs/heads/feat/canonical-docs"]:
                return 0, "refs/remotes/origin/feat/canonical-docs\n"
            if args in (["check-ref-format", "refs/heads/feat/canonical-docs"],
                        ["check-ref-format",
                         "refs/remotes/origin/feat/canonical-docs"]):
                return 0, ""
            if args == ["rev-list", "--count", "refs/heads/feat/canonical-docs.."
                        "refs/remotes/origin/feat/canonical-docs"]:
                return 0, "0\n"
            if args == ["symbolic-ref", "--quiet", "HEAD"]:
                return 0, "refs/heads/feat/canonical-docs\n"
            self.fail("unexpected git invocation: %r" % (args,))

        def fetch(args):
            calls.append(args)
            return 0, ""

        status = dad.checkout_deploy_status(
            "/repo", branch="feat/canonical-docs", git=git, fetch=fetch)
        self.assertEqual(status, dad.DeployCheckoutStatus(0))

        with tempfile.TemporaryDirectory() as d:
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=os.path.join(d, "state.json"),
                       deploy_guard=lambda branch: (
                           self.assertEqual(branch, "feat/canonical-docs") or status))

        self.assertEqual(res.reason, "applied")
        self.assertEqual(
            rec.calls,
            ["fetch", "apply", "rebuild", "deploy:feat/canonical-docs", "heartbeat"])
        self.assertEqual(rec.deploy_refusals, [])
        self.assertEqual(calls[-1], ["symbolic-ref", "--quiet", "HEAD"])

    def test_deployed_branch_behind_while_head_current_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            checkout, _remote, _cached_a, remote_b = _stale_remote_fixture(
                d, branch="stale-deploy")
            _git(checkout, "branch", "current-head", remote_b)
            _git(checkout, "checkout", "-q", "current-head")
            _git(checkout, "push", "-q", "-u", "origin", "current-head")

            self.assertEqual(
                dad.checkout_deploy_status(checkout, branch="current-head"),
                dad.DeployCheckoutStatus(0))
            status = dad.checkout_deploy_status(checkout, branch="stale-deploy")
            self.assertEqual(status, dad.DeployCheckoutStatus(1))

            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=os.path.join(d, "state.json"),
                       branch="stale-deploy",
                       deploy_guard=lambda branch: dad.checkout_deploy_status(
                           checkout, branch=branch))

        self.assertEqual(res.reason, "deploy_refused")
        self.assertEqual(rec.calls, ["fetch", "heartbeat", "notify_deploy_refusal"])
        self.assertEqual(rec.heartbeats, [{"ok": False, "applied": 0}])
        self.assertEqual(rec.deploy_refusals, [status])
        self.assertIsNone(rec.notified)

    def test_different_current_deployed_branch_and_head_refuse(self):
        def git(args):
            if args[0] == "check-ref-format":
                return 0, ""
            if args[:3] == ["show-ref", "--verify", "--quiet"]:
                return 0, ""
            if args[:2] == ["for-each-ref", "--format=%(upstream)"]:
                return 0, "refs/remotes/origin/deploy\n"
            if args == ["symbolic-ref", "--quiet", "HEAD"]:
                return 0, "refs/heads/checked-out\n"
            if args[:2] == ["rev-list", "--count"]:
                return 0, "0\n"
            self.fail("unexpected git invocation: %r" % (args,))

        status = dad.checkout_deploy_status("/repo", branch="deploy", git=git)
        self.assertEqual(status, dad.DeployCheckoutStatus())

        with tempfile.TemporaryDirectory() as d:
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=os.path.join(d, "state.json"),
                       branch="deploy", deploy_guard=lambda _branch: status)

        self.assertEqual(res.reason, "deploy_refused")
        self.assertEqual(rec.calls, ["fetch", "heartbeat", "notify_deploy_refusal"])

    def test_missing_branch_prefix_does_not_resolve_descendant(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=d, check=True, capture_output=True, text=True)

            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            git("commit", "--allow-empty", "-m", "base")
            git("branch", "-m", "release/v2")
            git("remote", "add", "origin", ".")
            current = git("rev-parse", "HEAD").stdout.strip()
            git("update-ref", "refs/remotes/origin/release/v2", current)
            git("config", "branch.release/v2.remote", "origin")
            git("config", "branch.release/v2.merge", "refs/heads/release/v2")

            self.assertEqual(
                dad.checkout_deploy_status(d, branch="release"),
                dad.DeployCheckoutStatus())

    def test_unresolvable_deployed_branch_refuses(self):
        calls = []

        def git(args):
            calls.append(args)
            if args == ["check-ref-format", "refs/heads/missing-deploy"]:
                return 0, ""
            if args == ["show-ref", "--verify", "--quiet",
                        "refs/heads/missing-deploy"]:
                return 1, ""
            self.fail("unexpected git invocation: %r" % (args,))

        status = dad.checkout_deploy_status(
            "/repo", branch="missing-deploy", git=git)
        self.assertEqual(
            status, dad.DeployCheckoutStatus())

        with tempfile.TemporaryDirectory() as d:
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=os.path.join(d, "state.json"),
                       branch="missing-deploy",
                       deploy_guard=lambda _branch: status)

        self.assertEqual(res.reason, "deploy_refused")
        self.assertNotIn("apply", rec.calls)
        self.assertFalse(any(call.startswith("deploy:") for call in rec.calls))
        self.assertEqual(rec.deploy_refusals, [status])

    def test_partially_configured_upstream_refuses_without_fallback(self):
        calls = []

        def git(args):
            calls.append(args)
            if args in (["check-ref-format", "refs/heads/deploy"],
                        ["show-ref", "--verify", "--quiet", "refs/heads/deploy"]):
                return 0, ""
            if args == ["config", "--get", "branch.deploy.remote"]:
                return 0, "origin\n"
            if args == ["config", "--get", "branch.deploy.merge"]:
                return 1, ""
            self.fail("unexpected git invocation: %r" % (args,))

        self.assertEqual(
            dad.checkout_deploy_status("/repo", branch="deploy", git=git),
            dad.DeployCheckoutStatus())
        self.assertFalse(any(call[0] == "fetch" for call in calls))

    def test_both_upstream_settings_unset_fall_back_to_origin_branch(self):
        with tempfile.TemporaryDirectory() as d:
            checkout, _remote, _cached_a, remote_b = _stale_remote_fixture(d)
            _git(checkout, "config", "--unset", "branch.main.remote")
            _git(checkout, "config", "--unset", "branch.main.merge")

            status = dad.checkout_deploy_status(checkout, branch="main")

            self.assertEqual(status.behind, 1)
            self.assertEqual(status.reason, "behind_fallback_origin")
            self.assertTrue(status.upstream_fallback)
            self.assertFalse(status.deployable)

            _git(checkout, "reset", "-q", "--hard", remote_b)
            current = dad.checkout_deploy_status(checkout, branch="main")
            self.assertEqual(current.reason, "current_fallback_origin")
            self.assertTrue(current.deployable)

    def test_local_repository_upstream_refuses_without_fetch(self):
        def git(args):
            if args in (["check-ref-format", "refs/heads/deploy"],
                        ["show-ref", "--verify", "--quiet", "refs/heads/deploy"],
                        ["check-ref-format", "refs/heads/upstream"]):
                return 0, ""
            if args == ["config", "--get", "branch.deploy.remote"]:
                return 0, ".\n"
            if args == ["config", "--get", "branch.deploy.merge"]:
                return 0, "refs/heads/upstream\n"
            if args == ["for-each-ref", "--format=%(upstream)",
                        "refs/heads/deploy"]:
                return 0, "refs/heads/upstream\n"
            self.fail("unexpected git invocation: %r" % (args,))

        def fetch(_args):
            self.fail("a local branch ref must never be a fetch destination")

        status = dad.checkout_deploy_status(
            "/repo", branch="deploy", git=git, fetch=fetch)
        self.assertEqual(status.reason, "upstream_unresolvable")
        self.assertFalse(status.deployable)

    def test_each_refusal_preserves_state_and_next_run_can_resume(self):
        initial = {"last_batch_id": "older-batch", "batch_reviewed": False,
                   "last_applied_ts": "2026-07-19T17:59:00+00:00"}
        current = dad.DeployCheckoutStatus(0)
        for refusal in (dad.DeployCheckoutStatus(4), dad.DeployCheckoutStatus()):
            with self.subTest(reason=refusal.reason), tempfile.TemporaryDirectory() as d:
                state_path = os.path.join(d, "state.json")
                dad.save_state(state_path, initial)

                refused = Recorder(rows=[row("aaaaaaaa")])
                first = _run(refused, state_path=state_path,
                             deploy_guard=lambda _branch: refusal)
                self.assertEqual(first.reason, "deploy_refused")
                self.assertEqual(dad.load_state(state_path), initial)

                resumed = Recorder(rows=[row("aaaaaaaa")])
                second = _run(resumed, state_path=state_path,
                              now=NOW + datetime.timedelta(minutes=2),
                              deploy_guard=lambda _branch: current)
                state = dad.load_state(state_path)

                self.assertEqual(second.reason, "applied")
                self.assertEqual(resumed.calls.count("apply"), 1)
                self.assertEqual(state["last_batch_id"], second.batch_id)
                self.assertFalse(state["batch_reviewed"])

    def test_refusal_notification_contains_only_checkout_metadata(self):
        published = []
        status = dad.DeployCheckoutStatus(39)
        dad.notify_deploy_refusal(
            status, topic_resolver=lambda: "topic",
            publish=lambda *args, **kwargs: published.append((args, kwargs)))

        args, kwargs = published[0]
        self.assertEqual(args[0], "topic")
        self.assertIn("refused to deploy", args[2])
        self.assertIn("behind its recorded upstream by 39 commits", args[2])
        self.assertNotIn("data/", args[2])
        self.assertEqual(kwargs["priority"], "high")

    def test_fetch_failure_notification_excludes_stderr(self):
        published = []
        status = dad.DeployCheckoutStatus(
            failure_reason="fetch_failed", fetch_rc=128,
            fetch_stderr="sensitive diagnostic /private/path")
        dad.notify_deploy_refusal(
            status, topic_resolver=lambda: "topic",
            publish=lambda *args, **kwargs: published.append((args, kwargs)))

        args, kwargs = published[0]
        self.assertIn("git fetch rc 128", args[2])
        self.assertNotIn("sensitive diagnostic", args[2])
        self.assertNotIn("/private/path", args[2])
        self.assertEqual(kwargs["priority"], "high")

    def test_main_maps_deploy_refusal_to_exit_one(self):
        refused = dad.DaemonResult(
            0, "batch-1", "deploy_refused", {"sent": True}, False, [])
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(dad, "run", return_value=refused):
            self.assertEqual(dad.main(["--no-lock"]), 1)


class TestRevertJournalTransport(unittest.TestCase):
    def test_record_and_complete_send_cloudflare_safe_service_user_agent(self):
        requests = []

        class Response(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False

        def opener(request, timeout=None):
            requests.append(request)
            return Response(b'{"ok":true}')

        evidence = {"id":"revert-1","batch_id":"revert-revert-1"}
        with mock.patch.object(dad.urllib.request, "urlopen", opener):
            self.assertTrue(dad.record_revert_mutation(
                "https://edit.example/edit/v1", "secret", evidence, "record")["sent"])
            self.assertTrue(dad.record_revert_mutation(
                "https://edit.example/edit/v1", "secret", evidence, "complete")["sent"])

        self.assertEqual([json.loads(req.data)["action"] for req in requests],
                         ["record", "complete"])
        for request in requests:
            self.assertEqual(request.full_url,
                             "https://edit.example/edit/v1/revert-record")
            self.assertEqual(request.get_header("User-agent"),
                             "sonsteng-apply-daemon/1.0")
            self.assertFalse(request.get_header("User-agent").startswith("Python-urllib/"))


class TestNoOpPath(unittest.TestCase):
    def test_no_accepted_heartbeats_zero_and_skips_engine(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("bbbbbbbb", "pending")])
            res = _run(rec, state_path=sp)
        self.assertEqual(res.reason, "no_accepted")
        self.assertEqual(res.applied, 0)
        self.assertEqual(rec.calls, ["fetch", "heartbeat"])  # NO apply/rebuild/deploy
        self.assertEqual(rec.heartbeats, [{"ok": True, "applied": 0}])


class TestApplyBatchOrdering(unittest.TestCase):
    def test_order_apply_rebuild_deploy_heartbeat(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa"), row("eeeeeeee")])
            res = _run(rec, state_path=sp)
        self.assertEqual(res.reason, "applied")
        self.assertEqual(res.applied, 2)
        # The load-bearing sequence: apply -> rebuild -> deploy -> heartbeat.
        self.assertEqual(rec.calls,
                         ["fetch", "apply", "rebuild", "deploy:feat/canonical-docs", "heartbeat"])
        self.assertEqual(rec.heartbeats[-1], {"ok": True, "applied": 2})
        self.assertIsNone(rec.notified)

    def test_state_records_unreviewed_batch_for_session_end(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=sp)
            st = dad.load_state(sp)
        self.assertEqual(st["last_batch_id"], res.batch_id)
        self.assertEqual(st["last_batch_size"], 1)
        self.assertFalse(st["batch_reviewed"])  # editorial window opens


class TestLegacyU18Consistency(unittest.TestCase):
    def test_main_rejects_dev_bearer_reused_as_observer(self):
        env = {dad.ENV_API_BASE:"https://edit.example/edit/v1",
               dad.ENV_SERVICE_TOKEN:"same-secret",
               dad.ENV_OBSERVER_TOKEN:"same-secret"}
        with mock.patch.dict(os.environ,env,clear=True), \
             mock.patch("sys.stderr",new=io.StringIO()) as err:
            self.assertEqual(dad.main(["--dry-run","--no-lock"]),2)
            self.assertIn("must be distinct",err.getvalue())

    def test_successful_accepted_batch_uses_exact_frontier_and_records_clean(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d,"state.json")
            rec = Recorder(rows=[row("aaaaaaaa")])
            calls,alerts = [],[]
            result = _run(rec,state_path=sp,legacy_u18=dad.LegacyU18Hooks(
                fetch_frontier=lambda: "a"*40,
                check=lambda sha: (calls.append(sha) or {
                  "status":"clean","stale_count":0,"model_count":0,"filed":0}),
                notify=lambda status,batch: alerts.append((status,batch))))
            state = dad.load_state(sp)
        self.assertEqual(result.reason,"applied")
        self.assertEqual(calls,["a"*40])
        self.assertEqual(state["legacy_u18"]["status"],"clean")
        self.assertEqual(state["legacy_u18"]["frontier_sha"],"a"*40)
        self.assertEqual(alerts,[("clean",result.batch_id)])
        self.assertEqual([step[0] for step in result.steps].count("legacy_u18"),1)

    def test_flagged_missing_bad_revision_and_checker_error_are_nonfatal(self):
        scenarios = [
          (lambda: "b"*40,lambda _sha:{"status":"flagged","stale_count":2,
             "model_count":0,"filed":2},"flagged"),
          (lambda: None,lambda _sha:None,"missing-baseline"),
          (lambda: "c"*40,lambda _sha:{"status":"bad-revision"},"bad-revision"),
          (lambda: "d"*40,lambda _sha:(_ for _ in ()).throw(RuntimeError("private")),
             "checker-error"),
        ]
        for frontier,checker,expected in scenarios:
            with self.subTest(expected),tempfile.TemporaryDirectory() as d:
                sp = os.path.join(d,"state.json")
                rec = Recorder(rows=[row("aaaaaaaa")])
                result = _run(rec,state_path=sp,legacy_u18=dad.LegacyU18Hooks(
                    fetch_frontier=frontier,check=checker,notify=lambda *_:None))
                state = dad.load_state(sp)
                self.assertEqual(result.reason,"applied")
                self.assertEqual(state["legacy_u18"]["status"],expected)
                self.assertNotIn("private",json.dumps(state))

    def test_no_accepted_and_dry_run_never_invoke_or_persist(self):
        for rows,dry in [([],False),([row("aaaaaaaa")],True)]:
            with self.subTest(dry=dry),tempfile.TemporaryDirectory() as d:
                sp = os.path.join(d,"state.json")
                rec = Recorder(rows=rows)
                called=[]
                _run(rec,state_path=sp,dry_run=dry,legacy_u18=dad.LegacyU18Hooks(
                    fetch_frontier=lambda: called.append("frontier"),
                    check=lambda _sha: called.append("checker")))
                self.assertEqual(called,[])
                self.assertNotIn("legacy_u18",dad.load_state(sp))

    def test_completed_frontier_accepts_context_or_active_base_only(self):
        class Observer:
            def __init__(self,context): self.context=context
            def preparation_context(self): return self.context
        self.assertEqual(dad.completed_production_frontier(
            Observer({"base_sha":"a"*40})),"a"*40)
        self.assertEqual(dad.completed_production_frontier(
            Observer({"active_release":{"base_sha":"b"*40}})),"b"*40)
        self.assertIsNone(dad.completed_production_frontier(
            Observer({"base_sha":"not-a-revision"})))


class TestFailurePath(unittest.TestCase):
    def test_apply_failure_heartbeats_false_and_alerts_ids_only(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa"), row("eeeeeeee")], apply_rc=2)
            res = _run(rec, state_path=sp)
        self.assertEqual(res.reason, "apply_failed")
        self.assertEqual(rec.heartbeats[-1], {"ok": False, "applied": 0})
        # Alert carries the failed IDS ONLY (never content), and rebuild/deploy skipped.
        self.assertEqual(rec.notified, ["aaaaaaaa", "eeeeeeee"])
        self.assertNotIn("rebuild", rec.calls)
        self.assertNotIn("deploy:feat/canonical-docs", rec.calls)

    def test_rebuild_failure_alerts_and_stops(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa")], rebuild_ok=False)
            res = _run(rec, state_path=sp)
        self.assertEqual(res.reason, "rebuild_failed")
        self.assertEqual(rec.heartbeats[-1], {"ok": False, "applied": 0})
        self.assertEqual(rec.notified, ["aaaaaaaa"])
        self.assertNotIn("deploy:feat/canonical-docs", rec.calls)

    def test_deploy_failure_alerts(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa")], deploy_ok=False)
            res = _run(rec, state_path=sp)
        self.assertEqual(res.reason, "deploy_failed")
        self.assertEqual(rec.notified, ["aaaaaaaa"])


class TestCrashIdempotency(unittest.TestCase):
    """Reasoning test: a crash mid-sequence must NOT double-apply. Mechanism: the
    engine reconciles FIRST and only claims `accepted` rows; a batch that already
    merged before the crash leaves its rows in `applied` (terminal), so the NEXT
    daemon tick sees ZERO accepted rows and no-ops — no second apply."""

    def test_rerun_after_crash_sees_applied_rows_and_no_ops(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            # First tick: one accepted row -> flush.
            rec1 = Recorder(rows=[row("aaaaaaaa", "accepted")])
            r1 = _run(rec1, state_path=sp)
            self.assertEqual(r1.reason, "applied")
            # Crash happened AFTER merge; the engine's reconcile+finalize left the
            # row terminal `applied`. The retry tick fetches the post-crash truth:
            rec2 = Recorder(rows=[row("aaaaaaaa", "applied")])
            r2 = _run(rec2, state_path=sp, now=NOW + datetime.timedelta(minutes=2))
            self.assertEqual(r2.reason, "no_accepted")
            self.assertNotIn("apply", rec2.calls)  # NOT re-applied

    def test_partial_crash_requeued_rows_are_reflushed_once(self):
        # Crash BEFORE merge: reconcile re-queued in_flight -> accepted. The retry
        # sees `accepted` again and flushes exactly once (canonical was untouched).
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa", "accepted")])
            res = _run(rec, state_path=sp)
            self.assertEqual(res.reason, "applied")
            self.assertEqual(rec.calls.count("apply"), 1)


class TestHeartbeatTolerance(unittest.TestCase):
    def test_heartbeat_404_does_not_fail_run(self):
        def hb404(ok, applied):
            return {"sent": False, "reason": "http_404", "tolerated": True}
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=sp, heartbeat=hb404)
        self.assertEqual(res.reason, "applied")  # apply succeeds despite HB 404


class TestEditorialWindowing(unittest.TestCase):
    def test_due_after_idle_over_unreviewed_batch(self):
        st = {"batch_reviewed": False,
              "last_applied_ts": (NOW - datetime.timedelta(minutes=31)).isoformat()}
        self.assertTrue(dad.should_run_editorial(st, NOW, idle_minutes=30))

    def test_not_due_before_idle(self):
        st = {"batch_reviewed": False,
              "last_applied_ts": (NOW - datetime.timedelta(minutes=10)).isoformat()}
        self.assertFalse(dad.should_run_editorial(st, NOW, idle_minutes=30))

    def test_not_due_when_already_reviewed(self):
        st = {"batch_reviewed": True,
              "last_applied_ts": (NOW - datetime.timedelta(hours=5)).isoformat()}
        self.assertFalse(dad.should_run_editorial(st, NOW, idle_minutes=30))

    def test_not_due_without_batch(self):
        self.assertFalse(dad.should_run_editorial({}, NOW))

    def test_naive_timestamp_compared_safely(self):
        st = {"batch_reviewed": False,
              "last_applied_ts": "2026-07-19T17:00:00"}  # naive
        self.assertTrue(dad.should_run_editorial(st, NOW, idle_minutes=30))

    def test_session_end_dispatch_fires_and_marks_reviewed(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            dad.save_state(sp, {
                "batch_reviewed": False, "last_batch_id": "batch-XYZ",
                "last_applied_ts": (NOW - datetime.timedelta(minutes=45)).isoformat()})
            rec = Recorder(rows=[])  # quiet tick
            res = _run(rec, state_path=sp)
            st = dad.load_state(sp)
        self.assertTrue(res.editorial_due)
        self.assertEqual(rec.editorial_batch, "batch-XYZ")  # dispatched over the batch
        self.assertTrue(st["batch_reviewed"])  # window closed


class TestStateRoundTrip(unittest.TestCase):
    def test_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "sub", "state.json")
            dad.save_state(sp, {"batch_reviewed": True, "last_batch_id": "b1"})
            self.assertEqual(dad.load_state(sp)["last_batch_id"], "b1")

    def test_load_missing_is_empty(self):
        self.assertEqual(dad.load_state("/nonexistent/nope.json"), {})


class TestDryRun(unittest.TestCase):
    def test_dry_run_plans_without_engine_or_state_write(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa")])
            res = _run(rec, state_path=sp, dry_run=True)
        self.assertEqual(res.reason, "dry_run")
        self.assertNotIn("apply", rec.calls)
        self.assertFalse(os.path.exists(sp))  # no state mutation in dry-run


class TestRegenerableSiteChurn(unittest.TestCase):
    """The stamp-churn guard (2026-07-24).

    `build_site.py` writes the CURRENT HEAD sha into
    site/platform/data/.build-stamp.json, so the tick's post-apply rebuild — which
    runs at the just-merged commit — always leaves that one tracked file dirty.
    The apply engine's assert_clean_tree is strict, so without a restore the tick
    AFTER any successful apply would refuse to run ("canonical tree is dirty").
    """

    def test_clean_site_runs_before_apply_and_after_deploy(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("aaaaaaaa")])

            def clean_site():
                rec.calls.append("clean_site")
                return True

            res = _run(rec, state_path=sp, clean_site=clean_site)
        self.assertEqual(res.reason, "applied")
        # Guard on BOTH sides: entering the apply (clears the previous tick's
        # churn) and leaving the tick (clears this tick's own rebuild churn).
        self.assertEqual(
            rec.calls,
            ["fetch", "clean_site", "apply", "rebuild",
             "deploy:feat/canonical-docs", "clean_site", "heartbeat"])

    def test_quiet_tick_never_touches_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            rec = Recorder(rows=[row("bbbbbbbb", "pending")])
            res = _run(rec, state_path=sp,
                       clean_site=lambda: rec.calls.append("clean_site"))
        self.assertEqual(res.reason, "no_accepted")
        self.assertNotIn("clean_site", rec.calls)

    def test_restore_only_touches_site(self):
        """The restore is scoped to site/ — source dirt must still stop the engine."""
        seen = {}

        def fake_git(args, repo_root, timeout=300):
            seen["args"] = list(args)
            return 0, ""

        real = dad._git
        dad._git = fake_git
        try:
            self.assertTrue(dad.restore_regenerable_site("/tmp/whatever"))
        finally:
            dad._git = real
        self.assertEqual(seen["args"], ["checkout", "--", "site"])


if __name__ == "__main__":
    unittest.main()
