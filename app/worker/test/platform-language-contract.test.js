import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { validateLearnerResultRequest } from "../src/validate.js";

const HERE = dirname(fileURLToPath(import.meta.url));

test("learner assessment requests reject alumni routing fields", () => {
  for (const field of [
    "alumni_assessor", "alumni_reviewer", "alumni_recipient",
    "alumni_notification", "alumni_feedback_destination",
  ]) {
    assert.deepEqual(validateLearnerResultRequest({ [field]: "someone@example.test" }), {
      ok: false,
      error: "Alumni routing fields are not supported.",
    });
  }
});

test("ordinary debrief and critique request fields remain accepted", () => {
  assert.deepEqual(validateLearnerResultRequest({ matter_id: "m01", persona_id: "m01.per.client" }), { ok: true });
  assert.deepEqual(validateLearnerResultRequest({ matter_id: "m01", deliverable_text: "Draft" }), { ok: true });
});

test("the formative memo handler rejects alumni routing before session or provider work", () => {
  const source = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
  const start = source.indexOf("async function handleMemoAssessment");
  const end = source.indexOf("// ---- POST /v1/critique", start);
  assert.ok(start > 0, "memo assessment handler must exist");
  const handler = source.slice(start, end);
  const guard = handler.indexOf("validateLearnerResultRequest(body)");
  assert.ok(guard > 0);
  assert.ok(guard < handler.indexOf("verifySession("));
  assert.ok(guard < handler.indexOf("resolvePanelUpstreams("));
  assert.match(handler, /assessment_use/);
  assert.match(handler, /summative_eligible !== false/);
  assert.match(handler, /human_human_calibration/);
  assert.match(handler, /provider_terms_review/);
});

test("the router exposes only the explicitly formative memo assessment route", () => {
  const source = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
  assert.match(source, /POST" && url\.pathname === "\/v1\/memo-assessment"/);
  assert.doesNotMatch(source, /summative-assessment/);
});

test("both learner-result handlers enforce the routing guard before sessions", () => {
  const source = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
  const debrief = source.slice(source.indexOf("async function handleDebrief"), source.indexOf("// ---- POST /v1/critique"));
  const critique = source.slice(source.indexOf("async function handleCritique"), source.indexOf("// ---- router"));
  for (const handler of [debrief, critique]) {
    const guard = handler.indexOf("validateLearnerResultRequest(body)");
    assert.ok(guard > 0);
    assert.ok(guard < handler.indexOf("verifySession("));
    assert.match(handler, /errorEnvelope\("validation_error", routing\.error, 400\)/);
  }
});
