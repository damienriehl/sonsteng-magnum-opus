#!/usr/bin/env python3
r"""direct_apply_daemon.py — the Sonsteng home-box apply daemon (direct-apply mode).

WHY THIS EXISTS (plan docs/plans/2026-07-19-001-feat-canonical-direct-apply):
John/Roger edits become **direct-apply**: the Worker auto-accepts suggestions,
and this daemon — fired by a systemd-user timer every 2 min — converges canonical
+ DEV within ~1-2 min with ZERO Damien action. It is a THIN orchestrator around
the existing, battle-tested apply engine (tools/apply_suggestions.py). It does NOT
re-implement any apply logic, validator, parity, or status machine — it only
decides *whether* to run a flush, invokes the engine, publishes the canonical
branch to DEV, and reports a liveness heartbeat.

EACH RUN (under its own daemon flock, cooperating with the engine's apply.lock):
  1. GET {EDIT_API_BASE}/review (admin) -> the full suggestion set.
  2. Filter status == "accepted" (the auto-accept output the worker lane emits;
     works with today's API shape — /review already surfaces `accepted`).
  3. If NONE: post a best-effort heartbeat {ok:true, applied:0, ts} and stop
     (SL3: the 2-min cadence IS the flush — no withholding).
  4. If ANY: invoke tools/apply_suggestions.py (subprocess, APPLY_DEPLOY=1 so the
     engine patches canonical + validates + parity + marks applied/needs_human via
     the /finalize RPC + fast-forward merges into canonical). The engine RECONCILES
     FIRST (crash recovery) before it claims — that is where crash-safety is
     inherited (see docs/direct-apply-daemon.md "Crash-safety").
  5. Rebuild (build_site.py) + deploy DEV (deploy/deploy-dev.sh <branch>, branch
     passed explicitly, default main) — the authoritative publish
     of the just-merged canonical branch. DEV ONLY, never PROD.
  6. POST {EDIT_API_BASE}/heartbeat (admin Bearer) {ok, applied:N, ts}. The
     endpoint is being added by the worker lane; the daemon sends best-effort and
     TOLERATES 404 until that merges.
  On apply-engine FAILURE: heartbeat {ok:false} + an ntfy alert naming the failed
  suggestion IDS ONLY (never content) so a home-box stall is never silent (SL1/SL6).

IDEMPOTENT + CRASH-SAFE: rerunning after a crash mid-sequence never double-applies.
The engine's reconcile-first + DO in_flight-lease + append-only apply_batches
journal + git-worktree isolation own this; the daemon adds a host-local flock so
two timer firings never overlap. See docs/direct-apply-daemon.md.

Python 3, stdlib only. Every side effect (review fetch, engine run, rebuild,
deploy, heartbeat, notify, clock) is injectable so the orchestration is unit-
testable with no network, no subprocess, and no live model (see
tools/tests/test_direct_apply_daemon.py). `--dry-run` plans without mutating.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import digest_push  # noqa: E402  (reuse resolve_topic/publish_ntfy — never modified)
from apply_suggestions import generator_identity  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
LOCK_DIR = os.path.join(REPO_ROOT, ".locks")
DAEMON_LOCK_PATH = os.path.join(LOCK_DIR, "daemon.lock")  # cooperates with apply.lock

# ---- config (env-var names; NEVER hard-code secrets) ----------------------- #
ENV_API_BASE = "EDIT_API_BASE"            # https://<worker>/edit/v1 (no trailing slash)
ENV_SERVICE_TOKEN = "EDIT_SERVICE_TOKEN"  # admin/service bookmark token (opaque). NEVER commit/log.
ENV_DEPLOY_BRANCH = "APPLY_DEPLOY_BRANCH"  # canonical branch to publish; default below
ENV_STATE_FILE = "SONSTENG_APPLY_STATE"   # override the daemon state path
ENV_IDLE_MIN = "APPLY_EDITORIAL_IDLE_MIN"  # session-end idle threshold (min); default 30
ENV_OBSERVER_TOKEN = "SONSTENG_PROD_OBSERVER_BEARER"  # dedicated read-only PROD bearer

DEFAULT_DEPLOY_BRANCH = "main"  # canonical since the 2026-07-24 merge of feat/canonical-docs
SERVICE_USER_AGENT = "sonsteng-apply-daemon/1.0"
DEFAULT_IDLE_MINUTES = 30

# The status the daemon flushes. Auto-accept (worker lane) lands rows here; the
# engine's /claim only ever claims `accepted` rows, so this is the exact trigger.
FLUSH_STATUS = "accepted"


class DaemonError(RuntimeError):
    """A daemon-level fatal (bad config, unreachable review API before any apply)."""


# --------------------------------------------------------------------------- #
# flock — host-local guard so two timer firings never overlap. This is DISTINCT
# from the engine's .locks/apply.lock (which the engine takes internally); the
# two cooperate — the daemon lock serialises daemon runs, the apply lock serialises
# the engine within a run.
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def daemon_lock(lock_path=DAEMON_LOCK_PATH):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise DaemonError(
                "another apply-daemon run holds %s — skipping this tick (%s)"
                % (lock_path, exc))
        fh.write("pid=%d\n" % os.getpid())
        fh.flush()
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# --------------------------------------------------------------------------- #
# State file (session-end editorial windowing + last-applied bookkeeping)
# --------------------------------------------------------------------------- #
def default_state_path():
    override = os.environ.get(ENV_STATE_FILE)
    if override:
        return override
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "sonsteng-apply", "state.json")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts)
    except ValueError:
        return None


def should_run_editorial(state, now, idle_minutes=DEFAULT_IDLE_MINUTES):
    """Session-end trigger (a): TRUE iff there is a batch whose applied edits have
    NOT yet been reviewed by the editorial pass AND at least `idle_minutes` have
    elapsed since that batch applied (the editor session has gone quiet). Pure —
    no I/O — so the windowing math is directly unit-tested."""
    if state.get("batch_reviewed", True):
        return False
    last = _parse_iso(state.get("last_applied_ts"))
    if last is None:
        return False
    # Normalise to aware/naive consistently: compare on UTC if both aware.
    if last.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    if last.tzinfo is None and now.tzinfo is not None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    return (now - last) >= datetime.timedelta(minutes=idle_minutes)


# --------------------------------------------------------------------------- #
# Review fetch — admin GET /review, filter accepted (today's API shape).
# --------------------------------------------------------------------------- #
def accepted_ids(rows):
    """Pure: the IDs of rows the daemon should flush (status == accepted)."""
    return [r["id"] for r in rows
            if r.get("status") == FLUSH_STATUS and r.get("id")]


def fetch_review(api_base, token, timeout=30):
    """GET {api_base}/review (admin) -> list of full suggestion rows.

    Standalone (no apply-engine import) — same wire contract the engine speaks."""
    if not api_base:
        raise DaemonError("EDIT_API_BASE is required (e.g. https://<worker>/edit/v1).")
    url = api_base.rstrip("/") + "/review"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("X-Edit-Request", "1")
    req.add_header("User-Agent", SERVICE_USER_AGENT)  # CF edge bans default UA
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")  # token never echoed in it
        raise DaemonError("GET /review -> HTTP %d: %s" % (exc.code, body))
    except urllib.error.URLError as exc:
        raise DaemonError("GET /review unreachable: %s" % (exc.reason,))
    return payload.get("items") or payload.get("suggestions") or []


# --------------------------------------------------------------------------- #
# Apply-engine invocation — subprocess the EXISTING engine, APPLY_DEPLOY=1 so it
# patches + merges canonical. We NEVER re-implement any apply logic here.
# --------------------------------------------------------------------------- #
def run_apply_engine(batch_id, *, api_base, token, repo_root=REPO_ROOT, timeout=1800):
    """Invoke tools/apply_suggestions.py for `batch_id`. Returns (rc, tail_stdout).

    APPLY_DEPLOY=1 => the engine patches canonical, runs validator + parity, marks
    applied/needs_human/accepted_blocked via /finalize, deploys the worktree to DEV
    as its pre-merge gate, then fast-forward merges into canonical. shell=False."""
    env = dict(os.environ)
    env["APPLY_DEPLOY"] = "1"
    if api_base:
        env["EDIT_API_BASE"] = api_base
    if token:
        env["EDIT_SERVICE_TOKEN"] = token
    cmd = [sys.executable, os.path.join(repo_root, "tools", "apply_suggestions.py"),
           "--batch-id", batch_id, "--base-url", api_base or ""]
    proc = subprocess.run(
        cmd, cwd=repo_root, check=False, shell=False, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "")[-4000:]


def rebuild(repo_root=REPO_ROOT, timeout=900):
    """Regenerate the site from the just-merged canonical source. Returns (ok, tail)."""
    proc = subprocess.run(
        [sys.executable, os.path.join(repo_root, "tools", "build_site.py")],
        cwd=repo_root, check=False, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return proc.returncode == 0, (proc.stdout or "")[-2000:]


def deploy_dev(branch, repo_root=REPO_ROOT, timeout=900):
    """Publish the canonical `branch` to the Hetzner DEV box. Branch passed
    EXPLICITLY. DEV ONLY — this script never targets PROD. Returns (ok, tail)."""
    proc = subprocess.run(
        ["bash", os.path.join(repo_root, "deploy", "deploy-dev.sh"), branch],
        cwd=repo_root, check=False, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return proc.returncode == 0, (proc.stdout or "")[-2000:]


def deploy_worker(repo_root=REPO_ROOT, timeout=900):
    """Redeploy the Worker (wrangler) so its BUNDLED editor map matches the
    checkout. The APPLY path already gets this via the apply engine's own
    deploy; the REVERT path must do it explicitly — a revert changes the map
    (a restored block re-enters the allowlist), and a stale worker bundle
    rejects edits against restored blocks (caught live 2026-07-28: an undone
    delete's paragraph answered "That block is not editable"). Returns
    (ok, tail)."""
    proc = subprocess.run(
        ["npx", "--yes", "wrangler@latest", "deploy"],
        cwd=os.path.join(repo_root, "app", "worker"), check=False, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return proc.returncode == 0, (proc.stdout or "")[-2000:]


# --------------------------------------------------------------------------- #
# History bundle regeneration — wired into the rebuild step so the redline
# History browser tracks every applied revision. Regenerates build/history-
# bundle.generated.json (build_history.py) and refreshes the Worker import tree
# (bundle-editor-data.mjs). NON-GATING: a history failure never blocks the site
# publish (history is a safety-net view, not the live site). The served History
# browser refreshes on the next Worker deploy; the bundle is always kept fresh.
# --------------------------------------------------------------------------- #
def regen_history(repo_root=REPO_ROOT, timeout=600):
    """Rebuild the history bundle + copy it into the Worker import tree. Returns
    (ok, tail). Best-effort — the caller logs but does not gate on it."""
    hp = subprocess.run(
        [sys.executable, os.path.join(repo_root, "tools", "build_history.py")],
        cwd=repo_root, check=False, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    tail = (hp.stdout or "")[-1500:]
    if hp.returncode != 0:
        return False, tail
    # Refresh the Worker import tree (client bundle regeneration flow) so the next
    # worker deploy inlines the fresh history bundle. Missing node is tolerated.
    bp = subprocess.run(
        ["node", os.path.join(repo_root, "app", "worker", "scripts", "bundle-editor-data.mjs")],
        cwd=repo_root, check=False, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return bp.returncode == 0, tail + (bp.stdout or "")[-500:]


# --------------------------------------------------------------------------- #
# Revert requests (History browser "Request revert", SL8). The daemon polls
# approved requests, git-reverts the run range on the canonical branch under the
# ENGINE'S apply flock (cooperating, never fighting the apply engine), rebuilds +
# republishes, then marks the request done/failed. The revert is a clean-tree,
# conflict-aware inverse commit: `git revert --no-commit <first>^..<last>` stages
# the inverse; build_site + build_history regenerate the derived output; ONE
# attributed commit lands. A git conflict (overlap since the run) ABORTS — never
# a partial revert (the fence build's perform_revert invariant).
# --------------------------------------------------------------------------- #
APPLY_LOCK_PATH = os.path.join(LOCK_DIR, "apply.lock")


@contextlib.contextmanager
def apply_lock(lock_path=APPLY_LOCK_PATH):
    """Cooperate with the apply engine's flock so a daemon revert never races an
    in-flight apply. Blocking acquire (the daemon already serialises ticks)."""
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _git(args, repo_root=REPO_ROOT, timeout=300):
    proc = subprocess.run(["git", *args], cwd=repo_root, check=False, shell=False,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "")


def fetch_revert_requests(api_base, token, timeout=30):
    """GET {api_base}/revert-requests?status=approved (admin) -> list of rows.
    Best-effort: a missing endpoint or unreachable worker degrades to [] (a revert
    is never the reason a tick fails)."""
    if not api_base:
        return []
    url = api_base.rstrip("/") + "/revert-requests?status=approved"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("X-Edit-Request", "1")
    req.add_header("User-Agent", SERVICE_USER_AGENT)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        return []
    return payload.get("items") or []


def resolve_revert_request(api_base, token, request_id, status, note=None, timeout=15):
    """POST {api_base}/revert-resolve (admin) { id, status, note? }. Best-effort."""
    if not api_base:
        return {"sent": False, "reason": "no_api_base"}
    url = api_base.rstrip("/") + "/revert-resolve"
    payload = {"id": request_id, "status": status}
    if note:
        payload["note"] = note
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Edit-Request", "1")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", SERVICE_USER_AGENT)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"sent": True, "status": resp.status}
    except urllib.error.HTTPError as exc:
        return {"sent": False, "reason": "http_%d" % exc.code}
    except urllib.error.URLError as exc:
        return {"sent": False, "reason": "unreachable:%s" % (exc.reason,)}


def record_revert_mutation(api_base, token, evidence, action="record", timeout=30):
    if not api_base:
        return {"sent": False, "reason": "no_api_base"}
    url = api_base.rstrip("/") + "/revert-record"
    req = urllib.request.Request(url, data=json.dumps({**evidence,"action":action}).encode("utf-8"), method="POST")
    for key, value in (("Content-Type", "application/json"), ("X-Edit-Request", "1"),
                       ("User-Agent", SERVICE_USER_AGENT),
                       ("Authorization", "Bearer " + token if token else "")):
        if value:
            req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return {"sent": payload.get("ok") is True, **payload}
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        return {"sent": False, "reason": type(exc).__name__}


def restore_regenerable_site(repo_root=REPO_ROOT):
    """Restore tracked site/ output to HEAD. Returns True on success.

    WHY (2026-07-24): `build_site.py` stamps the CURRENT HEAD into
    `site/platform/data/.build-stamp.json` (`git_base_sha`, traceability only —
    deliberately NOT part of the parity hash). The tick's post-apply rebuild
    therefore runs at a commit newer than the one the engine stamped, and leaves
    that one tracked file dirty. The apply engine's `assert_clean_tree` is strict,
    so WITHOUT this the tick after any successful apply would fail with
    "canonical tree is dirty" — auto-apply would stall on the SECOND edit of a
    session (exactly the walkthrough failure mode). `execute_revert` already
    carried this tolerance; the apply path now does too.

    Safe by construction: site/ is generated output, fully reproducible from
    data/ (the rebuild below rewrites it), and DEV is published with
    `git archive <branch>` — the COMMITTED tree, never the working copy. Source
    dirtiness (data/, app/, tools/) is untouched and still stops the engine.
    """
    rc, _ = _git(["checkout", "--", "site"], repo_root)
    return rc == 0


def execute_revert(req, *, repo_root=REPO_ROOT, do_rebuild=None, do_history=None,
                   generator=None):
    """Execute ONE approved revert request on the canonical branch (clean-tree,
    conflict-aware). Returns (ok, detail). Holds the apply flock for the whole
    inverse-commit so it never races an in-flight apply. On a git conflict it
    aborts cleanly (never a partial revert) and returns (False, reason)."""
    do_rebuild = do_rebuild or (lambda: rebuild(repo_root))
    do_history = do_history or (lambda: regen_history(repo_root))
    generator = generator or generator_identity
    doc = (req or {}).get("doc") or ""
    first = (req or {}).get("run_first") or ""
    last = (req or {}).get("run_last") or ""
    if not first or not last:
        return False, "bad_run_range"

    with apply_lock():
        # Clear benign GENERATED-output churn first: the daemon's post-apply
        # rebuild rewrites site/.build-stamp.json (git_base_sha = current HEAD),
        # so site/ is routinely dirty vs the last commit. That output is fully
        # regenerable (rebuild overwrites it below), never precious — restore it
        # to HEAD so it doesn't block the revert. Best-effort (site/ may not exist
        # in a bare test repo). SOURCE dirtiness is still caught by the check next.
        _git(["checkout", "--", "site"], repo_root)
        # Refuse on a dirty working tree in SOURCE (excludes untracked/gitignored
        # build/). A real uncommitted edit to data/app/tools -> never revert over it.
        rc, out = _git(["status", "--porcelain", "--untracked-files=no"], repo_root)
        if rc != 0:
            return False, "git_status_failed"
        if out.strip():
            return False, "tree_not_clean"
        request_id = (req or {}).get("id") or ""
        rc, prior_commit = _git(["log", "-1", "--format=%H",
            "--grep=^Revert-Request: %s$" % request_id], repo_root)
        if rc == 0 and prior_commit.strip():
            prior_commit = prior_commit.strip()
            _, head = _git(["rev-parse", "HEAD"], repo_root)
            if prior_commit != head.strip():
                return False, "revert_retry_ambiguous"
            before_rc, before_text = _git(["show", prior_commit + "^:" + doc], repo_root)
            after_rc, after_text = _git(["show", prior_commit + ":" + doc], repo_root)
            if before_rc != 0 or after_rc != 0:
                return False, "revert_evidence_unavailable"
            return True, {"id":request_id,"batch_id":"revert-" + request_id,
                "actor":req.get("editor") or "slot:unknown","source_ref":doc,
                "original_text":before_text,"new_text":after_text,
                "original_hash":hashlib.sha256(before_text.encode()).hexdigest(),
                "new_hash":hashlib.sha256(after_text.encode()).hexdigest(),
                "base_sha":_git(["rev-parse", prior_commit + "^"], repo_root)[1].strip(),
                "commit_sha":prior_commit,"generator_id":generator(repo_root)}
        rc, base_sha = _git(["rev-parse", "HEAD"], repo_root)
        before_rc, before_text = _git(["show", "HEAD:" + doc], repo_root)
        if rc != 0 or before_rc != 0:
            return False, "revert_evidence_unavailable"

        # Stage the inverse of the whole run range (first..last inclusive).
        rc, rout = _git(["revert", "--no-commit", "%s^..%s" % (first, last)], repo_root)
        if rc != 0:
            # Overlap/conflict since the run -> abort, never partial.
            _git(["revert", "--abort"], repo_root)
            _git(["reset", "--hard", "HEAD"], repo_root)
            _git(["checkout", "--", "."], repo_root)
            return False, "revert_conflict"

        # Regenerate the SITE from the reverted sources so the single commit is
        # self-consistent (site/ is tracked and must ride the commit).
        rebuilt, _ = do_rebuild()
        if not rebuilt:
            _git(["revert", "--abort"], repo_root)
            _git(["reset", "--hard", "HEAD"], repo_root)
            return False, "rebuild_failed"

        short = "%s..%s" % (first[:8], last[:8])
        msg = ("revert(history): %s run %s\n\n"
               "Admin-executed revert requested via the History browser.\n"
               "Editor: %s\n"
               "Revert-Request: %s\n" % (doc or "(doc)", short,
                                            req.get("editor") or "unknown", request_id))
        _git(["add", "-A"], repo_root)
        after_rc, after_text = _git(["show", ":" + doc], repo_root)
        if (after_rc != 0 or before_text == after_text or
                len(before_text.encode("utf-8")) > 131072 or
                len(after_text.encode("utf-8")) > 131072):
            _git(["reset", "--hard", "HEAD"], repo_root)
            return False, "revert_evidence_unavailable"
        rc, cout = _git(["commit", "-m", msg], repo_root)
        if rc != 0:
            _git(["reset", "--hard", "HEAD"], repo_root)
            return False, "commit_failed"
        # Regenerate history AFTER the revert commit lands so git log includes the
        # revert revision (build/ + worker import tree; both gitignored — never
        # dirties the tree). Non-gating.
        do_history()
        rc, sha = _git(["rev-parse", "HEAD"], repo_root)
        commit_sha = sha.strip()
        return True, {"id":req.get("id"),"batch_id":"revert-" + req.get("id", ""),
            "actor":req.get("editor") or "slot:unknown","source_ref":doc,
            "original_text":before_text,"new_text":after_text,"base_sha":base_sha.strip(),
            "original_hash":hashlib.sha256(before_text.encode()).hexdigest(),
            "new_hash":hashlib.sha256(after_text.encode()).hexdigest(),
            "commit_sha":commit_sha,"generator_id":generator(repo_root)}


# --------------------------------------------------------------------------- #
# Heartbeat — best-effort; TOLERATE 404 until the worker lane adds the endpoint.
# --------------------------------------------------------------------------- #
def post_heartbeat(api_base, token, *, ok, applied, ts, timeout=15):
    """POST {api_base}/heartbeat (admin Bearer) {ok, applied, ts}. Returns a small
    dict describing the outcome; NEVER raises for the daemon's benefit — a missing
    endpoint (404) or an unreachable worker degrades to a logged best-effort miss
    so the apply itself is never gated on the heartbeat landing."""
    if not api_base:
        return {"sent": False, "reason": "no_api_base"}
    url = api_base.rstrip("/") + "/heartbeat"
    body = json.dumps({"ok": bool(ok), "applied": int(applied), "ts": ts}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Edit-Request", "1")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", SERVICE_USER_AGENT)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"sent": True, "status": resp.status}
    except urllib.error.HTTPError as exc:
        # 404 = endpoint not merged yet (expected until the worker lane ships it).
        return {"sent": False, "reason": "http_%d" % exc.code,
                "tolerated": exc.code == 404}
    except urllib.error.URLError as exc:
        return {"sent": False, "reason": "unreachable:%s" % (exc.reason,)}


def notify_failure(failed_ids, *, topic_resolver=None, publish=None):
    """ntfy alert on apply-engine failure. Names the failed suggestion IDS ONLY —
    never any suggestion content (privacy invariant, parity with the digest push).
    Best-effort: a notify failure never re-raises into the daemon."""
    topic_resolver = topic_resolver or digest_push.resolve_topic
    publish = publish or digest_push.publish_ntfy
    n = len(failed_ids)
    title = "Sonsteng apply DAEMON failed"
    id_line = ", ".join(failed_ids[:20]) + ("" if n <= 20 else ", +%d more" % (n - 20))
    body = ("The home-box apply daemon could not flush %d accepted edit%s.\n"
            "Failed suggestion ids: %s\n"
            "Changes are NOT live. Check the home box / apply log."
            % (n, "" if n == 1 else "s", id_line or "(none)"))
    with contextlib.suppress(Exception):
        topic = topic_resolver()
        publish(topic, title, body, None, priority="high", tags="rotating_light")


def notify_consistency(status, batch_id, *, topic_resolver=None, publish=None):
    """Best-effort, text-free U18 alert. Clean results need no interruption."""
    if status == "clean":
        return
    topic_resolver = topic_resolver or digest_push.resolve_topic
    publish = publish or digest_push.publish_ntfy
    title = "Sonsteng consistency check: %s" % status
    body = ("Post-apply legacy U18 result for batch %s: %s. "
            "The DEV apply remains complete; this result cannot authorize publication."
            % (batch_id, status))
    with contextlib.suppress(Exception):
        publish(topic_resolver(), title, body, None, priority="high",
                tags="mag,warning")


def completed_production_frontier(observer):
    """Read the exact completed PROD baseline through observer-only authority."""
    context = observer.preparation_context()
    candidate = context.get("base_sha")
    if candidate is None and isinstance(context.get("active_release"), dict):
        candidate = context["active_release"].get("base_sha")
    if not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{40}", candidate):
        return None
    return candidate


def run_consistency_from(frontier_sha, *, api_base, token, repo_root=REPO_ROOT):
    """Invoke the existing checker; its service bearer may file DEV flags only."""
    import editor_consistency
    result = editor_consistency.run(api_base=api_base, token=token,
                                    since=frontier_sha, repo_root=repo_root)
    return editor_consistency.daemon_summary(result)


@dataclasses.dataclass(frozen=True)
class LegacyU18Hooks:
    fetch_frontier: Callable[[], object]
    check: Callable[[str], object]
    notify: Callable[[str, str], object] = notify_consistency


def _legacy_u18_result(hooks):
    """Collect one bounded U18 result; injected failures never escape."""
    summary = {"status":"checker-error","stale_count":0,
               "model_count":0,"filed":0}
    frontier_sha = None
    try:
        frontier_sha = hooks.fetch_frontier()
        if frontier_sha is None:
            summary["status"] = "missing-baseline"
        elif not isinstance(frontier_sha,str) or not re.fullmatch(r"[0-9a-f]{40}",frontier_sha):
            frontier_sha = None
            summary["status"] = "bad-revision"
        else:
            candidate = hooks.check(frontier_sha)
            if not isinstance(candidate, dict) or candidate.get("status") not in {
                    "clean","flagged","bad-revision"}:
                raise ValueError("unbounded consistency result")
            numbers = [candidate.get(key,0) for key in
                       ("stale_count","model_count","filed")]
            if any(not isinstance(value,int) or isinstance(value,bool) or
                   value < 0 or value > 1_000_000 for value in numbers):
                raise ValueError("unbounded consistency counts")
            summary = {"status":candidate["status"],
                "stale_count":numbers[0],"model_count":numbers[1],"filed":numbers[2]}
    except Exception:
        summary = {"status":"checker-error","stale_count":0,
                   "model_count":0,"filed":0}
    return frontier_sha, summary


# --------------------------------------------------------------------------- #
# Orchestration (thin; every side effect injected -> fully unit-testable)
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class DaemonResult:
    applied: int
    batch_id: str
    reason: str
    heartbeat: dict
    editorial_due: bool
    steps: list  # ordered record of side-effect steps (for tests + logging)


def _new_batch_id(now):
    return "batch-" + now.strftime("%Y%m%dT%H%M%SZ")


def dispatch_scoped_drafts(*, repo_root=REPO_ROOT, timeout=900):
    """U7: run one scoped-drafts pass (tools/editor_scoped_drafts.py) — claim
    requested scoped changes, draft them via the headless CLI, progress
    canaries. Best-effort and NON-GATING: a drafting failure never blocks the
    apply/flush flow. Returns (ok, tail)."""
    cmd = [sys.executable, os.path.join(repo_root, "tools", "editor_scoped_drafts.py")]
    try:
        proc = subprocess.run(cmd, cwd=repo_root, check=False, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout or "")[-2000:]


def dispatch_editorial(batch_id, *, repo_root=REPO_ROOT, timeout=600):
    """Session-end trigger (a): fire the editorial pass over one batch. subprocess
    so a slow/hung reviewer can never block the 2-min apply cadence. Returns rc."""
    cmd = [sys.executable, os.path.join(repo_root, "tools", "editorial_pass.py"),
           "--batch-id", batch_id]
    proc = subprocess.run(cmd, cwd=repo_root, check=False, shell=False,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=timeout)
    return proc.returncode


def run(*, api_base, token, branch=DEFAULT_DEPLOY_BRANCH, dry_run=False,
        state_path=None, idle_minutes=DEFAULT_IDLE_MINUTES, now=None,
        fetch=None, apply_engine=None, do_rebuild=None, do_deploy=None,
        heartbeat=None, notify=None, editorial=None, out=None,
        do_history=None, fetch_reverts=None, revert_exec=None, revert_resolve=None,
        revert_record=None,
        clean_site=None, do_deploy_worker=None, do_scoped=None,
        legacy_u18=None):
    """Execute one daemon tick. Returns DaemonResult. All I/O is injectable; the
    production wiring is supplied by main().

    do_history / fetch_reverts / revert_exec / revert_resolve / clean_site are
    OPT-IN: when None the corresponding behavior is skipped (keeps the pure
    orchestration tests hermetic). main() wires all five for production."""
    out = out or sys.stdout
    state_path = state_path or default_state_path()
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    ts = now.isoformat()

    fetch = fetch or (lambda: fetch_review(api_base, token))
    apply_engine = apply_engine or (
        lambda bid: run_apply_engine(bid, api_base=api_base, token=token))
    do_rebuild = do_rebuild or (lambda: rebuild())
    do_deploy = do_deploy or (lambda b: deploy_dev(b))
    do_deploy_worker = do_deploy_worker or (lambda: deploy_worker())
    heartbeat = heartbeat or (
        lambda ok, applied: post_heartbeat(api_base, token, ok=ok, applied=applied, ts=ts))
    notify = notify or notify_failure
    editorial = editorial or (lambda bid: dispatch_editorial(bid))

    steps = []
    state = load_state(state_path)

    # ---- approved revert requests (History browser, SL8) — execute FIRST -----
    # A quiet or busy tick both drain approved reverts. Each revert is an
    # independent clean-tree inverse commit + republish; a failure NEVER blocks the
    # accepted-suggestion flush below. Skipped entirely when unwired (tests).
    if fetch_reverts is not None and not dry_run:
        try:
            reqs = fetch_reverts()
        except Exception:  # best-effort: a revert fetch never fails the tick
            reqs = []
        for req in reqs or []:
            rid = req.get("id")
            if req.get("mutation_phase") == "merged":
                detail = {"id":rid,"batch_id":req["batch_id"],
                    "actor":req["mutation_actor"],"source_ref":req["source_ref"],
                    "original_text":req["original_text"],"new_text":req["new_text"],
                    "original_hash":req["original_hash"],"new_hash":req["new_hash"],
                    "base_sha":req["base_sha"],"commit_sha":req["commit_sha"],
                    "generator_id":req["generator_id"]}
                ok = True
                steps.append(("revert_resume", {"id":rid,"commit_sha":detail["commit_sha"]}))
            else:
                ok, detail = revert_exec(req)
            steps.append(("revert", {"id": rid, "ok": ok, "detail": detail}))
            if ok:
                recorded = revert_record(detail, "record") if revert_record else {"sent":False}
                if recorded.get("sent") is not True:
                    heartbeat(False, 0)
                    notify([rid])
                    return DaemonResult(0, "", "revert_record_failed", {}, False, steps)
                # REBUILD FIRST: the revert commit restores the TRACKED trees
                # (data/, site/) but build/ artifacts — the editor map above
                # all — are generated and still describe the pre-revert corpus.
                # Deploying without a rebuild ships a worker whose allowlist
                # rejects every restored block (caught live 2026-07-28).
                rok, _ = do_rebuild()
                dok, _ = do_deploy(branch)
                # The worker must follow: its bundled map now differs (restored
                # blocks re-entered the allowlist). Rebuild + site + worker
                # together are the publish; any failing marks the revert failed.
                wok, _ = do_deploy_worker()
                dok = rok and dok and wok
                completed = revert_record(detail, "complete") if dok and revert_record else {"sent":False}
                dok = dok and completed.get("sent") is True
                heartbeat(dok, 0)
                if not dok:
                    notify([rid])
                    # Keep the request approved and the batch at `merged`: the
                    # next tick resumes deployment from its exact journaled
                    # evidence and must never inverse the same git range twice.
                    return DaemonResult(0, detail.get("batch_id", ""),
                        "revert_deploy_failed", {}, False, steps)
            else:
                if revert_resolve:
                    revert_resolve(rid, "failed")
                heartbeat(False, 0)
                notify([rid])
                print("[daemon] revert %s FAILED (%s)." % (rid, detail), file=out)

    # U7: scoped-change drafting — opt-in, best-effort, never gates the flush.
    if do_scoped is not None and not dry_run:
        try:
            sok, _stail = do_scoped()
            steps.append(("scoped_drafts", sok))
        except Exception:
            steps.append(("scoped_drafts", False))

    rows = fetch()
    steps.append(("fetch_review", len(rows)))
    accepted = accepted_ids(rows)

    if not accepted:
        hb = heartbeat(True, 0)
        steps.append(("heartbeat", {"ok": True, "applied": 0}))
        # Session-end trigger (a): a quiet tick past the idle window over an
        # unreviewed batch fires the editorial pass, then marks the batch reviewed.
        editorial_due = should_run_editorial(state, now, idle_minutes)
        if editorial_due and not dry_run:
            with contextlib.suppress(Exception):
                editorial(state.get("last_batch_id") or "")
            steps.append(("editorial", state.get("last_batch_id")))
            state["batch_reviewed"] = True
            state["last_editorial_ts"] = ts
        state["last_run_ts"] = ts
        if not dry_run:
            save_state(state_path, state)
        print("[daemon] no accepted suggestions; no-op. editorial_due=%s"
              % editorial_due, file=out)
        return DaemonResult(0, "", "no_accepted", hb, editorial_due, steps)

    batch_id = _new_batch_id(now)
    print("[daemon] flushing %d accepted suggestion(s) as %s"
          % (len(accepted), batch_id), file=out)
    if dry_run:
        steps.append(("would_apply", batch_id))
        print("[daemon] DRY-RUN: would run apply engine, rebuild, deploy %s, heartbeat"
              % branch, file=out)
        return DaemonResult(len(accepted), batch_id, "dry_run", {}, False, steps)

    # 0) Clear benign GENERATED-output churn (site/.build-stamp.json carries the
    #    HEAD sha, so the last tick's rebuild left it dirty). The engine's
    #    assert_clean_tree is strict and would otherwise refuse every apply after
    #    the first. Source dirtiness is deliberately NOT cleared.
    if clean_site is not None:
        steps.append(("clean_site", bool(clean_site())))

    # 1) EXISTING apply engine — patch + validate + parity + finalize + merge.
    rc, tail = apply_engine(batch_id)
    steps.append(("apply_engine", rc))
    if rc != 0:
        hb = heartbeat(False, 0)
        steps.append(("heartbeat", {"ok": False, "applied": 0}))
        notify(accepted)  # IDs only, never content
        steps.append(("notify_failure", accepted))
        print("[daemon] apply engine FAILED (rc=%d). alerted. tail:\n%s"
              % (rc, tail), file=out)
        state["last_run_ts"] = ts
        save_state(state_path, state)
        return DaemonResult(0, batch_id, "apply_failed", hb, False, steps)

    # 2) Authoritative rebuild + DEV publish of the merged canonical branch.
    ok, rtail = do_rebuild()
    steps.append(("rebuild", ok))
    if not ok:
        hb = heartbeat(False, 0)
        steps.append(("heartbeat", {"ok": False, "applied": 0}))
        notify(accepted)
        steps.append(("notify_failure", accepted))
        print("[daemon] rebuild FAILED after apply. alerted. tail:\n%s" % rtail, file=out)
        state["last_run_ts"] = ts
        save_state(state_path, state)
        return DaemonResult(0, batch_id, "rebuild_failed", hb, False, steps)

    # Regenerate the redline History bundle BEFORE deploy (non-gating — a history
    # failure never blocks the authoritative site publish). Opt-in (tests skip it).
    if do_history is not None:
        try:
            hok, _ = do_history()
            steps.append(("history", hok))
        except Exception:
            steps.append(("history", False))

    ok, dtail = do_deploy(branch)
    steps.append(("deploy", ok))
    if not ok:
        hb = heartbeat(False, 0)
        steps.append(("heartbeat", {"ok": False, "applied": 0}))
        notify(accepted)
        steps.append(("notify_failure", accepted))
        print("[daemon] DEV deploy FAILED after apply. alerted. tail:\n%s" % dtail, file=out)
        state["last_run_ts"] = ts
        save_state(state_path, state)
        return DaemonResult(0, batch_id, "deploy_failed", hb, False, steps)

    # 2b) Leave the canonical tree CLEAN: the rebuild above re-stamped
    #     site/.build-stamp.json with the merged HEAD. DEV already has the
    #     committed tree (git archive), so this only tidies the working copy —
    #     but it is what keeps the NEXT tick's apply from refusing to run.
    if clean_site is not None:
        steps.append(("clean_site", bool(clean_site())))

    # 3) Heartbeat ok:true applied:N (best-effort; 404 tolerated).
    hb = heartbeat(True, len(accepted))
    steps.append(("heartbeat", {"ok": True, "applied": len(accepted)}))

    # 4) Record the batch for the session-end editorial window (unreviewed).
    state["last_run_ts"] = ts
    state["last_applied_ts"] = ts
    state["last_batch_id"] = batch_id
    state["last_batch_size"] = len(accepted)
    state["batch_reviewed"] = False

    # Legacy U18 runs only after a real, accepted batch has completed canonical
    # merge + DEV publication. It observes the exact last completed PROD SHA via
    # a separate read-only bearer; its result is evidence, never publication
    # authority, and every failure is nonfatal to the already-completed apply.
    if legacy_u18 is not None:
        frontier_sha, summary = _legacy_u18_result(legacy_u18)
        state["legacy_u18"] = {"batch_id":batch_id,"status":summary["status"],
            "frontier_sha":frontier_sha,"stale_count":summary["stale_count"],
            "model_count":summary["model_count"],"filed":summary["filed"],"at":ts}
        steps.append(("legacy_u18", dict(state["legacy_u18"])))
        with contextlib.suppress(Exception):
            legacy_u18.notify(summary["status"], batch_id)
    save_state(state_path, state)

    print("[daemon] applied %d, rebuilt, deployed %s, heartbeat sent."
          % (len(accepted), branch), file=out)
    return DaemonResult(len(accepted), batch_id, "applied", hb, False, steps)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Sonsteng home-box apply daemon (direct-apply mode).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan only: fetch + count + report; no apply/rebuild/deploy/state write.")
    ap.add_argument("--branch", default=os.environ.get(ENV_DEPLOY_BRANCH, DEFAULT_DEPLOY_BRANCH),
                    help="Canonical branch to publish to DEV (default main).")
    ap.add_argument("--state-file", default=None, help="Override the daemon state path.")
    ap.add_argument("--no-lock", action="store_true", help="(tests only) skip the daemon flock.")
    args = ap.parse_args(argv)

    api_base = os.environ.get(ENV_API_BASE)
    token = os.environ.get(ENV_SERVICE_TOKEN)
    observer_token = os.environ.get(ENV_OBSERVER_TOKEN)
    idle = int(os.environ.get(ENV_IDLE_MIN, DEFAULT_IDLE_MINUTES) or DEFAULT_IDLE_MINUTES)

    lock_cm = contextlib.nullcontext() if args.no_lock else daemon_lock()
    try:
        with lock_cm:
            if observer_token and token and observer_token == token:
                raise DaemonError("release observer token must be distinct from the DEV apply bearer")
            observer = None
            if observer_token and api_base:
                from prod_release_executor import ReleaseObserverHTTP
                observer = ReleaseObserverHTTP(api_base.rsplit("/edit/v1",1)[0],observer_token)
            result = run(api_base=api_base, token=token, branch=args.branch,
                         dry_run=args.dry_run, state_path=args.state_file,
                         idle_minutes=idle,
                         # History bundle regen in the rebuild step + approved-
                         # revert execution (both DEV-only, never PROD).
                         do_history=lambda: regen_history(),
                         clean_site=lambda: restore_regenerable_site(),
                         fetch_reverts=lambda: fetch_revert_requests(api_base, token),
                         revert_exec=lambda req: execute_revert(req),
                         revert_resolve=lambda rid, st: resolve_revert_request(
                             api_base, token, rid, st),
                         revert_record=lambda evidence, action: record_revert_mutation(
                             api_base, token, evidence, action),
                         do_scoped=lambda: dispatch_scoped_drafts(),
                         legacy_u18=LegacyU18Hooks(
                             fetch_frontier=(lambda: completed_production_frontier(observer))
                                if observer else (lambda: None),
                             check=lambda sha: run_consistency_from(
                                 sha,api_base=api_base,token=token)))
    except DaemonError as exc:
        print("[daemon] ERROR: %s" % exc, file=sys.stderr)
        return 2

    # Non-zero only on a genuine apply/rebuild/deploy failure (so systemd marks the
    # unit failed and the alert already fired). No-op + applied are both success.
    return 0 if result.reason in ("no_accepted", "applied", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
