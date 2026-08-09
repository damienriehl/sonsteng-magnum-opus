import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { publisherAuthorizeEndpoint, publisherReleaseEndpoint } from "../src/editor-endpoints.js";

function seedApplied(core, batchId, ids, at) {
  core.now = () => at;
  for (const id of ids) {
    core.suggest({ id, editor:"slot:john", scope:"edit", origin:"human", kind:"prose",
      source_ref:`data/copy/home.json#${id}`, original_text:"old", original_hash:"hash",
      new_text:"new", map_version:"v1" }, {}, { directApply:true });
  }
  assert.equal(core.claimBatch(batchId, { base_sha:"dev-base", ids }).ok, true);
  assert.equal(core.finalize(batchId, { phase:"done", applied:ids,
    commit_sha:`commit-${batchId}`, generator_id:"generator-v1" }).ok, true);
}

function release(over = {}) {
  return { id:"release-1", idempotency_key:"idem-1", request_digest:"digest-1",
    actor:"service:builder", credential_channel:"bearer", target_environment:"production",
    target_batch_id:"batch-2", base_sha:"prod-base", candidate_sha:"candidate-sha",
    generator_id:"generator-v1", evidence_hash:"evidence-1", manifest_hash:"manifest-1",
    ancestry_verified:true, ...over };
}

function authorize(prepared, over = {}) {
  return { id:prepared.id, idempotency_key:"authorize-1", request_digest:"authorize-digest-1",
    actor:"slot:damien", credential_channel:"access", base_sha:prepared.base_sha,
    candidate_sha:prepared.candidate_sha, generator_id:prepared.generator_id,
    evidence_hash:prepared.evidence_hash, manifest_hash:prepared.manifest_hash,
    membership_hash:prepared.membership_hash, ...over };
}

test("Publisher freezes every complete apply batch through the chosen frontier", () => {
  const core = makeCore(() => 1000);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  seedApplied(core, "batch-2", ["suggestion-0002", "suggestion-0003"], 1200);
  const draft = core.prepareProductionRelease(release());
  const made = core.authorizeProductionRelease(authorize(draft.release));
  assert.equal(made.ok, true);
  assert.deepEqual(made.release.batches.map((b) => b.batch_id), ["batch-1", "batch-2"]);
  assert.deepEqual(made.release.suggestion_ids,
    ["suggestion-0001", "suggestion-0002", "suggestion-0003"]);
  assert.equal(made.release.state, "authorized");
  assert.deepEqual(made.release.events.map((e) => e.type), ["prepared", "authorized"]);
});

test("authorization is immutable and idempotent only for the identical binding", () => {
  const core = makeCore();
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1" })).release;
  const input = authorize(draft);
  assert.equal(core.authorizeProductionRelease(input).ok, true);
  assert.equal(core.authorizeProductionRelease(input).replay, true);
  assert.equal(core.authorizeProductionRelease({ ...input, candidate_sha:"changed",
    request_digest:"changed-digest" }).reason,
    "idempotency_conflict");
  assert.equal(core.authorizeProductionRelease({ ...input, idempotency_key:"authorize-2",
    request_digest:"authorize-digest-2" }).reason, "idempotency_conflict");
});

test("Publisher cannot authorize a mutated prepared draft", () => {
  const core = makeCore();
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1" })).release;
  assert.equal(core.authorizeProductionRelease(authorize(draft, {
    manifest_hash:"client-mutated-manifest" })).reason, "stale_draft");
  assert.equal(core.getProductionRelease(draft.id).state, "prepared");
});

test("partial, stale, nonancestor, and skipped apply-batch membership fail closed", () => {
  const core = makeCore();
  seedApplied(core, "batch-1", ["suggestion-0001", "suggestion-0002"], 1100);
  seedApplied(core, "batch-2", ["suggestion-0003"], 1200);
  assert.equal(core.prepareProductionRelease(release({ ancestry_verified:false })).reason,
    "nonancestor_candidate");
  assert.equal(core.prepareProductionRelease(release({ expected_batch_ids:["batch-2"] })).reason,
    "membership_mismatch");
  assert.equal(core.prepareProductionRelease(release({ expected_suggestion_ids:
    ["suggestion-0001", "suggestion-0003"] })).reason, "membership_mismatch");
  core.sql.exec("UPDATE suggestions SET status='drift' WHERE id=?", "suggestion-0003");
  assert.equal(core.prepareProductionRelease(release()).reason, "stale_member");
});

function post(body) {
  return new Request("https://edit.example/edit/v1/prod/releases/authorize", { method:"POST",
    headers:{ "Content-Type":"application/json", "X-Edit-Request":"1",
      Origin:"https://edit.example", "Sec-Fetch-Site":"same-origin" },
    body:JSON.stringify(body) });
}
const scopes = (publisher = false, admin = false) => ({ edit:{granted:false},
  instructor:{granted:false}, admin:{granted:admin}, publisher:{granted:publisher} });

test("only a human Access Publisher can authorize; bearer and admin-only cannot", async () => {
  let calls = 0;
  const env = { EDIT_ORIGIN:"https://edit.example", EDIT_ENVIRONMENT:"production",
    EDITOR:{ getByName:() => ({ authorizeProductionRelease:async (x) =>
      (calls++, { ok:true, release:{ id:x.id } }) }) } };
  const body = { id:"release-1", idempotency_key:"idem-1", target_batch_id:"batch-1",
    base_sha:"base", candidate_sha:"candidate", generator_id:"gen", evidence_hash:"ev",
    manifest_hash:"man", membership_hash:"members" };
  const human = { editor:"slot:damien", credential_channel:"access", scopes:scopes(true) };
  assert.equal((await publisherAuthorizeEndpoint(post(body), env, human)).status, 201);
  for (const denied of [
    { editor:"slot:damien", credential_channel:"access", scopes:scopes(false, true) },
    { editor:"slot:service", credential_channel:"bearer", scopes:scopes(true, true) },
    { editor:"slot:ai", service:"ai-review", credential_channel:"service", scopes:scopes(true) },
    { editor:"slot:damien", credential_channel:"cookie", scopes:scopes(true) },
  ]) assert.equal((await publisherAuthorizeEndpoint(post(body), env, denied)).status, 403);
  assert.equal(calls, 1);
});

test("authorized membership and audit are machine-readable without edited content", async () => {
  const release = { id:"release-1", state:"authorized", suggestion_ids:["suggestion-0001"],
    events:[{ type:"authorized", actor:"slot:damien" }] };
  const env = { EDIT_ENVIRONMENT:"production", EDITOR:{ getByName:() => ({
    getProductionRelease:async () => release }) } };
  const request = new Request("https://edit.example/edit/v1/prod/releases/status?id=release-1");
  const response = await publisherReleaseEndpoint(request, env,
    { scopes:scopes(false, true), credential_channel:"bearer" });
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).release, release);
});
