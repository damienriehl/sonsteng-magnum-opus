#!/usr/bin/env python3
r"""editor_ai_rewrite.py — the AI-rewrite PROPOSER for accepted conceptual comments.

Damien's deepening decision (plan item 6): AI-rewrite proposals ship in v1, and
the apply-loop AGENT (this orchestrator harness) authors them — there is NO API
key and NO Worker LLM call anywhere in the pipeline. This tool is the machine
half of that handoff: it collects the accepted *conceptual comments* that ask for
a rewrite and writes a structured task file the orchestrator fulfills.

STATUS MODEL (from docs/research/editor-apply-spec.md + API-CONTRACTS.md):
  * A `comment` suggestion carries John's free-text note, not an edit.
  * Admin `decide(accept)` is the SOLE writer of `accepted`. Accepting a comment
    does NOT produce an edit — the apply engine cannot patch a comment.
  * This tool reads the accepted `comment` rows and emits, per row:
        { id, source_ref, matter, original_text, comment }
    into build/ai-rewrite-queue.json.

THE HANDOFF (documented contract — no code here does the LLM step):
  1. This tool writes build/ai-rewrite-queue.json (the work list).
  2. The ORCHESTRATOR (a Claude harness turn) reads each item, authors a rewrite
     of `original_text` that honors `comment`, and SUBMITS it back as a NEW
     suggestion via POST /edit/v1/suggest with:
         origin   = "ai_rewrite"        (attributed "AI (from JOS comment)")
         kind     = "prose"
         source_ref = <the comment's source_ref>
         new_text = <the authored rewrite>
     landing as `pending` — subject to the SAME admin-accept gate + text-node-only
     rendering as any human suggestion. It is structurally impossible for an
     ai_rewrite to reach the spine without Damien accepting it, then the normal
     apply transaction running.
  3. Optionally the orchestrator marks the source comment consumed (a decline
     with a note, or leaves it accepted as the provenance record).

Python 3, stdlib only. The RPC client + map are injectable (same shape as
tools/apply_suggestions.py) so this is unit-testable without a live Worker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_suggestions as ap  # noqa: E402  (reuse RPC client, index_map, matter_of)

REPO_ROOT = ap.REPO_ROOT
QUEUE_PATH = os.path.join(REPO_ROOT, "build", "ai-rewrite-queue.json")


def collect_rewrite_tasks(client, source_index):
    """Return the AI-rewrite task list from the accepted conceptual comments.

    `client.fetch_accepted_comments()` returns the accepted rows whose kind is
    `comment`. For each, original_text is re-resolved from the CURRENT map
    (server-truth) — never trusted from the row — so the orchestrator rewrites
    against what the source actually says today.
    """
    tasks = []
    for row in client.fetch_accepted_comments():
        source_ref = row.get("source_ref", "")
        block = source_index.get(source_ref) or {}
        tasks.append({
            "id": row.get("id"),
            "source_ref": source_ref,
            "matter": ap.matter_of(source_ref),
            "original_text": block.get("original_text") or row.get("original_text") or "",
            "context": block.get("context") or row.get("context") or "",
            "comment": row.get("comment") or "",
            "submit_as": {
                "origin": "ai_rewrite",
                "kind": "prose",
                "source_ref": source_ref,
                "status": "pending",
                "attribution": "AI (from JOS comment)",
            },
        })
    return tasks


def write_queue(tasks, path=QUEUE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bundle = {
        "generated_by": "tools/editor_ai_rewrite.py",
        "note": ("Accepted conceptual comments awaiting an AI rewrite. The apply-loop "
                 "ORCHESTRATOR authors each rewrite (no API key in-tool) and submits it "
                 "via POST /edit/v1/suggest origin=ai_rewrite, status=pending — same "
                 "admin-accept gate as any human suggestion."),
        "count": len(tasks),
        "tasks": tasks,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


# HTTP client extension: the accepted-comments query (GET /edit/v1/review, filter).
class RewriteHttpClient(ap.HttpRpcClient):
    def fetch_accepted_comments(self):
        review = self._req("GET", "/review")
        items = review.get("items") or review.get("suggestions") or []
        return [r for r in items
                if r.get("status") == "accepted" and r.get("kind") == "comment"]


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="editor_ai_rewrite.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get(ap.ENV_API_BASE),
                   help="Worker /edit/v1 base URL (env %s)." % ap.ENV_API_BASE)
    p.add_argument("--out", default=QUEUE_PATH, help="queue output path.")
    p.add_argument("--rebuild-map", action="store_true",
                   help="Regenerate the editor map before resolving original_text.")
    args = p.parse_args(argv)

    token = os.environ.get(ap.ENV_SERVICE_TOKEN)
    if not args.base_url or not token:
        print("error: needs --base-url and $%s (service token)." % ap.ENV_SERVICE_TOKEN,
              file=sys.stderr)
        return 2

    if args.rebuild_map:
        source_index = ap.SubprocessPipeline().regenerate_map(REPO_ROOT)
    else:
        map_path = os.path.join(REPO_ROOT, "build", "editor-map.generated.json")
        if os.path.isfile(map_path):
            with open(map_path, "r", encoding="utf-8") as fh:
                source_index = ap.index_map(json.load(fh))
        else:
            source_index = {}

    client = RewriteHttpClient(args.base_url, token)
    tasks = collect_rewrite_tasks(client, source_index)
    path = write_queue(tasks, args.out)
    print("wrote %d AI-rewrite task(s) -> %s" % (len(tasks), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
