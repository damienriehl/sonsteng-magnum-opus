"""Full-corpus and perturbation tests for presentation-only Platform work."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

from fresh_site_build import build_fresh_site  # noqa: E402
import platform_semantic_contract as contract  # noqa: E402

BASELINE = os.path.join(HERE, "fixtures", "platform-semantic-baseline.json")


class TestPlatformSemanticContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.site, editor_map = build_fresh_site("semantic-contract-")
        cls.actual = contract.capture_site(cls.site, editor_map)
        with open(BASELINE, encoding="utf-8") as fh:
            cls.baseline = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def mutate(self, field, value, page="index.html"):
        changed = copy.deepcopy(self.actual)
        changed["pages"][page][field] = value
        return contract.compare_snapshots(self.baseline, changed)

    def test_clean_fresh_production_build_matches_baseline(self):
        self.assertEqual(contract.compare_snapshots(self.baseline, self.actual), [])

    def test_authored_text_canary_fires(self):
        values = list(self.actual["pages"]["index.html"]["text"])
        values[0] += " MUTATED"
        self.assertTrue(any("text changed" in e for e in self.mutate("text", values)))

    def test_link_destination_canary_fires(self):
        values = copy.deepcopy(self.actual["pages"]["index.html"]["links"])
        values[0]["href"] = "changed-destination.html"
        self.assertTrue(any("links changed" in e for e in self.mutate("links", values)))

    def test_heading_order_canary_fires(self):
        values = list(reversed(self.actual["pages"]["index.html"]["headings"]))
        self.assertTrue(any("headings changed" in e for e in self.mutate("headings", values)))

    def test_every_editor_identity_dimension_has_a_canary(self):
        page = next(p for p, v in self.actual["pages"].items() if v["editor_blocks"])
        for mutation in ("delete", "duplicate", "id", "source", "kind", "text"):
            changed = copy.deepcopy(self.actual)
            blocks = changed["pages"][page]["editor_blocks"]
            if mutation == "delete":
                blocks.pop()
            elif mutation == "duplicate":
                blocks.append(copy.deepcopy(blocks[0]))
            elif mutation == "id":
                blocks[0]["source_ref"] += "changed"
            elif mutation == "source":
                blocks[0]["source_ref"] = "data/other.md#b00000000"
            else:
                blocks[0]["original_text" if mutation == "text" else "kind"] += " changed"
            self.assertTrue(contract.compare_snapshots(self.baseline, changed), mutation)

    def test_reading_order_canary_fires_without_identity_loss(self):
        page = next(p for p, v in self.actual["pages"].items()
                    if len(v["reading_order"]) > 1)
        changed = copy.deepcopy(self.actual)
        changed["pages"][page]["reading_order"][:2] = reversed(
            changed["pages"][page]["reading_order"][:2])
        errors = contract.compare_snapshots(self.baseline, changed)
        self.assertTrue(any("reading_order" in e or "attachment order" in e for e in errors))

    def test_positional_and_source_resolvability_canaries_fire(self):
        changed = copy.deepcopy(self.actual)
        changed["integrity_errors"] = [
            "x.html: duplicate editor placement index",
            "x.html: unresolvable editor source_ref 'missing.md#b00000000'",
        ]
        errors = contract.compare_snapshots(self.baseline, changed)
        self.assertTrue(any("placement index" in e for e in errors))
        self.assertTrue(any("unresolvable" in e for e in errors))

    def test_presentational_wrapper_and_class_changes_are_ignored(self):
        html = '<main><div class="old"><h1>Title</h1><p>Body</p></div></main>'
        restyled = '<main class="new"><section><h1 class="display">Title</h1><p>Body</p></section></main>'
        def parsed(source):
            p = contract._SemanticHTMLParser()
            p.feed(source)
            return p.text, p.headings, p.links
        self.assertEqual(parsed(html), parsed(restyled))


if __name__ == "__main__":
    unittest.main()
