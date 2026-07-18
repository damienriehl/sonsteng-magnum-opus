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
