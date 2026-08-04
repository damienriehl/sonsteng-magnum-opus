#!/usr/bin/env python3
r"""U5b of the word-like-editing plan — the per-matter Law page (R11).

Damien's settled decision (2026-07-28): each matter gets a Law page rendering
its governing law. Fictional Meridian law is EDITABLE there; real-jurisdiction
law (New York, California, ...) is NEVER editable — rendered read-only and
visibly marked as actual law. The rule is mechanical, by path: refs under
data/jurisdictions/meridian.json register as editable blocks; anything under
data/jurisdictions/real/ renders WITHOUT data-ebsrc and never enters the map,
so the Worker refuses a forged ref by allowlist absence (no oracle).

These tests pin:
  * every matter has a law page registered in the map's page allowlist;
  * a Meridian-tier matter's law page carries editable blocks, every one
    sourced under data/jurisdictions/meridian, value-only (hash == value);
  * a real-jurisdiction matter's law page has ZERO map blocks, carries the
    visible "not editable" marking, and no data-ebsrc anywhere;
  * corpus-wide, NO source_ref in the whole map starts with
    data/jurisdictions/real (the R11 map-test requirement).

Run:  python3 -m pytest tools/tests/test_law_pages.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import build_site as bs   # noqa: E402
import spine_stamp        # noqa: E402
import text_norm          # noqa: E402

MERIDIAN_SLUG = "m03-tort-meridian"
REAL_SLUG = "m16-noncompete-ny"

_FRESH = {}


def _build_fresh():
    """Full in-process site build into a tempdir (mirrors build_site.main()'s
    build phase), returning the map bundle plus one meridian and one real
    law-page HTML. Cached per test run; repo site/ and build/ untouched."""
    if "bundle" in _FRESH:
        return _FRESH
    tmp = tempfile.mkdtemp(prefix="law-")
    saved = {k: getattr(bs, k) for k in
             ("OUT", "BUILD_DIR", "EDITOR_MAP_PATH", "SPINE_BUILD_ID")}
    bs.OUT = os.path.join(tmp, "site", "platform")
    bs.BUILD_DIR = os.path.join(tmp, "build")
    bs.EDITOR_MAP_PATH = os.path.join(bs.BUILD_DIR, "editor-map.generated.json")
    try:
        bs.SPINE_BUILD_ID = spine_stamp.compute(bs.DATA)
        bs.EDMAP.reset()
        bs.EDMAP.enabled = True
        corpus = bs.load_corpus()
        bs.SKILLS_BY_ID.update({s["id"]: s for s in corpus["skills"]["skills"]})
        bs.TASKS_BY_ID.update({t["id"]: t for t in corpus["tasks"]["tasks"]})
        bs.clean_output()
        bs.write_platform_assets()
        bs.copy_chat_app()
        bs.build_home(corpus)
        bs.build_modules(corpus)
        bs.build_templates(corpus)
        bs.build_skills(corpus)
        bs.build_matter_library(corpus)
        bs.build_packet_pages(corpus)
        bs.build_facts_pages(corpus)
        bs.build_law_pages(corpus)
        bs.build_firm_dashboard(corpus)
        bs.build_third_party()
        bs.build_data_catalog(corpus)
        bs.write_build_stamp(bs.SPINE_BUILD_ID)
        bs.build_editor_map(bs.SPINE_BUILD_ID, bs.compute_scope_index(corpus))
        with open(bs.EDITOR_MAP_PATH, encoding="utf-8") as fh:
            bundle = json.load(fh)
        mer_html = open(os.path.join(
            bs.OUT, "matters", MERIDIAN_SLUG, "law", "index.html"),
            encoding="utf-8").read()
        real_html = open(os.path.join(
            bs.OUT, "matters", REAL_SLUG, "law", "index.html"),
            encoding="utf-8").read()
        packet_html = open(os.path.join(
            bs.OUT, "matters", MERIDIAN_SLUG, "index.html"),
            encoding="utf-8").read()
        slugs = [m["_slug"] for m in corpus["matters"]]
        tiers = {m["_slug"]: m.get("tier") for m in corpus["matters"]}
    finally:
        bs.EDMAP.reset()
        for k, v in saved.items():
            setattr(bs, k, v)
        shutil.rmtree(tmp, ignore_errors=True)
    _FRESH.update({"bundle": bundle, "mer_html": mer_html,
                   "real_html": real_html, "packet_html": packet_html,
                   "slugs": slugs, "tiers": tiers})
    return _FRESH


class LawPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f = _build_fresh()
        cls.bundle = f["bundle"]
        cls.pages = f["bundle"]["pages"]
        cls.mer_html = f["mer_html"]
        cls.real_html = f["real_html"]
        cls.packet_html = f["packet_html"]
        cls.slugs = f["slugs"]
        cls.tiers = f["tiers"]

    # ---- every matter gets a Law page in the page allowlist -------------- #
    def test_every_matter_has_a_law_page_in_the_map(self):
        self.assertEqual(len(self.slugs), 20)
        for slug in self.slugs:
            with self.subTest(slug=slug):
                self.assertIn("matters/%s/law/index.html" % slug, self.pages)

    # ---- Meridian law is editable, sourced ONLY from the fictional canon - #
    def test_meridian_law_page_carries_editable_meridian_blocks(self):
        blocks = self.pages["matters/%s/law/index.html" % MERIDIAN_SLUG]
        self.assertGreater(len(blocks), 0,
                           "the fictional canon must be editable on the Law page")
        for b in blocks:
            with self.subTest(ref=b["source_ref"]):
                self.assertTrue(
                    b["source_ref"].startswith("data/jurisdictions/meridian"),
                    "every Law-page block must come from the Meridian canon")
                self.assertEqual(b["kind"], "json_scalar")
                # value-only element: rendered text IS the authored value
                self.assertEqual(b["original_hash"],
                                 text_norm.norm_hash(b["original_text"]))

    def test_every_meridian_matter_law_page_is_editable(self):
        for slug, tier in self.tiers.items():
            if tier != "meridian":
                continue
            with self.subTest(slug=slug):
                blocks = self.pages["matters/%s/law/index.html" % slug]
                self.assertGreater(len(blocks), 0)

    # ---- real law: zero blocks, visible marking, no anchors -------------- #
    def test_real_law_page_has_zero_blocks(self):
        for slug, tier in self.tiers.items():
            if tier == "meridian":
                continue
            with self.subTest(slug=slug):
                self.assertEqual(
                    self.pages["matters/%s/law/index.html" % slug], [],
                    "real-jurisdiction law must never enter the map")

    def test_real_law_page_is_marked_and_carries_no_anchor(self):
        self.assertIn("Actual New York law — not editable here.",
                      self.real_html)
        self.assertNotIn("data-ebsrc", self.real_html)

    def test_meridian_law_page_is_marked_fictional(self):
        self.assertIn("Fictional Meridian law", self.mer_html)

    # ---- R11 corpus-wide: the real/ tree NEVER appears in the map -------- #
    def test_no_source_ref_anywhere_under_real_jurisdictions(self):
        for page, blocks in self.pages.items():
            for b in blocks:
                with self.subTest(page=page, ref=b["source_ref"]):
                    self.assertFalse(
                        b["source_ref"].startswith("data/jurisdictions/real"),
                        "a real-jurisdiction ref reached the allowlist")

    # ---- the packet TOC links the Law page like the Facts page ----------- #
    def test_matter_toc_links_the_law_page(self):
        self.assertIn('href="law/"', self.packet_html)
        self.assertIn("toc-law", self.packet_html)


if __name__ == "__main__":
    unittest.main()
