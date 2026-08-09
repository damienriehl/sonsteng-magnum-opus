import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeTaxonomyPlainText } from "../src/editor-endpoints.js";

const REF = "data/taxonomy/skills.json#skills.0.name";

test("taxonomy wording normalizes as plain text but preserves inert markup characters", () => {
  assert.equal(
    normalizeTaxonomyPlainText(REF, "  Cafe\u0301 <script>alert(1)</script>\r\n  "),
    "Café <script>alert(1)</script>",
  );
});

test("taxonomy wording rejects controls and bidi override characters", () => {
  for (const value of ["safe\u0000unsafe", "safe\u202eunsafe", "safe\u2066unsafe", "  "])
    assert.equal(normalizeTaxonomyPlainText(REF, value), null);
});

test("non-taxonomy scalar behavior remains unchanged", () => {
  const value = "  Existing scalar behavior  ";
  assert.equal(normalizeTaxonomyPlainText("data/copy/home.json#hero.heading", value), value);
});
