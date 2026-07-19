// Golden-file byte-identity + Segment-B rendering tests for prompts.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { buildSystemPrompt, renderPersona, buildDebriefPrompt, buildTierData, rubricCriteriaLabels } from "../src/prompts.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIX = join(__dirname, "fixtures");
const BUNDLE = join(__dirname, "..", "personas", "personas.generated.json");

const bundle = JSON.parse(readFileSync(BUNDLE, "utf8"));
const persona = JSON.parse(readFileSync(join(FIX, "persona-m00-client.json"), "utf8"));
const golden = readFileSync(join(FIX, "rendered-system-prompt-m00.txt"), "utf8");

test("buildSystemPrompt reproduces the golden file byte-for-byte", () => {
  const rendered = buildSystemPrompt(bundle.segment_a, persona);
  const a = Buffer.from(rendered, "utf8");
  const b = Buffer.from(golden, "utf8");
  if (!a.equals(b)) {
    // Pinpoint the first divergence to make failures debuggable.
    const min = Math.min(a.length, b.length);
    let i = 0;
    while (i < min && a[i] === b[i]) i++;
    assert.fail(
      `bytes differ at offset ${i} (rendered ${a.length}B, golden ${b.length}B)\n` +
        `rendered: ${JSON.stringify(rendered.slice(Math.max(0, i - 40), i + 40))}\n` +
        `golden:   ${JSON.stringify(golden.slice(Math.max(0, i - 40), i + 40))}`
    );
  }
  assert.ok(a.equals(b));
});

test("segment_a is embedded verbatim (18316 chars) and byte-stable", () => {
  assert.equal(bundle.segment_a.length, 18316);
  // Rendering twice yields identical bytes (cache byte-stability invariant).
  const one = buildSystemPrompt(bundle.segment_a, persona);
  const two = buildSystemPrompt(bundle.segment_a, persona);
  assert.equal(one, two);
});

test("fact_ref ids never leak into the rendered persona prompt", () => {
  const rendered = renderPersona(persona);
  assert.ok(!/m00\.fact\.\d{3}/.test(rendered), "fact_ref must not appear in the prompt");
});

test("empty disclosure tiers still emit their lead line + placeholder", () => {
  const bare = JSON.parse(JSON.stringify(persona));
  bare.disclosure = { volunteered: [], revealed_if_asked: [], rapport_gated: [], concealed: [], unknown: [] };
  const rendered = renderPersona(bare);
  assert.ok(rendered.includes("(nothing in this tier)."));
});

test("rule 4.2 section only renders when applies === true", () => {
  assert.ok(!renderPersona(persona).includes("Someone should be here with you"));
  const rep = JSON.parse(JSON.stringify(persona));
  rep.rule_4_2 = { applies: true, counsel_name: "Dana Vinstead" };
  const rendered = renderPersona(rep);
  assert.ok(rendered.includes("## Someone should be here with you"));
  assert.ok(rendered.includes("your own lawyer, Dana Vinstead, and the person interviewing you"));
});

test("buildDebriefPrompt fills every slot and never leaves a placeholder", () => {
  const prompt = buildDebriefPrompt(bundle.debrief_template, {
    matterId: "m00",
    personaId: "m00.per.tester",
    persona: bundle.personas["m00.per.tester"],
    factMap: bundle.fact_map["m00.per.tester"],
    transcript: [
      { role: "user", content: "Tell me what happened." },
      { role: "assistant", content: "I slipped in the produce aisle." },
    ],
    interviewerOnOpposingSide: false,
  });
  assert.ok(!/\{\{[A-Z_0-9]+\}\}/.test(prompt), "no unfilled {{SLOT}} may remain");
  assert.ok(prompt.includes("[1] INTERVIEWER: Tell me what happened."));
  assert.ok(prompt.includes("[2] CLIENT: I slipped in the produce aisle."));
});

test("buildTierData carries topic_labels and rapport requirements", () => {
  const td = buildTierData(bundle.personas["m00.per.tester"], bundle.fact_map["m00.per.tester"]);
  assert.ok(td.includes("topic_label: how the injury is healing"));
  assert.ok(td.includes("min_turns=4"));
  assert.ok(td.includes("requires=[no_interruption_streak, nonjudgmental_response]"));
});

test("rubricCriteriaLabels maps criterion and subcriterion ids to names", () => {
  const rubric = {
    id: "m01.rub",
    criteria: [
      { id: "m01.rub.c01", name: "Case theory", weight_points: 45 },
      {
        id: "m01.rub.c03", name: "Interview craft", weight_points: 30,
        subcriteria: [
          { id: "m01.rub.c03.s01", name: "Rapport and opening", weight_points: 15 },
          { id: "m01.rub.c03.s02", name: "T-funnel listening", weight_points: 15 },
        ],
      },
    ],
  };
  assert.deepEqual(rubricCriteriaLabels(rubric), {
    "m01.rub.c01": "Case theory",
    "m01.rub.c03": "Interview craft",
    "m01.rub.c03.s01": "Rapport and opening",
    "m01.rub.c03.s02": "T-funnel listening",
  });
  // Tolerant of absent/odd input.
  assert.deepEqual(rubricCriteriaLabels(null), {});
  assert.deepEqual(rubricCriteriaLabels({}), {});
});

test("every bundled rubric yields non-empty criteria_labels", () => {
  for (const [mid, rubric] of Object.entries(bundle.rubrics || {})) {
    const labels = rubricCriteriaLabels(rubric);
    assert.ok(Object.keys(labels).length > 0, mid + " rubric must produce labels");
    for (const [id, name] of Object.entries(labels)) {
      assert.match(id, /^m\d{2}\.rub\.c\d{2}(\.s\d{2})*$/);
      assert.ok(name.length > 0);
    }
  }
});
