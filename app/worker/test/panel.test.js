// U11 contract tests for formative-only memo panel orchestration.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  aggregateMemoPanel,
  runFormativeMemoPanel,
} from "../src/panel.js";
import { buildMemoScorecardPrompt } from "../src/prompts.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const INSTRUMENT = JSON.parse(readFileSync(
  join(HERE, "..", "..", "..", "data", "curriculum", "assessment-instrument.json"),
  "utf8"
));
const HEADINGS = INSTRUMENT.content.dimensions.map((dimension) => dimension.id);
const SUBMISSION = HEADINGS.map((heading) => `Evidence for ${heading}.`).join("\n\n");

function scorecard(scores) {
  return {
    schema_version: "1.0.0",
    instrument_id: INSTRUMENT.id,
    instrument_version: INSTRUMENT.instrument_version,
    instrument_content_hash: INSTRUMENT.content_hash,
    headings: HEADINGS.map((heading_id, index) => ({
      heading_id,
      evidence_spans: [`Evidence for ${heading_id}.`],
      rationale: `Anonymous grader rationale ${scores[index]}.`,
      score: scores[index],
    })),
  };
}

const GRADERS = [
  { mode: "byok", provider: "anthropic", model: "claude-haiku-4-5", apiKey: "sentinel-anthropic" },
  { mode: "byok", provider: "openai", model: "gpt-4o-mini", apiKey: "sentinel-openai" },
  { mode: "byok", provider: "google", model: "gemini-2.0-flash", apiKey: "sentinel-google" },
];

function panelEntries(scoreSets) {
  return scoreSets.map((scores, index) => ({
    grader: GRADERS[index],
    scorecard: scorecard(scores),
  }));
}

test("median is computed in code from a hand-checked three-grader fixture", () => {
  const entries = panelEntries([
    [2, 3, 4, 4, 5, 6, 7],
    [4, 3, 4, 4, 5, 6, 7],
    [7, 4, 4, 4, 5, 6, 7],
  ]);
  const result = aggregateMemoPanel(entries, {});
  assert.equal(result.headings[0].median_score, 4);
  assert.equal(result.headings[0].score, 4);
  assert.equal(result.headings[1].median_score, 3);
});

test("spread >= 2 triggers adjudication while smaller spread never does", () => {
  const entries = panelEntries([
    [1, 3, 4, 4, 5, 6, 7],
    [3, 3, 4, 4, 5, 6, 7],
    [4, 4, 4, 4, 5, 6, 7],
  ]);
  const result = aggregateMemoPanel(entries, { [HEADINGS[0]]: 2, [HEADINGS[1]]: 7 });
  assert.equal(result.headings[0].spread, 3);
  assert.deepEqual(result.headings[0].adjudication, { triggered: true, proposed_score: 2, score: 2 });
  assert.equal(result.headings[1].spread, 1);
  assert.deepEqual(result.headings[1].adjudication, { triggered: false });
  assert.equal(result.headings[1].score, 3, "an unsolicited adjudication must be ignored");
});

test("adjudication is constrained to the observed min-max range", () => {
  const entries = panelEntries([
    [1, 3, 4, 4, 5, 6, 7],
    [3, 3, 4, 4, 5, 6, 7],
    [4, 3, 4, 4, 5, 6, 7],
  ]);
  const result = aggregateMemoPanel(entries, { [HEADINGS[0]]: 6 });
  assert.equal(result.headings[0].observed_min, 1);
  assert.equal(result.headings[0].observed_max, 4);
  assert.equal(result.headings[0].adjudication.proposed_score, 6);
  assert.equal(result.headings[0].adjudication.score, 4);
  assert.equal(result.headings[0].score, 4);
});

test("identical validated evidence repeats deterministically", () => {
  const entries = panelEntries([
    [2, 3, 4, 4, 5, 6, 7],
    [4, 3, 4, 4, 5, 6, 7],
    [6, 4, 4, 4, 5, 6, 7],
  ]);
  assert.deepEqual(
    aggregateMemoPanel(entries, { [HEADINGS[0]]: 5 }),
    aggregateMemoPanel(entries, { [HEADINGS[0]]: 5 })
  );
});

test("orchestrator blinds inputs, adjudicates only contested headings, and strips credentials", async () => {
  const sets = {
    anthropic: [1, 3, 4, 4, 5, 6, 7],
    openai: [3, 3, 4, 4, 5, 6, 7],
    google: [4, 4, 4, 4, 5, 6, 7],
  };
  const calls = [];
  const complete = async ({ grader, kind, prompt }) => {
    calls.push({ provider: grader.provider, kind, prompt });
    if (kind === "adjudication") {
      return { ok: true, text: JSON.stringify({
        headings: [{ heading_id: HEADINGS[0], score: 6 }],
      }), usage: { input_tokens: 9, output_tokens: 2 } };
    }
    return { ok: true, text: JSON.stringify(scorecard(sets[grader.provider])), usage: { input_tokens: 8, output_tokens: 3 } };
  };

  const run = await runFormativeMemoPanel({
    submission: SUBMISSION,
    instrument: INSTRUMENT,
    graders: GRADERS,
    complete,
  });
  assert.equal(run.ok, true, run.errors?.join("; "));
  assert.equal(calls.filter((call) => call.kind === "grader").length, 3);
  assert.equal(calls.filter((call) => call.kind === "adjudication").length, 1);
  assert.deepEqual(run.result.headings[0].adjudication, { triggered: true, proposed_score: 6, score: 4 });
  assert.equal(run.result.assessment_use, "formative");
  assert.equal(run.result.summative_eligible, false);
  assert.deepEqual(run.result.summative_blockers, ["human_human_calibration", "provider_terms_review"]);
  assert.equal(run.result.assurance, "multi_provider_formative");
  assert.deepEqual(run.result.providers.map((p) => p.provider).sort(), ["anthropic", "google", "openai"]);

  const persistable = JSON.stringify(run.result);
  for (const secret of GRADERS.map((grader) => grader.apiKey)) assert.ok(!persistable.includes(secret));
  assert.ok(!/api[_-]?key|authorization|credential/i.test(persistable));
  for (const call of calls) {
    assert.ok(!call.prompt.includes("sentinel-"), "provider prompts must not contain credentials");
    assert.ok(!/student_name|student_id|email/i.test(call.prompt), "grader input must stay blind");
  }
});

test("a one-key run stays formative and is explicitly reduced assurance", async () => {
  const grader = GRADERS[0];
  const run = await runFormativeMemoPanel({
    submission: SUBMISSION,
    instrument: INSTRUMENT,
    graders: [grader],
    complete: async () => ({ ok: true, text: JSON.stringify(scorecard([4, 4, 4, 4, 4, 4, 4])), usage: {} }),
  });
  assert.equal(run.ok, true);
  assert.equal(run.result.assurance, "reduced_assurance");
  assert.equal(run.result.assessment_use, "formative");
  assert.equal(run.result.summative_eligible, false);
  assert.equal(run.result.providers.length, 1);
});

test("a live key echoed into otherwise valid evidence fails closed", async () => {
  const grader = GRADERS[0];
  const leaked = scorecard([4, 4, 4, 4, 4, 4, 4]);
  leaked.headings[0].rationale = `Unexpected upstream echo: ${grader.apiKey}`;
  const run = await runFormativeMemoPanel({
    submission: SUBMISSION,
    instrument: INSTRUMENT,
    graders: [grader],
    complete: async () => ({ ok: true, text: JSON.stringify(leaked), usage: {} }),
  });
  assert.equal(run.ok, false);
  assert.equal(run.kind, "validation");
  assert.match(run.errors.join(" "), /credential material rejected/i);
  assert.equal(run.result, undefined);
});

test("memo scorecard prompt includes only the server instrument and blinded submission", () => {
  const prompt = buildMemoScorecardPrompt("template {{ASSESSMENT_INSTRUMENT_JSON}} :: {{SUBMISSION}}", {
    instrument: INSTRUMENT,
    submission: SUBMISSION,
  });
  assert.match(prompt, /memo-seven-heading-1-7/);
  assert.match(prompt, /Evidence for governing_law/);
  assert.ok(!prompt.includes("{{"));
});
