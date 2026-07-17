#!/usr/bin/env python3
"""Build the Worker's persona injection artifact.

Reads the confidential persona files (data/matters/*/personas/*.json) plus the
m00 test fixture, and the ground-truth facts, and emits a single server-only
bundle at app/worker/personas/personas.generated.json containing:

  - segment_a : the verbatim shared cacheable prefix (Segment A) extracted from
                app/worker/prompts/system-template.md between the BEGIN/END
                markers, so the Worker imports one build-time-frozen string
                instead of re-parsing the .md at runtime.
  - personas  : per-persona INJECTION fields only (identity, disclosure tiers,
                knowledge_boundary, rule_4_2, disposition, narrative fields) —
                the fields prompts.js needs to render Segment B. No @id/@context.
  - fact_map  : out-of-band {persona_id -> {fact_ref -> {topic_label, tier}}}
                for /debrief. topic_label is a short (3-6 word) HUMAN label
                derived here from the fact text — never the fact's content — so
                the debrief can name a missed TOPIC without ever leaking a
                concealed/un-elicited fact.

Design constraints (see docs/plans/...-curriculum-buildout-plan.md item 4):
  * Python 3 STDLIB ONLY (no new deps).
  * MUST tolerate an empty / partial data/matters/ (the matter fleet authors it
    concurrently); it is re-run at ship time. It ALWAYS includes the m00 fixture
    so the Worker is testable immediately.
  * NEVER bundle instructor notes, answer keys, or the full data spine.

Usage:  python3 tools/build_worker_personas.py
"""

import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROMPTS_DIR = os.path.join(REPO_ROOT, "app", "worker", "prompts")
SYSTEM_TEMPLATE = os.path.join(PROMPTS_DIR, "system-template.md")
DEBRIEF_TEMPLATE = os.path.join(PROMPTS_DIR, "debrief-template.md")
CRITIQUE_TEMPLATE = os.path.join(PROMPTS_DIR, "critique-template.md")
FIXTURE_PERSONA = os.path.join(REPO_ROOT, "app", "worker", "test", "fixtures", "persona-m00-client.json")
MATTERS_DIR = os.path.join(REPO_ROOT, "data", "matters")
OUT_PATH = os.path.join(REPO_ROOT, "app", "worker", "personas", "personas.generated.json")

SEGMENT_A_BEGIN = "<!-- ===== BEGIN SEGMENT A (verbatim) ===== -->"
SEGMENT_A_END = "<!-- ===== END SEGMENT A ===== -->"

TIERS = ["volunteered", "revealed_if_asked", "rapport_gated", "concealed", "unknown"]

# Injection whitelist: only these top-level persona fields reach the Worker.
INJECTION_FIELDS = [
    "id",
    "matter_id",
    "identity",
    "background",
    "personality",
    "emotional_state",
    "communication_style",
    "objectives_fears",
    "disposition",
    "interviewable_by",
    "represented_by_counsel",
    "rule_4_2",
    "disclosure",
    "knowledge_boundary",
]

# Curated, leak-safe topic labels for the m00 fixture facts. A topic label names
# the SUBJECT a student could have explored, never the fact's actual content.
CURATED_TOPIC_LABELS = {
    "m00.fact.001": "when and where it happened",
    "m00.fact.002": "the slip in the produce aisle",
    "m00.fact.003": "what she was doing just before",
    "m00.fact.004": "the wrist and knee injuries",
    "m00.fact.005": "any warning sign near the spill",
    "m00.fact.006": "what the liquid was",
    "m00.fact.007": "the store's response after the fall",
    "m00.fact.008": "what a store employee said",
    "m00.fact.009": "the store incident report",
    "m00.fact.010": "whether any video exists",
    "m00.fact.011": "the medical treatment she got",
    "m00.fact.012": "how the injury is healing",
    "m00.fact.013": "the effect on her work and income",
    "m00.fact.014": "any history of prior claims",
    "m00.fact.015": "her worry about prior claims",
}

# Content-FREE neutral fallback labels, keyed by tier. A truncation of raw fact
# text inevitably leaks concealed/un-elicited content (the debrief-oracle risk),
# so any persona without curated labels gets these safe placeholders and a loud
# warning — real matters MUST be curated at ship time (see build output).
NEUTRAL_TIER_LABEL = {
    "volunteered": "a point the client raised",
    "revealed_if_asked": "a topic that was available for the asking",
    "rapport_gated": "a sensitive topic that needed trust first",
    "concealed": "a topic the client was protecting",
    "unknown": "something the client did not know",
}


def extract_between(text, begin, end):
    """Return the bytes strictly between the LAST begin/end markers, trimming
    exactly one leading and one trailing newline (markers may also appear inside
    an implementation-notes comment, so use the last occurrence)."""
    b = text.rindex(begin) + len(begin)
    e = text.rindex(end)
    seg = text[b:e]
    if seg.startswith("\n"):
        seg = seg[1:]
    if seg.endswith("\n"):
        seg = seg[:-1]
    return seg


def read_template(path, begin, end):
    with open(path, "r", encoding="utf-8") as f:
        return extract_between(f.read(), begin, end)


def derive_topic_label(fact_ref, text, tier):
    """Curated override if known; else a CONTENT-FREE neutral placeholder keyed by
    tier. Returns (label, is_fallback). Truncating raw fact text would leak the
    very content the debrief-oracle rule forbids, so un-curated facts get a safe
    generic label and are reported for ship-time curation."""
    if fact_ref in CURATED_TOPIC_LABELS:
        return CURATED_TOPIC_LABELS[fact_ref], False
    return NEUTRAL_TIER_LABEL.get(tier, "an unexplored topic"), True


def build_persona(persona):
    """Split one persona JSON into (injection_obj, fact_map_entries, fallback_refs)."""
    injection = {k: persona[k] for k in INJECTION_FIELDS if k in persona}
    fact_entries = {}
    fallback_refs = []
    disclosure = persona.get("disclosure", {})
    for tier in TIERS:
        for item in disclosure.get(tier, []):
            fref = item.get("fact_ref")
            if not fref:
                continue
            label, is_fallback = derive_topic_label(fref, item.get("text", ""), tier)
            if is_fallback:
                fallback_refs.append(fref)
            fact_entries[fref] = {"topic_label": label, "tier": tier}
    return injection, fact_entries, fallback_refs


def collect_rubrics():
    """Scan data/matters/*/rubric.json -> {matter_id: rubric_obj}. Rubrics are
    student-facing (not confidential) and are needed server-side by /critique.
    Tolerates a missing/empty data/matters tree."""
    rubrics = {}
    if not os.path.isdir(MATTERS_DIR):
        return rubrics
    for matter in sorted(os.listdir(MATTERS_DIR)):
        rpath = os.path.join(MATTERS_DIR, matter, "rubric.json")
        if not os.path.isfile(rpath):
            continue
        try:
            with open(rpath, "r", encoding="utf-8") as f:
                rubric = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        mid = rubric.get("matter_id")
        if not mid and isinstance(rubric.get("id"), str):
            mid = rubric["id"].split(".")[0]
        if mid:
            rubrics[mid] = rubric
    return rubrics


def collect_persona_paths():
    """m00 fixture first, then every data/matters/*/personas/*.json (sorted).
    Tolerates a missing/empty data/matters tree."""
    paths = [FIXTURE_PERSONA]
    if os.path.isdir(MATTERS_DIR):
        for matter in sorted(os.listdir(MATTERS_DIR)):
            pdir = os.path.join(MATTERS_DIR, matter, "personas")
            if not os.path.isdir(pdir):
                continue
            for fn in sorted(os.listdir(pdir)):
                if fn.endswith(".json"):
                    paths.append(os.path.join(pdir, fn))
    return paths


def main():
    segment_a = read_template(
        SYSTEM_TEMPLATE, SEGMENT_A_BEGIN, SEGMENT_A_END)
    debrief_template = read_template(
        DEBRIEF_TEMPLATE,
        "<!-- ===== BEGIN DEBRIEF PROMPT ===== -->",
        "<!-- ===== END DEBRIEF PROMPT ===== -->")
    critique_template = read_template(
        CRITIQUE_TEMPLATE,
        "<!-- ===== BEGIN CRITIQUE PROMPT ===== -->",
        "<!-- ===== END CRITIQUE PROMPT ===== -->")

    personas = {}
    fact_map = {}
    skipped = []
    fallback_by_persona = {}

    for path in collect_persona_paths():
        if not os.path.isfile(path):
            skipped.append((path, "missing"))
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                persona = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append((path, str(exc)))
            continue
        pid = persona.get("id")
        if not pid:
            skipped.append((path, "no id field"))
            continue
        if pid in personas:
            skipped.append((path, "duplicate id %s" % pid))
            continue
        injection, fact_entries, fallback_refs = build_persona(persona)
        personas[pid] = injection
        fact_map[pid] = fact_entries
        if fallback_refs:
            fallback_by_persona[pid] = fallback_refs

    bundle = {
        "schema_version": "1.0.0",
        "generated_by": "tools/build_worker_personas.py",
        "note": (
            "BUILD ARTIFACT — do not edit by hand. Server-only: contains "
            "confidential disclosure facts. Never ship as a public/static asset."
        ),
        "segment_a": segment_a,
        "debrief_template": debrief_template,
        "critique_template": critique_template,
        "personas": personas,
        "fact_map": fact_map,
        "rubrics": collect_rubrics(),
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
        f.write("\n")

    total_facts = sum(len(v) for v in fact_map.values())
    print("persona bundle written: %s" % os.path.relpath(OUT_PATH, REPO_ROOT))
    print("  personas   : %d" % len(personas))
    print("  fact_map   : %d facts across %d personas" % (total_facts, len(fact_map)))
    print("  rubrics    : %d" % len(bundle["rubrics"]))
    print("  segment_a  : %d chars" % len(segment_a))
    if skipped:
        print("  skipped    : %d" % len(skipped))
        for path, why in skipped:
            print("    - %s (%s)" % (os.path.relpath(path, REPO_ROOT), why))
    if fallback_by_persona:
        n = sum(len(v) for v in fallback_by_persona.values())
        print("")
        print("  WARNING: %d fact(s) across %d persona(s) used CONTENT-FREE "
              "placeholder topic labels." % (n, len(fallback_by_persona)))
        print("           Curate leak-safe topic_labels before ship (debrief-oracle rule):")
        for pid, refs in sorted(fallback_by_persona.items()):
            print("           - %s: %s" % (pid, ", ".join(refs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
