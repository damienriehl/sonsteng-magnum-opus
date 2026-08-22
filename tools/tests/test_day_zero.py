from __future__ import annotations

import json
import hashlib
import stat
import sys
from pathlib import Path

import pytest
import jsonschema

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import day_zero  # noqa: E402
import json_surgical  # noqa: E402
import day_zero_equivalence  # noqa: E402
import day_zero_locator  # noqa: E402
import apply_day_zero_review  # noqa: E402


def _approve_fixture_review(repo: Path, initial: day_zero.Result):
    holdouts, audit = day_zero.governed_reports(repo, initial)
    proposals = []
    for category, rows in (("holdout", holdouts["entries"]),
                           ("anchor_attention", audit["attention_required"])):
        for row in rows:
            matter = row["source"].split("/")[2]
            disposition = ("convertible" if category == "holdout"
                           else "convertible_after_durable_locator_added")
            proposal = {
                "category": category,
                "source": row["source"],
                "locator": row["locator"],
                "literal": row["literal"],
                "matter": matter,
                "proposed_disposition": disposition,
                "reason_code": "fixture-approved-conversion",
                "rationale": "Fixture review approved this matter-relative date.",
                "matter_anchor": "2026-01-01",
                "proposed_day_zero_offset": day_zero.offset_days(
                    row["literal"], day_zero.parse_date("2026-01-01")
                ),
            }
            proposal["key"] = apply_day_zero_review.proposal_key(proposal)
            proposals.append(proposal)
    proposal = {"proposals": proposals}
    proposal_path = repo / apply_day_zero_review.PROPOSAL_REL
    proposal_path.parent.mkdir(parents=True)
    proposal_path.write_text(json.dumps(proposal, indent=2) + "\n")
    approval_path = repo / apply_day_zero_review.APPROVAL_REL
    approval_path.parent.mkdir(parents=True)
    digest = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    approval_path.write_text(f"Approved proposal SHA-256: `{digest}`\n")
    resolved_holdouts, resolved_audit = apply_day_zero_review.apply_review(
        repo, proposal, holdouts, audit
    )
    holdouts_path = repo / apply_day_zero_review.HOLDOUTS_REL
    audit_path = repo / apply_day_zero_review.AUDIT_REL
    holdouts_path.write_text(json.dumps(resolved_holdouts, indent=2) + "\n")
    audit_path.write_text(json.dumps(resolved_audit, indent=2) + "\n")


def test_iso_long_form_and_negative_offsets():
    anchor = day_zero.parse_date("2026-03-01")
    assert day_zero.offset_days("2026-03-04", anchor) == 3
    assert day_zero.offset_days("March 4, 2026", anchor) == 3
    assert day_zero.offset_days("February 16, 2026", anchor) == -13


def test_fixed_fact_candidates_are_held_out():
    assert day_zero.classify_candidate("2019").kind == "holdout"
    citation = day_zero.classify_candidate("410 U.S. 113 (1973)")
    assert citation.kind == "holdout"
    statutory = day_zero.classify_candidate(
        "The statute became effective January 1, 2020."
    )
    assert statutory.kind == "holdout"


def test_json_property_insert_is_surgical_and_idempotent():
    raw = '{\n  "date": "2026-03-04",\n  "packed": [1, 2, 3]\n}\n'
    out = json_surgical.insert_object_properties(
        raw, [("", "date_day_zero_offset", 3)]
    )
    assert '"packed": [1, 2, 3]' in out
    assert json.loads(out)["date_day_zero_offset"] == 3
    assert json_surgical.insert_object_properties(
        out, [("", "date_day_zero_offset", 3)]
    ) == out


def test_fixture_conversion_leaves_markdown_and_writes_sidecar(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{\n  "id": "m01",\n  "open_date": "2026-03-01",\n'
        '  "as_of_date": "2026-03-04"\n}\n'
    )
    prose = "Hearing on February 16, 2026. {#b:deadbeef}\n"
    (matter / "facts.md").write_text(prose)
    result = day_zero.convert_corpus(tmp_path, write=True)
    assert (matter / "facts.md").read_text() == prose
    sidecar = json.loads((matter / "date-offsets.json").read_text())
    schema = json.loads((TOOLS.parent / "data" / "schemas" /
                         "date-offsets.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(sidecar)
    assert all("anchor" not in row for row in sidecar["entries"])
    assert all(isinstance(row["locator"], int) for row in sidecar["entries"])
    prose_entry = next(row for row in sidecar["entries"] if row.get("block_id") == "b:deadbeef")
    assert prose_entry["day_zero_offset"] == -13
    assert json.loads((matter / "matter.json").read_text())["as_of_date_day_zero_offset"] == 3
    assert result.converted_dates == result.proof_records
    before = {p: p.read_bytes() for p in matter.iterdir()}
    day_zero.convert_corpus(tmp_path, write=True)
    assert before == {p: p.read_bytes() for p in matter.iterdir()}


def test_audit_has_anchor_and_reason_for_every_conversion(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{"id":"m01","open_date":"2026-03-01","as_of_date":"2026-03-04"}'
    )
    result = day_zero.convert_corpus(tmp_path, write=False)
    assert result.matter_anchors == [{"matter_id": "m01", "matter_slug": "m01-fixture",
                                      "anchor": "2026-03-01",
                                      "reason": "matter m01 existing open_date"}]
    assert result.audit
    assert all(row["anchor"] and row["anchor_reason"] for row in result.audit)


def test_schema_additions_are_optional():
    repo = TOOLS.parent
    for name in ("matter", "business", "exercise"):
        schema = json.loads((repo / "data" / "schemas" / f"{name}.schema.json").read_text())
        assert not any("day_zero" in key for key in schema.get("required", []))


def test_block_ids_are_not_inventoried_as_years_and_renderer_separates_list_blocks(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text('{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}')
    (matter / "facts.md").write_text(
        "- First was January 3, 2026. {#b:2063dafb}\n"
        "- Second was January 4, 2026. {#b:abcdef12}\n"
    )
    result = day_zero.convert_corpus(tmp_path, write=True)
    assert not any(row["literal"] == "2063" for row in result.holdouts)
    entries = json.loads((matter / "date-offsets.json").read_text())["entries"]
    assert [(row["block_id"], row["day_zero_offset"]) for row in entries if "block_id" in row] == [
        ("b:2063dafb", 2), ("b:abcdef12", 3)
    ]


def test_raw_inventory_reconciles_unrendered_and_nonmatter_dates(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text('{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}')
    (matter / "unrendered.txt").write_text("Unrendered date: 2026-01-03\n")
    curriculum = tmp_path / "data" / "curriculum"
    curriculum.mkdir()
    (curriculum / "template.md").write_text("Template date: January 4, 2026\n")
    result = day_zero.convert_corpus(tmp_path, write=False)
    assert result.iso_dates == 3
    assert result.long_form_dates == 1
    assert result.converted_dates + result.full_date_holdouts + len(result.unclassified) == 4
    assert any("outside a renderer-recognized" in row["reason"] for row in result.unclassified)
    assert any("has no matter open_date anchor" in row["reason"] for row in result.unclassified)


def test_iso_looking_identifier_is_reconciled_as_non_date(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text('{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}')
    (matter / "record.md").write_text("Report AMSD-2026-04-0388. {#b:deadbeef}\n")
    result = day_zero.convert_corpus(tmp_path)
    assert result.iso_like_raw_occurrences == 3
    assert result.iso_dates == 2
    assert result.excluded_non_dates[0]["literal"] == "2026-04-03"


def test_converter_output_round_trips_through_u6_and_mutation_fails(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    business = matter / "business"
    business.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{\n  "id": "m01",\n  "open_date": "2026-01-01",\n  "as_of_date": "2026-01-02"\n}\n'
    )
    (business / "business.json").write_text(
        '{\n  "matter_id": "m01",\n  "invoice_date": "2026-01-05",\n  "packed": [1, 2]\n}\n'
    )
    (matter / "facts.md").write_text("Hearing January 3, 2026. {#b:deadbeef}\n")
    result = day_zero.convert_corpus(tmp_path, write=True)
    assert len(result.date_proofs) == result.converted_dates == 4
    assert any(proof.path.endswith("business/business.json") for proof in result.file_proofs)
    sidecar_path = "data/matters/m01-fixture/date-offsets.json"
    assert result.before_files[sidecar_path] == b""
    proof = day_zero_equivalence.file_round_trip(
        sorted(result.touched_files), result.before_files, result.after_files,
        result.file_proofs, result.date_proofs,
        converted_date_count=result.converted_dates,
    )
    assert proof.converted_date_count == proof.proof_covered_date_count == 4
    mutated = dict(result.after_files)
    business_path = "data/matters/m01-fixture/business/business.json"
    mutated[business_path] = mutated[business_path].replace(b'"packed"', b'"PACKED"')
    with pytest.raises(day_zero_equivalence.EquivalenceError, match="business.json.*byte mismatch"):
        day_zero_equivalence.file_round_trip(
            sorted(result.touched_files), result.before_files, mutated,
            result.file_proofs, result.date_proofs,
            converted_date_count=result.converted_dates,
        )


def test_mixed_block_uses_local_clause_context(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}'
    )
    (matter / "facts.md").write_text(
        "Statute effective January 1, 2020; hearing occurred January 3, 2026. {#b:deadbeef}\n"
    )
    result = day_zero.convert_corpus(tmp_path)
    assert any(row["literal"] == "January 1, 2020" for row in result.holdouts)
    assert any(proof.literal == "January 3, 2026" for proof in result.date_proofs)


def test_generic_effective_today_in_other_sentence_does_not_hold_out_event(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}'
    )
    (matter / "facts.md").write_text(
        "Policy is effective today, but hearing occurred January 3, 2026. {#b:deadbeef}\n"
    )
    result = day_zero.convert_corpus(tmp_path)
    assert any(proof.literal == "January 3, 2026" for proof in result.date_proofs)


def test_invalid_calendar_date_requires_attention_instead_of_crashing(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-02-30"}'
    )
    result = day_zero.convert_corpus(tmp_path)
    assert any(row["literal"] == "2026-02-30" and
               "invalid calendar" in row["reason"] for row in result.unclassified)


def test_late_proof_failure_writes_nothing(tmp_path, monkeypatch):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    original = b'{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}'
    (matter / "matter.json").write_bytes(original)
    (matter / "facts.md").write_text("Hearing January 3, 2026. {#b:deadbeef}\n")
    def reject(*args, **kwargs):
        raise day_zero_equivalence.EquivalenceError("late proof conflict")
    monkeypatch.setattr(day_zero_equivalence, "file_round_trip", reject)
    with pytest.raises(day_zero_equivalence.EquivalenceError, match="late proof"):
        day_zero.convert_corpus(tmp_path, write=True)
    assert (matter / "matter.json").read_bytes() == original
    assert not (matter / "date-offsets.json").exists()


def test_collateral_sibling_mutation_is_rejected():
    before = '{"date":"2026-01-02","name":"original"}'
    after = '{"date":"2026-01-02","name":"changed","date_day_zero_offset":1}'
    with pytest.raises(RuntimeError, match="collateral"):
        day_zero._validate_intended_json_additions(
            before, after, [("", "date_day_zero_offset", 1)]
        )


def test_cli_proof_failure_is_nonzero_and_precedes_reports(tmp_path, monkeypatch):
    audit = tmp_path / "audit.json"
    holdouts = tmp_path / "holdouts.json"
    monkeypatch.setattr(day_zero, "convert_corpus", lambda *args, **kwargs: day_zero.Result())
    def reject(*args, **kwargs):
        raise day_zero_equivalence.EquivalenceError("corrupted emitted proof")
    monkeypatch.setattr(day_zero_equivalence, "file_round_trip", reject)
    monkeypatch.setattr(sys, "argv", ["day_zero.py", "--repo", str(tmp_path),
                                      "--audit-output", str(audit),
                                      "--holdouts-output", str(holdouts)])
    with pytest.raises(day_zero_equivalence.EquivalenceError, match="corrupted"):
        day_zero.main()
    assert not audit.exists()
    assert not holdouts.exists()


def test_approved_proposal_is_materialized_with_stable_durable_locators(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    (matter / "matter.json").write_text(
        '{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02",'
        '"summary":"Conference January 5, 2026."}'
    )
    original_block = "b:deadbeef"
    (matter / "facts.md").write_text(
        "# Ledger {#b:deadbeef}\n\n"
        "| Date | Event |\n|---|---|\n"
        "| January 3, 2026 | first |\n"
        "| January 3, 2026 | second |\n\n"
        "Contract effective January 4, 2026. {#b:cafebabe}\n"
    )
    initial = day_zero.convert_corpus(tmp_path)
    assert len(initial.unclassified) == 3
    assert any(row["literal"] == "January 4, 2026" for row in initial.holdouts)
    _approve_fixture_review(tmp_path, initial)

    # Physical line drift after approval must not invalidate durable content identity.
    facts = matter / "facts.md"
    facts.write_text("\n" + facts.read_text())
    result = day_zero.convert_corpus(tmp_path, write=True)

    assert result.unclassified == []
    assert result.staged_date_writes == result.proof_records == result.converted_dates
    approved = [row for row in json.loads((matter / "date-offsets.json").read_text())["entries"]
                if row.get("durable_locator")]
    assert len(approved) == 3
    assert len({row["durable_locator"] for row in approved}) == 3
    assert all("block_id" not in row for row in approved)
    assert sorted(row["literal"] for row in approved) == [
        "January 3, 2026", "January 3, 2026", "January 5, 2026"
    ]
    assert original_block in facts.read_text()
    assert any(proof.literal == "January 4, 2026" for proof in result.date_proofs)


def test_raw_durable_locator_resolves_repeated_literal_on_one_line(tmp_path):
    source = tmp_path / "facts.md"
    source.write_text("Hearings January 3, 2026 and January 3, 2026.\n")
    row = {
        "key": "fixture-key",
        "source": "facts.md",
        "locator": "line:1:raw-occurrence:2",
        "literal": "January 3, 2026",
    }
    durable = day_zero_locator.durable_locator(tmp_path, row)

    assert durable.endswith(":occurrence:2:date:1")
    assert day_zero_locator.resolve_durable_locator(
        source, durable, row["literal"]
    ) == 1


def test_real_approved_proposal_projects_exact_materialization_without_writes():
    repo = TOOLS.parent
    before = (repo / "data" / "matters" / "m01-arbitration-meridian" /
              "matter.json").read_bytes()
    result = day_zero.convert_corpus(repo, write=False)
    assert result.staged_date_writes == result.proof_records == result.converted_dates == 1236
    assert result.governed_conversion_target == 1236
    assert result.unclassified == []
    assert result.iso_dates + result.long_form_dates == 1242
    assert result.full_date_holdouts == 6
    assert (repo / "data" / "matters" / "m01-arbitration-meridian" /
            "matter.json").read_bytes() == before


def test_staged_file_replacement_rolls_back_every_path_on_failure(tmp_path, monkeypatch):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first-old")
    second.write_text("second-old")
    first.chmod(0o640)
    second.chmod(0o664)
    real_replace = day_zero.os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        return real_replace(source, target)

    monkeypatch.setattr(day_zero.os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated"):
        day_zero._write_staged_files({first: b"first-new", second: b"second-new"})
    assert first.read_text() == "first-old"
    assert second.read_text() == "second-old"
    assert stat.S_IMODE(first.stat().st_mode) == 0o640
    assert stat.S_IMODE(second.stat().st_mode) == 0o664


def test_staged_write_preserves_existing_mode_and_sets_new_sidecar_mode(tmp_path):
    existing = tmp_path / "matter.json"
    sidecar = tmp_path / "date-offsets.json"
    existing.write_text("old")
    existing.chmod(0o640)

    day_zero._write_staged_files({
        existing: b"new",
        sidecar: b'{"entries": []}\n',
    })

    assert existing.read_text() == "new"
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640
    assert stat.S_IMODE(sidecar.stat().st_mode) == day_zero.ORDINARY_FILE_MODE


def test_unresolvable_approved_identity_writes_nothing(tmp_path):
    matter = tmp_path / "data" / "matters" / "m01-fixture"
    matter.mkdir(parents=True)
    original = '{"id":"m01","open_date":"2026-01-01","as_of_date":"2026-01-02"}'
    (matter / "matter.json").write_text(original)
    facts = matter / "facts.md"
    facts.write_text("Unmarked hearing January 3, 2026.\n")
    _approve_fixture_review(tmp_path, day_zero.convert_corpus(tmp_path))
    facts.write_text("The approved date was removed.\n")

    with pytest.raises(RuntimeError, match="approved Day Zero identity no longer resolves"):
        day_zero.convert_corpus(tmp_path, write=True)
    assert (matter / "matter.json").read_text() == original
    assert not (matter / "date-offsets.json").exists()


def test_governed_cli_count_mismatch_fails_before_outputs(tmp_path, monkeypatch):
    audit = tmp_path / "audit.json"
    holdouts = tmp_path / "holdouts.json"
    result = day_zero.Result()
    result.staged_date_writes = 1235
    result.date_proofs = [object()] * 1235
    monkeypatch.setattr(day_zero, "convert_corpus", lambda *args, **kwargs: result)
    monkeypatch.setattr(day_zero_equivalence, "file_round_trip", lambda *args, **kwargs: None)
    monkeypatch.setattr(day_zero, "governed_reports", lambda *args, **kwargs: (
        {"summary": {"count": 635}},
        {"summary": {"converted_dates": 1236, "attention_required": 0,
                     "full_date_holdouts": 635}},
    ))
    monkeypatch.setattr(day_zero, "_approved_conversion_target", lambda repo: 1236)
    monkeypatch.setattr(sys, "argv", ["day_zero.py", "--repo", str(tmp_path),
                                      "--audit-output", str(audit),
                                      "--holdouts-output", str(holdouts)])
    with pytest.raises(RuntimeError, match="staged=1235.*governed=1236"):
        day_zero.main()
    assert not audit.exists()
    assert not holdouts.exists()
