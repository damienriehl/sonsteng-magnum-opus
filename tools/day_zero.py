#!/usr/bin/env python3
"""Deterministic Day Zero inventory/converter. Default operation is dry-run."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from collections import Counter

import json_surgical
import build_site
import day_zero_equivalence
import day_zero_locator
from identifier_base import (
    OLD_JSONLD_BASE,
    NEW_JSONLD_BASE,
    authoritative_paths,
    replace_identifier_base,
)

ISO_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
ISO_LIKE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
LONG_RE = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})\b"
)
YEAR_RE = re.compile(r"(?<![\d-])(?:18|19|20)\d{2}(?![\d-])")
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
    staged_date_writes: int = 0
    governed_conversion_target: int | None = None
    identifier_base_replacements: int = 0
    identifier_files: set[str] = field(default_factory=set)
    _file_proof_indexes: dict[str, int] = field(default_factory=dict, repr=False)

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


def _is_full_date(value: str) -> bool:
    try:
        parse_date(value)
        return True
    except ValueError:
        return False


def classify_candidate(text: str) -> Classification:
    if CITATION_RE.search(text):
        return Classification("holdout", "case-citation year is a fixed fact")
    if STATUTORY_RE.search(text) and (ISO_RE.search(text) or LONG_RE.search(text) or YEAR_RE.search(text)):
        return Classification("holdout", "statutory/effective-date context is a fixed fact")
    if YEAR_RE.fullmatch(text.strip()):
        return Classification("holdout", "bare year is a fixed-fact candidate")
    return Classification("convert", "dated matter fact relative to matter open_date")


def _local_context(text: str, start: int, end: int) -> str:
    """Return the candidate's bounded clause/sentence, not its whole block."""
    boundaries = ("\n", ".", ";", "!", "?", "—")
    left = max(text.rfind(mark, 0, start) for mark in boundaries) + 1
    ends = [pos for mark in boundaries
            if (pos := text.find(mark, end)) >= 0]
    right = min(ends) + 1 if ends else len(text)
    context = text[max(left, start - 240):min(right, end + 240)]
    # A generic status phrase in a preceding comma-clause ("effective today,")
    # must not turn a later event date into a statutory holdout.
    relative_start = start - max(left, start - 240)
    comma = context.rfind(",", 0, relative_start)
    if comma >= 0 and re.search(r"\beffective\s+today\b", context[:comma], re.I):
        context = context[comma + 1:]
    return context


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


def _record(result: Result, source: str, locator: str, literal: str, anchor: date,
            reason: str, storage_kind: str, storage_path: str,
            storage_locator: str | int):
    try:
        offset = offset_days(literal, anchor)
    except ValueError:
        result.unclassified.append({"source": source, "locator": locator, "literal": literal,
                                    "reason": "invalid calendar date requires human attention"})
        return
    result.date_proofs.append(day_zero_equivalence.DateProof(
        source, locator, literal, anchor.isoformat(), offset,
        storage_kind, storage_path, storage_locator
    ))
    result.audit.append({"source": source, "locator": locator, "literal": literal,
                         "anchor": anchor.isoformat(), "day_zero_offset": offset,
                         "anchor_reason": reason})


def _capture_file_proof(result: Result, source: str, before: bytes, after: bytes):
    before = result.before_files.get(source, before)
    result.before_files[source] = before
    result.after_files[source] = after
    prefix = 0
    common_limit = min(len(before), len(after))
    while prefix < common_limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < common_limit - prefix and
           before[len(before) - suffix - 1] == after[len(after) - suffix - 1]):
        suffix += 1
    after_end = len(after) - suffix
    before_end = len(before) - suffix
    reverse_edits = [] if before == after else [day_zero_equivalence.ReverseEdit(
        prefix, after_end, before[prefix:before_end]
    )]
    proof = day_zero_equivalence.FileProof(source, tuple(reverse_edits))
    index = result._file_proof_indexes.get(source)
    if index is None:
        result._file_proof_indexes[source] = len(result.file_proofs)
        result.file_proofs.append(proof)
    else:
        result.file_proofs[index] = proof


def _stage_identifier_base_rewrite(repo: Path, result: Result,
                                   staged_writes: dict[Path, bytes]) -> None:
    """Compose the exact identifier substitution into the atomic U8 write set."""
    for path in authoritative_paths(repo / "data"):
        source = str(path.relative_to(repo))
        before = result.before_files.get(source)
        if before is None:
            before = path.read_bytes()
        current = staged_writes.get(path, before)
        after, replacements = replace_identifier_base(current)
        if replacements == 0:
            continue
        result.identifier_base_replacements += replacements
        result.identifier_files.add(source)
        result.touched_files.add(source)
        _capture_file_proof(result, source, before, after)
        staged_writes[path] = after


def _validate_intended_json_additions(before: str, after: str, insertions: list[tuple]):
    """Reject any semantic mutation except the independently declared siblings."""
    expected = json.loads(before)
    for parent, key, value in insertions:
        target = expected
        if parent:
            for part in parent.split("."):
                target = target[int(part)] if isinstance(target, list) else target[part]
        if key in target and target[key] != value:
            raise RuntimeError("conflicting existing Day Zero sibling %s.%s" % (parent, key))
        target[key] = value
    if json.loads(after) != expected:
        raise RuntimeError("surgical edit introduced collateral JSON mutation")


def _dated_matches(text: str):
    clean = re.sub(r"\{#b:[0-9a-f]{8}\}", "", text)
    return sorted(list(ISO_RE.finditer(clean)) + list(LONG_RE.finditer(clean)), key=lambda match: match.start())


def _append_prose_candidates(result, sidecar_entries, storage_path, text, source, block_id, anchor, reason,
                             locator_prefix="", approved_conversions=None):
    approved_conversions = approved_conversions or {}
    occupied = []
    for occurrence, match in enumerate(_dated_matches(text)):
        occupied.append((match.start(), match.end()))
        literal = match.group(1)
        locator = "%s:%d" % (block_id, occurrence) if block_id else "%sdate:%d" % (
            locator_prefix + ":" if locator_prefix else "", occurrence)
        citation_window = text[max(0, match.start() - 180):min(len(text), match.end() + 40)]
        approved = approved_conversions.get((source, locator, literal))
        if approved:
            classification = Classification("convert", "human-approved matter-relative date")
        elif CITATION_RE.search(citation_window):
            classification = Classification("holdout", "case-citation year is a fixed fact")
        else:
            classification = classify_candidate(_local_context(text, match.start(), match.end()))
        if classification.kind == "holdout":
            result.holdouts.append({"source": source, "locator": locator, "literal": literal,
                                    "reason": classification.reason})
        elif not block_id:
            result.unclassified.append({"source": source, "locator": locator, "literal": literal,
                                        "reason": "prose date has no durable block ID"})
        else:
            try:
                parsed_offset = offset_days(literal, anchor)
            except ValueError:
                result.unclassified.append({"source": source, "locator": locator,
                                            "literal": literal,
                                            "reason": "invalid calendar date requires human attention"})
                continue
            storage_index = len(sidecar_entries)
            sidecar_entries.append({"source": source, "block_id": block_id,
                                    "locator": occurrence, "literal": literal,
                                    "day_zero_offset": parsed_offset})
            result.staged_date_writes += 1
            _record(result, source, locator, literal, anchor, reason,
                    "prose_sidecar", storage_path, storage_index)
    marker_stripped = re.sub(r"\{#b:[0-9a-f]{8}\}", "", text)
    for match in YEAR_RE.finditer(marker_stripped):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        literal = match.group(0)
        locator = "%s:year:%d" % (block_id, match.start()) if block_id else "year:%d" % match.start()
        citation_window = text[max(0, match.start() - 180):min(len(text), match.end() + 40)]
        classification = (Classification("holdout", "case-citation year is a fixed fact")
                          if CITATION_RE.search(citation_window)
                          else classify_candidate(_local_context(text, match.start(), match.end())))
        result.holdouts.append({"source": source, "locator": locator, "literal": literal,
                                "reason": classification.reason if classification.kind == "holdout"
                                else "bare year is a fixed-fact candidate"})


def _resolve_approved_storage(repo: Path, row: dict) -> int:
    """Resolve one governed durable locator to its date ordinal."""
    try:
        return day_zero_locator.resolve_durable_locator(
            repo / row["source"], row["durable_locator"], row["literal"]
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"approved Day Zero identity no longer resolves: {row['key']}"
        ) from exc


def _approved_materialization(repo: Path) -> tuple[list[dict], list[dict], int | None]:
    """Load and verify the approved proposal plus its governed resolution."""
    import apply_day_zero_review

    approval = repo / apply_day_zero_review.APPROVAL_REL
    if not approval.is_file():
        return [], [], None
    proposal_path = repo / apply_day_zero_review.PROPOSAL_REL
    apply_day_zero_review._approval_digest(repo, proposal_path)
    proposal = json.loads(proposal_path.read_text())
    holdouts = json.loads((repo / apply_day_zero_review.HOLDOUTS_REL).read_text())
    audit = json.loads((repo / apply_day_zero_review.AUDIT_REL).read_text())
    try:
        apply_day_zero_review.validate_applied_review(repo, proposal, holdouts, audit)
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError(f"approved Day Zero identity no longer resolves: {exc}") from exc
    governed = {
        (row["source"], row.get("source_locator", row["locator"]), row["literal"]): row
        for row in audit.get("entries", [])
        if row.get("review_status") == "human_confirmed_convertible"
    }
    rows = []
    declared_holdouts = []
    for row in proposal.get("proposals", []):
        disposition = row.get("proposed_disposition")
        if disposition in {"declared_holdout", "declared_out_of_anchor_holdout"}:
            declared_holdouts.append(row)
            continue
        if disposition not in {"convertible", "convertible_after_durable_locator_added"}:
            continue
        identity = (row["source"], row["locator"], row["literal"])
        approved = governed.get(identity)
        if not approved:
            raise RuntimeError(f"approved conversion lacks governed identity: {row['key']}")
        rows.append(dict(row, durable_locator=approved["locator"]))
    return rows, declared_holdouts, audit["summary"]["converted_dates"]


def _materialize_approved_rows(repo, rows, sidecar_entries, sidecar_source,
                               result, anchor, reason):
    existing = {(proof.path, proof.locator, proof.literal) for proof in result.date_proofs}
    for row in rows:
        identity = (row["source"], row["locator"], row["literal"])
        if identity in existing:
            continue
        if row["durable_locator"].startswith("b:"):
            raise RuntimeError(f"approved Day Zero identity no longer resolves: {row['key']}")
        occurrence = _resolve_approved_storage(repo, row)
        storage_index = len(sidecar_entries)
        sidecar_entries.append({
            "source": row["source"],
            "locator": occurrence, "literal": row["literal"],
            "day_zero_offset": row["proposed_day_zero_offset"],
            "durable_locator": row["durable_locator"],
        })
        result.staged_date_writes += 1
        _record(result, row["source"], row["durable_locator"], row["literal"],
                anchor, reason, "prose_sidecar", sidecar_source, storage_index)
        existing.add(identity)
    approved_identities = {(row["source"], row["locator"], row["literal"]) for row in rows}
    result.unclassified[:] = [
        item for item in result.unclassified
        if (item["source"], item["locator"], item["literal"]) not in approved_identities
    ]


def _markdown_blocks(text: str):
    spans = []
    build_site.markdown(text, spans=spans)
    return spans


ORDINARY_FILE_MODE = 0o644


def _stage_payload(path: Path, payload: bytes, mode: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                        dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            os.fchmod(handle.fileno(), mode)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return staged
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        staged.unlink(missing_ok=True)
        raise


def _write_staged_files(writes: dict[Path, bytes]) -> None:
    """Replace the proved write set, rolling every path back on any failure."""
    originals = {}
    for path in writes:
        try:
            original_stat = path.stat()
            originals[path] = (path.read_bytes(), stat.S_IMODE(original_stat.st_mode))
        except FileNotFoundError:
            originals[path] = None
    staged = {}
    try:
        for path, payload in writes.items():
            original = originals[path]
            mode = original[1] if original is not None else ORDINARY_FILE_MODE
            staged[path] = _stage_payload(path, payload, mode)
    except Exception:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        raise
    replaced = []
    try:
        for path in sorted(staged, key=lambda item: str(item)):
            os.replace(staged[path], path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                original_payload, original_mode = original
                rollback = _stage_payload(path, original_payload, original_mode)
                os.replace(rollback, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def convert_corpus(repo: Path, write: bool = False) -> Result:
    repo = Path(repo)
    result = Result()
    approved_rows, declared_rows, governed_target = _approved_materialization(repo)
    result.governed_conversion_target = governed_target
    approved_by_identity = {
        (row["source"], row["locator"], row["literal"]): row
        for row in approved_rows
    }
    staged_writes: dict[Path, bytes] = {}
    matters = repo / "data" / "matters"
    for matter_json in sorted(matters.glob("*/matter.json")):
        matter_dir = matter_json.parent
        matter = json.loads(matter_json.read_text())
        anchor = parse_date(matter["open_date"])
        reason = "matter %s existing open_date" % matter.get("id", matter_dir.name)
        result.matter_anchors.append({"matter_id": matter.get("id"), "matter_slug": matter_dir.name,
                                      "anchor": anchor.isoformat(), "reason": reason})
        sidecar_entries = []
        sidecar_target = matter_dir / "date-offsets.json"
        sidecar_source = str(sidecar_target.relative_to(repo))
        for path in sorted(matter_dir.rglob("*.json")):
            if path.name == "date-offsets.json":
                continue
            raw = path.read_text()
            obj = json.loads(raw)
            insertions = []
            for parent, key, value in _scalar_strings(obj):
                if key.endswith("_day_zero_offset"):
                    if _is_full_date(value):
                        locator = "%s.%s" % (parent, key) if parent else key
                        result.unclassified.append({
                            "source": str(path.relative_to(repo)),
                            "locator": locator,
                            "literal": value,
                            "reason": (
                                "date-valued authored fact uses the reserved "
                                "Day Zero offset suffix and must be renamed"
                            ),
                        })
                    continue
                matches = _dated_matches(value)
                if not matches:
                    continue
                # Additive siblings are unambiguous only for a scalar that is exactly a date.
                if len(matches) == 1 and matches[0].group(1) == value:
                    literal = value
                    locator = "%s.%s" % (parent, key) if parent else key
                    classification = classify_candidate(value)
                    if classification.kind == "holdout":
                        result.holdouts.append({"source": str(path.relative_to(repo)), "locator": locator,
                                                "literal": literal, "reason": classification.reason})
                        continue
                    try:
                        parsed_offset = offset_days(literal, anchor)
                    except ValueError:
                        result.unclassified.append({"source": str(path.relative_to(repo)),
                                                    "locator": locator, "literal": literal,
                                                    "reason": "invalid calendar date requires human attention"})
                        continue
                    source = str(path.relative_to(repo))
                    sibling_locator = "%s.%s" % (parent, key + "_day_zero_offset") \
                        if parent else key + "_day_zero_offset"
                    _record(result, source, locator, literal, anchor, reason,
                            "json_sibling", source, sibling_locator)
                    insertions.append((parent, key + "_day_zero_offset", parsed_offset))
                    result.staged_date_writes += 1
                else:
                    source = str(path.relative_to(repo))
                    spans = _markdown_blocks(value)
                    if any(span["bid"] for span in spans):
                        for span in spans:
                            _append_prose_candidates(result, sidecar_entries, sidecar_source,
                                                     span["raw"], source,
                                                     "b:" + span["bid"] if span["bid"] else None,
                                                     anchor, reason,
                                                     approved_conversions=approved_by_identity)
                    else:
                        json_locator = "%s.%s" % (parent, key) if parent else key
                        _append_prose_candidates(result, sidecar_entries, sidecar_source,
                                                 value, source, None, anchor,
                                                 reason, locator_prefix=json_locator,
                                                 approved_conversions=approved_by_identity)
            if insertions:
                source = str(path.relative_to(repo))
                result.touched_files.add(source)
                try:
                    converted_raw = json_surgical.insert_object_properties(
                        raw, insertions)
                except json_surgical.SurgicalError as exc:
                    raise RuntimeError(
                        f"{source} cannot add Day Zero sibling: {exc}"
                    ) from exc
                _validate_intended_json_additions(raw, converted_raw, insertions)
                _capture_file_proof(result, source, raw.encode(), converted_raw.encode())
                staged_writes[path] = converted_raw.encode()
        for md in sorted(matter_dir.rglob("*.md")):
            raw = md.read_text()
            source = str(md.relative_to(repo))
            for span in _markdown_blocks(raw):
                _append_prose_candidates(result, sidecar_entries, sidecar_source,
                                         span["raw"], source,
                                         "b:" + span["bid"] if span["bid"] else None,
                                         anchor, reason,
                                         approved_conversions=approved_by_identity)
            if sidecar_entries:
                result.touched_files.add(str((matter_dir / "date-offsets.json").relative_to(repo)))
        matter_approved = [row for row in approved_rows
                           if row["source"].startswith(
                               str(matter_dir.relative_to(repo)).rstrip("/") + "/")]
        _materialize_approved_rows(repo, matter_approved, sidecar_entries,
                                   sidecar_source, result, anchor, reason)
        if sidecar_entries:
            result.touched_files.add(sidecar_source)
            sidecar = {"schema_version": "1.0.0", "matter_id": matter.get("id"),
                       "anchor": anchor.isoformat(), "entries": sidecar_entries}
            text = json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n"
            target = sidecar_target
            source = str(target.relative_to(repo))
            before = target.read_bytes() if target.exists() else b""
            after = text.encode()
            _capture_file_proof(result, source, before, after)
            staged_writes[target] = after
    _reconcile_full_date_inventory(repo, result)
    if declared_rows:
        declared = {(row["source"], row["locator"], row["literal"]): row
                    for row in declared_rows}
        remaining = []
        for item in result.unclassified:
            row = declared.get((item["source"], item["locator"], item["literal"]))
            if row:
                result.holdouts.append({
                    "source": item["source"], "locator": item["locator"],
                    "literal": item["literal"], "reason": row["reason_code"],
                })
            else:
                remaining.append(item)
        result.unclassified = remaining
        result.full_date_holdouts = sum(
            1 for row in result.holdouts
            if _is_full_date(row.get("literal", ""))
        )
    _stage_identifier_base_rewrite(repo, result, staged_writes)
    day_zero_equivalence.file_round_trip(
        sorted(result.touched_files), result.before_files, result.after_files,
        result.file_proofs, result.date_proofs,
        converted_date_count=result.converted_dates,
    )
    _assert_governed_materialization(result, governed_target, governed_target)
    if write:
        _write_staged_files(staged_writes)
    return result


def _raw_full_date_inventory(repo: Path):
    rows = []
    excluded = []
    for tree_name in ("matters", "curriculum", "jurisdictions"):
        tree = repo / "data" / tree_name
        if not tree.exists():
            continue
        for path in sorted(p for p in tree.rglob("*") if p.is_file()):
            if path.name == "date-offsets.json":
                continue
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
        if not _is_full_date(row["literal"]):
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


def governed_reports(repo: Path, result: Result):
    """Build deterministic review reports, applying a recorded approval when present."""
    audit = {
        "schema_version": "1.0.0",
        "description": "Human-review record of every proposed Day Zero conversion. No corpus source file was rewritten to produce this artifact.",
        "method": "An independent raw census scans matters, curriculum, and jurisdictions. Occurrences are reconciled by deterministic (source, literal, occurrence) counters so duplicates remain distinct; every valid full date is converted, held out, or listed under attention_required. ISO-looking substrings embedded in longer identifiers are listed separately as excluded_non_dates. Each matter uses its existing open_date; offsets are signed calendar-day differences parsed with datetime.strptime. Renderer block spans bind prose dates to durable IDs.",
        "summary": {
            "converted_dates": result.converted_dates,
            "proof_records": result.proof_records,
            "iso_dates_in_inventory": result.iso_dates,
            "iso_like_raw_occurrences": result.iso_like_raw_occurrences,
            "long_form_dates_in_inventory": result.long_form_dates,
            "full_date_inventory_total": result.iso_dates + result.long_form_dates,
            "full_date_holdouts": result.full_date_holdouts,
            "holdout_candidates": len(result.holdouts),
            "attention_required": len(result.unclassified),
            "reconciled_category_total": (
                result.converted_dates + result.full_date_holdouts + len(result.unclassified)
            ),
        },
        "matter_anchors": result.matter_anchors,
        "excluded_non_dates": result.excluded_non_dates,
        "attention_required": result.unclassified,
        "entries": result.audit,
    }
    reason_counts = Counter(row["reason"] for row in result.holdouts)
    holdouts = {
        "schema_version": "1.0.0",
        "description": "Candidate fixed facts excluded from automatic Day Zero conversion pending human review.",
        "method": "Conservative deterministic classification: bare years, case-citation years, and dates in statutory/effective-date context are held out.",
        "summary": {
            "count": len(result.holdouts),
            "by_reason": dict(reason_counts),
        },
        "entries": [
            dict(row, review_status="candidate_pending_human_review")
            for row in result.holdouts
        ],
    }
    approval = repo / "docs" / "decisions" / "2026-08-17-day-zero-review-approval.md"
    if approval.is_file():
        import apply_day_zero_review

        proposal_path = repo / apply_day_zero_review.PROPOSAL_REL
        apply_day_zero_review._approval_digest(repo, proposal_path)
        proposal = json.loads(proposal_path.read_text())
        holdouts = json.loads((repo / apply_day_zero_review.HOLDOUTS_REL).read_text())
        audit = json.loads((repo / apply_day_zero_review.AUDIT_REL).read_text())
        apply_day_zero_review.validate_applied_review(repo, proposal, holdouts, audit)
    return holdouts, audit


def _approved_conversion_target(repo: Path) -> int | None:
    _, _, target = _approved_materialization(Path(repo))
    return target


def _assert_governed_materialization(result: Result, governed: int | None,
                                     target: int | None) -> None:
    if target is None:
        return
    staged = result.staged_date_writes
    proofs = result.proof_records
    unclassified = len(result.unclassified)
    if not (staged == proofs == governed == target and unclassified == 0):
        raise RuntimeError(
            "approved Day Zero materialization mismatch: "
            f"staged={staged}, proofs={proofs}, governed={governed}, "
            f"target={target}, unclassified={unclassified}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--write", action="store_true",
        help="write corpus offsets and the legalpracticum.org identifier base (U8 only)",
    )
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--holdouts-output", type=Path)
    args = parser.parse_args(argv)
    result = convert_corpus(args.repo, write=args.write)
    # Keep the command boundary explicitly gated even though convert_corpus
    # also proves its result before returning. This prevents a future caller
    # refactor from turning the CLI into a green-without-proof path.
    day_zero_equivalence.file_round_trip(
        sorted(result.touched_files), result.before_files, result.after_files,
        result.file_proofs, result.date_proofs,
        converted_date_count=result.converted_dates,
    )
    holdout_report, audit_report = governed_reports(args.repo, result)
    _assert_governed_materialization(
        result, audit_report.get("summary", {}).get("converted_dates"),
        _approved_conversion_target(args.repo)
    )
    if args.audit_output:
        args.audit_output.write_text(json.dumps(audit_report, indent=2, ensure_ascii=False) + "\n")
    if args.holdouts_output:
        args.holdouts_output.write_text(json.dumps(holdout_report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"mode": "write" if args.write else "dry-run",
                      "converted_dates": audit_report["summary"]["converted_dates"],
                      "proof_records": audit_report["summary"]["proof_records"],
                      "holdouts": holdout_report["summary"]["count"],
                      "unclassified": audit_report["summary"]["attention_required"],
                      "touched_files": len(result.touched_files),
                      "iso_dates": result.iso_dates, "long_form_dates": result.long_form_dates,
                      "iso_like_raw_occurrences": result.iso_like_raw_occurrences,
                      "excluded_non_dates": len(result.excluded_non_dates),
                      "full_date_holdouts": audit_report["summary"]["full_date_holdouts"],
                      "identifier_base_replacements": result.identifier_base_replacements,
                      "identifier_files": len(result.identifier_files),
                      "inventory_total": result.iso_dates + result.long_form_dates,
                      "reconciled_total": result.converted_dates + result.full_date_holdouts + len(result.unclassified)}))
    if audit_report["attention_required"]:
        print(json.dumps({"unclassified_dates": audit_report["attention_required"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
