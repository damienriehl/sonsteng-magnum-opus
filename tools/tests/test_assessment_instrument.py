"""Contract tests for the canonical memo assessment instrument (U8)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import jsonschema

from tools import validate_spine as spine


REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "data" / "schemas" / "assessment-instrument.schema.json"
INSTRUMENT_PATH = REPO / "data" / "curriculum" / "assessment-instrument.json"
MANIFEST_PATH = REPO / "data" / "spine-manifest.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _content_hash(content: dict) -> str:
    encoded = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _resolve_contract(instrument: dict, deliverable_type: str) -> str:
    resolution = instrument["content"]["assessment_resolution"]
    for route in resolution["routes"]:
        if deliverable_type in route["deliverable_types"]:
            return route["assessment_contract"]
    return resolution["fallback_contract"]


def test_instrument_validates_and_is_registered() -> None:
    schema = _load(SCHEMA_PATH)
    instrument = _load(INSTRUMENT_PATH)
    manifest = _load(MANIFEST_PATH)

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(instrument)
    assert list((REPO / "data" / "curriculum").glob("*assessment-instrument*.json")) == [
        INSTRUMENT_PATH
    ]
    assert manifest["schemas"]["assessment_instrument"] == instrument["schema_version"]

    world = spine.discover(REPO / "data", only_matter=None)
    assert [loaded.path for loaded in world.assessment_instruments] == [INSTRUMENT_PATH]
    loaded = world.assessment_instruments[0]
    assert spine.SchemaSet(REPO / "data" / "schemas").validate(
        loaded.entity_type, loaded.obj
    ) == []


def test_band_set_is_complete_non_overlapping_and_monotonic() -> None:
    instrument = _load(INSTRUMENT_PATH)
    bands = instrument["content"]["scale"]["bands"]
    scores = [band["score"] for band in bands]

    assert scores == list(range(1, 8))
    assert len(scores) == len(set(scores))
    assert all(band["descriptor"].strip() for band in bands)


def test_schema_rejects_a_score_outside_the_seven_band_contract() -> None:
    schema = _load(SCHEMA_PATH)
    invalid = copy.deepcopy(_load(INSTRUMENT_PATH))
    invalid["content"]["scale"]["bands"][0]["score"] = 0

    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(invalid))
    assert errors


def test_content_hash_is_current_and_changes_with_a_descriptor() -> None:
    instrument = _load(INSTRUMENT_PATH)
    assert instrument["content_hash"] == _content_hash(instrument["content"])

    changed = copy.deepcopy(instrument["content"])
    changed["scale"]["bands"][3]["descriptor"] += " Clarified."
    assert _content_hash(changed) != instrument["content_hash"]


def test_memo_and_non_memo_contracts_resolve_without_inference() -> None:
    instrument = _load(INSTRUMENT_PATH)
    content = instrument["content"]

    assert _resolve_contract(instrument, "memo") == instrument["id"]
    assert _resolve_contract(instrument, "client_interview") == "legacy_weighted_rubric"
    assert content["dimensions"] == [
        {"id": "governing_law", "heading": "Governing law"},
        {
            "id": "strengths_and_weaknesses_both_sides",
            "heading": "Strengths and weaknesses of both sides",
        },
        {"id": "issues", "heading": "Issues"},
        {"id": "suggested_solutions", "heading": "Suggested solutions"},
        {"id": "theory_and_themes", "heading": "Theory and themes"},
        {"id": "elements_to_prevail", "heading": "Elements to prevail"},
        {"id": "liabilities_and_remedies", "heading": "Liabilities and remedies"},
    ]
    assert content["thresholds"] == {
        "default_competence_score": 4,
        "default_redo_eligible_below": 6,
        "configuration_precedence": ["instructor", "school", "default"],
    }
    assert content["layering"] == {
        "base_layer": "august_assessment_wave",
        "later_panel_requirements": "build_on_base",
    }
    relationship = content["legacy_weighted_rubric_relationship"]
    assert relationship["applies_to"] == ["non_memo", "legacy_exercise"]
    assert relationship["preserved_fields"] == [
        "criteria",
        "declared_total",
        "letter_grade_map",
    ]
    assert relationship["memo_score_effect"] == "none"
    assert content["presentation"]["score_4_label"] == "competent"
    assert content["presentation"]["letter_grade_translation"] == "prohibited"
