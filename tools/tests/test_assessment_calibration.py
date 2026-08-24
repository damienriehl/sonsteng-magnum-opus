from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import assessment_calibration as calibration  # noqa: E402


def complete_payload(count: int = 40) -> dict:
    rows = []
    for index in range(count):
        # Vary the ratings so perfect agreement has non-zero expected disagreement.
        scores = {
            heading: ((index + heading_index) % 7) + 1
            for heading_index, heading in enumerate(calibration.HEADING_IDS)
        }
        for role in calibration.RATER_ROLES:
            rows.append({"work_id": f"work-{index:03d}", "rater_role": role, "scores": dict(scores)})
    return {"ratings": rows}


def analyze(payload: dict, floor: float = 0.5, bias: float = 0.5) -> dict:
    return calibration.analyze(payload, min_kappa=floor, max_abs_signed_difference=bias)


def test_perfect_complete_sample_reports_only_aggregate_metrics():
    report = analyze(complete_payload())
    assert report["sample_size"] == 40
    assert report["calibration_pass"] is True
    assert set(report) == {"sample_size", "thresholds", "headings", "calibration_pass"}
    assert len(report["headings"]) == 7
    for heading in report["headings"]:
        for comparison in heading["comparisons"].values():
            assert comparison == {"quadratic_weighted_kappa": 1.0, "mean_signed_difference": 0.0}
    serialized = json.dumps(report)
    assert "work-" not in serialized
    assert "ratings" not in serialized


def test_systematic_panel_generosity_has_positive_signed_difference():
    payload = complete_payload()
    for row in payload["ratings"]:
        if row["rater_role"] == "panel":
            row["scores"] = {heading: min(7, score + 1) for heading, score in row["scores"].items()}
    report = analyze(payload, floor=0.0, bias=1.0)
    for heading in report["headings"]:
        assert heading["comparisons"]["panel_faculty_1"]["mean_signed_difference"] > 0
        assert heading["comparisons"]["panel_faculty_2"]["mean_signed_difference"] > 0


def test_panel_must_meet_both_humans_and_the_human_baseline():
    payload = complete_payload()
    for index, row in enumerate(payload["ratings"]):
        if row["rater_role"] == "panel":
            row["scores"] = {heading: ((index * 3 + offset) % 7) + 1
                             for offset, heading in enumerate(calibration.HEADING_IDS)}
    report = analyze(payload, floor=-1.0, bias=6.0)
    assert report["calibration_pass"] is False
    assert any(not heading["threshold_result"]["panel_faculty_1"] for heading in report["headings"])


@pytest.mark.parametrize("count", [39, 61])
def test_sample_size_is_bounded(count):
    with pytest.raises(calibration.CalibrationInputError, match="40 through 60"):
        analyze(complete_payload(count))


def test_missing_heading_out_of_range_and_duplicate_role_fail_closed():
    missing = complete_payload()
    missing["ratings"][0]["scores"].pop(calibration.HEADING_IDS[0])
    with pytest.raises(calibration.CalibrationInputError, match="exactly the seven"):
        analyze(missing)

    out_of_range = complete_payload()
    out_of_range["ratings"][0]["scores"][calibration.HEADING_IDS[0]] = 8
    with pytest.raises(calibration.CalibrationInputError, match="integer"):
        analyze(out_of_range)

    duplicate = complete_payload()
    duplicate["ratings"].append(copy.deepcopy(duplicate["ratings"][0]))
    with pytest.raises(calibration.CalibrationInputError, match="duplicate"):
        analyze(duplicate)


@pytest.mark.parametrize("extra", [{"name": "A Person"}, {"memo_text": "private work"}, {"email": "x@example.test"}])
def test_identity_or_free_text_fields_are_rejected(extra):
    payload = complete_payload()
    payload["ratings"][0].update(extra)
    with pytest.raises(calibration.CalibrationInputError, match="contain only"):
        analyze(payload)


def test_missing_role_and_unknown_top_level_fields_are_rejected():
    payload = complete_payload()
    payload["ratings"] = [row for row in payload["ratings"] if not (
        row["work_id"] == "work-000" and row["rater_role"] == "panel"
    )]
    with pytest.raises(calibration.CalibrationInputError, match="all three"):
        analyze(payload)
    with pytest.raises(calibration.CalibrationInputError, match="only the ratings"):
        analyze({**complete_payload(), "course": "private"})


def test_constant_ratings_produce_undefined_kappa_and_fail():
    payload = complete_payload()
    for row in payload["ratings"]:
        row["scores"] = {heading: 4 for heading in calibration.HEADING_IDS}
    report = analyze(payload, floor=-1.0, bias=6.0)
    assert report["calibration_pass"] is False
    assert all(
        heading["comparisons"]["faculty_1_faculty_2"]["quadratic_weighted_kappa"] is None
        for heading in report["headings"]
    )


def test_sub_floor_baseline_and_excessive_bias_fail():
    payload = complete_payload()
    for row in payload["ratings"]:
        if row["rater_role"] == "faculty-2":
            row["scores"] = {heading: 8 - score for heading, score in row["scores"].items()}
    assert analyze(payload, floor=0.9, bias=6.0)["calibration_pass"] is False

    generous = complete_payload()
    for row in generous["ratings"]:
        if row["rater_role"] == "panel":
            row["scores"] = {heading: min(7, score + 1) for heading, score in row["scores"].items()}
    assert analyze(generous, floor=-1.0, bias=0.01)["calibration_pass"] is False


@pytest.mark.parametrize(
    "floor,ceiling",
    [(None, 1.0), (0.5, None), (float("nan"), 1.0), (1.1, 1.0), (0.5, -0.1)],
)
def test_thresholds_are_required_finite_and_bounded(floor, ceiling):
    with pytest.raises(calibration.CalibrationInputError):
        analyze(complete_payload(), floor=floor, bias=ceiling)


def test_cli_requires_thresholds_and_never_rewrites_input(tmp_path):
    source = tmp_path / "ratings.json"
    source.write_text(json.dumps(complete_payload()))
    before = source.read_bytes()
    script = TOOLS / "assessment_calibration.py"
    missing = subprocess.run(
        [sys.executable, str(script), str(source)], capture_output=True, text=True, check=False
    )
    assert missing.returncode == 2
    assert "--min-kappa" in missing.stderr

    valid = subprocess.run(
        [sys.executable, str(script), str(source), "--min-kappa", "0.5",
         "--max-abs-signed-difference", "0.5"],
        capture_output=True, text=True, check=False,
    )
    assert valid.returncode == 0
    assert json.loads(valid.stdout)["calibration_pass"] is True
    assert source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]
