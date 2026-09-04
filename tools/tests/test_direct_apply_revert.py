#!/usr/bin/env python3
r"""Tests for the revert-v1 path of tools/direct_apply_daemon.py.

Two layers:
  * run() ORCHESTRATION — approved reverts are drained FIRST each tick; success
    deploys + resolves 'done' + heartbeats ok; failure resolves 'failed' +
    heartbeats ok:false + alerts (IDs only). All I/O injected (no network/subproc).
  * execute_revert GIT path — exercised against a real throwaway git repo: a clean
    run range reverts to the prior content; an overlapping edit ABORTS cleanly
    (never a partial revert), leaving the tree pristine.

Run:  python3 -m pytest tools/tests/test_direct_apply_revert.py -q
"""

from __future__ import annotations

import datetime
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import direct_apply_daemon as dad  # noqa: E402

NOW = datetime.datetime(2026, 7, 19, 18, 0, 0, tzinfo=datetime.timezone.utc)


def _empty_review():
    return []


def _merged_revert_evidence():
    return {"id":"rq1","batch_id":"revert-rq1","mutation_actor":"slot:john",
        "source_ref":"data/public.json","original_text":"after","new_text":"before",
        "original_hash":"after-hash","new_hash":"before-hash",
        "base_sha":"base","commit_sha":"sha123","generator_id":"generator-v1",
        "mutation_phase":"merged"}


class RevertRecorder:
    """Injected revert side-effects for run() orchestration assertions."""

    def __init__(self, reqs, *, exec_ok=True, deploy_ok=True, detail=None):
        self.reqs = reqs
        self.exec_ok = exec_ok
        self.deploy_ok = deploy_ok
        self.detail = detail or {"id":"rq1","batch_id":"revert-rq1","actor":"slot:john",
            "source_ref":"d","original_text":"after","new_text":"before",
            "original_hash":"after-hash","new_hash":"before-hash",
            "base_sha":"base","commit_sha":"sha123","generator_id":"generator-v1"}
        self.calls = []
        self.resolved = []
        self.heartbeats = []
        self.notified = []
        self.deploy_refusals = []

    def fetch_reverts(self):
        self.calls.append("fetch_reverts")
        return self.reqs

    def revert_exec(self, req):
        self.calls.append(("revert_exec", req["id"]))
        return self.exec_ok, self.detail

    def deploy(self, branch):
        self.calls.append(("deploy", branch))
        return self.deploy_ok, "deploy-tail"

    def deploy_worker(self):
        self.calls.append("deploy_worker")
        return self.deploy_ok, "wrangler-tail"

    def rebuild(self):
        self.calls.append("rebuild")
        return True, ""

    def revert_resolve(self, rid, status):
        self.calls.append(("resolve", rid, status))
        self.resolved.append((rid, status))

    def revert_record(self, evidence, action):
        self.calls.append((action, evidence["id"], evidence["commit_sha"]))
        return {"sent": True, "ok": True}

    def heartbeat(self, ok, applied):
        self.calls.append(("heartbeat", ok))
        self.heartbeats.append({"ok": ok, "applied": applied})
        return {"sent": True}

    def notify(self, ids):
        self.calls.append("notify")
        self.notified.append(list(ids))

    def notify_deploy_refusal(self, status):
        self.calls.append("notify_deploy_refusal")
        self.deploy_refusals.append(status)


def _run_reverts(rec, **kw):
    with tempfile.TemporaryDirectory() as td:
        sp = os.path.join(td, "state.json")
        defaults = dict(
            api_base="https://x/edit/v1", token="tok", branch="feat/canonical-docs",
            now=NOW, state_path=sp, out=io.StringIO(),
            fetch=_empty_review,                       # no accepted suggestions
            do_rebuild=rec.rebuild, do_deploy=rec.deploy,
            heartbeat=rec.heartbeat, notify=rec.notify,
            deploy_guard=lambda: dad.DeployCheckoutStatus(0),
            deploy_refusal_notify=rec.notify_deploy_refusal,
            fetch_reverts=rec.fetch_reverts, revert_exec=rec.revert_exec,
            revert_resolve=rec.revert_resolve, do_deploy_worker=rec.deploy_worker,
            revert_record=rec.revert_record,
        )
        defaults.update(kw)
        return dad.run(**defaults)


class TestRevertOrchestration(unittest.TestCase):
    def test_approved_revert_executes_deploys_resolves_done(self):
        rec = RevertRecorder([{"id": "rq1", "doc": "d", "run_first": "aa", "run_last": "bb"}])
        res = _run_reverts(rec)
        self.assertEqual(res.reason, "no_accepted")  # no suggestions this tick
        self.assertIn(("revert_exec", "rq1"), rec.calls)
        self.assertIn("rebuild", rec.calls)   # map regenerates BEFORE any deploy
        self.assertIn(("deploy", "feat/canonical-docs"), rec.calls)
        # The WORKER must redeploy too: a revert changes the editable-block map
        # (a restored block re-enters the allowlist), and a stale worker bundle
        # rejects edits against it — caught live on 2026-07-28 (U4 cycle).
        self.assertIn("deploy_worker", rec.calls)
        self.assertIn(("record", "rq1", "sha123"), rec.calls)
        self.assertIn(("complete", "rq1", "sha123"), rec.calls)
        self.assertEqual(rec.heartbeats[0], {"ok": True, "applied": 0})
        self.assertEqual(rec.notified, [])

    def test_recorded_merged_revert_resumes_deploy_without_second_git_revert(self):
        evidence = _merged_revert_evidence()
        rec = RevertRecorder([evidence])
        _run_reverts(rec)
        self.assertNotIn(("revert_exec", "rq1"), rec.calls)
        self.assertIn(("record", "rq1", "sha123"), rec.calls)
        self.assertIn(("complete", "rq1", "sha123"), rec.calls)

    def test_guard_refusal_keeps_merged_revert_retryable(self):
        evidence = _merged_revert_evidence()
        behind = dad.DeployCheckoutStatus(7)
        refused = RevertRecorder([evidence])

        first = _run_reverts(refused, deploy_guard=lambda: behind)

        self.assertEqual(first.reason, "deploy_refused")
        self.assertNotIn(("revert_exec", "rq1"), refused.calls)
        self.assertFalse(any(call[0] in {"record", "complete", "resolve"}
                             for call in refused.calls if isinstance(call, tuple)))
        self.assertFalse(any(isinstance(call, tuple) and call[0] == "deploy"
                             for call in refused.calls))
        self.assertNotIn("deploy_worker", refused.calls)
        self.assertEqual(refused.deploy_refusals, [behind])

        resumed = RevertRecorder([evidence])
        second = _run_reverts(resumed)

        self.assertEqual(second.reason, "no_accepted")
        self.assertNotIn(("revert_exec", "rq1"), resumed.calls)
        self.assertIn(("record", "rq1", "sha123"), resumed.calls)
        self.assertIn(("complete", "rq1", "sha123"), resumed.calls)

    def test_revert_exec_failure_resolves_failed_and_alerts_ids_only(self):
        rec = RevertRecorder([{"id": "rq2", "doc": "d", "run_first": "aa", "run_last": "bb"}],
                             exec_ok=False, detail="revert_conflict")
        _run_reverts(rec)
        self.assertIn(("rq2", "failed"), rec.resolved)
        self.assertEqual(rec.heartbeats[0], {"ok": False, "applied": 0})
        self.assertEqual(rec.notified, [["rq2"]])
        # A failed revert never triggers the deploy step.
        self.assertNotIn(("deploy", "feat/canonical-docs"), rec.calls)
        self.assertNotIn("deploy_worker", rec.calls)

    def test_deploy_failure_keeps_journaled_revert_retryable_and_alerts(self):
        rec = RevertRecorder([{"id": "rq3", "doc": "d", "run_first": "aa", "run_last": "bb"}],
                             deploy_ok=False)
        _run_reverts(rec)
        self.assertIn(("deploy", "feat/canonical-docs"), rec.calls)
        self.assertNotIn(("rq3", "failed"), rec.resolved)
        self.assertEqual(rec.heartbeats[0], {"ok": False, "applied": 0})
        self.assertEqual(rec.notified, [["rq3"]])

    def test_no_reverts_no_revert_side_effects(self):
        rec = RevertRecorder([])
        _run_reverts(rec)
        self.assertEqual(rec.resolved, [])
        self.assertEqual(rec.notified, [])
        # No revert executed/deployed; the only heartbeat is the normal no-accepted
        # tick's ok:true (fetch_reverts ran, found nothing).
        self.assertNotIn(("revert_exec", "rq1"), rec.calls)
        self.assertFalse(any(isinstance(c, tuple) and c[0] == "deploy" for c in rec.calls))


# --------------------------------------------------------------------------- #
# execute_revert — real git repo (the authoritative revert logic)
# --------------------------------------------------------------------------- #
def _git(td, *args):
    return subprocess.run(["git", *args], cwd=td, check=True, capture_output=True, text=True)


def _init_repo(td):
    _git(td, "init", "-q")
    _git(td, "config", "user.email", "t@t")
    _git(td, "config", "user.name", "t")
    os.makedirs(os.path.join(td, "data"))


def _write_commit(td, path, content, msg):
    with open(os.path.join(td, path), "w") as f:
        f.write(content)
    _git(td, "add", "-A")
    _git(td, "commit", "-q", "-m", msg)
    return _git(td, "rev-parse", "HEAD").stdout.strip()


class TestExecuteRevertGit(unittest.TestCase):
    def test_clean_run_range_reverts_to_prior_content(self):
        with tempfile.TemporaryDirectory() as td:
            _init_repo(td)
            _write_commit(td, "data/foo.txt", "v0\n", "base")
            first = _write_commit(td, "data/foo.txt", "v1\n", "apply: batch b1")
            last = _write_commit(td, "data/foo.txt", "v2\n", "apply: batch b2")
            req = {"id": "rq", "doc": "data/foo.txt", "run_first": first, "run_last": last}
            ok, detail = dad.execute_revert(
                req, repo_root=td, do_rebuild=lambda: (True, ""), do_history=lambda: (True, ""),
                generator=lambda _root: "generator-test")
            self.assertTrue(ok, detail)
            self.assertEqual(detail["original_text"], "v2\n")
            self.assertEqual(detail["new_text"], "v0\n")
            self.assertEqual(detail["commit_sha"], _git(td, "rev-parse", "HEAD").stdout.strip())
            self.assertTrue(detail["generator_id"])
            retry_ok, retry_detail = dad.execute_revert(
                req, repo_root=td, do_rebuild=lambda: (True, ""), do_history=lambda: (True, ""),
                generator=lambda _root: "generator-test")
            self.assertTrue(retry_ok)
            self.assertEqual(retry_detail, detail)
            self.assertEqual(_git(td, "rev-list", "--count", "HEAD").stdout.strip(), "4")
            with open(os.path.join(td, "data/foo.txt")) as f:
                self.assertEqual(f.read(), "v0\n")
            # A real revert commit landed (build_history classifies it as kind=revert).
            subj = _git(td, "log", "-1", "--pretty=%s").stdout.strip()
            self.assertTrue(subj.startswith("revert(history):"))

    def test_overlapping_edit_aborts_never_partial(self):
        with tempfile.TemporaryDirectory() as td:
            _init_repo(td)
            _write_commit(td, "data/foo.txt", "line\n", "base")
            first = _write_commit(td, "data/foo.txt", "line-A\n", "apply: batch b1")
            # A later edit to the SAME line, NOT in the revert range -> conflict.
            _write_commit(td, "data/foo.txt", "line-B\n", "apply: batch b2")
            head_before = _git(td, "rev-parse", "HEAD").stdout.strip()
            req = {"id": "rq", "doc": "data/foo.txt", "run_first": first, "run_last": first}
            ok, detail = dad.execute_revert(
                req, repo_root=td, do_rebuild=lambda: (True, ""), do_history=lambda: (True, ""))
            self.assertFalse(ok)
            self.assertEqual(detail, "revert_conflict")
            # Tree pristine: HEAD unmoved, working tree clean, content untouched.
            self.assertEqual(_git(td, "rev-parse", "HEAD").stdout.strip(), head_before)
            self.assertEqual(_git(td, "status", "--porcelain").stdout.strip(), "")
            with open(os.path.join(td, "data/foo.txt")) as f:
                self.assertEqual(f.read(), "line-B\n")

    def test_multibyte_evidence_over_byte_limit_aborts_before_commit(self):
        with tempfile.TemporaryDirectory() as td:
            _init_repo(td)
            _write_commit(td, "data/foo.txt", "é" * 70000, "base")
            first = _write_commit(td, "data/foo.txt", "short\n", "apply: batch b1")
            head_before = _git(td, "rev-parse", "HEAD").stdout.strip()
            req = {"id":"rq-multibyte","doc":"data/foo.txt",
                   "run_first":first,"run_last":first}
            ok, detail = dad.execute_revert(
                req,repo_root=td,do_rebuild=lambda: (True,""),
                do_history=lambda: (True,""),generator=lambda _root:"generator-test")
            self.assertFalse(ok)
            self.assertEqual(detail,"revert_evidence_unavailable")
            self.assertEqual(_git(td,"rev-parse","HEAD").stdout.strip(),head_before)
            self.assertEqual(_git(td,"status","--porcelain").stdout.strip(),"")

    def test_dirty_tree_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            _init_repo(td)
            first = _write_commit(td, "data/foo.txt", "v1\n", "apply: batch b1")
            with open(os.path.join(td, "data/foo.txt"), "w") as f:
                f.write("uncommitted\n")
            req = {"id": "rq", "doc": "data/foo.txt", "run_first": first, "run_last": first}
            ok, detail = dad.execute_revert(
                req, repo_root=td, do_rebuild=lambda: (True, ""), do_history=lambda: (True, ""))
            self.assertFalse(ok)
            self.assertEqual(detail, "tree_not_clean")


if __name__ == "__main__":
    unittest.main()
