import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { publisherAuthorizeEndpoint, publisherReleaseEndpoint, productionPrepareEndpoint,
  productionPreparationContextEndpoint, productionClaimEndpoint,
  productionRenewEndpoint, productionTransitionEndpoint,
  productionRestoreClaimEndpoint, productionAuditEndpoint } from "../src/editor-endpoints.js";
import { editorFetch } from "../src/editor.js";

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

test("production audit is text-free and reports migration integrity", async () => {
  const core = makeCore(() => 1250);
  seedApplied(core,"batch-audit",["audit-suggestion"],1000);
  const result = core.productionReleaseAudit();
  assert.equal(result.schema_version,1);
  assert.equal(result.counts.apply_batches_done,1);
  assert.equal(result.counts.applied_suggestions,1);
  assert.equal(result.counts.review_migrations,0);
  assert.equal(result.invariants.legacy_receipts_without_operations,0);
  assert.equal(result.invariants.submitted_sources_without_revision,0);
  assert.equal(result.invariants.operations_without_revision,0);
  assert.deepEqual(result.active_releases,[]);
  assert.equal(result.migrations_truncated,false);
  assert.equal(result.active_releases_truncated,false);
  assert.equal(JSON.stringify(result).includes("old"),false);
  assert.equal(JSON.stringify(result).includes("new"),false);

  core.sql.exec("INSERT INTO production_review_submission_decisions (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id) VALUES (?,?,?,?,?,?,?)",
    "orphan-review","orphan-revision","orphan-operation","accepted","private note","digest",null);
  const broken = core.productionReleaseAudit();
  assert.equal(broken.invariants.decisions_without_operation,1);
  assert.equal(JSON.stringify(broken).includes("private note"),false);

  const grouped = makeCore(() => 1260);
  grouped.sql.exec("INSERT INTO production_review_submission_decisions (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id) VALUES (?,?,?,?,?,?,?)",
    "group-review","group-revision","group-decision","accepted","","digest","group-1");
  grouped.sql.exec("INSERT INTO production_review_operations (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state) VALUES (?,?,?,?,?,?,?,?,?)",
    "physical-operation","group-decision","group-review","group-revision","data/copy/home.json#lead","group-1","accepted","","unpublished");
  assert.equal(grouped.productionReleaseAudit().invariants.decisions_without_operation,0,
    "a shared group decision is backed by its physical operation through decision_id");

  const corruptionCases = [
    ["legacy_receipts_without_operations",(c) => c.sql.exec("INSERT INTO production_reviews (id,idempotency_key,request_digest,actor,review_revision_id,source_revision,prod_base,receipt_hash,receipt_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)","legacy","key","digest","reviewer","missing-revision","source-v1","prod","receipt","{}",1)],
    ["submitted_sources_without_revision",(c) => c.sql.exec("INSERT INTO production_review_submission_sources (review_id,review_revision_id,source_revision,prod_base,evidence_digest) VALUES (?,?,?,?,?)","review","missing-revision","source-v1","prod","digest")],
    ["submitted_revision_operations_missing",(c) => {
      c.sql.exec("INSERT INTO production_review_revisions (id,source_ref,source_revision,prod_base,commit_sha,original_hash,proposed_hash,original_text,proposed_text,source_original_text,source_proposed_text,suggestion_ids_json,operations_json,evidence_digest,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        "revision","source","source-v1","prod","commit","old","new","old","new","old","new","[]",JSON.stringify([{ id:"unanswered-operation" }]),"digest",1);
      c.sql.exec("INSERT INTO production_review_submission_sources (review_id,review_revision_id,source_revision,prod_base,evidence_digest) VALUES (?,?,?,?,?)",
        "review","revision","source-v1","prod","digest");
    }],
    ["decisions_without_operation",(c) => c.sql.exec("INSERT INTO production_review_submission_decisions (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id) VALUES (?,?,?,?,?,?,?)","review","revision","missing-decision","accepted","","digest",null)],
    ["operations_without_revision",(c) => c.sql.exec("INSERT INTO production_review_operations (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state) VALUES (?,?,?,?,?,?,?,?,?)","operation","operation","review","missing-revision","source",null,"accepted","","unpublished")],
    ["published_operations_without_release",(c) => c.sql.exec("INSERT INTO production_published_operations (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,published_at) VALUES (?,?,?,?,?,?,?)","operation","missing-release","revision","source","source-v1","candidate",1)],
  ];
  for (const [name,corrupt] of corruptionCases) {
    const corruptCore = makeCore(() => 1270);
    corrupt(corruptCore);
    assert.equal(corruptCore.productionReleaseAudit().invariants[name],1,
      `${name} must have a positive corruption canary`);
  }

  const request = new Request("https://worker.example/edit/v1/prod/releases/audit");
  const env = { PROD_RELEASE_LEDGER:"true", EDITOR:{ getByName:() => ({
    productionReleaseAudit:async () => result }) } };
  const service = { editor:"service:release",credential_channel:"bearer",
    scopes:{ release_service:{ granted:true } } };
  assert.equal((await productionAuditEndpoint(request,env,service)).status,200);
  assert.equal((await productionAuditEndpoint(request,env,{ ...service,
    credential_channel:"access" })).status,403);
  assert.equal((await productionAuditEndpoint(request,env,{ editor:"slot:damien",
    credential_channel:"access",scopes:{ publisher:{ granted:true } } })).status,403);
  assert.equal((await productionAuditEndpoint(request,{ ...env,PROD_RELEASE_LEDGER:"false" },service)).status,404);
});

test("production audit bounds migrations and active releases", () => {
  const core = makeCore(() => 1300);
  for (let i=0;i<101;i++) core.sql.exec(
    "INSERT INTO production_review_migrations (id,prod_base,evidence_digest,revision_count,actor,created_at) VALUES (?,?,?,?,?,?)",
    `migration-${String(i).padStart(3,"0")}`,"prod-base","digest",1,"service",i);
  for (let i=0;i<21;i++) core.sql.exec(`INSERT INTO production_releases
    (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
     target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
     membership_hash,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    `release-${i}`,`key-${i}`,`digest-${i}`,"prepared","service","bearer","production",
    `batch-${i}`,"base","candidate","generator","evidence","manifest",`membership-${i}`,i,i);
  const audit = core.productionReleaseAudit();
  assert.equal(audit.migrations.length,100);
  assert.equal(audit.migrations_truncated,true);
  assert.equal(audit.active_releases.length,20);
  assert.equal(audit.active_releases_truncated,true);

  core.sql.exec("INSERT INTO production_review_migrations (id,prod_base,evidence_digest,revision_count,actor,created_at) VALUES (?,?,?,?,?,?)",
    "é".repeat(129),"prod","digest",1,"service",999);
  const bounded = core.productionReleaseAudit();
  assert.equal(bounded.invariants.oversized_migration_fields,1);
  assert.equal(bounded.migrations.at(-1).id,null);
});

test("routed production audit accepts only dedicated release read bearers", async () => {
  const audit = { schema_version:1,counts:{},invariants:{},migrations:[],release_states:[],
    active_releases:[],migrations_truncated:false,active_releases_truncated:false };
  let calls = 0;
  const env = {
    PROD_RELEASE_LEDGER:"true",SESSION_SIGNING_KEY:"test-signing-key",
    EDIT_TOKEN_SCOPES:JSON.stringify({ release:{ release_service:1 },observer:{ release_observer:1 },
      admin:{ admin:1 } }),
    EDIT_TOKEN_RELEASE:"release-secret",EDIT_TOKEN_OBSERVER:"observer-secret",
    EDIT_TOKEN_ADMIN:"admin-secret",
    EDIT_ORIGIN:"https://edit.example",EDITOR:{ getByName:() => ({
      productionReleaseAudit:async () => { calls++; return audit; },
    }) },
  };
  const routed = (token) => editorFetch(new Request(
    "https://edit.example/edit/v1/prod/releases/audit",{
      headers:{ Authorization:`Bearer ${token}`,"X-Edit-Request":"1" },
    }),env,{});
  const ok = await routed("release-secret");
  assert.equal(ok.status,200);
  assert.deepEqual((await ok.json()).audit,audit);
  assert.equal(calls,1);
  assert.equal((await routed("observer-secret")).status,200);
  assert.equal(calls,2);
  assert.equal((await routed("admin-secret")).status,403);
  assert.equal((await routed("wrong-secret")).status,403);
  assert.equal(calls,2,"forbidden callers never reach the Durable Object RPC");
  assert.equal((await editorFetch(new Request(
    "https://edit.example/edit/v1/prod/releases/audit",{
      headers:{ Authorization:"Bearer release-secret","X-Edit-Request":"1" },
    }),{ ...env,PROD_RELEASE_LEDGER:"false" },{})).status,404);
});

function authorize(prepared, over = {}) {
  return { id:prepared.id, idempotency_key:"authorize-1", request_digest:"authorize-digest-1",
    actor:"slot:damien", credential_channel:"access", base_sha:prepared.base_sha,
    candidate_sha:prepared.candidate_sha, generator_id:prepared.generator_id,
    evidence_hash:prepared.evidence_hash, manifest_hash:prepared.manifest_hash,
    membership_hash:prepared.membership_hash, ...over };
}

function reviewedProjection(core) {
  const sourceRef = "data/copy/home.json#lead";
  const operations = [
    { id:"op-accepted",decision_id:"op-accepted",kind:"replace",source_ref:sourceRef,
      source_revision:"dev-1",prod_base:"prod-base",base_range:[4,7],old_text:"bad",new_text:"good" },
    { id:"op-held",decision_id:"op-held",kind:"insert",source_ref:sourceRef,
      source_revision:"dev-1",prod_base:"prod-base",base_range:[12,12],old_text:"",new_text:"," },
  ];
  assert.equal(core.recordReviewRevision({ id:"revision-v2",source_ref:sourceRef,
    source_revision:"dev-1",prod_base:"prod-base",commit_sha:"dev-1",
    original_hash:"old",proposed_hash:"new",original_text:"The bad idea",
    proposed_text:"The good idea,",suggestion_ids:["suggestion-parent"],operations }).ok,true);
  const decisions = [{ operation_id:"op-accepted",decision:"accepted" },
    { operation_id:"op-held",decision:"rejected",note:"Hold." }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:"revision-v2",
    source_revision:"dev-1",prod_base:"prod-base",decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-v2",idempotency_key:"review-v2",
    request_digest:"review-v2",actor:"slot:damien",review_revision_id:"revision-v2",
    source_revision:"dev-1",prod_base:"prod-base",decisions }).ok,true);
  return core.productionPreparationContext().projection;
}

test("v2 preparation freezes exact reviewed operation membership and held exclusions", () => {
  const core = makeCore(() => 2000);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const input = release({ schema_version:2,target_batch_id:"operation-frontier",
    candidate_sha:"candidate-v2",review_receipt_hash:receipt,
    projection_identity:"projection-v2",accepted_operation_ids:["op-accepted"],
    held_exclusions:[{ operation_id:"op-held",decision:"rejected",reason:"held" }] });
  const enlarged = core.prepareProductionRelease({ ...input,id:"release-enlarged",
    idempotency_key:"release-enlarged",request_digest:"release-enlarged",
    accepted_operation_ids:["op-accepted","op-held"] });
  assert.equal(enlarged.reason,"operation_membership_mismatch");
  const prepared = core.prepareProductionRelease(input);
  assert.equal(prepared.ok,true);
  assert.equal(prepared.release.schema_version,2);
  assert.deepEqual(prepared.release.operation_ids,["op-accepted"]);
  assert.deepEqual(prepared.release.held_exclusions.map((x) => x.operation_id),["op-held"]);
  assert.equal(prepared.release.review_receipt_hash,receipt);
  assert.equal(prepared.release.projection_identity,"projection-v2");

});

test("v2 preparation expands one accepted move decision to both immutable endpoints", () => {
  const core = makeCore(() => 2500);
  const sourceRef = "data/copy/home.json#lead";
  const operations = [
    { id:"move-from",decision_id:"move-1",move_pair_id:"move-1",move_role:"from",
      kind:"delete",source_ref:sourceRef,source_revision:"dev-1",prod_base:"prod-base",
      base_range:[0,26],proposed_range:[0,0],old_text:"Distinctive moved phrase. ",new_text:"" },
    { id:"move-to",decision_id:"move-1",move_pair_id:"move-1",move_role:"to",
      kind:"insert",source_ref:sourceRef,source_revision:"dev-1",prod_base:"prod-base",
      base_range:[31,31],proposed_range:[5,31],old_text:"",new_text:"Distinctive moved phrase. " },
  ];
  assert.equal(core.recordReviewRevision({ id:"revision-move",source_ref:sourceRef,
    source_revision:"dev-1",prod_base:"prod-base",commit_sha:"dev-1",
    original_hash:"old",proposed_hash:"new",original_text:"Distinctive moved phrase. Rest",
    proposed_text:"Rest Distinctive moved phrase.",suggestion_ids:["suggestion-parent"],operations }).ok,true);
  const decisions = [{ operation_id:"move-1",decision:"accepted" }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:"revision-move",
    source_revision:"dev-1",prod_base:"prod-base",decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-move",idempotency_key:"review-move",
    request_digest:"review-move",actor:"slot:damien",sources:[{
      review_revision_id:"revision-move",source_revision:"dev-1",prod_base:"prod-base",decisions }] }).ok,true);
  const receipt = core.productionPreparationContext().projection.review_receipts[0].receipt_hash;

  const prepared = core.prepareProductionRelease(release({ id:"release-move",idempotency_key:"release-move",
    request_digest:"release-move",schema_version:2,target_batch_id:"operation-frontier",
    candidate_sha:"candidate-move",review_receipt_hash:receipt,projection_identity:"projection-move",
    accepted_operation_ids:["move-from","move-to"],held_exclusions:[] }));

  assert.equal(prepared.ok,true);
  assert.deepEqual(prepared.release.operation_ids,["move-from","move-to"]);
});

test("structural operations and dependent prose stay held outside production membership", () => {
  const core = makeCore(() => 2750);
  const sourceA = "data/copy/home.json#a";
  const sourceB = "data/copy/home.json#b";
  const operations = [{ id:"merge-operation",decision_id:"merge-operation",kind:"merge",op:"merge",
    source_ref:sourceA,op_arg:sourceB,source_revision:"dev-merge",prod_base:"prod-base" }];
  assert.equal(core.recordReviewRevision({ id:"revision-merge",source_ref:sourceA,
    source_revision:"dev-merge",prod_base:"prod-base",commit_sha:"dev-merge",
    original_hash:"a-old",proposed_hash:"a-new",original_text:"A",proposed_text:"A B",
    suggestion_ids:["suggestion-merge"],operations }).ok,true);
  assert.equal(core.recordReviewRevision({ id:"revision-b",source_ref:sourceB,
    source_revision:"dev-b",prod_base:"prod-base",commit_sha:"dev-b",
    original_hash:"b-old",proposed_hash:"b-new",original_text:"B",proposed_text:"B edited",
    suggestion_ids:["suggestion-b"],operations:[{ id:"operation-b",kind:"replace",
      source_ref:sourceB,source_revision:"dev-b",prod_base:"prod-base",base_range:[0,1],
      old_text:"B",new_text:"B edited" }] }).ok,true);
  const decisions = [{ operation_id:"merge-operation",decision:"accepted" }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
    review_revision_id:"revision-merge",source_revision:"dev-merge",prod_base:"prod-base",
    decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-merge",idempotency_key:"review-merge",
    request_digest:"review-merge",actor:"slot:damien",sources:[{
      review_revision_id:"revision-merge",source_revision:"dev-merge",prod_base:"prod-base",
      decisions }] }).ok,true);
  const proseDecisions = [{ operation_id:"operation-b",decision:"accepted" }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
    review_revision_id:"revision-b",source_revision:"dev-b",prod_base:"prod-base",
    decisions:proseDecisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-b",idempotency_key:"review-b",
    request_digest:"review-b",actor:"slot:damien",sources:[{
      review_revision_id:"revision-b",source_revision:"dev-b",prod_base:"prod-base",
      decisions:proseDecisions }] }).ok,true);
  const projection = core.productionPreparationContext().projection;
  assert.equal(projection.eligible_operation_count,0);
  const reviewContext = core.getPublisherReview("slot:damien");
  assert.equal(reviewContext.counts.held,2);
  assert.equal(reviewContext.counts.unreviewed,0);
  assert.equal(reviewContext.counts.reviewed,0);
  assert.equal(reviewContext.counts.accepted,0);
  assert.deepEqual(projection.sources.flatMap((source) => source.operations)
    .map(({ id,production_hold_reason }) => [id,production_hold_reason]).sort(),[
      ["merge-operation","structural_prod_deferred"],
      ["operation-b","depends_on_structural_prod_deferred"],
    ]);
  const receipt = projection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ id:"release-merge",
    idempotency_key:"release-merge",request_digest:"release-merge",schema_version:2,
    target_batch_id:"operation-frontier",candidate_sha:"candidate-merge",
    review_receipt_hash:receipt,projection_identity:"projection-merge",
    review_receipts:projection.review_receipts.map((item) => item.receipt_hash),
    accepted_operation_ids:["merge-operation"],held_exclusions:[] }));
  assert.equal(prepared.reason,"operation_membership_mismatch");
});

test("v2 partial completion publishes operations without stamping their parent suggestion", () => {
  const core = makeCore(() => 3000);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ schema_version:2,
    target_batch_id:"operation-frontier",candidate_sha:"candidate-v2",
    review_receipt_hash:receipt,projection_identity:"projection-v2",
    accepted_operation_ids:["op-accepted"],held_exclusions:[
      { operation_id:"op-held",decision:"rejected",reason:"held" }] })).release;
  const authorized = core.authorizeProductionRelease(authorize(prepared,{
    review_receipt_hash:receipt,projection_identity:"projection-v2"})).release;
  const claimed = core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer",id:authorized.id }).release;
  for (const state of ["pages_deployed","worker_deployed","verified","complete"])
    assert.equal(core.transitionProductionRelease({ id:claimed.id,state,actor:"service:release",
      credential_channel:"bearer",fencing_token:claimed.fencing_token,
      detail:{ candidate_sha:"candidate-v2"} }).ok,true);
  assert.deepEqual(core.getProductionRelease(prepared.id).published_operation_ids,["op-accepted"]);
  assert.equal(core._one("SELECT production_release_id FROM suggestions WHERE id='suggestion-parent'"),undefined);
});

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

test("characterization: filtering ledger rows cannot exclude one applied sibling", () => {
  const core = makeCore(() => 1000);
  seedApplied(core, "batch-1", ["accepted-intent", "rejected-intent"], 1100);

  // The legacy frontier is suggestion/batch membership, not accepted atomic operations. Asking
  // preparation to omit a sibling fails; an unfiltered preparation freezes both DEV members.
  assert.equal(core.prepareProductionRelease(release({ target_batch_id:"batch-1",
    candidate_sha:"commit-batch-1", expected_suggestion_ids:["accepted-intent"] })).reason,
    "membership_mismatch");
  const prepared = core.prepareProductionRelease(release({ id:"release-all-dev",
    idempotency_key:"idem-all-dev", request_digest:"digest-all-dev",
    target_batch_id:"batch-1", candidate_sha:"commit-batch-1" })).release;
  assert.deepEqual(prepared.suggestion_ids, ["accepted-intent", "rejected-intent"]);
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

test("trusted builder freezes submitted projection evidence and held leak canaries", () => {
  const core = makeCore(() => 1200);
  const sourceRef = "data/copy/home.json#lead";
  const operations = [
    { id:"accepted",decision_id:"accepted",kind:"replace",source_ref:sourceRef,
      source_revision:"dev-1",prod_base:"prod-1",base_range:[4,7],old_text:"bad",new_text:"good" },
    { id:"held",decision_id:"held",kind:"insert",source_ref:sourceRef,
      source_revision:"dev-1",prod_base:"prod-1",base_range:[12,12],old_text:"",new_text:"," },
  ];
  assert.equal(core.recordReviewRevision({ id:"revision-1",source_ref:sourceRef,
    source_revision:"dev-1",prod_base:"prod-1",commit_sha:"dev-1",
    original_hash:"old",proposed_hash:"new",original_text:"The bad idea",
    proposed_text:"The good idea,",suggestion_ids:["s1"],operations }).ok,true);
  const decisions = [{ operation_id:"accepted",decision:"accepted" },
    { operation_id:"held",decision:"rejected",note:"Keep this out." }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:"revision-1",
    source_revision:"dev-1",prod_base:"prod-1",decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-1",idempotency_key:"submit-1",
    request_digest:"digest-1",actor:"slot:damien",review_revision_id:"revision-1",
    source_revision:"dev-1",prod_base:"prod-1",decisions }).ok,true);

  const projection = core.productionPreparationContext().projection;
  assert.ok(projection.review_receipts[0].receipt_hash);
  assert.deepEqual(projection.sources[0].decisions.map((item) => item.decision),
    ["accepted","rejected"]);
  assert.equal(projection.sources[0].operations[1].new_text,","); // positive leak canary
  assert.equal(projection.sources[0].stale,false);
  assert.equal("draft" in projection.sources[0],false);
});

test("trusted builder omits fully published reviews without blocking later sources", () => {
  const core = makeCore(() => 1250);
  const recordAccepted = ({ suffix, sourceRef, prodBase }) => {
    const revisionId = `revision-${suffix}`;
    const operationId = `operation-${suffix}`;
    const reviewId = `review-${suffix}`;
    const operations = [{ id:operationId,decision_id:operationId,kind:"replace",
      source_ref:sourceRef,source_revision:`dev-${suffix}`,prod_base:prodBase,
      base_range:[0,3],old_text:"old",new_text:"new" }];
    assert.equal(core.recordReviewRevision({ id:revisionId,source_ref:sourceRef,
      source_revision:`dev-${suffix}`,prod_base:prodBase,commit_sha:`dev-${suffix}`,
      original_hash:`old-${suffix}`,proposed_hash:`new-${suffix}`,original_text:"old",
      proposed_text:"new",suggestion_ids:[`suggestion-${suffix}`],operations }).ok,true);
    const decisions = [{ operation_id:operationId,decision:"accepted" }];
    assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
      review_revision_id:revisionId,source_revision:`dev-${suffix}`,prod_base:prodBase,
      decisions }).ok,true);
    assert.equal(core.submitPublisherReview({ id:reviewId,idempotency_key:reviewId,
      request_digest:reviewId,actor:"slot:damien",sources:[{ review_revision_id:revisionId,
        source_revision:`dev-${suffix}`,prod_base:prodBase,decisions }] }).ok,true);
    return { operationId, reviewId, revisionId, sourceRef };
  };

  const published = recordAccepted({ suffix:"published",
    sourceRef:"data/copy/home.json#lead",prodBase:"prod-base" });
  core.sql.exec(`INSERT INTO production_published_operations
    (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,published_at)
    VALUES (?,?,?,?,?,?,?)`,published.operationId,"release-published",published.revisionId,
    published.sourceRef,"dev-published","candidate-published",1240);
  const pending = recordAccepted({ suffix:"pending",
    sourceRef:"data/copy/home.json#cta",prodBase:"candidate-published" });

  const projection = core.productionPreparationContext().projection;
  assert.deepEqual(projection.sources.map((source) => source.source_ref),[pending.sourceRef]);
  assert.deepEqual(projection.review_receipts.map((receipt) => receipt.id),[pending.reviewId]);
  assert.equal(projection.sources[0].stale,false);
});

test("unpublished semantic groups retain superseded endpoints across sources", () => {
  const core = makeCore(() => 1275);
  const groupId = "group-cross-source";
  const record = ({ revisionId,sourceRef,operationId,sourceRevision,group = groupId }) => {
    const operations = [{ id:operationId,decision_id:group ? "group-decision" : operationId,
      group_id:group,kind:"replace",source_ref:sourceRef,source_revision:sourceRevision,
      prod_base:"prod-base",base_range:[0,3],old_text:"old",new_text:"new" }];
    assert.equal(core.recordReviewRevision({ id:revisionId,source_ref:sourceRef,
      source_revision:sourceRevision,prod_base:"prod-base",commit_sha:sourceRevision,
      original_hash:`old-${revisionId}`,proposed_hash:`new-${revisionId}`,
      original_text:"old",proposed_text:"new",suggestion_ids:[`suggestion-${revisionId}`],
      operations }).ok,true);
    const decisions = [{ operation_id:group ? "group-decision" : operationId,
      decision:"accepted" }];
    assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:revisionId,
      source_revision:sourceRevision,prod_base:"prod-base",decisions }).ok,true);
    return { review_revision_id:revisionId,source_revision:sourceRevision,
      prod_base:"prod-base",decisions };
  };
  const sourceA = "data/copy/home.json#a";
  const sourceB = "data/copy/home.json#b";
  const firstSources = [
    record({ revisionId:"revision-a1",sourceRef:sourceA,
      operationId:"operation-a1",sourceRevision:"dev-1" }),
    record({ revisionId:"revision-b1",sourceRef:sourceB,
      operationId:"operation-b1",sourceRevision:"dev-1" }),
  ];
  assert.equal(core.submitPublisherReview({ id:"review-group",idempotency_key:"review-group",
    request_digest:"review-group",actor:"slot:damien",sources:firstSources }).ok,true);
  const later = record({ revisionId:"revision-a2",sourceRef:sourceA,
    operationId:"operation-a2",sourceRevision:"dev-2",group:null });
  assert.equal(core.submitPublisherReview({ id:"review-a2",idempotency_key:"review-a2",
    request_digest:"review-a2",actor:"slot:damien",sources:[later] }).ok,true);

  const projection = core.productionPreparationContext().projection;
  assert.deepEqual(projection.sources.map((source) => source.review_revision_id).sort(),
    ["revision-a1","revision-a2","revision-b1"]);
  assert.equal(projection.sources.find((source) => source.review_revision_id === "revision-a1").stale,
    true);
  const prepared = core.prepareProductionRelease(release({ id:"release-superseded-group",
    idempotency_key:"release-superseded-group",request_digest:"release-superseded-group",
    schema_version:2,target_batch_id:"operation-frontier",candidate_sha:"candidate-a2",
    review_receipt_hash:"multi-receipt",projection_identity:"projection-a2",
    accepted_operation_ids:["operation-a2"],held_exclusions:[
      { operation_id:"operation-a1",decision:"accepted",reason:"stale" },
      { operation_id:"operation-b1",decision:"accepted",reason:"group_held" },
    ] }));
  assert.equal(prepared.ok,true);
  assert.deepEqual(prepared.release.operation_ids,["operation-a2"]);
});

test("v2 completion removes published operations from the eligibility summary", () => {
  const core = makeCore(() => 1280);
  seedApplied(core,"batch-parent",["suggestion-parent"],1200);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ id:"release-summary-v2",
    idempotency_key:"release-summary-v2",request_digest:"release-summary-v2",schema_version:2,
    target_batch_id:"operation-frontier",candidate_sha:"candidate-summary-v2",
    review_receipt_hash:receipt,projection_identity:"projection-summary-v2",
    accepted_operation_ids:["op-accepted"],held_exclusions:[
      { operation_id:"op-held",decision:"rejected",reason:"held" }] })).release;
  assert.equal(core.publisherSummary().eligible,0);
  const authorized = core.authorizeProductionRelease(authorize(prepared,{ id:prepared.id,
    review_receipt_hash:receipt,projection_identity:"projection-summary-v2" })).release;
  assert.equal(core.publisherSummary().eligible,0);
  const claimed = core.claimAuthorizedProductionRelease({ actor:"service:release",
    credential_channel:"bearer",id:authorized.id }).release;
  assert.equal(core.publisherSummary().eligible,0);
  for (const state of ["pages_deployed","worker_deployed","verified"]) {
    assert.equal(core.transitionProductionRelease({ id:claimed.id,state,actor:"service:release",
      credential_channel:"bearer",fencing_token:claimed.fencing_token,
      detail:{ candidate_sha:"candidate-summary-v2"} }).ok,true);
    assert.equal(core.publisherSummary().eligible,0);
  }
  assert.equal(core.transitionProductionRelease({ id:claimed.id,state:"complete",actor:"service:release",
    credential_channel:"bearer",fencing_token:claimed.fencing_token,
    detail:{ candidate_sha:"candidate-summary-v2"} }).ok,true);
  assert.equal(core.publisherSummary().eligible,0);
});

test("normalized frontier backfills existing submitted reviews and drives the first v2 summary", () => {
  const core = makeCore(() => 1285);
  reviewedProjection(core);
  assert.equal(core.publisherSummary().eligible,1);
  core.sql.exec("DELETE FROM production_review_operations WHERE operation_id='op-accepted'");
  core.sql.exec("DELETE FROM editor_schema_migrations WHERE id='production-review-operations-v1'");
  assert.equal(core._one("SELECT COUNT(*) AS count FROM production_review_operations").count,1);
  core.initSchema();
  assert.equal(core.publisherSummary().eligible,1);
  assert.equal(core._one("SELECT COUNT(*) AS count FROM production_review_operations").count,2);
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
const scopes = (publisher = false, admin = false, releaseService = false, observer = false) => ({ edit:{granted:false},
  instructor:{granted:false}, admin:{granted:admin}, publisher:{granted:publisher},
  release_service:{granted:releaseService}, release_observer:{granted:observer} });

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
  const operationBinding = { ...binding,id:"release-v2",idempotency_key:"prepare-v2",
    schema_version:2,review_receipt_hash:"receipt-set",review_receipts:["receipt-1"],
    projection_identity:"projection-v2",accepted_operation_ids:["op-1"],
    held_exclusions:[{ operation_id:"op-2",decision:"rejected",reason:"rejected" }] };
  assert.equal((await productionPrepareEndpoint(req("/edit/v1/prod/releases/prepare",
    operationBinding),env,auth)).status,201);
  assert.deepEqual(calls.at(-1)[1].accepted_operation_ids,["op-1"]);
  assert.equal((await productionPrepareEndpoint(req("/edit/v1/prod/releases/prepare",
    { ...operationBinding,id:"bad-v2",review_receipts:undefined }),env,auth)).status,400);
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
  assert.equal((await productionPrepareEndpoint(req(
    "/edit/v1/prod/releases/prepare",binding),env,humanAdmin)).status,403);
  assert.equal((await productionPreparationContextEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/frontier"),env,humanAdmin)).status,403);
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,humanAdmin)).status,403);
  assert.equal((await productionRestoreClaimEndpoint(req(
    "/edit/v1/prod/releases/restore-claim",{ id:"release-1" }),env,humanAdmin)).status,403);
  assert.equal((await productionRenewEndpoint(req("/edit/v1/prod/releases/renew",
    { id:"release-1",fencing_token:"fence" }),env,humanAdmin)).status,403);
  assert.equal((await productionTransitionEndpoint(req("/edit/v1/prod/releases/transition",
    { id:"release-1",state:"verified",fencing_token:"fence" }),env,humanAdmin)).status,403);
  const devDaemon = { editor:"service:apply",credential_channel:"bearer",scopes:scopes(false,true) };
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,devDaemon)).status,403);
  assert.deepEqual(calls.map((x) => x[0]),
    ["prepare","prepare","frontier","claim","restore-claim","renew","transition"]);
});

test("release observer can read only status frontier and audit", async () => {
  const calls = [];
  const stub = {
    getProductionRelease:async (id) => (calls.push(["status",id]),
      { id,state:"prepared",base_sha:"a".repeat(40),candidate_sha:"b".repeat(40) }),
    productionPreparationContext:async () => (calls.push(["frontier"]),
      { active_release:null,base_sha:"a".repeat(40),batches:[] }),
    productionReleaseAudit:async () => (calls.push(["audit"]),
      { counts:{},invariants:{},active_releases:[] }),
    prepareProductionRelease:async () => { throw new Error("observer reached mutation"); },
    claimAuthorizedProductionRelease:async () => { throw new Error("observer reached mutation"); },
    claimProductionRestore:async () => { throw new Error("observer reached mutation"); },
    renewProductionReleaseLease:async () => { throw new Error("observer reached mutation"); },
    transitionProductionRelease:async () => { throw new Error("observer reached mutation"); },
  };
  const env = { EDIT_ORIGIN:"https://edit.example",PROD_RELEASE_LEDGER:"true",
    EDITOR:{ getByName:() => stub } };
  const observer = { editor:"service:observer",credential_channel:"bearer",
    scopes:scopes(false,false,false,true) };
  assert.equal((await publisherReleaseEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/status?id=release-1"),env,observer)).status,200);
  assert.equal((await productionPreparationContextEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/frontier"),env,observer)).status,200);
  assert.equal((await productionAuditEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/audit"),env,observer)).status,200);
  const postMutation = (path, body={}) => new Request("https://edit.example"+path,{
    method:"POST",headers:{ "Content-Type":"application/json","X-Edit-Request":"1",
      Origin:"https://edit.example","Sec-Fetch-Site":"same-origin" },body:JSON.stringify(body) });
  const binding = { id:"release-1",idempotency_key:"prepare-1",target_batch_id:"batch-1",
    base_sha:"base",candidate_sha:"candidate",generator_id:"gen",evidence_hash:"evidence",
    manifest_hash:"manifest",ancestry_verified:true };
  assert.equal((await productionPrepareEndpoint(postMutation(
    "/edit/v1/prod/releases/prepare",binding),env,observer)).status,403);
  assert.equal((await productionClaimEndpoint(postMutation(
    "/edit/v1/prod/releases/claim"),env,observer)).status,403);
  assert.equal((await productionRestoreClaimEndpoint(postMutation(
    "/edit/v1/prod/releases/restore-claim",{id:"release-1"}),env,observer)).status,403);
  assert.equal((await productionRenewEndpoint(postMutation(
    "/edit/v1/prod/releases/renew",{id:"release-1",fencing_token:"fence"}),env,observer)).status,403);
  assert.equal((await productionTransitionEndpoint(postMutation(
    "/edit/v1/prod/releases/transition",{id:"release-1",state:"verified",fencing_token:"fence"}),env,observer)).status,403);
  assert.deepEqual(calls,[["status","release-1"],["frontier"],["audit"]]);

  const cookieObserver = { ...observer,credential_channel:"cookie" };
  assert.equal((await productionAuditEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/audit"),env,cookieObserver)).status,403);
});

test("schema-v2 preparation idempotency binds the operation membership", async () => {
  let prior;
  const stub = { prepareProductionRelease:async (input) => {
    if (!prior) { prior = input; return { ok:true,release:{ id:input.id } }; }
    return input.idempotency_key === prior.idempotency_key && input.request_digest !== prior.request_digest
      ? { ok:false,reason:"idempotency_conflict" } : { ok:true,replay:true,release:{ id:input.id } };
  } };
  const env = { EDIT_ORIGIN:"https://edit.example",PROD_RELEASE_LEDGER:"true",
    EDITOR:{ getByName:() => stub } };
  const auth = { editor:"service:release",credential_channel:"bearer",scopes:scopes(false,false,true) };
  const postPrepare = (body) => productionPrepareEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/prepare",{ method:"POST",
      headers:{ "Content-Type":"application/json","X-Edit-Request":"1",
        Origin:"https://edit.example","Sec-Fetch-Site":"same-origin" },body:JSON.stringify(body) }),env,auth);
  const body = { id:"release-v2",idempotency_key:"prepare-v2",target_batch_id:"operation-frontier",
    base_sha:"base",candidate_sha:"candidate",generator_id:"gen",evidence_hash:"evidence",
    manifest_hash:"manifest",ancestry_verified:true,schema_version:2,
    review_receipt_hash:"receipt-set",review_receipts:["receipt-1"],
    projection_identity:"projection-v2",accepted_operation_ids:["op-1"],held_exclusions:[] };

  assert.equal((await postPrepare(body)).status,201);
  assert.equal((await postPrepare(body)).status,200);
  assert.equal((await postPrepare({ ...body,accepted_operation_ids:["op-1","op-2"] })).status,409);
});
