// U10 contract tests for the seven-heading memo evaluator output.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { validateMemoScorecard } from "../src/validate.js";

const HEADINGS = [
  "governing_law",
  "strengths_and_weaknesses_both_sides",
  "issues",
  "suggested_solutions",
  "theory_and_themes",
  "elements_to_prevail",
  "liabilities_and_remedies",
];
const __dirname = dirname(fileURLToPath(import.meta.url));
const INSTRUMENT = JSON.parse(readFileSync(
  join(__dirname, "..", "..", "..", "data", "curriculum", "assessment-instrument.json"),
  "utf8"
));

const SUBMISSION = [
  "The governing rule requires notice before liability attaches.",
  "The claimant has strong notice evidence, but the defense can contest timing.",
  "The decisive issue is whether notice preceded the accident.",
  "The parties should preserve the video and explore an early settlement.",
  "The case theory is preventable harm despite a fair warning opportunity.",
  "To prevail, the claimant must prove duty, breach, causation, and damages.",
  "Potential remedies include compensatory damages and injunctive relief.",
].join("\n\n");

function validScorecard() {
  return {
    schema_version: "1.0.0",
    instrument_id: "memo-seven-heading-1-7",
    instrument_version: INSTRUMENT.instrument_version,
    instrument_content_hash: INSTRUMENT.content_hash,
    headings: HEADINGS.map((heading_id, i) => ({
      heading_id,
      evidence_spans: [SUBMISSION.split("\n\n")[i]],
      rationale: "The quoted passage supports this heading-level judgment.",
      score: i + 1,
    })),
  };
}

test("valid evaluator output exposes all seven evidence-verified scores", () => {
  const result = validateMemoScorecard(validScorecard(), SUBMISSION, INSTRUMENT);
  assert.equal(result.ok, true, result.errors?.join("; "));
  assert.deepEqual(result.scorecard.headings.map((h) => h.heading_id), HEADINGS);
  assert.deepEqual(result.scorecard.headings.map((h) => h.score), [1, 2, 3, 4, 5, 6, 7]);
});

test("a missing heading fails closed", () => {
  const output = validScorecard();
  output.headings.pop();
  const result = validateMemoScorecard(output, SUBMISSION, INSTRUMENT);
  assert.equal(result.ok, false);
  assert.equal(result.scorecard, undefined);
  assert.match(result.errors.join(" "), /missing|exactly 7/i);
});

test("a duplicate heading fails closed", () => {
  const output = validScorecard();
  output.headings[6].heading_id = output.headings[0].heading_id;
  const result = validateMemoScorecard(output, SUBMISSION, INSTRUMENT);
  assert.equal(result.ok, false);
  assert.equal(result.scorecard, undefined);
  assert.match(result.errors.join(" "), /duplicate|missing/i);
});

test("scores outside the integer 1-7 enum fail closed", () => {
  for (const score of [0, 8, 4.5, "4"]) {
    const output = validScorecard();
    output.headings[0].score = score;
    const result = validateMemoScorecard(output, SUBMISSION, INSTRUMENT);
    assert.equal(result.ok, false, `score ${JSON.stringify(score)} must fail`);
    assert.equal(result.scorecard, undefined);
  }
});

test("model-supplied overall score is rejected and can never be displayed", () => {
  const output = validScorecard();
  output.overall_score = 7;
  const result = validateMemoScorecard(output, SUBMISSION, INSTRUMENT);
  assert.equal(result.ok, false);
  assert.equal(result.scorecard, undefined);
  assert.match(result.errors.join(" "), /overall_score|unexpected/i);
});

test("every evidence span must occur verbatim in the submission", () => {
  const absent = validScorecard();
  absent.headings[0].evidence_spans = [];
  assert.equal(validateMemoScorecard(absent, SUBMISSION, INSTRUMENT).ok, false);

  const invented = validScorecard();
  invented.headings[0].evidence_spans = ["The submission proves actual notice."];
  const result = validateMemoScorecard(invented, SUBMISSION, INSTRUMENT);
  assert.equal(result.ok, false);
  assert.equal(result.scorecard, undefined);
  assert.match(result.errors.join(" "), /verbatim/i);
});

test("letter-grade and other undeclared model fields fail closed", () => {
  const output = validScorecard();
  output.letter_grade = "A";
  const result = validateMemoScorecard(output, SUBMISSION, INSTRUMENT);
  assert.equal(result.ok, false);
  assert.equal(result.scorecard, undefined);
  assert.match(result.errors.join(" "), /letter_grade|unexpected/i);
});

test("instrument provenance must match the server-owned canonical instrument", () => {
  for (const [field, value] of [
    ["instrument_version", "9.9.9"],
    ["instrument_content_hash", "sha256:" + "b".repeat(64)],
  ]) {
    const output = validScorecard();
    output[field] = value;
    const result = validateMemoScorecard(output, SUBMISSION, INSTRUMENT);
    assert.equal(result.ok, false);
    assert.equal(result.scorecard, undefined);
    assert.match(result.errors.join(" "), new RegExp(field));
  }
});
