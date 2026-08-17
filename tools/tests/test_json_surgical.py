#!/usr/bin/env python3
r"""Tests for tools/json_surgical.py — the formatting-preserving JSON scalar
writer (WP5, value-sync fast-follow) and its integration into apply_suggestions.

Property under test: a scalar/string edit produces a MINIMAL diff — only the
targeted value's bytes change; rewriting a value to itself is byte-identical;
and the surgical path is provably equivalent to parse->set->serialize (or it
declines via SurgicalError and the engine falls back). Includes a round-trip
sweep over the repo's REAL data/ JSON corpus.

Run:  python3 -m pytest tools/tests/test_json_surgical.py -q
  or: python3 tools/tests/test_json_surgical.py
"""
from __future__ import annotations

import difflib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

import json_surgical as js  # noqa: E402
import apply_suggestions as ap  # noqa: E402


def _changed_lines(a, b):
    """Count of +/- lines between two texts (excluding diff headers)."""
    out = []
    for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
        if l.startswith(("+++", "---")):
            continue
        if l.startswith(("+", "-")):
            out.append(l)
    return out


# --------------------------------------------------------------------------- #
# Parser / locate
# --------------------------------------------------------------------------- #
class LocateTest(unittest.TestCase):
    def test_locate_top_scalar(self):
        raw = '{\n  "a": "hello",\n  "b": 42\n}\n'
        s, e = js.locate(raw, "a")
        self.assertEqual(raw[s:e], '"hello"')
        s, e = js.locate(raw, "b")
        self.assertEqual(raw[s:e], "42")

    def test_locate_nested_object(self):
        raw = '{"outer": {"inner": {"leaf": "x"}}}'
        s, e = js.locate(raw, "outer.inner.leaf")
        self.assertEqual(raw[s:e], '"x"')

    def test_locate_array_index(self):
        raw = '{"xs": ["a", "b", "c"]}'
        s, e = js.locate(raw, "xs.1")
        self.assertEqual(raw[s:e], '"b"')

    def test_locate_array_of_objects(self):
        raw = '{"parties": [{"name": "Alice"}, {"name": "Bob"}]}'
        s, e = js.locate(raw, "parties.1.name")
        self.assertEqual(raw[s:e], '"Bob"')

    def test_locate_missing_key_raises(self):
        with self.assertRaises(js.SurgicalError):
            js.locate('{"a": 1}', "b")

    def test_locate_out_of_range_raises(self):
        with self.assertRaises(js.SurgicalError):
            js.locate('{"xs": [1, 2]}', "xs.5")

    def test_locate_descend_into_scalar_raises(self):
        with self.assertRaises(js.SurgicalError):
            js.locate('{"a": 1}', "a.b")

    def test_parse_rejects_trailing_garbage(self):
        with self.assertRaises(js.SurgicalError):
            js.parse('{"a": 1} extrajunk')

    def test_parse_rejects_nan_infinity(self):
        # stdlib json.loads accepts these; our surgical parser deliberately does
        # NOT — that is the clean SurgicalError -> fallback trigger.
        with self.assertRaises(js.SurgicalError):
            js.parse('{"a": NaN}')
        with self.assertRaises(js.SurgicalError):
            js.parse('{"a": Infinity}')


# --------------------------------------------------------------------------- #
# Minimal-diff splice
# --------------------------------------------------------------------------- #
class SpliceTest(unittest.TestCase):
    def test_insert_object_property_preserves_siblings(self):
        raw = '{\n  "date": "2026-01-02",\n  "packed": [1, 2]\n}\n'
        out = js.insert_object_properties(raw, [("", "date_day_zero", 1)])
        self.assertIn('"packed": [1, 2]', out)
        self.assertEqual(json.loads(out)["date_day_zero"], 1)

    def test_noop_is_byte_identical(self):
        raw = '{\n  "a": "hello",\n  "b": "world"\n}\n'
        self.assertEqual(js.splice_scalar(raw, "a", "hello"), raw)

    def test_single_edit_changes_one_value_only(self):
        raw = '{\n  "a": "hello",\n  "b": "world"\n}\n'
        out = js.splice_scalar(raw, "a", "HELLO")
        self.assertEqual(json.loads(out), {"a": "HELLO", "b": "world"})
        # everything except the "a" value byte-preserved
        self.assertEqual(_changed_lines(raw, out),
                         ['-  "a": "hello",', '+  "a": "HELLO",'])

    def test_compact_single_line_arrays_preserved(self):
        # The real-world case: hand-packed one-line arrays/objects that
        # json.dumps(indent=2) would explode. Surgery must leave them alone.
        raw = ('{\n'
               '  "caption": "Old Caption",\n'
               '  "party_names": ["Cloverdyke Dairy Cooperative"],\n'
               '  "parties": [\n'
               '    { "name": "Alice", "role": "buyer" }\n'
               '  ]\n'
               '}\n')
        out = js.splice_scalar(raw, "caption", "New Caption")
        self.assertIn('"party_names": ["Cloverdyke Dairy Cooperative"]', out)
        self.assertIn('{ "name": "Alice", "role": "buyer" }', out)
        self.assertEqual(len(_changed_lines(raw, out)), 2)  # one line swapped

    def test_string_needing_escaping(self):
        raw = '{"a": "plain"}'
        out = js.splice_scalar(raw, "a", 'has "quotes" and \\ and\ttab')
        self.assertEqual(json.loads(out)["a"], 'has "quotes" and \\ and\ttab')

    def test_newline_in_value_escaped_not_raw(self):
        raw = '{"body": "one line"}'
        out = js.splice_scalar(raw, "body", "line1\nline2")
        self.assertNotIn("\n", out[out.index('"body"'):])  # newline escaped inline
        self.assertEqual(json.loads(out)["body"], "line1\nline2")

    def test_unicode_raw_style_preserved(self):
        raw = '{"name": "café", "x": 1}'  # raw non-ascii -> ensure_ascii False
        out = js.splice_scalar(raw, "name", "résumé")
        self.assertIn('"résumé"', out)
        self.assertEqual(json.loads(out)["name"], "résumé")

    def test_unicode_escape_style_preserved(self):
        raw = '{"name": "caf\\u00e9", "x": 1}'  # ascii-escaped -> ensure_ascii True
        self.assertTrue(js.detect_ensure_ascii(raw))
        out = js.splice_scalar(raw, "name", "résumé")
        self.assertIn("\\u", out)                      # kept ascii-escape style
        self.assertNotIn("résumé", out)
        self.assertEqual(json.loads(out)["name"], "résumé")

    def test_number_bool_null_values(self):
        raw = '{"n": 1, "f": 1.5, "b": true, "z": null}'
        out = js.splice_scalars(raw, [("n", 7), ("f", 2.25),
                                       ("b", False), ("z", None)])
        self.assertEqual(json.loads(out), {"n": 7, "f": 2.25, "b": False, "z": None})

    def test_edit_a_number_scalar_minimal(self):
        raw = '{\n  "amount": 8400,\n  "note": "keep"\n}\n'
        out = js.splice_scalar(raw, "amount", 15000)
        self.assertEqual(_changed_lines(raw, out),
                         ['-  "amount": 8400,', '+  "amount": 15000,'])

    def test_multiple_edits_descending_splice(self):
        raw = '{\n  "a": "1",\n  "b": "2",\n  "c": "3"\n}\n'
        out = js.splice_scalars(raw, [("a", "AA"), ("c", "CC")])
        self.assertEqual(json.loads(out), {"a": "AA", "b": "2", "c": "CC"})
        self.assertIn('"b": "2"', out)   # untouched middle preserved verbatim

    def test_container_target_rejected(self):
        with self.assertRaises(js.SurgicalError):
            js.splice_scalar('{"a": [1, 2]}', "a", "x")

    def test_empty_edits_returns_raw(self):
        raw = '{"a": 1}'
        self.assertEqual(js.splice_scalars(raw, []), raw)

    def test_duplicate_key_uses_last_like_json(self):
        # json.loads is last-wins; locate navigates to the LAST occurrence so the
        # safety gate (json.loads(out) == expected) holds.
        raw = '{"a": "first", "a": "second"}'
        out = js.splice_scalar(raw, "a", "third")
        self.assertEqual(json.loads(out), {"a": "third"})
        self.assertIn('"first"', out)  # only the last occurrence was spliced


# --------------------------------------------------------------------------- #
# Integration with apply_suggestions.write_json_edits (surgical + fallback)
# --------------------------------------------------------------------------- #
class WriteJsonEditsTest(unittest.TestCase):
    def test_surgical_path_is_minimal(self):
        raw = ('{\n'
               '  "caption": "X",\n'
               '  "party_names": ["A", "B"],\n'
               '  "amount": 100\n'
               '}\n')
        out = ap.write_json_edits(raw, [("caption", "Y")])
        self.assertIn('"party_names": ["A", "B"]', out)  # compact array preserved
        self.assertEqual(len(_changed_lines(raw, out)), 2)

    def test_fallback_when_surgical_declines(self):
        # NaN forces SurgicalError in the parser; write_json_edits must fall back
        # to v1 parse->set->serialize and STILL apply the edit correctly.
        raw = '{"a": "old", "weird": NaN}'
        out = ap.write_json_edits(raw, [("a", "new")])
        obj = json.loads(out)
        self.assertEqual(obj["a"], "new")
        self.assertNotEqual(out, raw)  # fallback produced valid output

    def test_fallback_output_matches_v1_object(self):
        raw = '{"a": "old", "weird": Infinity}'
        out = ap.write_json_edits(raw, [("a", "new")])
        # equals the v1 whole-file result exactly (fallback path)
        obj = json.loads(raw)
        ap.json_set(obj, "a", "new")
        self.assertEqual(out, ap.dump_json_like(obj, raw))

    def test_prose_json_body_string_edit_minimal(self):
        # A markdown body embedded as a JSON string field: editing it should
        # touch only that value's line, not the rest of the file.
        raw = ('{\n'
               '  "id": "m03",\n'
               '  "sections": {"intro": {"body_md": "The retainer is $8,400 total."}},\n'
               '  "tags": ["a", "b"]\n'
               '}\n')
        body = ap.json_get(json.loads(raw), "sections.intro.body_md")
        new_body = body.replace("$8,400", "$9,100")
        out = ap.write_json_edits(raw, [("sections.intro.body_md", new_body)])
        self.assertEqual(json.loads(out)["sections"]["intro"]["body_md"],
                         "The retainer is $9,100 total.")
        self.assertIn('"tags": ["a", "b"]', out)  # sibling compact array untouched
        self.assertEqual(len(_changed_lines(raw, out)), 2)


# --------------------------------------------------------------------------- #
# Round-trip sweep over the REAL data/ corpus (the strong test)
# --------------------------------------------------------------------------- #
def _iter_data_json():
    data = os.path.join(REPO, "data")
    for dp, _dn, fns in os.walk(data):
        for fn in fns:
            if fn.endswith(".json"):
                yield os.path.join(dp, fn)


def _scalar_paths(obj, prefix=""):
    """Yield dotted paths to scalar/string leaves (json_get/json_set semantics)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "." in k:      # dotted keys aren't addressable by this path scheme
                continue
            yield from _scalar_paths(v, "%s.%s" % (prefix, k) if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _scalar_paths(v, "%s.%d" % (prefix, i))
    else:
        if prefix:
            yield prefix, obj


class RealCorpusRoundTripTest(unittest.TestCase):
    def test_every_data_json_noop_is_byte_identical(self):
        """Rewrite EVERY scalar leaf to itself in EVERY real data/ JSON file and
        assert byte-identity — the strongest guarantee that surgery never
        perturbs untouched formatting."""
        files = list(_iter_data_json())
        self.assertGreater(len(files), 100, "expected the full data corpus")
        checked_files = 0
        checked_scalars = 0
        for p in files:
            raw = open(p, encoding="utf-8").read()
            obj = json.loads(raw)
            leaves = list(_scalar_paths(obj))
            if not leaves:
                continue
            checked_files += 1
            # Splice EVERY scalar leaf to itself in one call: navigates every
            # path and must return byte-identical raw (no-op skip preserves all
            # source formatting, incl. non-canonical numbers like 207.50).
            out = js.splice_scalars(raw, leaves)
            self.assertEqual(out, raw, "noop rewrite changed bytes in %s" % p)
            checked_scalars += len(leaves)
        self.assertGreater(checked_files, 100)
        self.assertGreater(checked_scalars, 1000)

    def test_single_string_edit_is_minimal_across_corpus(self):
        """For each real file with a top-level string scalar, edit it and assert
        the surgical diff is far smaller than the v1 whole-file reserialize."""
        wins = 0
        for p in _iter_data_json():
            raw = open(p, encoding="utf-8").read()
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                continue
            target = next(((k, v) for k, v in obj.items() if isinstance(v, str)), None)
            if not target:
                continue
            key, val = target
            surg = js.splice_scalar(raw, key, val + " Z")
            surg_lines = len(_changed_lines(raw, surg))
            # surgical edit of one string scalar must touch <= 2 diff lines
            self.assertLessEqual(surg_lines, 2,
                                 "%s#%s surgical edit touched %d lines"
                                 % (p, key, surg_lines))
            # and must be strictly better-or-equal vs the v1 whole-file path
            o2 = json.loads(raw)
            ap.json_set(o2, key, val + " Z")
            v1_lines = len(_changed_lines(raw, ap.dump_json_like(o2, raw)))
            self.assertLessEqual(surg_lines, v1_lines)
            if surg_lines < v1_lines:
                wins += 1
        # the corpus is hand-formatted; surgery should win big on many files
        self.assertGreater(wins, 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
