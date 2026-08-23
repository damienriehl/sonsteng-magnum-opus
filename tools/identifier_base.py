"""Canonical JSON-LD identifier bases and authoritative corpus scope."""
from __future__ import annotations

import json
from pathlib import Path


OLD_JSONLD_BASE_TEXT = "https://sonsteng.damienriehl.com/spine/"
NEW_JSONLD_BASE_TEXT = "https://legalpracticum.org/spine/"
OLD_JSONLD_BASE = OLD_JSONLD_BASE_TEXT.encode("ascii")
NEW_JSONLD_BASE = NEW_JSONLD_BASE_TEXT.encode("ascii")
OLD_JSONLD_ESCAPED_BASE = b"https:\\/\\/sonsteng.damienriehl.com\\/spine\\/"
NEW_JSONLD_ESCAPED_BASE = b"https:\\/\\/legalpracticum.org\\/spine\\/"
IDENTIFIER_TREE_NAMES = (
    "matters", "curriculum", "jurisdictions", "firm", "taxonomy", "schemas",
)


def replace_identifier_base(payload: bytes) -> tuple[bytes, int]:
    """Replace raw and conventionally slash-escaped legacy identifier bases."""
    replacements = payload.count(OLD_JSONLD_BASE)
    replacements += payload.count(OLD_JSONLD_ESCAPED_BASE)
    payload = payload.replace(OLD_JSONLD_BASE, NEW_JSONLD_BASE)
    payload = payload.replace(OLD_JSONLD_ESCAPED_BASE, NEW_JSONLD_ESCAPED_BASE)
    return payload, replacements


def identifier_base_counts(path: Path, payload: bytes) -> tuple[int, int]:
    """Count semantic JSON string bases, or literal bases in non-JSON files."""
    if path.suffix.lower() != ".json":
        return payload.count(OLD_JSONLD_BASE), payload.count(NEW_JSONLD_BASE)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (
            payload.count(OLD_JSONLD_BASE) + payload.count(OLD_JSONLD_ESCAPED_BASE),
            payload.count(NEW_JSONLD_BASE) + payload.count(NEW_JSONLD_ESCAPED_BASE),
        )
    old_count = 0
    new_count = 0
    stack = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            old_count += value.count(OLD_JSONLD_BASE_TEXT)
            new_count += value.count(NEW_JSONLD_BASE_TEXT)
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return old_count, new_count


def authoritative_paths(data_dir: Path) -> list[Path]:
    """Return the deterministic, non-symlink file set governed by U8/U9."""
    data_dir = Path(data_dir)
    paths = []
    manifest = data_dir / "spine-manifest.json"
    if manifest.is_file() and not manifest.is_symlink():
        paths.append(manifest)
    for tree_name in IDENTIFIER_TREE_NAMES:
        tree = data_dir / tree_name
        if not tree.is_dir() or tree.is_symlink():
            continue
        paths.extend(
            path for path in tree.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    return sorted(paths, key=lambda path: path.relative_to(data_dir).as_posix())


def authoritative_scope_gaps(data_dir: Path, paths: list[Path]) -> list[str]:
    """Describe missing or empty inputs that would make a scan incomplete."""
    data_dir = Path(data_dir)
    gaps = []
    manifest = data_dir / "spine-manifest.json"
    if not manifest.is_file() or manifest.is_symlink():
        gaps.append("spine-manifest.json is missing, invalid, or a symlink")
    for tree_name in IDENTIFIER_TREE_NAMES:
        tree = data_dir / tree_name
        if not tree.is_dir() or tree.is_symlink():
            gaps.append(f"{tree_name}/ is missing, invalid, or a symlink")
            continue
        if not any(tree in path.parents for path in paths):
            gaps.append(f"{tree_name}/ contains no authoritative files")
    return gaps
