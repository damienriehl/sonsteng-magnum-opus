#!/usr/bin/env python3
r"""Formatting-preserving surgical JSON scalar writes (WP5, value-sync fast-follow).

WHY THIS EXISTS
---------------
v1 value-sync wrote JSON by `json.loads` -> mutate -> `json.dumps(indent=2)`.
That is *structurally* correct but *not formatting-preserving*: on the real
`data/` corpus, 143 of 188 hand-authored JSON files are reformatted even on a
NO-OP reserialize, because the authors hand-pack arrays/objects onto single
lines (`"party_names": ["X"]`, one-line `{...}` records) and `json.dumps` always
expands them. A single scalar edit therefore produced a diff touching dozens of
unrelated lines — noisy review, noisy git blame, and a large surface for a
value-sync patch to accidentally alter untouched content.

THE APPROACH (surgical in-place text replacement)
-------------------------------------------------
We parse the raw source ONCE with a position-tracking parser that records the
exact `(start, end)` byte-span of every value node, navigate to the target path
(same dotted semantics as apply_suggestions.json_get/json_set), and splice ONLY
that scalar/string span. Everything else in the file stays byte-identical, so a
one-scalar edit yields a one-line (one-value) diff, and rewriting a value to
itself is byte-identical.

Robustness is enforced by a hard SAFETY GATE: after splicing we re-parse the
result and assert it deep-equals `parse -> set-at-path -> (unchanged rest)`. If
the span can't be located, spans overlap, or the re-parse doesn't match the
intended object EXACTLY, we raise SurgicalError and the caller falls back to the
v1 exact-literal parse->set->serialize path. Surgery never silently corrupts:
either it produces the provably-correct minimal splice, or it declines.

Handles: nested objects/arrays, strings needing escaping, numbers/bools/null,
and unicode (raw or `\uXXXX`). Value decoding reuses stdlib `json.loads` on the
located token so escape/number semantics are always exactly Python's.
"""
from __future__ import annotations

import json


class SurgicalError(Exception):
    """Surgical splice cannot be applied safely; caller should fall back."""


# --------------------------------------------------------------------------- #
# Position-tracking parser
# --------------------------------------------------------------------------- #
_WS = " \t\n\r"
_NUM_CHARS = "-+.eE0123456789"


class _Node:
    """A parsed JSON value plus its raw-text span [start, end)."""
    __slots__ = ("kind", "value", "start", "end", "items", "pairs")

    def __init__(self, kind, start):
        self.kind = kind        # object | array | string | number | literal
        self.value = None       # decoded python value
        self.start = start      # offset of first token char
        self.end = start        # offset just past last token char
        self.items = None       # list[_Node] for arrays
        self.pairs = None       # list[(key, _Node)] for objects (source order)


def _skip_ws(s, i):
    n = len(s)
    while i < n and s[i] in _WS:
        i += 1
    return i


def _parse_value(s, i):
    i = _skip_ws(s, i)
    if i >= len(s):
        raise SurgicalError("unexpected end of input")
    c = s[i]
    if c == "{":
        return _parse_object(s, i)
    if c == "[":
        return _parse_array(s, i)
    if c == '"':
        return _parse_string(s, i)
    if c in "tfn":
        return _parse_literal(s, i)
    if c in "-0123456789":
        return _parse_number(s, i)
    raise SurgicalError("unexpected character %r at %d" % (c, i))


def _parse_string(s, i):
    start = i
    j = i + 1
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == '"':
            j += 1
            break
        j += 1
    else:
        raise SurgicalError("unterminated string at %d" % start)
    node = _Node("string", start)
    node.end = j
    node.value = json.loads(s[start:j])
    return node


def _parse_number(s, i):
    start = i
    n = len(s)
    j = i
    while j < n and s[j] in _NUM_CHARS:
        j += 1
    node = _Node("number", start)
    node.end = j
    node.value = json.loads(s[start:j])   # int/float exactly as stdlib decodes
    return node


def _parse_literal(s, i):
    for lit, val in (("true", True), ("false", False), ("null", None)):
        if s.startswith(lit, i):
            node = _Node("literal", i)
            node.end = i + len(lit)
            node.value = val
            return node
    raise SurgicalError("invalid literal at %d" % i)


def _parse_array(s, i):
    node = _Node("array", i)
    node.items = []
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == "]":
        node.end = i + 1
        node.value = []
        return node
    while True:
        child = _parse_value(s, i)
        node.items.append(child)
        i = _skip_ws(s, child.end)
        if i >= len(s):
            raise SurgicalError("unterminated array")
        if s[i] == ",":
            i = _skip_ws(s, i + 1)
            continue
        if s[i] == "]":
            node.end = i + 1
            break
        raise SurgicalError("expected ',' or ']' at %d" % i)
    node.value = [c.value for c in node.items]
    return node


def _parse_object(s, i):
    node = _Node("object", i)
    node.pairs = []
    i = _skip_ws(s, i + 1)
    if i < len(s) and s[i] == "}":
        node.end = i + 1
        node.value = {}
        return node
    while True:
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != '"':
            raise SurgicalError("expected object key at %d" % i)
        keynode = _parse_string(s, i)
        key = keynode.value
        i = _skip_ws(s, keynode.end)
        if i >= len(s) or s[i] != ":":
            raise SurgicalError("expected ':' at %d" % i)
        child = _parse_value(s, i + 1)
        node.pairs.append((key, child))
        i = _skip_ws(s, child.end)
        if i >= len(s):
            raise SurgicalError("unterminated object")
        if s[i] == ",":
            i += 1
            continue
        if s[i] == "}":
            node.end = i + 1
            break
        raise SurgicalError("expected ',' or '}' at %d" % i)
    # last-wins to mirror json.loads() for (malformed) duplicate keys
    val = {}
    for k, c in node.pairs:
        val[k] = c.value
    node.value = val
    return node


def parse(raw):
    """Parse `raw` into a span-tracking node tree. Raises SurgicalError on any
    structure the surgical path won't touch (incl. trailing garbage)."""
    root = _parse_value(raw, 0)
    tail = _skip_ws(raw, root.end)
    if tail != len(raw):
        raise SurgicalError("trailing content after top-level value at %d" % tail)
    return root


# --------------------------------------------------------------------------- #
# Path navigation (same dotted semantics as apply_suggestions.json_get/json_set)
# --------------------------------------------------------------------------- #
def _navigate(root, dotted):
    node = root
    for key in dotted.split("."):
        if node.kind == "object":
            found = None
            for k, child in node.pairs:   # last-wins, mirrors dict semantics
                if k == key:
                    found = child
            if found is None:
                raise SurgicalError("key %r not found in object" % key)
            node = found
        elif node.kind == "array":
            try:
                idx = int(key)
            except ValueError:
                raise SurgicalError("non-int index %r for array" % key)
            if idx < 0 or idx >= len(node.items):
                raise SurgicalError("index %d out of range" % idx)
            node = node.items[idx]
        else:
            raise SurgicalError("path descends into scalar at %r" % key)
    return node


def locate(raw, dotted):
    """Return the (start, end) raw-text span of the value at `dotted`.
    Raises SurgicalError if the path can't be resolved."""
    node = _navigate(parse(raw), dotted)
    return node.start, node.end


# --------------------------------------------------------------------------- #
# Style detection + scalar serialization
# --------------------------------------------------------------------------- #
def detect_ensure_ascii(raw):
    """True if the file ascii-escapes non-ASCII (contains `\\u` escapes and no
    raw non-ASCII bytes) — so re-encoded values match the file's escape style."""
    has_uesc = "\\u" in raw
    try:
        raw.encode("ascii")
        raw_nonascii = False
    except UnicodeEncodeError:
        raw_nonascii = True
    return has_uesc and not raw_nonascii


def serialize_scalar(value, ensure_ascii):
    """JSON-encode a scalar (or string) value as a single token, matching the
    file's escape style. Rejects containers — surgery is scalar/string-only."""
    if isinstance(value, (dict, list)):
        raise SurgicalError("surgical splice targets scalars/strings only")
    return json.dumps(value, ensure_ascii=ensure_ascii)


# --------------------------------------------------------------------------- #
# Splice + safety gate
# --------------------------------------------------------------------------- #
def _set_at_path(obj, dotted, value):
    """parse->set semantics identical to apply_suggestions.json_set, used to
    build the EXPECTED object for the safety gate."""
    keys = dotted.split(".")
    cur = obj
    for key in keys[:-1]:
        cur = cur[int(key)] if isinstance(cur, list) else cur[key]
    last = keys[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def splice_scalars(raw, edits):
    """Apply `edits` = [(dotted_path, new_value), ...] to `raw` as minimal
    in-place splices, preserving all surrounding formatting.

    Returns the new raw text. Raises SurgicalError (caller falls back to v1
    parse->set->serialize) if ANY edit can't be located, spans overlap, or the
    re-parsed result does not EXACTLY equal parse->set-at-path->(unchanged rest).
    """
    if not edits:
        return raw

    ensure_ascii = detect_ensure_ascii(raw)
    root = parse(raw)

    # Resolve every span against the ORIGINAL text, then serialize replacements.
    # A semantic NO-OP (same value AND same type) is skipped entirely so the
    # original bytes are preserved verbatim — this keeps non-canonical but valid
    # source formatting (e.g. `207.50`, `1e3`, `1.0`) byte-identical when the
    # value didn't actually change. bool/int are kept distinct (True is not 1).
    spans = []
    for path, value in edits:
        node = _navigate(root, path)
        if node.kind in ("object", "array"):
            raise SurgicalError("target %r is a container, not a scalar" % path)
        if node.value == value and type(node.value) is type(value):
            continue  # unchanged value -> leave source bytes untouched
        spans.append((node.start, node.end, serialize_scalar(value, ensure_ascii)))

    if not spans:
        return raw

    # Non-overlap guard (distinct scalar leaves never overlap; be defensive).
    ordered = sorted(spans, key=lambda t: t[0])
    for (s0, e0, _), (s1, _e1, _t) in zip(ordered, ordered[1:]):
        if e0 > s1:
            raise SurgicalError("overlapping edit spans")

    # Splice position-descending so earlier offsets stay valid.
    new_raw = raw
    for start, end, text in sorted(spans, key=lambda t: t[0], reverse=True):
        new_raw = new_raw[:start] + text + new_raw[end:]

    # SAFETY GATE: result must equal parse->set-at-path with everything else
    # untouched. This is the load-bearing correctness proof of the whole module.
    expected = json.loads(raw)
    for path, value in edits:
        _set_at_path(expected, path, value)
    try:
        got = json.loads(new_raw)
    except ValueError as exc:
        raise SurgicalError("spliced result is not valid JSON: %s" % exc)
    if got != expected:
        raise SurgicalError("spliced result does not match parse->set object")
    return new_raw


def splice_scalar(raw, dotted, value):
    """Single-edit convenience wrapper around splice_scalars."""
    return splice_scalars(raw, [(dotted, value)])
