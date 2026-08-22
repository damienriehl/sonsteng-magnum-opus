#!/usr/bin/env python3
r"""Tests for tools/editor_consistency.py — the Inconsistency checker (U10, R12).

Pure-logic + orchestration tests: NO live model, NO network, NO real editor
map. The map loader, facts loaders, CLI runner and flag filer are injected.
Pinned here:
  * a changed fact (old -> new) with prose still carrying OLD -> exactly one
    stale-value flag, comment begins "Fact check — ", names BOTH repair routes,
    targets the right source_ref;
  * a clean (untouched) matter yields ZERO flags — the plan's
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

    def test_day_zero_machine_fields_are_not_facts(self):
        rows = ec.fact_rows_from_data(
            MATTER_REL,
            {
                "open_date": "2026-01-05",
                "open_date_day_zero_offset": 0,
            },
            BIZ_REL,
            {
                "engagement": {
                    "engagement_date": "2026-01-09",
                    "engagement_date_day_zero_offset": 4,
                },
            },
        )

        paths = {path for _relpath, path, _value in rows}
        self.assertIn("open_date", paths)
        self.assertIn("engagement.engagement_date", paths)
        self.assertNotIn("open_date_day_zero_offset", paths)
        self.assertNotIn("engagement.engagement_date_day_zero_offset", paths)

    def test_custom_fact_day_zero_offsets_only_hide_machine_integers(self):
        rows = ec.fact_rows_from_data(
            MATTER_REL,
            {
                "custom_facts": {
                    "hearing_date_day_zero_offset": 12,
                    "authored_day_zero_offset": "Twelve days after filing",
                },
            },
        )

        facts = {path: value for _relpath, path, value in rows}
        self.assertNotIn(
            "custom_facts.hearing_date_day_zero_offset", facts)
        self.assertEqual(
            facts["custom_facts.authored_day_zero_offset"],
            "Twelve days after filing",
        )

    def test_prose_carrying_new_value_not_flagged(self):
        flags = self._flags([_block("memo.md#b:cccc",
                                    "Now captioned Osgard v. Northland Freight.")])
        self.assertEqual(flags, [])

    def test_unchanged_fact_never_flags(self):
        changed = ec.diff_fact_rows(self.NEW, self.NEW)
        self.assertEqual(changed, [])

    def test_substring_rename_flags_old_but_not_new(self):
        changed = ec.diff_fact_rows(
            [(MATTER_REL, "client_name", "Marceline Osgard")],
            [(MATTER_REL, "client_name", "Marceline Osgard-Smith")])
        flags = ec.stale_value_flags(changed, [
            _block("old.md#b:aaaa", "Marceline Osgard signed the affidavit."),
            _block("new.md#b:bbbb", "Marceline Osgard-Smith signed the affidavit."),
        ])
        self.assertEqual([f["source_ref"] for f in flags], [
            "data/matters/%s/old.md#b:aaaa" % SLUG])

    def test_short_old_values_skipped(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "n", "42")],
                                    [(MATTER_REL, "n", "43")])
        self.assertEqual(changed, [])

    def test_short_numeric_currency_is_safe_but_bare_number_is_not(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "filing_fee", "250")],
                                    [(MATTER_REL, "filing_fee", "275")])
        flags = ec.stale_value_flags(changed, [
            _block("currency.md#b:aaaa", "The filing fee is $250."),
            _block("bare.md#b:bbbb", "There were 250 pages in the record."),
        ])
        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0]["source_ref"].endswith("currency.md#b:aaaa"))

    def test_short_percentage_with_explicit_marker_is_safe(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "interest_rate", "25%")],
                                    [(MATTER_REL, "interest_rate", "30%")])
        flags = ec.stale_value_flags(
            changed, [_block("rate.md#b:aaaa", "Interest accrued at 25%.")])
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["old_literal"], "25%")

    def test_short_bare_numeric_requires_nearby_label_or_unit(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "filing_fee", "250")],
                                    [(MATTER_REL, "filing_fee", "275")])
        flags = ec.stale_value_flags(changed, [
            _block("label.md#b:aaaa", "The filing fee remains 250."),
            _block("unit.md#b:bbbb", "The charge remains 250 dollars."),
            _block("far.md#b:cccc", "Filing fee. " + ("x" * 60) +
                                     " The record has 250 pages."),
        ])
        self.assertEqual({f["old_literal"] for f in flags}, {"250"})
        self.assertEqual(len(flags), 2)

    def test_short_numeric_with_full_label_is_safe(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "filing_fee", "250")],
                                    [(MATTER_REL, "filing_fee", "275")])
        flags = ec.stale_value_flags(
            changed, [_block("fee.md#b:aaaa", "The filing fee remains 250.")])
        self.assertEqual(len(flags), 1)

    def test_written_date_matches_changed_iso_fact(self):
        changed = ec.diff_fact_rows(
            [(MATTER_REL, "hearing_date", "2026-02-16")],
            [(MATTER_REL, "hearing_date", "2026-03-02")])
        flags = ec.stale_value_flags(
            changed, [_block("memo.md#b:date", "Hearing: February 16, 2026.")])
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["old_literal"], "February 16, 2026")

    def test_formatted_integer_and_currency_match_numeric_fact(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "fee", 2500)],
                                    [(MATTER_REL, "fee", 2750)])
        flags = ec.stale_value_flags(changed, [
            _block("plain.md#b:aaaa", "The fee remains 2,500 dollars."),
            _block("money.md#b:bbbb", "The fee remains $2,500."),
        ])
        self.assertEqual({f["old_literal"] for f in flags}, {"2,500", "$2,500"})

    def test_integer_does_not_match_prefix_of_decimal(self):
        changed = ec.diff_fact_rows([(MATTER_REL, "fee", 2500)],
                                    [(MATTER_REL, "fee", 2750)])
        flags = ec.stale_value_flags(
            changed, [_block("money.md#b:bbbb", "The amount is $2,500.50.")])
        self.assertEqual(flags, [])

    def test_json_scalar_blocks_excluded(self):
        # the facts page's own value blocks are the facts, not restatements
        bundle = _bundle([_block("matter.json#caption",
                                 "Osgard v. Meridian Freight", kind="json_scalar")])
        self.assertEqual(ec.blocks_for_matter(bundle, SLUG), [])


class TestDeliberatePageOverrides(unittest.TestCase):
    FACT_REF = MATTER_REL + "#caption"
    OVERRIDE_REF = "data/copy/home.json#overrides.aaaaaaaaaaaaaaaa.value"
    CHANGED = [{"fact_path": FACT_REF, "label": "Caption",
                "old": "Osgard v. Meridian Freight",
                "new": "Osgard v. Northland Freight"}]
    BLOCK = {"source_ref": OVERRIDE_REF, "kind": "json_scalar",
             "original_text": "Osgard v. Meridian Freight"}
    RECORD = {"intent": "deliberate_page_override",
              "page": "matters/m03-tort-meridian/index.html",
              "shared_source_ref": FACT_REF,
              "source_ref": OVERRIDE_REF,
              "value": "Osgard v. Meridian Freight"}

    def test_matching_recorded_override_is_silent(self):
        flags = ec.stale_value_flags(self.CHANGED, [self.BLOCK], [self.RECORD])
        self.assertEqual(flags, [])

    def test_map_record_associates_override_block_with_its_matter(self):
        bundle = {"pages": {self.RECORD["page"]: [self.BLOCK]},
                  "overrides": [self.RECORD]}
        self.assertEqual(ec.blocks_for_matter(bundle, SLUG), [self.BLOCK])

    def test_checker_run_consumes_map_record_as_evidence(self):
        bundle = {"pages": {self.RECORD["page"]: [self.BLOCK]},
                  "overrides": [self.RECORD]}
        res = _run(
            since="BASE..HEAD", map_loader=lambda: bundle,
            facts_loader=lambda slug: [(MATTER_REL, "caption", self.CHANGED[0]["new"])],
            old_facts_loader=lambda slug: [(MATTER_REL, "caption", self.CHANGED[0]["old"])],
            flag_filer=lambda payload: (True, 200))
        self.assertEqual(res.stale_flags, [])

    def test_same_divergence_without_record_is_reported(self):
        flags = ec.stale_value_flags(self.CHANGED, [self.BLOCK], [])
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["source_ref"], self.OVERRIDE_REF)

    def test_record_for_another_leaf_does_not_suppress(self):
        record = dict(self.RECORD, shared_source_ref=MATTER_REL + "#client_name")
        self.assertEqual(len(ec.stale_value_flags(
            self.CHANGED, [self.BLOCK], [record])), 1)

    def test_record_whose_value_does_not_match_does_not_suppress(self):
        record = dict(self.RECORD, value="Unrelated page text")
        self.assertEqual(len(ec.stale_value_flags(
            self.CHANGED, [self.BLOCK], [record])), 1)


# --------------------------------------------------------------------------- #
# --since is mandatory
# --------------------------------------------------------------------------- #
class TestSinceRequired(unittest.TestCase):
    def test_no_since_refuses_without_loading_map(self):
        called = []
        buf = io.StringIO()
        res = _run(map_loader=lambda: called.append(True), out=buf)
        self.assertEqual(res.model_degraded, "missing_since")
        self.assertEqual(called, [])
        self.assertIn("requires --since", buf.getvalue())

    def test_main_without_since_exits_nonzero(self):
        self.assertNotEqual(ec.main(["--dry-run", "--no-model"]), 0)


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


class TestCatchPowerCanary(unittest.TestCase):
    """The check that would have caught the shipped-inert checker: perturb a
    fact that IS restated in prose and assert the required --since path flags
    it, while an invalid base refuses with a failing process status."""

    def _blocks(self, *texts):
        return [{"source_ref": "data/matters/m03-tort-meridian/case-file/a.md#b0000000%d" % i,
                 "kind": "prose", "original_text": t} for i, t in enumerate(texts)]

    def test_since_path_flags_a_restated_iso_date(self):
        old_rows = [("data/matters/m03-tort-meridian/matter.json", "open_date", "2025-02-13")]
        new_rows = [("data/matters/m03-tort-meridian/matter.json", "open_date", "2025-03-01")]
        blocks = self._blocks("The file was opened on 2025-02-13 and calendared.")
        flags = ec.stale_value_flags(ec.diff_fact_rows(old_rows, new_rows), blocks)
        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0]["message"].startswith(ec.FACT_PREFIX))

    def test_long_form_dates_are_recognized_as_the_same_date(self):
        # the corpus writes dates as prose; an ISO-only matcher was blind to it
        self.assertIn("February 13, 2025", ec.date_forms("2025-02-13"))
        self.assertNotIn("February 14, 2025", ec.date_forms("2025-02-13"))

    def test_unresolvable_since_refuses_instead_of_reporting_clean(self):
        import io
        buf = io.StringIO()
        res = ec.run(map_loader=lambda: {"pages": {}}, since="no-such-rev-xyz..HEAD",
                     dry_run=True, no_model=True, out=buf)
        self.assertEqual(res.model_degraded, "bad_since")
        self.assertIn("refusing to run", buf.getvalue())

    def test_unresolvable_since_exits_nonzero(self):
        self.assertNotEqual(
            ec.main(["--since", "no-such-rev-xyz..HEAD", "--dry-run", "--no-model"]),
            0)
