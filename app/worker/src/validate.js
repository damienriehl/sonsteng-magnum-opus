// validate.js — lightweight STRUCTURAL validation of the evaluator model output
// against data/schemas/debrief.scorecard.schema.json and critique.scorecard.schema.json.
// A model can return malformed JSON or drift the shape; we reject anything that
// does not conform BEFORE it reaches the client (return validation_error, never
// raw model text). This is a targeted checker (no external JSON-Schema engine, per
// the repo's no-new-deps rule), covering required keys, types, patterns, enums,
// and ranges that the schemas declare.

const SEMVER = /^\d+\.\d+\.\d+$/;
const MATTER = /^m\d{2}$/;
const PERSONA = /^m\d{2}\.per\.[a-z0-9-]+$/;
const FACT = /^m\d{2}\.fact\.\d{3}$/;
const RUBRIC = /^m\d{2}\.rub$/;
const CRITERION = /^m\d{2}\.rub\.c\d{2}(\.s\d{2})*$/;
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const MEMO_HEADING_IDS = [
  "governing_law",
  "strengths_and_weaknesses_both_sides",
  "issues",
  "suggested_solutions",
  "theory_and_themes",
  "elements_to_prevail",
  "liabilities_and_remedies",
];
const MEMO_HEADING_SET = new Set(MEMO_HEADING_IDS);
const TRIGGERS = new Set([
  "open_ended_invitation", "wellbeing_question", "acknowledged_emotion",
  "no_interruption_streak", "confidentiality_reassurance", "nonjudgmental_response",
  "follow_up_on_hint", "explained_process",
]);

function isStr(v) { return typeof v === "string"; }
function isInt(v) { return typeof v === "number" && Number.isInteger(v); }
function isNum(v) { return typeof v === "number" && Number.isFinite(v); }
function isArr(v) { return Array.isArray(v); }
function isObj(v) { return v && typeof v === "object" && !Array.isArray(v); }

const ALUMNI_ROUTE_FIELDS = new Set([
  "alumni_assessor", "alumni_reviewer", "alumni_recipient",
  "alumni_notification", "alumni_feedback_destination",
]);

// Assessment, debrief, and critique return only to the requesting learner.
// Reject proposed alumni routing explicitly instead of silently ignoring it.
export function validateLearnerResultRequest(body) {
  if (!isObj(body)) return { ok: false, error: "Request body must be an object." };
  if (Object.keys(body).some((key) => ALUMNI_ROUTE_FIELDS.has(key))) {
    return { ok: false, error: "Alumni routing fields are not supported." };
  }
  return { ok: true };
}

function checkRating(v, path, errs) {
  if (!isObj(v)) return errs.push(path + " must be an object");
  if (!isInt(v.score) || v.score < 0 || v.score > 10) errs.push(path + ".score must be int 0-10");
  if (!isStr(v.comment)) errs.push(path + ".comment must be a string");
}

export function validateDebriefScorecard(o) {
  const e = [];
  if (!isObj(o)) return { ok: false, errors: ["root must be an object"] };
  if (!SEMVER.test(o.schema_version || "")) e.push("schema_version invalid");
  if (!MATTER.test(o.matter_id || "")) e.push("matter_id invalid");
  if (!PERSONA.test(o.persona_id || "")) e.push("persona_id invalid");

  const a = o.axis_a;
  if (!isObj(a)) e.push("axis_a missing");
  else {
    if (!isArr(a.facts_elicited) || !a.facts_elicited.every((x) => FACT.test(x)))
      e.push("axis_a.facts_elicited must be fact_ref[]");
    if (!isArr(a.revealed_if_asked_missed) || !a.revealed_if_asked_missed.every(isStr))
      e.push("axis_a.revealed_if_asked_missed must be string[]");
    if (!isArr(a.rapport_gated_unearned)) e.push("axis_a.rapport_gated_unearned must be array");
    else a.rapport_gated_unearned.forEach((x, i) => {
      if (!isObj(x) || !isStr(x.topic) || !TRIGGERS.has(x.trigger_needed))
        e.push(`axis_a.rapport_gated_unearned[${i}] invalid`);
    });
    if (!isArr(a.rule_4_2_flags) || !a.rule_4_2_flags.every(isStr))
      e.push("axis_a.rule_4_2_flags must be string[]");
  }

  const b = o.axis_b;
  if (!isObj(b)) e.push("axis_b missing");
  else for (const k of ["rapport_opening", "listening_t_funnel", "understanding_goals", "explanation_next_steps", "overall_confidence"])
    checkRating(b[k], "axis_b." + k, e);

  if (!isInt(o.ethics_score) || o.ethics_score < -2 || o.ethics_score > 2)
    e.push("ethics_score must be int -2..2");
  if (!isStr(o.narrative)) e.push("narrative must be a string");
  if (!isStr(o.self_reflection_prompt)) e.push("self_reflection_prompt must be a string");

  return { ok: e.length === 0, errors: e };
}

export function validateCritiqueScorecard(o) {
  const e = [];
  if (!isObj(o)) return { ok: false, errors: ["root must be an object"] };
  if (!SEMVER.test(o.schema_version || "")) e.push("schema_version invalid");
  if (!MATTER.test(o.matter_id || "")) e.push("matter_id invalid");
  if (!RUBRIC.test(o.rubric_id || "")) e.push("rubric_id invalid");

  if (!isArr(o.criteria) || o.criteria.length < 1) e.push("criteria must be a non-empty array");
  else o.criteria.forEach((c, i) => {
    if (!isObj(c)) return e.push(`criteria[${i}] must be an object`);
    if (!CRITERION.test(c.criterion_id || "")) e.push(`criteria[${i}].criterion_id invalid`);
    if (!isNum(c.score) || c.score < 0) e.push(`criteria[${i}].score invalid`);
    if (!isNum(c.weight_points) || c.weight_points < 0) e.push(`criteria[${i}].weight_points invalid`);
    if (!isStr(c.evidence)) e.push(`criteria[${i}].evidence must be a string`);
    if (!isStr(c.suggestions)) e.push(`criteria[${i}].suggestions must be a string`);
  });

  if (!isObj(o.total)) e.push("total missing");
  else {
    if (!isNum(o.total.earned) || o.total.earned < 0) e.push("total.earned invalid");
    if (!isNum(o.total.possible) || o.total.possible < 0) e.push("total.possible invalid");
  }
  if (!isStr(o.narrative)) e.push("narrative must be a string");
  if (!isStr(o.revise_resubmit_note)) e.push("revise_resubmit_note must be a string");

  return { ok: e.length === 0, errors: e };
}

// Validate and canonicalize one grader's seven-heading memo output. This check
// intentionally needs the ORIGINAL submission: shape validation alone cannot
// establish that an evidence quote is real. A canonical scorecard is returned
// only after every heading has a unique id, an integer 1-7 score, and at least
// one exact (case-, punctuation-, and whitespace-sensitive) submission span.
// Unknown fields fail closed, so model-authored totals, overall scores, weights,
// and letter grades never become displayable result data.
export function validateMemoScorecard(o, submission, instrument) {
  const e = [];
  if (!isObj(o)) return { ok: false, errors: ["root must be an object"] };
  if (!isStr(submission)) return { ok: false, errors: ["submission must be a string"] };
  if (!isObj(instrument) || instrument.id !== "memo-seven-heading-1-7" ||
      !SEMVER.test(instrument.instrument_version || "") ||
      !SHA256.test(instrument.content_hash || ""))
    return { ok: false, errors: ["canonical instrument invalid"] };

  const rootKeys = new Set([
    "schema_version", "instrument_id", "instrument_version",
    "instrument_content_hash", "headings",
  ]);
  for (const key of Object.keys(o)) {
    if (!rootKeys.has(key)) e.push(`${key} is unexpected`);
  }
  if (o.schema_version !== "1.0.0") e.push("schema_version must be 1.0.0");
  if (o.instrument_id !== "memo-seven-heading-1-7")
    e.push("instrument_id invalid");
  if (!SEMVER.test(o.instrument_version || "") ||
      o.instrument_version !== instrument.instrument_version)
    e.push("instrument_version does not match the canonical instrument");
  if (!SHA256.test(o.instrument_content_hash || "") ||
      o.instrument_content_hash !== instrument.content_hash)
    e.push("instrument_content_hash does not match the canonical instrument");

  const seen = new Set();
  if (!isArr(o.headings) || o.headings.length !== MEMO_HEADING_IDS.length) {
    e.push("headings must contain exactly 7 results");
  } else {
    o.headings.forEach((heading, i) => {
      const path = `headings[${i}]`;
      if (!isObj(heading)) {
        e.push(`${path} must be an object`);
        return;
      }
      const headingKeys = new Set(["heading_id", "evidence_spans", "rationale", "score"]);
      for (const key of Object.keys(heading)) {
        if (!headingKeys.has(key)) e.push(`${path}.${key} is unexpected`);
      }
      if (!MEMO_HEADING_SET.has(heading.heading_id)) {
        e.push(`${path}.heading_id invalid`);
      } else if (seen.has(heading.heading_id)) {
        e.push(`${path}.heading_id duplicate: ${heading.heading_id}`);
      } else {
        seen.add(heading.heading_id);
      }
      if (!isInt(heading.score) || heading.score < 1 || heading.score > 7)
        e.push(`${path}.score must be an integer 1-7`);
      if (!isArr(heading.evidence_spans) || heading.evidence_spans.length < 1) {
        e.push(`${path}.evidence_spans must be a non-empty array`);
      } else {
        const evidenceSeen = new Set();
        heading.evidence_spans.forEach((span, j) => {
          const evidencePath = `${path}.evidence_spans[${j}]`;
          if (!isStr(span) || span.length === 0) {
            e.push(`${evidencePath} must be a non-empty string`);
          } else {
            if (evidenceSeen.has(span)) e.push(`${evidencePath} must be unique`);
            evidenceSeen.add(span);
            if (!submission.includes(span))
              e.push(`${evidencePath} must occur verbatim in the submission`);
          }
        });
      }
      if (!isStr(heading.rationale) || heading.rationale.trim().length === 0)
        e.push(`${path}.rationale must be a non-empty string`);
    });
  }

  for (const headingId of MEMO_HEADING_IDS) {
    if (!seen.has(headingId)) e.push(`heading missing: ${headingId}`);
  }
  if (e.length) return { ok: false, errors: e };

  // Return a fresh canonical object instead of the model-owned object. This is
  // the only shape downstream code may aggregate or display.
  return {
    ok: true,
    errors: [],
    scorecard: {
      schema_version: "1.0.0",
      instrument_id: instrument.id,
      instrument_version: instrument.instrument_version,
      instrument_content_hash: instrument.content_hash,
      headings: o.headings.map((heading) => ({
        heading_id: heading.heading_id,
        evidence_spans: [...heading.evidence_spans],
        rationale: heading.rationale,
        score: heading.score,
      })),
    },
  };
}

// DEBRIEF-ORACLE hard guard (defense-in-depth over the prompt's own rule).
// validateDebriefScorecard only checks that the Axis-A "missed" fields are the
// right SHAPE (string[] / {topic,trigger}[]) — it cannot tell a neutral topic
// LABEL from a leaked concealed-fact TEXT. A weak or jailbroken BYOK evaluator
// model (or a transcript-injected instruction) could therefore turn the scorecard
// into an answer key by echoing un-elicited fact text in those strings. This
// function REBUILDS the two missed-item fields from server-side ground truth so
// every emitted string comes ONLY from fact_map.topic_label, never from model
// output: the miss SET is derived from facts_elicited (already validated as
// fact_refs) versus the persona's own disclosure tiers. Mutates + returns o.
export function redactDebriefOracle(o, persona, factMap) {
  if (!isObj(o) || !isObj(o.axis_a)) return o;
  const disclosure = (persona && persona.disclosure) || {};
  const fm = factMap || {};
  const a = o.axis_a;
  const elicited = new Set(isArr(a.facts_elicited) ? a.facts_elicited.filter(isStr) : []);
  const labelFor = (ref) =>
    (fm[ref] && isStr(fm[ref].topic_label) && fm[ref].topic_label) || "(topic withheld)";

  // Keep the model's chosen trigger ONLY when its topic is already a known, safe
  // topic_label (a well-behaved model); otherwise fall back to the fact's own
  // required trigger. A model topic string that is not a known label is never
  // trusted (it may be leaked fact text) and is discarded.
  const knownLabels = new Set();
  for (const tier of ["rapport_gated", "revealed_if_asked", "volunteered", "concealed", "unknown"]) {
    for (const it of disclosure[tier] || []) {
      if (isObj(it) && isStr(it.fact_ref)) knownLabels.add(labelFor(it.fact_ref));
    }
  }
  const modelTrigByLabel = {};
  if (isArr(a.rapport_gated_unearned)) {
    for (const r of a.rapport_gated_unearned) {
      if (isObj(r) && isStr(r.topic) && knownLabels.has(r.topic) && TRIGGERS.has(r.trigger_needed))
        modelTrigByLabel[r.topic] = r.trigger_needed;
    }
  }

  const revealedMissed = [];
  for (const it of disclosure.revealed_if_asked || []) {
    if (isObj(it) && isStr(it.fact_ref) && !elicited.has(it.fact_ref)) revealedMissed.push(labelFor(it.fact_ref));
  }

  const unearned = [];
  for (const it of disclosure.rapport_gated || []) {
    if (!isObj(it) || !isStr(it.fact_ref) || elicited.has(it.fact_ref)) continue;
    const label = labelFor(it.fact_ref);
    const derived = isArr(it.requires) ? it.requires.find((t) => TRIGGERS.has(t)) : null;
    unearned.push({ topic: label, trigger_needed: modelTrigByLabel[label] || derived || "follow_up_on_hint" });
  }

  a.revealed_if_asked_missed = revealedMissed;
  a.rapport_gated_unearned = unearned;
  return o;
}

// ---- C1 fail-closed leak scan (defense-in-depth over the free-text fields) ---
// redactDebriefOracle owns only the two Axis-A "missed" fields (it rebuilds them
// from ground truth). It does NOT touch the model-authored FREE-TEXT fields
// (narrative, self_reflection_prompt, axis_b.*.comment, axis_a.rule_4_2_flags),
// which rest solely on the evaluator prompt's own oracle rule. A jailbroken or
// transcript-injected evaluator that pastes un-elicited concealed/rapport-gated
// fact TEXT into those fields would turn the scorecard into an answer key.
// detectDebriefOracleLeak scans exactly those fields and, on a hit, lets the
// handler FAIL CLOSED (reject the scorecard with the same retryable
// validation_error used for malformed model output) rather than ship the leak.
//
// Scope is deliberately narrow to avoid false positives:
//  - Only CONCEALED + RAPPORT-GATED fact text is treated as answer-key material.
//  - Facts the model reports as elicited (facts_elicited — the same allowlist
//    redactDebriefOracle uses) are carved out: a legitimately surfaced fact may
//    be discussed in the narrative without tripping the guard.
//  - Matching is on NORMALIZED text (NFKC + Cyrillic-homoglyph fold + zero-width
//    strip + whitespace collapse), mirroring the offline red-team's fold(), so
//    cosmetic obfuscation (homoglyphs, spacing, fences) cannot slip a leak past.
//  - A fragment must clear LEAK_MIN_FOLD folded chars to count, so short/common
//    phrases in legitimate pedagogy prose can never trigger a false reject; secret
//    fact TEXT is full descriptive sentences (folded ~60-180 chars here), so a run
//    that long matching an un-elicited secret is a genuine leak, not overlap.
const LEAK_HOMOGLYPH = {
  "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
  "х": "x", "ѕ": "s", "і": "i", "ј": "j", "һ": "h",
};
function foldLeak(s) {
  let out = String(s == null ? "" : s).normalize("NFKC").toLowerCase();
  out = out.replace(/[аеорсхѕіјһ]/g, (c) => LEAK_HOMOGLYPH[c] || c);
  return out.replace(/[​‌‍⁠﻿]/g, "").replace(/\s+/g, "");
}
const LEAK_MIN_FOLD = 24;

// Return the offending field name (truthy) on a detected leak, or null if clean.
export function detectDebriefOracleLeak(o, persona, factMap) {
  if (!isObj(o) || !isObj(o.axis_a)) return null;
  const disclosure = (persona && persona.disclosure) || {};
  const a = o.axis_a;
  const elicited = new Set(isArr(a.facts_elicited) ? a.facts_elicited.filter(isStr) : []);

  // Build the set of un-elicited concealed/gated fact fragments to look for. Each
  // secret contributes its whole folded text AND each of its folded sentences (so
  // a partial paste of a multi-sentence secret is still caught), keeping only
  // fragments long enough to be distinctive.
  const needles = [];
  for (const tier of ["concealed", "rapport_gated"]) {
    for (const it of disclosure[tier] || []) {
      if (!isObj(it) || !isStr(it.text)) continue;
      if (isStr(it.fact_ref) && elicited.has(it.fact_ref)) continue; // legitimately elicited: carve out
      for (const frag of [it.text, ...it.text.split(/[.?!—]+/)]) {
        const f = foldLeak(frag);
        if (f.length >= LEAK_MIN_FOLD) needles.push(f);
      }
    }
  }
  if (needles.length === 0) return null;

  // The free-text fields the guard does NOT rebuild.
  const fields = [];
  if (isStr(o.narrative)) fields.push(["narrative", o.narrative]);
  if (isStr(o.self_reflection_prompt)) fields.push(["self_reflection_prompt", o.self_reflection_prompt]);
  const b = o.axis_b;
  if (isObj(b)) {
    for (const k of ["rapport_opening", "listening_t_funnel", "understanding_goals", "explanation_next_steps", "overall_confidence"]) {
      if (isObj(b[k]) && isStr(b[k].comment)) fields.push(["axis_b." + k + ".comment", b[k].comment]);
    }
  }
  if (isArr(a.rule_4_2_flags)) {
    a.rule_4_2_flags.forEach((f, i) => { if (isStr(f)) fields.push(["axis_a.rule_4_2_flags[" + i + "]", f]); });
  }

  for (const [field, text] of fields) {
    const h = foldLeak(text);
    for (const n of needles) {
      if (h.includes(n)) return field;
    }
  }
  return null;
}

// Best-effort extraction of a single JSON object from model text (tolerates an
// accidental code fence, though the prompts forbid one). Returns parsed or null.
export function parseModelJson(text) {
  if (!isStr(text)) return null;
  let t = text.trim();
  const fence = t.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/);
  if (fence) t = fence[1].trim();
  try { return JSON.parse(t); } catch {}
  const s = t.indexOf("{");
  const ei = t.lastIndexOf("}");
  if (s >= 0 && ei > s) {
    try { return JSON.parse(t.slice(s, ei + 1)); } catch {}
  }
  return null;
}
