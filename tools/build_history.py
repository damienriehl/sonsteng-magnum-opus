#!/usr/bin/env python3
r"""build_history.py — pre-render the git-backed redline History browser data.

The History browser gives John/Roger/Damien a durable, attributed, redlined
change history for every canonical document, plus named baselines and one-click
(admin-executed) revert. Under the direct-apply plan
(docs/plans/2026-07-19-001-feat-canonical-direct-apply-plan.md) this REPLACES the
pre-approval gate: editors ship freely; history + revert are the safety net.

WHY PRE-RENDER (and why editor-gated):
  fence-litigation serves history live from a Python service. sonsteng has NO
  such service — the /edit surface is a Cloudflare Worker that serves inlined,
  build-time bundles. So we pre-render the whole history (coalesced revisions +
  every bounded redline) into ONE JSON bundle the Worker inlines exactly like it
  already inlines editor-map / instructor bundles (bundle-editor-data.mjs).

  CRITICAL LEAK CONSTRAINT: history redlines of canonical sources expose
  INSTRUCTOR-ONLY material (facts.md, answer keys, concealed persona facts) that
  the public-site leak-sweep (build_site.check_no_instructor_leaks) keeps out of
  site/platform/. Therefore history output NEVER lands in site/platform/ — it is
  written only under build/ (a gitignored throwaway) and served EXCLUSIVELY
  through the authenticated /edit proxy (edit/instructor scope). Every history
  artifact carries the sentinel below; the leak assertion proves that sentinel
  appears nowhere under site/platform/.

OUTPUTS (all under build/, gitignored — never under site/):
  build/history-bundle.generated.json   the servable bundle (Worker inlines this)
  build/history/index.json               doc list + generation metadata
  build/history/<slug>.json              per-doc history (debug/inspection)
  build/history/<slug>.preview.html      self-contained preview (QA only; see note)

SERVING (contract for the worker lane — see docs/notes/history-browser.md):
  bundle-editor-data.mjs copies build/history-bundle.generated.json into
  app/worker/editor-data/ ; a Worker route GET /edit/history/<doc> renders a shell
  with a <script id="history-data" type="application/json"> island (the doc's
  slice of the bundle) + <script src="/edit/assets/history.js" defer> — identical
  in shape to the existing review page (editor-review.renderReviewPage). Assets
  app/history/history.{js,css} are served at /edit/assets/history.* (CSP
  script-src 'self'). All behind the same edit/instructor scope gate.

Run:
  python3 tools/build_history.py              # discover docs from the editor-map
  python3 tools/build_history.py --docs data/firm/firm.json data/curriculum/m1.md
  python3 tools/build_history.py --check      # also run the public-site leak assertion
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
BUILD = os.path.join(ROOT, "build")
SITE_PLATFORM = os.path.join(ROOT, "site", "platform")
HISTORY_DIR = os.path.join(BUILD, "history")
BUNDLE_PATH = os.path.join(BUILD, "history-bundle.generated.json")
EDITOR_MAP = os.path.join(BUILD, "editor-map.generated.json")
ASSETS_DIR = os.path.join(ROOT, "app", "history")

COALESCE_WINDOW_SECS = 600  # 10 minutes (matches the fence build + the plan)
ANYVSANY_CAP = 20  # full any-vs-any redlines precomputed only for the last N revisions/doc

# Sentinel stamped into every history artifact. The public-site leak assertion
# proves this string appears NOWHERE under site/platform/.
HISTORY_SENTINEL = "SONSTENG-HISTORY-EDITOR-GATED"

# The apply engine commits as this identity (tools/apply_suggestions.py step 11:
#   git -c user.name=apply-engine -c user.email=apply@sonsteng.local commit
#   -m "apply: batch <id> (<n> suggestions)").
APPLY_ENGINE_EMAIL = "apply@sonsteng.local"
APPLY_ENGINE_NAME = "apply-engine"
_APPLY_SUBJECT_RE = re.compile(r"^apply:\s*batch\s+(\S+)")
# Revert: the daemon may use a fence-style revert(<doc>) subject; a plain
# `git revert` emits the default `Revert "<subject>"`. Match both.
_REVERT_SUBJECT_RE = re.compile(r'^(revert\(|Revert ")')
# A commit that itself declares a baseline (belt-and-braces; tags are primary).
_BASELINE_SUBJECT_RE = re.compile(r"^baseline\(")

# Author name/email -> editor initials chip. apply-engine batches carry the
# originating human only if the commit body has an `Editor:`/`Co-authored-by:`
# trailer (parsed first); otherwise the git author maps here.
_INITIALS = {
    "damienriehl@gmail.com": "DVR",
    "damienriehl": "DVR",
    "john.sonsteng@sonsteng.local": "JOS",
    "jos@sonsteng.local": "JOS",
    "john sonsteng": "JOS",
    "roger haydock": "RSH",
    "rsh@sonsteng.local": "RSH",
    APPLY_ENGINE_EMAIL: "APPLY",
    APPLY_ENGINE_NAME: "APPLY",
}
_EDITOR_TRAILER_RE = re.compile(
    r"^(?:Editor|Attribution|Co-authored-by):\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_INITIALS_TOKEN_RE = re.compile(r"\b(JOS|RSH|DVR)\b")

# Record layout for `git log`. Fields split on \x1f; records terminated by \x1e
# so the body (%b, may be multi-line) is captured intact.
_LOG_FMT = "%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%at%x1f%s%x1f%b%x1e"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #
class GitRepo:
    """Thin, read-only git wrapper rooted at a working tree."""

    def __init__(self, root: str):
        self.root = root

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", self.root, *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def head_sha(self) -> Optional[str]:
        cp = self._git("rev-parse", "HEAD", check=False)
        return cp.stdout.strip() if cp.returncode == 0 else None

    def rev_parse(self, ref: str) -> Optional[str]:
        cp = self._git("rev-parse", "--verify", "--quiet", ref, check=False)
        out = cp.stdout.strip()
        return out or None

    def read_at(self, ref: Optional[str], relpath: str) -> str:
        """File content at a ref (empty string if the ref is EMPTY/None or the
        path did not exist there)."""
        if ref in (None, "EMPTY", "empty", ""):
            return ""
        cp = self._git("show", f"{ref}:{relpath}", check=False)
        return cp.stdout if cp.returncode == 0 else ""

    def log_follow(self, relpath: str) -> List[Dict[str, Any]]:
        """`git log --follow` for one file, parsed + classified, oldest->newest."""
        cp = self._git(
            "log", "--follow", "--date-order", f"--format={_LOG_FMT}", "--", relpath,
            check=False,
        )
        commits: List[Dict[str, Any]] = []
        if cp.returncode != 0:
            return commits
        for record in cp.stdout.split("\x1e"):
            record = record.lstrip("\n")
            if not record.strip():
                continue
            parts = record.split("\x1f")
            if len(parts) < 8:
                continue
            sha, an, ae, cn, ce, ts, subject, body = parts[:8]
            kind, batch = _classify(subject, cn, ce, ae)
            commits.append({
                "sha": sha, "author": an, "author_email": ae,
                "committer": cn, "committer_email": ce, "ts": int(ts),
                "subject": subject, "body": body, "kind": kind,
                "batch": batch, "attribution": _attribution(an, ae, subject, body),
            })
        commits.reverse()  # oldest -> newest
        return commits

    def baseline_tags(self) -> List[Dict[str, Any]]:
        """Tags named baseline-* -> {name, sha, date, message} (annotated or
        lightweight). git for-each-ref does NOT interpret %x1f escapes, so we
        list the names and resolve each tag's fields individually."""
        cp = self._git("tag", "-l", "--sort=creatordate", "baseline-*", check=False)
        tags: List[Dict[str, Any]] = []
        if cp.returncode != 0:
            return tags
        for name in cp.stdout.splitlines():
            name = name.strip()
            if not name:
                continue
            # The commit the tag resolves to (works for annotated + lightweight).
            sha = self.rev_parse(f"{name}^{{commit}}") or self.rev_parse(name)
            if not sha:
                continue
            date = self._git("log", "-1", "--format=%aI", sha,
                             check=False).stdout.strip()
            # Annotated-tag subject if present, else the target commit subject.
            msg = self._git("tag", "-l", "--format=%(contents:subject)", name,
                            check=False).stdout.strip()
            if not msg:
                msg = self._git("log", "-1", "--format=%s", sha,
                                check=False).stdout.strip()
            tags.append({"name": name, "sha": sha, "date": date, "message": msg})
        return tags


def _classify(subject: str, committer: str, committer_email: str,
              author_email: str) -> Tuple[str, Optional[str]]:
    """Return (kind, batch_id). kind in {edit, external, revert, baseline}.

    edit  = an apply-engine commit (the direct-apply engine landing editor
            suggestions). Recognised by the apply identity OR the `apply: batch`
            subject — batch-granular, so no per-block ids (unlike fence).
    revert  = an inverse commit (daemon revert(<doc>) or a plain `git revert`).
    baseline= a commit self-declaring a baseline (tags are the primary source).
    external= any other commit (a direct home-box session edit)."""
    if _REVERT_SUBJECT_RE.match(subject):
        return "revert", None
    if _BASELINE_SUBJECT_RE.match(subject):
        return "baseline", None
    m = _APPLY_SUBJECT_RE.match(subject)
    is_apply_identity = (
        committer == APPLY_ENGINE_NAME
        or committer_email == APPLY_ENGINE_EMAIL
        or author_email == APPLY_ENGINE_EMAIL
    )
    if m or is_apply_identity:
        return "edit", (m.group(1) if m else None)
    return "external", None


def _attribution(author_name: str, author_email: str, subject: str, body: str) -> str:
    """Best-effort editor chip (JOS/RSH/DVR/APPLY). A commit-body trailer wins
    (apply-engine batches carry the human there); else map the git author."""
    trailer = _EDITOR_TRAILER_RE.search(body or "")
    if trailer:
        val = trailer.group(1).strip()
        tok = _INITIALS_TOKEN_RE.search(val)
        if tok:
            return tok.group(1)
        mapped = _INITIALS.get(val.lower())
        if mapped:
            return mapped
    tok = _INITIALS_TOKEN_RE.search(subject or "")
    if tok:
        return tok.group(1)
    for key in (author_email.lower(), author_name.lower()):
        if key in _INITIALS:
            return _INITIALS[key]
    # Fall back to the author's initials (first letters of name words, ≤3).
    words = [w for w in re.split(r"\s+", author_name.strip()) if w]
    if words:
        return "".join(w[0] for w in words[:3]).upper()
    return "?"


# --------------------------------------------------------------------------- #
# coalescing (display-layer; git stays append-only)
# --------------------------------------------------------------------------- #
def coalesce(commits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group parsed commits (oldest->newest) into display revisions.

    RULE (as built — sonsteng variant of the fence rule, documented for the UAT
    gate): a commit CONTINUES the current run when ALL hold:
      * both the run and the commit are kind == "edit"; AND
      * same author_email; AND
      * the gap to the PREVIOUS commit in the run is <= 10 minutes
        (rolling consecutive-gap window — measured between consecutive
        author-timestamps, NOT from the run's start).
    Any commit failing a condition starts a new revision. external / revert /
    baseline commits NEVER coalesce (each is its own revision).

    DEVIATION FROM FENCE (documented): fence additionally requires block-set
    overlap because its service commits carry per-block `edit(<id>)` subjects.
    sonsteng's apply engine commits whole batches (`apply: batch <id>`) with no
    per-block subject, so there is no block set to overlap on — the coalescing
    key here is author_email + kind + the 10-min consecutive-gap window, exactly
    as the plan specifies ("same-editor ... within ~10 min as one revision")."""
    runs: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for c in commits:
        joinable = (
            cur is not None
            and c["kind"] == "edit"
            and cur["kind"] == "edit"
            and c["author_email"] == cur["author_email"]
            and (c["ts"] - cur["last_ts"]) <= COALESCE_WINDOW_SECS
        )
        if joinable:
            cur["shas"].append(c["sha"])
            cur["last_ts"] = c["ts"]
            cur["subjects"].append(c["subject"])
            if c["batch"]:
                cur["batches"].append(c["batch"])
        else:
            cur = {
                "kind": c["kind"],
                "author": c["author"],
                "author_email": c["author_email"],
                "attribution": c["attribution"],
                "shas": [c["sha"]],
                "batches": [c["batch"]] if c["batch"] else [],
                "first_ts": c["ts"],
                "last_ts": c["ts"],
                "subjects": [c["subject"]],
            }
            runs.append(cur)
    return runs


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _summary(run: Dict[str, Any]) -> str:
    n = len(run["shas"])
    if run["kind"] == "edit":
        base = "Applied editor changes"
        if run["batches"]:
            base += " (" + ", ".join(dict.fromkeys(run["batches"])) + ")"
        return base + (f" · {n} saves" if n > 1 else "")
    return run["subjects"][-1]


# --------------------------------------------------------------------------- #
# per-document history + bounded redline set
# --------------------------------------------------------------------------- #
@dataclass
class DocHistory:
    doc: str  # relpath (the document key)
    slug: str  # filename-safe slug
    revisions: List[Dict[str, Any]] = field(default_factory=list)
    baselines: List[Dict[str, Any]] = field(default_factory=list)
    diffs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dropped_pairs: int = 0


def _slug(relpath: str) -> str:
    return relpath.replace("/", "__")


def _diff_key(frm: str, to: str) -> str:
    return f"{frm}..{to}"


def build_doc_history(repo: GitRepo, relpath: str) -> DocHistory:
    """Coalesced revisions + baseline markers + the bounded, pre-rendered redline
    set for one document."""
    import render_diff_lib as rd

    commits = repo.log_follow(relpath)
    runs = coalesce(commits)
    tags = repo.baseline_tags()
    tag_by_sha: Dict[str, List[dict]] = {}
    for t in tags:
        tag_by_sha.setdefault(t["sha"], []).append(t)

    doc = DocHistory(doc=relpath, slug=_slug(relpath))

    # Build revision records (chronological), resolving each run's parent ref.
    rev_recs: List[Dict[str, Any]] = []
    for run in runs:
        first, last = run["shas"][0], run["shas"][-1]
        parent = repo.rev_parse(f"{first}^") or "EMPTY"
        run_tags: List[dict] = []
        for sha in run["shas"]:
            run_tags.extend(tag_by_sha.get(sha, []))
        rev_recs.append({
            "run": [first, last],
            "tip": last,
            "parent": parent,
            "n_commits": len(run["shas"]),
            "author": run["author"],
            "attribution": run["attribution"],
            "ts_start": _iso(run["first_ts"]),
            "ts_end": _iso(run["last_ts"]),
            "kind": run["kind"],
            "batches": list(dict.fromkeys(run["batches"])),
            "summary": _summary(run),
            "baselines": [t["name"] for t in run_tags],
        })

    # ---- bounded compare matrix -----------------------------------------
    # pairs: (from_ref, to_ref, category). Deduped into the diff cache.
    pairs: Dict[Tuple[str, str], str] = {}

    def add(frm: str, to: str, category: str) -> None:
        if frm == to:
            return
        pairs.setdefault((frm, to), category)

    n = len(rev_recs)
    tips = [r["tip"] for r in rev_recs]

    # (1) each revision's own redline (parent -> tip); (2) adjacent tips.
    for i, r in enumerate(rev_recs):
        add(r["parent"], r["tip"], "revision")
        if i + 1 < n:
            add(tips[i], tips[i + 1], "adjacent")

    # (3) baselines: each-vs-baselines + cumulative (baseline -> HEAD tip).
    head_tip = tips[-1] if tips else None
    for t in tags:
        for r in rev_recs:
            add(t["name"], r["tip"], "baseline")
        if head_tip:
            add(t["name"], head_tip, "cumulative")
    # (4) full document redline from nothing.
    if head_tip:
        add("EMPTY", head_tip, "full")

    # (5) any-vs-any — ONLY for the last ANYVSANY_CAP revisions (the cap).
    window = rev_recs[-ANYVSANY_CAP:]
    wtips = [r["tip"] for r in window]
    for a, b in combinations(range(len(wtips)), 2):
        add(wtips[a], wtips[b], "anyvsany")

    # Dropped-pair accounting: a full any-vs-any over ALL revisions would be
    # C(n,2) pairs; we precompute any-vs-any only within the last cap. Report
    # what a naive "compare any two" would have added beyond our set.
    full_anyvsany = n * (n - 1) // 2
    capped_anyvsany = len(wtips) * (len(wtips) - 1) // 2
    doc.dropped_pairs = max(0, full_anyvsany - capped_anyvsany)
    if doc.dropped_pairs:
        log(
            f"history: {relpath}: {n} revisions; any-vs-any capped to last "
            f"{ANYVSANY_CAP} ({capped_anyvsany} pairs); dropped "
            f"{doc.dropped_pairs} older any-vs-any pairs (older revisions remain "
            f"reachable via per-revision, adjacent, and baseline redlines)."
        )

    # Render every pair once (cache old-text reads).
    text_cache: Dict[str, str] = {}

    def read(ref: str) -> str:
        if ref not in text_cache:
            text_cache[ref] = repo.read_at(ref, relpath)
        return text_cache[ref]

    for (frm, to), category in pairs.items():
        res = rd.diff_html(read(frm), read(to))
        doc.diffs[_diff_key(frm, to)] = {
            "from": frm, "to": to, "category": category,
            "html": res.html, "n_ins": res.n_ins, "n_del": res.n_del,
        }

    rev_recs.reverse()  # newest-first for natural browsing
    doc.revisions = rev_recs
    doc.baselines = [
        {"name": t["name"], "sha": t["sha"], "date": t["date"], "message": t["message"]}
        for t in tags
    ]
    return doc


# --------------------------------------------------------------------------- #
# document discovery
# --------------------------------------------------------------------------- #
def discover_docs(repo: GitRepo, explicit: Optional[List[str]]) -> List[str]:
    """The set of canonical documents to build history for.

    Priority: explicit --docs; else the distinct source files in the generated
    editor-map (every editable block's source_ref file); else a curated glob of
    the data/ corpus. Only files that actually have git history are kept."""
    if explicit:
        candidates = explicit
    else:
        # Union the editor-map's editable source files with the curated data/
        # corpus glob. The editor-map may omit canonical files that ARE edited in
        # git (e.g. firm.json is rendered to CSVs, not directly editable) — the
        # History browser must still cover them.
        candidates = list(_docs_from_glob())
        if os.path.exists(EDITOR_MAP):
            candidates += _docs_from_editor_map()

    kept: List[str] = []
    for rel in sorted(set(candidates)):
        if repo.log_follow(rel):  # has history
            kept.append(rel)
        else:
            log(f"history: skip {rel} (no git history / not tracked)")
    return kept


def _docs_from_editor_map() -> List[str]:
    """Distinct source files from the editor-map. The map's ``pages`` is a dict of
    page-path -> [block, ...]; every block carries a ``source_ref`` shaped
    ``data/...#locator``."""
    with open(EDITOR_MAP, encoding="utf-8") as f:
        data = json.load(f)
    files: set = set()
    pages = data.get("pages") or {}
    if isinstance(pages, dict):
        for blocks in pages.values():
            for b in (blocks or []):
                ref = b.get("source_ref") if isinstance(b, dict) else None
                if ref:
                    files.add(ref.split("#", 1)[0])
    # Fallback for a flat schema (defensive; current map uses `pages`).
    for b in (data.get("blocks") or []):
        ref = b.get("source_ref") if isinstance(b, dict) else None
        if ref:
            files.add(ref.split("#", 1)[0])
    return sorted(files)


def _docs_from_glob() -> List[str]:
    pats: List[str] = []
    matters = os.path.join(DATA, "matters")
    if os.path.isdir(matters):
        for m in sorted(os.listdir(matters)):
            d = os.path.join(matters, m)
            if not os.path.isdir(d):
                continue
            for fn in ("matter.json", "facts.md", "rubric.json"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    pats.append(os.path.relpath(p, ROOT))
    for extra in ("firm/firm.json", "taxonomy/skills.json", "taxonomy/tasks.json"):
        p = os.path.join(DATA, extra)
        if os.path.exists(p):
            pats.append(os.path.relpath(p, ROOT))
    curric = os.path.join(DATA, "curriculum")
    if os.path.isdir(curric):
        for fn in sorted(os.listdir(curric)):
            if fn.endswith(".md"):
                pats.append(os.path.relpath(os.path.join(curric, fn), ROOT))
    return pats


# --------------------------------------------------------------------------- #
# bundle assembly + emit
# --------------------------------------------------------------------------- #
def build_bundle(repo: GitRepo, docs: List[str]) -> Dict[str, Any]:
    doc_objs = {}
    for rel in docs:
        dh = build_doc_history(repo, rel)
        doc_objs[rel] = {
            "doc": dh.doc,
            "slug": dh.slug,
            "revisions": dh.revisions,
            "baselines": dh.baselines,
            "diffs": dh.diffs,
            "dropped_pairs": dh.dropped_pairs,
        }
    return {
        "sentinel": HISTORY_SENTINEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coalesce_window_secs": COALESCE_WINDOW_SECS,
        "anyvsany_cap": ANYVSANY_CAP,
        "head": repo.head_sha(),
        "docs": doc_objs,
    }


def _read_asset(name: str) -> str:
    p = os.path.join(ASSETS_DIR, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    return ""


def _preview_html(doc_obj: Dict[str, Any]) -> str:
    """A self-contained preview page (inlines app/history assets + the doc's data
    island). QA/dev ONLY — the /edit-served production page uses the same island
    with EXTERNAL assets (CSP script-src 'self' forbids the inline <script>
    below). Never served publicly; lives under build/ only."""
    css = _read_asset("history.css")
    # Escape any literal </script in the inlined JS (its JSDoc mentions the island
    # <script> tag) so it can't close the preview's inline <script> early. This is
    # a PREVIEW-ONLY concern — production serves history.js as an external asset.
    js = _read_asset("history.js").replace("</script", "<\\/script")
    island = json.dumps(doc_obj, ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>History · {doc_obj['doc']} · {HISTORY_SENTINEL}</title>"
        f"<style>{css}</style>"
        "<div id=\"history-root\"></div>"
        f"<script id=\"history-data\" type=\"application/json\">{island}</script>"
        f"<script>{js}</script>"
    )


def emit(bundle: Dict[str, Any]) -> None:
    # Clean stale per-doc outputs so a shrinking doc set never leaves orphans.
    if os.path.isdir(HISTORY_DIR):
        for fn in os.listdir(HISTORY_DIR):
            if fn.endswith(".preview.html") or fn.endswith(".json"):
                try:
                    os.remove(os.path.join(HISTORY_DIR, fn))
                except OSError:
                    pass
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(BUNDLE_PATH, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))
    log(f"history: wrote {os.path.relpath(BUNDLE_PATH, ROOT)} "
        f"({len(bundle['docs'])} docs, {os.path.getsize(BUNDLE_PATH)//1024} KB)")

    index = {
        "sentinel": HISTORY_SENTINEL,
        "generated_at": bundle["generated_at"],
        "docs": [
            {"doc": d["doc"], "slug": d["slug"],
             "revisions": len(d["revisions"]), "baselines": len(d["baselines"]),
             "diffs": len(d["diffs"]), "dropped_pairs": d["dropped_pairs"]}
            for d in bundle["docs"].values()
        ],
    }
    with open(os.path.join(HISTORY_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    for d in bundle["docs"].values():
        with open(os.path.join(HISTORY_DIR, d["slug"] + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        with open(os.path.join(HISTORY_DIR, d["slug"] + ".preview.html"), "w",
                  encoding="utf-8") as f:
            f.write(_preview_html(d))


# --------------------------------------------------------------------------- #
# LEAK ASSERTION — history output must never reach the public site build
# --------------------------------------------------------------------------- #
_LEAK_SCAN_EXT = (".html", ".htm", ".json", ".csv", ".md", ".txt", ".js", ".css", ".svg")


def assert_no_history_leak(site_platform: str = SITE_PLATFORM) -> List[str]:
    """Prove the public site build (site/platform/) contains ZERO history output.

    Two nets:
      1. Path net — no history artifact filename (history-bundle*, *.preview.html,
         a history/ dir) exists anywhere under site/platform/.
      2. Content net — the HISTORY_SENTINEL string appears in NO generated file
         under site/platform/ (every history artifact carries it, so its absence
         downstream proves none leaked).
    Returns a list of violations (empty == clean)."""
    violations: List[str] = []
    if not os.path.isdir(site_platform):
        return violations  # nothing built yet — vacuously clean
    for root, dirs, files in os.walk(site_platform):
        # site/platform/assets is input-only design assets — mirror build_site.
        if os.path.relpath(root, site_platform) == "." and "assets" in dirs:
            dirs.remove("assets")
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), site_platform)
            if fn.startswith("history-bundle") or fn.endswith(".preview.html"):
                violations.append(f"history artifact in public site: {rel}")
            if os.path.basename(root) == "history":
                violations.append(f"history/ dir in public site: {rel}")
            if fn.lower().endswith(_LEAK_SCAN_EXT):
                try:
                    with open(os.path.join(root, fn), encoding="utf-8",
                              errors="replace") as fh:
                        if HISTORY_SENTINEL in fh.read():
                            violations.append(
                                f"HISTORY_SENTINEL present in public site file: {rel}")
                except OSError:
                    continue
    return violations


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: List[str]) -> int:
    explicit: Optional[List[str]] = None
    if "--docs" in argv:
        i = argv.index("--docs")
        explicit = [a for a in argv[i + 1:] if not a.startswith("--")]
    do_check = "--check" in argv

    repo = GitRepo(ROOT)
    docs = discover_docs(repo, explicit)
    if not docs:
        log("history: no documents with git history found — nothing to build.")
        return 0
    log(f"history: building history for {len(docs)} document(s).")
    bundle = build_bundle(repo, docs)
    emit(bundle)

    leaks = assert_no_history_leak()
    print("== history leak assertion (site/platform) ==")
    if leaks:
        for v in leaks:
            print("  LEAK: " + v)
        print("history: LEAK ASSERTION FAILED — history output reached the public site.")
        return 1
    print("  clean — no history output under site/platform/.")
    if do_check:
        # --check makes the assertion fatal even when clean paths were vacuous.
        print("history: --check OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
