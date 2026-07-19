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
import os
import sys
import tempfile
import unittest

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

    def editorial(self, batch_id):
        self.calls.append("editorial")
        self.editorial_batch = batch_id


def _run(rec, **kw):
    defaults = dict(
        api_base="https://x/edit/v1", token="tok", branch="feat/canonical-docs",
        now=NOW, fetch=rec.fetch, apply_engine=rec.apply_engine,
        do_rebuild=rec.rebuild, do_deploy=rec.deploy, heartbeat=rec.heartbeat,
        notify=rec.notify, editorial=rec.editorial, out=io.StringIO(),
    )
    defaults.update(kw)
    return dad.run(**defaults)


class TestAcceptedFilter(unittest.TestCase):
    def test_only_accepted(self):
        rows = [row("aaaaaaaa", "accepted"), row("bbbbbbbb", "pending"),
                row("cccccccc", "in_flight"), row("dddddddd", "applied"),
                row("eeeeeeee", "accepted")]
        self.assertEqual(dad.accepted_ids(rows), ["aaaaaaaa", "eeeeeeee"])

    def test_skips_rows_without_id(self):
        self.assertEqual(dad.accepted_ids([{"status": "accepted"}]), [])


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


if __name__ == "__main__":
    unittest.main()
