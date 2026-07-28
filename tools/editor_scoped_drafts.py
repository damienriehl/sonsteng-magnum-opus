#!/usr/bin/env python3
r"""editor_scoped_drafts.py — the U7 drafter: enumerate -> draft -> one group.

An editor files a natural-language change at a chosen scope (POST
/edit/v1/scoped-request; ceiling + radius enforced server-side). This home-box
tool turns each request into ONE reviewable ai_rewrite group:

  1. CLAIM the request (requested -> drafting).
  2. ENUMERATE its blocks deterministically from build/editor-map.generated.json
     (the same scope semantics the Worker's /edit/v1/scope serves — part /
     matter / module / course, with task-derived module membership).
  3. DRAFT one edit per block that the instruction actually touches, via the
     headless Claude CLI (same pattern as editorial_pass.py — no API key in the
     pipeline; the CLI call is injectable and every draft is validated: known
     ref, changed text, no {#b: marker bytes).
  4. SUBMIT the drafts via POST /system-suggest — origin=ai_rewrite, ONE
     group_id, the instruction riding each row's comment as provenance. They
     land pending; Damien reviews the redline and accepts or declines THE
     GROUP as one unit (R5/R7 — an ai_rewrite is never auto-accepted).

KTD5 — canary: a module- or course-scoped request drafts ONE matter first (the
alphabetically first member — deterministic). Only after that canary group is
FULLY APPLIED (the apply transaction's validate+build+parity is the "verifies
clean" gate) does the remainder draft, as a second group. A declined canary
declines the whole request. Progression is driven by this tool on later runs.

Python 3, stdlib only. CLI, client and map injectable; unit-tested with fakes
(tools/tests/test_editor_scoped_drafts.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_suggestions as ap  # noqa: E402  (HttpRpcClient + env conventions)

REPO_ROOT = ap.REPO_ROOT
MAP_PATH = os.path.join(REPO_ROOT, "build", "editor-map.generated.json")

DEFAULT_CLI = os.environ.get("SONSTENG_CLAUDE_BIN", "claude")
DEFAULT_MODEL = os.environ.get("SONSTENG_SCOPED_MODEL", "opus")
CLI_TIMEOUT_S = int(os.environ.get("SONSTENG_SCOPED_TIMEOUT_S", "600"))
CHUNK = 30                       # blocks per CLI call
BID_MARKER = "{#b:"


class ScopedError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Enumeration — the python twin of the Worker's enumerateScope (editor-map.js).
# --------------------------------------------------------------------------- #
def _all_blocks(map_bundle):
    out = []
    for page, blocks in (map_bundle.get("pages") or {}).items():
        for b in blocks:
            out.append(dict(b, page=page))
    return out

def enumerate_blocks(map_bundle, params):
    """The exact block set a scope contains. Raises ScopedError on an invalid
    scope — the drafter never guesses."""
    scopes = map_bundle.get("scopes") or {}
    level = params.get("level")
    blocks = _all_blocks(map_bundle)

    if level == "course":
        return blocks
    if level == "matter":
        matter = params.get("matter")
        if not matter or matter not in (scopes.get("matters") or {}):
            raise ScopedError("unknown matter %r" % matter)
        pre = "data/matters/%s/" % matter
        return [b for b in blocks if b["source_ref"].startswith(pre)]
    if level == "part":
        matter = params.get("matter")
        part = params.get("part")
        meta = (scopes.get("matters") or {}).get(matter or "")
        if not meta or not part or part not in meta.get("parts", []):
            raise ScopedError("unknown part %r of %r" % (part, matter))
        pre = ("data/matters/%s/matter.json#" % matter if part == "matter"
               else "data/matters/%s/%s/" % (matter, part))
        return [b for b in blocks if b["source_ref"].startswith(pre)]
    if level == "module":
        mod = (scopes.get("modules") or {}).get(params.get("module") or "")
        if not mod:
            raise ScopedError("unknown module %r" % params.get("module"))
        prefixes = [mod["curriculum"] + "#"] + \
            ["data/matters/%s/exercise/" % s for s in mod["matters"]]
        return [b for b in blocks
                if any(b["source_ref"].startswith(p) for p in prefixes)]
    raise ScopedError("unknown level %r" % level)


def module_members(map_bundle, req):
    scopes = map_bundle.get("scopes") or {}
    if req["level"] == "module":
        return (scopes.get("modules") or {}).get(req["module"] or "", {}) \
            .get("matters", [])
    if req["level"] == "course":
        return sorted((scopes.get("matters") or {}).keys())
    return []


def pick_canary(members):
    """Deterministic: the alphabetically first member."""
    return sorted(members)[0] if members else None


def split_canary(blocks, canary):
    pre = "data/matters/%s/" % canary
    canary_blocks = [b for b in blocks if b["source_ref"].startswith(pre)]
    remainder = [b for b in blocks if not b["source_ref"].startswith(pre)]
    return canary_blocks, remainder


# --------------------------------------------------------------------------- #
# Drafting — headless CLI, strict JSON out, validation before anything posts.
# --------------------------------------------------------------------------- #
PROMPT_HEAD = (
    "You are drafting a scoped editorial change for the Sonsteng legal "
    "practicum. The editor's instruction:\n\n  %s\n\n"
    "Below is a JSON list of content blocks (each with a source_ref and its "
    "current text). For EVERY block the instruction genuinely requires "
    "changing, produce the revised text. Blocks the instruction does not "
    "touch must be OMITTED from your answer.\n"
    "Rules: preserve the register and factual content except as instructed; "
    "plain text only (no markdown additions beyond what the original "
    "carries); never invent citations; never include the byte sequence "
    "'{#b:'.\n"
    "Answer with ONLY this JSON, nothing else:\n"
    '{"drafts": [{"source_ref": "...", "new_text": "..."}]}\n\n'
    "BLOCKS_JSON:")


def build_prompt(instruction, blocks):
    payload = {"blocks": [{"source_ref": b["source_ref"],
                           "original_text": b.get("original_text") or ""}
                          for b in blocks]}
    return PROMPT_HEAD % instruction + json.dumps(payload, ensure_ascii=False)


def run_cli(prompt, *, cli=DEFAULT_CLI, model=DEFAULT_MODEL,
            timeout=CLI_TIMEOUT_S):
    """Invoke the headless Claude CLI (editorial_pass.py pattern). Returns raw
    stdout text; raises ScopedError on failure/timeout."""
    cmd = [cli, "-p", prompt, "--model", model, "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, check=False, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScopedError("cli unavailable: %s" % exc)
    if proc.returncode != 0:
        raise ScopedError("cli exit %d: %s" % (proc.returncode,
                                               (proc.stderr or "")[-400:]))
    return proc.stdout or ""


def parse_drafts(raw):
    """Accept either the CLI JSON envelope ({"result": "<text>"}) or bare JSON."""
    text = raw
    try:
        env = json.loads(raw)
        if isinstance(env, dict) and isinstance(env.get("result"), str):
            text = env["result"]
        elif isinstance(env, dict) and "drafts" in env:
            return env.get("drafts") or []
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return []
    return obj.get("drafts") or []


def validate_drafts(drafts, blocks):
    """Keep only drafts that address an enumerated block, actually change its
    text, and carry no reserved marker bytes."""
    by_ref = {b["source_ref"]: b for b in blocks}
    seen = set()
    out = []
    for d in drafts or []:
        ref = d.get("source_ref")
        new = d.get("new_text")
        blk = by_ref.get(ref)
        if not blk or not isinstance(new, str) or ref in seen:
            continue
        new = new.strip()
        if not new or BID_MARKER in new:
            continue
        if new == (blk.get("original_text") or "").strip():
            continue
        seen.add(ref)
        out.append({"source_ref": ref, "new_text": new, "block": blk})
    return out


def draft_blocks(instruction, blocks, invoke_cli):
    """Chunked drafting over the block set. Returns validated drafts."""
    all_drafts = []
    for i in range(0, len(blocks), CHUNK):
        chunk = blocks[i:i + CHUNK]
        raw = invoke_cli(build_prompt(instruction, chunk))
        all_drafts.extend(validate_drafts(parse_drafts(raw), chunk))
    return all_drafts


def _draft_id(req_id, ref):
    import hashlib
    h = hashlib.sha256(("%s|%s" % (req_id, ref)).encode()).hexdigest()[:24]
    return "scdraft-%s" % h


def submit_drafts(client, req, drafts, group_id):
    """POST each draft via /system-suggest as one ai_rewrite group. The
    instruction rides `comment` as provenance for the review redline."""
    posted = 0
    for d in drafts:
        blk = d["block"]
        payload = {
            "id": _draft_id(req["id"], d["source_ref"]),
            "source_ref": d["source_ref"],
            "new_text": d["new_text"],
            "origin": "ai_rewrite",
            "group_id": group_id,
            "comment": "Scoped change (%s): %s" % (req["level"],
                                                   req["instruction"]),
        }
        if blk.get("kind") == "json_scalar":
            payload["json_path"] = blk.get("json_path")
        res = client.propose_scoped(payload)
        if res.get("ok"):
            posted += 1
    return posted


# --------------------------------------------------------------------------- #
# Orchestration — one pass: progress drafted requests, then draft new ones.
# --------------------------------------------------------------------------- #
def _say(out, msg):
    if out:
        print(msg, file=out)


def _blocks_for_phase(map_bundle, req):
    params = {"level": req["level"], "matter": req.get("matter"),
              "part": req.get("part"), "module": req.get("module")}
    blocks = enumerate_blocks(map_bundle, params)
    if req["level"] in ("module", "course"):
        canary = req.get("canary_matter") or pick_canary(
            module_members(map_bundle, req))
        canary_blocks, remainder = split_canary(blocks, canary)
        if (req.get("phase") or "canary") == "canary":
            return canary_blocks, canary
        return remainder, canary
    return blocks, None


def _progress_drafted(client, map_bundle, invoke_cli, out):
    for req in client.fetch_scoped_requests("drafted"):
        outcome = client.group_status(req["group_id"]).get("outcome") or {}
        total = outcome.get("total") or 0
        by = outcome.get("by_status") or {}
        if total and by.get("declined", 0) > 0:
            client.resolve_scoped(req["id"], status="declined",
                                  note="group %s declined in review" % req["group_id"])
            _say(out, "scoped %s: declined." % req["id"])
            continue
        if not total or by.get("applied", 0) != total:
            continue  # still in review / applying — wait
        if req["level"] in ("module", "course") and req.get("phase") == "canary":
            # canary verified clean -> draft the remainder as a second group
            client.resolve_scoped(req["id"], status="drafting", phase="remainder")
            req = dict(req, phase="remainder")
            blocks, canary = _blocks_for_phase(map_bundle, req)
            drafts = draft_blocks(req["instruction"], blocks, invoke_cli)
            gid = "scoped-%s-remainder" % req["id"]
            if drafts:
                submit_drafts(client, req, drafts, gid)
                client.resolve_scoped(req["id"], status="drafted", phase="remainder",
                                      group_id=gid,
                                      note="remainder: %d draft(s) of %d block(s)"
                                           % (len(drafts), len(blocks)))
                _say(out, "scoped %s: remainder drafted (%d)." % (req["id"], len(drafts)))
            else:
                # canary changed everything there was to change
                client.resolve_scoped(req["id"], status="drafted", phase="remainder",
                                      group_id=gid, note="remainder: nothing to change")
                client.resolve_scoped(req["id"], status="done",
                                      note="canary applied; remainder empty")
        else:
            client.resolve_scoped(req["id"], status="done",
                                  note="group %s fully applied" % req["group_id"])
            _say(out, "scoped %s: done." % req["id"])


def _draft_requested(client, map_bundle, invoke_cli, out):
    for req in client.fetch_scoped_requests("requested"):
        rid = req["id"]
        if not client.claim_scoped(rid).get("ok"):
            continue
        try:
            blocks, canary = _blocks_for_phase(map_bundle, req)
            drafts = draft_blocks(req["instruction"], blocks, invoke_cli)
        except ScopedError as exc:
            client.resolve_scoped(rid, status="failed", note=str(exc))
            _say(out, "scoped %s: FAILED (%s)." % (rid, exc))
            continue
        phase = "canary" if req["level"] in ("module", "course") else "all"
        gid = "scoped-%s-%s" % (rid, phase)
        if not drafts:
            client.resolve_scoped(rid, status="failed",
                                  note="the instruction matched nothing in scope")
            _say(out, "scoped %s: no block matched." % rid)
            continue
        submit_drafts(client, req, drafts, gid)
        client.resolve_scoped(rid, status="drafted", group_id=gid,
                              canary_matter=canary,
                              note="%s: %d draft(s) of %d block(s)"
                                   % (phase, len(drafts), len(blocks)))
        _say(out, "scoped %s: %s drafted (%d of %d blocks)."
             % (rid, phase, len(drafts), len(blocks)))


def run_once(client, map_bundle, invoke_cli, out=sys.stderr):
    """One drafter pass: progress canaries first, then draft new requests."""
    _progress_drafted(client, map_bundle, invoke_cli, out)
    _draft_requested(client, map_bundle, invoke_cli, out)


# --------------------------------------------------------------------------- #
# HTTP client + CLI wiring
# --------------------------------------------------------------------------- #
class ScopedHttpClient(ap.HttpRpcClient):
    def fetch_scoped_requests(self, status):
        res = self._req("GET", "/scoped-requests?status=" + status)
        return res.get("items") or []

    def claim_scoped(self, rid):
        return self._req("POST", "/scoped-claim", {"id": rid})

    def resolve_scoped(self, rid, **patch):
        return self._req("POST", "/scoped-resolve", dict(patch, id=rid))

    def propose_scoped(self, payload):
        return self._req("POST", "/system-suggest", payload)

    def group_status(self, gid):
        return self._req("GET", "/group-status?group_id=" + gid)


def main(argv=None):
    p = argparse.ArgumentParser(prog="editor_scoped_drafts.py",
                                description=__doc__.split("\n")[0])
    p.add_argument("--base-url", default=os.environ.get(ap.ENV_API_BASE))
    p.add_argument("--dry-run", action="store_true",
                   help="enumerate + report; never claim, draft or post")
    args = p.parse_args(argv)

    token = os.environ.get(ap.ENV_SERVICE_TOKEN)
    if not args.base_url or not token:
        print("error: needs --base-url and $%s." % ap.ENV_SERVICE_TOKEN,
              file=sys.stderr)
        return 2
    if not os.path.isfile(MAP_PATH):
        print("error: no %s — run build_site.py first." % MAP_PATH, file=sys.stderr)
        return 2
    with open(MAP_PATH, "r", encoding="utf-8") as fh:
        map_bundle = json.load(fh)

    client = ScopedHttpClient(args.base_url, token)
    if args.dry_run:
        for status in ("requested", "drafting", "drafted"):
            items = client.fetch_scoped_requests(status)
            print("%s: %d" % (status, len(items)))
            for r in items:
                blocks, canary = _blocks_for_phase(map_bundle, r)
                print("  %s level=%s phase=%s blocks=%d canary=%s"
                      % (r["id"], r["level"], r.get("phase"), len(blocks), canary))
        return 0

    run_once(client, map_bundle, run_cli)
    return 0


if __name__ == "__main__":
    sys.exit(main())
