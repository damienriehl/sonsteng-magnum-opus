#!/usr/bin/env python3
"""Deterministic Day Zero inventory/converter. Default operation is dry-run."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from collections import Counter
import difflib

import json_surgical
import build_site
import day_zero_equivalence

ISO_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
ISO_LIKE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
LONG_RE = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})\b"
)
YEAR_RE = re.compile(r"(?<![\d-])(?:18|19|20)\d{2}(?![\d-])")
BLOCK_RE = re.compile(r"\{#(b:[0-9a-f]{8})\}\s*$")
STATUTORY_RE = re.compile(r"\b(statute|statutory|code|act|effective|enacted)\b", re.I)
CITATION_RE = re.compile(r"\b\d+\s+(?:U\.S\.|F\.\s?\d|F\.\s?Supp\.|N\.W\.\s?2d|S\.Ct\.)\s+\d+.*\((?:[^)]*\s)?(?:18|19|20)\d{2}\)")


@dataclass(frozen=True)
class Classification:
    kind: str
    reason: str


@dataclass
class Result:
    audit: list[dict] = field(default_factory=list)
    holdouts: list[dict] = field(default_factory=list)
    unclassified: list[dict] = field(default_factory=list)
    touched_files: set[str] = field(default_factory=set)
    matter_anchors: list[dict] = field(default_factory=list)
    iso_dates: int = 0
    long_form_dates: int = 0
    full_date_holdouts: int = 0
    iso_like_raw_occurrences: int = 0
    excluded_non_dates: list[dict] = field(default_factory=list)
    before_files: dict[str, bytes] = field(default_factory=dict)
    after_files: dict[str, bytes] = field(default_factory=dict)
    file_proofs: list[day_zero_equivalence.FileProof] = field(default_factory=list)
    date_proofs: list[day_zero_equivalence.DateProof] = field(default_factory=list)

    @property
    def converted_dates(self):
        return len(self.date_proofs)

    @property
    def proof_records(self):
        return len(self.date_proofs)


def parse_date(value: str) -> date:
    """Parse supported literals with the standard library's calendar parser."""
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("unsupported or invalid date: %s" % value)


def offset_days(value: str, anchor: date) -> int:
    return (parse_date(value) - anchor).days


def classify_candidate(text: str, source: str, locator: str) -> Classification:
    del source, locator
    if CITATION_RE.search(text):
        return Classification("holdout", "case-citation year is a fixed fact")
    if STATUTORY_RE.search(text) and (ISO_RE.search(text) or LONG_RE.search(text) or YEAR_RE.search(text)):
        return Classification("holdout", "statutory/effective-date context is a fixed fact")
    if YEAR_RE.fullmatch(text.strip()):
        return Classification("holdout", "bare year is a fixed-fact candidate")
    return Classification("convert", "dated matter fact relative to matter open_date")


def _scalar_strings(value, path="") -> Iterable[tuple[str, str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = "%s.%s" % (path, key) if path else key
            if isinstance(child, str):
                yield path, key, child
            else:
                yield from _scalar_strings(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_strings(child, "%s.%d" % (path, index) if path else str(index))


def _record(result: Result, source: str, locator: str, literal: str, anchor: date, reason: str):
    offset = offset_days(literal, anchor)
    result.date_proofs.append(day_zero_equivalence.DateProof(
        source, locator, literal, anchor.isoformat(), offset
    ))
    result.audit.append({"source": source, "locator": locator, "literal": literal,
                         "anchor": anchor.isoformat(), "day_zero_offset": offset,
                         "anchor_reason": reason})


def _capture_file_proof(result: Result, source: str, before: bytes, after: bytes):
    if before == after:
        return
    result.before_files[source] = before
    result.after_files[source] = after
    reverse_edits = []
    for tag, after_start, after_end, before_start, before_end in difflib.SequenceMatcher(
            None, after, before, autojunk=False).get_opcodes():
        if tag != "equal":
            reverse_edits.append(day_zero_equivalence.ReverseEdit(
                after_start, after_end, before[before_start:before_end]
            ))
    result.file_proofs.append(day_zero_equivalence.FileProof(source, tuple(reverse_edits)))


def _dated_matches(text: str):
    clean = re.sub(r"\{#b:[0-9a-f]{8}\}", "", text)
    return sorted(list(ISO_RE.finditer(clean)) + list(LONG_RE.finditer(clean)), key=lambda match: match.start())


def _append_prose_candidates(result, sidecar_entries, text, source, block_id, anchor, reason,
                             locator_prefix=""):
    occupied = []
    for occurrence, match in enumerate(_dated_matches(text)):
        occupied.append((match.start(), match.end()))
        literal = match.group(1)
        locator = "%s:%d" % (block_id, occurrence) if block_id else "%sdate:%d" % (
            locator_prefix + ":" if locator_prefix else "", occurrence)
        classification = classify_candidate(text, source, locator)
        if classification.kind == "holdout":
            result.holdouts.append({"source": source, "locator": locator, "literal": literal,
                                    "reason": classification.reason})
        elif not block_id:
            result.unclassified.append({"source": source, "locator": locator, "literal": literal,
                                        "reason": "prose date has no durable block ID"})
        else:
            _record(result, source, locator, literal, anchor, reason)
            sidecar_entries.append({"source": source, "block_id": block_id, "locator": occurrence,
                                    "literal": literal, "day_zero_offset": offset_days(literal, anchor)})
    marker_stripped = re.sub(r"\{#b:[0-9a-f]{8}\}", "", text)
    for match in YEAR_RE.finditer(marker_stripped):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        literal = match.group(0)
        locator = "%s:year:%d" % (block_id, match.start()) if block_id else "year:%d" % match.start()
        classification = classify_candidate(text, source, locator)
        result.holdouts.append({"source": source, "locator": locator, "literal": literal,
                                "reason": classification.reason if classification.kind == "holdout"
                                else "bare year is a fixed-fact candidate"})


def _markdown_blocks(text: str):
    spans = []
    build_site.markdown(text, spans=spans)
    return spans


def convert_corpus(repo: Path, write: bool = False) -> Result:
    repo = Path(repo)
    result = Result()
    matters = repo / "data" / "matters"
    for matter_json in sorted(matters.glob("*/matter.json")):
        matter_dir = matter_json.parent
        matter = json.loads(matter_json.read_text())
        anchor = parse_date(matter["open_date"])
        reason = "matter %s existing open_date" % matter.get("id", matter_dir.name)
        result.matter_anchors.append({"matter_id": matter.get("id"), "matter_slug": matter_dir.name,
                                      "anchor": anchor.isoformat(), "reason": reason})
        sidecar_entries = []
        for path in sorted(matter_dir.rglob("*.json")):
            if path.name == "date-offsets.json":
                continue
            raw = path.read_text()
            obj = json.loads(raw)
            insertions = []
            for parent, key, value in _scalar_strings(obj):
                if key.endswith("_day_zero_offset"):
                    continue
                matches = _dated_matches(value)
                if not matches:
                    continue
                # Additive siblings are unambiguous only for a scalar that is exactly a date.
                if len(matches) == 1 and matches[0].group(1) == value:
                    literal = value
                    locator = "%s.%s" % (parent, key) if parent else key
                    classification = classify_candidate(value, str(path.relative_to(repo)), locator)
                    if classification.kind == "holdout":
                        result.holdouts.append({"source": str(path.relative_to(repo)), "locator": locator,
                                                "literal": literal, "reason": classification.reason})
                        continue
                    _record(result, str(path.relative_to(repo)), locator, literal, anchor, reason)
                    insertions.append((parent, key + "_day_zero_offset", offset_days(literal, anchor)))
                else:
                    source = str(path.relative_to(repo))
                    spans = _markdown_blocks(value)
                    if any(span["bid"] for span in spans):
                        for span in spans:
                            _append_prose_candidates(result, sidecar_entries, span["raw"], source,
                                                     "b:" + span["bid"] if span["bid"] else None,
                                                     anchor, reason)
                    else:
                        json_locator = "%s.%s" % (parent, key) if parent else key
                        _append_prose_candidates(result, sidecar_entries, value, source, None, anchor,
                                                 reason, locator_prefix=json_locator)
            if insertions:
                source = str(path.relative_to(repo))
                result.touched_files.add(source)
                converted_raw = json_surgical.insert_object_properties(raw, insertions)
                _capture_file_proof(result, source, raw.encode(), converted_raw.encode())
                if write:
                    path.write_text(converted_raw)
        for md in sorted(matter_dir.rglob("*.md")):
            raw = md.read_text()
            source = str(md.relative_to(repo))
            for span in _markdown_blocks(raw):
                _append_prose_candidates(result, sidecar_entries, span["raw"], source,
                                         "b:" + span["bid"] if span["bid"] else None,
                                         anchor, reason)
            if sidecar_entries:
                result.touched_files.add(str((matter_dir / "date-offsets.json").relative_to(repo)))
        if sidecar_entries:
            sidecar = {"schema_version": "1.0.0", "matter_id": matter.get("id"),
                       "anchor": anchor.isoformat(), "entries": sidecar_entries}
            text = json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n"
            target = matter_dir / "date-offsets.json"
            source = str(target.relative_to(repo))
            before = target.read_bytes() if target.exists() else b""
            after = text.encode()
            _capture_file_proof(result, source, before, after)
            if write and before != after:
                target.write_text(text)
    _reconcile_full_date_inventory(repo, result)
    return result


def _raw_full_date_inventory(repo: Path):
    rows = []
    excluded = []
    for tree_name in ("matters", "curriculum", "jurisdictions"):
        tree = repo / "data" / tree_name
        if not tree.exists():
            continue
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            try:
                raw = path.read_text()
            except UnicodeDecodeError:
                continue
            source = str(path.relative_to(repo))
            for ordinal, match in enumerate(ISO_LIKE_RE.finditer(raw), 1):
                bounded = not ((match.start() and raw[match.start() - 1].isdigit()) or
                               (match.end() < len(raw) and raw[match.end()].isdigit()))
                if bounded:
                    continue
                excluded.append({"source": source,
                                 "locator": "line:%d:iso-like:%d" % (raw.count("\n", 0, match.start()) + 1, ordinal),
                                 "literal": match.group(0),
                                 "reason": "substring of a longer identifier, not a standalone date"})
            for match in _dated_matches(raw):
                line = raw.count("\n", 0, match.start()) + 1
                rows.append({"source": source, "literal": match.group(1), "line": line,
                             "format": "iso" if ISO_RE.fullmatch(match.group(1)) else "long_form"})
    return rows, excluded


def _reconcile_full_date_inventory(repo: Path, result: Result):
    inventory, excluded = _raw_full_date_inventory(repo)
    result.excluded_non_dates = excluded
    result.iso_like_raw_occurrences = sum(row["format"] == "iso" for row in inventory) + len(excluded)
    result.iso_dates = sum(row["format"] == "iso" for row in inventory)
    result.long_form_dates = sum(row["format"] == "long_form" for row in inventory)
    categorized = Counter((row["source"], row["literal"]) for row in result.audit)
    full_holdouts = []
    for row in result.holdouts:
        try:
            parse_date(row["literal"])
        except ValueError:
            continue
        full_holdouts.append(row)
        categorized[(row["source"], row["literal"])] += 1
    result.full_date_holdouts = len(full_holdouts)
    for row in result.unclassified:
        categorized[(row["source"], row.get("literal", ""))] += 1
    seen = Counter()
    for row in inventory:
        key = (row["source"], row["literal"])
        seen[key] += 1
        if seen[key] <= categorized[key]:
            continue
        reason = ("date is outside data/matters and has no matter open_date anchor"
                  if not row["source"].startswith("data/matters/")
                  else "date occurs outside a renderer-recognized durable block")
        result.unclassified.append({"source": row["source"],
                                    "locator": "line:%d:raw-occurrence:%d" % (row["line"], seen[key]),
                                    "literal": row["literal"], "reason": reason})
        categorized[key] += 1
    expected = len(inventory)
    actual = result.converted_dates + result.full_date_holdouts + len(result.unclassified)
    if actual != expected:
        raise RuntimeError("date inventory reconciliation failed: %d categorized != %d inventoried"
                           % (actual, expected))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write", action="store_true", help="write corpus offsets (U8 only)")
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--holdouts-output", type=Path)
    args = parser.parse_args()
    result = convert_corpus(args.repo, write=args.write)
    if args.audit_output:
        args.audit_output.write_text(json.dumps({"schema_version": "1.0.0",
                                                 "description": "Human-review record of every proposed Day Zero conversion. No corpus source file was rewritten to produce this artifact.",
                                                 "method": "An independent raw census scans matters, curriculum, and jurisdictions. Occurrences are reconciled by deterministic (source, literal, occurrence) counters so duplicates remain distinct; every valid full date is converted, held out, or listed under attention_required. ISO-looking substrings embedded in longer identifiers are listed separately as excluded_non_dates. Each matter uses its existing open_date; offsets are signed calendar-day differences parsed with datetime.strptime. Renderer block spans bind prose dates to durable IDs.",
                                                 "summary": {"converted_dates": result.converted_dates,
                                                             "proof_records": result.proof_records,
                                                             "iso_dates_in_inventory": result.iso_dates,
                                                             "iso_like_raw_occurrences": result.iso_like_raw_occurrences,
                                                             "long_form_dates_in_inventory": result.long_form_dates,
                                                             "full_date_inventory_total": result.iso_dates + result.long_form_dates,
                                                             "full_date_holdouts": result.full_date_holdouts,
                                                             "holdout_candidates": len(result.holdouts),
                                                             "attention_required": len(result.unclassified),
                                                             "reconciled_category_total": result.converted_dates + result.full_date_holdouts + len(result.unclassified)},
                                                 "matter_anchors": result.matter_anchors,
                                                 "excluded_non_dates": result.excluded_non_dates,
                                                 "attention_required": result.unclassified,
                                                 "entries": result.audit}, indent=2) + "\n")
    if args.holdouts_output:
        reason_counts = {}
        for row in result.holdouts:
            reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + 1
        reviewed_holdouts = [dict(row, review_status="candidate_pending_human_review") for row in result.holdouts]
        args.holdouts_output.write_text(json.dumps({"schema_version": "1.0.0",
                                                    "description": "Candidate fixed facts excluded from automatic Day Zero conversion pending human review.",
                                                    "method": "Conservative deterministic classification: bare years, case-citation years, and dates in statutory/effective-date context are held out.",
                                                    "summary": {"count": len(result.holdouts),
                                                                "by_reason": reason_counts},
                                                    "entries": reviewed_holdouts}, indent=2) + "\n")
    print(json.dumps({"mode": "write" if args.write else "dry-run", "converted_dates": result.converted_dates,
                      "proof_records": result.proof_records, "holdouts": len(result.holdouts),
                      "unclassified": len(result.unclassified), "touched_files": len(result.touched_files),
                      "iso_dates": result.iso_dates, "long_form_dates": result.long_form_dates,
                      "iso_like_raw_occurrences": result.iso_like_raw_occurrences,
                      "excluded_non_dates": len(result.excluded_non_dates),
                      "full_date_holdouts": result.full_date_holdouts,
                      "inventory_total": result.iso_dates + result.long_form_dates,
                      "reconciled_total": result.converted_dates + result.full_date_holdouts + len(result.unclassified)}))
    if result.unclassified:
        print(json.dumps({"unclassified_dates": result.unclassified}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
