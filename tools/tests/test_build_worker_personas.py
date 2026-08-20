"""U10 build-contract tests for the embedded memo evaluator assets."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import jsonschema

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import build_worker_personas as builder  # noqa: E402


TEMPLATE = REPO / "app" / "worker" / "prompts" / "memo-scorecard-template.md"
INSTRUMENT = REPO / "data" / "curriculum" / "assessment-instrument.json"
SCHEMA = REPO / "data" / "schemas" / "memo-scorecard.schema.json"
MANIFEST = REPO / "data" / "spine-manifest.json"


def test_builder_embeds_memo_template_and_instrument_verbatim(tmp_path, monkeypatch) -> None:
    output = tmp_path / "personas.generated.json"
    monkeypatch.setattr(builder, "OUT_PATH", str(output))

    assert builder.main() == 0
    bundle = json.loads(output.read_text(encoding="utf-8"))
    expected_template = builder.read_template(
        str(TEMPLATE),
        "<!-- ===== BEGIN MEMO SCORECARD PROMPT ===== -->",
        "<!-- ===== END MEMO SCORECARD PROMPT ===== -->",
    )

    assert bundle["memo_scorecard_template"] == expected_template
    assert bundle["assessment_instrument"] == json.loads(
        INSTRUMENT.read_text(encoding="utf-8")
    )


def test_memo_prompt_defends_submission_boundary_and_omits_derived_scores() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")

    assert "{{ASSESSMENT_INSTRUMENT_JSON}}" in template
    assert "{{SUBMISSION}}" in template
    assert "BEGIN UNTRUSTED STUDENT SUBMISSION" in template
    assert "END UNTRUSTED STUDENT SUBMISSION" in template
    assert "overall_score" not in template
    assert "letter_grade" not in template
    assert "weight_points" not in template


def test_memo_scorecard_schema_is_valid_and_registered() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    instrument = json.loads(INSTRUMENT.read_text(encoding="utf-8"))
    heading_ids = [
        dimension["id"] for dimension in instrument["content"]["dimensions"]
    ]
    instance = {
        "schema_version": "1.0.0",
        "instrument_id": instrument["id"],
        "instrument_version": instrument["instrument_version"],
        "instrument_content_hash": instrument["content_hash"],
        "headings": [
            {
                "heading_id": heading_id,
                "evidence_spans": [f"verbatim evidence for {heading_id}"],
                "rationale": "Compared with the heading-specific anchors.",
                "score": index,
            }
            for index, heading_id in enumerate(heading_ids, start=1)
        ],
    }

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(instance)
    assert manifest["schemas"]["memo_scorecard"] == schema["version"]

    instance["overall_score"] = 7
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(instance))
    assert errors
