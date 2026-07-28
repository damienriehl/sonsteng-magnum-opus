#!/usr/bin/env python3
"""Tests for the scope index (U6 of the word-like-editing plan): the editor map
bundle carries a deterministic `scopes` section resolving the four-level ladder
part -> matter -> module -> course, so a scoped change's blast radius is
enumerable BEFORE any model runs (R4, KD2).

Run:  python3 -m pytest tools/tests/test_scope_index.py -q
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import build_site as bs  # noqa: E402


def load_corpus_once():
    if not hasattr(load_corpus_once, "_c"):
        load_corpus_once._c = bs.load_corpus()
    return load_corpus_once._c


class TestScopeIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_corpus_once()
        cls.scopes = bs.compute_scope_index(cls.corpus)

    def test_every_matter_is_indexed_with_its_parts(self):
        matters = self.scopes["matters"]
        self.assertEqual(len(matters), 20)
        for slug, meta in matters.items():
            self.assertRegex(meta["id"], r"^m\d{2}$")
            self.assertTrue(set(meta["parts"]) <= {"case-file", "exercise",
                                                   "business", "matter"},
                            "%s: %r" % (slug, meta["parts"]))
            self.assertIn("case-file", meta["parts"])

    def test_modules_map_to_curriculum_and_matters(self):
        mods = self.scopes["modules"]
        self.assertEqual(set(mods), {"M1", "M2", "M3"})
        for code, meta in mods.items():
            self.assertEqual(meta["curriculum"],
                             "data/curriculum/%s.md" % code.lower())
            # a module cuts across matters — several, and only real slugs
            self.assertGreater(len(meta["matters"]), 1)
            self.assertTrue(set(meta["matters"]) <= set(self.scopes["matters"]))

    def test_module_membership_is_task_derived_and_deterministic(self):
        again = bs.compute_scope_index(self.corpus)
        self.assertEqual(self.scopes, again)
        # m03's rubric references M1+M2 tasks (verified by hand in planning):
        self.assertIn("m03-tort-meridian", self.scopes["modules"]["M2"]["matters"])

    def test_index_is_embedded_in_the_committed_map(self):
        m = json.load(open(os.path.join(REPO, "build",
                                        "editor-map.generated.json")))
        self.assertIn("scopes", m)
        self.assertEqual(set(m["scopes"]), {"matters", "modules"})
        # counts reconcile: every mapped matter file belongs to exactly one
        # indexed matter, and each carries a known part
        for entries in m["pages"].values():
            for b in entries:
                f = b["source_ref"].split("#", 1)[0]
                mm = re.match(r"data/matters/([^/]+)/(?:([^/]+)/)?", f)
                if not mm:
                    continue
                slug, part = mm.group(1), mm.group(2) or "matter"
                self.assertIn(slug, m["scopes"]["matters"], f)
                part = "matter" if f.endswith("/matter.json") else part
                self.assertIn(part, m["scopes"]["matters"][slug]["parts"],
                              "%s not indexed for %s" % (part, slug))


if __name__ == "__main__":
    unittest.main(verbosity=2)
