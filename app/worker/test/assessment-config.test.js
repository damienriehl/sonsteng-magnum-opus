// R8/AE4: request-scoped memo threshold configuration is strict, deterministic,
// and visibly local rather than implied institutional policy.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { resolveAssessmentThresholdConfig } from "../src/assessment-config.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const INSTRUMENT = JSON.parse(readFileSync(
  join(HERE, "..", "..", "..", "data", "curriculum", "assessment-instrument.json"),
  "utf8"
));

const school = {
  id: "school:midstate-local-2026",
  competence_score: 3,
  redo_eligible_below: 5,
};
const instructor = {
  id: "instructor:john-local-2026",
  competence_score: 5,
  redo_eligible_below: 7,
};

test("canonical defaults resolve to 4 competent and below 6 redo-eligible", () => {
  const out = resolveAssessmentThresholdConfig(undefined, INSTRUMENT);
  assert.equal(out.ok, true);
  assert.deepEqual(out.config, {
    schema_version: "memo-assessment-threshold-resolution/v1",
    source: "default",
    source_id: "memo-seven-heading-1-7",
    competence_score: 4,
    redo_eligible_below: 6,
    resolution: "instructor>school>default",
    locally_supplied: false,
    authority_status: "canonical_default",
    verified_institutional_authority: false,
    version: "1.1.0",
    content_hash: INSTRUMENT.content_hash,
  });
});

test("instructor record deterministically supersedes a school record", () => {
  const out = resolveAssessmentThresholdConfig({
    schema_version: "memo-assessment-threshold-config/v1",
    school,
    instructor,
  }, INSTRUMENT);
  assert.equal(out.ok, true);
  assert.equal(out.config.source, "instructor");
  assert.equal(out.config.source_id, instructor.id);
  assert.equal(out.config.competence_score, 5);
  assert.equal(out.config.redo_eligible_below, 7);
  assert.equal(out.config.locally_supplied, true);
  assert.equal(out.config.authority_status, "claimed_locally_supplied");
  assert.equal(out.config.verified_institutional_authority, false);
});

test("school record resolves when no instructor record is supplied", () => {
  const out = resolveAssessmentThresholdConfig({
    schema_version: "memo-assessment-threshold-config/v1",
    school,
  }, INSTRUMENT);
  assert.equal(out.ok, true);
  assert.equal(out.config.source, "school");
  assert.equal(out.config.source_id, school.id);
  assert.equal(out.config.competence_score, 3);
  assert.equal(out.config.redo_eligible_below, 5);
});

test("present invalid configuration fails closed instead of falling back", () => {
  const invalid = [
    null,
    {},
    { schema_version: "memo-assessment-threshold-config/v2", school },
    { schema_version: "memo-assessment-threshold-config/v1", school, unexpected: true },
    { schema_version: "memo-assessment-threshold-config/v1", school: { ...school, note: "trust me" } },
    { schema_version: "memo-assessment-threshold-config/v1", school: { ...school, id: "bad id" } },
    { schema_version: "memo-assessment-threshold-config/v1", school: { ...school, competence_score: 0 } },
    { schema_version: "memo-assessment-threshold-config/v1", school: { ...school, competence_score: 6, redo_eligible_below: 5 } },
    { schema_version: "memo-assessment-threshold-config/v1", instructor: { ...instructor, redo_eligible_below: 8 } },
  ];
  for (const config of invalid) {
    const out = resolveAssessmentThresholdConfig(config, INSTRUMENT);
    assert.equal(out.ok, false, JSON.stringify(config));
    assert.match(out.error, /assessment_config/);
    assert.equal(out.config, undefined);
  }
});

test("memo route resolves configuration before provider or persistence work", () => {
  const source = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
  const start = source.indexOf("async function handleMemoAssessment");
  const end = source.indexOf("// ---- POST /v1/critique", start);
  const handler = source.slice(start, end);
  const resolution = handler.indexOf("resolveAssessmentThresholdConfig");
  const panel = handler.indexOf("resolvePanelUpstreams");
  const persisted = handler.indexOf("persistAssessmentAudit");
  assert.ok(resolution > 0 && panel > resolution && persisted > panel);
  assert.match(handler, /thresholdConfig: thresholdResolution\.config/);
  assert.match(handler, /validation_error[^]*thresholdResolution\.error/);
});
