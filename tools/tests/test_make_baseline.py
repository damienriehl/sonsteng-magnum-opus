#!/usr/bin/env python3
"""Tests for tools/make_baseline.py — annotated baseline-* tag creation, name
validation, and refusal to move an existing baseline. No push, no history
rewrite.

Run:  python3 -m pytest tools/tests/test_make_baseline.py -q
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import make_baseline as mb  # noqa: E402


class BaselineFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hx-baseline-")
        self._run("git", "init", "-q")
        self._run("git", "config", "user.email", "a@b.c")
        self._run("git", "config", "user.name", "a")
        with open(os.path.join(self.dir, "f.txt"), "w") as f:
            f.write("hello")
        self._run("git", "add", "f.txt")
        self._run("git", "commit", "-qm", "c1")
        # Point make_baseline at our temp repo + disable history regen.
        self._orig_root = mb.ROOT
        mb.ROOT = self.dir

    def tearDown(self):
        mb.ROOT = self._orig_root
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(args, cwd=self.dir, capture_output=True, text=True, check=True)

    def _tags(self):
        return self._run("git", "tag", "-l").stdout.split()


class TestMakeBaseline(BaselineFixture):
    def test_creates_annotated_tag(self):
        rc = mb.make_baseline("walkthrough-2026-07-23", "before walkthrough",
                              "HEAD", regen=False)
        self.assertEqual(rc, 0)
        self.assertIn("baseline-walkthrough-2026-07-23", self._tags())
        # annotated (has a tag object)
        t = self._run("git", "cat-file", "-t",
                      "baseline-walkthrough-2026-07-23").stdout.strip()
        self.assertEqual(t, "tag")

    def test_rejects_bad_name(self):
        rc = mb.make_baseline("Bad Name!", "", "HEAD", regen=False)
        self.assertEqual(rc, 2)
        self.assertEqual(self._tags(), [])

    def test_refuses_to_move_existing(self):
        self.assertEqual(mb.make_baseline("x", "", "HEAD", regen=False), 0)
        rc = mb.make_baseline("x", "", "HEAD", regen=False)  # again
        self.assertEqual(rc, 2)

    def test_unknown_ref(self):
        rc = mb.make_baseline("y", "", "does-not-exist", regen=False)
        self.assertEqual(rc, 2)

    def test_list_runs(self):
        mb.make_baseline("z", "note", "HEAD", regen=False)
        self.assertEqual(mb.list_baselines(), 0)


if __name__ == "__main__":
    unittest.main()
