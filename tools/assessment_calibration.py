#!/usr/bin/env python3
"""Validate de-identified assessment ratings and emit aggregate calibration evidence.

The input is read once from a caller-owned local file.  This tool never writes
row-level data, accepts no identity or free-text fields, and supplies no policy
defaults for the summative-use thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
INSTRUMENT_PATH = REPO / "data" / "curriculum" / "assessment-instrument.json"
RATER_ROLES = ("faculty-1", "faculty-2", "panel")
WORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEADING_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MIN_WORKS = 40
MAX_WORKS = 60
MAX_INPUT_BYTES = 1_048_576


class CalibrationInputError(ValueError):
    """The caller-supplied calibration data is unsafe or incomplete."""


def _load_heading_ids() -> tuple[str, ...]:
    try:
        payload = json.loads(INSTRUMENT_PATH.read_text(encoding="utf-8"))
        dimensions = payload["content"]["dimensions"]
        heading_ids = tuple(dimension["id"] for dimension in dimensions)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CalibrationInputError("canonical assessment headings are unavailable") from exc
    if (
        len(heading_ids) != 7
        or len(set(heading_ids)) != 7
        or any(not isinstance(heading, str) or not HEADING_ID_RE.fullmatch(heading)
               for heading in heading_ids)
    ):
        raise CalibrationInputError("canonical assessment headings are invalid")
    return heading_ids


HEADING_IDS = _load_heading_ids()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationInputError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise CalibrationInputError(f"{label} must be a finite number")
    return number


def validate_thresholds(min_kappa: Any, max_abs_signed_difference: Any) -> tuple[float, float]:
    floor = _finite_number(min_kappa, "minimum kappa")
    ceiling = _finite_number(max_abs_signed_difference, "maximum absolute signed difference")
    if not -1.0 <= floor <= 1.0:
        raise CalibrationInputError("minimum kappa must be between -1 and 1")
    if not 0.0 <= ceiling <= 6.0:
        raise CalibrationInputError("maximum absolute signed difference must be between 0 and 6")
    return floor, ceiling


def validate_ratings(payload: Any) -> dict[str, dict[str, dict[str, int]]]:
    if not isinstance(payload, dict) or set(payload) != {"ratings"}:
        raise CalibrationInputError("input must contain only the ratings array")
    rows = payload["ratings"]
    if not isinstance(rows, list):
        raise CalibrationInputError("ratings must be an array")

    works: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"work_id", "rater_role", "scores"}:
            raise CalibrationInputError("each rating must contain only work_id, rater_role, and scores")
        work_id = row["work_id"]
        role = row["rater_role"]
        scores = row["scores"]
        if not isinstance(work_id, str) or not WORK_ID_RE.fullmatch(work_id):
            raise CalibrationInputError("work_id must be an opaque bounded identifier")
        if role not in RATER_ROLES:
            raise CalibrationInputError("rater_role must be faculty-1, faculty-2, or panel")
        if not isinstance(scores, dict) or set(scores) != set(HEADING_IDS):
            raise CalibrationInputError("scores must contain exactly the seven memo headings")
        if any(isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 7
               for score in scores.values()):
            raise CalibrationInputError("every heading score must be an integer from 1 through 7")
        by_role = works.setdefault(work_id, {})
        if role in by_role:
            raise CalibrationInputError("duplicate work-role rating")
        by_role[role] = {heading: scores[heading] for heading in HEADING_IDS}

    if not MIN_WORKS <= len(works) <= MAX_WORKS:
        raise CalibrationInputError(f"sample must contain {MIN_WORKS} through {MAX_WORKS} works")
    if any(set(by_role) != set(RATER_ROLES) for by_role in works.values()):
        raise CalibrationInputError("every work must contain all three anonymous rater roles")
    return works


def quadratic_weighted_kappa(left: Sequence[int], right: Sequence[int]) -> float | None:
    """Return quadratic weighted Cohen's kappa across the fixed 1-7 scale."""
    if len(left) != len(right) or not left:
        raise ValueError("paired non-empty ratings are required")
    left_counts = Counter(left)
    right_counts = Counter(right)
    observed_disagreement = sum((a - b) ** 2 for a, b in zip(left, right)) / len(left)
    expected_disagreement = sum(
        left_counts[a] * right_counts[b] * (a - b) ** 2
        for a in range(1, 8)
        for b in range(1, 8)
    ) / (len(left) ** 2)
    if expected_disagreement == 0:
        return None
    return 1.0 - (observed_disagreement / expected_disagreement)


def _rounded(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if rounded == -0.0 else rounded


def _comparison(
    reference: Sequence[int], comparison: Sequence[int]
) -> tuple[dict[str, Any], float | None, float]:
    raw_kappa = quadratic_weighted_kappa(reference, comparison)
    raw_signed_difference = (
        sum(candidate - baseline for baseline, candidate in zip(reference, comparison))
        / len(reference)
    )
    return {
        "quadratic_weighted_kappa": None if raw_kappa is None else _rounded(raw_kappa),
        "mean_signed_difference": _rounded(raw_signed_difference),
    }, raw_kappa, raw_signed_difference


def analyze(
    payload: Any,
    *,
    min_kappa: Any,
    max_abs_signed_difference: Any,
) -> dict[str, Any]:
    floor, ceiling = validate_thresholds(min_kappa, max_abs_signed_difference)
    works = validate_ratings(payload)
    work_ids = sorted(works)
    heading_results = []
    overall_pass = True

    for heading in HEADING_IDS:
        faculty_1 = [works[work_id]["faculty-1"][heading] for work_id in work_ids]
        faculty_2 = [works[work_id]["faculty-2"][heading] for work_id in work_ids]
        panel = [works[work_id]["panel"][heading] for work_id in work_ids]
        baseline, baseline_kappa, _ = _comparison(faculty_1, faculty_2)
        panel_f1, panel_f1_kappa, panel_f1_bias = _comparison(faculty_1, panel)
        panel_f2, panel_f2_kappa, panel_f2_bias = _comparison(faculty_2, panel)
        baseline_pass = baseline_kappa is not None and baseline_kappa >= floor
        comparisons_pass = {}
        for label, kappa, raw_bias in (
            ("panel_faculty_1", panel_f1_kappa, panel_f1_bias),
            ("panel_faculty_2", panel_f2_kappa, panel_f2_bias),
        ):
            comparisons_pass[label] = (
                baseline_pass
                and kappa is not None
                and kappa >= floor
                and kappa >= baseline_kappa
                and abs(raw_bias) <= ceiling
            )
        heading_pass = baseline_pass and all(comparisons_pass.values())
        overall_pass = overall_pass and heading_pass
        heading_results.append({
            "heading_id": heading,
            "comparisons": {
                "faculty_1_faculty_2": baseline,
                "panel_faculty_1": panel_f1,
                "panel_faculty_2": panel_f2,
            },
            "threshold_result": {
                "faculty_baseline": baseline_pass,
                **comparisons_pass,
                "pass": heading_pass,
            },
        })

    return {
        "sample_size": len(works),
        "thresholds": {
            "minimum_kappa": floor,
            "maximum_absolute_signed_difference": ceiling,
        },
        "headings": heading_results,
        "calibration_pass": overall_pass,
    }


def human_summary(report: dict[str, Any]) -> str:
    lines = [
        f"Calibration: {'PASS' if report['calibration_pass'] else 'FAIL'}",
        f"Complete de-identified works: {report['sample_size']}",
    ]
    for heading in report["headings"]:
        comparisons = heading["comparisons"]
        baseline = comparisons["faculty_1_faculty_2"]["quadratic_weighted_kappa"]
        panel_1 = comparisons["panel_faculty_1"]
        panel_2 = comparisons["panel_faculty_2"]
        lines.append(
            f"{heading['heading_id']}: {'PASS' if heading['threshold_result']['pass'] else 'FAIL'}; "
            f"baseline kappa={baseline}; panel/faculty-1 kappa={panel_1['quadratic_weighted_kappa']} "
            f"bias={panel_1['mean_signed_difference']}; panel/faculty-2 "
            f"kappa={panel_2['quadratic_weighted_kappa']} bias={panel_2['mean_signed_difference']}"
        )
    return "\n".join(lines) + "\n"


def _read_payload(path: Path) -> Any:
    with path.open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise CalibrationInputError("input exceeds the calibration size limit")
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationInputError("input must be valid UTF-8 JSON") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="caller-owned de-identified JSON file")
    parser.add_argument("--min-kappa", type=float, required=True)
    parser.add_argument("--max-abs-signed-difference", type=float, required=True)
    parser.add_argument("--human", action="store_true", help="emit an aggregate human-readable summary")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = _read_payload(args.input)
        report = analyze(
            payload,
            min_kappa=args.min_kappa,
            max_abs_signed_difference=args.max_abs_signed_difference,
        )
    except (OSError, json.JSONDecodeError, CalibrationInputError) as exc:
        print(f"calibration input rejected: {exc}", file=sys.stderr)
        return 2
    if args.human:
        sys.stdout.write(human_summary(report))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["calibration_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
