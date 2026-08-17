#!/usr/bin/env python3
"""Audit every matter packet against the nine-part exercise contract.

The audit is intentionally report-only: it reads the corpus, emits JSON on
stdout and a one-line human summary on stderr, and never rewrites packet data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


MIN_SECTION_WORDS = 150

PART_DEFINITIONS = (
    ("introduction", "Short introduction"),
    ("syllabus", "Syllabus"),
    ("planning_guide_checklist", "Planning guide and checklist"),
    ("learning_objectives", "Learning objectives"),
    ("legal_factual_history", "Legal and factual history"),
    ("dates_method", "Dates method"),
    ("witnesses_participants", "Description of witnesses and participants"),
    ("facts", "Facts"),
    ("assessment_feedback_form", "Assessment and feedback form"),
)

PART_SECTION_MAPPING = {
    "introduction": ("intro",),
    "planning_guide_checklist": ("activities", "instructions"),
    "learning_objectives": ("objectives",),
    "legal_factual_history": ("history",),
    "witnesses_participants": ("case_file",),
    "facts": ("case_file",),
}

DEDICATED_ARTIFACTS = {
    "syllabus": "exercise/syllabus.md",
    "dates_method": "exercise/dates-method.md",
    "assessment_feedback_form": "exercise/assessment-feedback-form.md",
}


def _word_count(value: str) -> int:
    return len(value.split())


def _is_nonempty_file(path: Path) -> bool:
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _issue(
    issue_class: str,
    gap_class: str,
    part: str | None,
    message: str,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "class": issue_class,
        "gap_class": gap_class,
        "message": message,
    }
    if part is not None:
        result["part"] = part
    if evidence:
        result["evidence"] = sorted(evidence)
    return result


def _part(part_id: str, title: str, status: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "id": part_id,
        "title": title,
        "status": status,
        "evidence": sorted(evidence),
    }


def _section_evidence(
    root: Path,
    packet_path: Path,
    sections: dict[str, Any],
    key: str,
    part_id: str,
    issues: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    section = sections.get(key)
    packet_ref = f"{_relative(packet_path, root)}#/sections/{key}"
    if not isinstance(section, dict):
        issues.append(
            _issue(
                "missing_section",
                "mechanical",
                part_id,
                f"Required mapped section '{key}' is absent.",
            )
        )
        return False, []

    body = section.get("body_md")
    files = section.get("files")
    has_body = isinstance(body, str) and _word_count(body) >= MIN_SECTION_WORDS
    has_files = isinstance(files, list) and bool(files)
    if not has_body and not has_files:
        issues.append(
            _issue(
                "trivial_section",
                "mechanical",
                part_id,
                f"Mapped section '{key}' has neither {MIN_SECTION_WORDS} words nor file references.",
                [packet_ref],
            )
        )
        return False, [packet_ref]
    return True, [packet_ref]


def _audit_matter(root: Path, matter_dir: Path) -> dict[str, Any]:
    packet_path = matter_dir / "exercise" / "exercise.json"
    issues: list[dict[str, Any]] = []
    matter_id = matter_dir.name.split("-", 1)[0]

    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(
            _issue(
                "invalid_json",
                "mechanical",
                None,
                f"Cannot read exercise packet as JSON: {exc}",
                [_relative(packet_path, root)],
            )
        )
        return {
            "matter": matter_dir.name,
            "matter_id": matter_id,
            "conforms": False,
            "parts": [
                _part(part_id, title, "not_auditable", [])
                for part_id, title in PART_DEFINITIONS
            ],
            "issues": issues,
        }

    if isinstance(packet.get("matter_id"), str):
        matter_id = packet["matter_id"]
    sections = packet.get("sections")
    if not isinstance(sections, dict):
        sections = {}

    mapped: dict[str, tuple[bool, list[str]]] = {}
    for part_id, keys in PART_SECTION_MAPPING.items():
        results = [
            _section_evidence(root, packet_path, sections, key, part_id, issues)
            for key in keys
        ]
        mapped[part_id] = (
            all(result[0] for result in results),
            [item for result in results for item in result[1]],
        )

    case_files = sections.get("case_file", {}).get("files", [])
    if isinstance(case_files, list):
        for reference in sorted(item for item in case_files if isinstance(item, str)):
            target = matter_dir / reference
            if not target.is_file():
                issues.append(
                    _issue(
                        "broken_file_reference",
                        "mechanical",
                        "facts",
                        f"Case-file reference does not resolve: {reference}",
                        [_relative(packet_path, root)],
                    )
                )
                mapped["facts"] = (False, mapped["facts"][1])

    facts_path = matter_dir / "facts.md"
    if not _is_nonempty_file(facts_path):
        issues.append(
            _issue(
                "missing_corpus_evidence",
                "mechanical",
                "facts",
                "The packet has no non-empty facts.md source.",
            )
        )
        mapped["facts"] = (False, mapped["facts"][1])
    else:
        mapped["facts"][1].append(_relative(facts_path, root))

    persona_files = sorted((matter_dir / "personas").glob("*.json"))
    witness_refs = [
        ref
        for ref in case_files
        if isinstance(ref, str) and ("witness" in ref.lower() or "statement" in ref.lower())
    ] if isinstance(case_files, list) else []
    if not persona_files or not witness_refs:
        issues.append(
            _issue(
                "missing_corpus_evidence",
                "mechanical",
                "witnesses_participants",
                "Witness/participant evidence requires both persona data and a witness or statement case-file reference.",
            )
        )
        mapped["witnesses_participants"] = (False, mapped["witnesses_participants"][1])
    else:
        mapped["witnesses_participants"][1].extend(
            _relative(path, root) for path in persona_files
        )

    parts: list[dict[str, Any]] = []
    for part_id, title in PART_DEFINITIONS:
        if part_id in DEDICATED_ARTIFACTS:
            artifact = matter_dir / DEDICATED_ARTIFACTS[part_id]
            if _is_nonempty_file(artifact):
                parts.append(_part(part_id, title, "conforming", [_relative(artifact, root)]))
            else:
                adjacent: list[str] = []
                if part_id == "assessment_feedback_form":
                    for candidate in (matter_dir / "rubric.json", matter_dir / "exercise" / "answer-key.md"):
                        if candidate.is_file():
                            adjacent.append(_relative(candidate, root))
                issues.append(
                    _issue(
                        "structural_gap",
                        "structural",
                        part_id,
                        f"No dedicated {title.lower()} artifact; the fixed eight-key schema has no field for it.",
                        adjacent,
                    )
                )
                parts.append(_part(part_id, title, "nonconforming", adjacent))
            continue

        ok, evidence = mapped[part_id]
        parts.append(_part(part_id, title, "conforming" if ok else "nonconforming", evidence))

    issues.sort(key=lambda item: (item.get("part", ""), item["class"], item["message"]))
    return {
        "matter": matter_dir.name,
        "matter_id": matter_id,
        "conforms": all(part["status"] == "conforming" for part in parts),
        "parts": parts,
        "issues": issues,
    }


def audit_repository(root: Path) -> dict[str, Any]:
    """Return a deterministic JSON-serializable audit report for *root*."""
    root = root.resolve()
    matter_root = root / "data" / "matters"
    matter_dirs = sorted(
        path
        for path in matter_root.glob("m[0-9][0-9]-*")
        if path.is_dir() and (path / "exercise").is_dir()
    )
    matters = [_audit_matter(root, path) for path in matter_dirs]
    gap_counts = Counter(
        issue["gap_class"] for matter in matters for issue in matter["issues"]
    )
    conforming = sum(matter["conforms"] for matter in matters)
    return {
        "audit": "nine-part-exercise-conformance",
        "report_version": 1,
        "report_only": True,
        "nine_parts": [
            {"id": part_id, "title": title} for part_id, title in PART_DEFINITIONS
        ],
        "schema_mapping": {
            key: sorted(
                part_id
                for part_id, keys in PART_SECTION_MAPPING.items()
                if key in keys
            )
            for key in (
                "intro",
                "objectives",
                "activities",
                "instructions",
                "case_file",
                "history",
                "considerations",
                "substantive_info",
            )
        },
        "spec_schema_disagreement": {
            "schema_part_count": 8,
            "spec_part_count": 9,
            "unrepresented_parts": [
                "syllabus",
                "dates_method",
                "assessment_feedback_form",
            ],
            "note": (
                "The schema's eight keys are content sections, not the specification's nine-part "
                "packet anatomy; widening the schema is outside U15."
            ),
        },
        "summary": {
            "matters_audited": len(matters),
            "conforming_matters": conforming,
            "nonconforming_matters": len(matters) - conforming,
            "mechanical_gaps": gap_counts["mechanical"],
            "structural_gaps": gap_counts["structural"],
        },
        "matters": matters,
    }


def human_summary(report: dict[str, Any]) -> str:
    summary = report["summary"]
    noun = "matter" if summary["matters_audited"] == 1 else "matters"
    return (
        f"Nine-part audit: {summary['matters_audited']} {noun}; "
        f"{summary['conforming_matters']} conforming; "
        f"{summary['nonconforming_matters']} nonconforming; "
        f"{summary['mechanical_gaps']} mechanical gaps; "
        f"{summary['structural_gaps']} structural gaps."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of tools/)",
    )
    args = parser.parse_args(argv)
    report = audit_repository(args.root)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    print(human_summary(report), file=sys.stderr)
    return 0 if report["summary"]["nonconforming_matters"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
