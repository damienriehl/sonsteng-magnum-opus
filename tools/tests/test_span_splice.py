#!/usr/bin/env python3
r"""Unit tests for the WP7 formatted-block span-splice (editor apply v1.1).

The engine hands `span_splice(original_raw, new_plain)` the RAW markdown of a
formatted block plus the editor's PLAIN edit (markers dropped). It returns the
reconstructed raw markdown when every formatted span's rendered text is unchanged
and in order (splicing the changed plain segments between the untouched spans,
preserving each span's markup EXACTLY), or None when the splice can't be proven
safe (=> the apply engine routes the block to needs_human).

These tests exercise the pure function directly (no git, no Worker). An
end-to-end run_apply integration test lives in test_apply_suggestions.py.

Run:  python3 -m pytest tools/tests/test_span_splice.py -q
  or: python3 tools/tests/test_span_splice.py
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import apply_suggestions as ap  # noqa: E402


class TokenizeTest(unittest.TestCase):
    def test_strip_matches_render_for_transformed_markup(self):
        self.assertEqual(
            ap.strip_inline_formatting("a **b** c *d* e `f` g"),
            "a b c d e f g")

    def test_links_and_underscores_render_literally(self):
        # build_site._inline does NOT transform links/__bold__/_italic_/[^fn];
        # they stay literal in the plain rendering, so they are NOT spans.
        s = "see [FOLIO](https://x) and __x__ and _y_ and [^1]"
        self.assertEqual(ap.strip_inline_formatting(s), s)
        self.assertEqual(
            [t for t in ap.tokenize_spans(s) if t[0] == "span"], [])

    def test_code_takes_priority_over_bold(self):
        # `a**b` is code; the inner ** is literal, not a bold span.
        toks = ap.tokenize_spans("x `a**b` y")
        spans = [t for t in toks if t[0] == "span"]
        self.assertEqual(spans, [("span", "`a**b`", "a**b")])


class HappyPathTest(unittest.TestCase):
    def _ok(self, raw, new_plain, expected):
        got = ap.span_splice(raw, new_plain)
        self.assertEqual(got, expected)
        # invariants the engine relies on
        self.assertEqual(ap.strip_inline_formatting(got), new_plain)
        self.assertEqual(
            [t[1] for t in ap.tokenize_spans(got) if t[0] == "span"],
            [t[1] for t in ap.tokenize_spans(raw) if t[0] == "span"])

    def test_edit_text_after_bold(self):
        self._ok("This has **bold emphasis** in it.",
                 "This has bold emphasis in the paragraph.",
                 "This has **bold emphasis** in the paragraph.")

    def test_edit_text_before_bold(self):
        self._ok("The **plaintiff** filed.",
                 "Yesterday the plaintiff filed.",
                 "Yesterday the **plaintiff** filed.")

    def test_edit_between_two_spans(self):
        self._ok("**Alpha** connects to *Beta* here.",
                 "Alpha now links to Beta here.",
                 "**Alpha** now links to *Beta* here.")

    def test_edit_around_code_span(self):
        self._ok("Run `git status` first.",
                 "Please run git status before committing.",
                 "Please run `git status` before committing.")

    def test_span_at_start(self):
        self._ok("**Note:** read this.",
                 "Note: read this carefully.",
                 "**Note:** read this carefully.")

    def test_span_at_end(self):
        self._ok("Signed by the **Registrar**",
                 "Duly signed by the Registrar",
                 "Duly signed by the **Registrar**")

    def test_adjacent_spans(self):
        self._ok("x **a**`b` y",   # bold 'a' immediately followed by code 'b'
                 "start ab finish",
                 "start **a**`b` finish")

    def test_link_only_block_whole_replace(self):
        # No transformed spans -> plain edit is the new block verbatim.
        self._ok("See [FOLIO](https://x) for the rule.",
                 "See [FOLIO](https://x) for the governing rule.",
                 "See [FOLIO](https://x) for the governing rule.")

    def test_unicode_around_span(self):
        self._ok("Café **naïveté** façade.",
                 "Café naïveté in the façade of São Paulo.",
                 "Café **naïveté** in the façade of São Paulo.")

    def test_idempotence_no_op(self):
        # new_plain == the original's plain rendering -> byte-identical original.
        raw = "A **bold** and a plain bold word."
        self._ok(raw, ap.strip_inline_formatting(raw), raw)

    def test_no_op_span_after_duplicate_plain(self):
        # The rendered word also appears as plain BEFORE the span; the diff must
        # keep the markup on the span, not the plain occurrence (no corruption).
        raw = "cat **cat**"
        self._ok(raw, "cat cat", raw)


class RejectionTest(unittest.TestCase):
    def _reject(self, raw, new_plain):
        self.assertIsNone(ap.span_splice(raw, new_plain))

    def test_span_text_edited(self):
        # 'plaintiff' -> 'defendant' inside the bold span: cannot preserve.
        self._reject("The **plaintiff** filed.", "The defendant filed.")

    def test_span_partially_edited(self):
        self._reject("Run `git status` now.", "Run git commit now.")

    def test_span_deleted(self):
        self._reject("This has **bold emphasis** here.",
                     "This has here.")

    def test_span_reordered(self):
        # Beta now precedes Alpha in the plain text -> order shifted.
        self._reject("**Alpha** then *Beta*.", "Beta then Alpha.")

    def test_duplicated_span_ambiguity(self):
        # Two spans with identical rendered text -> alignment uncertain -> decline,
        # even when the surrounding text is edited.
        self._reject("**cat** and **cat** sat.", "the cat and cat lounged.")

    def test_block_cleared(self):
        self._reject("Signed by the **Registrar**.", "")

    def test_new_plain_introduces_markup_no_span_block(self):
        # A link-only block whose plain edit accidentally introduces **markup**
        # must NOT be spliced blindly (spans would appear that weren't there).
        self._reject("See [FOLIO](https://x).", "See **FOLIO** now.")


class VerificationGateTest(unittest.TestCase):
    def test_result_round_trips_for_every_success(self):
        cases = [
            ("Hold **firm** now.", "Hold firm right now."),
            ("`code` matters.", "the code matters a lot."),
            ("A *b* C *d* E.", "A b then C and d then E."),
        ]
        for raw, new_plain in cases:
            got = ap.span_splice(raw, new_plain)
            self.assertIsNotNone(got, (raw, new_plain))
            # Gate 1: stripping formatting == the suggested plain text.
            self.assertEqual(ap.strip_inline_formatting(got), new_plain)
            # Gate 2: spans identical (set + order) to the original.
            self.assertEqual(
                [t[1] for t in ap.tokenize_spans(got) if t[0] == "span"],
                [t[1] for t in ap.tokenize_spans(raw) if t[0] == "span"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
