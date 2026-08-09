import { test } from "node:test";
import assert from "node:assert/strict";
import { renderPublisherPage, publisherViewModel } from "../src/editor-publisher.js";
import { PUBLISHER_CSS, PUBLISHER_JS } from "../src/editor-assets.js";
import { editorFetch } from "../src/editor.js";
import { makeCore } from "./editor-sql-helper.mjs";

const prepared = {
  id: "release-7", state: "prepared", target_environment: "production",
  target_batch_id: "batch-2", base_sha: "base0123456789", candidate_sha: "candidate0123456789",
  generator_id: "generator-v1", evidence_hash: "evidence-1", manifest_hash: "manifest-1",
  membership_hash: "members-1", suggestion_ids: ["s1", "s2"],
  batches: [
    { ordinal: 0, batch_id: "batch-1", commit_sha: "commit-1" },
    { ordinal: 1, batch_id: "batch-2", commit_sha: "commit-2" },
  ],
  events: [{ type: "prepared", actor: "service:builder", created_at: 1000 }],
};

const context = {
  release: prepared,
  batches: [
    { batch_id: "batch-1", commit_sha: "commit-1", changes: [
      { id: "s1", editor: "slot:john", source_ref: "data/x.json#title", original_text: "Old title", new_text: "New title", group_id: null },
    ] },
    { batch_id: "batch-2", commit_sha: "commit-2", changes: [
      { id: "s2", editor: "slot:roger", source_ref: "data/x.json#body", original_text: "Before", new_text: "After", group_id: "g1" },
    ] },
  ],
};

test("publisher view exposes only complete contiguous targets and truthful eligibility", () => {
  const vm = publisherViewModel({ release: null, batches: context.batches });
  assert.equal(vm.eligibleChanges, 2);
  assert.deepEqual(vm.targets.map((x) => x.batch_id), ["batch-1", "batch-2"]);
  assert.deepEqual(vm.targets.map((x) => x.enclosedChanges), [1, 2]);
  assert.equal(vm.productionStatus, "Available on DEV — waiting for Publisher");
  return renderPublisherPage({ release: null, batches: context.batches }).text().then((html) => {
    assert.match(html, /Prepare immutable preview/);
    assert.match(html, /Prepare immutable preview<\/button>/);
    assert.doesNotMatch(html, /id="pub-authorize"/);
  });
});

test("publisher context offers complete DEV apply batches and their exact redlines", () => {
  const core = makeCore(() => 1000);
  core.suggest({ id: "eligible-1", editor: "slot:john", scope: "edit", origin: "human",
    kind: "prose", source_ref: "data/x.json#title", original_text: "Before",
    original_hash: "hash", new_text: "After", map_version: "v1" }, {}, { directApply: true });
  core.claimBatch("batch-eligible", { base_sha: "base", ids: ["eligible-1"] });
  core.finalize("batch-eligible", { phase: "done", applied: ["eligible-1"],
    commit_sha: "commit-eligible", generator_id: "generator-v1" });
  const projected = core.publisherContext();
  assert.equal(projected.release, null);
  assert.equal(projected.batches.length, 1);
  assert.equal(projected.batches[0].changes[0].original_text, "Before");
  assert.equal(projected.batches[0].changes[0].new_text, "After");
});

test("prepared page discloses exact immutable release before one deliberate control", async () => {
  const html = await renderPublisherPage(context, "DR").text();
  assert.match(html, /Production Publisher/);
  assert.match(html, /Immutable prepared preview/);
  assert.match(html, /release-7/);
  assert.match(html, /batch-1/);
  assert.match(html, /batch-2/);
  assert.match(html, /Old title/);
  assert.match(html, /New title/);
  assert.match(html, /Available on DEV — waiting for Publisher/);
  assert.match(html, /type="button"[^>]*id="pub-authorize"/);
  assert.match(html, /<details/);
  assert.match(html, /aria-live="polite"/);
  assert.doesNotMatch(html, /Publish automatically|Execute now|Retry deployment/);
});

test("non-prepared lifecycle states explain status and never render authorization", async () => {
  for (const state of ["draft", "authorized", "executing", "delayed", "failed_fenced",
    "restoring", "restored", "verified", "complete"]) {
    const html = await renderPublisherPage({ ...context, release: { ...prepared, state } }, "DR").text();
    assert.match(html, new RegExp(`data-release-state="${state}"`));
    assert.doesNotMatch(html, /id="pub-authorize"/);
  }
});

test("publisher assets bind the immutable payload, announce results, restore focus, and stack on phones", () => {
  for (const field of ["target_batch_id", "base_sha", "candidate_sha", "membership_hash",
    "manifest_hash", "evidence_hash"]) assert.match(PUBLISHER_JS, new RegExp(field));
  assert.match(PUBLISHER_JS, /X-Edit-Request/);
  assert.match(PUBLISHER_JS, /ariaBusy/);
  assert.match(PUBLISHER_JS, /focus\(\)/);
  assert.match(PUBLISHER_JS, /crypto\.subtle\.digest\("SHA-256"/);
  assert.doesNotMatch(PUBLISHER_JS, /randomUUID|Date\.now/);
  assert.match(PUBLISHER_CSS, /@media \(max-width:640px\)/);
  assert.match(PUBLISHER_CSS, /:focus-visible/);
  assert.match(PUBLISHER_CSS, /grid-template-columns:1fr/);
});

test("publisher route is distinct, human Publisher-only, and review links to it", async () => {
  const response = await editorFetch(new Request("https://edit.example/edit/publish"), {
    PROD_RELEASE_LEDGER: "true", EDIT_ORIGIN: "https://edit.example",
    EDITOR: { getByName: () => ({ publisherContext: async () => context }) },
    ACCESS_EMAIL_SLOTS: JSON.stringify({ "damien@example.com": { slot: "damien", scopes: ["publisher"] } }),
    CF_ACCESS_AUD: "aud", CF_ACCESS_TEAM_DOMAIN: "team.example",
  }, {});
  // No forged identity: an unauthenticated request is indistinguishable from an unknown route.
  assert.equal(response.status, 404);
});
