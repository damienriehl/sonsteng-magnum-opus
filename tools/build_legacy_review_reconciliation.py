#!/usr/bin/env python3
"""Build an audited reconciliation for pre-provenance applied suggestions."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess

from apply_suggestions import Patch, STRUCTURAL_KINDS, build_review_revisions, json_get
from build_prod_review_backfill import BackfillError, _canonical
from structural_ops import StructuralError, _blocks, locate_block


def _git(repo, *args, text=True):
    return subprocess.check_output(["git", *args], cwd=repo, text=text)


def _commit_matches_batch(repo, commit, batch):
    if not isinstance(commit, str) or not commit:
        return False
    try:
        parents = _git(repo, "show", "-s", "--format=%P", commit).strip().split()
        subject = _git(repo, "show", "-s", "--format=%s", commit).strip()
    except subprocess.CalledProcessError:
        return False
    return bool(parents and parents[0] == batch.get("base_sha") and
                subject.startswith("apply: batch " + batch["batch_id"] + " "))


def _commit_applies_suggestion(repo, commit, batch, row):
    """Prove the named batch commit introduced this exact durable-source edit."""
    if not _commit_matches_batch(repo, commit, batch):
        return False
    rel, locator = row["source_ref"].split("#", 1)
    try:
        before = _source_value(repo, batch["base_sha"], rel, locator)
        after = _source_value(repo, commit, rel, locator)
        if before == row.get("original_text") and after == row.get("new_text"):
            return True
    except (BackfillError, subprocess.CalledProcessError, KeyError, ValueError):
        pass

    # The oldest captured rows predate durable block locators, and structural
    # operations deliberately retain or remove their anchor instead of turning
    # it into new_text. Prove those rows against the complete named file at the
    # exact parent and apply commit. Every accepted form below is unique and
    # transition-specific, so a correctly named commit touching another path or
    # another block still fails closed.
    try:
        before_file = _git(repo, "show", batch["base_sha"] + ":" + rel)
        after_file = _git(repo, "show", commit + ":" + rel)
    except subprocess.CalledProcessError:
        return False
    if before_file == after_file:
        return False
    old = row.get("original_text")
    new = row.get("new_text")
    kind = row.get("kind") or "prose"
    if kind in ("prose", "json_scalar") and old is not None and new is not None:
        return (before_file.count(str(old)) == 1 and after_file.count(str(old)) == 0 and
                before_file.count(str(new)) == 0 and after_file.count(str(new)) == 1)
    bid = locator.removeprefix("b")
    if kind in ("insert_after", "delete", "move") and rel.endswith(".md"):
        try:
            before_blocks = _blocks(before_file)
            after_blocks = _blocks(after_file)
            before_index = next(i for i, block in enumerate(before_blocks) if block.bid == bid)
        except (StructuralError, StopIteration):
            return False
        if kind == "insert_after" and new is not None:
            try:
                after_anchor = next(i for i, block in enumerate(after_blocks) if block.bid == bid)
            except StopIteration:
                return False
            inserted = [i for i, block in enumerate(after_blocks) if block.raw == str(new)]
            return len(inserted) == 1 and inserted[0] == after_anchor + 1
        if kind == "delete":
            return all(block.bid != bid for block in after_blocks)
        if kind == "move":
            try:
                after_index = next(i for i, block in enumerate(after_blocks) if block.bid == bid)
            except StopIteration:
                return False
            before_side = {block.bid: i < before_index for i, block in enumerate(before_blocks)
                           if block.bid != bid}
            after_side = {block.bid: i < after_index for i, block in enumerate(after_blocks)
                          if block.bid != bid}
            return any(before_side[other] != after_side[other]
                       for other in before_side.keys() & after_side.keys())
    return False


def _source_value(repo, revision, relpath, locator):
    raw = _git(repo, "show", revision + ":" + relpath, text=False)
    if relpath.endswith(".json"):
        value = json.loads(raw.decode("utf-8"))
        parts = locator.rsplit(".b", 1)
        if len(parts) == 2 and len(parts[1]) == 8:
            try:
                return locate_block(json_get(value, parts[0]), parts[1]).raw
            except StructuralError as exc:
                raise BackfillError("source locator is not uniquely resolvable") from exc
        return json_get(value, locator)
    text = raw.decode("utf-8")
    bid = locator.removeprefix("b")
    try:
        return locate_block(text, bid).raw
    except StructuralError as exc:
        raise BackfillError("source locator is not uniquely resolvable") from exc


def build_reconciliation(evidence, classification, repo, migration_id, prod_base):
    repo = pathlib.Path(repo).resolve()
    suggestion_rows = evidence.get("suggestions", [])
    batch_rows = evidence.get("batches", [])
    effective_ids = classification.get("effective_ids") or []
    exclusion_rows = classification.get("exclusions") or []
    if len({row.get("id") for row in suggestion_rows}) != len(suggestion_rows) or \
       len({row.get("batch_id") for row in batch_rows}) != len(batch_rows) or \
       len(set(effective_ids)) != len(effective_ids) or \
       len({row.get("suggestion_id") for row in exclusion_rows}) != len(exclusion_rows):
        raise BackfillError("legacy evidence identities must be unique")
    rows = {row["id"]: row for row in suggestion_rows}
    batches = {row["batch_id"]: row for row in batch_rows}
    effective = set(effective_ids)
    excluded = {row["suggestion_id"]: row for row in exclusion_rows}
    if not effective or effective & excluded.keys() or effective | excluded.keys() != rows.keys():
        raise BackfillError("classification must cover every legacy suggestion exactly once")
    exclusions = []
    for suggestion_id, item in sorted(excluded.items()):
        row = rows[suggestion_id]
        batch = batches.get(row.get("apply_batch_id"))
        commit = item.get("apply_commit", "")
        if not batch or not _commit_applies_suggestion(repo, commit, batch, row):
            raise BackfillError("exclusion apply commit does not match its immutable batch")
        rel = row["source_ref"].split("#", 1)[0]
        current = _git(repo, "show", "HEAD:" + rel, text=False)
        proof_base = item.get("proof_base_sha")
        if proof_base:
            restored = _git(repo, "show", proof_base + ":" + rel, text=False)
            if current != restored:
                raise BackfillError("excluded source is not restored to its proof base")
        else:
            locator = row["source_ref"].split("#", 1)[1]
            if row.get("kind") in ("insert_after", "move"):
                raise BackfillError("structural exclusion requires an exact proof base")
            try:
                restored_value = _source_value(repo, "HEAD", rel, locator)
                restored = restored_value == row.get("original_text")
            except (BackfillError, KeyError, ValueError):
                # Old pN locators are not durable. For those legacy rows only,
                # require the recorded original to be unique in current
                # canonical bytes and the applied replacement to be absent.
                text = current.decode("utf-8")
                old = row.get("original_text")
                new = row.get("new_text")
                restored = (old is not None and text.count(str(old)) == 1 and
                            (new is None or text.count(str(new)) == 0))
            if not restored:
                raise BackfillError("excluded prose is not restored in current canonical source")
        proof = {"suggestion_id": suggestion_id, "apply_batch_id": batch["batch_id"],
                 "apply_commit": commit, "proof_base_sha": proof_base or "",
                 "current_blob": hashlib.sha256(current).hexdigest()}
        exclusions.append({"suggestion_id": suggestion_id, "source_ref": row["source_ref"],
                           "apply_batch_id": batch["batch_id"], "apply_commit": commit,
                           "apply_base": batch["base_sha"],
                           "reason": "reverted_legacy_uat",
                           "evidence_hash": hashlib.sha256(_canonical(proof).encode()).hexdigest()})
    patches_by_source = {}
    source_commits = {}
    for suggestion_id in sorted(effective):
        row = rows[suggestion_id]
        batch = batches.get(row.get("apply_batch_id"))
        commit = batch and batch.get("commit_sha")
        if not batch or not _commit_applies_suggestion(repo, commit, batch, row):
            raise BackfillError("effective suggestion lacks exact apply commit evidence")
        rel, locator = row["source_ref"].split("#", 1)
        current_value = _source_value(repo, "HEAD", rel, locator)
        base_value = _source_value(repo, prod_base, rel, locator)
        if current_value != row.get("new_text") or base_value != row.get("original_text"):
            raise BackfillError("effective suggestion does not match PROD and current source")
        source_ref = row["source_ref"]
        patches_by_source.setdefault(source_ref, []).append(Patch(
            suggestion_id=suggestion_id, group_id=row.get("group_id"), source_ref=source_ref,
            relpath=rel, kind=row.get("kind") or "prose", json_path=locator,
            original_text=row["original_text"], new_text=row["new_text"],
            op=row.get("kind") if row.get("kind") in STRUCTURAL_KINDS else None,
            created_at=int(row.get("created_at") or 0), editor=row.get("editor")))
        prior = source_commits.get(source_ref)
        candidate = (int(row.get("created_at") or 0), suggestion_id, commit)
        if prior is None or candidate[:2] > prior[:2]:
            source_commits[source_ref] = candidate
    revisions = []
    for source_ref, patches in sorted(patches_by_source.items()):
        built = build_review_revisions(
            sorted(patches, key=lambda patch: (patch.created_at, patch.suggestion_id)),
            source_commits[source_ref][2], prod_base)
        evidence_items = []
        for patch in sorted(patches, key=lambda item: (item.created_at, item.suggestion_id)):
            row = rows[patch.suggestion_id]
            batch = batches[row["apply_batch_id"]]
            evidence_items.append({"suggestion_id": patch.suggestion_id,
                                   "batch_id": batch["batch_id"],
                                   "base_sha": batch["base_sha"],
                                   "commit_sha": batch["commit_sha"]})
        for revision in built:
            revision["suggestion_evidence"] = evidence_items
        revisions.extend(built)
    return {"migration_id": migration_id, "prod_base": prod_base,
            "exclusions": exclusions, "revisions": revisions}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--classification", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--prod-base", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    payload = _canonical(build_reconciliation(
        json.loads(pathlib.Path(args.evidence).read_text(encoding="utf-8")),
        json.loads(pathlib.Path(args.classification).read_text(encoding="utf-8")),
        args.repo, args.migration_id, args.prod_base))
    out = pathlib.Path(args.output)
    if args.check:
        if out.read_text(encoding="utf-8") != payload:
            raise BackfillError("reconciliation payload drift")
    else:
        out.write_text(payload, encoding="utf-8")
    return 0
if __name__ == "__main__": raise SystemExit(main())
