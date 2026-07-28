#!/usr/bin/env python3
r"""editor_consistency.py — the Inconsistency checker (U10, R12 of the
word-like-editing plan, docs/plans/2026-07-28-002).

WHY THIS EXISTS: facts are the upstream surface (U5). Narrative that no longer
matches the facts must be FLAGGED — never auto-corrected in either direction.
Every flag names BOTH repair routes (edit the Fact, or edit the paragraph) and
rides the EXISTING review surface: each flag is filed as a COMMENT on the
affected block via POST {EDIT_API_BASE}/system-suggest (origin=ai_rewrite,
comment only, no new_text) — exactly like tools/editorial_pass.py. Zero new
client-side UI code.

TWO FLAG CLASSES, visibly distinct in the comment text:
  * "Fact check — ..."  STALE VALUE. Deterministic — no model involved.
  * "AI guess — ..."    CONTRADICTION. Model-assisted, lower confidence; the
                        prefix exists so an 83-year-old never mistakes a guess
                        for a fact. Skipped entirely with --no-model, and
                        degrades gracefully (CLI missing/timeout -> log, never
                        crash) exactly like editorial_pass.

THE DETERMINISTIC PASS (exact rules):
  With --since <git range> ("BASE..HEAD" or a base rev):
    1. Load the matter's fact rows (mirror of build_site._fact_rows: matter.json
       top-level scalars off the deny list, custom_facts, and business.json
       intake/conflicts_check/engagement scalar leaves) at BASE (git show) and
       at the worktree.
    2. A fact whose value CHANGED (same json_path, old != new, len(old) >= 4,
       and old is not a substring of new) is a candidate.
    3. Every PROSE block of the same matter (editor map blocks whose source_ref
       starts data/matters/<slug>/, kind == "prose", deduped by source_ref)
       whose original_text still contains the OLD literal -> one STALE-VALUE
       flag on that block.
  Without --since (internally-consistent fallback; dates + money ONLY):
    1. Only facts whose whole value is date-shaped (YYYY-MM-DD) or
       money-shaped ($1,234.56) participate.
    2. A prose block is flagged for a fact only when the correspondence is
       unambiguous: the fact's label has AT LEAST TWO distinctive words (>= 3
       chars, from the json_path leaf, e.g. intake_date -> "intake", "date";
       a generic one-word label like as_of_date -> "date" never participates),
       ALL of those words appear in the block, the block contains EXACTLY ONE
       distinct literal of the same shape, that literal differs from the
       fact's value, and NO OTHER same-shape fact of the matter also has all
       its label words in the block.
  Conservative by design: an untouched matter yields ZERO flags (the plan's
  verification), because prose that restates the CURRENT value never differs.

IDEMPOTENCY: the suggestion id is derived from (source_ref, fact_path,
old_literal) — re-runs re-file the same ids and the Worker dedupes; no
duplicate flags ever accumulate.

NOT WIRED into the direct-apply daemon tick — that wiring lands with U8's
polish. Runnable standalone:

    python3 tools/editor_consistency.py --matter m03-tort-meridian \
        [--since BASE..HEAD] [--dry-run] [--no-model]

Python 3, stdlib only. The git reads, map load, facts load, CLI call and flag
filing are all injectable (see tools/tests/test_editor_consistency.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editorial_pass as ep  # noqa: E402  (reuse run_cli/parse_flags/file_flag)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDITOR_MAP_PATH = os.path.join(REPO_ROOT, "build", "editor-map.generated.json")

FLAG_ORIGIN = "ai_rewrite"  # SYSTEM proposer's sanctioned model/tool provenance

# Both repair routes, in plain words, on EVERY flag (R12: repair offered in
# either direction; the checker never auto-corrects either side).
REPAIR_ROUTES = ("Fix by editing the Fact on the Facts page, or by editing "
                 "this paragraph — whichever is right.")

FACT_PREFIX = "Fact check — "   # deterministic (stale value)
GUESS_PREFIX = "AI guess — "    # model-assisted (contradiction)

# --- mirrored from tools/build_site.py (keep in lockstep; build_site is the
# --- canonical copy but is too heavy to import standalone) -------------------
FACTS_DENY = {"id", "@id", "@context", "schema_version", "slug", "shape",
              "tier", "jurisdiction", "client_id", "matter_id", "fee_type",
              "letter_md"}
FACTS_BUSINESS_SECTIONS = ("intake", "conflicts_check", "engagement")

# Mechanical "shapes" for the fallback check — dates and money ONLY.
SHAPE_RES = {
    # Dates appear in TWO forms and both must be seen. The facts carry ISO
    # ("2025-02-13"); the prose almost always restates them the way a lawyer
    # writes them ("February 13, 2025" — 359 blocks in the corpus). An
    # ISO-only pattern made this checker structurally incapable of a true
    # flag: an adversarial pass perturbed all 80 participating facts and got
    # ZERO flags. Matching both forms is what makes the check real.
    "date": re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b"
        r"|\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},\s+\d{4}\b"),
    "money": re.compile(r"\$\d[\d,]*(?:\.\d{2})?"),
}

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def date_forms(value):
    """Every written form one date fact can take in the prose. An ISO fact
    value yields itself plus the long form; anything else yields itself."""
    forms = {str(value)}
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value).strip())
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            forms.add("%s %d, %d" % (_MONTHS[mo - 1], d, y))
    return forms


def literal_matches_fact(literal, fact_value, shape):
    """Does a literal found in prose denote this fact's value? Dates compare
    across written forms; everything else is exact."""
    if shape == "date":
        return literal in date_forms(fact_value)
    return literal == str(fact_value)


def _fact_label(path):
    """Human label from a dotted path: 'intake.intake_date' -> 'Intake date'."""
    leaf = path.split(".")[-1]
    return leaf.replace("_", " ").strip().capitalize()


def _label_words(path):
    return [w for w in _fact_label(path).lower().split() if len(w) >= 3]


# --------------------------------------------------------------------------- #
# 1) Facts — rows for one matter, from parsed JSON (pure) or files/git
# --------------------------------------------------------------------------- #
def fact_rows_from_data(matter_rel, matter, business_rel=None, business=None):
    """[(relpath, json_path, value)] — the mirror of build_site._fact_rows,
    operating on already-parsed dicts so old (git) and new (worktree) revisions
    go through the identical extraction."""
    rows = []
    matter = matter or {}
    for k, v in matter.items():
        if k.startswith("_") or k in FACTS_DENY or isinstance(v, (dict, list)):
            continue
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            rows.append((matter_rel, k, v))
    for k in sorted((matter.get("custom_facts") or {})):
        rows.append((matter_rel, "custom_facts.%s" % k, matter["custom_facts"][k]))
    if business_rel and isinstance(business, dict):
        for section in FACTS_BUSINESS_SECTIONS:
            sec = business.get(section)
            if not isinstance(sec, dict):
                continue
            for k, v in sec.items():
                if k in FACTS_DENY or isinstance(v, (dict, list)):
                    continue
                if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    rows.append((business_rel, "%s.%s" % (section, k), v))
    return rows


def _matter_paths(slug):
    matter_rel = "data/matters/%s/matter.json" % slug
    business_rel = "data/matters/%s/business/business.json" % slug
    return matter_rel, business_rel


def load_fact_rows(repo_root, slug):
    """Current (worktree) fact rows for one matter."""
    matter_rel, business_rel = _matter_paths(slug)
    matter = _read_json_file(os.path.join(repo_root, matter_rel))
    business = _read_json_file(os.path.join(repo_root, business_rel))
    return fact_rows_from_data(matter_rel, matter or {}, business_rel, business)


def load_fact_rows_at(repo_root, slug, rev, timeout=60):
    """Fact rows at a git revision (via `git show rev:path`; a file absent at
    that rev contributes no rows)."""
    matter_rel, business_rel = _matter_paths(slug)
    matter = _git_show_json(repo_root, rev, matter_rel, timeout=timeout)
    business = _git_show_json(repo_root, rev, business_rel, timeout=timeout)
    return fact_rows_from_data(matter_rel, matter or {}, business_rel, business)


def _read_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _rev_exists(repo_root, rev, timeout=30):
    """Does this revision resolve? Used to fail loudly on a typo'd --since."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--verify", "--quiet",
             "%s^{commit}" % rev],
            check=False, shell=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _git_show_json(repo_root, rev, relpath, timeout=60):
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "show", "%s:%s" % (rev, relpath)],
            check=False, shell=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        # Never crash the run: the module's contract is degrade-and-report.
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def base_rev(since):
    """The BASE side of a --since selector: 'A..B'/'A...B' -> 'A'; a bare rev
    is itself the base (compared against the worktree)."""
    if not since:
        return None
    for sep in ("...", ".."):
        if sep in since:
            return since.split(sep, 1)[0] or None
    return since


def diff_fact_rows(old_rows, new_rows, min_len=4):
    """Changed fact values old -> new. Pure. Returns
    [{fact_path: 'relpath#json_path', label, old, new}] for paths present in
    BOTH revisions whose stringified values differ, where len(old) >= min_len
    and old is NOT a substring of new (a containing block would then match both
    and the stale test would false-flag prose carrying the new value)."""
    old_by = {("%s#%s" % (r, p)): (p, str(v)) for r, p, v in old_rows}
    changed = []
    for r, p, v in new_rows:
        key = "%s#%s" % (r, p)
        if key not in old_by:
            continue
        old = old_by[key][1]
        new = str(v)
        if old == new or len(old) < min_len or old in new:
            continue
        changed.append({"fact_path": key, "label": _fact_label(p),
                        "old": old, "new": new})
    return changed


# --------------------------------------------------------------------------- #
# 2) Blocks — the matter's prose blocks from the editor map (pure)
# --------------------------------------------------------------------------- #
def load_map_bundle(path=None):
    path = path or EDITOR_MAP_PATH
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def blocks_for_matter(bundle, slug):
    """The matter's PROSE blocks, deduped by source_ref (a block can render on
    several pages — one flag per block, not per page). json_scalar blocks are
    excluded: they ARE the facts, not restatements of them."""
    prefix = "data/matters/%s/" % slug
    seen, out = set(), []
    for blocks in (bundle.get("pages") or {}).values():
        for b in blocks or []:
            ref = b.get("source_ref") or ""
            if not ref.startswith(prefix) or b.get("kind") != "prose":
                continue
            if ref in seen:
                continue
            seen.add(ref)
            out.append(b)
    return out


# --------------------------------------------------------------------------- #
# 3) Deterministic pass — stale values (pure)
# --------------------------------------------------------------------------- #
def stale_value_flags(changed, blocks):
    """--since mode: prose blocks still carrying a CHANGED fact's OLD literal."""
    flags = []
    for ch in changed:
        for b in blocks:
            text = b.get("original_text") or ""
            if ch["old"] not in text:
                continue
            msg = ('%sthis paragraph still says "%s", but the Fact \'%s\' is '
                   'now "%s". %s'
                   % (FACT_PREFIX, ch["old"], ch["label"], ch["new"],
                      REPAIR_ROUTES))
            flags.append({"source_ref": b["source_ref"],
                          "fact_path": ch["fact_path"],
                          "old_literal": ch["old"],
                          "severity": "consistency",
                          "message": msg})
    return flags


def value_shape(sval):
    for name, rx in SHAPE_RES.items():
        if rx.fullmatch(sval):
            return name
    return None


def fallback_mismatch_flags(fact_rows, blocks):
    """No-history mode: dates + money only, unambiguous correspondence only
    (exact rules in the module docstring). Conservative: zero false flags on an
    untouched matter beats coverage.

    KNOW ITS CEILING — measured, not assumed. Correspondence here rests on the
    fact's LABEL words appearing in the same paragraph as the literal, and
    real prose does not write that way: it says "On February 13, 2025, I met
    with the client", never "intake date". Perturbing every participating fact
    in the live corpus yields ZERO flags. So this mode is a floor, not a
    check: it exists to be silent-and-safe when no history is available, and
    run() says so out loud rather than printing a clean report that reads like
    an all-clear. The mode with real catch power is --since, which compares
    the OLD literal against the prose and needs no label correspondence."""
    shaped = []
    for relpath, path, value in fact_rows:
        sval = str(value)
        shape = value_shape(sval)
        # A one-word label ("date") is generic enough to match unrelated prose
        # (an untouched exhibit mentioning any other date would false-flag) —
        # only facts with a distinctive multi-word label participate.
        if shape and len(_label_words(path)) >= 2:
            shaped.append({"fact_path": "%s#%s" % (relpath, path),
                           "label": _fact_label(path),
                           "words": _label_words(path),
                           "shape": shape, "value": sval})
    flags = []
    for b in blocks:
        text = b.get("original_text") or ""
        low = text.lower()
        for shape, rx in SHAPE_RES.items():
            literals = sorted(set(rx.findall(text)))
            if len(literals) != 1:
                continue  # none, or ambiguous which literal maps to which fact
            lit = literals[0]
            matching = [f for f in shaped if f["shape"] == shape
                        and f["words"] and all(w in low for w in f["words"])]
            if len(matching) != 1:
                continue  # no labelled fact here, or more than one -> ambiguous
            f = matching[0]
            if literal_matches_fact(lit, f["value"], shape):
                continue  # prose agrees with the current fact (any written form)
            msg = ('%sthis paragraph says "%s" where the Fact \'%s\' says '
                   '"%s". %s'
                   % (FACT_PREFIX, lit, f["label"], f["value"], REPAIR_ROUTES))
            flags.append({"source_ref": b["source_ref"],
                          "fact_path": f["fact_path"],
                          "old_literal": lit,
                          "severity": "consistency",
                          "message": msg})
    return flags


# --------------------------------------------------------------------------- #
# 4) Model pass — contradictions (optional, degrades gracefully)
# --------------------------------------------------------------------------- #
MODEL_BLOCK_SAMPLE = 40      # at most this many prose blocks per matter
MODEL_MAX_CHARS = 50000


def build_contradiction_prompt(slug, fact_rows, blocks,
                               sample=MODEL_BLOCK_SAMPLE,
                               max_chars=MODEL_MAX_CHARS):
    facts_lines = "\n".join("  %s#%s = %s" % (r, p, json.dumps(str(v)))
                            for r, p, v in fact_rows)
    body_parts = []
    for b in blocks[:sample]:
        body_parts.append("[%s]\n%s" % (b["source_ref"],
                                        (b.get("original_text") or "").strip()))
    body = "\n\n".join(body_parts)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n[...blocks truncated for length...]"
    return (
        "You are a consistency checker for the Sonsteng legal-practicum matter "
        "'%s'. Below are the matter's STATED FACTS (the single source of truth) "
        "and a sample of its prose blocks, each preceded by its [source_ref].\n"
        "Report ONLY CONTRADICTIONS: a prose block asserting something the "
        "stated facts rule out. Do NOT report restatements, paraphrases, "
        "omissions, style issues, or anything the facts merely fail to "
        "mention. Be conservative — when unsure, stay silent.\n\n"
        "Respond with ONLY a JSON object, no prose, of the form:\n"
        '{"flags": [{"source_ref": "data/...", "message": "..."}]}\n'
        'If there are no contradictions, respond with {"flags": []}.\n\n'
        "=== STATED FACTS ===\n%s\n\n=== PROSE BLOCKS ===\n%s"
        % (slug, facts_lines, body))


def model_contradiction_flags(slug, fact_rows, blocks, cli_runner=None):
    """Run the headless CLI (injectable) and normalise its flags. Returns
    (flags, degraded_reason). Every model flag's message BEGINS with
    'AI guess — ' so a guess is never mistaken for a fact; a malformed
    response degrades to zero flags, a missing/timed-out CLI to a reason —
    never an exception."""
    if not blocks:
        return [], None
    cli_runner = cli_runner or (lambda prompt: ep.run_cli(prompt))
    prompt = build_contradiction_prompt(slug, fact_rows, blocks)
    ok, raw, degraded = cli_runner(prompt)
    if not ok:
        return [], degraded or "cli_failed"
    known = {b["source_ref"] for b in blocks}
    flags = []
    for f in ep.parse_flags(raw):  # tolerant envelope/prose parsing, never raises
        if f["source_ref"] not in known:
            continue  # the model may not invent refs; server re-validates too
        msg = f["message"].strip()
        if msg.startswith(GUESS_PREFIX):
            msg = msg[len(GUESS_PREFIX):]
        message = "%s%s %s" % (GUESS_PREFIX, msg.rstrip(), REPAIR_ROUTES)
        flags.append({"source_ref": f["source_ref"],
                      "fact_path": "model:contradiction",
                      # NOT the model's wording: an id derived from
                    # nondeterministic prose mints a fresh id every rerun and
                    # duplicate AI-guess comments pile up on the same block.
                    # One AI-guess slot per block instead.
                    "old_literal": "",
                      "severity": "contradiction",
                      "message": message})
    return flags, None


# --------------------------------------------------------------------------- #
# 5) Filing — idempotent ids, comment-only payloads
# --------------------------------------------------------------------------- #
def consistency_flag_id(source_ref, fact_path, old_literal):
    """Deterministic id from (source_ref, fact_path, old_literal) — the same
    finding re-files under the SAME id on every run (the Worker's uuid ceiling
    is [a-zA-Z0-9_-]{8,64}), so re-runs never duplicate flags."""
    h = hashlib.sha256(("%s|%s|%s" % (source_ref, fact_path, old_literal))
                       .encode("utf-8")).hexdigest()[:24]
    return ("cflag-%s" % h)[:64]


def flag_payload(flag):
    """The /system-suggest body: a COMMENT on the block (no new_text),
    origin=ai_rewrite — identical surface to editorial_pass flags."""
    return {
        "id": consistency_flag_id(flag["source_ref"], flag["fact_path"],
                                  flag["old_literal"]),
        "origin": FLAG_ORIGIN,
        "source_ref": flag["source_ref"],
        "comment": flag["message"][:16000],
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ConsistencyResult:
    matters: list
    stale_flags: list
    model_flags: list
    filed: int
    model_degraded: str
    payloads: list


def run(*, api_base=None, token=None, matter=None, since=None, dry_run=False,
        no_model=False, repo_root=REPO_ROOT, map_loader=None,
        facts_loader=None, old_facts_loader=None, cli_runner=None,
        flag_filer=None, out=None):
    """One checker pass. Every side effect is injectable for tests."""
    out = out or sys.stdout
    map_loader = map_loader or load_map_bundle
    facts_loader = facts_loader or (lambda slug: load_fact_rows(repo_root, slug))
    flag_filer = flag_filer or (lambda payload: ep.file_flag(api_base, token, payload))
    base = base_rev(since)
    # A --since that cannot be honoured must fail LOUDLY. Falling through to
    # the (much weaker) no-history mode printed a clean report for a typo'd
    # base — the worst failure this tool can have, because "clean" is exactly
    # what it says when everything is fine. Only checked when the real git
    # loader will run (an injected loader owns its own revisions).
    if since and old_facts_loader is None:
        if not base:
            print("[consistency] --since %r has no base revision — refusing to "
                  "run (a bad selector must never look like a clean corpus)."
                  % since, file=out)
            return ConsistencyResult([], [], [], 0, "bad_since", [])
        if not _rev_exists(repo_root, base):
            print("[consistency] --since base %r does not resolve — refusing "
                  "to run." % base, file=out)
            return ConsistencyResult([], [], [], 0, "bad_since", [])
    old_facts_loader = old_facts_loader or (
        lambda slug: load_fact_rows_at(repo_root, slug, base))

    try:
        bundle = map_loader()
    except (OSError, ValueError) as exc:
        print("[consistency] editor map unavailable (%s) — nothing checked." % exc,
              file=out)
        return ConsistencyResult([], [], [], 0, "", [])

    if not since:
        print("[consistency] NO --since: running the limited no-history check. "
              "It can only see a date/money literal in a paragraph that also "
              "names the fact's label, which real prose rarely does — treat a "
              "clean result here as 'not checked', NOT as 'consistent'. Pass "
              "--since <rev> for the real check.", file=out)

    slugs = [matter] if matter else sorted(
        {(b.get("source_ref") or "").split("/")[2]
         for blocks in (bundle.get("pages") or {}).values() for b in blocks or []
         if (b.get("source_ref") or "").startswith("data/matters/")})

    stale, model_flags, degraded = [], [], ""
    for slug in slugs:
        blocks = blocks_for_matter(bundle, slug)
        if not blocks:
            continue
        facts = facts_loader(slug)
        if base:
            changed = diff_fact_rows(old_facts_loader(slug), facts)
            stale.extend(stale_value_flags(changed, blocks))
        else:
            stale.extend(fallback_mismatch_flags(facts, blocks))
        if not no_model:
            mf, reason = model_contradiction_flags(slug, facts, blocks,
                                                   cli_runner=cli_runner)
            model_flags.extend(mf)
            if reason:
                degraded = reason
                print("[consistency] model pass degraded for %s (%s) — "
                      "deterministic flags unaffected." % (slug, reason), file=out)

    all_flags = stale + model_flags
    payloads = [flag_payload(f) for f in all_flags]

    if dry_run:
        print("[consistency] DRY-RUN: %d stale-value, %d AI-guess flag(s); "
              "would file, files nothing." % (len(stale), len(model_flags)),
              file=out)
        for p in payloads:
            print("  would-file: %s -> %s" % (p["source_ref"], p["comment"][:100]),
                  file=out)
        return ConsistencyResult(slugs, stale, model_flags, 0, degraded, payloads)

    filed = 0
    for p in payloads:
        fok, reason = flag_filer(p)
        if fok:
            filed += 1
        else:
            print("[consistency] flag NOT filed for %s (%s) — skipped."
                  % (p["source_ref"], reason), file=out)
    print("[consistency] %d matter(s): %d stale-value, %d AI-guess flag(s); "
          "%d filed." % (len(slugs), len(stale), len(model_flags), filed),
          file=out)
    return ConsistencyResult(slugs, stale, model_flags, filed, degraded, payloads)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sonsteng Inconsistency checker "
                                 "(facts vs narrative; flags only, never edits).")
    ap.add_argument("--matter", default=None,
                    help="Restrict to one matter slug (e.g. m03-tort-meridian).")
    ap.add_argument("--since", default=None,
                    help="Git range (BASE..HEAD) or base rev: diff facts to find "
                         "changed values; prose carrying the OLD value is flagged.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan + print; file nothing.")
    ap.add_argument("--no-model", action="store_true",
                    help="Deterministic pass only (skip the AI contradiction pass).")
    args = ap.parse_args(argv)

    api_base = os.environ.get(ep.ENV_API_BASE)
    token = os.environ.get(ep.ENV_SERVICE_TOKEN)
    if not args.dry_run and not api_base:
        print("[consistency] %s unset — running as --dry-run." % ep.ENV_API_BASE)
        args.dry_run = True

    run(api_base=api_base, token=token, matter=args.matter, since=args.since,
        dry_run=args.dry_run, no_model=args.no_model)
    return 0  # degradation is a clean, non-fatal exit (same discipline as editorial)


if __name__ == "__main__":
    raise SystemExit(main())
