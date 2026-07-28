#!/usr/bin/env python3
"""structural_ops.py — insert/delete/split/merge/move on markdown text,
addressed by durable block IDs (U4 of the word-like-editing plan).

Every operation takes the RAW markdown text of a source (a whole ``.md`` file
or a ``body_md`` string inside JSON), locates blocks by their trailing
``{#b:xxxxxxxx}`` marker using build_site.markdown()'s own segmentation (the
``spans`` collector — the one parser, never a re-implementation), edits the
source LINES, and returns the new text. Identity rules:

  * insert_after mints a fresh bid for the new block (caller supplies the
    corpus-wide registry so mints can't collide);
  * delete retires the block's bid (never reused — the registry keeps it);
  * split keeps the original bid on the FIRST part and mints for the second;
  * merge keeps the FIRST block's bid and retires the second's;
  * move changes NO bid.

Any ambiguity or invariant violation raises StructuralError — the caller
routes it to needs_human; nothing is ever silently corrupted.
"""

from __future__ import annotations

import dataclasses
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_site as bs        # noqa: E402
import stamp_block_ids as sb   # noqa: E402


class StructuralError(Exception):
    """The operation cannot be applied unambiguously."""


@dataclasses.dataclass
class Block:
    tag: str          # p | h1..h6 | li | blockquote
    start_line: int   # 0-based, inclusive
    end_line: int     # 0-based, inclusive
    bid: str
    raw: str          # marker-stripped source span


def _blocks(text):
    spans = []
    bs.markdown(text, spans=spans)
    return [Block(s["tag"], s["start_line"], s["end_line"], s["bid"], s["raw"])
            for s in spans]


def locate_block(text, bid):
    """The Block carrying `bid`, or StructuralError."""
    hits = [b for b in _blocks(text) if b.bid == bid]
    if len(hits) != 1:
        raise StructuralError(
            "bid %s matches %d block(s) — cannot address it" % (bid, len(hits)))
    return hits[0]


def _validate_payload(new_text):
    """A structural payload is ONE block's plain text: no marker of its own, no
    blank lines (which would silently create unmarked sibling blocks), no
    block-structural lead-in that would change the segmentation."""
    if not isinstance(new_text, str) or not new_text.strip():
        raise StructuralError("empty payload")
    if sb.BID_RE.search(new_text):
        raise StructuralError("payload may not carry a {#b:} marker")
    if "\n" in new_text.strip():
        raise StructuralError("payload must be a single block (no line breaks)")
    return new_text.strip()


def _lines(text):
    return text.replace("\r\n", "\n").split("\n")


def _is_blank(line):
    return not line.strip()


def _splice(lines, start, end, replacement):
    """lines[start..end] (inclusive) -> replacement (list of lines)."""
    return lines[:start] + replacement + lines[end + 1:]


def _drop_with_separator(lines, start, end):
    """Remove lines[start..end] plus ONE adjacent blank separator so paragraph
    spacing stays canonical (never leaves a doubled blank line)."""
    new = lines[:start] + lines[end + 1:]
    # after removal, index `start` is the line that followed the block
    if start < len(new) and _is_blank(new[start]) and \
            (start == 0 or _is_blank(new[start - 1])):
        del new[start]
    elif start > 0 and _is_blank(new[start - 1]) and \
            (start >= len(new) or _is_blank(new[start]) if start < len(new) else True):
        # block was at EOF (or followed by blank): drop the preceding separator
        if start >= len(new) or _is_blank(new[start]):
            del new[start - 1]
    return new


def _marker(bid):
    return "{#b:%s}" % bid


def op_insert_after(text, anchor_bid, new_text, existing_bids):
    """Insert a new block after `anchor_bid`. After a list item the new block
    is a sibling item; otherwise a paragraph. Returns (new_raw, new_bid)."""
    payload = _validate_payload(new_text)
    anchor = locate_block(text, anchor_bid)
    lines = _lines(text)
    new_bid = sb.mint_bid(set(existing_bids) | set(sb.BID_RE.findall(text)))

    if anchor.tag == "li":
        # mirror the anchor's own list marker style ("- " / "* " / "1. ")
        src = lines[anchor.end_line]
        indent = src[:len(src) - len(src.lstrip())]
        stripped = src.strip()
        if stripped[0].isdigit():
            lead = "%d. " % 2  # renderer renumbers <ol> itself; source number is cosmetic
        else:
            lead = stripped[0] + " "
        new_lines = [indent + lead + payload + " " + _marker(new_bid)]
        out = _splice(lines, anchor.end_line, anchor.end_line,
                      [lines[anchor.end_line]] + new_lines)
    else:
        new_lines = ["", payload + " " + _marker(new_bid)]
        out = _splice(lines, anchor.end_line, anchor.end_line,
                      [lines[anchor.end_line]] + new_lines)
        # keep a blank separator before whatever followed the anchor
        insert_end = anchor.end_line + len(new_lines)
        if insert_end + 1 <= len(out) - 1 and not _is_blank(out[insert_end + 1]):
            out.insert(insert_end + 1, "")

    new_raw = "\n".join(out)
    _verify(text, new_raw, added={new_bid}, removed=set())
    return new_raw, new_bid


def op_delete(text, bid):
    """Remove the block entirely (its bid is retired, never reused)."""
    blk = locate_block(text, bid)
    lines = _lines(text)
    out = _drop_with_separator(lines, blk.start_line, blk.end_line)
    new_raw = "\n".join(out)
    _verify(text, new_raw, added=set(), removed={bid})
    return new_raw


def op_split(text, bid, part1, part2, existing_bids):
    """Split one paragraph block into two at a caller-chosen point. The
    original bid stays on part1; part2 gets a fresh bid. The concatenated text
    must equal the original exactly (modulo the whitespace at the cut)."""
    p1 = _validate_payload(part1)
    p2 = _validate_payload(part2)
    blk = locate_block(text, bid)
    if blk.tag != "p":
        raise StructuralError("split applies to paragraphs only (got %s)" % blk.tag)
    if (p1 + " " + p2) != " ".join((blk.raw or "").split()) and \
            (p1 + p2).replace(" ", "") != (blk.raw or "").replace(" ", ""):
        raise StructuralError("split parts do not reconcatenate to the original")
    lines = _lines(text)
    new_bid = sb.mint_bid(set(existing_bids) | set(sb.BID_RE.findall(text)))
    replacement = [p1 + " " + _marker(bid), "", p2 + " " + _marker(new_bid)]
    out = _splice(lines, blk.start_line, blk.end_line, replacement)
    new_raw = "\n".join(out)
    _verify(text, new_raw, added={new_bid}, removed=set())
    return new_raw, new_bid


def op_merge(text, first_bid, second_bid):
    """Merge two ADJACENT same-tag paragraph blocks into one. The first bid
    survives; the second is retired. Text is concatenated exactly."""
    blocks = _blocks(text)
    idx = {b.bid: i for i, b in enumerate(blocks)}
    if first_bid not in idx or second_bid not in idx:
        raise StructuralError("unknown bid")
    a, b = blocks[idx[first_bid]], blocks[idx[second_bid]]
    if idx[second_bid] != idx[first_bid] + 1:
        raise StructuralError("merge requires adjacent blocks")
    if a.tag != b.tag or a.tag != "p":
        raise StructuralError("merge applies to two adjacent paragraphs")
    lines = _lines(text)
    merged = a.raw + " " + b.raw + " " + _marker(first_bid)
    out = _splice(lines, a.start_line, b.end_line, [merged])
    new_raw = "\n".join(out)
    _verify(text, new_raw, added=set(), removed={second_bid})
    return new_raw


def op_move(text, bid, dest_anchor_bid):
    """Move a block to sit immediately after `dest_anchor_bid`. No bid
    changes. Paragraph-level blocks only (li moves stay within their list)."""
    if bid == dest_anchor_bid:
        raise StructuralError("cannot move a block after itself")
    blk = locate_block(text, bid)
    locate_block(text, dest_anchor_bid)  # must exist before we cut
    lines = _lines(text)

    moved_lines = lines[blk.start_line:blk.end_line + 1]
    without = _drop_with_separator(lines, blk.start_line, blk.end_line)
    without_text = "\n".join(without)
    dest = locate_block(without_text, dest_anchor_bid)

    if blk.tag == "li":
        d = locate_block(without_text, dest_anchor_bid)
        if d.tag != "li":
            raise StructuralError("a list item may only move after a list item")
        out = _splice(without, d.end_line, d.end_line,
                      [without[d.end_line]] + moved_lines)
    else:
        out = _splice(without, dest.end_line, dest.end_line,
                      [without[dest.end_line], ""] + moved_lines)
        insert_end = dest.end_line + 1 + len(moved_lines)
        if insert_end + 1 <= len(out) - 1 and not _is_blank(out[insert_end + 1]):
            out.insert(insert_end + 1, "")

    new_raw = "\n".join(out)
    _verify(text, new_raw, added=set(), removed=set())
    return new_raw


def _verify(before, after, added, removed):
    """The correctness gate every op passes before returning: the bid multiset
    after == (before + added - removed), no bid duplicated, and the renderer
    parses the result (segmentation still sound — every block marked)."""
    b_bids = sb.BID_RE.findall(before)
    a_bids = sb.BID_RE.findall(after)
    if len(set(a_bids)) != len(a_bids):
        raise StructuralError("verification failed: duplicate bid after op")
    expect = (set(b_bids) | set(added)) - set(removed)
    if set(a_bids) != expect:
        raise StructuralError("verification failed: bid set mismatch")
    spans = []
    bs.markdown(after, spans=spans)
    unmarked = [s for s in spans if s["bid"] is None]
    if unmarked:
        raise StructuralError(
            "verification failed: op produced %d unmarked block(s)" % len(unmarked))
