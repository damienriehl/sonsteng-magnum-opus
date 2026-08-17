#!/usr/bin/env python3
"""Build and validate the U14b agent proposal without mutating governed artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import day_zero  # noqa: E402
from apply_day_zero_review import resolve_raw_occurrence  # noqa: E402


PROPOSAL_REL = Path("docs/evidence/2026-08-17-day-zero-review-proposal.json")
SHEET_REL = Path("docs/decisions/2026-08-17-day-zero-review-decision-sheet.md")
HOLDOUTS_REL = Path("data/day-zero-holdouts.json")
AUDIT_REL = Path("data/day-zero-anchor-audit.json")
APPROVAL_REL = Path("docs/decisions/2026-08-17-day-zero-review-approval.md")
CONFIDENCES = {"high", "medium", "low"}
HOLDOUT_DISPOSITIONS = {
    "declared_holdout",
    "convertible",
    "needs_subject_matter_judgment",
}
ANCHOR_DISPOSITIONS = {
    "convertible_after_durable_locator_added",
    "declared_out_of_anchor_holdout",
    "needs_subject_matter_judgment",
}
MARKER_RE = re.compile(r"\{#b:[0-9a-f]{8}\}")
LINE_LOCATOR_RE = re.compile(r"^line:(\d+):raw-occurrence:(\d+)$")
APPROVED_RAW_CONTEXT_EXCEPTIONS = frozenset({
    (
        "anchor_attention|data/matters/m03-tort-meridian/case-file/"
        "exhibit-medical-summary.md|line:8:raw-occurrence:2|2025-02-12"
    ),
    (
        "anchor_attention|data/matters/m11-arbitration-il/case-file/"
        "timekeeping-records.md|line:7:raw-occurrence:2|2025-10-14"
    ),
})


def _load(path: Path):
    return json.loads(path.read_text())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(category: str, row: dict) -> str:
    # Literal is a necessary final component: the governed audit currently assigns
    # one raw table-row locator to two different dates.
    return "|".join((category, row["source"], row["locator"], row["literal"]))


def _compact(text: str, literal: str, radius: int = 150) -> str:
    text = MARKER_RE.sub("", text)
    text = " ".join(text.split())
    at = text.find(literal)
    if at < 0:
        raise ValueError(f"literal {literal!r} was not resolved in source context")
    start = max(0, at - radius)
    end = min(len(text), at + len(literal) + radius)
    return text[start:end]


def _json_scalars(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(child, str):
                yield child_path, child
            else:
                yield from _json_scalars(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}.{index}" if path else str(index)
            if isinstance(child, str):
                yield child_path, child
            else:
                yield from _json_scalars(child, child_path)


def _source_blocks(path: Path):
    raw = path.read_text()
    texts = [value for _, value in _json_scalars(json.loads(raw))] if path.suffix == ".json" else [raw]
    blocks = []
    for text in texts:
        for span in day_zero._markdown_blocks(text):
            if span.get("bid"):
                blocks.append(("b:" + span["bid"], span["raw"]))
    return blocks


def _context_for(repo: Path, row: dict) -> str:
    path = repo / row["source"]
    locator = row["locator"]
    literal = row["literal"]
    block_match = re.match(r"^(b:[0-9a-f]{8})(?::year:(\d+)|:(\d+))$", locator)
    if block_match:
        block_id, year_offset, date_ordinal = block_match.groups()
        matches = [raw for bid, raw in _source_blocks(path) if bid == block_id]
        if len(matches) != 1:
            raise ValueError(f"{row['source']}#{locator}: expected one source block, found {len(matches)}")
        clean = MARKER_RE.sub("", matches[0])
        if year_offset is not None:
            start = int(year_offset)
            if clean[start:start + len(literal)] != literal:
                raise ValueError(f"{row['source']}#{locator}: year offset no longer resolves")
        else:
            dated = day_zero._dated_matches(clean)
            ordinal = int(date_ordinal)
            if ordinal >= len(dated) or dated[ordinal].group(1) != literal:
                raise ValueError(f"{row['source']}#{locator}: date ordinal no longer resolves")
        return _compact(clean, literal)

    if ":date:" in locator and path.suffix == ".json":
        scalar_path = locator.rsplit(":date:", 1)[0]
        values = [value for candidate, value in _json_scalars(_load(path)) if candidate == scalar_path]
        if len(values) != 1:
            raise ValueError(f"{row['source']}#{locator}: JSON scalar no longer resolves")
        return _compact(values[0], literal)

    line_match = LINE_LOCATOR_RE.match(locator)
    if line_match:
        line, _, _ = resolve_raw_occurrence(repo, row)
        return _compact(line, literal)

    raw = path.read_text()
    if raw.count(literal) != 1:
        raise ValueError(f"{row['source']}#{locator}: unsupported ambiguous locator")
    return _compact(raw, literal)


def render_proposal(proposal: dict) -> str:
    return json.dumps(proposal, indent=2, ensure_ascii=False) + "\n"


def _governed_identities(repo: Path):
    holdouts = _load(repo / HOLDOUTS_REL)
    audit = _load(repo / AUDIT_REL)
    expected = Counter(
        ("holdout", row["source"], row["locator"], row["literal"])
        for row in holdouts["entries"]
    )
    expected.update(
        ("anchor_attention", row["source"], row["locator"], row["literal"])
        for row in audit["attention_required"]
    )
    return holdouts, audit, expected


def validate_proposal(repo: Path, proposal: dict) -> None:
    holdouts, audit, expected = _governed_identities(repo)
    anchors = {row["matter_slug"]: row["anchor"] for row in audit["matter_anchors"]}
    approved = (repo / APPROVAL_REL).is_file()
    if not approved:
        if not all(row.get("review_status") == "candidate_pending_human_review" for row in holdouts["entries"]):
            raise ValueError("governed holdouts no longer retain pending review states")
        if audit["summary"].get("attention_required") != len(audit["attention_required"]):
            raise ValueError("governed anchor audit no longer retains its attention-required set")
    actual = Counter()
    keys = set()
    previous = None
    for row in proposal.get("proposals", []):
        required = {
            "key", "category", "source", "locator", "literal", "matter", "current_state",
            "proposed_disposition", "reason_code", "rationale", "confidence", "needs_john",
            "context_excerpt",
        }
        missing = required - row.keys()
        if missing:
            raise ValueError(f"{row.get('key', '<unknown>')}: missing {sorted(missing)}")
        if row["category"] not in {"holdout", "anchor_attention"}:
            raise ValueError(f"{row['key']}: invalid category")
        if row["key"] in keys:
            raise ValueError(f"duplicate proposal key: {row['key']}")
        if row["key"] != _key(row["category"], row):
            raise ValueError(f"{row['key']}: key does not match category/source/locator/literal")
        if previous is not None and row["key"] <= previous:
            raise ValueError("proposal ordering is not deterministic key order")
        previous = row["key"]
        keys.add(row["key"])
        identity = (row["category"], row["source"], row["locator"], row["literal"])
        actual[identity] += 1
        allowed = HOLDOUT_DISPOSITIONS if row["category"] == "holdout" else ANCHOR_DISPOSITIONS
        expected_state = (
            "candidate_pending_human_review"
            if row["category"] == "holdout"
            else "attention_required"
        )
        if row["current_state"] != expected_state:
            raise ValueError(f"{row['key']}: proposal misstates governed pending state")
        if row["proposed_disposition"] not in allowed:
            raise ValueError(f"{row['key']}: invalid disposition")
        if (
            not isinstance(row["reason_code"], str)
            or not row["reason_code"].strip()
            or not isinstance(row["rationale"], str)
            or not row["rationale"].strip()
        ):
            raise ValueError(f"{row['key']}: empty reason")
        if row["confidence"] not in CONFIDENCES or not isinstance(row["needs_john"], bool):
            raise ValueError(f"{row['key']}: invalid confidence or needs_john")
        if not (repo / row["source"]).is_file():
            raise ValueError(f"{row['key']}: referenced source does not exist")
        if row["literal"] not in row["context_excerpt"]:
            raise ValueError(f"{row['key']}: context does not contain literal")
        resolved_context = _context_for(repo, row)
        if row["context_excerpt"] != resolved_context:
            # The approved proposal is immutable evidence. Its original raw-line
            # resolver copied the first matching context for two repeated dates.
            # After approval, require the exact occurrence to remain resolvable,
            # but do not rewrite the evidence or invalidate its recorded digest.
            approved_raw_evidence = (
                approved and row["key"] in APPROVED_RAW_CONTEXT_EXCEPTIONS
            )
            if not approved_raw_evidence:
                raise ValueError(
                    f"{row['key']}: stored context is stale or resolves another occurrence"
                )
        if row["proposed_disposition"] in {
            "convertible", "convertible_after_durable_locator_added"
        }:
            expected_anchor = anchors.get(row["matter"])
            if row.get("matter_anchor") != expected_anchor:
                raise ValueError(f"{row['key']}: convertible proposal has wrong matter anchor")
            expected_offset = day_zero.offset_days(
                row["literal"], day_zero.parse_date(expected_anchor)
            )
            if row.get("proposed_day_zero_offset") != expected_offset:
                raise ValueError(f"{row['key']}: convertible proposal has wrong offset")
    governed = proposal.get("governed_inputs", {})
    if approved:
        expected_counts = Counter({
            "holdout": governed.get(str(HOLDOUTS_REL), {}).get("pending_count"),
            "anchor_attention": governed.get(str(AUDIT_REL), {}).get("attention_required_count"),
        })
        if Counter(row["category"] for row in proposal.get("proposals", [])) != expected_counts:
            raise ValueError("proposal coverage mismatch against its immutable governed-input counts")
        for metadata in governed.values():
            if not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("sha256", ""))):
                raise ValueError("proposal governed-input digest is invalid")
    else:
        if actual != expected:
            missing = list((expected - actual).elements())[:3]
            unknown = list((actual - expected).elements())[:3]
            raise ValueError(f"proposal coverage mismatch; missing={missing}, unknown={unknown}")
        expected_inputs = {
            str(HOLDOUTS_REL): {
                "sha256": _sha256(repo / HOLDOUTS_REL),
                "pending_count": len(holdouts["entries"]),
            },
            str(AUDIT_REL): {
                "sha256": _sha256(repo / AUDIT_REL),
                "attention_required_count": len(audit["attention_required"]),
            },
        }
        if governed != expected_inputs:
            raise ValueError("proposal governed-input hashes or counts are stale")


def _table(counter: Counter, heading: str) -> list[str]:
    lines = [f"### {heading}", "", "| Value | Count |", "|---|---:|"]
    lines.extend(f"| {value} | {count} |" for value, count in sorted(counter.items()))
    lines.append("")
    return lines


def render_sheet(proposal: dict) -> str:
    rows = proposal["proposals"]
    by_category = Counter(row["category"] for row in rows)
    lines = [
        "# Day Zero holdout and anchor agent proposal — decision sheet",
        "",
        "This is the U14b **agent-proposal pass only**. The governed Day Zero JSON remains pending; "
        "nothing here is human approval or authority to run U15.",
        "",
        "## Live coverage",
        "",
        f"- Pending holdout candidates: **{by_category['holdout']}**",
        f"- Attention-required anchor cases: **{by_category['anchor_attention']}**",
        f"- Total one-to-one proposals: **{len(rows)}**",
        f"- `needs_john: true`: **{sum(row['needs_john'] for row in rows)}**",
        "",
        "The plan's 674/41 (715 total) snapshot is stale on this branch; the live governed files "
        f"contain {by_category['holdout']}/{by_category['anchor_attention']} ({len(rows)} total).",
        "",
        "## Summaries",
        "",
    ]
    lines += _table(Counter(row["proposed_disposition"] for row in rows), "By disposition")
    lines += _table(Counter(row["matter"] for row in rows), "By matter")
    lines += _table(Counter(row["reason_code"] for row in rows), "By reason")
    lines += _table(Counter(row["confidence"] for row in rows), "By confidence")
    lines += _table(Counter(str(row["needs_john"]).lower() for row in rows), "By `needs_john`")
    judgment = [
        row for row in rows
        if row["confidence"] == "low"
        or row["proposed_disposition"] == "needs_subject_matter_judgment"
        or row["needs_john"]
    ]
    locator_counts = Counter((row["source"], row["locator"]) for row in rows)
    ambiguous_locators = [
        row for row in rows if locator_counts[(row["source"], row["locator"])] > 1
    ]
    lines += ["## Judgment review batches", ""]
    if not judgment:
        lines += [
            "No low-confidence, subject-matter, or `needs_john` proposals were identified.",
            "",
        ]
    else:
        for batch_start in range(0, len(judgment), 10):
            lines.append(f"### Batch {batch_start // 10 + 1}")
            lines.append("")
            for row in judgment[batch_start:batch_start + 10]:
                lines.append(f"- `{row['key']}` — {row['rationale']}")
            lines.append("")
    lines += ["## Locator ambiguity to confirm mechanically", ""]
    for row in ambiguous_locators:
        lines.append(
            f"- `{row['key']}` — the governed raw-census locator is shared by another date on "
            "the same table row. The proposal key adds the literal; disposition remains a "
            "matter-relative conversion after durable locator remediation."
        )
    if not ambiguous_locators:
        lines.append("None.")
    john_count = sum(row["needs_john"] for row in rows)
    john_question = (
        f"Should the {john_count}-item `needs_john` subset go to John before governed JSON is "
        "updated?"
        if john_count
        else "Should the `needs_john` subset go to John? The proposed subset is currently empty; "
        "if Damien changes any item to a legal or pedagogical judgment during review, should "
        "those changed items be routed to John before governed JSON is updated?"
    )
    lines += [
        "",
        "## How Damien's approval is applied",
        "",
        "1. Review this sheet and the full evidence JSON; record approval or edits as a separate "
        "human decision artifact.",
        "2. In a later human-confirmation change, match each proposal by its exact "
        "`category|source|locator|literal` key. Do not use array position.",
        "3. For approved holdouts, update the matching governed holdout entry to the schema's "
        "human-confirmed status and approved fixed-fact reason; for approved convertible items, "
        "remove them from the holdout set only while adding them to the conversion inventory.",
        "4. For anchor cases, first add the proposed durable locator where required, then move the "
        "matching item from `attention_required` into the governed conversion audit or declared "
        "out-of-anchor holdout set. Recompute summaries and validate both schemas.",
        "5. Re-run the Day Zero dry-run and proposal validator. U15 may begin only after every "
        "governed item is resolved and the human approval artifact is present.",
        "",
        "No governed state should be bulk-replaced from this proposal JSON; applying approval is a "
        "reviewed, key-by-key mutation so omissions and the duplicate raw locator cannot be hidden.",
        "",
        "## Explicit U14b question",
        "",
        john_question,
        "",
    ]
    return "\n".join(lines)


def validate_rendered_artifacts(proposal: dict, proposal_text: str, sheet_text: str) -> None:
    if proposal_text != render_proposal(proposal):
        raise ValueError("proposal JSON is not normalized deterministic JSON")
    if sheet_text != render_sheet(proposal):
        raise ValueError("decision sheet is stale or non-deterministic")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=TOOLS.parent)
    args = parser.parse_args()
    repo = args.repo.resolve()
    proposal_path = repo / PROPOSAL_REL
    sheet_path = repo / SHEET_REL
    if not proposal_path.is_file() or not sheet_path.is_file():
        raise SystemExit("proposal artifacts are missing")
    committed = _load(proposal_path)
    validate_proposal(repo, committed)
    try:
        validate_rendered_artifacts(
            committed, proposal_path.read_text(), sheet_path.read_text()
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if (repo / APPROVAL_REL).is_file():
        import apply_day_zero_review

        apply_day_zero_review._approval_digest(repo, proposal_path)
        apply_day_zero_review.validate_applied_review(
            repo,
            committed,
            _load(repo / HOLDOUTS_REL),
            _load(repo / AUDIT_REL),
        )
    counts = Counter(row["category"] for row in committed["proposals"])
    print(json.dumps({
        "holdout_proposals": counts["holdout"],
        "anchor_attention_proposals": counts["anchor_attention"],
        "total": len(committed["proposals"]),
        "needs_john": sum(row["needs_john"] for row in committed["proposals"]),
        "status": "valid",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
