import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { publisherAuthorizeEndpoint, publisherReleaseEndpoint, productionPrepareEndpoint,
  productionPreparationContextEndpoint, productionClaimEndpoint,
  productionTransitionEndpoint } from "../src/editor-endpoints.js";

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
const scopes = (publisher = false, admin = false) => ({ edit:{granted:false},
  instructor:{granted:false}, admin:{granted:admin}, publisher:{granted:publisher} });

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
    { scopes:scopes(false, true), credential_channel:"bearer" });
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).release, release);
});

test("trusted release service alone can prepare, claim, and transition", async () => {
  const calls = [];
  const stub = {
    prepareProductionRelease:async (x) => (calls.push(["prepare",x]), { ok:true,release:{id:x.id} }),
    productionPreparationContext:async () => (calls.push(["frontier"]), { batches:[] }),
    claimAuthorizedProductionRelease:async (x) => (calls.push(["claim",x]), { ok:true,release:null }),
    transitionProductionRelease:async (x) => (calls.push(["transition",x]), { ok:true }),
  };
  const env = { EDIT_ORIGIN:"https://edit.example", PROD_RELEASE_LEDGER:"true",
    EDITOR:{ getByName:() => stub } };
  const auth = { editor:"service:release", credential_channel:"bearer", scopes:scopes(false,true) };
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
  assert.equal((await productionTransitionEndpoint(req("/edit/v1/prod/releases/transition",
    { id:"release-1",state:"verified",fencing_token:"fence",detail:{ candidate_sha:"candidate"} }),env,auth)).status,200);
  const humanAdmin = { editor:"slot:damien",credential_channel:"access",scopes:scopes(false,true) };
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,humanAdmin)).status,403);
  assert.deepEqual(calls.map((x) => x[0]), ["prepare","frontier","claim","transition"]);
});
