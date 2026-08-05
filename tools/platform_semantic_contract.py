#!/usr/bin/env python3
"""Capture and compare the generated Platform's presentation-neutral contract.

The snapshot intentionally records authored text, headings, links, and durable
editor identity while ignoring element wrappers, classes, and other styling.
"""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
import re
import sys


_SPACE_RE = re.compile(r"\s+")
_PRESENTATIONAL_TEXT = {"→", "←", "↗", "▸", "•"}
_STATIC_EXCLUDED_PREFIXES = ("assets/", "chat/")


def _normalize(value):
    return _SPACE_RE.sub(" ", value or "").strip()


class _SemanticHTMLParser(HTMLParser):
    """Extract visible semantic content without depending on page structure."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._excluded = 0
        self._heading = None
        self._link = None
        self.text = []
        self.headings = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self._excluded or tag in {"script", "style", "template", "noscript"} \
                or "hidden" in attrs or attrs.get("aria-hidden") == "true":
            self._excluded += 1
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = [int(tag[1]), []]
        if tag == "a":
            self._link = [attrs.get("href", ""), []]

    def handle_endtag(self, tag):
        if self._excluded:
            self._excluded -= 1
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading:
            level, chunks = self._heading
            self.headings.append({"level": level, "text": _normalize(" ".join(chunks))})
            self._heading = None
        if tag == "a" and self._link:
            href, chunks = self._link
            self.links.append({"text": _normalize(" ".join(chunks)), "href": href})
            self._link = None

    def handle_data(self, data):
        if self._excluded:
            return
        value = _normalize(data)
        if not value or value in _PRESENTATIONAL_TEXT:
            return
        self.text.append(value)
        if self._heading:
            self._heading[1].append(value)
        if self._link:
            self._link[1].append(value)


def capture_site(site_dir, editor_map):
    """Return a stable, JSON-serializable semantic snapshot."""
    pages = {}
    integrity_errors = []
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root, dirs, files in os.walk(site_dir):
        dirs.sort()
        for filename in sorted(files):
            if not filename.endswith(".html"):
                continue
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, site_dir).replace(os.sep, "/")
            # Authored design assets and JavaScript-driven chat/critique have
            # separate contracts. This snapshot covers generated static pages.
            if rel.startswith(_STATIC_EXCLUDED_PREFIXES):
                continue
            parser = _SemanticHTMLParser()
            with open(path, encoding="utf-8") as fh:
                parser.feed(fh.read())
            blocks = (editor_map.get("pages") or {}).get(rel, [])
            indices = [block.get("index") for block in blocks]
            if any(not isinstance(index, int) for index in indices):
                integrity_errors.append(f"{rel}: editor block without integer placement index")
            if len(indices) != len(set(indices)):
                integrity_errors.append(f"{rel}: duplicate editor placement index")
            for block in blocks:
                source_file = (block.get("source_ref") or "").split("#", 1)[0]
                if not source_file or not os.path.isfile(os.path.join(repo_root, source_file)):
                    integrity_errors.append(
                        f"{rel}: unresolvable editor source_ref {block.get('source_ref')!r}")
            pages[rel] = {
                "text": parser.text,
                "headings": parser.headings,
                "links": parser.links,
                "editor_blocks": [
                    {key: block.get(key) for key in
                     ("source_ref", "kind", "json_path", "original_text")}
                    for block in blocks
                ],
                # Index is placement, not identity. Preserve only the ordered
                # attachment of durable identities to the rendered page.
                "reading_order": [block.get("source_ref") for block in blocks],
            }
    return {"schema_version": "1.0.0", "pages": pages,
            "integrity_errors": integrity_errors}


def freeze_snapshot(snapshot):
    """Compact a snapshot into a durable full-corpus baseline."""
    source_pages = snapshot.get("pages") or {}
    fields = {}
    for field in ("text", "headings", "links", "editor_blocks", "reading_order"):
        values = {page: record.get(field) for page, record in sorted(source_pages.items())}
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        fields[field] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"schema_version": "1.0.0-digests", "page_ids": sorted(source_pages),
            "fields": fields}


def validate_snapshot(snapshot):
    """Return identity integrity errors independent of snapshot parity."""
    errors = list(snapshot.get("integrity_errors") or [])
    for page, record in (snapshot.get("pages") or {}).items():
        refs = [b.get("source_ref") for b in record.get("editor_blocks", [])]
        if any(not ref for ref in refs):
            errors.append(f"{page}: editor block without source_ref")
        if len(refs) != len(set(refs)):
            errors.append(f"{page}: duplicate durable source_ref")
        if refs != record.get("reading_order", []):
            errors.append(f"{page}: editor block attachment order is inconsistent")
    return errors


def compare_snapshots(expected, actual):
    """Return concise drift diagnostics; an empty list means parity."""
    errors = validate_snapshot(actual)
    if expected.get("schema_version") == "1.0.0-digests":
        frozen = freeze_snapshot(actual)
        if expected.get("page_ids") != frozen["page_ids"]:
            errors.append("page identity changed: %r -> %r" %
                          (expected.get("page_ids"), frozen["page_ids"]))
        for field, digest in expected.get("fields", {}).items():
            if digest != frozen["fields"].get(field):
                errors.append(f"full corpus {field} changed")
        return errors
    expected_pages = expected.get("pages") or {}
    actual_pages = actual.get("pages") or {}
    for page in sorted(set(expected_pages) | set(actual_pages)):
        if page not in actual_pages:
            errors.append(f"{page}: page missing")
            continue
        if page not in expected_pages:
            errors.append(f"{page}: unexpected page")
            continue
        before, after = expected_pages[page], actual_pages[page]
        for field in ("text", "headings", "links", "editor_blocks", "reading_order"):
            expected_value = before.get(field)
            actual_value = after.get(field)
            if isinstance(expected_value, dict) and "sha256" in expected_value:
                payload = json.dumps(actual_value, ensure_ascii=False,
                                     sort_keys=True, separators=(",", ":"))
                actual_value = {
                    "count": len(actual_value or []),
                    "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                }
            if expected_value != actual_value:
                errors.append(f"{page}: {field} changed: {before.get(field)!r} -> {after.get(field)!r}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline")
    parser.add_argument("site")
    parser.add_argument("editor_map")
    args = parser.parse_args(argv)
    with open(args.baseline, encoding="utf-8") as fh:
        baseline = json.load(fh)
    with open(args.editor_map, encoding="utf-8") as fh:
        editor_map = json.load(fh)
    errors = compare_snapshots(baseline, capture_site(args.site, editor_map))
    for error in errors:
        print("SEMANTIC DRIFT: " + error)
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
