#!/usr/bin/env python3
r"""U3 of the word-like-editing plan — editable coverage for the missing trees.

`skills/index.html` and `firm/index.html` shipped with ZERO editable blocks:
the taxonomy (`data/taxonomy/`) and the firm profile (`data/firm/`) were never
registered in the editor map, so John opened the Skills browser and found
nothing editable. U3 registers the authored prose strings (task names, task
descriptions, the firm name and letterhead note) as `json_scalar` blocks
attached to elements that are ALREADY walker candidates — so no pre-existing
block's index can shift anywhere.

These tests pin the three load-bearing properties:

  * the two zero-block pages move off zero (R1);
  * every block in the COMMITTED map survives at the SAME page + index with the
    SAME source_ref — the fresh build is a superset, never a reshuffle (R4/R8);
  * no ID or crosswalk join key (skill/task/subtask IDs, FOLIO IRIs, firm
    ledger IDs) appears inside any new editable block's text, and each new
    block's candidate element renders EXACTLY its authored source string —
    nothing more (R2 / KTD3).

The fresh map is built in-process into a temp directory (the full site build
takes <1s), so these tests never dirty the repo's site/ or build/ trees and
never depend on the committed map being regenerated first.

Run:  python3 -m pytest tools/tests/test_editable_coverage.py -q
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

COMMITTED_MAP = os.path.join(REPO, "build", "editor-map.generated.json")

# The trees U3 brings into the map. A block whose source file lives under one
# of these is a "new-coverage" block and carries U3's extra guarantees.
NEW_TREES = ("data/taxonomy/", "data/firm/", "data/jurisdictions/")

_FRESH = {}   # module-level cache: one in-process site build for the whole file


def _build_fresh_map():
    """Run the full generator into a temp dir and return the map bundle.

    Mirrors build_site.main()'s build phase with OUT/BUILD_DIR redirected, so
    the repo's site/ and build/ trees stay untouched. Cached per test run."""
    if "bundle" in _FRESH:
        return _FRESH["bundle"]

    tmp = tempfile.mkdtemp(prefix="edcov-")
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
        bs.build_firm_dashboard(corpus)
        bs.build_third_party()
        bs.build_data_catalog(corpus)
        bs.write_build_stamp(bs.SPINE_BUILD_ID)
        bs.build_editor_map(bs.SPINE_BUILD_ID)
        with open(bs.EDITOR_MAP_PATH, encoding="utf-8") as fh:
            bundle = json.load(fh)
    finally:
        bs.EDMAP.reset()
        for k, v in saved.items():
            setattr(bs, k, v)
        shutil.rmtree(tmp, ignore_errors=True)

    _FRESH["bundle"] = bundle
    _FRESH["corpus_tasks"] = json.load(
        open(os.path.join(REPO, "data", "taxonomy", "tasks.json"), encoding="utf-8"))
    return bundle


def _new_coverage_blocks(bundle):
    """Every (page, block) whose source file lives under a U3 tree."""
    out = []
    for page, blocks in bundle["pages"].items():
        for b in blocks:
            relpath = b["source_ref"].split("#", 1)[0]
            if relpath.startswith(NEW_TREES):
                out.append((page, b))
    return out


def _join_keys():
    """Every identifier that is a join key another surface resolves against:
    skill/task/subtask IDs, FOLIO IRIs, firm record IDs. None may appear
    inside an editable block's text (R2)."""
    keys = set()
    tax = os.path.join(REPO, "data", "taxonomy")
    skills = json.load(open(os.path.join(tax, "skills.json"), encoding="utf-8"))
    tasks = json.load(open(os.path.join(tax, "tasks.json"), encoding="utf-8"))
    cross = json.load(open(os.path.join(tax, "folio-crosswalk.json"), encoding="utf-8"))

    def harvest(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("id", "skill_id", "task_id", "iri") and isinstance(v, str):
                    keys.add(v)
                else:
                    harvest(v)
        elif isinstance(obj, list):
            for item in obj:
                harvest(item)

    harvest(skills)
    harvest(tasks)
    harvest(cross)
    firm = json.load(open(os.path.join(REPO, "data", "firm", "firm.json"),
                          encoding="utf-8"))
    harvest(firm)
    # Filenames double as ids in some records; keep only real identifier shapes.
    return {k for k in keys if len(k) >= 5}


class NewCoverageTest(unittest.TestCase):
    """The missing trees are in the map, safely."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = _build_fresh_map()
        cls.pages = cls.bundle["pages"]

    # ---- R1: the zero-block pages move off zero -------------------------- #
    def test_skills_page_moves_off_zero(self):
        blocks = self.pages["skills/index.html"]
        tasks = json.load(open(os.path.join(REPO, "data", "taxonomy", "tasks.json"),
                               encoding="utf-8"))["tasks"]
        # one name + one description per task, and nothing else
        self.assertEqual(len(blocks), 2 * len(tasks))
        self.assertGreater(len(blocks), 0)

    def test_firm_page_moves_off_zero(self):
        blocks = self.pages["firm/index.html"]
        refs = {b["source_ref"] for b in blocks}
        self.assertEqual(refs, {
            "data/firm/firm.json#identity.name",
            "data/firm/firm.json#identity.letterhead_note",
        })

    # ---- new blocks are well-formed json_scalars ------------------------- #
    def test_new_blocks_are_json_scalars_with_matching_paths(self):
        new = _new_coverage_blocks(self.bundle)
        self.assertTrue(new, "U3 must register blocks from the new trees")
        for page, b in new:
            with self.subTest(page=page, ref=b["source_ref"]):
                self.assertEqual(b["kind"], "json_scalar")
                relpath, locator = b["source_ref"].split("#", 1)
                # json_scalar grammar: locator IS the json_path
                self.assertEqual(b["json_path"], locator)
                # the path resolves in the real source file to the real string
                obj = json.load(open(os.path.join(REPO, relpath), encoding="utf-8"))
                cur = obj
                for key in locator.split("."):
                    cur = cur[int(key)] if isinstance(cur, list) else cur[key]
                self.assertEqual(cur, b["original_text"])

    # ---- R2: no ID / crosswalk key inside an editable block's text ------- #
    def test_no_join_key_inside_any_new_block_text(self):
        keys = _join_keys()
        self.assertTrue(keys)
        for page, b in _new_coverage_blocks(self.bundle):
            with self.subTest(page=page, ref=b["source_ref"]):
                for key in keys:
                    self.assertNotIn(key, b["original_text"])

    # ---- KTD3: the candidate renders EXACTLY the authored string --------- #
    def test_new_block_elements_contain_only_their_source_string(self):
        """original_hash is computed from the candidate's RENDERED text; if it
        equals the hash of the SOURCE string, the element carries the authored
        prose and nothing else (no chips, no IDs, no generated framing)."""
        for page, b in _new_coverage_blocks(self.bundle):
            with self.subTest(page=page, ref=b["source_ref"]):
                self.assertEqual(b["original_hash"],
                                 text_norm.norm_hash(b["original_text"]))


class StabilityTest(unittest.TestCase):
    """R4/R8: nothing that was editable moved. The committed map is the
    baseline; the fresh build must contain every one of its blocks at the
    same page, the same index, the same source_ref, the same kind."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(COMMITTED_MAP):
            raise unittest.SkipTest("no committed build/editor-map.generated.json")
        with open(COMMITTED_MAP, encoding="utf-8") as fh:
            cls.baseline = json.load(fh)
        cls.fresh = _build_fresh_map()

    def test_every_baseline_block_survives_unmoved(self):
        for page, blocks in self.baseline["pages"].items():
            fresh_blocks = self.fresh["pages"].get(page)
            self.assertIsNotNone(fresh_blocks, f"page {page} vanished from the map")
            fresh_by_index = {b["index"]: b for b in fresh_blocks}
            for b in blocks:
                with self.subTest(page=page, ref=b["source_ref"]):
                    fb = fresh_by_index.get(b["index"])
                    self.assertIsNotNone(
                        fb, f"{page}[{b['index']}] ({b['source_ref']}) is gone")
                    self.assertEqual(fb["source_ref"], b["source_ref"])
                    self.assertEqual(fb["kind"], b["kind"])

    def test_no_baseline_page_loses_blocks(self):
        for page, blocks in self.baseline["pages"].items():
            with self.subTest(page=page):
                self.assertGreaterEqual(
                    len(self.fresh["pages"].get(page, [])), len(blocks))


if __name__ == "__main__":
    unittest.main()
