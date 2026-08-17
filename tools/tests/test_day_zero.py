from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import day_zero  # noqa: E402
import json_surgical  # noqa: E402
import day_zero_equivalence  # noqa: E402


def test_iso_long_form_and_negative_offsets():
    anchor = day_zero.parse_date("2026-03-01")
    assert day_zero.offset_days("2026-03-04", anchor) == 3
    assert day_zero.offset_days("March 4, 2026", anchor) == 3
    assert day_zero.offset_days("February 16, 2026", anchor) == -13


def test_fixed_fact_candidates_are_held_out():
    assert day_zero.classify_candidate("2019", "facts.md", "b:abc:0").kind == "holdout"
    citation = day_zero.classify_candidate(
        "410 U.S. 113 (1973)", "facts.md", "b:abc:0"
    )
    assert citation.kind == "holdout"
    statutory = day_zero.classify_candidate(
        "The statute became effective January 1, 2020.", "facts.md", "b:abc:0"
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
    assert sidecar["entries"][0]["block_id"] == "b:deadbeef"
    assert sidecar["entries"][0]["day_zero_offset"] == -13
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
    assert [(row["block_id"], row["day_zero_offset"]) for row in entries] == [
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
        result.before_files, result.after_files, result.file_proofs, result.date_proofs,
        converted_date_count=result.converted_dates,
    )
    assert proof.converted_date_count == proof.proof_covered_date_count == 4
    mutated = dict(result.after_files)
    business_path = "data/matters/m01-fixture/business/business.json"
    mutated[business_path] = mutated[business_path].replace(b'"packed"', b'"PACKED"')
    with pytest.raises(day_zero_equivalence.EquivalenceError, match="business.json.*byte mismatch"):
        day_zero_equivalence.file_round_trip(
            result.before_files, mutated, result.file_proofs, result.date_proofs,
            converted_date_count=result.converted_dates,
        )
