#!/usr/bin/env python3
"""Shared durable-locator rules for governed Day Zero date evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


DATE_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)|"
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}\b"
)
LINE_LOCATOR_RE = re.compile(r"^line:(\d+):raw-occurrence:(\d+)$")
RAW_DURABLE_LOCATOR_RE = re.compile(
    r"^raw:([0-9a-f]{16}):occurrence:([1-9][0-9]*):date:([0-9]+)$"
)
JSON_DURABLE_LOCATOR_RE = re.compile(r"^json:(.+):date:([0-9]+)$")


def _normalized_line(line: str) -> str:
    return " ".join(DATE_RE.sub("<date>", line).split())


def resolve_raw_occurrence(repo: Path, row: dict) -> tuple[str, int, int]:
    """Resolve a proposal raw locator to one exact literal occurrence."""
    match = LINE_LOCATOR_RE.fullmatch(row["locator"])
    if not match:
        raise ValueError(f"unsupported raw locator: {row['key']}")
    requested = int(match.group(2))
    candidates = []
    for line in (Path(repo) / row["source"]).read_text().splitlines():
        for date_ordinal, dated in enumerate(DATE_RE.finditer(line)):
            if dated.group(0) == row["literal"]:
                candidates.append((line, date_ordinal))
    if requested < 1 or requested > len(candidates):
        raise ValueError(f"raw locator no longer resolves: {row['key']}")
    line, date_ordinal = candidates[requested - 1]
    return line, date_ordinal, requested


def durable_locator(repo: Path, row: dict) -> str:
    """Convert a reviewed proposal locator into its content-bound identity."""
    locator = row["locator"]
    if LINE_LOCATOR_RE.fullmatch(locator):
        line, date_ordinal, occurrence = resolve_raw_occurrence(repo, row)
        fingerprint = hashlib.sha256(_normalized_line(line).encode()).hexdigest()[:16]
        return f"raw:{fingerprint}:occurrence:{occurrence}:date:{date_ordinal}"
    if ":date:" in locator:
        return "json:" + locator
    return locator


def _json_value_at(value, dotted: str):
    for part in dotted.split(".") if dotted else []:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def resolve_durable_locator(source_path: Path, locator: str, literal: str) -> int:
    """Validate a durable locator against current source and return its date ordinal."""
    source_path = Path(source_path)
    json_match = JSON_DURABLE_LOCATOR_RE.fullmatch(locator)
    if json_match:
        value = _json_value_at(json.loads(source_path.read_text()), json_match.group(1))
        dates = list(DATE_RE.finditer(value)) if isinstance(value, str) else []
        ordinal = int(json_match.group(2))
        if ordinal >= len(dates) or dates[ordinal].group(0) != literal:
            raise ValueError("durable JSON locator no longer resolves")
        return ordinal

    raw_match = RAW_DURABLE_LOCATOR_RE.fullmatch(locator)
    if not raw_match:
        raise ValueError("unsupported durable locator")
    fingerprint, requested, date_ordinal = (
        raw_match.group(1), int(raw_match.group(2)), int(raw_match.group(3))
    )
    candidates = []
    for line in source_path.read_text().splitlines():
        dates = list(DATE_RE.finditer(line))
        for dated in dates:
            if dated.group(0) == literal:
                candidates.append((line, dates))
    if requested < 1 or requested > len(candidates):
        raise ValueError("durable raw locator no longer resolves")
    line, dates = candidates[requested - 1]
    observed = hashlib.sha256(_normalized_line(line).encode()).hexdigest()[:16]
    if (observed != fingerprint or date_ordinal >= len(dates)
            or dates[date_ordinal].group(0) != literal):
        raise ValueError("durable raw locator no longer resolves")
    return date_ordinal
