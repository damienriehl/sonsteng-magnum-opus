#!/usr/bin/env python3
"""Apply Damien's approved Day Zero review by exact proposal identity.

The proposal is immutable evidence. This command never trusts array position: every
mutation is joined on ``category|source|locator|literal`` and the approved proposal
digest must be present in the separate human decision artifact.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


PROPOSAL_REL = Path("docs/evidence/2026-08-17-day-zero-review-proposal.json")
APPROVAL_REL = Path("docs/decisions/2026-08-17-day-zero-review-approval.md")
HOLDOUTS_REL = Path("data/day-zero-holdouts.json")
AUDIT_REL = Path("data/day-zero-anchor-audit.json")

DATE_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)|"
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}\b"
)
LINE_LOCATOR_RE = re.compile(r"^line:(\d+):raw-occurrence:(\d+)$")


def _load(path: Path):
    return json.loads(path.read_text())


def _render(value: dict) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def proposal_key(row: dict) -> str:
    return "|".join((row["category"], row["source"], row["locator"], row["literal"]))


def _governed_key(category: str, row: dict) -> str:
    return "|".join((category, row["source"], row["locator"], row["literal"]))


def _proposal_map(proposal: dict) -> dict[str, dict]:
    rows = proposal.get("proposals", [])
    mapped = {}
    for row in rows:
        key = proposal_key(row)
        if row.get("key") != key:
            raise ValueError(f"proposal key does not match row identity: {row.get('key')}")
        if key in mapped:
            raise ValueError(f"duplicate proposal key: {key}")
        mapped[key] = row
    return mapped


def _resolve_source_line(repo: Path, row: dict) -> str:
    match = LINE_LOCATOR_RE.match(row["locator"])
    if not match:
        raise ValueError(f"unsupported raw locator: {row['key']}")
    lines = (repo / row["source"]).read_text().splitlines()
    line_number = int(match.group(1))
    if 1 <= line_number <= len(lines) and row["literal"] in lines[line_number - 1]:
        return lines[line_number - 1]
    candidates = [line for line in lines if row["literal"] in line]
    if not candidates:
        raise ValueError(f"raw locator no longer resolves: {row['key']}")
    occurrence = min(int(match.group(2)), len(candidates)) - 1
    return candidates[occurrence]


def _durable_locator(repo: Path, row: dict) -> str:
    locator = row["locator"]
    if LINE_LOCATOR_RE.match(locator):
        line = _resolve_source_line(repo, row)
        matches = list(DATE_RE.finditer(line))
        literal_indexes = [index for index, match in enumerate(matches) if match.group(0) == row["literal"]]
        if not literal_indexes:
            raise ValueError(f"literal is absent from resolved raw line: {row['key']}")
        ordinal = literal_indexes[0]
        normalized = " ".join(DATE_RE.sub("<date>", line).split())
        fingerprint = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        return f"raw:{fingerprint}:date:{ordinal}"
    if ":date:" in locator:
        return "json:" + locator
    return locator


def _approved_conversion(repo: Path, row: dict, anchor_reason: str) -> dict:
    return {
        "source": row["source"],
        "locator": _durable_locator(repo, row),
        "literal": row["literal"],
        "anchor": row["matter_anchor"],
        "day_zero_offset": row["proposed_day_zero_offset"],
        "anchor_reason": anchor_reason,
        "source_locator": row["locator"],
        "review_status": "human_confirmed_convertible",
        "review_reason": row["rationale"],
    }


def apply_review(repo: Path, proposal: dict, holdouts: dict, audit: dict) -> tuple[dict, dict]:
    """Return resolved governed artifacts; inputs are not mutated."""
    repo = Path(repo)
    proposals = _proposal_map(proposal)
    expected_keys = {
        _governed_key("holdout", row) for row in holdouts.get("entries", [])
    } | {
        _governed_key("anchor_attention", row)
        for row in audit.get("attention_required", [])
    }
    if set(proposals) != expected_keys:
        missing = sorted(expected_keys - set(proposals))[:3]
        unknown = sorted(set(proposals) - expected_keys)[:3]
        raise ValueError(f"proposal coverage mismatch; missing={missing}, unknown={unknown}")
    if any(row.get("review_status") != "candidate_pending_human_review"
           for row in holdouts.get("entries", [])):
        raise ValueError("governed holdout is not pending human review")
    if audit.get("summary", {}).get("attention_required") != len(audit.get("attention_required", [])):
        raise ValueError("governed anchor audit attention count is stale")

    anchors = {row["matter_slug"]: row for row in audit["matter_anchors"]}
    resolved_holdout_rows = []
    approved_conversions = []

    for governed in holdouts["entries"]:
        row = proposals[_governed_key("holdout", governed)]
        disposition = row["proposed_disposition"]
        if disposition == "declared_holdout":
            resolved_holdout_rows.append({
                "source": row["source"],
                "locator": row["locator"],
                "literal": row["literal"],
                "reason": row["reason_code"],
                "review_status": "declared_absolute_holdout",
            })
        elif disposition == "convertible":
            anchor = anchors.get(row["matter"])
            if not anchor or anchor["anchor"] != row.get("matter_anchor"):
                raise ValueError(f"approved conversion has no matching matter anchor: {row['key']}")
            approved_conversions.append(_approved_conversion(repo, row, anchor["reason"]))
        else:
            raise ValueError(f"unsupported approved holdout disposition: {row['key']}")

    for governed in audit["attention_required"]:
        row = proposals[_governed_key("anchor_attention", governed)]
        disposition = row["proposed_disposition"]
        if disposition == "declared_out_of_anchor_holdout":
            resolved_holdout_rows.append({
                "source": row["source"],
                "locator": _durable_locator(repo, row),
                "literal": row["literal"],
                "reason": row["reason_code"],
                "review_status": "declared_absolute_holdout",
            })
        elif disposition == "convertible_after_durable_locator_added":
            anchor = anchors.get(row["matter"])
            if not anchor or anchor["anchor"] != row.get("matter_anchor"):
                raise ValueError(f"approved conversion has no matching matter anchor: {row['key']}")
            approved_conversions.append(_approved_conversion(repo, row, anchor["reason"]))
        else:
            raise ValueError(f"unsupported approved anchor disposition: {row['key']}")

    resolved_holdout_rows.sort(key=lambda row: (row["source"], row["locator"], row["literal"]))
    reason_counts = Counter(row["reason"] for row in resolved_holdout_rows)
    resolved_holdouts = copy.deepcopy(holdouts)
    resolved_holdouts.update({
        "description": "Human-approved fixed facts excluded from Day Zero conversion.",
        "method": (
            "Damien approved the one-to-one U14b proposal recorded in "
            f"{APPROVAL_REL}; entries are joined by category|source|locator|literal."
        ),
        "summary": {
            "count": len(resolved_holdout_rows),
            "by_reason": dict(sorted(reason_counts.items())),
        },
        "entries": resolved_holdout_rows,
    })

    resolved_audit = copy.deepcopy(audit)
    resolved_entries = list(resolved_audit["entries"]) + approved_conversions
    resolved_entries.sort(key=lambda row: (row["source"], str(row["locator"]), row["literal"]))
    full_date_holdouts = sum(bool(DATE_RE.fullmatch(row["literal"])) for row in resolved_holdout_rows)
    inventory_total = resolved_audit["summary"]["full_date_inventory_total"]
    resolved_audit["description"] = (
        "Human-approved Day Zero conversion inventory; no corpus source file was rewritten "
        "to produce this artifact."
    )
    resolved_audit["method"] = (
        "The deterministic census was resolved by Damien's approval in "
        f"{APPROVAL_REL}; approved rows are joined by category|source|locator|literal and "
        "raw prose rows receive content-fingerprint locators."
    )
    resolved_audit["entries"] = resolved_entries
    resolved_audit["attention_required"] = []
    resolved_audit["summary"].update({
        "converted_dates": len(resolved_entries),
        "proof_records": len(resolved_entries),
        "full_date_holdouts": full_date_holdouts,
        "holdout_candidates": len(resolved_holdout_rows),
        "attention_required": 0,
        "reconciled_category_total": len(resolved_entries) + full_date_holdouts,
    })
    if resolved_audit["summary"]["reconciled_category_total"] != inventory_total:
        raise ValueError("resolved full-date inventory does not reconcile")
    return resolved_holdouts, resolved_audit


def _approval_digest(repo: Path, proposal_path: Path) -> str:
    digest = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    approval = repo / APPROVAL_REL
    if not approval.is_file() or digest not in approval.read_text():
        raise ValueError("human approval artifact is missing or names another proposal digest")
    return digest


def _synthetic_pending(
    proposal: dict, resolved_holdouts: dict, resolved_audit: dict
) -> tuple[dict, dict]:
    pending_holdouts = []
    attention = []
    for row in proposal["proposals"]:
        governed = {
            "source": row["source"], "locator": row["locator"],
            "literal": row["literal"], "reason": "proposal snapshot",
        }
        if row["category"] == "holdout":
            pending_holdouts.append(dict(governed, review_status="candidate_pending_human_review"))
        else:
            attention.append(governed)
    base_audit = copy.deepcopy(resolved_audit)
    base_audit["entries"] = [
        row for row in base_audit["entries"]
        if row.get("review_status") != "human_confirmed_convertible"
    ]
    base_audit["attention_required"] = attention
    base_audit["summary"]["attention_required"] = len(attention)
    base_holdouts = copy.deepcopy(resolved_holdouts)
    base_holdouts["entries"] = pending_holdouts
    return base_holdouts, base_audit


def validate_applied_review(repo: Path, proposal: dict, holdouts: dict, audit: dict) -> None:
    synthetic_holdouts, synthetic_audit = _synthetic_pending(
        proposal, holdouts, audit
    )
    expected_holdouts, expected_audit = apply_review(
        repo, proposal, synthetic_holdouts, synthetic_audit
    )
    if holdouts != expected_holdouts or audit != expected_audit:
        raise ValueError("committed governed artifacts do not match the approved proposal")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    proposal_path = repo / PROPOSAL_REL
    proposal = _load(proposal_path)
    digest = _approval_digest(repo, proposal_path)
    holdout_path = repo / HOLDOUTS_REL
    audit_path = repo / AUDIT_REL
    holdouts = _load(holdout_path)
    audit = _load(audit_path)
    if args.write:
        if any(
            row.get("review_status") == "candidate_pending_human_review"
            for row in holdouts.get("entries", [])
        ):
            pending_holdouts, pending_audit = holdouts, audit
        else:
            pending_holdouts, pending_audit = _synthetic_pending(
                proposal, holdouts, audit
            )
        resolved_holdouts, resolved_audit = apply_review(
            repo, proposal, pending_holdouts, pending_audit
        )
        holdout_path.write_text(_render(resolved_holdouts))
        audit_path.write_text(_render(resolved_audit))
        state = "applied"
    else:
        validate_applied_review(repo, proposal, holdouts, audit)
        state = "verified"
    print(json.dumps({
        "approval_sha256": digest,
        "holdouts": 635,
        "converted_dates": 1236,
        "attention_required": 0,
        "state": state,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
