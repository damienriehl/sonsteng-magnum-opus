// Regression tests for the DEBRIEF-ORACLE hard guard (validate.js ::
// redactDebriefOracle). The oracle rule is prompt-enforced in the evaluator
// template, but a weak/jailbroken BYOK model — or a transcript-injected
// instruction — could ignore it and echo un-elicited CONCEALED fact TEXT in the
// Axis-A "missed" fields, turning the student-visible scorecard into an answer
// key. The guard rebuilds those fields from server-side ground truth so every
// emitted string comes ONLY from fact_map.topic_label, never from model output.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { redactDebriefOracle, validateDebriefScorecard } from "../src/validate.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const bundle = JSON.parse(
  readFileSync(join(__dirname, "..", "personas", "personas.generated.json"), "utf8")
);

// A persona with one revealed_if_asked fact (never elicited) and one
// rapport_gated fact (never earned), plus a fact_map giving each a NEUTRAL topic
// label distinct from its damning TEXT.
const persona = {
  disclosure: {
    volunteered: [{ fact_ref: "m99.fact.001", text: "I slipped in aisle 5." }],
    revealed_if_asked: [
      { fact_ref: "m99.fact.010", text: "I had four beers at lunch before the fall." },
    ],
    rapport_gated: [
      {
        fact_ref: "m99.fact.020",
        text: "I have filed three identical slip-and-fall claims before.",
        requires: ["acknowledged_emotion", "no_interruption_streak"],
        min_turns: 4,
      },
    ],
    concealed: [
      { fact_ref: "m99.fact.030", text: "I forged the incident report timestamp." },
    ],
    unknown: [],
  },
};
const factMap = {
  "m99.fact.001": { topic_label: "the fall itself", tier: "volunteered" },
  "m99.fact.010": { topic_label: "the timeline before the fall", tier: "revealed_if_asked" },
  "m99.fact.020": { topic_label: "any history of prior claims", tier: "rapport_gated" },
  "m99.fact.030": { topic_label: "the incident report", tier: "concealed" },
};

// The forbidden strings — verbatim fact TEXT that must NEVER appear in output.
const LEAK_STRINGS = [
  "four beers",
  "three identical slip-and-fall",
  "forged",
];

function baseScorecard(axisAOverrides) {
  return {
    schema_version: "1.0.0",
    matter_id: "m99",
    persona_id: "m99.per.tester",
    axis_a: Object.assign(
      {
        facts_elicited: ["m99.fact.001"],
        revealed_if_asked_missed: [],
        rapport_gated_unearned: [],
        rule_4_2_flags: [],
      },
      axisAOverrides
    ),
    axis_b: {
      rapport_opening: { score: 5, comment: "ok" },
      listening_t_funnel: { score: 5, comment: "ok" },
      understanding_goals: { score: 5, comment: "ok" },
      explanation_next_steps: { score: 5, comment: "ok" },
      overall_confidence: { score: 5, comment: "ok" },
    },
    ethics_score: 0,
    narrative: "Solid first pass.",
    self_reflection_prompt: "What would a broader opening have surfaced?",
  };
}

test("leaked fact TEXT in missed fields is replaced with neutral topic labels", () => {
  // A misbehaving model dumps the raw fact text into the missed fields.
  const sc = baseScorecard({
    revealed_if_asked_missed: ["I had four beers at lunch before the fall."],
    rapport_gated_unearned: [
      { topic: "I have filed three identical slip-and-fall claims before.", trigger_needed: "acknowledged_emotion" },
    ],
  });
  redactDebriefOracle(sc, persona, factMap);

  const blob = JSON.stringify(sc);
  for (const leak of LEAK_STRINGS) {
    assert.ok(!blob.includes(leak), `leaked fact text survived: ${JSON.stringify(leak)}`);
  }
  // The neutral labels from fact_map are what ships.
  assert.deepEqual(sc.axis_a.revealed_if_asked_missed, ["the timeline before the fall"]);
  assert.equal(sc.axis_a.rapport_gated_unearned.length, 1);
  assert.equal(sc.axis_a.rapport_gated_unearned[0].topic, "any history of prior claims");
  // still schema-valid after redaction
  assert.ok(validateDebriefScorecard(sc).ok);
});

test("miss SET is derived from ground truth, not trusted from the model", () => {
  // Model falsely reports NOTHING missed. Guard still surfaces the real misses
  // (fact.010 not elicited, fact.020 not elicited), each as a topic label.
  const sc = baseScorecard({
    facts_elicited: ["m99.fact.001"],
    revealed_if_asked_missed: [],
    rapport_gated_unearned: [],
  });
  redactDebriefOracle(sc, persona, factMap);
  assert.deepEqual(sc.axis_a.revealed_if_asked_missed, ["the timeline before the fall"]);
  assert.deepEqual(sc.axis_a.rapport_gated_unearned, [
    { topic: "any history of prior claims", trigger_needed: "acknowledged_emotion" },
  ]);
});

test("elicited facts are excluded from the missed sets", () => {
  // The student DID draw out both the revealed and rapport facts.
  const sc = baseScorecard({
    facts_elicited: ["m99.fact.001", "m99.fact.010", "m99.fact.020"],
  });
  redactDebriefOracle(sc, persona, factMap);
  assert.deepEqual(sc.axis_a.revealed_if_asked_missed, []);
  assert.deepEqual(sc.axis_a.rapport_gated_unearned, []);
});

test("a well-behaved model's valid trigger choice is preserved", () => {
  const sc = baseScorecard({
    rapport_gated_unearned: [
      // topic is already the canonical label; trigger is a valid enum token
      { topic: "any history of prior claims", trigger_needed: "no_interruption_streak" },
    ],
  });
  redactDebriefOracle(sc, persona, factMap);
  assert.equal(sc.axis_a.rapport_gated_unearned[0].topic, "any history of prior claims");
  assert.equal(sc.axis_a.rapport_gated_unearned[0].trigger_needed, "no_interruption_streak");
});

test("guard is robust against a missing/garbage fact_map and axis_a", () => {
  const sc = baseScorecard({
    revealed_if_asked_missed: ["I had four beers at lunch before the fall."],
  });
  // no fact_map at all -> labels fall back to a safe withheld placeholder, and
  // the raw text is still gone
  redactDebriefOracle(sc, persona, null);
  assert.ok(!JSON.stringify(sc).includes("four beers"));
  assert.deepEqual(sc.axis_a.revealed_if_asked_missed, ["(topic withheld)"]);
  // non-object scorecard is returned untouched, no throw
  assert.equal(redactDebriefOracle(null, persona, factMap), null);
  assert.doesNotThrow(() => redactDebriefOracle({}, persona, factMap));
});

test("real bundled persona: no un-elicited disclosure text leaks", () => {
  // Drive the guard with a real persona + fact_map from the shipped bundle and a
  // model that maliciously pastes every fact's text into the missed fields.
  const personaId = "m05.per.broshears";
  const p = bundle.personas[personaId];
  const fm = bundle.fact_map[personaId] || {};
  assert.ok(p && p.disclosure, "expected bundled persona with disclosure");

  const allTexts = [];
  for (const tier of ["volunteered", "revealed_if_asked", "rapport_gated", "concealed", "unknown"]) {
    for (const it of p.disclosure[tier] || []) if (it && it.text) allTexts.push(it.text);
  }
  const sc = baseScorecard({
    facts_elicited: [],
    revealed_if_asked_missed: allTexts.slice(),
    rapport_gated_unearned: allTexts.map((t) => ({ topic: t, trigger_needed: "follow_up_on_hint" })),
  });
  sc.matter_id = "m05";
  sc.persona_id = personaId;

  redactDebriefOracle(sc, p, fm);
  const blob = JSON.stringify(sc.axis_a);
  for (const t of allTexts) {
    // fact TEXT must not appear; only fact_map topic labels may.
    assert.ok(!blob.includes(t), `bundled fact text leaked: ${JSON.stringify(t.slice(0, 40))}`);
  }
  // every emitted revealed-miss string must be a known topic label
  const labels = new Set(Object.values(fm).map((m) => m.topic_label));
  for (const s of sc.axis_a.revealed_if_asked_missed) {
    assert.ok(labels.has(s), `unexpected non-label string: ${JSON.stringify(s)}`);
  }
});
