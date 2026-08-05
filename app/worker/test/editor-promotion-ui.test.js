import { test } from "node:test";
import assert from "node:assert/strict";
import { renderReviewPage } from "../src/editor-review.js";
import { REVIEW_JS, REVIEW_CSS } from "../src/editor-assets.js";

const candidate = {
  id: "candidate-1", stage: "awaiting_approval", source_ref: "data/x#y",
  stage_at: Date.parse("2026-08-05T12:00:00Z"), created_at: Date.parse("2026-08-05T11:58:00Z"),
  attempt_id: "candidate-1:1", base_sha: "base", evidence_hash: "evidence",
  manifest_hash: "manifest", preview_href: "/edit/v1/prod/preview?id=candidate-1",
};

test("review page carries oldest-first promotion queue and lane context without lifecycle authority", async () => {
  const page = renderReviewPage([], [], [candidate],
    { version: 2, paused: false, health: "healthy", updated_at: 1 }, "live:manifest");
  const html = await page.text();
  assert.match(html, /id="promotion-data"/);
  assert.match(html, /candidate-1/);
  assert.match(html, /live:manifest/);
  assert.match(html, /Promotion review/);
  assert.doesNotMatch(html, /lease_owner|fencing_token/);
});

test("promotion review assets cover evidence-bound decisions and accessible recovery", () => {
  assert.match(REVIEW_JS, /\/edit\/v1\/prod\/candidate/);
  assert.match(REVIEW_JS, /\/edit\/v1\/prod\/decision/);
  assert.match(REVIEW_JS, /changed evidence/i);
  assert.match(REVIEW_JS, /rationale/);
  assert.match(REVIEW_JS, /focus\(\)/);
  assert.match(REVIEW_CSS, /:focus-visible/);
  assert.match(REVIEW_CSS, /@media \(max-width:640px\)/);
});
