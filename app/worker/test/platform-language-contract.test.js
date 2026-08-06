import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { validateLearnerResultRequest } from "../src/validate.js";

const HERE = dirname(fileURLToPath(import.meta.url));

test("learner assessment requests reject alumni routing fields", () => {
  for (const field of ["alumni_assessor", "alumni_recipient", "alumni_notification"]) {
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

test("both learner-result handlers enforce the routing guard", () => {
  const source = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
  assert.equal((source.match(/validateLearnerResultRequest\(body\)/g) || []).length, 2);
});
