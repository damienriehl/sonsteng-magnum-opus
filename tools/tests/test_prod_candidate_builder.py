#!/usr/bin/env python3
"""Focused tests for the prepare-only PROD candidate boundary."""

import hashlib
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_suggestions as ap  # noqa: E402
import direct_apply_daemon as dad  # noqa: E402
import test_apply_suggestions as fx  # noqa: E402


def _row(root, sid="s1", replacement="Revised intake notes for the tort matter."):
    ref = fx.bref(root, fx.M03_MD, 0)
    block = fx.resolve_index(root, fx.SPEC)[ref]
    return {"id": sid, "source_ref": ref, "new_text": replacement,
            "original_hash": block["original_hash"],
            "original_text": block["original_text"], "kind": "prose",
            "group_id": None, "status": "accepted"}


class CandidatePreparationTest(unittest.TestCase):
    def setUp(self):
        self.root = fx.make_repo()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_happy_path_creates_immutable_ref_and_does_not_mutate_checkout(self):
        before = fx.snapshot_data(self.root)
        result = ap.prepare_candidate([_row(self.root)], fx.FakePipeline(fx.SPEC),
                                      "cand-1", canonical_root=self.root,
                                      logger=lambda *_: None)
        self.assertTrue(result.promotable)
        self.assertEqual(before, fx.snapshot_data(self.root))
        self.assertEqual(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=self.root, text=True).strip(), "")
        resolved = subprocess.check_output(
            ["git", "rev-parse", result.candidate_ref], cwd=self.root, text=True).strip()
        self.assertEqual(resolved, result.commit_sha)
        self.assertEqual(result.evidence_hash, hashlib.sha256(
            ap.json.dumps(result.evidence, sort_keys=True,
                          separators=(",", ":")).encode()).hexdigest())
        self.assertEqual([g["status"] for g in result.evidence["hard_gates"]],
                         ["pass"] * len(result.evidence["hard_gates"]))

    def test_validation_failure_has_named_gate_and_no_ref(self):
        result = ap.prepare_candidate([_row(self.root)],
                                      fx.FakePipeline(fx.SPEC, validate_ok=False),
                                      "cand-red", canonical_root=self.root,
                                      logger=lambda *_: None)
        self.assertFalse(result.promotable)
        self.assertEqual(result.reason, "validator_red")
        self.assertIn({"name": "validate_spine", "status": "fail",
                       "detail": {"report": {"errors": ["x"]}}},
                      result.evidence["hard_gates"])
        proc = subprocess.run(["git", "show-ref", "--verify", "--quiet",
                               "refs/sonsteng/candidates/cand-red"], cwd=self.root)
        self.assertNotEqual(proc.returncode, 0)

    def test_candidate_ref_is_compare_and_set_not_overwritten(self):
        first = ap.prepare_candidate([_row(self.root)], fx.FakePipeline(fx.SPEC),
                                     "same", canonical_root=self.root,
                                     logger=lambda *_: None)
        second = ap.prepare_candidate([_row(self.root, replacement="Another revision.")],
                                      fx.FakePipeline(fx.SPEC), "same",
                                      canonical_root=self.root, logger=lambda *_: None)
        self.assertTrue(first.promotable)
        self.assertFalse(second.promotable)
        self.assertEqual(second.reason, "candidate_ref_exists")
        resolved = subprocess.check_output(
            ["git", "rev-parse", first.candidate_ref], cwd=self.root, text=True).strip()
        self.assertEqual(resolved, first.commit_sha)

    def test_revert_is_prepared_without_moving_head(self):
        path = fx.M03_MD
        with open(os.path.join(self.root, path), "w", encoding="utf-8") as fh:
            fh.write("changed\n")
        fx._git(["add", "-A"], self.root)
        fx._git(["commit", "-q", "-m", "applied change"], self.root)
        changed = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                          cwd=self.root, text=True).strip()
        result = ap.prepare_revert_candidate(
            "revert-1", changed, changed, fx.FakePipeline(fx.SPEC),
            canonical_root=self.root, logger=lambda *_: None)
        self.assertTrue(result.promotable)
        self.assertEqual(subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip(), changed)
        restored = subprocess.check_output(
            ["git", "show", "%s:%s" % (result.commit_sha, path)],
            cwd=self.root, text=True)
        self.assertIn("Intake notes", restored)

    def test_sandbox_drops_credentials_and_disables_network(self):
        fake = mock.Mock(returncode=0, stdout="ok")
        pipeline = ap.SandboxedSubprocessPipeline(bubblewrap="/usr/bin/bwrap")
        with mock.patch.object(ap.subprocess, "run", return_value=fake) as run:
            rc, _ = pipeline._run(["python3", "tool.py"], self.root)
        self.assertEqual(rc, 0)
        command = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertIn("--unshare-net", command)
        self.assertNotIn(["--ro-bind", "/", "/"],
                         [command[i:i + 3] for i in range(len(command) - 2)])
        self.assertNotIn(os.path.expanduser("~"), command)
        binds = [(command[i], command[i + 1], command[i + 2])
                 for i in range(len(command) - 2)
                 if command[i] in ("--bind", "--ro-bind")]
        writable = [(src, dst) for mode, src, dst in binds if mode == "--bind"]
        self.assertEqual(writable, [(os.path.realpath(self.root), "/workspace")])
        self.assertIn(("--tmpfs", "/"), list(zip(command, command[1:])))
        self.assertNotIn("EDIT_SERVICE_TOKEN", env)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", env)
        self.assertEqual(set(env), set(ap.SandboxedSubprocessPipeline.SAFE_ENV))

    def test_dev_branch_guard_rejects_release_branches(self):
        for branch in ("main", "master", "prod", "production", ""):
            with self.subTest(branch=branch), self.assertRaises(dad.DaemonError):
                dad.validate_dev_branch(branch)
        self.assertEqual(dad.validate_dev_branch("dev-direct-apply"),
                         "dev-direct-apply")

    def test_candidate_refs_do_not_collapse_sanitized_or_unicode_ids(self):
        refs = {ap.candidate_ref_for(value) for value in (
            "!!!", "???", "a/b", "a?b", "候補", "候補!", "",
        )}
        self.assertEqual(len(refs), 7)
        for ref in refs:
            self.assertRegex(ref, r"^refs/heads/releases/[A-Za-z0-9._-]+-[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
