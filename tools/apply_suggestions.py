#!/usr/bin/env python3
r"""apply_suggestions.py — the Sonsteng Editor apply-transaction engine.

Implements docs/research/editor-apply-spec.md ("Apply transaction") exactly. The
apply loop is the ONLY writer that ever moves a suggestion's edit into the
canonical data spine, and it does so as an all-or-nothing transaction:

    flock(.locks/apply.lock)                # host-local intra-host guard
      -> assert canonical tree clean, record base_sha
      -> RECONCILE first                     # crash recovery before any new claim
      -> claim accepted rows (whole group_id groups only) via RPC   [accepted->in_flight + lease]
      -> git worktree add (from base_sha)    # canonical stays byte-clean throughout
      -> regenerate the editor map from CURRENT worktree source (server-truth)
      -> pre-apply DRIFT gate  (rendered-hash mismatch -> drift, drop)
      -> formatting gate       (has_inline_formatting -> needs_human, v1 rule)
      -> patch (file-grouped, position DESCENDING):
             prose  -> exact-match -> context-anchor -> needs_human
             json_scalar -> parse -> surgical span-splice (WP5, formatting-
                            preserving; NEVER regex) with a re-parse SAFETY GATE,
                            falling back to whole-file parse->set->serialize
      -> value-sync PROPOSER   (matter-prefix-bounded literal search; companions
                                land as pending/origin=companion — NEVER applied here)
      -> validate_spine.py --strict --json     RED = whole-batch accepted_blocked + discard
      -> build_site --check + persona bundle + instructor bundle
      -> check_build_parity                    mismatch = abort + rollback
      -> deploy      (GATED behind APPLY_DEPLOY=1; default = stop, report would-deploy)
      -> merge worktree to canonical
      -> finalize applied + emit word-level diff digest
      -> worktree remove -> release flock

Security invariants (defense in depth — the Worker enforces the same at suggest
AND apply time):
  * Every source_ref / json_path is re-validated against the freshly-generated
    build/editor-map.generated.json (the universal allowlist) at apply time.
  * subprocess is shell=False EVERYWHERE; git runs with core.hooksPath=/dev/null.
  * Every filesystem write is canonicalized under data/ with reject-on
    `..` / absolute / symlink-escape.
  * json_scalar writes go parse->surgical-splice with a re-parse safety gate
    (result must deep-equal parse->set-at-path), never regex, never eval; if the
    span can't be located or verified, we fall back to whole-file
    parse->set->serialize. prose splices are fixed-string exact matches of the
    map-resolved source span.

Cross-host mutex: the flock is host-local. The TRUE cross-host mutex is the DO
`in_flight` lease (claim stamps it; reconcile breaks expired ones). Two hosts can
each hold their own flock, but only one can hold the DO claim — the loser gets
`nothing_to_claim` / `batch_exists`.

Python 3, stdlib only. RPCs and the heavy pipeline steps are injectable so the
engine is unit-testable without a live Worker or a full site build (see
tools/tests/test_apply_suggestions.py).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import decimal
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import text_norm  # noqa: E402  (the ONE canonical normalization contract)
import json_surgical  # noqa: E402  (WP5: formatting-preserving scalar splices)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
LOCK_DIR = os.path.join(REPO_ROOT, ".locks")
LOCK_PATH = os.path.join(LOCK_DIR, "apply.lock")

# Env-var names (documented; NEVER set here — EDIT_SERVICE_TOKEN is a secret).
ENV_API_BASE = "EDIT_API_BASE"          # e.g. https://<worker>/edit/v1  (no trailing slash)
ENV_SERVICE_TOKEN = "EDIT_SERVICE_TOKEN"  # admin/service bookmark token (opaque). NEVER commit/log.
ENV_DEPLOY = "APPLY_DEPLOY"             # "1" => actually deploy; anything else => build+verify only.

# apply_batches journal phases (Worker-owned enum).
PHASE_CLAIMED = "claimed"
PHASE_PATCHED = "patched"
PHASE_VALIDATED = "validated"
PHASE_BUILT = "built"
PHASE_PARITY_OK = "parity_ok"
PHASE_DEPLOYED = "deployed"
PHASE_MERGED = "merged"
PHASE_DONE = "done"
PHASE_ROLLED_BACK = "rolled_back"

# Per-suggestion apply outcomes (map to finalize() status buckets).
OUT_APPLIED = "applied"
OUT_ACCEPTED_BLOCKED = "accepted_blocked"
OUT_NEEDS_HUMAN = "needs_human"
OUT_DRIFT = "drift"


class ApplyError(RuntimeError):
    """A fatal, abort-and-rollback condition (canonical stays clean)."""


# --------------------------------------------------------------------------- #
# Path safety — canonicalize every write target under data/, reject escapes.
# --------------------------------------------------------------------------- #
def safe_data_path(root, source_ref_or_relpath):
    """Resolve a source_ref (``data/...#locator``) or bare relpath to an absolute
    path that is provably INSIDE ``<root>/data`` with no traversal / symlink
    escape. Raises ApplyError on any violation. Returns the absolute path.

    This is the apply-time mirror of the Worker's suggest-time allowlist guard:
    no client-influenced string ever reaches an arbitrary filesystem path.
    """
    relpath = source_ref_or_relpath.split("#", 1)[0]
    if not relpath:
        raise ApplyError("empty source path")
    if os.path.isabs(relpath) or relpath.startswith("~"):
        raise ApplyError("absolute path rejected: %r" % relpath)
    if ".." in relpath.replace("\\", "/").split("/"):
        raise ApplyError("path traversal rejected: %r" % relpath)
    norm = os.path.normpath(relpath)
    if norm.startswith("..") or os.path.isabs(norm):
        raise ApplyError("path escapes repo: %r" % relpath)
    data_root = os.path.realpath(os.path.join(root, "data"))
    candidate = os.path.join(root, norm)
    # realpath of the PARENT (the file may or may not exist yet); reject if the
    # resolved location is not under data/ (blocks symlinked-directory escape).
    real_parent = os.path.realpath(os.path.dirname(candidate))
    if not (real_parent == data_root or real_parent.startswith(data_root + os.sep)):
        raise ApplyError("path escapes data/: %r" % relpath)
    real_full = os.path.realpath(candidate)
    if os.path.exists(candidate) and not (
        real_full == data_root or real_full.startswith(data_root + os.sep)
    ):
        raise ApplyError("symlink escapes data/: %r" % relpath)
    if not norm.startswith("data" + os.sep) and norm != "data":
        raise ApplyError("source_ref must live under data/: %r" % relpath)
    return candidate


# --------------------------------------------------------------------------- #
# Git — shell=False, hooks disabled (never run a repo hook during apply).
# --------------------------------------------------------------------------- #
def git(args, cwd, check=True, capture=True):
    cmd = ["git", "-c", "core.hooksPath=/dev/null", *args]
    proc = subprocess.run(
        cmd, cwd=cwd, check=False, shell=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and proc.returncode != 0:
        raise ApplyError(
            "git %s failed (%d): %s"
            % (" ".join(args), proc.returncode, (proc.stderr or "").strip())
        )
    return proc


def assert_clean_tree(root):
    out = git(["status", "--porcelain"], root).stdout.strip()
    if out:
        raise ApplyError(
            "canonical tree is dirty — refusing to apply. Uncommitted:\n%s" % out
        )


def head_sha(root):
    return git(["rev-parse", "HEAD"], root).stdout.strip()


# --------------------------------------------------------------------------- #
# flock — host-local intra-host guard (the DO in_flight lease is the true mutex).
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def apply_lock(lock_path=LOCK_PATH):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise ApplyError(
                "another apply run holds %s (host-local). Cross-host mutex is the "
                "DO lease. (%s)" % (lock_path, exc)
            )
        fh.write("pid=%d\n" % os.getpid())
        fh.flush()
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# --------------------------------------------------------------------------- #
# RPC client — the Worker EditorStore apply-engine RPCs. Injectable.
# --------------------------------------------------------------------------- #
class HttpRpcClient:
    """Talks to the live Worker's admin/service /edit/v1 RPCs.

    Wire contract (app/worker/API-CONTRACTS.md "Apply-engine RPCs"):
      * POST /reconcile                       -> { ok, ... }
      * POST /claim   {batch_id, base_sha?}   -> { ok, batch_id, claimed:[id...], lease_expires_at }
      * GET  /review                          -> { ok, items:[<full suggestion rows>] }  (admin)
      * POST /finalize {batch_id, phase, applied?, accepted_blocked?, needs_human?, drift?}
      * POST /system-suggest {id, source_ref, origin, ...} -> SYSTEM proposer
            (origin=companion|ai_rewrite, pending). Admin scope only; the human
            /suggest endpoint hardcodes origin:human and is edit/instructor-scoped,
            so system-generated provenance MUST use this admin-scoped path.

    Auth: the admin bookmark token in EDIT_SERVICE_TOKEN (== service scope). The
    token is sent as a Bearer credential and is NEVER logged. CSRF header
    X-Edit-Request:1 is included on every mutation (matches the Worker guard).
    """

    def __init__(self, base_url, token, timeout=30):
        self.base = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def _req(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Edit-Request", "1")
        req.add_header("Accept", "application/json")
        # Cloudflare edge bot-mitigation bans the default python-urllib UA
        # (error 1010) before the Worker sees the request; send a normal UA.
        req.add_header("User-Agent", "sonsteng-apply-engine/1.0")
        if self._token:
            req.add_header("Authorization", "Bearer " + self._token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace")
            # NEVER echo the token; only status + server error body (token not in it).
            raise ApplyError("RPC %s %s -> HTTP %d: %s" % (method, path, exc.code, payload))
        except urllib.error.URLError as exc:
            raise ApplyError("RPC %s %s unreachable: %s" % (method, path, exc.reason))

    def reconcile(self):
        return self._req("POST", "/reconcile", {})

    def claim(self, batch_id, base_sha=None):
        return self._req("POST", "/claim", {"batch_id": batch_id, "base_sha": base_sha})

    def fetch_batch_rows(self, batch_id, claimed_ids):
        review = self._req("GET", "/review")
        items = review.get("items") or review.get("suggestions") or []
        wanted = set(claimed_ids)
        return [r for r in items if r.get("id") in wanted]

    def propose_companion(self, companion):
        # Companions are born pending/origin=companion; the Worker resolves
        # editor/original_text server-side and enforces the group. Structurally
        # cannot auto-apply (only admin decide -> accepted). SYSTEM provenance
        # (companion/ai_rewrite) goes to the admin-scoped /system-suggest endpoint;
        # the human /suggest endpoint hardcodes origin:human and would 403 the
        # admin service token ("No edit scope").
        return self._req("POST", "/system-suggest", companion)

    def finalize(self, batch_id, phase=None, applied=None, accepted_blocked=None,
                 needs_human=None, drift=None, base_sha=None):
        body = {"batch_id": batch_id}
        if phase is not None:
            body["phase"] = phase
        if base_sha is not None:
            body["base_sha"] = base_sha
        for key, val in (("applied", applied), ("accepted_blocked", accepted_blocked),
                         ("needs_human", needs_human), ("drift", drift)):
            if val:
                body[key] = list(val)
        return self._req("POST", "/finalize", body)


# --------------------------------------------------------------------------- #
# Pipeline — the heavy tools the engine orchestrates. Injectable for tests.
# --------------------------------------------------------------------------- #
class SubprocessPipeline:
    """Production pipeline: shells out (shell=False) to the real spine tools,
    always running the WORKTREE's own copy so the tools operate on worktree data.
    """

    def _run(self, args, cwd):
        proc = subprocess.run(
            args, cwd=cwd, check=False, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        return proc.returncode, proc.stdout

    def regenerate_map(self, worktree):
        """Rebuild the editor map from CURRENT worktree source and return the
        source-indexed allowlist {source_ref: block}. This IS the server-truth
        re-validation (reuse build_site's walker/renderer — never reinvent)."""
        rc, out = self._run(
            [sys.executable, os.path.join(worktree, "tools", "build_site.py")], worktree)
        map_path = os.path.join(worktree, "build", "editor-map.generated.json")
        if not os.path.isfile(map_path):
            raise ApplyError("map regeneration produced no editor map:\n%s" % out[-2000:])
        with open(map_path, "r", encoding="utf-8") as fh:
            bundle = json.load(fh)
        return index_map(bundle)

    def validate(self, worktree):
        report_path = os.path.join(worktree, "build", "validate-report.json")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        rc, out = self._run(
            [sys.executable, os.path.join(worktree, "tools", "validate_spine.py"),
             "--strict", "--json", report_path, "--quiet"], worktree)
        report = None
        if os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
        return rc == 0, {"stdout": out, "report": report}

    def build(self, worktree):
        rc1, o1 = self._run(
            [sys.executable, os.path.join(worktree, "tools", "build_site.py"), "--check"], worktree)
        if rc1 != 0:
            return False, {"step": "build_site --check", "stdout": o1}
        rc2, o2 = self._run(
            [sys.executable, os.path.join(worktree, "tools", "build_worker_personas.py")], worktree)
        if rc2 != 0:
            return False, {"step": "build_worker_personas", "stdout": o2}
        rc3, o3 = self._run(
            [sys.executable, os.path.join(worktree, "tools", "build_instructor_bundle.py")], worktree)
        if rc3 != 0:
            return False, {"step": "build_instructor_bundle", "stdout": o3}
        return True, {"stdout": o1 + o2 + o3}

    def parity(self, worktree):
        rc, out = self._run(
            [sys.executable, os.path.join(worktree, "tools", "check_build_parity.py")], worktree)
        return rc == 0, {"stdout": out}

    def deploy(self, worktree, branch, plan_only):
        """Deploy the site (Hetzner DEV) + Worker (wrangler). GATED: only executes
        when plan_only is False (APPLY_DEPLOY=1). Returns the command plan either
        way so a dry/build-only run reports exactly what WOULD deploy."""
        plan = [
            ["bash", os.path.join(worktree, "deploy", "deploy-dev.sh"), branch],
            ["npx", "--yes", "wrangler@latest", "deploy"],  # cwd app/worker
        ]
        if plan_only:
            return True, {"planned": plan, "executed": False}
        rc1, o1 = self._run(plan[0], worktree)
        if rc1 != 0:
            return False, {"step": "deploy-dev.sh", "stdout": o1, "planned": plan}
        rc2, o2 = self._run(plan[1], os.path.join(worktree, "app", "worker"))
        if rc2 != 0:
            return False, {"step": "wrangler deploy", "stdout": o2, "planned": plan}
        return True, {"planned": plan, "executed": True, "stdout": o1 + o2}


def index_map(bundle):
    """Flatten editor-map.generated.json {pages: {page: [blocks]}} into
    {source_ref: block(+page)}. source_ref is unique per (file, block)."""
    idx = {}
    for page, blocks in (bundle.get("pages") or {}).items():
        for block in blocks:
            b = dict(block)
            b["page"] = page
            idx[b["source_ref"]] = b
    return idx


# --------------------------------------------------------------------------- #
# JSON path helpers (parse -> set -> serialize; NEVER text-splice).
# --------------------------------------------------------------------------- #
def json_get(obj, dotted):
    cur = obj
    for key in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(key)]
        elif isinstance(cur, dict):
            cur = cur[key]
        else:
            raise KeyError(dotted)
    return cur


def json_set(obj, dotted, value):
    keys = dotted.split(".")
    cur = obj
    for key in keys[:-1]:
        cur = cur[int(key)] if isinstance(cur, list) else cur[key]
    last = keys[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _detect_json_style(raw):
    """Best-effort preservation of a file's JSON formatting for minimal diffs.
    Detects indent width and whether the file ascii-escapes non-ASCII."""
    ensure_ascii = "\\u" in raw and not _has_raw_nonascii(raw)
    indent = 2
    m = re.search(r"\n( +)\"", raw)
    if m:
        indent = len(m.group(1))
    return indent, ensure_ascii


def _has_raw_nonascii(raw):
    try:
        raw.encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def dump_json_like(obj, raw_original):
    indent, ensure_ascii = _detect_json_style(raw_original)
    text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent)
    if raw_original.endswith("\n"):
        text += "\n"
    return text


# --------------------------------------------------------------------------- #
# source_ref classification + patchers
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Patch:
    suggestion_id: str
    group_id: str
    source_ref: str
    relpath: str
    kind: str           # "json_scalar" | "prose_md" | "prose_json_body"
    json_path: str      # scalar path, or body_md path for prose_json_body
    original_text: str  # RAW source span (map-resolved) for prose; scalar value for json_scalar
    new_text: str


def classify(source_ref, block):
    relpath, locator = source_ref.split("#", 1)
    if block.get("kind") == "json_scalar":
        return "json_scalar", block.get("json_path") or locator
    if relpath.endswith(".md"):
        return "prose_md", ""
    # prose living inside a JSON markdown field: locator = "<path>.body_md.pN"
    body_path = re.sub(r"\.p\d+$", "", locator)
    return "prose_json_body", body_path


def _count_and_replace(text, needle, replacement):
    """Exact fixed-string replace of a UNIQUE occurrence. Returns
    (new_text, n_matches). n!=1 => caller routes to needs_human. Never regex."""
    n = text.count(needle)
    if n != 1:
        return text, n
    return text.replace(needle, replacement), 1


def apply_file_patches(worktree, relpath, patches):
    """Apply all patches targeting one physical file atomically (parse-once for
    JSON). Returns {suggestion_id: True|"needs_human"}. Never partially writes a
    file: if ANY patch on the file is ambiguous, NOTHING in the file is written
    (caller drops the whole file's group set to needs_human via outcomes)."""
    abspath = safe_data_path(worktree, relpath)
    with open(abspath, "r", encoding="utf-8") as fh:
        raw = fh.read()

    results = {}
    if relpath.endswith(".md"):
        # prose_md only. Apply position-DESCENDING so offsets never shift.
        ordered = sorted(patches, key=lambda p: raw.find(p.original_text), reverse=True)
        new_raw = raw
        for p in ordered:
            new_raw, n = _count_and_replace(new_raw, p.original_text, p.new_text)
            results[p.suggestion_id] = True if n == 1 else OUT_NEEDS_HUMAN
        if OUT_NEEDS_HUMAN in results.values():
            return results  # ambiguity: abandon the whole-file write
        with open(abspath, "w", encoding="utf-8") as fh:
            fh.write(new_raw)
        return results

    # .json file: resolve every edit to a (json_path, new_value) pair, then write
    # the file with a SINGLE strategy so all edits land atomically.
    #
    #   json_scalar      -> new_value = p.new_text (the scalar itself)
    #   prose_json_body  -> new_value = the body string with the ONE exact-literal
    #                       old span replaced (n!=1 => needs_human, abandon file)
    #
    # The WRITE is formatting-preserving (WP5): a surgical span-splice that edits
    # only each targeted value's bytes, so a one-scalar edit yields a one-line
    # diff and a value-rewrite-to-itself is byte-identical. If the surgical path
    # can't be applied safely (span unlocatable, overlap, or the re-parsed result
    # doesn't EXACTLY equal parse->set-at-path), we fall back to the v1
    # whole-file parse->set->serialize path. Either way the logical object is
    # identical; only the diff minimality differs.
    obj = json.loads(raw)
    edits = []  # [(json_path, new_value)] in patch order
    for p in patches:
        if p.kind == "json_scalar":
            edits.append((p.json_path, p.new_text))
            results[p.suggestion_id] = True
        else:  # prose_json_body
            body = json_get(obj, p.json_path)
            if not isinstance(body, str):
                results[p.suggestion_id] = OUT_NEEDS_HUMAN
                continue
            new_body, n = _count_and_replace(body, p.original_text, p.new_text)
            if n != 1:
                results[p.suggestion_id] = OUT_NEEDS_HUMAN
                continue
            edits.append((p.json_path, new_body))
            results[p.suggestion_id] = True
    if OUT_NEEDS_HUMAN in results.values():
        return results  # ambiguity: abandon the whole-file write

    new_raw = write_json_edits(raw, edits)
    with open(abspath, "w", encoding="utf-8") as fh:
        fh.write(new_raw)
    return results


def write_json_edits(raw, edits):
    """Produce the new text for a .json file with `edits` = [(json_path, value)]
    applied. Prefers the formatting-preserving surgical splice; on SurgicalError
    falls back to the v1 whole-file parse->set->serialize. Both yield the same
    logical object (the surgical path proves it via a re-parse equality gate)."""
    try:
        return json_surgical.splice_scalars(raw, edits)
    except json_surgical.SurgicalError:
        obj = json.loads(raw)
        for path, value in edits:
            json_set(obj, path, value)
        return dump_json_like(obj, raw)


# --------------------------------------------------------------------------- #
# Value-sync proposer (scope rules — no NLP). Companions are NEVER auto-applied.
# --------------------------------------------------------------------------- #
_MONEY_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\$\s?\d+(?:\.\d{2})?")
_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b")
# Proper noun: a multi-word Capitalized run (>=2 tokens) — single Capitalized
# words are too low-entropy (route to manual).
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-zA-Z’'.-]+)(?:\s+[A-Z][a-zA-Z’'.-]+)+\b")
# Low-entropy money/years that must route to manual, never auto-companioned.
_LOW_ENTROPY_MONEY = {"$1,000", "$100", "$1000", "$500", "$10,000", "$5,000"}


def changed_value_tokens(original_text, new_text):
    """Return the set of money/date/proper-noun literals that appear in the NEW
    text but not the OLD (the values that changed and might need syndication).
    Bare unanchored digits + low-entropy values are excluded (route to manual)."""
    tokens = set()
    for pat, kind in ((_MONEY_RE, "money"), (_DATE_RE, "date"), (_PROPER_RE, "proper")):
        old = set(m.group(0) for m in pat.finditer(original_text or ""))
        new = set(m.group(0) for m in pat.finditer(new_text or ""))
        for tok in new - old:
            t = tok.strip()
            if kind == "money" and t.replace(" ", "") in _LOW_ENTROPY_MONEY:
                continue
            tokens.add((kind, t))
    return tokens


def _money_equal(a, b):
    try:
        da = decimal.Decimal(re.sub(r"[^\d.]", "", a))
        db = decimal.Decimal(re.sub(r"[^\d.]", "", b))
        return abs(da - db) <= decimal.Decimal("0.01")
    except (decimal.InvalidOperation, ValueError):
        return False


def matter_scope_files(worktree, matter):
    """The value-sync search scope for a matter: every file under this matter's
    dir + the firm book (firm.json). Matter-prefix-BOUNDED — never crosses mNN."""
    files = []
    matters_dir = os.path.join(worktree, "data", "matters")
    if os.path.isdir(matters_dir):
        for name in sorted(os.listdir(matters_dir)):
            # match "m03" / "m03-..." exactly, never m30, never m3.
            if name == matter or re.match(r"^" + re.escape(matter) + r"(?:-|$)", name):
                mdir = os.path.join(matters_dir, name)
                for root, _dirs, fnames in os.walk(mdir):
                    for fn in sorted(fnames):
                        files.append(os.path.join(root, fn))
    firm = os.path.join(worktree, "data", "firm", "firm.json")
    if os.path.isfile(firm):
        files.append(firm)
    return files


def matter_of(source_ref):
    m = re.search(r"data/matters/(m\d{2})", source_ref)
    return m.group(1) if m else None


def propose_value_sync(worktree, applied_patches, source_index, client, batch_id):
    """For each APPLIED prose patch, find the OLD literal's other anchored
    occurrences within the SAME matter's scope and propose a pending companion
    suggestion per hit. Companions are grouped, origin=companion, and are NEVER
    applied by this engine (structurally admin-gated). Returns the list of
    companion payloads proposed (also useful for the digest/tests)."""
    proposed = []
    for patch in applied_patches:
        if patch.kind == "json_scalar":
            continue  # json_scalar syndicates via regenerate — not a companion case
        matter = matter_of(patch.source_ref)
        if not matter:
            continue
        changed_new = changed_value_tokens(patch.original_text, patch.new_text)
        if not changed_new:
            continue
        # For each changed value, the OLD literal it replaced (same kind).
        for kind, new_tok in changed_new:
            old_candidates = _old_literals_for(kind, patch.original_text, patch.new_text)
            for old_tok in old_candidates:
                group_id = "vs-%s-%s" % (batch_id, _slug(old_tok))
                for target_ref, occ in _find_anchored_occurrences(
                        worktree, matter, old_tok, new_tok, kind, source_index,
                        exclude=patch.source_ref):
                    payload = {
                        "id": _companion_id(old_tok, target_ref),
                        "origin": "companion",
                        "kind": occ["kind"],
                        "source_ref": target_ref,
                        "json_path": occ.get("json_path"),
                        "new_text": occ["proposed_text"],
                        "original_hash": occ.get("original_hash"),
                        "comment": "Value-sync: %r changed to %r in %s"
                                   % (old_tok, new_tok, patch.source_ref),
                        "group_id": group_id,
                        "status": "pending",
                        "map_version": None,
                    }
                    client.propose_companion(payload)
                    proposed.append(payload)
    return proposed


def _old_literals_for(kind, original_text, new_text):
    pat = {"money": _MONEY_RE, "date": _DATE_RE, "proper": _PROPER_RE}[kind]
    old = set(m.group(0).strip() for m in pat.finditer(original_text or ""))
    new = set(m.group(0).strip() for m in pat.finditer(new_text or ""))
    out = []
    for tok in old - new:
        if kind == "money" and tok.replace(" ", "") in _LOW_ENTROPY_MONEY:
            continue
        out.append(tok)
    return out


def _find_anchored_occurrences(worktree, matter, old_tok, new_tok, kind, source_index, exclude):
    """Exact-literal, matter-prefix-bounded search for old_tok across the matter's
    scope. 'Anchored' = the literal falls inside a KNOWN editable block from the
    map (so companions carry a real source_ref/hash and can round-trip), never a
    bare grep hit in arbitrary bytes. Yields (source_ref, occurrence_meta)."""
    scope = set(os.path.realpath(f) for f in matter_scope_files(worktree, matter))
    for source_ref, block in source_index.items():
        if source_ref == exclude:
            continue
        # Same matter only (defense in depth over scope files).
        if matter_of(source_ref) not in (matter, None):
            continue
        try:
            abspath = os.path.realpath(safe_data_path(worktree, source_ref))
        except ApplyError:
            continue
        if abspath not in scope:
            continue
        original_text = block.get("original_text") or ""
        if old_tok not in original_text:
            continue
        # money: also require Decimal equality (belt-and-suspenders vs literal).
        if kind == "money":
            hits = [m.group(0) for m in _MONEY_RE.finditer(original_text)]
            if not any(_money_equal(h, old_tok) for h in hits):
                continue
        yield source_ref, {
            "kind": block.get("kind", "prose"),
            "json_path": block.get("json_path"),
            "original_hash": block.get("original_hash"),
            # Proposed text = the block with the old literal swapped for the new one.
            # (Damien reviews before it can ever be accepted — companions never
            # auto-apply.)
            "proposed_text": original_text.replace(old_tok, new_tok),
        }


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40] or "x"


def _companion_id(old_tok, target_ref):
    """A deterministic companion suggestion id that fits the Worker's uuid ceiling
    (`[a-zA-Z0-9_-]{8,64}`). The naive "companion-<slug>-<slug>" form overran 64
    chars for real source_refs and was rejected at /system-suggest with
    validation_error; we keep a short readable prefix and append a stable hash of
    (old literal, target_ref) so the id is unique per target AND idempotent across
    retries of the same proposal."""
    import hashlib
    h = hashlib.sha256(("%s|%s" % (old_tok, target_ref)).encode("utf-8")).hexdigest()[:16]
    return ("companion-%s-%s" % (_slug(old_tok)[:24], h))[:64]


# --------------------------------------------------------------------------- #
# Word-level diff digest (markdown)
# --------------------------------------------------------------------------- #
def word_diff(old, new):
    import difflib
    old_w, new_w = (old or "").split(), (new or "").split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_w, new_w).get_opcodes():
        if tag == "equal":
            out.append(" ".join(old_w[i1:i2]))
        elif tag == "delete":
            out.append("~~%s~~" % " ".join(old_w[i1:i2]))
        elif tag == "insert":
            out.append("**%s**" % " ".join(new_w[j1:j2]))
        elif tag == "replace":
            out.append("~~%s~~ **%s**" % (" ".join(old_w[i1:i2]), " ".join(new_w[j1:j2])))
    return " ".join(w for w in out if w)


def build_digest(batch_id, base_sha, applied, drift, needs_human, blocked,
                 companions, deploy_info):
    lines = ["# Apply digest — batch `%s`" % batch_id, ""]
    lines.append("- base_sha: `%s`" % base_sha)
    lines.append("- generated: %s" % datetime.datetime.now(datetime.timezone.utc).isoformat())
    lines.append("- deploy: %s" % ("EXECUTED" if deploy_info.get("executed")
                                   else "NOT run (APPLY_DEPLOY unset) — would run below"))
    lines.append("")
    if deploy_info.get("planned"):
        lines.append("## Would deploy")
        for cmd in deploy_info["planned"]:
            lines.append("- `%s`" % " ".join(cmd))
        lines.append("")
    lines.append("## Applied (%d)" % len(applied))
    for p in applied:
        lines.append("- **%s** (`%s`)" % (p.source_ref, p.suggestion_id))
        lines.append("  - %s" % word_diff(p.original_text, p.new_text))
    for label, rows in (("Drift — re-review (%d)", drift),
                        ("Needs human (%d)", needs_human),
                        ("Accepted-blocked — validator RED (%d)", blocked)):
        if rows:
            lines.append("")
            lines.append("## " + label % len(rows))
            for p in rows:
                ref = p.source_ref if isinstance(p, Patch) else p.get("source_ref", "?")
                sid = p.suggestion_id if isinstance(p, Patch) else p.get("id", "?")
                lines.append("- `%s` (`%s`)" % (ref, sid))
    if companions:
        lines.append("")
        lines.append("## Value-sync companions PROPOSED (pending — not applied) (%d)"
                     % len(companions))
        for c in companions:
            lines.append("- `%s` — %s" % (c["source_ref"], c["comment"]))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The transaction
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ApplyResult:
    batch_id: str
    base_sha: str
    applied: list
    drift: list
    needs_human: list
    accepted_blocked: list
    companions: list
    deploy: dict
    digest_md: str
    committed: bool
    reason: str = ""


def _group_outcomes(rows, source_index):
    """Group claimed rows by group_id (singletons keyed by their own id). Returns
    {group_key: [rows]} and the per-row Patch objects / drop reasons.

    Group-atomic policy (spec: 'never split a group'): a group is patched ONLY if
    every member is cleanly patchable. If any member drifts / needs_human, the
    WHOLE group takes that (worst) status and none of its members are patched."""
    groups = {}
    for r in rows:
        key = r.get("group_id") or ("solo:" + r["id"])
        groups.setdefault(key, []).append(r)
    return groups


def run_apply(client, pipeline, batch_id, *, worktree_parent=None, deploy_plan_only=True,
              branch="feat/editor-experience", canonical_root=REPO_ROOT, logger=print):
    """Execute the whole apply transaction. Returns ApplyResult. Canonical tree is
    guaranteed byte-clean unless the run reaches the final merge (committed=True)."""
    assert_clean_tree(canonical_root)
    base_sha = head_sha(canonical_root)
    logger("base_sha=%s" % base_sha)

    # 1) RECONCILE FIRST — crash recovery before any new claim.
    client.reconcile()

    # 2) CLAIM (whole groups only; accepted -> in_flight + lease + journal=claimed).
    claim = client.claim(batch_id, base_sha=base_sha)
    if not claim.get("ok"):
        return ApplyResult(batch_id, base_sha, [], [], [], [], [], {}, "", False,
                           reason=claim.get("reason", "claim_failed"))
    claimed_ids = claim.get("claimed", [])
    rows = client.fetch_batch_rows(batch_id, claimed_ids)
    logger("claimed %d rows across %d groups" % (
        len(rows), len({r.get("group_id") or r["id"] for r in rows})))

    wt = tempfile.mkdtemp(prefix="apply-wt-", dir=worktree_parent)
    committed = False
    try:
        # 3) worktree add from base_sha (canonical never dirtied).
        scratch_branch = "apply/%s" % batch_id
        git(["worktree", "add", "--detach", wt, base_sha], canonical_root)
        git(["checkout", "-B", scratch_branch], wt)

        # 4) Regenerate the map from CURRENT worktree source = server-truth allowlist.
        source_index = pipeline.regenerate_map(wt)

        # 5) Gate + classify each group atomically.
        groups = _group_outcomes(rows, source_index)
        drift, needs_human, patch_by_file = [], [], {}
        candidate_patches = []  # groups that passed all pre-patch gates
        for key, members in groups.items():
            group_status, group_patches = _gate_group(members, source_index, wt)
            if group_status == OUT_DRIFT:
                drift.extend(members)
            elif group_status == OUT_NEEDS_HUMAN:
                needs_human.extend(members)
            else:
                candidate_patches.extend(group_patches)

        # 6) Patch (file-grouped, position DESCENDING within file).
        for p in candidate_patches:
            patch_by_file.setdefault(p.relpath, []).append(p)

        file_results = {}
        for relpath, patches in patch_by_file.items():
            file_results.update(apply_file_patches(wt, relpath, patches))

        # Ambiguity discovered at splice time -> whole group -> needs_human.
        blocked_by_ambiguity = {sid for sid, ok in file_results.items()
                                if ok == OUT_NEEDS_HUMAN}
        if blocked_by_ambiguity:
            # Roll the ambiguous groups (and their file co-tenants) out of applied.
            ambiguous_groups = {p.group_id for p in candidate_patches
                                if p.suggestion_id in blocked_by_ambiguity}
            kept = []
            for p in candidate_patches:
                if p.group_id in ambiguous_groups:
                    needs_human.append(_row_of(rows, p.suggestion_id))
                else:
                    kept.append(p)
            # Re-derive the worktree from scratch to drop the partial ambiguous writes.
            git(["checkout", "--", "."], wt)
            candidate_patches = []
            patch_by_file = {}
            for p in kept:
                patch_by_file.setdefault(p.relpath, []).append(p)
            for relpath, patches in patch_by_file.items():
                apply_file_patches(wt, relpath, patches)
            candidate_patches = kept

        applied_patches = candidate_patches
        client.finalize(batch_id, phase=PHASE_PATCHED)

        # 7) Value-sync PROPOSER — companions (pending, never applied here).
        companions = propose_value_sync(wt, applied_patches, source_index, client, batch_id)

        # 8) validate_spine --strict --json. RED = whole batch accepted_blocked + discard.
        ok, vinfo = pipeline.validate(wt)
        if not ok:
            client.finalize(batch_id, phase=PHASE_ROLLED_BACK,
                            accepted_blocked=[p.suggestion_id for p in applied_patches],
                            needs_human=[_id(r) for r in needs_human],
                            drift=[_id(r) for r in drift])
            digest = build_digest(batch_id, base_sha, [], drift, needs_human,
                                  applied_patches, companions, {})
            return ApplyResult(batch_id, base_sha, [], drift, needs_human,
                               applied_patches, companions, {}, digest, False,
                               reason="validator_red")
        client.finalize(batch_id, phase=PHASE_VALIDATED)

        # 9) Build all three bundles.
        ok, binfo = pipeline.build(wt)
        if not ok:
            client.finalize(batch_id, phase=PHASE_ROLLED_BACK,
                            accepted_blocked=[p.suggestion_id for p in applied_patches])
            return ApplyResult(batch_id, base_sha, [], drift, needs_human,
                               applied_patches, companions, {}, "", False,
                               reason="build_failed:%s" % binfo.get("step"))
        client.finalize(batch_id, phase=PHASE_BUILT)

        # 10) Two-bundle parity gate — abort + rollback on mismatch.
        ok, pinfo = pipeline.parity(wt)
        if not ok:
            client.finalize(batch_id, phase=PHASE_ROLLED_BACK,
                            accepted_blocked=[p.suggestion_id for p in applied_patches])
            return ApplyResult(batch_id, base_sha, [], drift, needs_human,
                               applied_patches, companions, {}, "", False,
                               reason="parity_mismatch")
        client.finalize(batch_id, phase=PHASE_PARITY_OK)

        # 11) Commit the worktree (so deploy/merge have a ref).
        git(["add", "-A"], wt)
        git(["-c", "user.name=apply-engine", "-c", "user.email=apply@sonsteng.local",
             "commit", "-m", "apply: batch %s (%d suggestions)" % (batch_id, len(applied_patches))],
            wt, check=bool(applied_patches))

        # 12) DEPLOY — GATED. Default (plan_only) stops here and reports would-deploy.
        deploy_ok, deploy_info = pipeline.deploy(wt, scratch_branch, deploy_plan_only)
        if not deploy_ok:
            client.finalize(batch_id, phase=PHASE_ROLLED_BACK,
                            accepted_blocked=[p.suggestion_id for p in applied_patches])
            return ApplyResult(batch_id, base_sha, [], drift, needs_human,
                               applied_patches, companions, deploy_info, "", False,
                               reason="deploy_failed")
        if deploy_info.get("executed"):
            client.finalize(batch_id, phase=PHASE_DEPLOYED)

        digest = build_digest(batch_id, base_sha, applied_patches, drift, needs_human,
                              [], companions, deploy_info)

        if deploy_plan_only:
            # SAFE build-only mode: everything validated + built + parity-checked;
            # we STOP before deploy+merge so the engine is runnable without shipping.
            client.finalize(batch_id, phase=PHASE_ROLLED_BACK,
                            drift=[_id(r) for r in drift],
                            needs_human=[_id(r) for r in needs_human])
            logger("APPLY_DEPLOY unset -> stopped pre-deploy. Canonical untouched.")
            return ApplyResult(batch_id, base_sha, applied_patches, drift, needs_human,
                               [], companions, deploy_info, digest, False,
                               reason="build_only_stopped_pre_deploy")

        # 13) Merge worktree -> canonical (fast-forward; canonical == base_sha under lock).
        git(["merge", "--ff-only", scratch_branch], canonical_root)
        client.finalize(batch_id, phase=PHASE_MERGED)
        committed = True

        # 14) Finalize applied + terminal statuses.
        client.finalize(batch_id, phase=PHASE_DONE,
                        applied=[p.suggestion_id for p in applied_patches],
                        drift=[_id(r) for r in drift],
                        needs_human=[_id(r) for r in needs_human])
        return ApplyResult(batch_id, base_sha, applied_patches, drift, needs_human,
                           [], companions, deploy_info, digest, True)
    finally:
        # worktree remove (never leaves canonical dirty; safe on every failure path).
        with contextlib.suppress(Exception):
            git(["worktree", "remove", "--force", wt], canonical_root, check=False)
        with contextlib.suppress(Exception):
            git(["branch", "-D", "apply/%s" % batch_id], canonical_root, check=False)
        import shutil
        with contextlib.suppress(Exception):
            shutil.rmtree(wt, ignore_errors=True)
        if not committed:
            # Belt-and-suspenders: canonical must be byte-clean on any non-merge exit.
            with contextlib.suppress(Exception):
                assert_clean_tree(canonical_root)


def _gate_group(members, source_index, worktree):
    """Return (group_status, [Patch]). group_status in
    {OUT_DRIFT, OUT_NEEDS_HUMAN, ""}. "" => all members cleanly patchable."""
    patches = []
    for r in members:
        source_ref = r["source_ref"]
        block = source_index.get(source_ref)
        # Allowlist re-validation (defense in depth) — unknown ref => drift (re-review).
        if block is None:
            return OUT_DRIFT, []
        # json_path forgery check.
        if r.get("kind") == "json_scalar":
            if (r.get("json_path") or "") != (block.get("json_path") or ""):
                return OUT_NEEDS_HUMAN, []
        # DRIFT gate: rendered-hash of CURRENT source vs the suggestion's stored hash.
        if r.get("original_hash") and block.get("original_hash") \
                and r["original_hash"] != block["original_hash"]:
            return OUT_DRIFT, []
        # FORMATTING gate (v1): has_inline_formatting => needs_human (no merge algo).
        if block.get("has_inline_formatting"):
            return OUT_NEEDS_HUMAN, []
        kind, json_path = classify(source_ref, block)
        patches.append(Patch(
            suggestion_id=r["id"],
            group_id=r.get("group_id") or ("solo:" + r["id"]),
            source_ref=source_ref,
            relpath=source_ref.split("#", 1)[0],
            kind=kind,
            json_path=json_path,
            original_text=block.get("original_text") or r.get("original_text") or "",
            new_text=r.get("new_text") or "",
        ))
    return "", patches


def _row_of(rows, sid):
    for r in rows:
        if r["id"] == sid:
            return r
    return {"id": sid, "source_ref": "?"}


def _id(r):
    return r["id"] if isinstance(r, dict) else r.suggestion_id


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _new_batch_id():
    return "batch-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv=None):
    try:
        return _main(argv)
    except ApplyError as exc:
        print("apply: %s" % exc, file=sys.stderr)
        return 2


def _main(argv=None):
    ap = argparse.ArgumentParser(
        prog="apply_suggestions.py",
        description="Sonsteng Editor apply-transaction engine (see module docstring).")
    ap.add_argument("--batch-id", default=None, help="apply batch id (default: timestamp).")
    ap.add_argument("--base-url", default=os.environ.get(ENV_API_BASE),
                    help="Worker /edit/v1 base URL (env %s)." % ENV_API_BASE)
    ap.add_argument("--dry-run", action="store_true",
                    help="Non-mutating: lock + clean-tree assert + base_sha + reconcile + "
                         "report the deploy plan. No claim/patch/build/deploy.")
    ap.add_argument("--no-lock", action="store_true", help="(tests only) skip the flock.")
    args = ap.parse_args(argv)

    batch_id = args.batch_id or _new_batch_id()
    token = os.environ.get(ENV_SERVICE_TOKEN)
    deploy_plan_only = os.environ.get(ENV_DEPLOY) != "1"

    lock_cm = contextlib.nullcontext() if args.no_lock else apply_lock()
    with lock_cm:
        assert_clean_tree(REPO_ROOT)
        base_sha = head_sha(REPO_ROOT)
        if args.dry_run:
            print("DRY-RUN — canonical clean, base_sha=%s" % base_sha)
            print("APPLY_DEPLOY=%s -> %s" % (
                os.environ.get(ENV_DEPLOY, "<unset>"),
                "WOULD deploy" if not deploy_plan_only else "build-only (no deploy)"))
            if args.base_url and token:
                client = HttpRpcClient(args.base_url, token)
                rec = client.reconcile()
                print("reconcile: %s" % json.dumps(rec))
            else:
                print("no --base-url/%s -> skipped RPC (offline self-check only)."
                      % ENV_SERVICE_TOKEN)
            print("Would deploy site: bash deploy/deploy-dev.sh %s" % "feat/editor-experience")
            print("Would deploy worker: (cd app/worker && npx wrangler deploy)")
            return 0

        if not args.base_url or not token:
            print("error: live apply needs --base-url and $%s (service token)."
                  % ENV_SERVICE_TOKEN, file=sys.stderr)
            return 2
        client = HttpRpcClient(args.base_url, token)
        pipeline = SubprocessPipeline()
        result = run_apply(client, pipeline, batch_id,
                           deploy_plan_only=deploy_plan_only)
        print(result.digest_md or ("(no digest) reason=%s" % result.reason))
        digest_path = os.path.join(REPO_ROOT, "build", "apply-digest-%s.md" % batch_id)
        with contextlib.suppress(Exception):
            os.makedirs(os.path.dirname(digest_path), exist_ok=True)
            with open(digest_path, "w", encoding="utf-8") as fh:
                fh.write(result.digest_md or "")
        return 0 if result.committed or result.reason.startswith("build_only") \
            or result.reason == "nothing_to_claim" else 1


if __name__ == "__main__":
    sys.exit(main())
