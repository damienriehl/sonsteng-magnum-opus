#!/usr/bin/env python3
"""Tests for tools/build_history.py — coalescing rule, redline correctness,
baseline diffs, the any-vs-any compare cap, and the public-site leak assertion.

Uses throwaway git repos with controlled author timestamps so the 10-minute
rolling window and the kind boundaries are exercised deterministically.

Run:  python3 -m pytest tools/tests/test_build_history.py -q
  or: python3 tools/tests/test_build_history.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import build_history as bh  # noqa: E402

REL = "data/firm/firm.json"  # any tracked-looking relpath; content is arbitrary here
BASE_TS = 1_700_000_000  # fixed epoch base for reproducible windows


class GitFixture:
    """A disposable git repo with a single tracked file and scripted commits."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="hx-test-")
        self._run("git", "init", "-q")
        self._run("git", "config", "user.email", "seed@example.com")
        self._run("git", "config", "user.name", "seed")
        self.path = os.path.join(self.dir, REL)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _run(self, *args, env_extra=None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(args, cwd=self.dir, capture_output=True, text=True,
                              check=True, env=env)

    def commit(self, content, subject, *, author="damienriehl",
               author_email="damienriehl@gmail.com", committer=None,
               committer_email=None, ts=BASE_TS, body=""):
        committer = committer or author
        committer_email = committer_email or author_email
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(content)
        self._run("git", "add", REL)
        iso = f"{ts} +0000"
        msg = subject + (("\n\n" + body) if body else "")
        self._run(
            "git",
            "-c", f"user.name={author}", "-c", f"user.email={author_email}",
            "commit", "-q", "-m", msg,
            env_extra={
                "GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso,
                "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": committer, "GIT_COMMITTER_EMAIL": committer_email,
            },
        )

    def apply_commit(self, content, batch, *, ts, body=""):
        """An apply-engine edit commit (matches apply_suggestions.py step 11)."""
        self.commit(content, f"apply: batch {batch} (1 suggestions)",
                    author="apply-engine", author_email=bh.APPLY_ENGINE_EMAIL,
                    committer="apply-engine", committer_email=bh.APPLY_ENGINE_EMAIL,
                    ts=ts, body=body)

    def tag(self, name, message="baseline"):
        self._run("git", "tag", "-a", name, "-m", message)

    def repo(self):
        return bh.GitRepo(self.dir)

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class TestClassify(unittest.TestCase):
    def test_apply_engine_is_edit(self):
        kind, batch = bh._classify("apply: batch xyz (2 suggestions)",
                                   "apply-engine", bh.APPLY_ENGINE_EMAIL,
                                   bh.APPLY_ENGINE_EMAIL)
        self.assertEqual(kind, "edit")
        self.assertEqual(batch, "xyz")

    def test_human_commit_is_external(self):
        kind, _ = bh._classify("content(m11): deepen personas", "damienriehl",
                               "damienriehl@gmail.com", "damienriehl@gmail.com")
        self.assertEqual(kind, "external")

    def test_git_default_revert_is_revert(self):
        kind, _ = bh._classify('Revert "apply: batch e2e (1 suggestions)"',
                               "damienriehl", "d@x", "d@x")
        self.assertEqual(kind, "revert")

    def test_fence_style_revert_is_revert(self):
        kind, _ = bh._classify("revert(firm): revision abc..def",
                               "damienriehl", "d@x", "d@x")
        self.assertEqual(kind, "revert")

    def test_baseline_subject(self):
        kind, _ = bh._classify("baseline(walkthrough): cut", "d", "d@x", "d@x")
        self.assertEqual(kind, "baseline")


class TestAttribution(unittest.TestCase):
    def test_git_author_maps_to_initials(self):
        self.assertEqual(bh._attribution("damienriehl", "damienriehl@gmail.com", "s", ""), "DVR")

    def test_apply_engine_default(self):
        self.assertEqual(bh._attribution("apply-engine", bh.APPLY_ENGINE_EMAIL, "s", ""), "APPLY")

    def test_editor_trailer_wins(self):
        self.assertEqual(
            bh._attribution("apply-engine", bh.APPLY_ENGINE_EMAIL,
                            "apply: batch x (1 suggestions)", "Editor: JOS"),
            "JOS")

    def test_coauthored_trailer(self):
        self.assertEqual(
            bh._attribution("apply-engine", bh.APPLY_ENGINE_EMAIL, "apply: batch x",
                            "Co-authored-by: RSH"),
            "RSH")


class TestCoalescing(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_two_edits_within_window_coalesce(self):
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        self.fx.apply_commit("v2", "b2", ts=BASE_TS + 300)  # +5 min
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(len(dh.revisions), 1)
        self.assertEqual(dh.revisions[0]["n_commits"], 2)
        self.assertEqual(dh.revisions[0]["kind"], "edit")

    def test_exact_window_boundary_joins(self):
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        self.fx.apply_commit("v2", "b2", ts=BASE_TS + bh.COALESCE_WINDOW_SECS)  # exactly 600s
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(len(dh.revisions), 1)

    def test_beyond_window_splits(self):
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        self.fx.apply_commit("v2", "b2", ts=BASE_TS + bh.COALESCE_WINDOW_SECS + 1)  # 601s
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(len(dh.revisions), 2)

    def test_rolling_window_not_from_start(self):
        # 3 edits, each 400s apart: total span 800s > 600, but consecutive gaps
        # are all <= 600, so the rolling window keeps them as ONE revision.
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        self.fx.apply_commit("v2", "b2", ts=BASE_TS + 400)
        self.fx.apply_commit("v3", "b3", ts=BASE_TS + 800)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(len(dh.revisions), 1)
        self.assertEqual(dh.revisions[0]["n_commits"], 3)

    def test_different_author_breaks_run(self):
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        # a human commit interleaved is 'external' -> its own revision, never joined
        self.fx.commit("v2", "content: manual tweak", ts=BASE_TS + 60)
        self.fx.apply_commit("v3", "b3", ts=BASE_TS + 120)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        kinds = [r["kind"] for r in dh.revisions]  # newest-first
        self.assertEqual(kinds, ["edit", "external", "edit"])

    def test_external_never_coalesces(self):
        self.fx.commit("v1", "content: a", ts=BASE_TS)
        self.fx.commit("v2", "content: b", ts=BASE_TS + 60)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(len(dh.revisions), 2)
        self.assertTrue(all(r["kind"] == "external" for r in dh.revisions))

    def test_revert_is_its_own_revision(self):
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        self.fx.commit("", 'Revert "apply: batch b1 (1 suggestions)"', ts=BASE_TS + 100)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual([r["kind"] for r in dh.revisions], ["revert", "edit"])


class TestRedlineCorrectness(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_first_revision_from_empty(self):
        self.fx.apply_commit("hello world", "b1", ts=BASE_TS)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        rev = dh.revisions[0]
        self.assertEqual(rev["parent"], "EMPTY")
        d = dh.diffs[bh._diff_key("EMPTY", rev["tip"])]
        self.assertIn("<ins>", d["html"])
        self.assertGreaterEqual(d["n_ins"], 1)
        self.assertEqual(d["n_del"], 0)

    def test_revision_redline_shows_change(self):
        self.fx.apply_commit("the cat sat", "b1", ts=BASE_TS)
        self.fx.apply_commit("the dog sat", "b2", ts=BASE_TS + 10_000)  # new revision
        dh = bh.build_doc_history(self.fx.repo(), REL)
        newest = dh.revisions[0]  # dog
        d = dh.diffs[bh._diff_key(newest["parent"], newest["tip"])]
        self.assertIn("<del>cat</del>", d["html"])
        self.assertIn("<ins>dog</ins>", d["html"])


class TestBaselines(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_baseline_tag_surfaced_and_diffable(self):
        self.fx.apply_commit("original text", "b1", ts=BASE_TS)
        self.fx.tag("baseline-walkthrough-2026-07-23", "before walkthrough")
        self.fx.apply_commit("original text plus additions", "b2", ts=BASE_TS + 10_000)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        names = [b["name"] for b in dh.baselines]
        self.assertIn("baseline-walkthrough-2026-07-23", names)
        head_tip = dh.revisions[0]["tip"]
        key = bh._diff_key("baseline-walkthrough-2026-07-23", head_tip)
        self.assertIn(key, dh.diffs)
        self.assertIn("<ins>", dh.diffs[key]["html"])

    def test_baseline_marker_on_revision(self):
        self.fx.apply_commit("v1", "b1", ts=BASE_TS)
        self.fx.tag("baseline-x")
        dh = bh.build_doc_history(self.fx.repo(), REL)
        # the tagged commit's revision carries the baseline name
        self.assertIn("baseline-x", dh.revisions[0]["baselines"])


class TestCompareCap(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()

    def tearDown(self):
        self.fx.cleanup()

    def test_anyvsany_capped_to_last_20(self):
        n = 22
        for i in range(n):
            # each >10 min apart -> its own revision
            self.fx.apply_commit(f"content revision {i}", f"b{i}",
                                 ts=BASE_TS + i * 1000)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(len(dh.revisions), n)
        # dropped = C(22,2) - C(20,2) = 231 - 190 = 41
        self.assertEqual(dh.dropped_pairs, 41)

        # Verify no any-vs-any pair exists between two of the OLDEST two tips
        # (revisions 0 and 1), which fall outside the last-20 window.
        tips_chrono = [r["tip"] for r in reversed(dh.revisions)]  # oldest->newest
        oldest_pair = bh._diff_key(tips_chrono[0], tips_chrono[1])
        cat = dh.diffs.get(oldest_pair, {}).get("category")
        # It may exist as an 'adjacent' pair, but must NOT be an 'anyvsany' pair.
        self.assertNotEqual(cat, "anyvsany")

        # A pair within the last-20 window (two recent, non-adjacent tips) IS
        # precomputed as anyvsany.
        recent_a, recent_b = tips_chrono[-1], tips_chrono[-3]
        self.assertIn(bh._diff_key(recent_b, recent_a), dh.diffs)

    def test_under_cap_no_drops(self):
        for i in range(5):
            self.fx.apply_commit(f"c{i}", f"b{i}", ts=BASE_TS + i * 1000)
        dh = bh.build_doc_history(self.fx.repo(), REL)
        self.assertEqual(dh.dropped_pairs, 0)


class TestLeakAssertion(unittest.TestCase):
    def test_sentinel_in_public_site_fails(self):
        d = tempfile.mkdtemp(prefix="hx-leak-")
        try:
            os.makedirs(os.path.join(d, "sub"))
            with open(os.path.join(d, "sub", "page.html"), "w") as f:
                f.write("<html>oops " + bh.HISTORY_SENTINEL + "</html>")
            violations = bh.assert_no_history_leak(d)
            self.assertTrue(violations)
            self.assertTrue(any("HISTORY_SENTINEL" in v for v in violations))
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_history_artifact_filename_in_site_fails(self):
        d = tempfile.mkdtemp(prefix="hx-leak-")
        try:
            with open(os.path.join(d, "history-bundle.generated.json"), "w") as f:
                f.write("{}")
            violations = bh.assert_no_history_leak(d)
            self.assertTrue(any("history artifact" in v for v in violations))
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_clean_site_passes(self):
        d = tempfile.mkdtemp(prefix="hx-leak-")
        try:
            with open(os.path.join(d, "index.html"), "w") as f:
                f.write("<html>clean student page</html>")
            self.assertEqual(bh.assert_no_history_leak(d), [])
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_site_is_vacuously_clean(self):
        self.assertEqual(bh.assert_no_history_leak("/nonexistent/path/xyz"), [])


if __name__ == "__main__":
    unittest.main()
