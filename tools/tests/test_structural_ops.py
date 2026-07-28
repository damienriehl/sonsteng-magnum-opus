#!/usr/bin/env python3
"""Tests for tools/structural_ops.py — insert/delete/split/merge/move on
markdown text, addressed by durable block IDs (U4 of the word-like-editing
plan). Written before the implementation.

The invariants (plan test scenarios):
  * insert_after: new block gets a fresh bid; NO other bid changes; the
    paragraph lands in the right place.
  * delete: block leaves the text; every other bid unchanged.
  * split / merge preserve total text exactly (assert on concatenation).
  * move reorders without changing any bid.
  * every op re-renders cleanly (the renderer emits the same blocks the op
    intended — segmentation proof, not just string manipulation).
"""

from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import build_site as bs          # noqa: E402
import stamp_block_ids as sb     # noqa: E402
import structural_ops as so      # noqa: E402

BID_RE = sb.BID_RE


def stamped_fixture():
    md = (
        "# Case file\n"
        "\n"
        "First paragraph about the matter,\n"
        "spanning two source lines.\n"
        "\n"
        "Second paragraph about the deadline.\n"
        "\n"
        "- item one\n"
        "- item two\n"
        "\n"
        "> A quoted authority.\n"
        "\n"
        "Closing paragraph.\n"
    )
    stamped, n = sb.stamp_md_text(md, set())
    assert n == 7  # h1, p, p, li, li, blockquote, p
    return stamped


def bids_of(text):
    return BID_RE.findall(text)


def blocks_of(text):
    """(tag, clean_raw, bid) for every renderer-emitted block."""
    spans = []
    bs.markdown(text, spans=spans)
    return [(s["tag"], s["raw"], s["bid"]) for s in spans]


def plain_concat(text):
    return "".join(raw for _t, raw, _b in blocks_of(text))


class TestLocate(unittest.TestCase):
    def test_locate_finds_extent_and_tag(self):
        text = stamped_fixture()
        blocks = blocks_of(text)
        first_p_bid = blocks[1][2]
        blk = so.locate_block(text, first_p_bid)
        self.assertEqual(blk.tag, "p")
        lines = text.split("\n")
        # the extent covers exactly the two source lines of the paragraph
        self.assertIn("First paragraph", lines[blk.start_line])
        self.assertIn("spanning two source lines.", lines[blk.end_line])

    def test_locate_unknown_bid_raises(self):
        with self.assertRaises(so.StructuralError):
            so.locate_block(stamped_fixture(), "ffffffff")


class TestInsertAfter(unittest.TestCase):
    def test_insert_paragraph_after_paragraph(self):
        text = stamped_fixture()
        before = bids_of(text)
        anchor = blocks_of(text)[1][2]  # first paragraph
        new_raw, new_bid = so.op_insert_after(
            text, anchor, "A brand-new paragraph.", set(before))
        after = bids_of(new_raw)
        self.assertEqual([b for b in after if b in set(before)], before)
        self.assertNotIn(new_bid, before)
        blocks = blocks_of(new_raw)
        self.assertEqual(blocks[2], ("p", "A brand-new paragraph.", new_bid))
        # neighbours intact, in order
        self.assertIn("First paragraph", blocks[1][1])
        self.assertIn("Second paragraph", blocks[3][1])

    def test_insert_after_list_item_is_a_list_item(self):
        text = stamped_fixture()
        blocks = blocks_of(text)
        li_bid = blocks[3][2]  # "item one"
        new_raw, new_bid = so.op_insert_after(
            text, li_bid, "item one-and-a-half", set(bids_of(text)))
        nb = blocks_of(new_raw)
        self.assertEqual(nb[4], ("li", "item one-and-a-half", new_bid))
        self.assertEqual(nb[5][1], "item two")  # still one list
        # still exactly one <ul> in the rendering
        self.assertEqual(bs.markdown(new_raw).count("<ul>"), 1)

    def test_insert_rejects_marker_in_payload(self):
        text = stamped_fixture()
        anchor = blocks_of(text)[1][2]
        with self.assertRaises(so.StructuralError):
            so.op_insert_after(text, anchor, "sneaky {#b:00000000}", set())

    def test_insert_multi_paragraph_payload_rejected(self):
        # one op = one block; a payload containing a blank line would silently
        # create unmarked siblings.
        text = stamped_fixture()
        anchor = blocks_of(text)[1][2]
        with self.assertRaises(so.StructuralError):
            so.op_insert_after(text, anchor, "two\n\nparagraphs", set())


class TestDelete(unittest.TestCase):
    def test_delete_paragraph(self):
        text = stamped_fixture()
        before = blocks_of(text)
        victim = before[2][2]  # "Second paragraph about the deadline."
        new_raw = so.op_delete(text, victim)
        after = blocks_of(new_raw)
        self.assertEqual(len(after), len(before) - 1)
        self.assertEqual([b[2] for b in after],
                         [b[2] for b in before if b[2] != victim])
        self.assertNotIn("deadline", new_raw)
        # no doubled blank lines left behind
        self.assertNotIn("\n\n\n", new_raw)

    def test_delete_list_item_keeps_list(self):
        text = stamped_fixture()
        before = blocks_of(text)
        victim = before[3][2]  # "item one"
        new_raw = so.op_delete(text, victim)
        after = blocks_of(new_raw)
        self.assertEqual([b[1] for b in after if b[0] == "li"], ["item two"])
        self.assertEqual(bs.markdown(new_raw).count("<ul>"), 1)


class TestSplit(unittest.TestCase):
    def test_split_preserves_total_text(self):
        text = stamped_fixture()
        blk = blocks_of(text)[1]  # two-line paragraph
        original = blk[1]
        cut = original.index("matter,") + len("matter,")
        part1, part2 = original[:cut], original[cut:].strip()
        new_raw, new_bid = so.op_split(
            text, blk[2], part1, part2, set(bids_of(text)))
        blocks = blocks_of(new_raw)
        self.assertEqual(blocks[1], ("p", part1, blk[2]))       # bid stays on part1
        self.assertEqual(blocks[2], ("p", part2, new_bid))      # fresh bid on part2
        # concatenation preserved exactly (modulo the split-point whitespace)
        self.assertEqual((blocks[1][1] + " " + blocks[2][1]),
                         original[:cut] + " " + original[cut:].strip())

    def test_split_rejects_text_change(self):
        text = stamped_fixture()
        blk = blocks_of(text)[1]
        with self.assertRaises(so.StructuralError):
            so.op_split(text, blk[2], "Completely", "different text", set())


class TestMerge(unittest.TestCase):
    def test_merge_two_paragraphs_preserves_text(self):
        text = stamped_fixture()
        blocks = blocks_of(text)
        first, second = blocks[1], blocks[2]
        new_raw = so.op_merge(text, first[2], second[2])
        after = blocks_of(new_raw)
        self.assertEqual(len(after), len(blocks) - 1)
        merged = [b for b in after if b[2] == first[2]][0]
        self.assertEqual(merged[1], first[1] + " " + second[1])  # concat exact
        self.assertNotIn(second[2], bids_of(new_raw))            # bid retired
        # every OTHER bid unchanged
        keep = [b[2] for b in blocks if b[2] != second[2]]
        self.assertEqual([b[2] for b in after], keep)

    def test_merge_requires_adjacent_same_tag(self):
        text = stamped_fixture()
        blocks = blocks_of(text)
        with self.assertRaises(so.StructuralError):
            so.op_merge(text, blocks[1][2], blocks[6][2])  # p + far p: not adjacent
        with self.assertRaises(so.StructuralError):
            so.op_merge(text, blocks[0][2], blocks[1][2])  # h1 + p: tag mismatch


class TestMove(unittest.TestCase):
    def test_move_paragraph_after_another(self):
        text = stamped_fixture()
        blocks = blocks_of(text)
        mover = blocks[1]     # first paragraph
        dest = blocks[6]      # closing paragraph
        new_raw = so.op_move(text, mover[2], dest[2])
        after = blocks_of(new_raw)
        self.assertEqual(set(bids_of(new_raw)), set(bids_of(text)))  # no bid changes
        order = [b[2] for b in after]
        self.assertEqual(order.index(mover[2]), order.index(dest[2]) + 1)
        self.assertEqual(sorted(b[1] for b in after),
                         sorted(b[1] for b in blocks))  # text preserved

    def test_move_is_exact_inverse(self):
        text = stamped_fixture()
        blocks = blocks_of(text)
        mover, dest = blocks[2], blocks[6]
        moved = so.op_move(text, mover[2], dest[2])
        # move it back after its original predecessor
        back = so.op_move(moved, mover[2], blocks[1][2])
        self.assertEqual([(b[0], b[1], b[2]) for b in blocks_of(back)],
                         [(b[0], b[1], b[2]) for b in blocks])


class TestJsonBody(unittest.TestCase):
    def test_ops_work_inside_json_body_strings(self):
        body = "Intro paragraph.\n\nSecond paragraph."
        stamped, n = sb.stamp_md_text(body, set())
        self.assertEqual(n, 2)
        b1, b2 = bids_of(stamped)
        new_body, new_bid = so.op_insert_after(
            stamped, b1, "Inserted between.", set([b1, b2]))
        self.assertEqual([b[1] for b in blocks_of(new_body)],
                         ["Intro paragraph.", "Inserted between.",
                          "Second paragraph."])


if __name__ == "__main__":
    unittest.main(verbosity=2)
