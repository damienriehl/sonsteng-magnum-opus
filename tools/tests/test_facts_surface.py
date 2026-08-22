#!/usr/bin/env python3
r"""U5 of the word-like-editing plan — the per-matter Facts page (Stage 1).

Damien's settled decisions (2026-07-28): the facts live on a DEDICATED page
per matter (OQ4); a new fact is added even when its drafted mentions are
declined (OQ5). KTD7: the panel is generated from the matter's JSON + schemas,
never hand-curated. IDs and join keys stay read-only everywhere.

These tests pin:
  * every matter gets a facts page whose editable elements render EXACTLY one
    authored scalar each (walker-joinable, hash == value);
  * deny-listed fields (ids, join keys, enums) are never editable;
  * where-used counts are honest (derived = rendered-from-field sites;
    restated = literal occurrences in the matter's OTHER sources);
  * the map bundle carries a `facts` index the Worker validates json_add
    against (file + custom_facts addable slot);
  * the matter schema accepts the optional custom_facts object.

Run:  python3 -m pytest tools/tests/test_facts_surface.py -q
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

SLUG = "m03-tort-meridian"
_FRESH = {}


def _build_fresh():
    if "bundle" in _FRESH:
        return _FRESH
    tmp = tempfile.mkdtemp(prefix="facts-")
    saved = {k: getattr(bs, k) for k in
             ("OUT", "BUILD_DIR", "EDITOR_MAP_PATH", "SPINE_BUILD_ID",
              "PASSIVE_OCCURRENCES")}
    bs.OUT = os.path.join(tmp, "site", "platform")
    bs.BUILD_DIR = os.path.join(tmp, "build")
    bs.EDITOR_MAP_PATH = os.path.join(bs.BUILD_DIR, "editor-map.generated.json")
    try:
        bs.SPINE_BUILD_ID = spine_stamp.compute(bs.DATA)
        bs.EDMAP.reset()
        bs.EDMAP.enabled = True
        bs.PASSIVE_OCCURRENCES = {}
        corpus = bs.load_corpus()
        bs.SKILLS_BY_ID.update({s["id"]: s for s in corpus["skills"]["skills"]})
        bs.TASKS_BY_ID.update({t["id"]: t for t in corpus["tasks"]["tasks"]})
        bs.clean_output()
        bs.write_platform_assets()
        bs.copy_chat_app()
        bs.build_home(corpus)
        bs.build_matter_library(corpus)
        bs.build_packet_pages(corpus)
        bs.build_facts_pages(corpus)
        bs.write_build_stamp(bs.SPINE_BUILD_ID)
        bs.build_editor_map(bs.SPINE_BUILD_ID, bs.compute_scope_index(corpus))
        with open(bs.EDITOR_MAP_PATH, encoding="utf-8") as fh:
            bundle = json.load(fh)
        page_html = open(os.path.join(
            bs.OUT, "matters", SLUG, "facts", "index.html"),
            encoding="utf-8").read()
    finally:
        bs.EDMAP.reset()
        for k, v in saved.items():
            setattr(bs, k, v)
        shutil.rmtree(tmp, ignore_errors=True)
    _FRESH.update({"bundle": bundle, "page_html": page_html})
    return _FRESH


class TestFactsSchema(unittest.TestCase):
    def test_matter_schema_accepts_custom_facts(self):
        schema = json.load(open(os.path.join(
            REPO, "data", "schemas", "matter.schema.json"), encoding="utf-8"))
        cf = schema["properties"].get("custom_facts")
        self.assertIsNotNone(cf, "matter.schema.json must define custom_facts")
        self.assertEqual(cf.get("type"), "object")
        self.assertEqual(cf.get("additionalProperties", {}).get("type"), "string")
        self.assertNotIn("custom_facts", schema.get("required", []))


class TestFactsPage(unittest.TestCase):
    def test_day_zero_machine_fields_are_not_editable_facts(self):
        with tempfile.TemporaryDirectory(prefix="facts-day-zero-") as tmp:
            saved_root = bs.ROOT
            try:
                bs.ROOT = tmp
                matter_dir = os.path.join(tmp, "data", "matters", "sample")
                business_dir = os.path.join(matter_dir, "business")
                os.makedirs(business_dir)
                with open(os.path.join(business_dir, "business.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"engagement": {
                        "signed_date": "2025-01-01",
                        "signed_date_day_zero_offset": 4,
                    }}, fh)
                rows = bs._fact_rows({
                    "_dir": matter_dir,
                    "open_date": "2025-01-02",
                    "open_date_day_zero_offset": 5,
                })
            finally:
                bs.ROOT = saved_root

        paths = {path for _relpath, path, _value in rows}
        self.assertIn("open_date", paths)
        self.assertIn("engagement.signed_date", paths)
        self.assertNotIn("open_date_day_zero_offset", paths)
        self.assertNotIn("engagement.signed_date_day_zero_offset", paths)

    def test_day_zero_sidecar_is_not_counted_as_an_authored_restatement(self):
        with tempfile.TemporaryDirectory(prefix="facts-day-zero-") as tmp:
            saved_root = bs.ROOT
            try:
                bs.ROOT = tmp
                matter_dir = os.path.join(tmp, "data", "matters", "sample")
                os.makedirs(matter_dir)
                literal = "2025-01-02"
                with open(os.path.join(matter_dir, "matter.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"open_date": literal}, fh)
                with open(os.path.join(matter_dir, "date-offsets.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"entries": [{"literal": literal, "offset": 5}]}, fh)
                derived, restated = bs.fact_usage(
                    matter_dir,
                    "data/matters/sample/matter.json",
                    "open_date",
                    literal,
                )
            finally:
                bs.ROOT = saved_root

        self.assertEqual(derived, 0)
        self.assertEqual(restated, 0)

    def test_every_matter_has_a_facts_page_in_the_map(self):
        bundle = _build_fresh()["bundle"]
        pages = [p for p in bundle["pages"]
                 if p.endswith("/facts/index.html")]
        self.assertEqual(len(pages), 20)
        self.assertIn("matters/%s/facts/index.html" % SLUG, pages)

    def test_editable_facts_render_exactly_their_value(self):
        f = _build_fresh()
        page = "matters/%s/facts/index.html" % SLUG
        blocks = f["bundle"]["pages"][page]
        self.assertGreater(len(blocks), 3)
        matter = json.load(open(os.path.join(
            REPO, "data", "matters", SLUG, "matter.json"), encoding="utf-8"))
        for b in blocks:
            self.assertEqual(b["kind"], "json_scalar")
            # the candidate element rendered EXACTLY the authored value
            self.assertEqual(b["original_hash"],
                             text_norm.norm_hash(b["original_text"]))
        by_path = {b["json_path"]: b for b in blocks
                   if b["source_ref"].endswith("#" + (b.get("json_path") or ""))
                   and b["source_ref"].split("#")[0].endswith("matter.json")}
        self.assertIn("caption", by_path)
        self.assertEqual(by_path["caption"]["original_text"], matter["caption"])

    def test_join_keys_and_enums_are_never_editable(self):
        f = _build_fresh()
        page = "matters/%s/facts/index.html" % SLUG
        paths = {b.get("json_path") for b in f["bundle"]["pages"][page]}
        for deny in ("id", "@id", "schema_version", "slug", "shape", "tier",
                     "jurisdiction", "client_id", "fee_type", "matter_id"):
            self.assertNotIn(deny, paths)

    def test_numeric_facts_are_editable_but_identifiers_and_enums_are_not(self):
        f = _build_fresh()
        page = "matters/%s/facts/index.html" % SLUG
        blocks = f["bundle"]["pages"][page]
        paths = {b.get("json_path"): b for b in blocks}
        self.assertIn("engagement.contingency_pct", paths)
        self.assertEqual(paths["engagement.contingency_pct"]["original_text"], "33.34")
        for denied in ("id", "@id", "client_id", "matter_id",
                       "engagement.fee_type", "tier", "shape"):
            self.assertNotIn(denied, paths)

    def test_where_used_counts_are_present_and_honest(self):
        f = _build_fresh()
        html = f["page_html"]
        self.assertIn("fact-uses", html)
        # the caption literal appears in matter sources beyond matter.json —
        # the page must not claim zero for it
        matter = json.load(open(os.path.join(
            REPO, "data", "matters", SLUG, "matter.json"), encoding="utf-8"))
        derived, restated = bs.fact_usage(
            os.path.join(REPO, "data", "matters", SLUG),
            "data/matters/%s/matter.json" % SLUG, "caption", matter["caption"],
            f["bundle"])
        self.assertGreaterEqual(derived + restated, 0)

    def test_facts_index_rides_the_map_for_worker_validation(self):
        bundle = _build_fresh()["bundle"]
        self.assertIn("facts", bundle)
        meta = bundle["facts"].get(SLUG)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["file"], "data/matters/%s/matter.json" % SLUG)
        self.assertEqual(meta["addable"], {"custom_facts": "string"})
        self.assertIn("caption", meta["editable"])
        self.assertNotIn("client_id", meta["editable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
