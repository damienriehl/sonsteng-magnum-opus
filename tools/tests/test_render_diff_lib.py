#!/usr/bin/env python3
"""Tests for tools/render_diff_lib.py — the ported word-level HTML diff engine.

Run:  python3 -m pytest tools/tests/test_render_diff_lib.py -q
  or: python3 tools/tests/test_render_diff_lib.py
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import render_diff_lib as rd  # noqa: E402


class TestTokenize(unittest.TestCase):
    def test_words_and_whitespace_alternate(self):
        self.assertEqual(rd.toks("a b"), ["a", " ", "b"])

    def test_roundtrip_preserves_text(self):
        s = "The quick  brown\nfox\t jumps."
        self.assertEqual("".join(rd.toks(s)), s)


class TestDiffHtml(unittest.TestCase):
    def test_identical_no_changes(self):
        r = rd.diff_html("hello world", "hello world")
        self.assertEqual(r.n_ins, 0)
        self.assertEqual(r.n_del, 0)
        self.assertNotIn("<ins>", r.html)
        self.assertNotIn("<del>", r.html)

    def test_pure_insertion(self):
        r = rd.diff_html("hello world", "hello brave world")
        self.assertEqual(r.n_ins, 1)
        self.assertEqual(r.n_del, 0)
        self.assertIn("<ins>", r.html)
        self.assertIn("brave", r.html)

    def test_pure_deletion(self):
        r = rd.diff_html("hello brave world", "hello world")
        self.assertEqual(r.n_del, 1)
        self.assertEqual(r.n_ins, 0)
        self.assertIn("<del>", r.html)

    def test_replace_counts_both_regions(self):
        r = rd.diff_html("the cat sat", "the dog sat")
        self.assertEqual(r.n_ins, 1)
        self.assertEqual(r.n_del, 1)
        self.assertIn("<del>cat</del>", r.html)
        self.assertIn("<ins>dog</ins>", r.html)

    def test_from_empty_is_all_insertion(self):
        r = rd.diff_html("", "brand new document text")
        self.assertEqual(r.n_del, 0)
        self.assertGreaterEqual(r.n_ins, 1)
        self.assertIn("<ins>", r.html)

    def test_html_is_escaped(self):
        # A literal <script> in the NEW text must be entity-escaped, never raw.
        r = rd.diff_html("safe", "<script>alert(1)</script>")
        self.assertNotIn("<script>", r.html)
        self.assertIn("&lt;script&gt;", r.html)

    def test_deletion_content_escaped(self):
        r = rd.diff_html("<b>bold</b>", "plain")
        self.assertNotIn("<b>", r.html.replace("<del>", "").replace("</del>", ""))
        self.assertIn("&lt;b&gt;", r.html)

    def test_long_equal_run_collapses(self):
        filler = " ".join(f"w{i}" for i in range(300))
        old = f"START {filler} END"
        new = f"CHANGED {filler} END"
        r = rd.diff_html(old, new)
        self.assertIn("<details>", r.html)
        self.assertIn("unchanged words", r.html)

    def test_short_equal_run_not_collapsed(self):
        r = rd.diff_html("alpha beta gamma", "ALPHA beta gamma")
        self.assertNotIn("<details>", r.html)


class TestDiffPage(unittest.TestCase):
    def test_page_is_standalone_html(self):
        page = rd.diff_page("old text", "new text", title="My Redline")
        self.assertTrue(page.lstrip().startswith("<!doctype html>"))
        self.assertIn("My Redline", page)
        self.assertIn("<pre>", page)

    def test_page_title_escaped(self):
        page = rd.diff_page("a", "b", title="<x>")
        self.assertIn("&lt;x&gt;", page)
        self.assertNotIn("<title><x>", page)


if __name__ == "__main__":
    unittest.main()
