#!/usr/bin/env python3
"""Self-test for tools/validate_spine.py.

Two parts:

  1. Structural self-test — every instance in data/schemas/examples/ must
     validate against its schema (proves the validator agrees with the
     hand-authored contracts).

  2. Synthetic-matter self-test — build a THROWAWAY spine under a temp dir:
       * a complete m99 stub that passes everything (baseline PASS), then
       * 10 mutated copies, each tripping exactly one targeted check,
     and confirm the right ERROR fires (per-matter isolation via --matter m99).

Run:  python3 tools/tests/test_validate_spine.py
Exit code 0 = all self-tests passed.

Nothing is written into the repo; the temp spine lives under /tmp and is
removed on exit.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

import validate_spine as vs  # noqa: E402
import day_zero  # noqa: E402

SCHEMAS_DIR = REPO / "data" / "schemas"
EXAMPLES_DIR = SCHEMAS_DIR / "examples"


def test_taxonomy_contract_manifests_are_not_misclassified_as_entities():
    world = vs.discover(REPO / "data", None)
    assert len(world.skills) == 31
    assert len(world.tasks) == 108


def test_day_zero_artifacts_are_enforced_by_f29(tmp_path):
    build_base_spine(tmp_path)
    malformed = {
        "schema_version": "1.0.0",
        "description": "Malformed fixture: required review fields are absent.",
    }
    (tmp_path / "day-zero-holdouts.json").write_text(
        json.dumps(malformed, indent=2), encoding="utf-8"
    )

    report = run_validator(tmp_path, strict=True)
    failures = [
        finding for finding in report.findings
        if finding.check == "F29"
        and finding.severity == vs.ERROR
        and finding.detail.get("file", "").endswith("day-zero-holdouts.json")
    ]
    assert failures


def test_matter_date_offsets_are_enforced_by_f29(tmp_path):
    build_base_spine(tmp_path)
    path = tmp_path / "matters" / "m99-noncompete-meridian" / "date-offsets.json"
    path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "matter_id": "m99",
        "anchor": "not-a-date",
        "entries": [{
            "source": "facts.md",
            "block_id": "changed-or-invalid",
            "locator": 0,
            "literal": "January 1, 2026",
            "day_zero_offset": 0,
        }],
    }, indent=2), encoding="utf-8")

    report = run_validator(tmp_path, matter="m99", strict=True)
    failures = [
        finding for finding in report.for_scope("m99")
        if finding.check == "F29"
        and finding.severity == vs.ERROR
        and finding.detail.get("file", "").endswith("date-offsets.json")
    ]
    assert failures


# ===========================================================================
# Part 1 — schema-example structural self-test
# ===========================================================================

EXAMPLE_TYPES = {
    "matter.example.json": "matter",
    "persona.example.json": "persona",
    "rubric.example.json": "rubric",
    "exercise.example.json": "exercise",
    "business.example.json": "business",
    "firm.example.json": "firm",
    "skill.example.json": "skill",
    "task.example.json": "task",
    "debrief.scorecard.example.json": "debrief_scorecard",
    "critique.scorecard.example.json": "critique_scorecard",
}


def check_examples(schemas: vs.SchemaSet):  # NOT a pytest test — helper called from main();
    # renamed off the test_* prefix so bare `pytest tools/tests/` does not miscollect it
    # (its `schemas` arg would be resolved as a missing fixture). Standalone self-test only.
    rows = []
    ok = True
    for fname, etype in sorted(EXAMPLE_TYPES.items()):
        path = EXAMPLES_DIR / fname
        obj, err = vs.read_json(path)
        if err:
            rows.append((fname, "LOAD-FAIL", err))
            ok = False
            continue
        errs = schemas.validate(etype, obj)
        if errs:
            rows.append((fname, "SCHEMA-FAIL", "; ".join(f"{l}:{m}" for l, m in errs[:2])))
            ok = False
        else:
            rows.append((fname, "PASS", ""))
    return ok, rows


# ===========================================================================
# Part 2 — synthetic spine builder
# ===========================================================================

def filler(nwords: int, seed: str) -> str:
    """Deterministic realistic-ish filler of >= nwords words."""
    base = (
        f"This section for {seed} records the operative facts and the working "
        "posture of the matter in plain professional language. The parties "
        "dispute the sequence of events and the reasonable inferences that "
        "follow from the record as it presently stands before the tribunal. "
        "Counsel must weigh the documentary exhibits against the recollections "
        "of the several witnesses, none of whom agrees entirely with the others "
        "on timing, intent, or the precise words that were exchanged during the "
        "relevant meetings and telephone calls that anchor the timeline here. "
    )
    words = base.split()
    out = []
    i = 0
    while len(out) < nwords:
        out.append(words[i % len(words)])
        i += 1
    return " ".join(out)


def build_facts_md() -> str:
    lines = ["# Ground-truth facts — m99 (throwaway)\n"]
    para = (
        "The client retained the firm after a dispute arose over a departure "
        "from a prior employer and a contested restrictive covenant. "
    )
    body = []
    for n in range(1, 41):
        anchor = f"[m99.fact.{n:03d}]"
        body.append(
            f"On the material date the record establishes a discrete fact {anchor} "
            f"that the parties either concede or vigorously contest, and which the "
            f"student must surface through careful interviewing and document review "
            f"before advising the client about the realistic range of outcomes here. "
        )
    text = "\n".join(lines) + para * 6 + " ".join(body)
    # ensure within 1200-2500 words
    return text


def skill_entities():
    ents = {}
    for i, group in enumerate(vs.SURVEYED_SKILLS, start=1):
        name = sorted(group)[0]
        if i <= 17:
            sid = f"SK-LP-{i:02d}"
            cat = "legal_practice"
        else:
            sid = f"SK-PM-{i-17:02d}"
            cat = "practice_management"
        ents[sid] = {
            "id": sid,
            "schema_version": "1.0.0",
            "@id": f"https://sonsteng.damienriehl.com/spine/skill/{sid}",
            "name": name,
            "category": cat,
            "extension": False,
            "no_folio_equivalent": True,
        }
    return ents


def task_entity():
    return {
        "id": "TSK-001",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/task/TSK-001",
        "skill_id": "SK-LP-13",
        "name": "Conduct a settlement negotiation",
        "description": "Plan and conduct a negotiation identifying interests and BATNA.",
        "bloom_level": "synthesis",
        "module": "M2",
        "subtasks": [
            {"id": "TSK-001.01", "name": "Prepare a plan", "description": "Draft an SSNP."},
        ],
        "no_folio_equivalent": True,
        "exercise_refs": ["m99.ex"],
    }


def firm_entity():
    return {
        "id": "FIRM",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/firm/FIRM",
        "identity": {"name": "Testfixture & Stub LLP"},
        "timekeepers": [
            {"id": "FIRM-TK-01", "name": "A. Partner", "role": "partner", "rate": 300},
            {"id": "FIRM-TK-02", "name": "B. Associate", "role": "associate", "rate": 200},
        ],
        "rate_card": [
            {"role": "partner", "rate": 300},
            {"role": "associate", "rate": 200},
            {"role": "paralegal", "rate": 110},
        ],
        "clients": [{"id": "FIRM-C-99", "name": "Verranto Holdings"}],
        "book_of_business": [
            {"matter_id": "m99", "client_id": "FIRM-C-99", "fee_type": "hourly", "status": "active"},
        ],
        "closed_matters": [],
        "ar_aging": {"current": 1050, "days_1_30": 0, "days_31_60": 0, "days_61_90": 0, "days_over_90": 0},
        "realization_target": 0.9,
        "collection_target": 0.95,
        "budget": [
            {"id": "FIRM-B-01", "name": "Fee revenue", "budget": 100000, "actual": 90000, "variance_is_good": False},
        ],
        "as_of_date": "2026-06-30",
    }


def matter_entity():
    return {
        "id": "m99",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/matter/m99",
        "slug": "m99-noncompete-meridian",
        "shape": "non-compete",
        "tier": "meridian",
        "jurisdiction": "meridian",
        "caption": "Verranto Holdings v. Okwuosa",
        "sides": [
            {"role_id": "m99.role.plaintiff", "label": "Plaintiff / Employer", "party_names": ["Verranto Holdings"]},
            {"role_id": "m99.role.defendant", "label": "Defendant / Former Employee", "party_names": ["Adaeze Okwuosa"]},
        ],
        "parties": [
            {"name": "Verranto Holdings", "role": "plaintiff employer", "side_role_id": "m99.role.plaintiff"},
            {"name": "Adaeze Okwuosa", "role": "defendant employee", "side_role_id": "m99.role.defendant"},
        ],
        "client_id": "FIRM-C-99",
        "fee_type": "hourly",
        "skill_refs": ["SK-LP-13"],
        "task_refs": ["TSK-001"],
        "personas": ["m99.per.okwuosa", "m99.per.salvato"],
        "witnesses": [
            {"id": "m99.wit.delacroix", "name": "Renee Delacroix"},
            {"id": "m99.wit.ferngrove", "name": "Miles Ferngrove"},
            {"id": "m99.wit.halligan", "name": "Prisca Halligan"},
        ],
        "exhibits": [
            {"id": "m99.exh.001", "title": "Employment Agreement"},
            {"id": "m99.exh.002", "title": "Resignation Email"},
            {"id": "m99.exh.003", "title": "Customer List Export Log"},
            {"id": "m99.exh.004", "title": "Offer Letter from New Employer"},
            {"id": "m99.exh.005", "title": "HR Exit Interview Notes"},
        ],
        "open_date": "2026-01-12",
        "as_of_date": "2026-06-30",
    }


def client_persona():
    return {
        "id": "m99.per.okwuosa",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/persona/m99.per.okwuosa",
        "matter_id": "m99",
        "identity": {"name": "Adaeze Okwuosa", "age": 41, "role": "client",
                     "occupation": "sales engineer", "pronouns": "she/her"},
        "background": "Twelve years as a sales engineer before resigning to join a competitor.",
        "personality": "Direct and proud of a strong record.",
        "emotional_state": "Anxious about a lawsuit threatening a new job.",
        "communication_style": "Answers factual questions readily; guarded when blamed.",
        "objectives_fears": {"objectives": ["Keep the new job"], "fears": ["Legal fees"]},
        "disposition": "guarded",
        "interviewable_by": ["m99.role.defendant"],
        "represented_by_counsel": False,
        "rule_4_2": {"applies": False},
        "disclosure": {
            "volunteered": [{"fact_ref": "m99.fact.001", "text": "I signed a non-compete when I started."}],
            "revealed_if_asked": [{"fact_ref": "m99.fact.007", "text": "The new company is in the same market."}],
            "rapport_gated": [
                {"fact_ref": "m99.fact.021", "text": "I emailed a client list to my personal account.",
                 "min_turns": 6, "requires": ["confidentiality_reassurance", "nonjudgmental_response"]},
                {"fact_ref": "m99.fact.022", "text": "I kept a spreadsheet of pricing notes.",
                 "min_turns": 5, "requires": ["follow_up_on_hint"]},
            ],
            "concealed": [{"fact_ref": "m99.fact.030", "text": "I already started contacting former clients."}],
            "unknown": [{"fact_ref": "m99.fact.040", "text": "Whether the covenant is enforceable."}],
        },
        "knowledge_boundary": {
            "pinned_to_case_file": True,
            "unknown_response_style": "I don't remember / I'm not sure.",
            "color_topics": ["their weekend hiking hobby", "the long winter commute downtown"],
        },
    }


def witness_persona():
    return {
        "id": "m99.per.salvato",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/persona/m99.per.salvato",
        "matter_id": "m99",
        "identity": {"name": "Dominic Salvato", "age": 52, "role": "former supervisor",
                     "occupation": "regional manager", "pronouns": "he/him"},
        "background": "Supervised the client for six years at the prior employer.",
        "personality": "Measured, careful, institutionally loyal.",
        "emotional_state": "Reluctant to be drawn into litigation.",
        "communication_style": "Precise; volunteers little.",
        "objectives_fears": {"objectives": ["Stay out of the dispute"], "fears": ["Company retaliation"]},
        "disposition": "guarded",
        "interviewable_by": ["m99.role.plaintiff"],
        "represented_by_counsel": False,
        "rule_4_2": {"applies": False},
        "disclosure": {
            "volunteered": [{"fact_ref": "m99.fact.007", "text": "The territories did overlap somewhat."}],
            "revealed_if_asked": [],
            "rapport_gated": [],
            "concealed": [],
            "unknown": [],
        },
        "knowledge_boundary": {
            "pinned_to_case_file": True,
            "unknown_response_style": "I couldn't say for certain.",
            "color_topics": ["office coffee", "the drive to the north branch"],
        },
    }


def rubric_entity():
    return {
        "id": "m99.rub",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/rubric/m99.rub",
        "matter_id": "m99",
        "declared_total": 189,
        "criteria": [
            {
                "id": "m99.rub.c01", "name": "Injunction-standard analysis",
                "description": "Applies the temporary-injunction standard.",
                "weight_points": 100, "skill_id": "SK-LP-13", "task_id": "TSK-001",
                "subcriteria": [
                    {"id": "m99.rub.c01.s01", "name": "Likelihood", "description": "Merits.", "weight_points": 60},
                    {"id": "m99.rub.c01.s02", "name": "Harm", "description": "Irreparable harm.", "weight_points": 40},
                ],
            },
            {"id": "m99.rub.c02", "name": "Persuasive writing",
             "description": "Clear, organized memorandum.", "weight_points": 89, "skill_id": "SK-LP-13"},
        ],
        "letter_grade_map": [
            {"grade": "A", "points": 189}, {"grade": "A-", "points": 180}, {"grade": "B+", "points": 170},
        ],
    }


def exercise_entity():
    secs = {}
    for key in vs.SECTION_KEYS:
        if key == "case_file":
            secs[key] = {"title": "Case File",
                         "files": ["case-file/employment-agreement.md", "case-file/resignation-email.md"]}
        else:
            secs[key] = {"title": key.title(), "body_md": filler(170, key)}
    return {
        "id": "m99.ex",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/exercise/m99.ex",
        "matter_id": "m99",
        "sections": secs,
    }


def business_entity():
    return {
        "id": "m99.biz",
        "schema_version": "1.0.0",
        "@id": "https://sonsteng.damienriehl.com/spine/business/m99.biz",
        "matter_id": "m99",
        "intake": {"client_name": "Adaeze Okwuosa", "matter_summary": "Non-compete defense.",
                   "intake_date": "2026-01-10"},
        "conflicts_check": {"parties_checked": ["Adaeze Okwuosa", "Verranto Holdings"],
                            "result": "clear", "checked_date": "2026-01-11"},
        "engagement": {"fee_type": "hourly", "rate": 300, "engagement_date": "2026-01-12",
                       "letter_md": "business/engagement-letter.md"},
        "time_entries": [
            {"id": "m99.te.0001", "date": "2026-01-14", "timekeeper_id": "FIRM-TK-01",
             "hours": 2.0, "rate": 300, "narrative": "Initial client interview."},
            {"id": "m99.te.0002", "date": "2026-01-20", "timekeeper_id": "FIRM-TK-01",
             "hours": 1.5, "rate": 300, "narrative": "Research enforceability."},
        ],
        "invoices": [
            {"id": "m99.inv.001", "date": "2026-02-01",
             "period": {"start": "2026-01-12", "end": "2026-01-31"},
             "fees": 1050, "expenses": 0, "payments_received": 0, "balance_due": 1050,
             "line_refs": ["m99.te.0001", "m99.te.0002"]},
        ],
        "trust_entries": [],
    }


def manifest_entity():
    return {
        "schema_version": "1.0.0",
        "@id": "MANIFEST-matters",
        "id": "MANIFEST-matters",
        "type": "matter_manifest",
        "spine_version": "1.0.0",
        "as_of_date": "2026-06-30",
        "frozen": True,
        "shape_keys": ["noncompete_trade_secret"],
        "matters": [
            {"id": "m99", "slug": "m99-noncompete-meridian", "shape": "noncompete_trade_secret",
             "tier": "fictional", "jurisdiction": "meridian",
             "caption": "Verranto Holdings v. Okwuosa",
             "sides": ["plaintiff_counsel", "defense_counsel"], "client_id": "FIRM-C-99",
             "fee_type": "hourly", "interview_focus": "defense_counsel", "premise": "Test stub."},
        ],
        "name_collision_sweep": {
            "performed": True,
            "surname_ledger": {"m99": ["Verranto", "Okwuosa", "Salvato",
                                       "Delacroix", "Ferngrove", "Halligan"]},
        },
    }


def spine_manifest_entity():
    return {
        "spine_version": "1.0.0",
        "jsonld_context_base": "https://sonsteng.damienriehl.com/spine/",
        "schemas": {k: "1.0.0" for k in vs.SCHEMA_FILES},
    }


def build_base_spine(root: Path):
    (root / "matters" / "m99-noncompete-meridian").mkdir(parents=True)
    (root / "firm").mkdir()
    (root / "skills").mkdir()
    (root / "tasks").mkdir()
    (root / "jurisdictions").mkdir()
    (root / "curriculum").mkdir()
    (root / "taxonomy").mkdir()

    # copy schemas so SchemaSet finds them
    shutil.copytree(SCHEMAS_DIR, root / "schemas")
    # copy meridian canon for the name sweep
    shutil.copy(REPO / "data" / "jurisdictions" / "meridian.json", root / "jurisdictions" / "meridian.json")

    def dump(path, obj):
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    dump(root / "spine-manifest.json", spine_manifest_entity())
    dump(root / "curriculum" / "identifier.json", {
        "@id": "https://sonsteng.damienriehl.com/spine/curriculum-fixture",
    })
    dump(root / "taxonomy" / "identifier.json", {
        "@id": "https://sonsteng.damienriehl.com/spine/taxonomy-fixture",
    })
    dump(root / "matters" / "manifest.json", manifest_entity())
    dump(root / "firm" / "firm.json", firm_entity())
    for sid, s in skill_entities().items():
        dump(root / "skills" / f"{sid}.json", s)
    dump(root / "tasks" / "TSK-001.json", task_entity())

    md = root / "matters" / "m99-noncompete-meridian"
    (md / "personas").mkdir()
    dump(md / "matter.json", matter_entity())
    dump(md / "rubric.json", rubric_entity())
    dump(md / "exercise.json", exercise_entity())
    dump(md / "business.json", business_entity())
    dump(md / "personas" / "m99.per.okwuosa.json", client_persona())
    dump(md / "personas" / "m99.per.salvato.json", witness_persona())
    (md / "facts.md").write_text(build_facts_md(), encoding="utf-8")


def run_validator(root: Path, matter=None, strict=True, enforce_day_zero_offsets=False,
                  enforce_legal_practicum_identifiers=False):
    world = vs.discover(root, matter)
    schemas = vs.SchemaSet(root / "schemas")
    report = vs.Report()
    vs.Validator(world, schemas, report, strict=strict, online=False,
                 enforce_day_zero_offsets=enforce_day_zero_offsets,
                 enforce_legal_practicum_identifiers=(
                     enforce_legal_practicum_identifiers
                 )).run()
    return report


def test_checked_date_count_is_nonzero_for_absolute_and_offset_fixtures(tmp_path):
    absolute = tmp_path / "absolute"
    build_base_spine(absolute)
    absolute_report = run_validator(absolute, matter="m99", strict=True)
    assert absolute_report.checked_dates > 0

    offset_repo = tmp_path / "offset-repo"
    offset = offset_repo / "data"
    shutil.copytree(absolute, offset)
    facts = offset / "matters" / "m99-noncompete-meridian" / "facts.md"
    facts.write_text(facts.read_text() +
                     "\nHearing January 13, 2026. {#b:deadbeef}\n",
                     encoding="utf-8")
    converted = day_zero.convert_corpus(offset_repo, write=True)
    assert converted.converted_dates > 0
    sidecar = json.loads((offset / "matters" / "m99-noncompete-meridian" /
                          "date-offsets.json").read_text())
    assert sidecar["entries"]
    offset_report = run_validator(offset, matter="m99", strict=True)
    assert offset_report.checked_dates > 0
    assert offset_report.offset_dates_checked >= len(sidecar["entries"])
    assert offset_report.day_zero_offset_enforcement is False


def test_custom_fact_date_survives_conversion_and_strict_validation(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    matter_path = data / "matters" / "m99-noncompete-meridian" / "matter.json"
    matter = json.loads(matter_path.read_text(encoding="utf-8"))
    matter["custom_facts"] = {
        "hearing_date": "2026-01-13",
        "authored_day_zero_offset": "Twelve days after filing",
    }
    matter_path.write_text(json.dumps(matter, indent=2), encoding="utf-8")

    converted = day_zero.convert_corpus(repo, write=True)
    converted_matter = json.loads(matter_path.read_text(encoding="utf-8"))
    assert converted_matter["custom_facts"] == {
        "hearing_date": "2026-01-13",
        "hearing_date_day_zero_offset": 1,
        "authored_day_zero_offset": "Twelve days after filing",
    }
    assert converted.converted_dates > 0

    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert not any(f.severity == vs.ERROR for f in report.for_scope("m99"))
    assert report.day_zero_offset_enforcement is True
    assert report.offset_dates_checked > 0

    converted_matter["custom_facts"]["hearing_date_day_zero_offset"] = 2
    matter_path.write_text(json.dumps(converted_matter, indent=2), encoding="utf-8")
    mismatch_report = run_validator(data, matter="m99", strict=True,
                                    enforce_day_zero_offsets=True)
    assert any(f.check == "F30" and
               "custom_facts.hearing_date literal" in f.message and
               "disagrees" in f.message
               for f in mismatch_report.for_scope("m99"))


def test_strict_validation_rejects_unbacked_custom_fact_offsets(tmp_path):
    cases = {
        "orphan": {"witness_count_day_zero_offset": 12},
        "non-date": {
            "witness_count": "Twelve witnesses",
            "witness_count_day_zero_offset": 12,
        },
    }
    for name, custom_facts in cases.items():
        data = tmp_path / name
        build_base_spine(data)
        matter_path = (data / "matters" / "m99-noncompete-meridian" /
                       "matter.json")
        matter = json.loads(matter_path.read_text(encoding="utf-8"))
        matter["custom_facts"] = custom_facts
        matter_path.write_text(json.dumps(matter, indent=2), encoding="utf-8")

        report = run_validator(data, matter="m99", strict=True,
                               enforce_day_zero_offsets=True)
        assert any(f.check == "F30" and
                   "custom_facts.witness_count_day_zero_offset" in f.message and
                   "has no supported date sibling" in f.message
                   for f in report.for_scope("m99")), name


def test_strict_validation_rejects_chained_custom_fact_offset(tmp_path):
    build_base_spine(tmp_path)
    matter_path = (tmp_path / "matters" / "m99-noncompete-meridian" /
                   "matter.json")
    matter = json.loads(matter_path.read_text(encoding="utf-8"))
    matter["custom_facts"] = {
        "deadline_day_zero_offset": "2026-01-13",
        "deadline_day_zero_offset_day_zero_offset": 2,
    }
    matter_path.write_text(json.dumps(matter, indent=2), encoding="utf-8")

    report = run_validator(tmp_path, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(
        f.check == "F30" and
        "custom_facts.deadline_day_zero_offset_day_zero_offset" in f.message and
        "chains Day Zero offset metadata" in f.message
        for f in report.for_scope("m99")
    )


def test_strict_validation_rejects_date_valued_authored_offset_suffix(tmp_path):
    build_base_spine(tmp_path)
    matter_path = (tmp_path / "matters" / "m99-noncompete-meridian" /
                   "matter.json")
    matter = json.loads(matter_path.read_text(encoding="utf-8"))
    matter["custom_facts"] = {
        "hearing_day_zero_offset": "2026-01-13",
    }
    matter_path.write_text(json.dumps(matter, indent=2), encoding="utf-8")

    report = run_validator(tmp_path, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(
        f.check == "F30" and
        "custom_facts.hearing_day_zero_offset" in f.message and
        "must be renamed" in f.message
        for f in report.for_scope("m99")
    )


def test_strict_validation_reports_huge_offset_instead_of_crashing(tmp_path):
    build_base_spine(tmp_path)
    matter_path = (tmp_path / "matters" / "m99-noncompete-meridian" /
                   "matter.json")
    matter = json.loads(matter_path.read_text(encoding="utf-8"))
    matter["custom_facts"] = {
        "hearing_date": "2026-01-13",
        "hearing_date_day_zero_offset": 10 ** 100,
    }
    matter_path.write_text(json.dumps(matter, indent=2), encoding="utf-8")

    report = run_validator(tmp_path, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(
        f.check == "F30" and
        "custom_facts.hearing_date" in f.message and
        "outside the supported calendar range" in f.message
        for f in report.for_scope("m99")
    )


def test_strict_validation_does_not_treat_extended_iso_forms_as_dates(tmp_path):
    build_base_spine(tmp_path)
    matter_path = (tmp_path / "matters" / "m99-noncompete-meridian" /
                   "matter.json")
    matter = json.loads(matter_path.read_text(encoding="utf-8"))
    matter["custom_facts"] = {
        "basic_iso": "20260113",
        "basic_iso_day_zero_offset": 12,
        "week_iso": "2026-W03-2",
        "week_iso_day_zero_offset": 12,
        "reduced_month": "2026-1-13",
        "reduced_month_day_zero_offset": 12,
        "reduced_day": "2026-01-3",
        "reduced_day_day_zero_offset": 12,
    }
    matter_path.write_text(json.dumps(matter, indent=2), encoding="utf-8")

    report = run_validator(tmp_path, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    messages = [f.message for f in report.for_scope("m99") if f.check == "F30"]
    assert any("custom_facts.basic_iso_day_zero_offset" in message and
               "has no supported date sibling" in message for message in messages)
    assert any("custom_facts.week_iso_day_zero_offset" in message and
               "has no supported date sibling" in message for message in messages)
    assert any("custom_facts.reduced_month_day_zero_offset" in message and
               "has no supported date sibling" in message for message in messages)
    assert any("custom_facts.reduced_day_day_zero_offset" in message and
               "has no supported date sibling" in message for message in messages)


def test_mutated_prose_offset_fails_semantic_resolution(tmp_path):
    data = tmp_path / "repo" / "data"
    build_base_spine(data)
    facts = data / "matters" / "m99-noncompete-meridian" / "facts.md"
    facts.write_text(facts.read_text() +
                     "\nHearing January 13, 2026. {#b:deadbeef}\n",
                     encoding="utf-8")
    day_zero.convert_corpus(tmp_path / "repo", write=True)
    sidecar_path = data / "matters" / "m99-noncompete-meridian" / "date-offsets.json"
    sidecar = json.loads(sidecar_path.read_text())
    assert sidecar["entries"]
    sidecar["entries"][0]["day_zero_offset"] += 1
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    report = run_validator(data, matter="m99", strict=True)
    assert any(f.check == "F30" and "date-offsets entries.0" in f.message
               for f in report.for_scope("m99"))


def test_offset_trust_ledger_uses_resolved_chronology(tmp_path):
    build_base_spine(tmp_path)
    md = tmp_path / "matters" / "m99-noncompete-meridian"
    business = _load(md, "business.json")
    business["trust_entries"] = [
        {"id": "m99.tr.001", "date": "2026-01-13", "date_day_zero_offset": 2,
         "type": "deposit", "amount": 500, "running_balance": 500},
        {"id": "m99.tr.002", "date": "2026-01-14", "date_day_zero_offset": 1,
         "type": "disbursement", "amount": 400, "running_balance": 0},
    ]
    _save(md, "business.json", business)

    report = run_validator(tmp_path, matter="m99", strict=True)
    assert any(f.check == "B10" and "goes negative" in f.message
               for f in report.for_scope("m99"))


def test_declared_absolute_holdout_remains_accepted(tmp_path):
    build_base_spine(tmp_path)
    matter_path = tmp_path / "matters" / "m99-noncompete-meridian" / "matter.json"
    matter = json.loads(matter_path.read_text())
    holdout_literal = matter["as_of_date"]
    (tmp_path / "day-zero-holdouts.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "description": "Declared fixed-fact fixture.",
        "method": "Test fixture declaration.",
        "summary": {"count": 1, "by_reason": {"fixed fact": 1}},
        "entries": [{
            "source": "matters/m99-noncompete-meridian/matter.json",
            "locator": "as_of_date",
            "literal": holdout_literal,
            "reason": "fixed fact",
            "review_status": vs.DECLARED_HOLDOUT_STATUS,
        }],
    }, indent=2), encoding="utf-8")
    world = vs.discover(tmp_path, "m99")
    report = vs.Report()
    validator = vs.Validator(world, vs.SchemaSet(tmp_path / "schemas"), report,
                             strict=True, online=False,
                             enforce_day_zero_offsets=True)
    validator.run()
    assert report.day_zero_offset_enforcement is True
    assert report.checked_dates > 0
    assert not any(f.check == "F30" and "as_of_date remains absolute" in f.message
                   for f in report.for_scope("m99"))
    assert any(f.check == "F30" and "open_date remains absolute" in f.message
               for f in report.for_scope("m99"))


def _converted_prose_fixture(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    facts = data / "matters" / "m99-noncompete-meridian" / "facts.md"
    facts.write_text(facts.read_text() +
                     "\nHearing January 13, 2026. {#b:deadbeef}\n",
                     encoding="utf-8")
    day_zero.convert_corpus(repo, write=True)
    return data, facts, facts.parent / "date-offsets.json"


def test_enforcement_requires_prose_sidecar_and_entry(tmp_path):
    data, _facts, sidecar = _converted_prose_fixture(tmp_path)
    sidecar.unlink()
    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(f.check == "F30" and "missing date-offsets.json" in f.message
               for f in report.for_scope("m99"))

    data, _facts, sidecar = _converted_prose_fixture(tmp_path / "second")
    payload = json.loads(sidecar.read_text())
    payload["entries"] = []
    sidecar.write_text(json.dumps(payload, indent=2))
    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(f.check == "F30" and "has no date-offsets entry" in f.message
               for f in report.for_scope("m99"))


def test_enforcement_accepts_complete_prose_sidecar(tmp_path):
    data, _facts, _sidecar = _converted_prose_fixture(tmp_path)
    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert report.checked_dates > 0
    assert report.offset_dates_checked > 0
    assert not any(f.check == "F30" and f.severity == vs.ERROR
                   for f in report.for_scope("m99"))


def test_enforcement_rejects_duplicate_sidecar_identity(tmp_path):
    data, _facts, sidecar = _converted_prose_fixture(tmp_path)
    payload = json.loads(sidecar.read_text())
    payload["entries"].append(dict(payload["entries"][0]))
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(f.check == "F30" and "duplicates a prior source identity" in f.message
               for f in report.for_scope("m99"))


def test_degraded_schema_rejects_negative_sidecar_locator(tmp_path, monkeypatch):
    data, _facts, sidecar = _converted_prose_fixture(tmp_path)
    payload = json.loads(sidecar.read_text())
    payload["entries"][0]["locator"] = -1
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    monkeypatch.setattr(vs, "HAVE_JSONSCHEMA", False)

    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)

    assert any(f.check == "F30" and "stale literal/block" in f.message
               for f in report.for_scope("m99"))


def test_enforcement_rejects_json_date_moved_to_another_block(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    exercise = data / "matters" / "m99-noncompete-meridian" / "exercise.json"
    payload = json.loads(exercise.read_text())
    payload["sections"]["instructions"]["body_md"] = (
        "Hearing January 13, 2026. {#b:deadbeef}"
    )
    payload["sections"]["objectives"]["body_md"] = "No date here. {#b:feedface}"
    exercise.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    day_zero.convert_corpus(repo, write=True)

    payload = json.loads(exercise.read_text())
    payload["sections"]["instructions"]["body_md"] = "No date here. {#b:deadbeef}"
    payload["sections"]["objectives"]["body_md"] = (
        "Hearing January 13, 2026. {#b:feedface}"
    )
    exercise.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(f.check == "F30" and "stale literal/block" in f.message
               for f in report.for_scope("m99"))


def test_enforcement_rejects_stale_literal_or_block(tmp_path):
    data, facts, _sidecar = _converted_prose_fixture(tmp_path)
    facts.write_text(facts.read_text().replace("b:deadbeef", "b:feedface"))
    report = run_validator(data, matter="m99", strict=True,
                           enforce_day_zero_offsets=True)
    assert any(f.check == "F30" and "stale literal/block" in f.message
               for f in report.for_scope("m99"))


def test_enforcement_cli_flag_is_opt_in():
    parser = vs.build_arg_parser()
    assert parser.parse_args([]).enforce_day_zero_offsets is False
    assert parser.parse_args(["--enforce-day-zero-offsets"]).enforce_day_zero_offsets is True
    assert parser.parse_args([]).enforce_legal_practicum_identifiers is False
    assert parser.parse_args([
        "--enforce-legal-practicum-identifiers"
    ]).enforce_legal_practicum_identifiers is True


def test_identifier_enforcement_rejects_old_base_and_accepts_combined_rewrite(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)

    compatible = run_validator(data, matter="m99", strict=True)
    assert not any(f.check == "F31" for f in compatible.findings)

    rejected = run_validator(
        data, matter="m99", strict=True,
        enforce_legal_practicum_identifiers=True,
    )
    assert rejected.identifier_base_enforcement is True
    assert rejected.old_identifier_base_occurrences > 0
    assert any(f.check == "F31" and "old JSON-LD base" in f.message
               for f in rejected.for_scope("GLOBAL"))

    day_zero.convert_corpus(repo, write=True)
    accepted = run_validator(
        data, matter="m99", strict=True,
        enforce_legal_practicum_identifiers=True,
    )
    assert accepted.identifier_base_values_checked > 0
    assert accepted.old_identifier_base_occurrences == 0
    assert not any(f.check == "F31" and f.severity == vs.ERROR
                   for f in accepted.for_scope("GLOBAL"))


def test_identifier_enforcement_rejects_manifest_mismatch_without_old_base(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    day_zero.convert_corpus(repo, write=True)
    manifest = data / "spine-manifest.json"
    payload = json.loads(manifest.read_text())
    payload["jsonld_context_base"] = "https://example.test/spine/"
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report = run_validator(
        data, matter="m99", strict=True,
        enforce_legal_practicum_identifiers=True,
    )

    errors = [f.message for f in report.for_scope("GLOBAL") if f.check == "F31"]
    assert report.old_identifier_base_occurrences == 0
    assert any("spine manifest JSON-LD base" in message for message in errors)
    assert not any("old JSON-LD base" in message for message in errors)


def test_identifier_enforcement_rejects_slash_escaped_old_base(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    day_zero.convert_corpus(repo, write=True)
    (data / "curriculum" / "identifier.json").write_text(
        '{"@id":"https:\\/\\/sonsteng.damienriehl.com\\/spine\\/escaped"}\n',
        encoding="utf-8",
    )

    report = run_validator(
        data, strict=True, enforce_legal_practicum_identifiers=True,
    )

    assert report.old_identifier_base_occurrences == 1
    assert any(
        f.check == "F31" and "old JSON-LD base" in f.message
        for f in report.for_scope("GLOBAL")
    )


def test_identifier_enforcement_rejects_zero_recognized_values(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    for path in day_zero.authoritative_paths(data):
        payload = path.read_bytes().replace(
            day_zero.OLD_JSONLD_BASE, b"https://example.test/spine/"
        )
        path.write_bytes(payload)

    report = run_validator(
        data, matter="m99", strict=True,
        enforce_legal_practicum_identifiers=True,
    )

    errors = [f.message for f in report.for_scope("GLOBAL") if f.check == "F31"]
    assert report.identifier_base_values_checked == 0
    assert any("zero recognized base values" in message for message in errors)
    assert not any("old JSON-LD base" in message for message in errors)


def test_identifier_enforcement_rejects_incomplete_authoritative_scope(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    day_zero.convert_corpus(repo, write=True)
    shutil.rmtree(data / "curriculum")

    report = run_validator(
        data, strict=True, enforce_legal_practicum_identifiers=True,
    )

    finding = next(
        f for f in report.for_scope("GLOBAL")
        if f.check == "F31" and "scope is incomplete" in f.message
    )
    assert any("curriculum/" in gap for gap in finding.detail["gaps"])


def test_identifier_enforcement_rejects_missing_manifest_matter(tmp_path):
    repo = tmp_path / "repo"
    data = repo / "data"
    build_base_spine(data)
    day_zero.convert_corpus(repo, write=True)
    shutil.rmtree(data / "matters" / "m99-noncompete-meridian")

    report = run_validator(
        data, strict=True, enforce_legal_practicum_identifiers=True,
    )

    finding = next(
        f for f in report.for_scope("GLOBAL")
        if f.check == "F31" and "scope is incomplete" in f.message
    )
    assert any("m99" in gap for gap in finding.detail["gaps"])


def test_json_summary_exposes_date_counts_and_effective_switch(tmp_path):
    build_base_spine(tmp_path)
    world = vs.discover(tmp_path, "m99")
    report = vs.Report()
    vs.Validator(world, vs.SchemaSet(tmp_path / "schemas"), report,
                 strict=True, online=False).run()
    payload = vs.build_json_report(world, report, strict=True)
    assert payload["totals"]["checked_dates"] == report.checked_dates > 0
    assert payload["totals"]["offset_dates_checked"] == 0
    assert payload["day_zero_offset_enforcement"] is False
    assert payload["identifier_base_enforcement"] is False
    assert payload["totals"]["identifier_files_checked"] > 0
    assert payload["totals"]["identifier_base_values_checked"] > 0
    assert payload["totals"]["old_identifier_base_occurrences"] > 0


def test_validator_registry_count_matches_docstring_and_help():
    spec = (REPO / "docs" / "research" / "validator-spec.md").read_text()
    numbered = [int(match.group(1)) for match in
                re.finditer(r"(?m)^(\d+)\. ", spec)]
    assert sorted(set(numbered)) == list(range(1, vs.VALIDATOR_CHECK_COUNT + 1))
    assert f"the {vs.VALIDATOR_CHECK_COUNT} checks" in vs.__doc__
    assert f"the {vs.VALIDATOR_CHECK_COUNT} checks" in vs.build_arg_parser().format_help()


def matter_error_checks(report, mid="m99"):
    return sorted({f.check for f in report.for_scope(mid) if f.severity == vs.ERROR})


# --- mutations: each returns nothing, edits files in the spine copy ---------

def _load(md, name):
    return json.loads((md / name).read_text())


def _save(md, name, obj):
    (md / name).write_text(json.dumps(obj, indent=2), encoding="utf-8")


def mut_dangling_fact(md):
    p = _load(md / "personas", "m99.per.okwuosa.json")
    p["disclosure"]["rapport_gated"][0]["fact_ref"] = "m99.fact.999"
    _save(md / "personas", "m99.per.okwuosa.json", p)


def mut_negative_trust(md):
    b = _load(md, "business.json")
    b["trust_entries"] = [
        {"id": "m99.tr.001", "date": "2026-01-12", "type": "deposit", "amount": 500, "running_balance": 500},
        {"id": "m99.tr.002", "date": "2026-02-05", "type": "disbursement", "amount": 2000, "running_balance": 0},
    ]
    _save(md, "business.json", b)


def mut_hours_increment(md):
    b = _load(md, "business.json")
    b["time_entries"][0]["hours"] = 0.15
    _save(md, "business.json", b)


def mut_rubric_sum(md):
    r = _load(md, "rubric.json")
    r["criteria"][1]["weight_points"] = 80  # 100 + 80 = 180 != declared 189
    _save(md, "rubric.json", r)


def mut_prefix_bleed(md):
    m = _load(md, "matter.json")
    m["sides"][0]["confidential_fact_refs"] = ["m01.fact.014"]  # foreign prefix
    _save(md, "matter.json", m)


def mut_missing_section(md):
    e = _load(md, "exercise.json")
    del e["sections"]["history"]
    _save(md, "exercise.json", e)


def mut_depth_floor(md):
    m = _load(md, "matter.json")
    m["witnesses"] = [m["witnesses"][0]]  # 1 witness < 3
    _save(md, "matter.json", m)


def mut_unknown_trigger(md):
    p = _load(md / "personas", "m99.per.okwuosa.json")
    p["disclosure"]["rapport_gated"][0]["requires"] = ["being_extra_nice"]
    _save(md / "personas", "m99.per.okwuosa.json", p)


def mut_rate_mismatch(md):
    b = _load(md, "business.json")
    b["time_entries"][0]["rate"] = 999  # not on firm rate card, no engagement rate = 999
    _save(md, "business.json", b)


def mut_invoice_arith(md):
    b = _load(md, "business.json")
    b["invoices"][0]["balance_due"] = 9999  # != fees+exp-pay
    _save(md, "business.json", b)


MUTATIONS = [
    ("dangling fact_ref (rapport-gated)", mut_dangling_fact, "C13"),
    ("negative trust running balance", mut_negative_trust, "B10"),
    ("time-entry hours = 0.15 (not 0.1 incr)", mut_hours_increment, "B8h"),
    ("rubric criteria sum != declared_total", mut_rubric_sum, "D19"),
    ("foreign matter-prefix id bleed", mut_prefix_bleed, "A1"),
    ("missing 8-part exercise section", mut_missing_section, "F29"),
    ("depth-floor miss (1 witness < 3)", mut_depth_floor, "DEPTH"),
    ("unknown rapport trigger token", mut_unknown_trigger, "F29"),
    ("time-entry rate off the rate card", mut_rate_mismatch, "B7"),
    ("invoice balance_due arithmetic wrong", mut_invoice_arith, "B9"),
]


def main():
    schemas = vs.SchemaSet(SCHEMAS_DIR)
    print("=" * 74)
    print("SELF-TEST: tools/validate_spine.py")
    print(f"jsonschema available: {vs.HAVE_JSONSCHEMA}")
    print("=" * 74)

    all_ok = True

    # ---- Part 1: examples ------------------------------------------------
    print("\n[1] Schema-example structural self-test")
    ex_ok, rows = check_examples(schemas)
    all_ok &= ex_ok
    for fname, status, note in rows:
        line = f"    {status:12} {fname}"
        if note:
            line += f"  ({note})"
        print(line)

    # ---- Part 2: synthetic matter ---------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="spine_selftest_"))
    try:
        base = tmp / "base"
        base.mkdir()
        build_base_spine(base)

        print("\n[2] Baseline complete m99 stub (expect PASS, 0 errors everywhere)")
        rep = run_validator(base, matter=None, strict=True)
        total_err = sum(1 for f in rep.findings if f.severity == vs.ERROR)
        total_warn = sum(1 for f in rep.findings if f.severity == vs.WARN)
        base_ok = total_err == 0
        all_ok &= base_ok
        print(f"    {'PASS' if base_ok else 'FAIL'}  m99 baseline: {total_err} errors, {total_warn} warns")
        if not base_ok:
            for f in sorted(rep.findings, key=lambda x: x.sort_key()):
                if f.severity == vs.ERROR:
                    print(f"        UNEXPECTED [{f.severity}] {f.scope} {f.check} {f.message}")

        print("\n[3] Mutation matrix (each must trip its target ERROR on m99, in isolation)")
        print(f"    {'#':>2}  {'expect':6}  {'fired?':6}  mutation")
        print("    " + "-" * 66)
        for i, (label, fn, expect) in enumerate(MUTATIONS, start=1):
            work = tmp / f"mut{i:02d}"
            shutil.copytree(base, work)
            md = work / "matters" / "m99-noncompete-meridian"
            fn(md)
            rep = run_validator(work, matter="m99", strict=True)
            checks = matter_error_checks(rep, "m99")
            fired = expect in checks
            all_ok &= fired
            mark = "OK" if fired else "MISS"
            print(f"    {i:>2}  {expect:6}  {mark:6}  {label}")
            if not fired:
                print(f"          got ERROR checks: {checks}")

        # isolation proof: mutating m99 must not create findings for a phantom sibling
        print("\n[4] Isolation: --matter m99 validates only m99")
        rep = run_validator(base, matter="m99", strict=True)
        seen = rep.matters_seen
        iso_ok = seen == {"m99"}
        all_ok &= iso_ok
        print(f"    {'PASS' if iso_ok else 'FAIL'}  matters_seen={sorted(seen)}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 74)
    print("SELF-TEST RESULT:", "ALL PASS" if all_ok else "FAILURES ABOVE")
    print("=" * 74)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
