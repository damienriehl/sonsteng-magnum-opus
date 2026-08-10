import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { publisherAuthorizeEndpoint, publisherReleaseEndpoint, productionPrepareEndpoint,
  productionPreparationContextEndpoint, productionClaimEndpoint,
  productionRenewEndpoint, productionTransitionEndpoint,
  productionRestoreClaimEndpoint } from "../src/editor-endpoints.js";

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
    target_batch_id:"batch-2", base_sha:"prod-base", candidate_sha:"commit-batch-2",
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

test("trusted builder sees a text-free exact DEV frontier", () => {
  const core = makeCore(() => 1000);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  seedApplied(core, "batch-2", ["suggestion-0002"], 1200);
  const context = core.productionPreparationContext();
  assert.equal(context.active_release, null);
  assert.deepEqual(context.batches.map((batch) => ({ id:batch.batch_id,
    commit:batch.commit_sha, members:batch.suggestion_ids })), [
      { id:"batch-1",commit:"commit-batch-1",members:["suggestion-0001"] },
      { id:"batch-2",commit:"commit-batch-2",members:["suggestion-0002"] },
    ]);
  assert.equal(JSON.stringify(context).includes("original_text"), false);
  assert.equal(JSON.stringify(context).includes("new_text"), false);
});

test("History revert batches are first-class production frontier members", () => {
  const core = makeCore(() => 1300);
  core.fileRevertRequest({ id:"revert-1",editor:"slot:damien",doc:"data/copy/home.json",
    run_first:"aaaaaaa",run_last:"bbbbbbb",approved:true });
  const recorded = core.recordCanonicalMutation({ id:"revert-1",batch_id:"revert-revert-1",
    actor:"slot:damien",kind:"history_revert",source_ref:"data/copy/home.json",
    original_text:"Edited copy",new_text:"Restored copy",base_sha:"before-revert",
    original_hash:"edited-hash",new_hash:"restored-hash",
    commit_sha:"commit-revert",generator_id:"generator-v1" });
  assert.equal(recorded.ok,true);
  assert.equal(recorded.phase,"merged");
  assert.equal(core.recordCanonicalMutation({ id:"revert-1",batch_id:"revert-revert-1",
    actor:"slot:damien",kind:"history_revert",source_ref:"data/copy/home.json",
    original_text:"Edited copy",new_text:"Restored copy",base_sha:"before-revert",
    original_hash:"edited-hash",new_hash:"restored-hash",
    commit_sha:"commit-revert",generator_id:"generator-v1" }).replay,true);
  assert.equal(core.recordCanonicalMutation({ id:"revert-1",batch_id:"revert-revert-1",
    actor:"slot:damien",kind:"history_revert",source_ref:"data/copy/home.json",
    original_text:"DIFFERENT",new_text:"Restored copy",base_sha:"before-revert",
    original_hash:"different-hash",new_hash:"restored-hash",
    commit_sha:"commit-revert",generator_id:"generator-v1" }).reason,"idempotency_conflict");
  assert.equal(core.productionPreparationContext().batches.length,0);
  const binding = { id:"revert-1",batch_id:"revert-revert-1",actor:"slot:damien",
    source_ref:"data/copy/home.json",original_text:"Edited copy",new_text:"Restored copy",
    original_hash:"edited-hash",new_hash:"restored-hash",
    base_sha:"before-revert",commit_sha:"commit-revert",generator_id:"generator-v1" };
  assert.equal(core.completeCanonicalMutation({ ...binding,commit_sha:"wrong" }).reason,
    "idempotency_conflict");
  assert.equal(core.completeCanonicalMutation(binding).ok,true);
  assert.equal(core.completeCanonicalMutation(binding).replay,true);
  let context = core.productionPreparationContext();
  assert.deepEqual(context.batches.map((batch) => ({ id:batch.batch_id,
    commit:batch.commit_sha,members:batch.suggestion_ids })), [
      { id:"revert-revert-1",commit:"commit-revert",members:["revert-1"] },
    ]);
  const preview = core.publisherContext();
  assert.equal(preview.batches[0].changes[0].kind,"history_revert");
  assert.equal(preview.batches[0].changes[0].editor,"slot:damien");
  assert.equal(preview.batches[0].changes[0].original_text,"Edited copy");
  assert.equal(preview.batches[0].changes[0].new_text,"Restored copy");

  seedApplied(core,"batch-after-revert",["suggestion-after-revert"],1400);
  context = core.productionPreparationContext();
  assert.deepEqual(context.batches.map((batch) => batch.batch_id),
    ["revert-revert-1","batch-after-revert"]);
  assert.deepEqual(context.batches.flatMap((batch) => batch.suggestion_ids),
    ["revert-1","suggestion-after-revert"]);
  const prepared = core.prepareProductionRelease(release({ id:"release-revert-plus-edit",
    idempotency_key:"idem-revert-plus-edit",request_digest:"digest-revert-plus-edit",
    base_sha:"prod-base",target_batch_id:"batch-after-revert",
    candidate_sha:"commit-batch-after-revert",expected_batch_ids:
      ["revert-revert-1","batch-after-revert"],expected_suggestion_ids:
      ["revert-1","suggestion-after-revert"] })).release;
  assert.deepEqual(prepared.suggestion_ids,["revert-1","suggestion-after-revert"]);
  assert.deepEqual(prepared.batches.map((batch) => batch.commit_sha),
    ["commit-revert","commit-batch-after-revert"]);
});

test("History revert evidence is bounded and restricted to public data", () => {
  const core = makeCore();
  for (const [id,source,text] of [
    ["private-revert","twin-secrets/private.json","before"],
    ["oversized-revert","data/public.json","x".repeat(131073)],
    ["multibyte-revert","data/public.json","é".repeat(70000)],
  ]) {
    core.fileRevertRequest({ id,editor:"slot:damien",doc:source,
      run_first:"aaaaaaa",run_last:"bbbbbbb",approved:true });
    assert.equal(core.recordCanonicalMutation({ id,batch_id:`revert-${id}`,
      actor:"slot:damien",kind:"history_revert",source_ref:source,original_text:text,
      new_text:"after",original_hash:"before-hash",new_hash:"after-hash",
      base_sha:"base",commit_sha:"commit",generator_id:"generator-v1" }).reason,
      "validation_error");
  }
});

test("executor claims only authorization and stale fences cannot advance it", () => {
  const core = makeCore(() => 2000);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  assert.equal(core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer" }).release, null);
  core.authorizeProductionRelease(authorize(draft));
  const claimed = core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer" }).release;
  assert.equal(claimed.state, "executing");
  assert.ok(claimed.fencing_token);
  assert.equal(core.claimAuthorizedProductionRelease({ id:claimed.id,actor:"service:other",
    credential_channel:"bearer" }).reason, "lease_active");
  assert.equal(core.transitionProductionRelease({ id:claimed.id,state:"pages_deployed",
    fencing_token:"stale",actor:"service:release",credential_channel:"bearer" }).reason,
    "stale_fence");
  assert.equal(core.transitionProductionRelease({ id:claimed.id,state:"pages_deployed",
    fencing_token:claimed.fencing_token,actor:"service:release",credential_channel:"bearer",
    detail:{ pages_id:"pages-1" } }).ok, true);
  assert.equal(core.transitionProductionRelease({ id:claimed.id,state:"pages_deployed",
    fencing_token:claimed.fencing_token,actor:"service:release",credential_channel:"bearer",
    detail:{ pages_id:"different" } }).reason, "idempotency_conflict");
  assert.equal(core.transitionProductionRelease({ id:claimed.id,state:"verified",
    fencing_token:claimed.fencing_token,actor:"service:release",credential_channel:"bearer" }).reason,
    "targets_incomplete");
});

test("lease heartbeat prevents failover and expires closed", () => {
  let now = 2000;
  const core = makeCore(() => now);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  core.now = () => now;
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  core.authorizeProductionRelease(authorize(draft));
  const service = { actor:"service:release",credential_channel:"bearer",lease_ms:5000 };
  const first = core.claimAuthorizedProductionRelease(service).release;
  const originalExpiry = first.lease_expires_at;
  now += 4000;
  const renewed = core.renewProductionReleaseLease({ id:first.id,
    fencing_token:first.fencing_token,...service });
  assert.equal(renewed.ok,true);
  assert.ok(renewed.lease_expires_at > originalExpiry);
  now = originalExpiry + 1;
  assert.equal(core.claimAuthorizedProductionRelease({ id:first.id,actor:"service:other",
    credential_channel:"bearer" }).reason,"lease_active");
  now = renewed.lease_expires_at;
  assert.equal(core.renewProductionReleaseLease({ id:first.id,
    fencing_token:first.fencing_token,...service }).reason,"lease_expired");
  const failover = core.claimAuthorizedProductionRelease({ id:first.id,actor:"service:other",
    credential_channel:"bearer" }).release;
  assert.notEqual(failover.fencing_token,first.fencing_token);
  assert.equal(core.renewProductionReleaseLease({ id:first.id,
    fencing_token:first.fencing_token,...service }).reason,"stale_fence");
});

test("a worst-case Worker lease remains exclusive beyond five minutes but is bounded", () => {
  let now = 2000;
  const core = makeCore(() => now);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  core.now = () => now;
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  core.authorizeProductionRelease(authorize(draft));
  const service = { actor:"service:release",credential_channel:"bearer" };
  const first = core.claimAuthorizedProductionRelease(service).release;
  const requested = (2 * 240 + 60) * 1000;
  const renewed = core.renewProductionReleaseLease({ id:first.id,
    fencing_token:first.fencing_token,lease_ms:requested,...service });
  assert.equal(renewed.lease_expires_at,now + requested);
  now += 5 * 60 * 1000 + 1;
  assert.equal(core.claimAuthorizedProductionRelease({ id:first.id,actor:"service:other",
    credential_channel:"bearer" }).reason,"lease_active");
  now = renewed.lease_expires_at;
  const failover = core.claimAuthorizedProductionRelease({ id:first.id,actor:"service:other",
    credential_channel:"bearer" }).release;
  assert.notEqual(failover.fencing_token,first.fencing_token);
});

test("completion marks exactly frozen applied IDs once", () => {
  const core = makeCore(() => 2000);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  core.authorizeProductionRelease(authorize(draft));
  const claimed = core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer" }).release;
  const advance = (state) => core.transitionProductionRelease({ id:claimed.id,state,
    fencing_token:claimed.fencing_token,actor:"service:release",credential_channel:"bearer" });
  assert.equal(advance("pages_deployed").ok, true);
  assert.equal(advance("worker_deployed").ok, true);
  assert.equal(advance("verified").ok, true);
  assert.equal(advance("complete").ok, true);
  const row = core.sql.exec("SELECT production_release_id FROM suggestions WHERE id=?",
    "suggestion-0001").toArray()[0];
  assert.equal(row.production_release_id, draft.id);
  assert.equal(advance("complete").replay, true);
});

test("ledger executor contract resumes pages crash through exact completion", () => {
  let now = 2000;
  const core = makeCore(() => now);
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  core.now = () => now;
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  core.authorizeProductionRelease(authorize(draft));
  const service = { actor:"service:release",credential_channel:"bearer",lease_ms:1000 };
  const first = core.claimAuthorizedProductionRelease(service).release;
  const move = (token,state,detail={}) => core.transitionProductionRelease({ id:draft.id,state,detail,
    fencing_token:token,actor:service.actor,credential_channel:"bearer" });
  assert.equal(move(first.fencing_token,"pages_deployed",{ pages_id:"pages-exact" }).ok,true);
  now += 5 * 60 * 1000 + 1;
  const resumed = core.claimAuthorizedProductionRelease(service).release;
  assert.equal(resumed.state,"pages_deployed");
  assert.notEqual(resumed.fencing_token,first.fencing_token);
  assert.equal(move(first.fencing_token,"worker_deployed",{ worker_id:"stale" }).reason,"stale_fence");
  assert.equal(move(resumed.fencing_token,"worker_deployed",{ worker_id:"worker-exact" }).ok,true);
  assert.equal(move(resumed.fencing_token,"verified",{ candidate_sha:"commit-batch-1" }).ok,true);
  assert.equal(move(resumed.fencing_token,"complete",{ candidate_sha:"commit-batch-1" }).ok,true);
  const final = core.getProductionRelease(draft.id);
  assert.equal(final.state,"complete");
  assert.deepEqual(final.events.filter((event) => event.type === "pages_deployed").length,1);
});

test("expired verified release is reclaimable and completes under a fresh fence", () => {
  let now = 2000;
  const core = makeCore(() => now);
  seedApplied(core, "batch-1", ["suggestion-0201"], 1100);
  core.now = () => now;
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  core.authorizeProductionRelease(authorize(draft));
  const service = { actor:"service:release",credential_channel:"bearer",lease_ms:1000 };
  const first = core.claimAuthorizedProductionRelease(service).release;
  const move = (token,state) => core.transitionProductionRelease({ id:draft.id,state,
    detail:{ candidate_sha:"commit-batch-1" },fencing_token:token,
    actor:service.actor,credential_channel:"bearer" });
  assert.equal(move(first.fencing_token,"pages_deployed").ok,true);
  assert.equal(move(first.fencing_token,"worker_deployed").ok,true);
  assert.equal(move(first.fencing_token,"verified").ok,true);
  now += 5 * 60 * 1000 + 1;
  const resumed = core.claimAuthorizedProductionRelease(service).release;
  assert.equal(resumed.state,"verified");
  assert.notEqual(resumed.fencing_token,first.fencing_token);
  assert.equal(move(resumed.fencing_token,"complete").ok,true);
});

test("restored manifest can start a fresh Publisher-authorized attempt", () => {
  let now = 2000;
  const core = makeCore(() => now);
  seedApplied(core, "batch-1", ["suggestion-0301"], 1100);
  core.now = () => now;
  const firstInput = release({ id:"release-attempt-1",idempotency_key:"attempt-1",
    target_batch_id:"batch-1",candidate_sha:"commit-batch-1" });
  const first = core.prepareProductionRelease(firstInput).release;
  core.authorizeProductionRelease(authorize(first));
  const claimed = core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer" }).release;
  const move = (state) => core.transitionProductionRelease({ id:first.id,state,detail:{},
    fencing_token:claimed.fencing_token,actor:"service:release",credential_channel:"bearer" });
  assert.equal(move("failed_fenced").ok,true);
  assert.equal(move("restoring").reason,"invalid_transition");
  const restore = core.claimProductionRestore({ id:first.id,actor:"service:release",
    credential_channel:"bearer",lease_ms:1000 }).release;
  assert.equal(restore.state,"restoring");
  assert.notEqual(restore.fencing_token,claimed.fencing_token);
  assert.equal(core.claimProductionRestore({ id:first.id,actor:"service:other",
    credential_channel:"bearer" }).reason,"lease_active");
  assert.equal(core.transitionProductionRelease({ id:first.id,state:"restored",detail:{},
    fencing_token:claimed.fencing_token,actor:"service:release",
    credential_channel:"bearer" }).reason,"stale_fence");
  now += 1001;
  const reclaimed = core.claimProductionRestore({ id:first.id,actor:"service:other",
    credential_channel:"bearer",lease_ms:1000 }).release;
  assert.notEqual(reclaimed.fencing_token,restore.fencing_token);
  assert.equal(core.renewProductionReleaseLease({ id:first.id,
    fencing_token:restore.fencing_token,actor:"service:release",
    credential_channel:"bearer" }).reason,"stale_fence");
  assert.equal(core.transitionProductionRelease({ id:first.id,state:"restored",detail:{},
    fencing_token:reclaimed.fencing_token,actor:"service:other",
    credential_channel:"bearer" }).ok,true);

  now += 1;
  const retryInput = { ...firstInput,id:"release-attempt-2",idempotency_key:"attempt-2",
    request_digest:"digest-attempt-2" };
  const retried = core.prepareProductionRelease(retryInput);
  assert.equal(retried.ok,true);
  assert.equal(retried.replay,undefined);
  assert.equal(retried.release.state,"prepared");
  assert.equal(retried.release.manifest_hash,first.manifest_hash);
  assert.equal(core.publisherContext().release.id,"release-attempt-2");

  const replay = core.prepareProductionRelease(retryInput);
  assert.equal(replay.ok,true);
  assert.equal(replay.replay,true);
  assert.equal(replay.release.id,"release-attempt-2");
  assert.equal(core.authorizeProductionRelease(authorize(retried.release, {
    idempotency_key:"authorize-attempt-2",request_digest:"authorize-digest-attempt-2" })).ok,true);
  const retryClaim = core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer" }).release;
  assert.equal(retryClaim.id,"release-attempt-2");
  assert.equal(retryClaim.state,"executing");
});

test("authorization is immutable and idempotent only for the identical binding", () => {
  const core = makeCore();
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
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
  const draft = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  assert.equal(core.authorizeProductionRelease(authorize(draft, {
    manifest_hash:"client-mutated-manifest" })).reason, "stale_draft");
  assert.equal(core.getProductionRelease(draft.id).state, "prepared");
});

test("partial batches publish applied members and terminal no-op batches do not block the frontier", () => {
  const core = makeCore();
  seedApplied(core, "batch-1", ["suggestion-0001", "suggestion-0002"], 1100);
  seedApplied(core, "batch-2", ["suggestion-0003"], 1200);
  assert.equal(core.prepareProductionRelease(release({ ancestry_verified:false })).reason,
    "nonancestor_candidate");
  assert.equal(core.prepareProductionRelease(release({ expected_batch_ids:["batch-2"] })).reason,
    "membership_mismatch");
  assert.equal(core.prepareProductionRelease(release({ expected_suggestion_ids:
    ["suggestion-0001", "suggestion-0003"] })).reason, "membership_mismatch");
  core.sql.exec("UPDATE suggestions SET status='drift' WHERE id=?", "suggestion-0001");
  const partial = core.prepareProductionRelease(release()).release;
  assert.deepEqual(partial.suggestion_ids, ["suggestion-0002", "suggestion-0003"]);

  const empty = makeCore();
  seedApplied(empty, "batch-1", ["suggestion-0101"], 1100);
  seedApplied(empty, "batch-2", ["suggestion-0102"], 1200);
  empty.sql.exec("UPDATE suggestions SET status='drift' WHERE id=?", "suggestion-0101");
  const context = empty.productionPreparationContext();
  assert.deepEqual(context.batches.map((batch) => batch.batch_id), ["batch-2"]);
  assert.deepEqual(context.batches[0].suggestion_ids, ["suggestion-0102"]);
});

test("preparation rejects mismatched frontier and every active release state", () => {
  const core = makeCore();
  seedApplied(core, "batch-1", ["suggestion-0001"], 1100);
  assert.equal(core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"not-the-batch-commit" })).reason, "stale_candidate");
  const prepared = core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1" })).release;
  for (const state of ["prepared","authorized","executing","pages_deployed","worker_deployed",
    "verified","failed_fenced","restoring"]) {
    core.sql.exec("UPDATE production_releases SET state=? WHERE id=?", state, prepared.id);
    assert.equal(core.prepareProductionRelease(release({ id:`release-${state}`,
      idempotency_key:`idem-${state}`, target_batch_id:"batch-1",
      candidate_sha:"commit-batch-1" })).reason, "active_release", state);
  }
});

function post(body) {
  return new Request("https://edit.example/edit/v1/prod/releases/authorize", { method:"POST",
    headers:{ "Content-Type":"application/json", "X-Edit-Request":"1",
      Origin:"https://edit.example", "Sec-Fetch-Site":"same-origin" },
    body:JSON.stringify(body) });
}
const scopes = (publisher = false, admin = false, releaseService = false) => ({ edit:{granted:false},
  instructor:{granted:false}, admin:{granted:admin}, publisher:{granted:publisher},
  release_service:{granted:releaseService} });

test("only a human Access Publisher can authorize; bearer and admin-only cannot", async () => {
  let calls = 0;
  const env = { EDIT_ORIGIN:"https://edit.example", PROD_RELEASE_LEDGER:"true",
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
  const env = { PROD_RELEASE_LEDGER:"true", EDITOR:{ getByName:() => ({
    getProductionRelease:async () => release }) } };
  const request = new Request("https://edit.example/edit/v1/prod/releases/status?id=release-1");
  const response = await publisherReleaseEndpoint(request, env,
    { scopes:scopes(false, false, true), credential_channel:"bearer" });
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).release, release);
});

test("trusted release service alone can prepare, claim, and transition", async () => {
  const calls = [];
  const stub = {
    prepareProductionRelease:async (x) => (calls.push(["prepare",x]), { ok:true,release:{id:x.id} }),
    productionPreparationContext:async () => (calls.push(["frontier"]), { batches:[] }),
    claimAuthorizedProductionRelease:async (x) => (calls.push(["claim",x]), { ok:true,release:null }),
    claimProductionRestore:async (x) => (calls.push(["restore-claim",x]),
      { ok:true,release:{ id:x.id,state:"restoring" } }),
    renewProductionReleaseLease:async (x) => (calls.push(["renew",x]), { ok:true }),
    transitionProductionRelease:async (x) => (calls.push(["transition",x]), { ok:true }),
  };
  const env = { EDIT_ORIGIN:"https://edit.example", PROD_RELEASE_LEDGER:"true",
    EDITOR:{ getByName:() => stub } };
  const auth = { editor:"service:release", credential_channel:"bearer", scopes:scopes(false,false,true) };
  const req = (path, body) => new Request("https://edit.example" + path, { method:"POST",
    headers:{ "Content-Type":"application/json", "X-Edit-Request":"1",
      Origin:"https://edit.example", "Sec-Fetch-Site":"same-origin" }, body:JSON.stringify(body) });
  const binding = { id:"release-1",idempotency_key:"prepare-1",target_batch_id:"batch-1",
    base_sha:"base",candidate_sha:"candidate",generator_id:"gen",evidence_hash:"evidence",
    manifest_hash:"manifest",ancestry_verified:true };
  assert.equal((await productionPrepareEndpoint(req("/edit/v1/prod/releases/prepare", binding),env,auth)).status,201);
  assert.equal((await productionPreparationContextEndpoint(
    new Request("https://edit.example/edit/v1/prod/releases/frontier"),env,auth)).status,200);
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,auth)).status,200);
  assert.equal((await productionRestoreClaimEndpoint(req(
    "/edit/v1/prod/releases/restore-claim",{ id:"release-1" }),env,auth)).status,200);
  assert.equal((await productionRenewEndpoint(req("/edit/v1/prod/releases/renew",
    { id:"release-1",fencing_token:"fence" }),env,auth)).status,200);
  assert.equal((await productionTransitionEndpoint(req("/edit/v1/prod/releases/transition",
    { id:"release-1",state:"verified",fencing_token:"fence",detail:{ candidate_sha:"candidate"} }),env,auth)).status,200);
  const humanAdmin = { editor:"slot:damien",credential_channel:"access",scopes:scopes(false,true) };
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,humanAdmin)).status,403);
  assert.equal((await productionRestoreClaimEndpoint(req(
    "/edit/v1/prod/releases/restore-claim",{ id:"release-1" }),env,humanAdmin)).status,403);
  const devDaemon = { editor:"service:apply",credential_channel:"bearer",scopes:scopes(false,true) };
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,devDaemon)).status,403);
  assert.deepEqual(calls.map((x) => x[0]),
    ["prepare","frontier","claim","restore-claim","renew","transition"]);
});
