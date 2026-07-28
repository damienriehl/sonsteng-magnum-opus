#!/usr/bin/env python3
r"""Tests for tools/editor_consistency.py — the Inconsistency checker (U10, R12).

Pure-logic + orchestration tests: NO live model, NO network, NO real editor
map. The map loader, facts loaders, CLI runner and flag filer are injected.
Pinned here:
  * a changed fact (old -> new) with prose still carrying OLD -> exactly one
    stale-value flag, comment begins "Fact check — ", names BOTH repair routes,
    targets the right source_ref;
  * a clean (untouched) matter yields ZERO flags in both modes — the plan's
    zero-false-flags verification;
  * model flags parse from the CLI JSON envelope and begin "AI guess — ";
    malformed model output degrades to zero model flags, no crash;
  * ids are deterministic over (source_ref, fact_path, old_literal) — same
    input twice -> same ids (re-runs never duplicate);
  * --dry-run files nothing;
  * the --since fact diff works against a real tmp git repo.

Run:  python3 -m pytest tools/tests/test_editor_consistency.py -q
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import editor_consistency as ec  # noqa: E402

SLUG = "m03-tort-meridian"
MATTER_REL = "data/matters/%s/matter.json" % SLUG
BIZ_REL = "data/matters/%s/business/business.json" % SLUG


def _block(ref_suffix, text, kind="prose"):
    return {"source_ref": "data/matters/%s/%s" % (SLUG, ref_suffix),
            "kind": kind, "original_text": text}


def _bundle(blocks):
    return {"pages": {"matters/%s/index.html" % SLUG: blocks}}


def _run(**kw):
    defaults = dict(api_base="https://x/edit/v1", token="tok", matter=SLUG,
                    no_model=True, out=io.StringIO())
    defaults.update(kw)
    return ec.run(**defaults)


# --------------------------------------------------------------------------- #
# Stale value (--since mode)
# --------------------------------------------------------------------------- #
class TestStaleValue(unittest.TestCase):
    OLD = [(MATTER_REL, "caption", "Osgard v. Meridian Freight")]
    NEW = [(MATTER_REL, "caption", "Osgard v. Northland Freight")]

    def _flags(self, blocks):
        changed = ec.diff_fact_rows(self.OLD, self.NEW)
        return ec.stale_value_flags(changed, blocks)

    def test_seeded_stale_value_yields_exactly_one_flag(self):
        blocks = [
            _block("memo.md#b:aaaa", "The caption Osgard v. Meridian Freight "
                                     "controls this exercise."),
            _block("memo.md#b:bbbb", "An unrelated paragraph about discovery."),
        ]
        flags = self._flags(blocks)
        self.assertEqual(len(flags), 1)
        f = flags[0]
        self.assertEqual(f["source_ref"],
                         "data/matters/%s/memo.md#b:aaaa" % SLUG)
        self.assertTrue(f["message"].startswith("Fact check — "))
        # both repair routes, in plain words
        self.assertIn("editing the Fact on the Facts page", f["message"])
        self.assertIn("editing this paragraph", f["message"])
        self.assertIn("Osgard v. Meridian Freight", f["message"])   # old
        self.assertIn("Osgard v. Northland Freight", f["message"])  # new

    def test_prose_carrying_new_value_not_flagged(self):
        flags = self._flags([_block("memo.md#b:cccc",
                                    "Now captioned Osgard v. Northland Freight.")])
        self.assertEqual(flags, [])

    def test_unchanged_fact_never_flags(self):
        changed = ec.diff_fact_rows(self.NEW, self.NEW)
        self.assertEqual(changed, [])

    def test_old_substring_of_new_is_skipped(self):
        # containing new implies containing old -> would false-flag; skipped.
        changed = ec.diff_fact_rows(
            [(MATTER_REL, "caption", "Osgard v. Freight")],
            [(MATTER_REL, "caption", "Osgard v. Freight Lines")])
        self.assertEqual(changed, [])

    def test_short_old_values_skipped(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "n", "42")],
                                    [(MATTER_REL, "n", "43")])
        self.assertEqual(changed, [])

    def test_json_scalar_blocks_excluded(self):
        # the facts page's own value blocks are the facts, not restatements
        bundle = _bundle([_block("matter.json#caption",
                                 "Osgard v. Meridian Freight", kind="json_scalar")])
        self.assertEqual(ec.blocks_for_matter(bundle, SLUG), [])


# --------------------------------------------------------------------------- #
# Fallback (no --since): dates + money, unambiguous only
# --------------------------------------------------------------------------- #
class TestFallbackMismatch(unittest.TestCase):
    FACTS = [(BIZ_REL, "intake.intake_date", "2025-02-13"),
             (BIZ_REL, "engagement.engagement_date", "2025-02-14")]

    def test_labelled_wrong_date_flagged(self):
        blocks = [_block("notes.md#b:aaaa",
                         "The intake date recorded was 2025-03-01.")]
        flags = ec.fallback_mismatch_flags(self.FACTS, blocks)
        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0]["message"].startswith("Fact check — "))
        self.assertIn("2025-03-01", flags[0]["message"])
        self.assertIn("2025-02-13", flags[0]["message"])

    def test_matching_date_not_flagged(self):
        blocks = [_block("notes.md#b:aaaa",
                         "The intake date recorded was 2025-02-13.")]
        self.assertEqual(ec.fallback_mismatch_flags(self.FACTS, blocks), [])

    def test_two_literals_ambiguous_not_flagged(self):
        blocks = [_block("notes.md#b:aaaa",
                         "Intake date 2025-03-01 or maybe 2025-03-02.")]
        self.assertEqual(ec.fallback_mismatch_flags(self.FACTS, blocks), [])

    def test_generic_one_word_label_never_participates(self):
        # as_of_date -> distinctive words {"date"} only: too generic — an
        # untouched exhibit mentioning any other date must NOT be flagged
        # (this exact false flag fired on m01's repair logs before the guard).
        facts = [(MATTER_REL, "as_of_date", "2026-06-30")]
        blocks = [_block("case-file/exh-004.md#b:aaaa",
                         "Repair log date of service: 2025-09-22.")]
        self.assertEqual(ec.fallback_mismatch_flags(facts, blocks), [])

    def test_no_label_words_not_flagged(self):
        blocks = [_block("notes.md#b:aaaa",
                         "Some unrelated deadline is 2025-03-01.")]
        self.assertEqual(ec.fallback_mismatch_flags(self.FACTS, blocks), [])

    def test_clean_matter_zero_flags_end_to_end(self):
        # the plan's verification: an untouched matter files NOTHING
        blocks = [
            _block("notes.md#b:aaaa", "The intake date was 2025-02-13, as filed."),
            _block("notes.md#b:bbbb", "Plain narrative with no literals at all."),
        ]
        filed = []
        res = _run(map_loader=lambda: _bundle(blocks),
                   facts_loader=lambda slug: self.FACTS,
                   flag_filer=lambda p: (filed.append(p) or (True, 200)))
        self.assertEqual(res.stale_flags, [])
        self.assertEqual(res.model_flags, [])
        self.assertEqual(filed, [])


# --------------------------------------------------------------------------- #
# Model pass (contradictions)
# --------------------------------------------------------------------------- #
class TestModelPass(unittest.TestCase):
    BLOCKS = [_block("memo.md#b:aaaa", "The defendant admits liability.")]
    FACTS = [(MATTER_REL, "caption", "Osgard v. Meridian Freight")]

    def test_model_flags_prefixed_ai_guess(self):
        raw = json.dumps({"flags": [
            {"source_ref": self.BLOCKS[0]["source_ref"],
             "message": "The facts say liability is contested."}]})
        flags, degraded = ec.model_contradiction_flags(
            SLUG, self.FACTS, self.BLOCKS, cli_runner=lambda p: (True, raw, None))
        self.assertIsNone(degraded)
        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0]["message"].startswith("AI guess — "))
        self.assertIn("editing the Fact on the Facts page", flags[0]["message"])
        self.assertIn("editing this paragraph", flags[0]["message"])

    def test_cli_json_envelope_parsed(self):
        inner = json.dumps({"flags": [
            {"source_ref": self.BLOCKS[0]["source_ref"], "message": "m"}]})
        raw = json.dumps({"type": "result", "result": inner})
        flags, _ = ec.model_contradiction_flags(
            SLUG, self.FACTS, self.BLOCKS, cli_runner=lambda p: (True, raw, None))
        self.assertEqual(len(flags), 1)

    def test_malformed_response_degrades_to_zero_flags(self):
        flags, degraded = ec.model_contradiction_flags(
            SLUG, self.FACTS, self.BLOCKS,
            cli_runner=lambda p: (True, "utter nonsense {broken", None))
        self.assertEqual(flags, [])
        self.assertIsNone(degraded)  # parsed-to-nothing, not a crash

    def test_cli_missing_degrades_with_reason(self):
        flags, degraded = ec.model_contradiction_flags(
            SLUG, self.FACTS, self.BLOCKS,
            cli_runner=lambda p: (False, "", "cli_not_found"))
        self.assertEqual(flags, [])
        self.assertEqual(degraded, "cli_not_found")

    def test_invented_source_ref_dropped(self):
        raw = json.dumps({"flags": [
            {"source_ref": "data/matters/%s/NOT-A-BLOCK.md#x" % SLUG,
             "message": "m"}]})
        flags, _ = ec.model_contradiction_flags(
            SLUG, self.FACTS, self.BLOCKS, cli_runner=lambda p: (True, raw, None))
        self.assertEqual(flags, [])

    def test_no_model_skips_cli_entirely(self):
        called = []
        _run(map_loader=lambda: _bundle(self.BLOCKS),
             facts_loader=lambda slug: self.FACTS,
             cli_runner=lambda p: (called.append(p) or (True, '{"flags":[]}', None)),
             flag_filer=lambda p: (True, 200), no_model=True)
        self.assertEqual(called, [])


# --------------------------------------------------------------------------- #
# Idempotency + payload shape + dry-run
# --------------------------------------------------------------------------- #
class TestFilingDiscipline(unittest.TestCase):
    def _stale_run(self, **kw):
        blocks = [_block("memo.md#b:aaaa", "Still says Meridian Freight here.")]
        return _run(
            since="BASE..HEAD",
            map_loader=lambda: _bundle(blocks),
            facts_loader=lambda slug: [(MATTER_REL, "caption", "Northland Freight")],
            old_facts_loader=lambda slug: [(MATTER_REL, "caption", "Meridian Freight")],
            **kw)

    def test_same_input_twice_same_ids(self):
        a = self._stale_run(flag_filer=lambda p: (True, 200))
        b = self._stale_run(flag_filer=lambda p: (True, 200))
        self.assertEqual([p["id"] for p in a.payloads],
                         [p["id"] for p in b.payloads])
        self.assertEqual(len(a.payloads), 1)

    def test_payload_is_comment_only_ai_rewrite(self):
        res = self._stale_run(flag_filer=lambda p: (True, 200))
        p = res.payloads[0]
        self.assertEqual(p["origin"], "ai_rewrite")
        self.assertNotIn("new_text", p)
        self.assertTrue(p["comment"].startswith("Fact check — "))
        self.assertRegex(p["id"], r"^[a-zA-Z0-9_-]{8,64}$")

    def test_id_varies_by_each_component(self):
        base = ec.consistency_flag_id("ref", "fact", "old")
        self.assertNotEqual(base, ec.consistency_flag_id("ref2", "fact", "old"))
        self.assertNotEqual(base, ec.consistency_flag_id("ref", "fact2", "old"))
        self.assertNotEqual(base, ec.consistency_flag_id("ref", "fact", "old2"))

    def test_dry_run_files_nothing(self):
        filed = []
        res = self._stale_run(dry_run=True,
                              flag_filer=lambda p: (filed.append(p) or (True, 200)))
        self.assertEqual(res.filed, 0)
        self.assertEqual(filed, [])
        self.assertEqual(len(res.payloads), 1)  # the plan is still returned

    def test_filing_best_effort_bad_ref_skipped(self):
        res = self._stale_run(flag_filer=lambda p: (False, "http_400"))
        self.assertEqual(res.filed, 0)  # skipped, no crash


# --------------------------------------------------------------------------- #
# --since against a real tmp git repo
# --------------------------------------------------------------------------- #
def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class TestGitFactDiff(unittest.TestCase):
    def test_changed_fact_detected_across_commits(self):
        root = tempfile.mkdtemp(prefix="consistency-fixture-")
        try:
            _git(["init", "-q"], root)
            _git(["config", "user.email", "t@t.local"], root)
            _git(["config", "user.name", "t"], root)
            _git(["config", "commit.gpgsign", "false"], root)
            mdir = os.path.join(root, "data", "matters", SLUG)
            os.makedirs(mdir)
            mpath = os.path.join(mdir, "matter.json")
            with open(mpath, "w", encoding="utf-8") as fh:
                json.dump({"id": "m03", "caption": "Old Caption Here"}, fh)
            _git(["add", "-A"], root)
            _git(["commit", "-q", "-m", "base"], root)
            with open(mpath, "w", encoding="utf-8") as fh:
                json.dump({"id": "m03", "caption": "New Caption Here"}, fh)
            old_rows = ec.load_fact_rows_at(root, SLUG, "HEAD")
            new_rows = ec.load_fact_rows(root, SLUG)
            changed = ec.diff_fact_rows(old_rows, new_rows)
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["old"], "Old Caption Here")
            self.assertEqual(changed[0]["new"], "New Caption Here")
            # deny-listed 'id' never becomes a fact row
            self.assertTrue(all("#id" not in c["fact_path"] for c in changed))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_base_rev_parsing(self):
        self.assertEqual(ec.base_rev("A..B"), "A")
        self.assertEqual(ec.base_rev("A...B"), "A")
        self.assertEqual(ec.base_rev("HEAD~3"), "HEAD~3")
        self.assertIsNone(ec.base_rev(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
