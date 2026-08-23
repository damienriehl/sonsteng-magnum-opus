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
    """Replace legacy bases, including arbitrarily escaped JSON strings.

    JSON permits any character in the URL to be represented by an escape (for
    example, ``\\u0068`` for ``h``), so byte substitutions alone are not
    complete. For valid JSON, rewrite string tokens according to their decoded
    value while preserving all bytes outside the matched URL. Non-JSON files
    retain the deliberately narrow literal-byte behavior.
    """
    try:
        json.loads(payload)
        text = payload.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError):
        replacements = payload.count(OLD_JSONLD_BASE)
        replacements += payload.count(OLD_JSONLD_ESCAPED_BASE)
        payload = payload.replace(OLD_JSONLD_BASE, NEW_JSONLD_BASE)
        payload = payload.replace(OLD_JSONLD_ESCAPED_BASE, NEW_JSONLD_ESCAPED_BASE)
        return payload, replacements

    rewritten, replacements = _replace_json_string_tokens(text)
    return rewritten.encode("utf-8"), replacements


def _replace_json_string_tokens(text: str) -> tuple[str, int]:
    """Surgically replace semantic bases inside the strings of valid JSON."""
    chunks = []
    cursor = 0
    replacements = 0
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        end = index + 1
        while end < len(text):
            if text[end] == "\\":
                end += 2
            elif text[end] == '"':
                end += 1
                break
            else:
                end += 1
        token, token_replacements = _replace_json_string_token(text[index:end])
        if token_replacements:
            chunks.extend((text[cursor:index], token))
            cursor = end
            replacements += token_replacements
        index = end
    chunks.append(text[cursor:])
    return "".join(chunks), replacements


def _replace_json_string_token(token: str) -> tuple[str, int]:
    """Rewrite one JSON string without normalizing its unrelated escapes."""
    raw = token[1:-1]
    characters = []
    spans = []
    index = 0
    while index < len(raw):
        start = index
        if raw[index] != "\\":
            character = raw[index]
            index += 1
        elif raw[index + 1] != "u":
            index += 2
            character = json.loads('"' + raw[start:index] + '"')
        else:
            index += 6
            codepoint = int(raw[start + 2:index], 16)
            if (0xD800 <= codepoint <= 0xDBFF and raw[index:index + 2] == "\\u"
                    and index + 6 <= len(raw)):
                low = int(raw[index + 2:index + 6], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    index += 6
            character = json.loads('"' + raw[start:index] + '"')
        characters.append(character)
        spans.append((start, index))

    semantic = "".join(characters)
    count = semantic.count(OLD_JSONLD_BASE_TEXT)
    if count == 0:
        return token, 0

    pieces = []
    raw_cursor = 0
    semantic_cursor = 0
    while True:
        match = semantic.find(OLD_JSONLD_BASE_TEXT, semantic_cursor)
        if match < 0:
            break
        raw_start = spans[match][0]
        raw_end = spans[match + len(OLD_JSONLD_BASE_TEXT) - 1][1]
        pieces.extend((raw[raw_cursor:raw_start], NEW_JSONLD_BASE_TEXT))
        raw_cursor = raw_end
        semantic_cursor = match + len(OLD_JSONLD_BASE_TEXT)
    pieces.append(raw[raw_cursor:])
    return '"' + "".join(pieces) + '"', count


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
