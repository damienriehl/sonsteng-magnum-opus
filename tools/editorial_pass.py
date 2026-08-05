#!/usr/bin/env python3
r"""editorial_pass.py — the Sonsteng post-hoc editorial pass (home-box Opus agent).

WHY THIS EXISTS (plan docs/plans/2026-07-19-001-feat-canonical-direct-apply):
direct-apply removes the pre-approval gate, so quality is guarded POST-HOC. This
pass reviews the window's just-applied editor diffs against voice / consistency /
factual self-contradiction and files each finding as a COMMENT on the affected
block (visible in the editor + the admin review page) plus an ntfy digest ping.
It is detection-lag by design (Damien accepted SL5) and NEVER edits canonical.

TWO TRIGGERS:
  (a) session-end — the apply daemon records the last-applied timestamp + an
      `batch_reviewed` flag in its state file; when >=30 min have elapsed since the
      last apply AND that batch is still unreviewed, this pass runs over the batch
      window (the daemon evaluates the math via direct_apply_daemon.should_run_
      editorial and dispatches this module). Marks the batch reviewed on success.
  (b) daily sweep — a dedicated timer (sonsteng-editorial.timer) fires at 21:30
      America/Chicago and runs `--daily`, reviewing the day's apply commits.

THE PASS:
  1. Collect the window's applied diffs: `git log` for the apply-engine commits
     (author apply@sonsteng.local / subject "apply: batch ...") in range, then
     `git show` their diffs restricted to data/ + site sources.
  2. Invoke the Claude CLI HEADLESS with a strict timeout + graceful degradation:

         claude -p <PROMPT> --model opus --output-format json

     (no secrets in the invocation — the prompt is public canonical diffs only).
     CLI unavailable / timeout -> log + an ntfy note, NEVER a crash (SL7 accepted).
  3. Parse the model's JSON flags {flags:[{source_ref, severity, message}, ...]}.
  4. File each flag as a COMMENT via POST {EDIT_API_BASE}/system-suggest
     (origin=ai_rewrite, comment only, no new_text) — the admin-scoped SYSTEM
     proposer. source_ref is re-validated against the map server-side; an unknown
     ref is rejected per-flag and skipped (best-effort, never aborts the batch).
  5. ntfy digest ping summarising the flag count by severity (content-light).

Python 3, stdlib only. The CLI call, the git reads, the flag filing, and ntfy are
injectable so all logic is unit-testable with NO live model, NO network, NO git
(see tools/tests/test_editorial_pass.py). `--dry-run` files nothing and pings
nothing — it returns the planned flags + payloads.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import digest_push  # noqa: E402  (reuse resolve_topic/publish_ntfy — never modified)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENV_API_BASE = "EDIT_API_BASE"
ENV_SERVICE_TOKEN = "EDIT_SERVICE_TOKEN"
ENV_CLI = "SONSTENG_CLAUDE_BIN"      # override the claude binary (default: "claude")
ENV_CLI_TIMEOUT = "SONSTENG_EDITORIAL_TIMEOUT"  # seconds; default 240

DEFAULT_CLI = "claude"
DEFAULT_TIMEOUT = 240
DEFAULT_MODEL = "opus"

# The apply engine stamps this identity on every apply commit (see
# tools/apply_suggestions.py step 11) — the exact selector for "applied edits".
APPLY_AUTHOR_EMAIL = "apply@sonsteng.local"
APPLY_SUBJECT_PREFIX = "apply: batch "

# Flags are filed through the SYSTEM proposer, which constrains origin to
# {companion, ai_rewrite}. Editorial findings are model-authored review notes -> ai_rewrite.
FLAG_ORIGIN = "ai_rewrite"
VALID_SEVERITIES = ("voice", "consistency", "contradiction", "warn", "note")


def parse_strict_json_object(raw, *, max_bytes=65536, max_depth=8):
    """Parse one bounded JSON object, rejecting trailing text and deep values.

    Promotion review uses this stricter boundary; the legacy editorial parser
    intentionally remains best-effort because its output only creates comments.
    """
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > max_bytes:
        raise ValueError("json_size")
    def reject_constant(value):
        raise ValueError("json_nonfinite:%s" % value)

    value = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")

    def depth(item, level=0):
        if level > max_depth:
            raise ValueError("json_depth")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("json_key")
                depth(child, level + 1)
        elif isinstance(item, list):
            for child in item:
                depth(child, level + 1)

    depth(value)
    return value


# --------------------------------------------------------------------------- #
# 1) Window selection — which apply commits fall in this pass's window (pure)
# --------------------------------------------------------------------------- #
def select_apply_commits(git_log_lines):
    """Given `git log --format=%H%x09%ae%x09%s` output lines, return the SHAs of
    the apply-engine commits (author == apply@sonsteng.local AND subject starts
    'apply: batch '). Pure so the selection is unit-tested without a live repo."""
    shas = []
    for line in (git_log_lines or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        sha, email, subject = parts[0], parts[1], "\t".join(parts[2:])
        if email == APPLY_AUTHOR_EMAIL and subject.startswith(APPLY_SUBJECT_PREFIX):
            shas.append(sha)
    return shas


def git_apply_commits(repo_root, since, batch_id=None, timeout=60):
    """Read apply-engine commits. `since` = a git revision range selector, e.g.
    '--since=1 day ago' (daily) or a base SHA '<sha>..HEAD' (session-end)."""
    args = ["git", "-C", repo_root, "log", "--format=%H%x09%ae%x09%s"]
    args += since if isinstance(since, list) else [since]
    proc = subprocess.run(args, check=False, shell=False,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout)
    shas = select_apply_commits(proc.stdout)
    if batch_id:
        # Session-end: restrict to the one batch's commit if its subject names it.
        want = APPLY_SUBJECT_PREFIX + batch_id
        keep = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and "\t".join(parts[2:]).startswith(want):
                keep.append(parts[0])
        if keep:
            shas = keep
    return shas


def collect_diffs(repo_root, shas, paths=("data/", "site/"), timeout=120):
    """`git show` each commit's diff, restricted to the given path prefixes."""
    diffs = []
    for sha in shas:
        args = ["git", "-C", repo_root, "show", "--no-color", "--format=commit %H%n%s%n",
                sha, "--", *paths]
        proc = subprocess.run(args, check=False, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
        if proc.stdout.strip():
            diffs.append(proc.stdout)
    return diffs


# --------------------------------------------------------------------------- #
# 2) Prompt + CLI (graceful degradation)
# --------------------------------------------------------------------------- #
def build_prompt(diffs, max_chars=60000):
    """Assemble the review prompt: the review contract + the applied diffs. The
    model MUST answer with a strict JSON object; any prose is ignored downstream."""
    body = "\n\n".join(diffs)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n[...diff truncated for length...]"
    return (
        "You are the editorial reviewer for the Sonsteng legal-practicum content "
        "spine. Editors applied the changes in the unified diffs below directly to "
        "canonical (no pre-approval). Review ONLY these applied changes for:\n"
        "  1. VOICE — drift from the established instructional/neutral spine voice.\n"
        "  2. CONSISTENCY — terminology, names, dates, numbers that now disagree "
        "with each other or with adjacent unchanged text.\n"
        "  3. FACTUAL SELF-CONTRADICTION — a claim that contradicts another claim "
        "in the same matter/document.\n"
        "  4. VALIDATOR WARN-level smells (formatting, dangling references).\n"
        "Be conservative: flag only real problems. For each finding, cite the "
        "block's source_ref EXACTLY as it appears in the diff hunk header or file "
        "path (data/...#locator when visible; otherwise the file path).\n\n"
        "Respond with ONLY a JSON object, no prose, of the form:\n"
        '{\"flags\": [{\"source_ref\": \"data/...\", \"severity\": '
        '\"voice|consistency|contradiction|warn|note\", \"message\": \"...\"}]}\n'
        "If there are no problems, respond with {\"flags\": []}.\n\n"
        "=== APPLIED DIFFS ===\n" + body
    )


def run_cli(prompt, *, cli=None, model=DEFAULT_MODEL, timeout=None):
    """Invoke the Claude CLI headless. Returns (ok, raw_stdout, degraded_reason).

    Graceful degradation: a missing binary (FileNotFoundError) or a timeout
    (TimeoutExpired) returns ok=False with a reason and NEVER raises — the caller
    logs + pings a note and exits cleanly (SL7)."""
    cli = cli or os.environ.get(ENV_CLI) or DEFAULT_CLI
    timeout = timeout or int(os.environ.get(ENV_CLI_TIMEOUT, DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    cmd = [cli, "-p", prompt, "--model", model, "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, check=False, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "", "cli_not_found"
    except subprocess.TimeoutExpired:
        return False, "", "cli_timeout"
    if proc.returncode != 0:
        return False, (proc.stdout or "") + (proc.stderr or ""), "cli_error_rc_%d" % proc.returncode
    return True, proc.stdout or "", None


def parse_flags(raw):
    """Extract the flag list from the CLI stdout. Tolerates:
      * the `claude --output-format json` envelope {..., "result": "<text>"};
      * a bare JSON object {"flags": [...]};
      * a JSON object embedded in surrounding prose.
    Returns a list of {source_ref, severity, message} dicts (best-effort; a
    malformed response yields [] — never an exception)."""
    if not raw or not raw.strip():
        return []
    inner = raw
    # Unwrap the CLI json envelope if present.
    try:
        env = json.loads(raw)
        if isinstance(env, dict):
            if isinstance(env.get("flags"), list):
                return _norm_flags(env["flags"])
            if isinstance(env.get("result"), str):
                inner = env["result"]
    except (ValueError, TypeError):
        inner = raw
    # Find the first {...} object in the inner text.
    obj = _first_json_object(inner)
    if not isinstance(obj, dict) or not isinstance(obj.get("flags"), list):
        return []
    return _norm_flags(obj["flags"])


def _first_json_object(text):
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    with contextlib.suppress(ValueError):
                        return json.loads(text[start:i + 1])
                    start = None
    return None


def _norm_flags(flags):
    out = []
    for f in flags:
        if not isinstance(f, dict):
            continue
        ref = f.get("source_ref") or f.get("ref")
        msg = f.get("message") or f.get("comment")
        if not ref or not msg:
            continue
        sev = f.get("severity") or "note"
        if sev not in VALID_SEVERITIES:
            sev = "note"
        out.append({"source_ref": str(ref), "severity": sev, "message": str(msg)})
    return out


# --------------------------------------------------------------------------- #
# 3) Flag filing — POST /system-suggest (comment only, origin=ai_rewrite)
# --------------------------------------------------------------------------- #
def flag_id(source_ref, message, salt=""):
    """Deterministic, idempotent comment id fitting the Worker uuid ceiling
    (`[a-zA-Z0-9_-]{8,64}`). Stable across retries of the same finding so a
    re-run does not double-file the same flag."""
    h = hashlib.sha256(("%s|%s|%s" % (source_ref, message, salt)).encode("utf-8")).hexdigest()[:24]
    return ("edflag-%s" % h)[:64]


def flag_payload(flag, salt=""):
    """The /system-suggest body for one editorial flag: a COMMENT on the block
    (no new_text -> stored as a `comment` kind). origin=ai_rewrite (the SYSTEM
    proposer's sanctioned model-authored provenance)."""
    comment = "[editorial:%s] %s" % (flag["severity"], flag["message"])
    return {
        "id": flag_id(flag["source_ref"], flag["message"], salt),
        "origin": FLAG_ORIGIN,
        "source_ref": flag["source_ref"],
        "comment": comment[:16000],
    }


def file_flag(api_base, token, payload, timeout=30):
    """POST one flag to {api_base}/system-suggest. Returns (ok, reason). Best-effort:
    an unknown source_ref (validation_error) or any HTTP error is returned, not
    raised, so one bad flag never aborts the rest of the batch."""
    url = api_base.rstrip("/") + "/system-suggest"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Edit-Request", "1")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "sonsteng-editorial-pass/1.0")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.status
    except urllib.error.HTTPError as exc:
        return False, "http_%d" % exc.code
    except urllib.error.URLError as exc:
        return False, "unreachable:%s" % (exc.reason,)


# --------------------------------------------------------------------------- #
# 4) ntfy digest ping (content-light: counts by severity only)
# --------------------------------------------------------------------------- #
def notify_digest(flags, *, trigger, filed_ok, degraded_reason=None,
                  topic_resolver=None, publish=None, review_url=None):
    topic_resolver = topic_resolver or digest_push.resolve_topic
    publish = publish or digest_push.publish_ntfy
    review_url = review_url or digest_push.review_url_from_env()
    if degraded_reason:
        title = "Sonsteng editorial pass degraded"
        body = ("The %s editorial pass could not run the reviewer (%s). No flags "
                "were filed this cycle." % (trigger, degraded_reason))
        tags, priority = "warning", "default"
    else:
        by_sev = {}
        for f in flags:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        n = len(flags)
        title = "Sonsteng editorial: %d flag%s" % (n, "" if n == 1 else "s")
        if n == 0:
            body = "The %s editorial pass found no issues in the applied edits." % trigger
        else:
            breakdown = ", ".join("%s %d" % (k, v) for k, v in sorted(by_sev.items()))
            body = ("The %s editorial pass filed %d comment%s (%s) on applied edits. "
                    "Open the review page to see them." % (trigger, filed_ok,
                    "" if filed_ok == 1 else "s", breakdown))
        tags, priority = "mag", "default"
    with contextlib.suppress(Exception):
        publish(topic_resolver(), title, body, review_url, priority=priority, tags=tags)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class EditorialResult:
    trigger: str
    commits: list
    flags: list
    filed: int
    degraded_reason: str
    payloads: list


def run(*, api_base, token, trigger="daily", since="--since=1 day ago",
        batch_id=None, dry_run=False, repo_root=REPO_ROOT,
        commits_reader=None, diff_reader=None, cli_runner=None,
        flag_filer=None, notifier=None, out=None):
    """Execute one editorial pass. Every side effect is injectable for tests."""
    out = out or sys.stdout
    commits_reader = commits_reader or (
        lambda: git_apply_commits(repo_root, since, batch_id=batch_id))
    diff_reader = diff_reader or (lambda shas: collect_diffs(repo_root, shas))
    cli_runner = cli_runner or (lambda prompt: run_cli(prompt))
    flag_filer = flag_filer or (lambda payload: file_flag(api_base, token, payload))
    notifier = notifier or notify_digest

    shas = commits_reader()
    if not shas:
        print("[editorial] no apply commits in window; nothing to review.", file=out)
        if not dry_run:
            notifier([], trigger=trigger, filed_ok=0)
        return EditorialResult(trigger, [], [], 0, "", [])

    diffs = diff_reader(shas)
    if not diffs:
        print("[editorial] apply commits touched no reviewable paths.", file=out)
        if not dry_run:
            notifier([], trigger=trigger, filed_ok=0)
        return EditorialResult(trigger, shas, [], 0, "", [])

    prompt = build_prompt(diffs)
    ok, raw, degraded = cli_runner(prompt)
    if not ok:
        print("[editorial] reviewer degraded (%s) — logged, no flags filed." % degraded,
              file=out)
        if not dry_run:
            notifier([], trigger=trigger, filed_ok=0, degraded_reason=degraded)
        return EditorialResult(trigger, shas, [], 0, degraded or "cli_failed", [])

    flags = parse_flags(raw)
    salt = batch_id or trigger
    payloads = [flag_payload(f, salt=salt) for f in flags]

    if dry_run:
        print("[editorial] DRY-RUN: %d flag(s) parsed; would file, would ping." % len(flags),
              file=out)
        for p in payloads:
            print("  would-file: %s -> %s" % (p["source_ref"], p["comment"][:80]), file=out)
        return EditorialResult(trigger, shas, flags, 0, "", payloads)

    filed = 0
    for p in payloads:
        fok, reason = flag_filer(p)
        if fok:
            filed += 1
        else:
            print("[editorial] flag NOT filed for %s (%s) — skipped." % (p["source_ref"], reason),
                  file=out)
    notifier(flags, trigger=trigger, filed_ok=filed)
    print("[editorial] %s pass: %d flag(s), %d filed." % (trigger, len(flags), filed), file=out)
    return EditorialResult(trigger, shas, flags, filed, "", payloads)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sonsteng post-hoc editorial pass.")
    ap.add_argument("--daily", action="store_true",
                    help="Daily sweep: review the last day's apply commits.")
    ap.add_argument("--since", default=None, help="Git range/selector override (default depends on trigger).")
    ap.add_argument("--batch-id", default=None, help="Session-end: restrict to one batch's commit.")
    ap.add_argument("--dry-run", action="store_true", help="Parse + plan; file nothing, ping nothing.")
    args = ap.parse_args(argv)

    api_base = os.environ.get(ENV_API_BASE)
    token = os.environ.get(ENV_SERVICE_TOKEN)
    trigger = "daily" if args.daily or not args.batch_id else "session-end"
    since = args.since or ("--since=1 day ago" if trigger == "daily" else "--since=2 hours ago")

    result = run(api_base=api_base, token=token, trigger=trigger, since=since,
                 batch_id=args.batch_id, dry_run=args.dry_run)
    return 0 if not result.degraded_reason else 0  # degradation is a clean, non-fatal exit


if __name__ == "__main__":
    raise SystemExit(main())
