// offline-redteam-redaction.test.js — WP8, component 1 (deterministic).
//
// OFFLINE PARTIAL SUBSTITUTE for the LIVE red-team harness (test/redteam.mjs),
// which cannot run without a provider key (BYOK-forever, q4). This file exercises
// the ONE server-side redaction / leak-stripping path in the Worker —
//   src/validate.js :: redactDebriefOracle
// — by feeding it CRAFTED SYNTHETIC evaluator-model outputs (as a jailbroken or
// transcript-injected model would emit) and asserting that un-elicited persona
// secret TEXT never survives in the fields the guard owns, while legitimate
// content is not over-redacted.
//
// The secret material is derived from the REAL shipped persona bundle
// (personas.generated.json) — actual concealed / rapport-gated fact TEXT for the
// demo matter m00 and a second matter — not invented placeholders. The point is
// the real strings a leak would expose.
//
// SCOPE of the guard (verified below, and documented in
// docs/evidence/EP-2026-07-19-offline-redteam.md): redactDebriefOracle REBUILDS
// axis_a.revealed_if_asked_missed and axis_a.rapport_gated_unearned wholesale from
// server ground truth. Because it rebuilds (rather than string-matches), it is
// immune BY CONSTRUCTION to fence / JSON / homoglyph / spacing / split evasions in
// those two fields. It does NOT scrub the other free-text fields (narrative,
// self_reflection_prompt, axis_b.*.comment, axis_a.rule_4_2_flags); GROUP 3 pins
// that KNOWN GAP as a regression sentinel.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { redactDebriefOracle, validateDebriefScorecard, detectDebriefOracleLeak } from "../src/validate.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const bundle = JSON.parse(
  readFileSync(join(__dirname, "..", "personas", "personas.generated.json"), "utf8")
);

// ---- real secret material, pulled straight from the shipped bundle ----------
function tierTexts(persona, tiers) {
  const out = [];
  const d = (persona && persona.disclosure) || {};
  for (const t of tiers) for (const it of d[t] || []) if (it && it.text) out.push(it.text);
  return out;
}
function labelsFor(personaId) {
  const fm = bundle.fact_map[personaId] || {};
  return new Set(Object.values(fm).map((m) => m.topic_label).filter(Boolean));
}

// Demo matter persona (m00.per.tester) — concealed + rapport-gated fact text.
const M00 = "m00.per.tester";
const p00 = bundle.personas[M00];
const fm00 = bundle.fact_map[M00] || {};
const CONCEALED_00 = tierTexts(p00, ["concealed"]);
const GATED_00 = tierTexts(p00, ["rapport_gated"]);
const REVEALED_00 = tierTexts(p00, ["revealed_if_asked"]);
const ALL_SECRET_00 = [...CONCEALED_00, ...GATED_00, ...REVEALED_00];

// Sanity: the bundle really does carry the strings we are probing for.
test("fixture wiring: real m00 secret text is present in the bundle", () => {
  assert.ok(CONCEALED_00.length >= 1, "expected concealed facts on m00.per.tester");
  assert.ok(GATED_00.length >= 1, "expected rapport-gated facts on m00.per.tester");
  // spot-check the demo's signature concealed string is really there
  assert.ok(
    CONCEALED_00.some((t) => t.includes("phone")),
    "expected the demo's phone-distraction concealed fact"
  );
});

// A leak scanner that also normalizes away the cheap evasions (unicode homoglyph
// fold + whitespace/zero-width collapse), so a "clean" assertion cannot be beaten
// by cosmetic obfuscation of a substring.
const HOMOGLYPH = {
  "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
  "х": "x", "ѕ": "s", "і": "i", "ј": "j", "һ": "h",
};
function fold(s) {
  let out = String(s == null ? "" : s).normalize("NFKC").toLowerCase();
  out = out.replace(/[аеорсхѕіјһ]/g, (c) => HOMOGLYPH[c] || c);
  // strip zero-width + collapse all whitespace so "t e x t i n g" == "texting"
  return out.replace(/[​‌‍⁠﻿]/g, "").replace(/\s+/g, "");
}
// Does `haystack` contain a meaningful fragment of any secret? We test the whole
// secret AND its distinctive head (first ~24 folded chars) to catch split leaks.
function leaks(haystack, secrets) {
  const h = fold(haystack);
  for (const s of secrets) {
    const f = fold(s);
    if (f.length >= 8 && h.includes(f)) return s;
    const head = f.slice(0, 24);
    if (head.length >= 12 && h.includes(head)) return s;
  }
  return null;
}

function baseScorecard() {
  return {
    schema_version: "1.0.0",
    matter_id: "m00",
    persona_id: M00,
    axis_a: {
      facts_elicited: [],
      revealed_if_asked_missed: [],
      rapport_gated_unearned: [],
      rule_4_2_flags: [],
    },
    axis_b: {
      rapport_opening: { score: 5, comment: "Warm open." },
      listening_t_funnel: { score: 5, comment: "Good funnel." },
      understanding_goals: { score: 5, comment: "Clear goals." },
      explanation_next_steps: { score: 5, comment: "Explained next steps." },
      overall_confidence: { score: 5, comment: "Confident." },
    },
    ethics_score: 0,
    narrative: "A solid first interview.",
    self_reflection_prompt: "What would a broader opening have surfaced?",
  };
}

// =====================================================================
// GROUP 1 — the guard OWNS the two Axis-A "missed" fields: every evasion
// form of a leaked secret is stripped (rebuild-based => immune by design).
// =====================================================================

const knownLabels00 = labelsFor(M00);

function assertMissedFieldsClean(sc) {
  const blob = JSON.stringify({
    a: sc.axis_a.revealed_if_asked_missed,
    b: sc.axis_a.rapport_gated_unearned,
  });
  // no secret fragment survives
  const leak = leaks(blob, ALL_SECRET_00);
  assert.equal(leak, null, `secret survived in a missed field: ${JSON.stringify(String(leak).slice(0, 48))}`);
  // and every emitted string is a real, neutral topic_label (never model text)
  for (const s of sc.axis_a.revealed_if_asked_missed)
    assert.ok(knownLabels00.has(s), `non-label string in revealed_if_asked_missed: ${JSON.stringify(s)}`);
  for (const r of sc.axis_a.rapport_gated_unearned)
    assert.ok(knownLabels00.has(r.topic), `non-label topic in rapport_gated_unearned: ${JSON.stringify(r.topic)}`);
  // still schema-valid after the rebuild
  assert.ok(validateDebriefScorecard(sc).ok, "scorecard invalid after redaction");
}

test("G1a verbatim secret dumped into missed fields is stripped", () => {
  const sc = baseScorecard();
  sc.axis_a.revealed_if_asked_missed = [...CONCEALED_00, ...REVEALED_00];
  sc.axis_a.rapport_gated_unearned = GATED_00.map((t) => ({ topic: t, trigger_needed: "follow_up_on_hint" }));
  redactDebriefOracle(sc, p00, fm00);
  assertMissedFieldsClean(sc);
});

test("G1b secret hidden in markdown / code fences is stripped", () => {
  const sc = baseScorecard();
  sc.axis_a.revealed_if_asked_missed = CONCEALED_00.map((t) => "```json\n" + t + "\n```");
  sc.axis_a.rapport_gated_unearned = GATED_00.map((t) => ({ topic: "**" + t + "**", trigger_needed: "acknowledged_emotion" }));
  redactDebriefOracle(sc, p00, fm00);
  assertMissedFieldsClean(sc);
});

test("G1c secret embedded in a JSON-ish string is stripped", () => {
  const sc = baseScorecard();
  sc.axis_a.revealed_if_asked_missed = CONCEALED_00.map((t) => JSON.stringify({ answer_key: t }));
  redactDebriefOracle(sc, p00, fm00);
  assertMissedFieldsClean(sc);
});

test("G1d unicode-homoglyph / spacing obfuscation of the secret is stripped", () => {
  const sc = baseScorecard();
  // Cyrillic look-alikes + zero-width spaces sprinkled through the secret.
  const obfusc = (t) =>
    t.replace(/a/g, "а").replace(/e/g, "е").replace(/o/g, "о")
      .split("").join("​");
  sc.axis_a.revealed_if_asked_missed = CONCEALED_00.map(obfusc);
  sc.axis_a.rapport_gated_unearned = GATED_00.map((t) => ({ topic: obfusc(t), trigger_needed: "no_interruption_streak" }));
  redactDebriefOracle(sc, p00, fm00);
  assertMissedFieldsClean(sc);
});

test("G1e secret split across multiple array entries is stripped", () => {
  const sc = baseScorecard();
  const halves = [];
  for (const t of CONCEALED_00) {
    const mid = Math.floor(t.length / 2);
    halves.push(t.slice(0, mid), t.slice(mid));
  }
  sc.axis_a.revealed_if_asked_missed = halves;
  redactDebriefOracle(sc, p00, fm00);
  assertMissedFieldsClean(sc);
});

test("G1f a homoglyph look-alike of a real topic LABEL is not trusted", () => {
  // Attacker passes a topic that LOOKS like a safe label but is a homoglyph, hoping
  // the guard preserves the model's (possibly poisoned) trigger choice for it. The
  // guard only trusts an EXACT known label, so the look-alike is discarded and the
  // ground-truth label + derived trigger ship instead.
  const realLabel = [...knownLabels00][0];
  const homoLabel = realLabel.replace(/a/g, "а").replace(/e/g, "е");
  const sc = baseScorecard();
  sc.axis_a.rapport_gated_unearned = [{ topic: homoLabel, trigger_needed: "explained_process" }];
  redactDebriefOracle(sc, p00, fm00);
  for (const r of sc.axis_a.rapport_gated_unearned)
    assert.ok(knownLabels00.has(r.topic), "homoglyph label was trusted into output");
});

// =====================================================================
// GROUP 2 — no over-redaction: legitimate, well-behaved model output and
// student-facing prose are preserved unmangled.
// =====================================================================

test("G2a a well-behaved model's correct label + valid trigger is preserved", () => {
  // Find a real rapport-gated fact + its label, feed the model the CORRECT answer.
  const gated = (p00.disclosure.rapport_gated || [])[0];
  const label = (fm00[gated.fact_ref] || {}).topic_label;
  const sc = baseScorecard();
  sc.axis_a.rapport_gated_unearned = [{ topic: label, trigger_needed: "confidentiality_reassurance" }];
  redactDebriefOracle(sc, p00, fm00);
  const kept = sc.axis_a.rapport_gated_unearned.find((r) => r.topic === label);
  assert.ok(kept, "correct label was dropped");
  assert.equal(kept.trigger_needed, "confidentiality_reassurance", "valid model trigger not preserved");
});

test("G2b elicited facts are correctly removed from the missed sets", () => {
  const revealed = (p00.disclosure.revealed_if_asked || [])[0];
  const gated = (p00.disclosure.rapport_gated || [])[0];
  const sc = baseScorecard();
  sc.axis_a.facts_elicited = [revealed.fact_ref, gated.fact_ref];
  redactDebriefOracle(sc, p00, fm00);
  const revLabel = (fm00[revealed.fact_ref] || {}).topic_label;
  const gatLabel = (fm00[gated.fact_ref] || {}).topic_label;
  assert.ok(!sc.axis_a.revealed_if_asked_missed.includes(revLabel), "elicited revealed fact still marked missed");
  assert.ok(!sc.axis_a.rapport_gated_unearned.some((r) => r.topic === gatLabel), "elicited gated fact still marked unearned");
});

test("G2c legitimate pedagogy prose (no secret text) passes through unmangled", () => {
  const sc = baseScorecard();
  const narrative = "The student opened warmly and funneled well, but never asked about the client's work or income.";
  sc.narrative = narrative;
  sc.axis_b.rapport_opening.comment = "Strong, unhurried opening built early trust.";
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(sc.narrative, narrative, "clean narrative was mutated");
  assert.equal(sc.axis_b.rapport_opening.comment, "Strong, unhurried opening built early trust.");
});

// =====================================================================
// GROUP 3 — C1 fail-closed free-text scan (rec C1, APPLIED 2026-07-19).
// redactDebriefOracle rebuilds only the two Axis-A "missed" fields. The model-
// authored FREE-TEXT fields (narrative / self_reflection_prompt / axis_b comments
// / rule_4_2_flags) are now guarded by detectDebriefOracleLeak: un-elicited
// concealed/rapport-gated fact TEXT in any of them is DETECTED so the handler can
// FAIL CLOSED (reject the scorecard) instead of shipping an answer key. These
// tests assert leak-REJECTED (was: gap-pinned as surviving), plus the
// false-positive carve-outs (clean prose + legitimately-elicited fact passes).
// =====================================================================

test("G3a secret in narrative is detected (fail-closed, was a survived gap)", () => {
  const secret = CONCEALED_00[0];
  const sc = baseScorecard();
  sc.narrative = "The interviewer missed that " + secret;
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), "narrative", "leak in narrative not detected");
});

test("G3b secret in self_reflection_prompt is detected", () => {
  const secret = CONCEALED_00[0];
  const sc = baseScorecard();
  sc.self_reflection_prompt = "Consider: " + secret;
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), "self_reflection_prompt");
});

test("G3c secret in an axis_b comment is detected", () => {
  const secret = CONCEALED_00[0];
  const sc = baseScorecard();
  sc.axis_b.rapport_opening.comment = secret;
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), "axis_b.rapport_opening.comment");
});

test("G3d secret in rule_4_2_flags is detected", () => {
  const secret = CONCEALED_00[0];
  const sc = baseScorecard();
  sc.axis_a.rule_4_2_flags = [secret];
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), "axis_a.rule_4_2_flags[0]");
});

test("G3e cosmetically-obfuscated secret (homoglyph + zero-width) in narrative is still detected", () => {
  const secret = CONCEALED_00[0];
  const obfusc = (t) => t.replace(/a/g, "а").replace(/e/g, "е").replace(/o/g, "о").split("").join("​");
  const sc = baseScorecard();
  sc.narrative = "note: " + obfusc(secret);
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), "narrative", "obfuscated leak slipped past the fold");
});

test("G3f a clean, secret-free scorecard passes (no false-positive reject)", () => {
  const sc = baseScorecard();
  sc.narrative = "The student opened warmly and funneled well, but never asked about the client's work or income.";
  sc.self_reflection_prompt = "What would a broader opening have surfaced about the client's situation?";
  sc.axis_b.rapport_opening.comment = "Strong, unhurried opening built early trust.";
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), null, "clean scorecard wrongly flagged as a leak");
});

test("G3g a LEGITIMATELY-ELICITED fact quoted in the narrative is NOT flagged (elicited carve-out)", () => {
  // The concealed fact was surfaced in the interview (in facts_elicited), so
  // discussing it in the narrative is legitimate — the guard must not fail closed.
  const concealedItem = (p00.disclosure.concealed || [])[0];
  const sc = baseScorecard();
  sc.axis_a.facts_elicited = [concealedItem.fact_ref];
  sc.narrative = "The student did well to surface a sensitive point: " + concealedItem.text;
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), null, "elicited fact wrongly flagged — over-redaction");
});

test("G3h an un-elicited secret still trips even when a DIFFERENT secret was elicited", () => {
  // Carve-out is per-fact: eliciting one concealed fact does not license leaking
  // another un-elicited concealed/gated fact.
  const concealed = p00.disclosure.concealed || [];
  const gated = p00.disclosure.rapport_gated || [];
  const elicitedItem = concealed[0];
  const leakItem = (gated[0] || concealed[1]);
  if (!leakItem) return; // matter without a second secret: nothing to assert
  const sc = baseScorecard();
  sc.axis_a.facts_elicited = [elicitedItem.fact_ref];
  sc.narrative = "Good work overall. " + leakItem.text;
  redactDebriefOracle(sc, p00, fm00);
  assert.equal(detectDebriefOracleLeak(sc, p00, fm00), "narrative", "un-elicited secret leaked despite a different fact being elicited");
});

// =====================================================================
// GROUP 4 — cross-matter real-data sweep: a second matter, malicious model
// pastes EVERY tier's text into the missed fields; nothing survives.
// =====================================================================

test("G4 second matter (m11): full tier-text dump into missed fields is stripped", () => {
  const pid = "m11.per.adeyemi";
  const p = bundle.personas[pid];
  const fm = bundle.fact_map[pid] || {};
  const labels = labelsFor(pid);
  const allText = tierTexts(p, ["volunteered", "revealed_if_asked", "rapport_gated", "concealed", "unknown"]);
  const sc = baseScorecard();
  sc.matter_id = "m11";
  sc.persona_id = pid;
  sc.axis_a.revealed_if_asked_missed = allText.slice();
  sc.axis_a.rapport_gated_unearned = allText.map((t) => ({ topic: t, trigger_needed: "follow_up_on_hint" }));
  redactDebriefOracle(sc, p, fm);

  const blob = JSON.stringify({ a: sc.axis_a.revealed_if_asked_missed, b: sc.axis_a.rapport_gated_unearned });
  assert.equal(leaks(blob, allText), null, "bundled fact text leaked in a missed field");
  for (const s of sc.axis_a.revealed_if_asked_missed)
    assert.ok(labels.has(s), `non-label string shipped: ${JSON.stringify(s)}`);
  for (const r of sc.axis_a.rapport_gated_unearned)
    assert.ok(labels.has(r.topic), `non-label topic shipped: ${JSON.stringify(r.topic)}`);
});

// =====================================================================
// GROUP 5 — guard robustness (no throw on garbage), so a malformed model
// response can never crash the redaction path into a fail-open.
// =====================================================================

test("G5 guard is total: null / empty / missing-factMap inputs never throw or fail open", () => {
  assert.equal(redactDebriefOracle(null, p00, fm00), null);
  assert.doesNotThrow(() => redactDebriefOracle({}, p00, fm00));
  const sc = baseScorecard();
  sc.axis_a.revealed_if_asked_missed = CONCEALED_00.slice();
  redactDebriefOracle(sc, p00, null); // no fact_map at all
  // secret gone; labels fall back to the safe withheld placeholder
  assert.equal(leaks(JSON.stringify(sc.axis_a), ALL_SECRET_00), null, "secret survived with missing fact_map");
});
