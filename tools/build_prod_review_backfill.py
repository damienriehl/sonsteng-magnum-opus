#!/usr/bin/env python3
"""Build or audit one deterministic legacy Publisher-review backfill payload."""
from __future__ import annotations

import argparse
import json
import pathlib

from apply_suggestions import Patch, STRUCTURAL_KINDS, build_review_revisions


class BackfillError(RuntimeError):
    pass


def _chain(batches, prod_base):
    remaining = {item["batch_id"]: item for item in batches}
    by_base = {}
    for item in remaining.values():
        if item.get("base_sha") in by_base:
            raise BackfillError("apply batches do not form one unambiguous chain")
        by_base[item.get("base_sha")] = item
    chain, expected = [], prod_base
    while remaining:
        item = by_base.get(expected)
        if not item or item["batch_id"] not in remaining:
            raise BackfillError("apply batches do not form one unambiguous chain")
        if item.get("phase") != "done" or not item.get("commit_sha"):
            raise BackfillError("backfill includes an incomplete apply batch")
        chain.append({key:item[key] for key in ("batch_id","base_sha","commit_sha")})
        expected = item["commit_sha"]
        del remaining[item["batch_id"]]
    return chain


def build_payload(evidence, migration_id, prod_base):
    if evidence.get("schema_version") != 1:
        raise BackfillError("unsupported evidence schema")
    suggestions = evidence.get("suggestions") or []
    batches = evidence.get("batches") or []
    if not suggestions or not batches:
        raise BackfillError("backfill evidence is empty")
    chain = _chain(batches, prod_base)
    positions = {item["batch_id"]: index for index,item in enumerate(chain)}
    grouped = {}
    for row in suggestions:
        batch_id = row.get("apply_batch_id")
        if batch_id not in positions:
            raise BackfillError("applied suggestion lacks completed batch evidence")
        source_ref = row.get("source_ref") or ""
        if "#" not in source_ref:
            raise BackfillError("applied suggestion lacks durable source identity")
        relpath, locator = source_ref.split("#",1)
        operation = row.get("kind") if row.get("kind") in STRUCTURAL_KINDS else None
        grouped.setdefault(source_ref,[]).append((positions[batch_id],Patch(
            suggestion_id=row["id"],group_id=row.get("group_id"),source_ref=source_ref,
            relpath=relpath,kind=row.get("kind") or "prose",json_path=locator,
            original_text=row.get("original_text") or "",new_text=row.get("new_text") or "",
            op=operation,created_at=int(row.get("created_at") or 0),editor=row.get("editor"))))
    revisions = []
    for source_ref, indexed in sorted(grouped.items()):
        indexed.sort(key=lambda item:(item[0],item[1].created_at,item[1].suggestion_id))
        last = max(index for index,_patch in indexed)
        source_chain = chain[:last + 1]
        patches = [patch for _index,patch in indexed]
        built = build_review_revisions(patches,source_chain[-1]["commit_sha"],prod_base)
        if len(built) != 1 or built[0]["source_ref"] != source_ref:
            raise BackfillError("atomic review evidence did not preserve source identity")
        revision = built[0]
        revision["batch_chain"] = source_chain
        revision["suggestion_evidence"] = [{
            "suggestion_id":patch.suggestion_id,
            "batch_id":chain[index]["batch_id"],
            "commit_sha":chain[index]["commit_sha"],
        } for index,patch in indexed]
        revisions.append(revision)
    return {"migration_id":migration_id,"prod_base":prod_base,"revisions":revisions}


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",required=True)
    parser.add_argument("--migration-id",required=True)
    parser.add_argument("--prod-base",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args(argv)
    evidence = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8"))
    payload = _canonical(build_payload(evidence,args.migration_id,args.prod_base))
    output = pathlib.Path(args.output)
    if args.check:
        try: existing = output.read_text(encoding="utf-8")
        except OSError as exc: raise BackfillError("backfill payload does not match deterministic evidence") from exc
        if existing != payload:
            raise BackfillError("backfill payload does not match deterministic evidence")
    else:
        output.write_text(payload,encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
