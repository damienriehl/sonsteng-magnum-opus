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
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import text_norm          # noqa: E402
import build_site         # noqa: E402
from fresh_site_build import build_fresh_site  # noqa: E402

COMMITTED_MAP = os.path.join(REPO, "build", "editor-map.generated.json")

# The trees U3 brings into the map. A block whose source file lives under one
# of these is a "new-coverage" block and carries U3's extra guarantees.
NEW_TREES = ("data/taxonomy/", "data/firm/", "data/jurisdictions/", "data/copy/")

_FRESH = {}   # module-level cache: one in-process site build for the whole file


def _build_fresh_map():
    """Run the full generator into a temp dir and return the map bundle.

    Calls build_site.main() with OUT/BUILD_DIR redirected, so
    the repo's site/ and build/ trees stay untouched. Cached per test run."""
    if "bundle" in _FRESH:
        return _FRESH["bundle"]

    tmp, _site_out, bundle = build_fresh_site("edcov-")
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

    # ---- R1: the landing pages carry their authored copy ----------------- #
    def test_authored_page_copy_counts(self):
        self.assertEqual(len(self.pages["index.html"]), 21)
        self.assertEqual(len(self.pages["matters/index.html"]), 3)
        self.assertEqual(len(self.pages["firm/index.html"]), 33)

    def test_multi_surface_copy_is_read_only(self):
        refs = {b["source_ref"] for b in self.pages["index.html"]}
        read_only_refs = {
            "data/copy/home.json#explore.cards.skills.title",
            "data/copy/home.json#explore.cards.templates.title",
        }
        for code in ("M1", "M2", "M3"):
            for field in ("title", "thesis"):
                read_only_refs.add(
                    "data/copy/home.json#volumes.modules.%s.%s" % (code, field))
        for ref in sorted(read_only_refs):
            with self.subTest(ref=ref):
                self.assertNotIn(ref, refs)

    def test_page_copy_sources_are_page_local(self):
        expected = {
            "index.html": "data/copy/home.json#",
            "matters/index.html": "data/copy/matters.json#",
            "firm/index.html": "data/copy/firm.json#",
        }
        for page, prefix in expected.items():
            refs = [b["source_ref"] for b in self.pages[page]
                    if b["source_ref"].startswith("data/copy/")]
            with self.subTest(page=page):
                self.assertTrue(refs)
                self.assertTrue(all(ref.startswith(prefix) for ref in refs))

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
        self.assertTrue({
            "data/firm/firm.json#identity.name",
            "data/firm/firm.json#identity.letterhead_note",
        }.issubset(refs))
        self.assertEqual(len(blocks), 33)

    def test_firm_provenance_fragments_declare_the_mixed_sentence(self):
        blocks = {b["source_ref"]: b for b in self.pages["firm/index.html"]}
        for ref in (
            "data/copy/firm.json#hero.provenance_before_path",
            "data/copy/firm.json#hero.provenance_after_path",
        ):
            with self.subTest(ref=ref):
                self.assertIs(blocks[ref].get("mixed"), True)

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
                # the path resolves in the real source file to the rendered
                # scalar text (JSON numbers render as text in the candidate)
                obj = json.load(open(os.path.join(REPO, relpath), encoding="utf-8"))
                cur = obj
                for key in locator.split("."):
                    cur = cur[int(key)] if isinstance(cur, list) else cur[key]
                self.assertEqual(str(cur), b["original_text"])

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


class OccurrencesTest(unittest.TestCase):
    """U1: every rendered location is indexed without changing page blocks."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = _build_fresh_map()
        cls.occurrences = cls.bundle["occurrences"]

    def test_single_page_leaf_has_one_occurrence(self):
        ref = "data/firm/firm.json#identity.name"
        self.assertEqual(
            self.occurrences[ref],
            [{"page": "firm/index.html", "index": 1}],
        )

    def test_meridian_canon_leaf_has_ten_distinct_page_occurrences(self):
        ref = "data/jurisdictions/meridian.json#name"
        occurrences = self.occurrences[ref]
        self.assertEqual(len(occurrences), 10)
        self.assertEqual(len({item["page"] for item in occurrences}), 10)

    def test_matter_caption_has_two_occurrences(self):
        ref = "data/matters/m01-arbitration-meridian/matter.json#caption"
        self.assertEqual(len(self.occurrences[ref]), 2)

    def test_source_metadata_stays_single_valued_across_occurrences(self):
        ref = "data/jurisdictions/meridian.json#name"
        blocks = [
            block
            for page_blocks in self.bundle["pages"].values()
            for block in page_blocks
            if block["source_ref"] == ref
        ]
        self.assertEqual(len(blocks), 10)
        self.assertEqual(len({block["original_text"] for block in blocks}), 1)
        self.assertEqual(len({block["json_path"] for block in blocks}), 1)
        self.assertEqual(
            set(self.occurrences[ref][0]), {"page", "index"},
            "occurrences must not duplicate source metadata",
        )

    def test_total_block_count_is_unchanged(self):
        self.assertEqual(self.bundle["counts"]["_total"], 5917)

    def test_occurrences_are_the_exact_page_block_projection(self):
        expected = {}
        for page, blocks in self.bundle["pages"].items():
            for block in blocks:
                expected.setdefault(block["source_ref"], []).append({
                    "page": page,
                    "index": block["index"],
                })
        self.assertEqual(self.bundle["schema_version"], "1.1.0")
        self.assertEqual(self.occurrences, expected)


class EditorContractGuardTest(unittest.TestCase):
    """U3: both absence guards have seeded proof that they can fail."""

    PURE_REF = "data/test.json#title"

    @staticmethod
    def _block(ref=PURE_REF, source="Authored", rendered="Authored", **extra):
        block = {
            "index": 0,
            "kind": "json_scalar",
            "source_ref": ref,
            "original_text": source,
            "original_hash": text_norm.norm_hash(rendered),
            "json_path": ref.split("#", 1)[1],
        }
        block.update(extra)
        return block

    def test_mixed_element_violation_fails(self):
        pages = {"one.html": [self._block(rendered="Authored computed-value")]}
        with self.assertRaisesRegex(build_site.EditorContractError, "mixed element"):
            build_site.validate_editor_contract(pages, {}, {})

    def test_declared_mixed_element_passes(self):
        pages = {"one.html": [self._block(
            rendered="Authored computed-value", mixed=True)]}
        build_site.validate_editor_contract(pages, {}, {})

    def test_normalization_artifacts_do_not_trip_mixed_guard(self):
        pages = {"one.html": [self._block(
            source="Authored text", rendered="  Authored\u200b   text  ")]}
        build_site.validate_editor_contract(pages, {}, {})

    def test_three_render_surfaces_with_one_recorded_fails(self):
        pages = {"one.html": [self._block()]}
        rendered = {self.PURE_REF: [
            {"page": "one.html", "index": 0},
            {"page": "two.html", "index": 4},
            {"page": "three.html", "index": 2},
        ]}
        with self.assertRaisesRegex(build_site.EditorContractError,
                                    "renders on 3 surfaces.*records 1"):
            build_site.validate_editor_contract(pages, rendered, {})


    def test_allowlisted_violation_passes(self):
        pages = {"one.html": [self._block(rendered="Authored computed")]}
        rendered = {self.PURE_REF: [
            {"page": "one.html", "index": 0},
            {"page": "two.html", "index": 1},
        ]}
        allowlist = {self.PURE_REF: "temporary test migration"}
        build_site.validate_editor_contract(pages, rendered, allowlist)

    def test_removing_live_coupled_ref_from_allowlist_fails(self):
        pages = {
            "one.html": [self._block()],
            "two.html": [self._block()],
        }
        rendered = {self.PURE_REF: [
            {"page": "one.html", "index": 0},
            {"page": "two.html", "index": 0},
        ]}
        with self.assertRaisesRegex(build_site.EditorContractError,
                                    "coupled ref is missing from transition allowlist"):
            build_site.validate_editor_contract(pages, rendered, {})

    def test_unannotated_task_name_pattern_is_caught(self):
        ref = "data/taxonomy/tasks.json#tasks.0.name"
        pages = {"skills/index.html": [self._block(ref=ref)]}
        rendered = {ref: [
            {"page": "skills/index.html", "index": 8},
            {"page": "modules/m1.html", "index": None,
             "pattern": "task-name"},
        ]}
        with self.assertRaisesRegex(build_site.EditorContractError,
                                    "renders on 2 surfaces.*records 1"):
            build_site.validate_editor_contract(pages, rendered, {})


class PageOverrideRenderTest(unittest.TestCase):
    def setUp(self):
        build_site.EDMAP.reset()
        build_site.EDMAP.enabled = True
        build_site.PAGE_OVERRIDES.clear()

    def tearDown(self):
        build_site.PAGE_OVERRIDES.clear()

    def test_renderer_prefers_surface_leaf_and_readdresses_the_block(self):
        shared = "data/copy/home.json#volumes.modules.M1.title"
        override = "data/copy/home.json#overrides.aaaaaaaaaaaaaaaa.value"
        build_site.PAGE_OVERRIDES[("modules/m1.html", shared)] = {
            "value": "Local title", "source_ref": override,
            "json_path": "overrides.aaaaaaaaaaaaaaaa.value",
        }
        html = '<main><h1 data-ebsrc="%s">Shared title</h1></main>' % shared
        rendered = build_site._apply_page_overrides("modules/m1.html", html)
        self.assertIn('data-ebsrc="%s"' % override, rendered)
        self.assertIn(">Local title</h1>", rendered)
        self.assertNotIn("Shared title", rendered)

        # A later shared edit still cannot reach the surface-owned copy.
        rerendered = build_site._apply_page_overrides(
            "modules/m1.html",
            '<main><h1 data-ebsrc="%s">Changed shared title</h1></main>' % shared)
        self.assertIn(">Local title</h1>", rerendered)

    def test_renderer_falls_back_byte_for_byte_after_revert(self):
        shared = "data/copy/home.json#volumes.modules.M1.title"
        html = '<main><h1 data-ebsrc="%s">Shared title</h1></main>' % shared
        self.assertEqual(build_site._apply_page_overrides("modules/m1.html", html), html)


if __name__ == "__main__":
    unittest.main()
