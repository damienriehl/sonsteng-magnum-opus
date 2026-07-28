#!/usr/bin/env python3
"""Tests for tools/stamp_block_ids.py + the bid-aware renderer (U1/U2 of the
word-like-editing plan) — written BEFORE the migration, per R8.

The load-bearing property: after stamping, every block markdown() emits carries
a durable {#b:xxxxxxxx} marker; the marker NEVER renders; existing markers are
NEVER changed by a re-stamp; and the before/after equivalence checker proves a
migration moved nothing (same page, same index, same text, same file — for
every block).

Run:  python3 -m pytest tools/tests/test_stamp_block_ids.py -q
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import build_site as bs           # noqa: E402
import stamp_block_ids as sb      # noqa: E402

BID_RE = re.compile(r"\{#b:([0-9a-f]{8})\}")

FIXTURE_MD = """# Statement of Marceline Osgard

Ms. Osgard operated Bus 41 on the Halden circuit
for eleven years without incident.

The filing deadline is 14 days from the date of
discharge, per the collective bargaining agreement.

## Grievance history

- First written warning, March 2024
- Second written warning, June 2024

1. Notice served
2. Hearing scheduled

> The arbitrator retains jurisdiction over remedy.

| Step | Date |
|------|------|
| Filing | June 1 |

---

Closing paragraph after a horizontal rule.
"""

# Blocks markdown() emits for FIXTURE_MD: h1, p, p, h2, li, li, li, li,
# blockquote, p  -> 10 editable blocks. Table rows + hr emit nothing.
FIXTURE_MD_BLOCKS = 10


def render_no_record(md):
    """Render with the editor map OFF — the public-page rendering path."""
    bs.EDMAP.reset()
    return bs.markdown(md)


def render_recorded(md, src):
    """Render with recording ON; returns (html, {source_ref: meta}, unmarked)."""
    bs.EDMAP.reset()
    bs.EDMAP.enabled = True
    html = bs.markdown(md, src=src)
    sources = dict(bs.EDMAP.sources)
    unmarked = list(bs.EDMAP.unmarked)
    bs.EDMAP.reset()
    return html, sources, unmarked


class TestRendererMarkerContract(unittest.TestCase):
    """The renderer strips markers, keys refs by bid, and flags unmarked."""

    def setUp(self):
        self.stamped, n = sb.stamp_md_text(FIXTURE_MD, set())
        self.assertEqual(n, FIXTURE_MD_BLOCKS)

    def test_marker_never_renders(self):
        html = render_no_record(self.stamped)
        self.assertNotIn("{#b:", html)
        # And the stamped source renders byte-identically to the unstamped one.
        self.assertEqual(html, render_no_record(FIXTURE_MD))

    def test_source_refs_are_bid_keyed(self):
        _, sources, unmarked = render_recorded(self.stamped, "data/x/case.md")
        self.assertEqual(len(sources), FIXTURE_MD_BLOCKS)
        self.assertEqual(unmarked, [])
        for ref, meta in sources.items():
            self.assertRegex(ref, r"^data/x/case\.md#b[0-9a-f]{8}$")
            self.assertNotIn("{#b:", meta["original_text"])

    def test_json_body_refs_use_dot_separator(self):
        body, n = sb.stamp_md_text("One paragraph only.", set())
        self.assertEqual(n, 1)
        _, sources, _ = render_recorded(body, "data/m/exercise.json#sections.intro.body_md")
        (ref,) = sources.keys()
        self.assertRegex(
            ref, r"^data/m/exercise\.json#sections\.intro\.body_md\.b[0-9a-f]{8}$")

    def test_unmarked_block_is_excluded_and_counted(self):
        md = self.stamped + "\nA brand-new paragraph typed by hand.\n"
        html, sources, unmarked = render_recorded(md, "data/x/case.md")
        self.assertEqual(len(sources), FIXTURE_MD_BLOCKS)   # not registered
        self.assertEqual(unmarked, ["data/x/case.md"])      # but flagged
        self.assertIn("brand-new paragraph", html)           # still renders
        # ... and read-only: no data-ebsrc on the unmarked paragraph.
        para_tag = [seg for seg in html.split("<p")
                    if "brand-new paragraph" in seg][0]
        self.assertNotIn("data-ebsrc", para_tag.split(">", 1)[0])

    def test_refs_stable_across_renders(self):
        _, first, _ = render_recorded(self.stamped, "data/x/case.md")
        _, second, _ = render_recorded(self.stamped, "data/x/case.md")
        self.assertEqual(set(first), set(second))


class TestStamper(unittest.TestCase):
    def test_stamps_every_emitted_block_and_only_those(self):
        stamped, n = sb.stamp_md_text(FIXTURE_MD, set())
        self.assertEqual(n, FIXTURE_MD_BLOCKS)
        self.assertEqual(len(BID_RE.findall(stamped)), FIXTURE_MD_BLOCKS)
        # Structural non-blocks stay pristine.
        for line in stamped.split("\n"):
            if line.strip().startswith("|") or re.match(r"^-{3,}$", line.strip()):
                self.assertNotIn("{#b:", line)

    def test_idempotent(self):
        once, n1 = sb.stamp_md_text(FIXTURE_MD, set())
        twice, n2 = sb.stamp_md_text(once, set(BID_RE.findall(once)))
        self.assertEqual(n2, 0)
        self.assertEqual(twice, once)

    def test_existing_markers_survive_and_insert_gets_fresh_bid(self):
        once, _ = sb.stamp_md_text(FIXTURE_MD, set())
        before = BID_RE.findall(once)
        # Hand-insert a paragraph in the middle (the property the plan rests on).
        edited = once.replace(
            "## Grievance history",
            "A paragraph John typed himself.\n\n## Grievance history")
        restamped, n = sb.stamp_md_text(edited, set(before))
        self.assertEqual(n, 1)
        after = BID_RE.findall(restamped)
        # Every pre-existing bid unchanged, in order; one new unique bid added.
        self.assertEqual([b for b in after if b in set(before)], before)
        new = [b for b in after if b not in set(before)]
        self.assertEqual(len(new), 1)

    def test_minted_bids_unique_against_existing(self):
        existing = set()
        stamped, n = sb.stamp_md_text(FIXTURE_MD, existing)
        bids = BID_RE.findall(stamped)
        self.assertEqual(len(bids), len(set(bids)))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{8}", b) for b in bids))
        # Minting honours (and extends) the caller's corpus-wide registry.
        self.assertEqual(existing, set(bids))

    def test_stamp_json_body_field(self):
        raw = json.dumps({
            "caption": "A scalar that must NOT be stamped",
            "sections": {"intro": {"title": "Intro",
                                   "body_md": "First para.\n\nSecond para."}},
        }, indent=2)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "exercise.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(raw + "\n")
            existing = set()
            n = sb.stamp_file(p, ["sections.intro.body_md"], existing)
            self.assertEqual(n, 2)
            obj = json.loads(open(p, encoding="utf-8").read())
            body = obj["sections"]["intro"]["body_md"]
            self.assertEqual(len(BID_RE.findall(body)), 2)
            self.assertNotIn("{#b:", obj["caption"])
            # Idempotent second pass.
            self.assertEqual(sb.stamp_file(p, ["sections.intro.body_md"],
                                           existing), 0)


class TestEquivalenceChecker(unittest.TestCase):
    """The mechanical before/after proof for the corpus migration (R8)."""

    @staticmethod
    def _bundle(pages):
        return {"pages": pages,
                "counts": {**{k: len(v) for k, v in pages.items()},
                           "_total": sum(len(v) for v in pages.values())}}

    @staticmethod
    def _blk(index, ref, text, kind="prose", json_path=None):
        return {"index": index, "source_ref": ref, "original_text": text,
                "kind": kind, "json_path": json_path,
                "original_hash": "h", "has_inline_formatting": False,
                "context": ""}

    def test_identical_content_passes_despite_locator_change(self):
        before = self._bundle({"p.html": [
            self._blk(0, "data/a.md#p0", "Alpha"),
            self._blk(1, "data/a.md#p1", "Beta")]})
        after = self._bundle({"p.html": [
            self._blk(0, "data/a.md#b0011aabb", "Alpha"),
            self._blk(1, "data/a.md#b0011aacc", "Beta")]})
        self.assertEqual(sb.equivalence_check(before, after), [])

    def test_text_change_is_an_error(self):
        before = self._bundle({"p.html": [self._blk(0, "data/a.md#p0", "Alpha")]})
        after = self._bundle({"p.html": [
            self._blk(0, "data/a.md#b0011aabb", "AlphaX")]})
        self.assertTrue(sb.equivalence_check(before, after))

    def test_index_shift_is_an_error(self):
        before = self._bundle({"p.html": [self._blk(3, "data/a.md#p0", "Alpha")]})
        after = self._bundle({"p.html": [
            self._blk(4, "data/a.md#b0011aabb", "Alpha")]})
        self.assertTrue(sb.equivalence_check(before, after))

    def test_file_change_is_an_error(self):
        before = self._bundle({"p.html": [self._blk(0, "data/a.md#p0", "Alpha")]})
        after = self._bundle({"p.html": [
            self._blk(0, "data/OTHER.md#b0011aabb", "Alpha")]})
        self.assertTrue(sb.equivalence_check(before, after))

    def test_lost_block_and_lost_page_are_errors(self):
        before = self._bundle({"p.html": [self._blk(0, "data/a.md#p0", "Alpha"),
                                          self._blk(1, "data/a.md#p1", "Beta")],
                               "q.html": []})
        self.assertTrue(sb.equivalence_check(
            before, self._bundle({"p.html": [
                self._blk(0, "data/a.md#b0011aabb", "Alpha")], "q.html": []})))
        self.assertTrue(sb.equivalence_check(
            before, self._bundle({"p.html": [
                self._blk(0, "data/a.md#b0011aabb", "Alpha"),
                self._blk(1, "data/a.md#b0011aacc", "Beta")]})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
