#!/usr/bin/env python3
"""Independent proof harness for a Day Zero corpus conversion.

The converter supplies byte-level reverse edits and date proof records.  This
module deliberately knows nothing about the converter's JSON property names or
prose-sidecar schema: it proves the complete declared touched set can be
reconstructed byte-for-byte and that every recorded offset resolves to the
literal it claims to represent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

import stamp_block_ids


class EquivalenceError(AssertionError):
    """The conversion could not be mechanically proved equivalent."""


@dataclass(frozen=True)
class ReverseEdit:
    """Replace ``after[start:end]`` with bytes from the pre-conversion file."""

    start: int
    end: int
    replacement: bytes


@dataclass(frozen=True)
class FileProof:
    """All reverse edits needed to reconstruct one touched file."""

    path: str
    reverse_edits: tuple[ReverseEdit, ...] = ()


@dataclass(frozen=True)
class DateProof:
    """One converted date and the anchor arithmetic used for it."""

    path: str
    locator: str
    literal: str
    anchor: str
    day_zero_offset: int


@dataclass(frozen=True)
class RoundTripResult:
    files_checked: int
    converted_date_count: int
    proof_covered_date_count: int


def _parse_date(value: str) -> date:
    """Parse supported ISO or long-form English dates using date parsers."""
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("unsupported date literal %r" % value)


def _restore(after: bytes, edits: Sequence[ReverseEdit], path: str) -> bytes:
    prior_start = len(after) + 1
    restored = after
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        if edit.start < 0 or edit.end < edit.start or edit.end > len(after):
            raise EquivalenceError("%s: invalid reverse-edit span %d:%d" %
                                   (path, edit.start, edit.end))
        if edit.end > prior_start:
            raise EquivalenceError("%s: overlapping reverse edits" % path)
        restored = restored[:edit.start] + edit.replacement + restored[edit.end:]
        prior_start = edit.start
    return restored


def file_round_trip(
    before_files: Mapping[str, bytes],
    after_files: Mapping[str, bytes],
    file_proofs: Sequence[FileProof],
    date_proofs: Sequence[DateProof],
    *,
    converted_date_count: int | None = None,
) -> RoundTripResult:
    """Prove offsets and bytes for every file in the declared touched set.

    ``before_files`` defines that complete set. A newly-created output is
    represented explicitly as ``path: b""`` and its reverse edit must delete
    the complete generated content. There must be exactly one
    file proof and an after snapshot for each path; silently proving only a
    converter/editor subset is therefore impossible.
    """
    proofs_by_path = {}
    for item in file_proofs:
        if item.path in proofs_by_path:
            raise EquivalenceError("%s: duplicate file proof" % item.path)
        proofs_by_path[item.path] = item

    expected = set(before_files)
    missing_after = expected - set(after_files)
    extra_after = set(after_files) - expected
    if missing_after or extra_after:
        raise EquivalenceError("touched-file snapshots differ: missing=%s extra=%s" %
                               (sorted(missing_after), sorted(extra_after)))
    missing_proofs = expected - set(proofs_by_path)
    extra_proofs = set(proofs_by_path) - expected
    if missing_proofs:
        path = sorted(missing_proofs)[0]
        raise EquivalenceError("%s: no file proof for touched file" % path)
    if extra_proofs:
        raise EquivalenceError("file proof outside touched set: %s" %
                               sorted(extra_proofs)[0])

    errors = []
    for path in sorted(expected):
        restored = _restore(after_files[path], proofs_by_path[path].reverse_edits, path)
        if restored != before_files[path]:
            errors.append("%s: byte mismatch after reversing conversion" % path)

    for item in date_proofs:
        try:
            literal = _parse_date(item.literal)
            anchor = _parse_date(item.anchor)
        except ValueError as exc:
            errors.append("%s %s: %s" % (item.path, item.literal, exc))
            continue
        if anchor + timedelta(days=item.day_zero_offset) != literal:
            errors.append(
                "%s %s: offset %d from %s resolves to %s" %
                (item.path, item.literal, item.day_zero_offset, item.anchor,
                 anchor + timedelta(days=item.day_zero_offset))
            )

    covered = len(date_proofs)
    converted = covered if converted_date_count is None else converted_date_count
    if converted != covered:
        errors.append("converted-date count %d != proof-covered-date count %d" %
                      (converted, covered))
    if errors:
        raise EquivalenceError("; ".join(errors))
    return RoundTripResult(len(expected), converted, covered)


def block_identity_check(before: dict, after: dict) -> list[str]:
    """Reuse the established equivalence proof, then guard durable IDs too.

    ``stamp_block_ids.equivalence_check`` intentionally ignores locator changes
    because it predates durable IDs and supported ordinal-to-ID stamping.  Day
    Zero conversions are later migrations, so an already-durable locator must
    additionally remain byte-identical.
    """
    errors = list(stamp_block_ids.equivalence_check(before, after))
    before_pages = before.get("pages") or {}
    after_pages = after.get("pages") or {}
    for page, old_blocks in before_pages.items():
        for old, new in zip(old_blocks, after_pages.get(page) or []):
            old_ref = old.get("source_ref") or ""
            new_ref = new.get("source_ref") or ""
            old_locator = old_ref.split("#", 1)[1] if "#" in old_ref else ""
            new_locator = new_ref.split("#", 1)[1] if "#" in new_ref else ""
            old_bid = old_locator.rsplit(".", 1)[-1]
            new_bid = new_locator.rsplit(".", 1)[-1]
            if old_bid.startswith("b") and old_bid != new_bid:
                errors.append("%s[%s]: block ID changed %s -> %s" %
                              (page, old.get("index"), old_bid, new_bid))
    return errors
