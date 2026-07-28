#!/usr/bin/env python3
"""stamp_block_ids.py — mint + stamp durable block IDs into editable sources.

U2 of docs/plans/2026-07-28-002-feat-word-like-practicum-editing-plan.md.

Every prose block build_site.markdown() emits (paragraph, heading, list item,
blockquote) gets a trailing ``{#b:xxxxxxxx}`` marker — 8 lowercase hex chars,
unique corpus-wide, never reused, never re-minted for a block that already has
one. The marker IS the block's identity: `source_ref` becomes ``<file>#b<hex8>``
(or ``<file>#<json.path>.b<hex8>`` for markdown inside JSON string fields), so
inserting, deleting or reordering blocks moves nothing else.

Segmentation is build_site.markdown()'s own — the stamper renders each source
with the `spans` collector and stamps exactly the blocks the renderer emitted,
so the two can never drift.

Usage:
    python3 tools/stamp_block_ids.py                # stamp everything (writes)
    python3 tools/stamp_block_ids.py --check        # report only, no writes
    python3 tools/stamp_block_ids.py --equivalence BEFORE.json AFTER.json
                                                    # prove a migration moved
                                                    # nothing (exit 1 on drift)

Targets are derived from the generated maps (the ground truth of what renders
as editable prose): build/editor-map.generated.json,
build/instructor-bundle.generated.json, and — for blocks a build flagged as
unmarked — build/editor-unmarked.generated.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_site as bs      # noqa: E402  (shared renderer = shared segmentation)
import json_surgical         # noqa: E402

BID_RE = re.compile(r"\{#b:([0-9a-f]{8})\}")
_LOCATOR_SUFFIX_RE = re.compile(r"\.(p\d+|b[0-9a-f]{8})$")

EDITOR_MAP = os.path.join(REPO_ROOT, "build", "editor-map.generated.json")
INSTRUCTOR_BUNDLE = os.path.join(REPO_ROOT, "build",
                                 "instructor-bundle.generated.json")
UNMARKED_REPORT = os.path.join(REPO_ROOT, "build",
                               "editor-unmarked.generated.json")


def mint_bid(existing):
    """Mint a fresh 8-hex-char bid, unique against (and added to) `existing`."""
    while True:
        b = secrets.token_hex(4)
        if b not in existing:
            existing.add(b)
            return b


def stamp_md_text(raw, existing):
    """Stamp every renderer-emitted block in `raw` that lacks a marker.

    Returns ``(new_raw, n_stamped)``. Existing markers are never touched; their
    bids are folded into `existing` so mints can't collide with them."""
    text = raw.replace("\r\n", "\n")
    spans = []
    bs.markdown(text, spans=spans)
    for s in spans:
        if s["bid"]:
            existing.add(s["bid"])
    lines = text.split("\n")
    stamped = 0
    for s in spans:
        if s["bid"] is None:
            ln = lines[s["end_line"]]
            lines[s["end_line"]] = ln.rstrip() + " {#b:%s}" % mint_bid(existing)
            stamped += 1
    return "\n".join(lines), stamped


def _json_get(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def stamp_file(path, body_md_paths, existing, write=True):
    """Stamp one source file. ``.md`` -> the whole file; ``.json`` -> each of
    `body_md_paths` (dotted paths to markdown-rendered string fields), spliced
    back surgically so only those values' bytes change. Returns blocks stamped.

    Fails loudly (SurgicalError) rather than ever reformatting a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    if path.endswith(".md"):
        new_raw, n = stamp_md_text(raw, existing)
        if n and write:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_raw)
        return n

    obj = json.loads(raw)
    edits = []
    total = 0
    for dotted in sorted(body_md_paths):
        val = _json_get(obj, dotted)
        if not isinstance(val, str):
            continue
        new_val, n = stamp_md_text(val, existing)
        if n:
            edits.append((dotted, new_val))
            total += n
    if edits and write:
        new_raw = json_surgical.splice_scalars(raw, edits)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_raw)
    return total


def collect_targets(map_bundles):
    """Derive {relpath: set(body_md_paths)} from generated map bundles. An .md
    target has an empty set (whole file); a .json target lists the dotted paths
    of its markdown-rendered string fields. Only prose blocks count — scalars
    are path-keyed already and never stamped."""
    targets = {}
    for bundle in map_bundles:
        blocks = []
        for entries in (bundle.get("pages") or {}).values():
            blocks.extend(entries)
        for doc in bundle.get("docs") or []:
            blocks.extend(doc.get("blocks") or [])
        for b in blocks:
            if b.get("kind") != "prose":
                continue
            relpath, locator = b["source_ref"].split("#", 1)
            if relpath.endswith(".md"):
                targets.setdefault(relpath, set())
            else:
                base = _LOCATOR_SUFFIX_RE.sub("", locator)
                targets.setdefault(relpath, set()).add(base)
    return targets


def collect_unmarked_targets(report):
    """Targets from a build's unmarked report: src bases are either a bare .md
    relpath or ``file.json#json.path``."""
    targets = {}
    for src in (report.get("unmarked") or {}):
        if "#" in src:
            relpath, base = src.split("#", 1)
            targets.setdefault(relpath, set()).add(base)
        else:
            targets.setdefault(src, set())
    return targets


def existing_bids_in_corpus(targets):
    """Every bid already present in the target files (corpus-wide registry)."""
    existing = set()
    for relpath in targets:
        p = os.path.join(REPO_ROOT, relpath)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as fh:
                existing.update(BID_RE.findall(fh.read()))
    return existing


def equivalence_check(before, after):
    """Mechanical before/after proof for a migration (R8): every block on every
    page must keep its page, its index, its text, its kind, its json_path and
    its source FILE. Only the locator (ordinal -> bid) may differ. Returns a
    list of human-readable errors — empty means nothing moved."""
    errors = []
    bp = before.get("pages") or {}
    ap = after.get("pages") or {}
    for page in bp:
        if page not in ap:
            errors.append("page missing after migration: %s" % page)
            continue
        b_blocks, a_blocks = bp[page], ap[page]
        if len(b_blocks) != len(a_blocks):
            errors.append("block count changed on %s: %d -> %d"
                          % (page, len(b_blocks), len(a_blocks)))
        for x, y in zip(b_blocks, a_blocks):
            where = "%s[%s]" % (page, x.get("index"))
            if x.get("index") != y.get("index"):
                errors.append("%s: index %s -> %s" % (where, x.get("index"),
                                                      y.get("index")))
            if x.get("original_text") != y.get("original_text"):
                errors.append("%s: text changed (%r -> %r)"
                              % (where, (x.get("original_text") or "")[:60],
                                 (y.get("original_text") or "")[:60]))
            if x.get("kind") != y.get("kind"):
                errors.append("%s: kind %s -> %s" % (where, x.get("kind"),
                                                     y.get("kind")))
            if (x.get("json_path") or None) != (y.get("json_path") or None):
                errors.append("%s: json_path %s -> %s"
                              % (where, x.get("json_path"), y.get("json_path")))
            bf = (x.get("source_ref") or "").split("#", 1)[0]
            af_ = (y.get("source_ref") or "").split("#", 1)[0]
            if bf != af_:
                errors.append("%s: source file %s -> %s" % (where, bf, af_))
    for page in ap:
        if page not in bp:
            errors.append("page appeared after migration: %s" % page)
    return errors


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap_ = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap_.add_argument("--check", action="store_true",
                     help="report what would be stamped; write nothing")
    ap_.add_argument("--equivalence", nargs=2, metavar=("BEFORE", "AFTER"),
                     help="compare two editor-map files; exit 1 on any drift")
    args = ap_.parse_args(argv)

    if args.equivalence:
        errors = equivalence_check(_load(args.equivalence[0]),
                                   _load(args.equivalence[1]))
        if errors:
            for e in errors:
                print("DRIFT: " + e)
            print("%d error(s) — the migration MOVED content." % len(errors))
            return 1
        print("equivalence proven: no block moved.")
        return 0

    bundles = []
    for p in (EDITOR_MAP, INSTRUCTOR_BUNDLE):
        if os.path.isfile(p):
            bundles.append(_load(p))
    if not bundles:
        print("FATAL: no generated map found under build/ — run "
              "tools/build_site.py first.", file=sys.stderr)
        return 2
    targets = collect_targets(bundles)
    if os.path.isfile(UNMARKED_REPORT):
        for relpath, bases in collect_unmarked_targets(
                _load(UNMARKED_REPORT)).items():
            targets.setdefault(relpath, set()).update(bases)

    existing = existing_bids_in_corpus(targets)
    n_before = len(existing)
    total = 0
    touched = 0
    for relpath in sorted(targets):
        p = os.path.join(REPO_ROOT, relpath)
        if not os.path.isfile(p):
            print("WARNING: mapped source missing on disk: %s" % relpath)
            continue
        n = stamp_file(p, targets[relpath], existing, write=not args.check)
        if n:
            touched += 1
            total += n
    verb = "would stamp" if args.check else "stamped"
    print("%s %d block(s) across %d file(s); %d pre-existing bid(s) preserved; "
          "%d file(s) targeted."
          % (verb, total, touched, n_before, len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
