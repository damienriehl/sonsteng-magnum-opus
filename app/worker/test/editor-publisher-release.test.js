import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { dirname,resolve } from "node:path";
import { fileURLToPath } from "node:url";
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

function seedSingleOperationProjectionCase(core,{ suffix,decision="accepted",groupId=null,
  lifecycleState="unpublished",legacy=false } = {}) {
  const reviewId = `review-compat-${suffix}`;
  const revisionId = `revision-compat-${suffix}`;
  const operationId = `operation-compat-${suffix}`;
  const sourceRef = `data/copy/compat-${suffix}.json#lead`;
  const sourceRevision = `dev-compat-${suffix}`;
  const operation = { id:operationId,decision_id:operationId,kind:"replace",
    source_ref:sourceRef,source_revision:sourceRevision,prod_base:"prod-base",
    base_range:[0,3],old_text:"old",new_text:"new",...(groupId ? { group_id:groupId } : {}) };
  assert.equal(core.recordReviewRevision({ id:revisionId,source_ref:sourceRef,
    source_revision:sourceRevision,prod_base:"prod-base",commit_sha:sourceRevision,
    original_hash:`old-${suffix}`,proposed_hash:`new-${suffix}`,original_text:"old",
    proposed_text:"new",suggestion_ids:[`suggestion-${suffix}`],operations:[operation] }).ok,true);
  const decisions = decision === "unanswered" ? [] : [{ operation_id:operationId,decision,
    ...(decision === "questioned" ? { note:"What changed?" } : {}) }];
  if (legacy) {
    const normalized = core._normalizeReviewDecisions(core._reviewRevision(revisionId),decisions);
    assert.equal(normalized.reason,undefined);
    const receipt = { review_id:reviewId,actor:"slot:damien",created_at:9002,
      review_revision_id:revisionId,source_revision:sourceRevision,prod_base:"prod-base",
      evidence_digest:core._reviewRevision(revisionId).evidence_digest,
      decisions:normalized.decisions };
    core.sql.exec(`INSERT INTO production_reviews
      (id,idempotency_key,request_digest,actor,review_revision_id,source_revision,prod_base,
       receipt_hash,receipt_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)`,reviewId,reviewId,
    reviewId,"slot:damien",revisionId,sourceRevision,"prod-base",core._digest(receipt),
    core._canonical(receipt),9002);
    if (normalized.decisions.length) core.sql.exec(`INSERT INTO production_review_decisions
      (review_id,operation_id,decision,note,operation_digest,group_id)
      VALUES (?,?,?,?,?,?)`,reviewId,operationId,decision,
    normalized.decisions[0].note,normalized.decisions[0].operation_digest,groupId);
    core.sql.exec(`INSERT INTO production_review_operations
      (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,
       lifecycle_state) VALUES (?,?,?,?,?,?,?,?,?)`,operationId,operationId,reviewId,revisionId,
    sourceRef,groupId,decision,"",lifecycleState);
  } else {
    const draft = core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:revisionId,
      source_revision:sourceRevision,prod_base:"prod-base",decisions });
    assert.equal(draft.ok,true,`${suffix}: ${draft.reason}`);
    assert.equal(core.submitPublisherReview({ id:reviewId,idempotency_key:reviewId,
      request_digest:reviewId,actor:"slot:damien",sources:[{ review_revision_id:revisionId,
        source_revision:sourceRevision,prod_base:"prod-base",decisions }] }).ok,true);
    if (lifecycleState !== "unpublished") core.sql.exec(
      "UPDATE production_review_operations SET lifecycle_state=? WHERE operation_id=?",
      lifecycleState,operationId);
  }
  const parentTable = legacy ? "production_reviews" : "production_review_submissions";
  const receiptHash = core._one(`SELECT receipt_hash FROM ${parentTable} WHERE id=?`,reviewId)
    .receipt_hash;
  return { reviewId,revisionId,operationId,sourceRef,sourceRevision,operation,decision,groupId,
    receiptHash };
}

function expectedSingleOperationProjection(seed) {
  const canonicalOperation = { base_range:[0,3],decision_id:seed.operationId,
    ...(seed.groupId ? { group_id:seed.groupId } : {}),id:seed.operationId,kind:"replace",
    new_text:"new",old_text:"old",prod_base:"prod-base",source_ref:seed.sourceRef,
    source_revision:seed.sourceRevision,production_scope:"prose",production_hold_reason:null };
  return { review_receipts:[{ id:seed.reviewId,actor:"slot:damien",created_at:9002,
    receipt_hash:seed.receiptHash }],sources:[{
    review_id:seed.reviewId,review_revision_id:seed.revisionId,source_ref:seed.sourceRef,
    source_revision:seed.sourceRevision,prod_base:"prod-base",
    original_hash:`old-${seed.reviewId.replace("review-compat-","")}`,
    proposed_hash:`new-${seed.reviewId.replace("review-compat-","")}`,
    original_text:"old",source_original_text:"old",
    operations:[canonicalOperation],
    stale:false,decisions:[{ operation_id:seed.operationId,decision:seed.decision,note:"",
      group_id:seed.groupId }],
  }],eligible_operation_count:1,held_operation_count:0 };
}

function seedPublicationIntegrityCase(core,{ suffix,lifecycleState="published",
  publication=true,releaseState="complete",schemaVersion=2,member=true,
  memberOverrides={},publicationOverrides={} } = {}) {
  const seed = seedSingleOperationProjectionCase(core,{ suffix });
  core.sql.exec("UPDATE production_review_operations SET lifecycle_state=? WHERE operation_id=?",
    lifecycleState,seed.operationId);
  const releaseId = `release-compat-${suffix}`;
  core.sql.exec(`INSERT INTO production_releases
    (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
     target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
     membership_hash,created_at,updated_at,schema_version)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,releaseId,
  releaseId,releaseId,releaseState,"service:release","bearer","production",
  "operation-frontier","prod-base",`candidate-${suffix}`,"generator","evidence","manifest",
  "membership",9003,9003,schemaVersion);
  if (member) core.sql.exec(`INSERT INTO production_release_operation_members
    (release_id,operation_id,review_revision_id,source_ref,affected_source_refs_json,group_id,
     ordinal) VALUES (?,?,?,?,?,?,?)`,releaseId,seed.operationId,
  memberOverrides.review_revision_id || seed.revisionId,
  memberOverrides.source_ref || seed.sourceRef,"[]",seed.groupId,0);
  if (publication) core.sql.exec(`INSERT INTO production_published_operations
    (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,
     published_at) VALUES (?,?,?,?,?,?,?)`,publicationOverrides.operation_id || seed.operationId,
  publicationOverrides.release_id || releaseId,
  publicationOverrides.review_revision_id || seed.revisionId,
  publicationOverrides.source_ref || seed.sourceRef,
  publicationOverrides.source_revision || seed.sourceRevision,
  publicationOverrides.candidate_sha || `candidate-${suffix}`,9003);
  return { ...seed,releaseId };
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
  const publishedProjection = core.productionPreparationContext().projection;
  const publishedReceipt = publishedProjection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ id:"release-published",
    idempotency_key:"release-published",request_digest:"release-published",schema_version:2,
    target_batch_id:"operation-frontier",base_sha:"prod-base",
    candidate_sha:"candidate-published",review_receipt_hash:publishedReceipt,
    review_receipts:[publishedReceipt],projection_identity:"projection-published",
    accepted_operation_ids:[published.operationId],held_exclusions:[],
  })).release;
  const authorized = core.authorizeProductionRelease(authorize(prepared,{
    id:prepared.id,idempotency_key:"authorize-published",request_digest:"authorize-published",
    review_receipt_hash:publishedReceipt,projection_identity:"projection-published",
  })).release;
  const claimed = core.claimAuthorizedProductionRelease({ id:authorized.id,
    actor:"service:release",credential_channel:"bearer" }).release;
  for (const state of ["pages_deployed","worker_deployed","verified","complete"])
    assert.equal(core.transitionProductionRelease({ id:claimed.id,state,actor:"service:release",
      credential_channel:"bearer",fencing_token:claimed.fencing_token,
      detail:{ candidate_sha:prepared.candidate_sha } }).ok,true);
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

test("publisher summary cannot bypass receipt integrity after all normalized rows vanish", async (t) => {
  for (const [suffix,legacy,parentTable] of [
    ["modern",false,"production_review_submissions"],
    ["legacy",true,"production_reviews"],
  ]) await t.test(suffix,() => {
    const core = makeCore(() => 1286);
    seedSingleOperationProjectionCase(core,{ suffix:`publisher-vanished-${suffix}`,legacy });
    core.sql.exec("DELETE FROM production_review_operations");
    assert.equal(core._one(`SELECT COUNT(*) AS count FROM ${parentTable}`).count,1,
      "the immutable receipt parent must remain as the operation-frontier mode signal");
    assert.throws(() => core.publisherSummary(),
      /operation_frontier_integrity:missing_normalized_row/);
  });
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
const scopes = ({ publisher = false, admin = false, releaseService = false,
  releaseObserver = false } = {}) => ({ edit:{granted:false},
  instructor:{granted:false}, admin:{granted:admin}, publisher:{granted:publisher},
  release_service:{granted:releaseService}, release_observer:{granted:releaseObserver} });

function frontierEnv(productionPreparationContext) {
  return { PROD_RELEASE_LEDGER:"true",EDITOR:{ getByName:() => ({
    productionPreparationContext,
  }) } };
}

async function observerFrontierFromProvider(productionPreparationContext) {
  const env = frontierEnv(productionPreparationContext);
  const response = await productionPreparationContextEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/frontier"),env,{
      editor:"service:observer",credential_channel:"bearer",
      scopes:scopes({ releaseObserver:true }),
    });
  assert.equal(response.status,200);
  return (await response.json()).context;
}

async function observerFrontier(context) {
  return observerFrontierFromProvider(async () => context);
}

async function observerFrontierFromCore(core) {
  return observerFrontierFromProvider(async (...args) => core.productionPreparationContext(...args));
}

// Producer-local approximation only. This is not the authoritative Python
// queue consumer and must never be described as a cross-boundary consumer test.
function localFrontierLooksEmpty(frontier) {
  return frontier.pending_operation_count === 0 && frontier.blocked_state === "unblocked";
}

async function assertOperationProjectionCorruptionFailsClosed(core, reason) {
  const env = frontierEnv(async (...args) => core.productionPreparationContext(...args));
  const request = () => new Request("https://edit.example/edit/v1/prod/releases/frontier");
  await assert.rejects(productionPreparationContextEndpoint(request(),env,{
    editor:"service:release",credential_channel:"bearer",
    scopes:scopes({ releaseService:true }),
  }),new RegExp(`operation_frontier_integrity:${reason}`));
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:0,blocked_state:"blocked",
  });
  assert.equal("projection" in context,false,
    "a caught integrity failure must not become a known-empty projection");
}

function assertObserverOperationFrontier(actual, expected) {
  assert.deepEqual(Object.keys(actual).sort(),["blocked_state","pending_operation_count"]);
  assert.deepEqual(actual,expected);
}

function insertCanonicalMalformedEvidence(core,{ suffix,operation }) {
  const reviewId = `review-malformed-${suffix}`;
  const revisionId = `revision-malformed-${suffix}`;
  const sourceRef = `data/copy/malformed-${suffix}.json#lead`;
  const sourceRevision = `dev-malformed-${suffix}`;
  const operations = [{ source_ref:sourceRef,source_revision:sourceRevision,
    prod_base:"prod-base",...operation }];
  const revisionEvidence = { source_ref:sourceRef,source_revision:sourceRevision,
    prod_base:"prod-base",commit_sha:sourceRevision,original_hash:"old",proposed_hash:"new",
    suggestion_ids:[`suggestion-${suffix}`],source_original_text:"old",
    source_proposed_text:"new",operations };
  const evidenceDigest = core._digest(revisionEvidence);
  core.sql.exec(`INSERT INTO production_review_revisions
    (id,source_ref,source_revision,prod_base,commit_sha,original_hash,proposed_hash,
     original_text,proposed_text,source_original_text,source_proposed_text,
     suggestion_ids_json,operations_json,evidence_digest,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,revisionId,sourceRef,sourceRevision,"prod-base",
  sourceRevision,"old","new","old","new","old","new",JSON.stringify([`suggestion-${suffix}`]),
  core._canonical(operations),evidenceDigest,9010);
  const receipt = { review_id:reviewId,actor:"slot:damien",created_at:9010,sources:[{
    review_revision_id:revisionId,source_revision:sourceRevision,prod_base:"prod-base",
    evidence_digest:evidenceDigest,decisions:[],
  }] };
  core.sql.exec(`INSERT INTO production_review_submissions
    (id,idempotency_key,request_digest,actor,receipt_hash,receipt_json,created_at)
    VALUES (?,?,?,?,?,?,?)`,reviewId,reviewId,reviewId,"slot:damien",core._digest(receipt),
  core._canonical(receipt),9010);
  core.sql.exec(`INSERT INTO production_review_submission_sources
    (review_id,review_revision_id,source_revision,prod_base,evidence_digest)
    VALUES (?,?,?,?,?)`,reviewId,revisionId,sourceRevision,"prod-base",evidenceDigest);
  return { reviewId,revisionId,sourceRef };
}

test("authoritative queue consumer cannot appear without an executing boundary test", () => {
  const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)),"../../..");
  const consumerName = ["prove","queues","empty.py"].join("_");
  const consumerPath = resolve(repoRoot,"tools",consumerName);
  assert.equal(existsSync(consumerPath),false,
    `tools/${consumerName} is now present: replace this tripwire in the same change with a test ` +
    "that executes the authoritative consumer");
});

function seedReviewedHeldOperation(core) {
  const sourceRef = "data/copy/home.json#structural";
  const operations = [{ id:"held-operation",decision_id:"held-operation",kind:"merge",op:"merge",
    source_ref:sourceRef,op_arg:"data/copy/home.json#dependent",
    source_revision:"dev-held",prod_base:"prod-base" }];
  assert.equal(core.recordReviewRevision({ id:"revision-held",source_ref:sourceRef,
    source_revision:"dev-held",prod_base:"prod-base",commit_sha:"dev-held",
    original_hash:"old",proposed_hash:"new",original_text:"A",proposed_text:"A B",
    suggestion_ids:["suggestion-held"],operations }).ok,true);
  const decisions = [{ operation_id:"held-operation",decision:"accepted" }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
    review_revision_id:"revision-held",source_revision:"dev-held",prod_base:"prod-base",
    decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-held",idempotency_key:"review-held",
    request_digest:"review-held",actor:"slot:damien",sources:[{
      review_revision_id:"revision-held",source_revision:"dev-held",prod_base:"prod-base",
      decisions }] }).ok,true);
}

test("observer operation frontier is present and empty stores are zero and unblocked", async () => {
  const core = makeCore(() => 9000);
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:0,blocked_state:"unblocked",
  });
});

test("empty-store release service preserves the exact legacy operation projection", () => {
  const core = makeCore(() => 9001);
  const context = core.productionPreparationContext();
  assert.equal(JSON.stringify(context.projection),
    '{"review_receipts":[],"sources":[],"eligible_operation_count":0}');
  assert.deepEqual(context.projection,{
    review_receipts:[],sources:[],eligible_operation_count:0,
  });
});

test("operation frontier schema rejects null and empty normalized identities", () => {
  const core = makeCore(() => 9001);
  const schema = core._one(`SELECT sql FROM sqlite_master
    WHERE type='table' AND name='production_review_operations'`).sql;
  assert.match(schema,/operation_id TEXT PRIMARY KEY NOT NULL/i);
  assert.match(schema,/CHECK\s*\(\s*typeof\(operation_id\)='text'\s+AND\s+length\(operation_id\)>0\s*\)/i);
  assert.match(schema,/decision_id TEXT NOT NULL[^,]*CHECK\s*\(\s*typeof\(decision_id\)='text'\s+AND\s+length\(decision_id\)>0\s*\)/i);
});

test("operation frontier rejects malformed evidence identities before set comparison", async (t) => {
  const cases = [
    ["null-operation-id",{ id:null }],
    ["empty-operation-id",{ id:"" }],
    ["numeric-operation-id",{ id:7 }],
    ["object-operation-id",{ id:{ nested:"id" } }],
    ["missing-operation-id",{}],
    ["empty-effective-decision-id",{ id:"operation-empty-decision",decision_id:"" }],
    ["numeric-effective-decision-id",{ id:"operation-numeric-decision",decision_id:7 }],
    ["object-effective-decision-id",{ id:"operation-object-decision",decision_id:{ nested:"id" } }],
  ];
  for (const [suffix,operation] of cases) await t.test(suffix,async () => {
    const core = makeCore(() => 9010);
    insertCanonicalMalformedEvidence(core,{ suffix,operation });
    await assertOperationProjectionCorruptionFailsClosed(core,"invalid_evidence_identity");
  });
});

test("operation frontier rejects identities accepted by a compatible legacy SQLite schema", async (t) => {
  for (const [suffix,operationId,decisionId] of [
    ["null-operation",null,"valid-decision"],
    ["empty-operation","","valid-decision"],
    ["empty-decision","valid-operation",""],
  ]) await t.test(suffix,async () => {
    const core = makeCore(() => 9011);
    const seeded = insertCanonicalMalformedEvidence(core,{ suffix,
      operation:{ id:"valid-operation",decision_id:"valid-decision" } });
    core.sql.exec("DROP TABLE production_review_operations");
    core.sql.exec(`CREATE TABLE production_review_operations (
      operation_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
      review_id TEXT NOT NULL, review_revision_id TEXT NOT NULL,
      source_ref TEXT NOT NULL, group_id TEXT, decision TEXT NOT NULL,
      note TEXT NOT NULL, lifecycle_state TEXT NOT NULL)`);
    core.sql.exec(`INSERT INTO production_review_operations
      (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,
       lifecycle_state) VALUES (?,?,?,?,?,?,?,?,?)`,operationId,decisionId,seeded.reviewId,
    seeded.revisionId,seeded.sourceRef,null,"unanswered","","unpublished");
    await assertOperationProjectionCorruptionFailsClosed(core,"invalid_normalized_identity");
  });
});

test("operation frontier rejects partial source deletion against the immutable receipt", async () => {
  const core = makeCore(() => 9012);
  const specs = [
    { suffix:"accepted",decision:"accepted" },
    { suffix:"rejected",decision:"rejected" },
  ];
  const sources = [];
  for (const spec of specs) {
    const sourceRef = `data/copy/receipt-${spec.suffix}.json#lead`;
    const revisionId = `revision-receipt-${spec.suffix}`;
    const sourceRevision = `dev-receipt-${spec.suffix}`;
    const operationId = `operation-receipt-${spec.suffix}`;
    assert.equal(core.recordReviewRevision({ id:revisionId,source_ref:sourceRef,
      source_revision:sourceRevision,prod_base:"prod-base",commit_sha:sourceRevision,
      original_hash:"old",proposed_hash:"new",original_text:"old",proposed_text:"new",
      suggestion_ids:[`suggestion-${spec.suffix}`],operations:[{ id:operationId,
        decision_id:operationId,kind:"replace",source_ref:sourceRef,
        source_revision:sourceRevision,prod_base:"prod-base" }] }).ok,true);
    const decisions = [{ operation_id:operationId,decision:spec.decision }];
    assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
      review_revision_id:revisionId,source_revision:sourceRevision,prod_base:"prod-base",
      decisions }).ok,true);
    sources.push({ review_revision_id:revisionId,source_revision:sourceRevision,
      prod_base:"prod-base",decisions });
  }
  assert.equal(core.submitPublisherReview({ id:"review-receipt-multi",
    idempotency_key:"review-receipt-multi",request_digest:"review-receipt-multi",
    actor:"slot:damien",sources }).ok,true);
  core.sql.exec(`DELETE FROM production_review_submission_decisions
    WHERE review_id=? AND review_revision_id=?`,"review-receipt-multi",
  "revision-receipt-accepted");
  core.sql.exec("DELETE FROM production_review_operations WHERE operation_id=?",
    "operation-receipt-accepted");
  core.sql.exec(`DELETE FROM production_review_submission_sources
    WHERE review_id=? AND review_revision_id=?`,"review-receipt-multi",
  "revision-receipt-accepted");

  await assertOperationProjectionCorruptionFailsClosed(core,"receipt_source_set_mismatch");
});

test("operation frontier rejects coordinated decision mutation against the immutable receipt", async () => {
  const core = makeCore(() => 9013);
  reviewedProjection(core);
  core.sql.exec(`UPDATE production_review_submission_decisions SET decision='rejected'
    WHERE review_id='review-v2' AND operation_id='op-accepted'`);
  core.sql.exec(`UPDATE production_review_operations SET decision='rejected'
    WHERE review_id='review-v2' AND operation_id='op-accepted'`);

  await assertOperationProjectionCorruptionFailsClosed(core,"receipt_decision_set_mismatch");
});

test("operation frontier validates canonical receipt JSON and receipt hash", async (t) => {
  await t.test("hash divergence",async () => {
    const core = makeCore(() => 9014);
    reviewedProjection(core);
    core.sql.exec("UPDATE production_review_submissions SET receipt_hash='changed'");
    await assertOperationProjectionCorruptionFailsClosed(core,"receipt_hash_mismatch");
  });
  await t.test("non-canonical JSON",async () => {
    const core = makeCore(() => 9014);
    reviewedProjection(core);
    const row = core._one("SELECT receipt_json FROM production_review_submissions");
    core.sql.exec("UPDATE production_review_submissions SET receipt_json=?",
      JSON.stringify(JSON.parse(row.receipt_json),null,2));
    await assertOperationProjectionCorruptionFailsClosed(core,"receipt_hash_mismatch");
  });
});

test("compatible legacy receipts reject immutable and child divergence", async (t) => {
  await t.test("hash divergence",async () => {
    const core = makeCore(() => 9014);
    seedSingleOperationProjectionCase(core,{ suffix:"legacy-hash",legacy:true });
    core.sql.exec("UPDATE production_reviews SET receipt_hash='changed'");
    await assertOperationProjectionCorruptionFailsClosed(core,"receipt_hash_mismatch");
  });
  await t.test("decision child deletion",async () => {
    const core = makeCore(() => 9014);
    seedSingleOperationProjectionCase(core,{ suffix:"legacy-child",legacy:true });
    core.sql.exec("DELETE FROM production_review_decisions");
    await assertOperationProjectionCorruptionFailsClosed(core,"receipt_decision_set_mismatch");
  });
});

test("non-empty release-service stores preserve the exact origin/main operation projection", () => {
  const cases = [
    { suffix:"accepted",expected:"single" },
    { suffix:"unanswered",decision:"unanswered",expected:"empty" },
    { suffix:"questioned",decision:"questioned",expected:"empty" },
    { suffix:"superseded",lifecycleState:"superseded",expected:"empty" },
    { suffix:"grouped",groupId:"compat-group",expected:"single" },
    { suffix:"legacy",legacy:true,expected:"empty" },
  ];
  for (const compatibilityCase of cases) {
    const core = makeCore(() => 9002);
    const seed = seedSingleOperationProjectionCase(core,compatibilityCase);
    assert.equal(Number(core._one(
      "SELECT COUNT(*) AS count FROM production_review_operations").count) > 0,true,
    `${compatibilityCase.suffix} must compare a non-empty store`);
    const actual = core.productionPreparationContext().projection;
    const expected = compatibilityCase.expected === "single" ?
      expectedSingleOperationProjection(seed) :
      { review_receipts:[],sources:[],eligible_operation_count:0 };
    const actualBytes = JSON.stringify(actual);
    const expectedBytes = JSON.stringify(expected);
    assert.equal(actualBytes.length > 0,true,
      `${compatibilityCase.suffix} actual comparison value must be non-empty`);
    assert.equal(expectedBytes.length > 0,true,
      `${compatibilityCase.suffix} expected comparison value must be non-empty`);
    assert.equal(actualBytes,expectedBytes,compatibilityCase.suffix);
  }
});

test("observer operation frontier reports eligible-only operation work", async () => {
  const core = makeCore(() => 9010);
  reviewedProjection(core);
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
  assert.equal("projection" in context,false);
});

test("observer operation frontier reports the sum of eligible and held operation work", async () => {
  const context = await observerFrontier({ projection:{
    eligible_operation_count:3,held_operation_count:4,
  } });
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:7,blocked_state:"unblocked",
  });
});

test("observer operation frontier blocks invalid or missing held operation counts", async () => {
  for (const projection of [
    { eligible_operation_count:0 },
    { eligible_operation_count:0,held_operation_count:-1 },
    { eligible_operation_count:0,held_operation_count:0.5 },
    { eligible_operation_count:0,held_operation_count:Number.MAX_SAFE_INTEGER + 1 },
  ]) {
    const context = await observerFrontier({ projection });
    assertObserverOperationFrontier(context.operation_frontier,{
      pending_operation_count:0,blocked_state:"blocked",
    });
  }
});

test("observer operation frontier blocks rather than truncates sums over the contract count bound", async () => {
  const bounded = await observerFrontier({ projection:{
    eligible_operation_count:60_000,held_operation_count:40_000,
  } });
  assertObserverOperationFrontier(bounded.operation_frontier,{
    pending_operation_count:100_000,blocked_state:"unblocked",
  });
  const overflow = await observerFrontier({ projection:{
    eligible_operation_count:60_000,held_operation_count:40_001,
  } });
  assertObserverOperationFrontier(overflow.operation_frontier,{
    pending_operation_count:0,blocked_state:"blocked",
  });
});

test("observer operation frontier reports bounded blockage when projection is unavailable", async () => {
  const core = makeCore(() => 9015);
  reviewedProjection(core);
  core.sql.exec("UPDATE production_review_revisions SET operations_json=? WHERE id=?",
    "{malformed","revision-v2");
  const tolerantContext = core.productionPreparationContext({
    tolerateOperationProjectionFailure:true,
  });
  assert.equal("projection" in tolerantContext,false,
    "a caught failure must leave the projection absent, not known-empty or undefined");
  const calls = [];
  const env = { PROD_RELEASE_LEDGER:"true",EDITOR:{ getByName:() => ({
    productionPreparationContext:async (...args) => {
      calls.push(args);
      return core.productionPreparationContext(...args);
    },
  }) } };
  const request = () => new Request("https://edit.example/edit/v1/prod/releases/frontier");
  const releaseService = { editor:"service:release",credential_channel:"bearer",
    scopes:scopes({ releaseService:true }) };
  await assert.rejects(productionPreparationContextEndpoint(request(),env,releaseService),
    SyntaxError,"the trusted release-service projection must still fail loudly");

  const response = await productionPreparationContextEndpoint(request(),env,{
    editor:"service:observer",credential_channel:"bearer",
    scopes:scopes({ releaseObserver:true }),
  });
  assert.equal(response.status,200);
  const context = (await response.json()).context;
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:0,blocked_state:"blocked",
  });
  assert.equal(localFrontierLooksEmpty(context.operation_frontier),false,
    "a blocked zero count must fail closed rather than prove the frontier empty");
  assert.deepEqual(calls,[[],[{ tolerateOperationProjectionFailure:true }]],
    "release service must use the default path while observers request tolerant projection");
});

test("operation frontier rejects missing normalized operation evidence", async () => {
  const core = makeCore(() => 9016);
  reviewedProjection(core);
  core.sql.exec("DELETE FROM production_review_operations WHERE operation_id=?","op-accepted");
  await assertOperationProjectionCorruptionFailsClosed(core,"missing_normalized_row");
});

test("operation frontier rejects extra normalized operation rows", async () => {
  const core = makeCore(() => 9017);
  reviewedProjection(core);
  core.sql.exec(`INSERT INTO production_review_operations
    (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state)
    VALUES (?,?,?,?,?,?,?,?,?)`,"op-extra","op-extra","review-v2","revision-v2",
    "data/copy/home.json#lead",null,"accepted","","unpublished");
  await assertOperationProjectionCorruptionFailsClosed(core,"extra_normalized_row");
});

test("operation frontier rejects orphaned active operation rows", async () => {
  const core = makeCore(() => 9018);
  core.sql.exec(`INSERT INTO production_review_operations
    (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state)
    VALUES (?,?,?,?,?,?,?,?,?)`,"op-orphan","op-orphan","review-missing","revision-missing",
    "data/copy/missing.json#lead",null,"accepted","","unpublished");
  await assertOperationProjectionCorruptionFailsClosed(core,"orphaned_normalized_row");
});

test("operation frontier rejects submission sources without submission parents", async () => {
  const core = makeCore(() => 9018);
  reviewedProjection(core);
  core.sql.exec("DELETE FROM production_review_submissions WHERE id=?","review-v2");
  await assertOperationProjectionCorruptionFailsClosed(core,
    "submission_source_missing_submission");
});

test("operation frontier rejects submission sources without revision parents", async () => {
  const core = makeCore(() => 9018);
  reviewedProjection(core);
  core.sql.exec("DELETE FROM production_review_revisions WHERE id=?","revision-v2");
  await assertOperationProjectionCorruptionFailsClosed(core,"submission_source_missing_revision");
});

test("operation frontier rejects submissions without source children", async () => {
  const core = makeCore(() => 9018);
  reviewedProjection(core);
  core.sql.exec("DELETE FROM production_review_submission_sources WHERE review_id=?","review-v2");
  await assertOperationProjectionCorruptionFailsClosed(core,"submission_missing_source");
});

test("operation frontier rejects legacy reviews without revision parents", async () => {
  const core = makeCore(() => 9018);
  core.sql.exec(`INSERT INTO production_reviews
    (id,idempotency_key,request_digest,actor,review_revision_id,source_revision,prod_base,
     receipt_hash,receipt_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)`,"legacy-orphan",
  "legacy-orphan","legacy-orphan","slot:damien","missing-revision","dev-missing",
  "prod-base","receipt-legacy-orphan","{}",9018);
  await assertOperationProjectionCorruptionFailsClosed(core,"legacy_review_missing_revision");
});

test("operation frontier rejects submission decisions without review parents", async () => {
  const core = makeCore(() => 9018);
  core.sql.exec(`INSERT INTO production_review_submission_decisions
    (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id)
    VALUES (?,?,?,?,?,?,?)`,"missing-review","missing-revision","missing-operation",
  "accepted","","digest",null);
  await assertOperationProjectionCorruptionFailsClosed(core,"orphaned_submission_decision");
});

test("operation frontier rejects submission decisions without operation evidence", async () => {
  const core = makeCore(() => 9018);
  reviewedProjection(core);
  core.sql.exec(`INSERT INTO production_review_submission_decisions
    (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id)
    VALUES (?,?,?,?,?,?,?)`,"review-v2","revision-v2","missing-operation","accepted","",
  "digest",null);
  await assertOperationProjectionCorruptionFailsClosed(core,"orphaned_submission_decision");
});

test("operation frontier rejects legacy decisions without review parents", async () => {
  const core = makeCore(() => 9018);
  core.sql.exec(`INSERT INTO production_review_decisions
    (review_id,operation_id,decision,note,operation_digest,group_id)
    VALUES (?,?,?,?,?,?)`,"missing-review","missing-operation","accepted","","digest",null);
  await assertOperationProjectionCorruptionFailsClosed(core,"orphaned_legacy_decision");
});

test("operation frontier rejects legacy decisions without operation evidence", async () => {
  const core = makeCore(() => 9018);
  seedSingleOperationProjectionCase(core,{ suffix:"legacy-decision",legacy:true });
  core.sql.exec(`INSERT INTO production_review_decisions
    (review_id,operation_id,decision,note,operation_digest,group_id)
    VALUES (?,?,?,?,?,?)`,"review-compat-legacy-decision","missing-operation","accepted","",
  "digest",null);
  await assertOperationProjectionCorruptionFailsClosed(core,"orphaned_legacy_decision");
});

test("operation frontier rejects published lifecycle without a publication row", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"lifecycle-only",publication:false });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_marker_mismatch");
});

test("operation frontier rejects publication rows without published lifecycle", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"row-only",lifecycleState:"unpublished" });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_marker_mismatch");
});

test("operation frontier rejects publication rows without normalized operations", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"missing-operation",
    publicationOverrides:{ operation_id:"missing-operation" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_marker_mismatch");
});

test("operation frontier rejects publication revision mismatches", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"revision-mismatch",
    publicationOverrides:{ review_revision_id:"different-revision" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_operation_mismatch");
});

test("operation frontier rejects publication source mismatches", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"source-mismatch",
    publicationOverrides:{ source_ref:"data/copy/different.json#lead" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_operation_mismatch");
});

test("operation frontier rejects publication source-revision mismatches", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"source-revision-mismatch",
    publicationOverrides:{ source_revision:"different-source-revision" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_operation_mismatch");
});

test("operation frontier rejects publication rows without release parents", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"missing-release",
    publicationOverrides:{ release_id:"missing-release" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects publication rows for incomplete releases", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"incomplete-release",releaseState:"restored" });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects publication rows for legacy releases", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"legacy-release",schemaVersion:1 });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects publication rows without release membership", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"missing-member",member:false });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects publication member revision mismatches", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"member-revision-mismatch",
    memberOverrides:{ review_revision_id:"different-revision" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects publication member source mismatches", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"member-source-mismatch",
    memberOverrides:{ source_ref:"data/copy/different.json#lead" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects publication candidate mismatches", async () => {
  const core = makeCore(() => 9018);
  seedPublicationIntegrityCase(core,{ suffix:"candidate-mismatch",
    publicationOverrides:{ candidate_sha:"different-candidate" } });
  await assertOperationProjectionCorruptionFailsClosed(core,"publication_release_mismatch");
});

test("operation frontier rejects a late member appended after real release completion", async () => {
  let now = 9020;
  const core = makeCore(() => now);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ id:"release-frozen-proof",
    idempotency_key:"release-frozen-proof",request_digest:"release-frozen-proof",
    schema_version:2,target_batch_id:"operation-frontier",candidate_sha:"candidate-frozen-proof",
    review_receipt_hash:receipt,review_receipts:[receipt],projection_identity:"frozen-proof",
    accepted_operation_ids:["op-accepted"],held_exclusions:[{
      operation_id:"op-held",decision:"rejected",reason:"rejected",
    }],
  })).release;
  const authorized = core.authorizeProductionRelease(authorize(prepared,{
    id:prepared.id,idempotency_key:"authorize-frozen-proof",
    request_digest:"authorize-frozen-proof",review_receipt_hash:receipt,
    projection_identity:"frozen-proof",
  })).release;
  const claimed = core.claimAuthorizedProductionRelease({ id:authorized.id,
    actor:"service:release",credential_channel:"bearer" }).release;
  for (const state of ["pages_deployed","worker_deployed","verified","complete"])
    assert.equal(core.transitionProductionRelease({ id:claimed.id,state,
      actor:"service:release",credential_channel:"bearer",fencing_token:claimed.fencing_token,
      detail:{ candidate_sha:prepared.candidate_sha } }).ok,true);
  const completionTime = core._one(`SELECT created_at FROM production_release_events
    WHERE release_id=? AND type='complete'`,prepared.id).created_at;

  now = 9030;
  const sourceRef = "data/copy/late-after-completion.json#lead";
  const operationId = "op-late-after-completion";
  assert.equal(core.recordReviewRevision({ id:"revision-late-after-completion",
    source_ref:sourceRef,source_revision:"dev-late",prod_base:prepared.candidate_sha,
    commit_sha:"dev-late",original_hash:"old",proposed_hash:"new",original_text:"old",
    proposed_text:"new",suggestion_ids:["suggestion-late"],operations:[{ id:operationId,
      decision_id:operationId,kind:"replace",source_ref:sourceRef,source_revision:"dev-late",
      prod_base:prepared.candidate_sha }] }).ok,true);
  const decisions = [{ operation_id:operationId,decision:"accepted" }];
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
    review_revision_id:"revision-late-after-completion",source_revision:"dev-late",
    prod_base:prepared.candidate_sha,decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-late-after-completion",
    idempotency_key:"review-late-after-completion",request_digest:"review-late-after-completion",
    actor:"slot:damien",review_revision_id:"revision-late-after-completion",
    source_revision:"dev-late",prod_base:prepared.candidate_sha,decisions }).ok,true);

  core.sql.exec(`INSERT INTO production_release_operation_members
    (release_id,operation_id,review_revision_id,source_ref,affected_source_refs_json,group_id,
     ordinal) VALUES (?,?,?,?,?,?,?)`,prepared.id,operationId,"revision-late-after-completion",
  sourceRef,JSON.stringify([sourceRef]),null,1);
  core.sql.exec(`INSERT INTO production_published_operations
    (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,
     published_at) VALUES (?,?,?,?,?,?,?)`,operationId,prepared.id,
  "revision-late-after-completion",sourceRef,"dev-late",prepared.candidate_sha,completionTime);
  core.sql.exec(`INSERT INTO production_published_operation_sources
    (operation_id,source_ref,release_id,candidate_sha,published_at) VALUES (?,?,?,?,?)`,
  operationId,sourceRef,prepared.id,prepared.candidate_sha,completionTime);
  core.sql.exec(`UPDATE production_review_operations SET lifecycle_state='published'
    WHERE operation_id=?`,operationId);

  await assertOperationProjectionCorruptionFailsClosed(core,
    "publication_release_evidence_mismatch");
});

test("operation frontier requires exact source-publication children from real completion", async () => {
  const core = makeCore(() => 9031);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ id:"release-source-proof",
    idempotency_key:"release-source-proof",request_digest:"release-source-proof",
    schema_version:2,target_batch_id:"operation-frontier",candidate_sha:"candidate-source-proof",
    review_receipt_hash:receipt,review_receipts:[receipt],projection_identity:"source-proof",
    accepted_operation_ids:["op-accepted"],held_exclusions:[{
      operation_id:"op-held",decision:"rejected",reason:"rejected",
    }],
  })).release;
  const authorized = core.authorizeProductionRelease(authorize(prepared,{
    id:prepared.id,idempotency_key:"authorize-source-proof",
    request_digest:"authorize-source-proof",review_receipt_hash:receipt,
    projection_identity:"source-proof",
  })).release;
  const claimed = core.claimAuthorizedProductionRelease({ id:authorized.id,
    actor:"service:release",credential_channel:"bearer" }).release;
  for (const state of ["pages_deployed","worker_deployed","verified","complete"])
    assert.equal(core.transitionProductionRelease({ id:claimed.id,state,
      actor:"service:release",credential_channel:"bearer",fencing_token:claimed.fencing_token,
      detail:{ candidate_sha:prepared.candidate_sha } }).ok,true);
  core.sql.exec("DELETE FROM production_published_operation_sources WHERE release_id=?",
    prepared.id);

  await assertOperationProjectionCorruptionFailsClosed(core,
    "publication_release_evidence_mismatch");
});

function completedOperationFrontierRelease(suffix,{ reclaimBeforeDeployment = false } = {}) {
  let now = 9032;
  const core = makeCore(() => now);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const candidateSha = `candidate-claim-${suffix}`;
  const prepared = core.prepareProductionRelease(release({ id:`release-claim-${suffix}`,
    idempotency_key:`release-claim-${suffix}`,request_digest:`release-claim-${suffix}`,
    schema_version:2,target_batch_id:"operation-frontier",
    candidate_sha:candidateSha,review_receipt_hash:receipt,
    review_receipts:[receipt],projection_identity:`claim-${suffix}`,
    accepted_operation_ids:["op-accepted"],held_exclusions:[{
      operation_id:"op-held",decision:"rejected",reason:"rejected",
    }],
  })).release;
  const authorized = core.authorizeProductionRelease(authorize(prepared,{
    id:prepared.id,idempotency_key:`authorize-claim-${suffix}`,
    request_digest:`authorize-claim-${suffix}`,review_receipt_hash:receipt,
    projection_identity:`claim-${suffix}`,
  })).release;
  const claimInput = { id:authorized.id,actor:"service:release",
    credential_channel:"bearer",lease_ms:1000 };
  const firstClaim = core.claimAuthorizedProductionRelease(claimInput).release;
  let claimed = firstClaim;
  if (reclaimBeforeDeployment) {
    now += 1000;
    claimed = core.claimAuthorizedProductionRelease(claimInput).release;
    assert.ok(firstClaim.fencing_token);
    assert.ok(claimed.fencing_token);
    assert.notEqual(claimed.fencing_token,firstClaim.fencing_token);
  }
  for (const state of ["pages_deployed","worker_deployed","verified","complete"])
    assert.equal(core.transitionProductionRelease({ id:claimed.id,state,
      actor:"service:release",credential_channel:"bearer",fencing_token:claimed.fencing_token,
      detail:{ candidate_sha:prepared.candidate_sha } }).ok,true);
  const completionTime = core._one(`SELECT created_at FROM production_release_events
    WHERE release_id=? AND type='complete'`,prepared.id).created_at;
  return { core,releaseId:prepared.id,candidateSha,completionTime,
    firstFence:firstClaim.fencing_token,currentFence:claimed.fencing_token };
}

test("operation frontier binds deployment evidence to a preceding real execution claim", async (t) => {
  await t.test("deployment before claim",async () => {
    const { core,releaseId } = completedOperationFrontierRelease("ordering");
    core.sql.exec(`UPDATE production_release_events SET type='event-swap'
      WHERE release_id=? AND type='executing'`,releaseId);
    core.sql.exec(`UPDATE production_release_events SET type='executing'
      WHERE release_id=? AND type='pages_deployed'`,releaseId);
    core.sql.exec(`UPDATE production_release_events SET type='pages_deployed'
      WHERE release_id=? AND type='event-swap'`,releaseId);
    await assertOperationProjectionCorruptionFailsClosed(core,
      "publication_release_evidence_mismatch");
  });

  await t.test("post-reclaim deployment rejects the first issued fence",async () => {
    const { core,releaseId,firstFence,currentFence } =
      completedOperationFrontierRelease("nearest-claim",{ reclaimBeforeDeployment:true });
    assert.notEqual(firstFence,currentFence,
      "the fixture must contain two genuinely issued claim fences");
    const event = core._one(`SELECT id,detail_json FROM production_release_events
      WHERE release_id=? AND type='pages_deployed'`,releaseId);
    const detail = JSON.parse(event.detail_json);
    assert.equal(detail.fencing_token,currentFence,
      "the valid fixture must initially bind deployment to the second claim");
    detail.fencing_token = firstFence;
    core.sql.exec("UPDATE production_release_events SET detail_json=? WHERE id=?",
      JSON.stringify(detail),event.id);
    await assertOperationProjectionCorruptionFailsClosed(core,
      "publication_release_evidence_mismatch");
  });

  for (const type of ["pages_deployed","worker_deployed","verified","complete"])
    await t.test(`${type} with unissued fence`,async () => {
      const { core,releaseId } = completedOperationFrontierRelease(`fence-${type}`);
      const event = core._one(`SELECT id,detail_json FROM production_release_events
        WHERE release_id=? AND type=?`,releaseId,type);
      const detail = JSON.parse(event.detail_json);
      detail.fencing_token = "unissued-fence";
      core.sql.exec("UPDATE production_release_events SET detail_json=? WHERE id=?",
        JSON.stringify(detail),event.id);
      await assertOperationProjectionCorruptionFailsClosed(core,
        "publication_release_evidence_mismatch");
    });
});

test("operation frontier rejects source publications without completed v2 operation parents", async (t) => {
  await t.test("missing operation publication with valid completed release",async () => {
    const { core,releaseId,candidateSha,completionTime } =
      completedOperationFrontierRelease("source-orphan-missing-operation");
    core.sql.exec(`INSERT INTO production_published_operation_sources
      (operation_id,source_ref,release_id,candidate_sha,published_at) VALUES (?,?,?,?,?)`,
    "operation-source-orphan-missing-operation",
    "data/copy/source-orphan-missing-operation.json#lead",
    releaseId,candidateSha,completionTime);
    assert.throws(() => core.publisherSummary(),{
      message:"operation_frontier_integrity:publication_source_missing_operation",
    });
    await assertOperationProjectionCorruptionFailsClosed(core,
      "publication_source_missing_operation");
  });

  for (const [suffix,state,schemaVersion] of [
    ["missing-release",null,null],
    ["incomplete-release","prepared",2],
    ["legacy-release","complete",1],
  ]) await t.test(suffix,() => {
    const core = makeCore(() => 9033);
    const releaseId = `release-source-orphan-${suffix}`;
    if (state) core.sql.exec(`INSERT INTO production_releases
      (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
       target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
       membership_hash,created_at,updated_at,schema_version)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,releaseId,releaseId,releaseId,state,
    "service:release","bearer","production","operation-frontier","base","candidate",
    "generator","evidence","manifest","membership",9033,9033,schemaVersion);
    core.sql.exec(`INSERT INTO production_published_operations
      (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,
       published_at) VALUES (?,?,?,?,?,?,?)`,`operation-source-orphan-${suffix}`,releaseId,
    `revision-source-orphan-${suffix}`,`data/copy/source-orphan-${suffix}.json#lead`,
    `source-revision-orphan-${suffix}`,"candidate",9033);
    core.sql.exec(`INSERT INTO production_published_operation_sources
      (operation_id,source_ref,release_id,candidate_sha,published_at) VALUES (?,?,?,?,?)`,
    `operation-source-orphan-${suffix}`,`data/copy/source-orphan-${suffix}.json#lead`,
    releaseId,"candidate",9033);
    assert.throws(() => core._operationFrontierSummaryProjection(),
      /operation_frontier_integrity:publication_source_missing_completed_release/);
  });
});

test("operation frontier rejects unknown operation lifecycle states", async () => {
  const core = makeCore(() => 9019);
  reviewedProjection(core);
  core.sql.exec("UPDATE production_review_operations SET lifecycle_state=? WHERE operation_id=?",
    "unexpected-state","op-accepted");
  await assertOperationProjectionCorruptionFailsClosed(core,"unknown_lifecycle_state");
});

test("operation frontier rejects normalized decision corruption", async () => {
  const core = makeCore(() => 9019);
  reviewedProjection(core);
  core.sql.exec("UPDATE production_review_operations SET decision=? WHERE operation_id=?",
    "rejected","op-accepted");
  await assertOperationProjectionCorruptionFailsClosed(core,"extra_normalized_row");
});

test("operation frontier rejects unknown normalized decision states", async () => {
  const core = makeCore(() => 9019);
  reviewedProjection(core);
  core.sql.exec("UPDATE production_review_operations SET decision=? WHERE operation_id=?",
    "unexpected-decision","op-accepted");
  await assertOperationProjectionCorruptionFailsClosed(core,"unknown_decision_state");
});

test("operation frontier rejects duplicate submitted operation evidence", async () => {
  const core = makeCore(() => 9019);
  reviewedProjection(core);
  const row = core._one("SELECT operations_json FROM production_review_revisions WHERE id=?",
    "revision-v2");
  const operations = JSON.parse(row.operations_json);
  operations.push(operations[0]);
  core.sql.exec("UPDATE production_review_revisions SET operations_json=? WHERE id=?",
    JSON.stringify(operations),"revision-v2");
  await assertOperationProjectionCorruptionFailsClosed(core,"receipt_revision_mismatch");
});

test("observer operation frontier reports held-only operation work", async () => {
  const core = makeCore(() => 9020);
  seedReviewedHeldOperation(core);
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
});

test("producer-local frontier approximation rejects unanswered-only work as empty", async () => {
  const core = makeCore(() => 9020);
  const sourceRef = "data/copy/unanswered.json#lead";
  assert.equal(core.recordReviewRevision({ id:"revision-unanswered",source_ref:sourceRef,
    source_revision:"dev-unanswered",prod_base:"prod-base",commit_sha:"dev-unanswered",
    original_hash:"old",proposed_hash:"new",original_text:"Original",proposed_text:"Proposal",
    suggestion_ids:["suggestion-unanswered"],operations:[{
      id:"operation-unanswered",kind:"replace",source_ref:sourceRef,
      source_revision:"dev-unanswered",prod_base:"prod-base",old_text:"Original",
      new_text:"Proposal",
    }] }).ok,true);
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
    review_revision_id:"revision-unanswered",source_revision:"dev-unanswered",
    prod_base:"prod-base",decisions:[] }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-unanswered",
    idempotency_key:"review-unanswered",request_digest:"review-unanswered",actor:"slot:damien",
    review_revision_id:"revision-unanswered",source_revision:"dev-unanswered",
    prod_base:"prod-base",decisions:[] }).ok,true);

  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
  assert.equal(localFrontierLooksEmpty(context.operation_frontier),false);
});

test("producer-local frontier approximation rejects legacy normalized work as empty", async () => {
  const core = makeCore(() => 9020);
  seedSingleOperationProjectionCase(core,{ suffix:"observer-legacy",legacy:true });

  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
  assert.equal(localFrontierLooksEmpty(context.operation_frontier),false);
});

test("producer-local frontier approximation rejects stale accepted work as empty", async () => {
  const core = makeCore(() => 9021);
  reviewedProjection(core);
  core.now = () => 9022;
  const sourceRef = "data/copy/home.json#lead";
  assert.equal(core.recordReviewRevision({ id:"revision-newer",source_ref:sourceRef,
    source_revision:"dev-2",prod_base:"prod-base",commit_sha:"dev-2",
    original_hash:"newer-old",proposed_hash:"newer-new",original_text:"Newer original",
    proposed_text:"Newer proposal",suggestion_ids:["suggestion-newer"],operations:[{
      id:"op-newer",decision_id:"op-newer",kind:"replace",source_ref:sourceRef,
      source_revision:"dev-2",prod_base:"prod-base",old_text:"original",new_text:"proposal",
    }] }).ok,true);

  const projection = core.productionPreparationContext().projection;
  const summary = core._operationFrontierSummaryProjection();
  const unpublishedNonRejected = projection.sources.flatMap((source) => source.operations)
    .filter((operation) => operation.id === "op-accepted").length;
  assert.equal(summary.eligible_operation_count + summary.held_operation_count,
    unpublishedNonRejected,
    "no projected, unpublished, non-rejected operation may be absent from both counts");

  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
  assert.equal(localFrontierLooksEmpty(context.operation_frontier),false);
});

test("producer-local frontier approximation rejects semantic group holds as empty", async () => {
  for (const linkageKey of ["group_id","move_pair_id"]) {
    const core = makeCore(() => 9022);
    const suffix = linkageKey === "group_id" ? "group" : "move";
    const sources = [
      { review_revision_id:`revision-${suffix}-a`,source_ref:`data/copy/${suffix}-a.json#lead`,
        source_revision:`dev-${suffix}-a`,operation_id:`operation-${suffix}-a`,decision:"accepted" },
      { review_revision_id:`revision-${suffix}-b`,source_ref:`data/copy/${suffix}-b.json#lead`,
        source_revision:`dev-${suffix}-b`,operation_id:`operation-${suffix}-b`,decision:"rejected" },
    ];
    for (const source of sources) {
      assert.equal(core.recordReviewRevision({ id:source.review_revision_id,
        source_ref:source.source_ref,source_revision:source.source_revision,prod_base:"prod-base",
        commit_sha:source.source_revision,original_hash:"old",proposed_hash:"new",
        original_text:"Original",proposed_text:"Proposal",
        suggestion_ids:[`suggestion-${source.operation_id}`],operations:[{
          id:source.operation_id,decision_id:source.operation_id,kind:"replace",
          [linkageKey]:`semantic-${suffix}`,source_ref:source.source_ref,
          source_revision:source.source_revision,prod_base:"prod-base",
          old_text:"Original",new_text:"Proposal",
        }] }).ok,true);
      const decisions = [{ operation_id:source.operation_id,decision:source.decision }];
      assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
        review_revision_id:source.review_revision_id,source_revision:source.source_revision,
        prod_base:"prod-base",decisions }).ok,true);
    }
    for (const source of sources) assert.equal(core.submitPublisherReview({
      id:`review-${source.operation_id}`,idempotency_key:`review-${source.operation_id}`,
      request_digest:`review-${source.operation_id}`,actor:"slot:damien",
      review_revision_id:source.review_revision_id,source_revision:source.source_revision,
      prod_base:"prod-base",decisions:[{
        operation_id:source.operation_id,decision:source.decision,
      }],
    }).ok,true);

    const context = await observerFrontierFromCore(core);
    assertObserverOperationFrontier(context.operation_frontier,{
      pending_operation_count:1,blocked_state:"unblocked",
    });
    assert.equal(localFrontierLooksEmpty(context.operation_frontier),false);
  }
});

test("producer-local frontier approximation rejects frozen accepted work as empty", async () => {
  const core = makeCore(() => 9023);
  const projection = reviewedProjection(core);
  const receipt = projection.review_receipts[0].receipt_hash;
  const prepared = core.prepareProductionRelease(release({ id:"release-frozen-v2",
    idempotency_key:"release-frozen-v2",request_digest:"release-frozen-v2",schema_version:2,
    target_batch_id:"operation-frontier",candidate_sha:"candidate-frozen-v2",
    review_receipt_hash:receipt,review_receipts:[receipt],projection_identity:"frozen-v2",
    accepted_operation_ids:["op-accepted"],held_exclusions:[{
      operation_id:"op-held",decision:"rejected",reason:"rejected",
    }],
  }));
  assert.equal(prepared.ok,true);

  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
  assert.equal(localFrontierLooksEmpty(context.operation_frontier),false);
});

test("observer operation frontier survives an active release with truthful counts", async () => {
  const core = makeCore(() => 9030);
  reviewedProjection(core);
  seedApplied(core,"batch-observer-active",["suggestion-observer-active"],9031);
  const prepared = core.prepareProductionRelease(release({
    target_batch_id:"batch-observer-active",candidate_sha:"commit-batch-observer-active",
  }));
  assert.equal(prepared.ok,true);
  const releaseServiceContext = core.productionPreparationContext();
  assert.equal("projection" in releaseServiceContext,false,
    "the release-service early-return shape must remain unchanged");

  const context = await observerFrontier(core.productionPreparationContext({
    tolerateOperationProjectionFailure:true,
  }));
  assert.equal(context.active_release.id,prepared.release.id);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
});

test("observer operation frontier survives missing batch evidence with truthful held counts", async () => {
  const core = makeCore(() => 9040);
  seedReviewedHeldOperation(core);
  seedApplied(core,"batch-blocked",["suggestion-blocked"],9041);
  core.sql.exec("UPDATE apply_batches SET phase='evidence_missing' WHERE batch_id=?",
    "batch-blocked");
  const releaseServiceContext = core.productionPreparationContext();
  assert.equal(releaseServiceContext.blocked_reason,"missing_batch_evidence");
  assert.equal("projection" in releaseServiceContext,false,
    "the release-service early-return shape must remain unchanged");

  const context = await observerFrontier(core.productionPreparationContext({
    tolerateOperationProjectionFailure:true,
  }));
  assert.equal(context.blocked_reason,"missing_batch_evidence");
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
});

function seedHighCardinalityReview(core,{ operationCount,prefix,normalized=true }) {
  const sourceRef = `data/copy/${prefix}.json#lead`;
  const operations = Array.from({ length:operationCount },(_value,index) => ({
    id:`${prefix}-operation-${String(index).padStart(6,"0")}`,source_ref:sourceRef,
    source_revision:`dev-${prefix}`,prod_base:"prod-base",
  }));
  assert.equal(core.recordReviewRevision({ id:`revision-${prefix}`,source_ref:sourceRef,
    source_revision:`dev-${prefix}`,prod_base:"prod-base",commit_sha:`dev-${prefix}`,
    original_hash:"old",proposed_hash:"new",original_text:"source text must not be projected",
    proposed_text:"proposal",suggestion_ids:[],operations }).ok,true);
  const revision = core._reviewRevision(`revision-${prefix}`);
  const receipt = { review_id:`review-${prefix}`,actor:"slot:damien",created_at:9042,
    sources:[{ review_revision_id:revision.id,source_revision:revision.source_revision,
      prod_base:revision.prod_base,evidence_digest:revision.evidence_digest,decisions:[] }] };
  core.sql.exec(`INSERT INTO production_review_submissions
    (id,idempotency_key,request_digest,actor,receipt_hash,receipt_json,created_at)
    VALUES (?,?,?,?,?,?,?)`,`review-${prefix}`,`review-${prefix}`,
    `review-${prefix}`,"slot:damien",core._digest(receipt),core._canonical(receipt),9042);
  core.sql.exec(`INSERT INTO production_review_submission_sources
    (review_id,review_revision_id,source_revision,prod_base,evidence_digest)
    VALUES (?,?,?,?,?)`,`review-${prefix}`,`revision-${prefix}`,`dev-${prefix}`,
    "prod-base",revision.evidence_digest);
  if (normalized) core.sql.exec(`INSERT INTO production_review_operations
      (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,
       lifecycle_state)
      SELECT json_extract(value,'$.id'),json_extract(value,'$.id'),?, ?, ?,NULL,'unanswered','',
        'unpublished' FROM json_each(?)`,`review-${prefix}`,`revision-${prefix}`,sourceRef,
  revision.operations_json);
  core.sql.exec(`INSERT INTO production_releases
    (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
     target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
     membership_hash,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,`release-${prefix}`,`release-${prefix}`,
    `release-${prefix}`,"prepared","service:release","bearer","production",
    "operation-frontier","prod-base",`candidate-${prefix}`,"generator","evidence","manifest",
  "membership",9042,9042);
}

function seedRejectedAndPublishedHistory(core,{ rejectedCount,publishedCount,prefix }) {
  const operationCount = rejectedCount + publishedCount;
  const sourceRef = `data/copy/${prefix}.json#lead`;
  const revisionId = `revision-${prefix}`;
  const reviewId = `review-${prefix}`;
  const releaseId = `release-${prefix}`;
  const operations = Array.from({ length:operationCount },(_value,index) => ({
    id:`${prefix}-operation-${String(index).padStart(6,"0")}`,source_ref:sourceRef,
    source_revision:`dev-${prefix}`,prod_base:"prod-base",
  }));
  assert.equal(core.recordReviewRevision({ id:revisionId,source_ref:sourceRef,
    source_revision:`dev-${prefix}`,prod_base:"prod-base",commit_sha:`dev-${prefix}`,
    original_hash:"old",proposed_hash:"new",original_text:"historical source text",
    proposed_text:"proposal",suggestion_ids:[],operations }).ok,true);
  const revision = core._reviewRevision(revisionId);
  const decisions = operations.map((operation,index) => ({ operation_id:operation.id,
    operation_digest:core._digest(operation),group_id:null,
    decision:index < rejectedCount ? "rejected" : "accepted",note:"" }));
  const receipt = { review_id:reviewId,actor:"slot:damien",created_at:9043,sources:[{
    review_revision_id:revisionId,source_revision:`dev-${prefix}`,prod_base:"prod-base",
    evidence_digest:revision.evidence_digest,decisions }] };
  const receiptHash = core._digest(receipt);
  core.sql.exec(`INSERT INTO production_review_submissions
    (id,idempotency_key,request_digest,actor,receipt_hash,receipt_json,created_at)
    VALUES (?,?,?,?,?,?,?)`,reviewId,reviewId,reviewId,"slot:damien",receiptHash,
  core._canonical(receipt),9043);
  core.sql.exec(`INSERT INTO production_review_submission_sources
    (review_id,review_revision_id,source_revision,prod_base,evidence_digest)
    VALUES (?,?,?,?,?)`,reviewId,revisionId,`dev-${prefix}`,"prod-base",revision.evidence_digest);
  core.sql.exec(`INSERT INTO production_review_submission_decisions
    (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id)
    SELECT ?,?,json_extract(value,'$.operation_id'),json_extract(value,'$.decision'),
      json_extract(value,'$.note'),
      json_extract(value,'$.operation_digest'),NULL FROM json_each(?)`,reviewId,revisionId,
  JSON.stringify(decisions));
  core.sql.exec(`INSERT INTO production_review_operations
    (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,
     lifecycle_state)
    SELECT json_extract(value,'$.id'),json_extract(value,'$.id'),?,?,?,NULL,
      CASE WHEN CAST(key AS INTEGER)<? THEN 'rejected' ELSE 'accepted' END,'',
      CASE WHEN CAST(key AS INTEGER)<? THEN 'unpublished' ELSE 'published' END
    FROM json_each(?)`,reviewId,revisionId,sourceRef,rejectedCount,rejectedCount,
  revision.operations_json);
  const members = operations.slice(rejectedCount).map((operation,ordinal) => ({
    operation_id:operation.id,review_revision_id:revisionId,source_ref:sourceRef,
    source_revision:`dev-${prefix}`,affected_source_refs:[sourceRef],group_id:null,ordinal,
  }));
  const held = operations.slice(0,rejectedCount).map((operation) => ({
    operation_id:operation.id,decision:"rejected",reason:"rejected",
  }));
  const membershipMembers = members.map(({ ordinal,...member }) => member);
  const membershipHash = core._fingerprint(JSON.stringify(membershipMembers),JSON.stringify(held),
    ["prod-base",`candidate-${prefix}`,"generator","evidence","manifest",receiptHash,
      `projection-${prefix}`].join("\0"));
  core.sql.exec(`INSERT INTO production_releases
    (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
     target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
     membership_hash,created_at,updated_at,schema_version,review_receipt_hash,
     projection_identity,authorization_key,authorization_digest,fencing_token)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,releaseId,releaseId,releaseId,
  "complete","slot:damien","access","production","operation-frontier","prod-base",
  `candidate-${prefix}`,"generator","evidence","manifest",membershipHash,9043,9043,2,
  receiptHash,`projection-${prefix}`,`authorization-${prefix}`,`authorization-digest-${prefix}`,
  `fence-${prefix}`);
  core.sql.exec(`INSERT INTO production_release_operation_members
    (release_id,operation_id,review_revision_id,source_ref,affected_source_refs_json,group_id,
     ordinal)
    SELECT ?,operation_id,review_revision_id,source_ref,?,NULL,
      ROW_NUMBER() OVER (ORDER BY operation_id)-1
    FROM production_review_operations WHERE lifecycle_state='published'`,releaseId,
  JSON.stringify([sourceRef]));
  core.sql.exec(`INSERT INTO production_release_held_exclusions
    (release_id,operation_id,decision,reason)
    SELECT ?,operation_id,'rejected','rejected' FROM production_review_operations
    WHERE decision='rejected'`,releaseId);
  core.sql.exec(`INSERT INTO production_published_operations
    (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,
     published_at)
    SELECT operation_id,?,review_revision_id,source_ref,?, ?,?
    FROM production_review_operations WHERE lifecycle_state='published'`,releaseId,
  `dev-${prefix}`,`candidate-${prefix}`,9043);
  core.sql.exec(`INSERT INTO production_published_operation_sources
    (operation_id,source_ref,release_id,candidate_sha,published_at)
    SELECT operation_id,source_ref,?,candidate_sha,published_at
    FROM production_published_operations WHERE release_id=?`,releaseId,releaseId);
  const releaseRow = core._one("SELECT * FROM production_releases WHERE id=?",releaseId);
  const eventDetails = [
    ["prepared",core._releaseEvidenceBinding(releaseRow)],
    ["authorized",core._releaseEvidenceBinding(releaseRow,{ authorization:true })],
    ...["executing","pages_deployed","worker_deployed","verified","complete"].map((type) =>
      [type,core._releaseEvidenceBinding(releaseRow,{ authorization:true,fencing:true })]),
  ];
  for (const [type,detail] of eventDetails) core.sql.exec(`INSERT INTO production_release_events
    (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)`,releaseId,type,
  "service:release",JSON.stringify(detail),9043);
}

function seedManySmallPendingRevisions(core,{ revisionCount,prefix }) {
  const reviewId = `${prefix}-review`;
  const revisionRows = [],receiptSources = [],decisionRows = [];
  for (let index=0;index<revisionCount;index++) {
    const suffix = String(index).padStart(6,"0");
    const revisionId = `${prefix}-revision-${suffix}`;
    const operationId = `${prefix}-operation-${suffix}`;
    const sourceRevision = `${prefix}-source-${suffix}`;
    const sourceRef = `data/copy/${prefix}-${suffix}.json#lead`;
    const operation = { id:operationId,decision_id:operationId,kind:"replace",
      source_ref:sourceRef,source_revision:sourceRevision,prod_base:"prod-base" };
    const evidence = { source_ref:sourceRef,source_revision:sourceRevision,
      prod_base:"prod-base",commit_sha:sourceRevision,original_hash:"old",proposed_hash:"new",
      suggestion_ids:[],source_original_text:"source text",source_proposed_text:"proposal",
      operations:[operation] };
    const evidenceDigest = core._digest(evidence);
    revisionRows.push({ id:revisionId,source_ref:sourceRef,source_revision:sourceRevision,
      prod_base:"prod-base",commit_sha:sourceRevision,original_hash:"old",proposed_hash:"new",
      original_text:"source text",proposed_text:"proposal",source_original_text:"source text",
      source_proposed_text:"proposal",suggestion_ids_json:"[]",
      operations_json:core._canonical([operation]),evidence_digest:evidenceDigest,created_at:9044 });
    const decision = { operation_id:operationId,operation_digest:core._digest(operation),
      group_id:null,decision:"accepted",note:"" };
    receiptSources.push({ review_revision_id:revisionId,source_revision:sourceRevision,
      prod_base:"prod-base",evidence_digest:evidenceDigest,decisions:[decision] });
    decisionRows.push({ review_revision_id:revisionId,...decision });
  }
  for (let start=0;start<revisionRows.length;start+=500) core.sql.exec(`
    INSERT INTO production_review_revisions
      (id,source_ref,source_revision,prod_base,commit_sha,original_hash,proposed_hash,
       original_text,proposed_text,source_original_text,source_proposed_text,suggestion_ids_json,
       operations_json,evidence_digest,created_at)
    SELECT json_extract(value,'$.id'),json_extract(value,'$.source_ref'),
      json_extract(value,'$.source_revision'),json_extract(value,'$.prod_base'),
      json_extract(value,'$.commit_sha'),json_extract(value,'$.original_hash'),
      json_extract(value,'$.proposed_hash'),json_extract(value,'$.original_text'),
      json_extract(value,'$.proposed_text'),json_extract(value,'$.source_original_text'),
      json_extract(value,'$.source_proposed_text'),json_extract(value,'$.suggestion_ids_json'),
      json_extract(value,'$.operations_json'),json_extract(value,'$.evidence_digest'),
      json_extract(value,'$.created_at') FROM json_each(?)`,JSON.stringify(
    revisionRows.slice(start,start+500)));
  const receipt = { review_id:reviewId,actor:"slot:damien",created_at:9044,
    sources:receiptSources };
  core.sql.exec(`INSERT INTO production_review_submissions
    (id,idempotency_key,request_digest,actor,receipt_hash,receipt_json,created_at)
    VALUES (?,?,?,?,?,?,?)`,reviewId,reviewId,reviewId,"slot:damien",core._digest(receipt),
  core._canonical(receipt),9044);
  core.sql.exec(`INSERT INTO production_review_submission_sources
    (review_id,review_revision_id,source_revision,prod_base,evidence_digest)
    SELECT ?,id,source_revision,prod_base,evidence_digest FROM production_review_revisions`,reviewId);
  core.sql.exec(`INSERT INTO production_review_submission_decisions
    (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id)
    SELECT ?,json_extract(value,'$.review_revision_id'),json_extract(value,'$.operation_id'),
      json_extract(value,'$.decision'),json_extract(value,'$.note'),
      json_extract(value,'$.operation_digest'),NULL FROM json_each(?)`,reviewId,
  JSON.stringify(decisionRows));
  core.sql.exec(`INSERT INTO production_review_operations
    (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,
     lifecycle_state)
    SELECT json_extract(operation.value,'$.id'),json_extract(operation.value,'$.decision_id'),?,
      revision.id,revision.source_ref,NULL,'accepted','','unpublished'
    FROM production_review_revisions revision JOIN json_each(revision.operations_json) operation`,
  reviewId);
}

test("observer operation frontier bounds normalized and evidence high-cardinality projections", async () => {
  const core = makeCore(() => 9042);
  seedHighCardinalityReview(core,{ operationCount:100_001,prefix:"high-normalized" });

  const projectionQueries = [];
  const all = core._all.bind(core);
  core._all = (sql,...args) => {
    projectionQueries.push({ sql,args });
    return all(sql,...args);
  };
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:0,blocked_state:"blocked",
  });
  assert.equal(context.active_release.id,"release-high-normalized");
  assert.equal(projectionQueries.some(({sql}) =>
    /r\.original_text|r\.operations_json/.test(sql)),false,
    "observer count projection must not select source text or full operations_json");
  assert.equal(projectionQueries.some(({sql,args}) =>
    /LIMIT 1 OFFSET \?/i.test(sql) && args.includes(100_000)),true,
    "observer count projection must stop at its 100001-row sentinel");

  const evidenceCore = makeCore(() => 9043);
  seedHighCardinalityReview(evidenceCore,{ operationCount:100_001,
    prefix:"high-evidence",normalized:false });
  const evidenceQueries = [];
  const evidenceAll = evidenceCore._all.bind(evidenceCore);
  evidenceCore._all = (sql,...args) => {
    evidenceQueries.push({ sql,args });
    return evidenceAll(sql,...args);
  };
  const evidenceContext = await observerFrontierFromCore(evidenceCore);
  assertObserverOperationFrontier(evidenceContext.operation_frontier,{
    pending_operation_count:0,blocked_state:"blocked",
  });
  assert.throws(() => evidenceCore._operationFrontierSummaryProjection(),
    /operation_frontier_integrity:missing_normalized_row/,
    "missing normalized evidence must fail through integrity validation");
  assert.equal(evidenceQueries.some(({sql,args}) =>
    /json_each\(revision\.operations_json\)[\s\S]*LIMIT 1 OFFSET \?/i.test(sql) &&
      args.includes(100_000)),false,
  "historical evidence must not be mistaken for pending-operation overflow");
});

test("observer operation frontier ignores high-cardinality rejected and published history", async () => {
  const core = makeCore(() => 9043);
  seedRejectedAndPublishedHistory(core,{ rejectedCount:50_001,publishedCount:50_000,
    prefix:"historical" });
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:0,blocked_state:"unblocked",
  });
  assert.deepEqual(core._operationFrontierSummaryProjection(),{
    eligible_operation_count:0,held_operation_count:0,
  });
});

test("observer operation frontier does not materialize rejected rows beside pending work", async () => {
  const core = makeCore(() => 9043);
  const sourceRef = "data/copy/mixed-history.json#lead";
  const operations = Array.from({ length:12_001 },(_value,index) => {
    const id = `mixed-history-operation-${String(index).padStart(5,"0")}`;
    return { id,decision_id:id,kind:"replace",source_ref:sourceRef,
      source_revision:"dev-mixed-history",prod_base:"prod-base" };
  });
  assert.equal(core.recordReviewRevision({ id:"revision-mixed-history",source_ref:sourceRef,
    source_revision:"dev-mixed-history",prod_base:"prod-base",commit_sha:"dev-mixed-history",
    original_hash:"old",proposed_hash:"new",original_text:"old",proposed_text:"new",
    suggestion_ids:["suggestion-mixed-history"],operations }).ok,true);
  const pendingId = operations.at(-1).id;
  const decisions = operations.map((operation) => ({ operation_id:operation.id,
    decision:operation.id === pendingId ? "accepted" : "rejected" }));
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",
    review_revision_id:"revision-mixed-history",source_revision:"dev-mixed-history",
    prod_base:"prod-base",decisions }).ok,true);
  assert.equal(core.submitPublisherReview({ id:"review-mixed-history",
    idempotency_key:"review-mixed-history",request_digest:"review-mixed-history",
    actor:"slot:damien",review_revision_id:"revision-mixed-history",
    source_revision:"dev-mixed-history",prod_base:"prod-base",decisions }).ok,true);
  assert.equal(core._one(`SELECT COUNT(*) AS count FROM production_review_operations
    WHERE lifecycle_state='unpublished' AND decision='accepted'`).count,1,
  "the fixture must contain exactly one pending operation");
  assert.equal(core._operationFrontierCandidateRevisions().length,1,
    "the pending operation must admit its revision to summary discovery");
  assert.doesNotThrow(() => core._assertOperationFrontierIntegrity(),
    "the mixed-history fixture must be internally coherent");

  const materialized = [];
  const all = core._all.bind(core);
  core._all = (sql,...args) => {
    const rows = all(sql,...args);
    if (sql.includes("normalized.*"))
      materialized.push(...rows.map((row) => row.operation_id));
    return rows;
  };
  const context = await observerFrontierFromCore(core);
  assert.deepEqual(materialized,[pendingId,pendingId],
    "only the pending universe should be materialized across the two summary passes");
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:1,blocked_state:"unblocked",
  });
});

test("observer operation frontier pages many small candidate revisions", async () => {
  const core = makeCore(() => 9044);
  seedManySmallPendingRevisions(core,{ revisionCount:6_001,prefix:"many-small" });
  let largestMaterializedQuery = 0;
  const candidatePageSizes = [];
  const all = core._all.bind(core);
  core._all = (sql,...args) => {
    const rows = all(sql,...args);
    largestMaterializedQuery = Math.max(largestMaterializedQuery,rows.length);
    if (/candidate_revisions[\s\S]*operation_count/i.test(sql)) candidatePageSizes.push(rows.length);
    return rows;
  };
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:6_001,blocked_state:"unblocked",
  });
  assert.deepEqual(candidatePageSizes,[5_000,1_001]);
  assert.equal(largestMaterializedQuery <= 5_000,true,
    "every observer query result must stay within the intended page bound");
});

test("observer operation frontier pages the exact 100000-operation maximum", async () => {
  const core = makeCore(() => 9044);
  seedHighCardinalityReview(core,{ operationCount:100_000,prefix:"high-exact" });
  let largestMaterializedPage = 0;
  const all = core._all.bind(core);
  core._all = (sql,...args) => {
    const rows = all(sql,...args);
    largestMaterializedPage = Math.max(largestMaterializedPage,rows.length);
    return rows;
  };
  const context = await observerFrontierFromCore(core);
  assertObserverOperationFrontier(context.operation_frontier,{
    pending_operation_count:100_000,blocked_state:"unblocked",
  });
  assert.equal(largestMaterializedPage <= 5_000,true,
    "the largest valid frontier must be counted in bounded pages");
});

test("observer operation frontier preserves every legacy observer key byte-for-byte", async () => {
  const legacy = { active_release:null,base_sha:"a".repeat(40),
    blocked_reason:"missing_batch_evidence",blocked_batch_id:"batch-blocked",
    batches:[{ batch_id:"batch-1",commit_sha:"c".repeat(40),generator_id:"generator-1",
      suggestion_ids:["private-id"] }] };
  const context = await observerFrontier({ ...legacy,projection:{
    eligible_operation_count:2,held_operation_count:0,
  } });
  const { operation_frontier,...otherKeys } = context;
  const expected = { active_release:null,base_sha:"a".repeat(40),
    blocked_reason:"missing_batch_evidence",blocked_batch_id:"batch-blocked",
    batches:[{ batch_id:"batch-1",commit_sha:"c".repeat(40),generator_id:"generator-1",
      member_count:1 }] };
  assert.equal(JSON.stringify(otherKeys),JSON.stringify(expected));
  assertObserverOperationFrontier(operation_frontier,{
    pending_operation_count:2,blocked_state:"unblocked",
  });
});

test("only a human Access Publisher can authorize; bearer and admin-only cannot", async () => {
  let calls = 0;
  const env = { EDIT_ORIGIN:"https://edit.example", PROD_RELEASE_LEDGER:"true",
    EDITOR:{ getByName:() => ({ authorizeProductionRelease:async (x) =>
      (calls++, { ok:true, release:{ id:x.id } }) }) } };
  const body = { id:"release-1", idempotency_key:"idem-1", target_batch_id:"batch-1",
    base_sha:"base", candidate_sha:"candidate", generator_id:"gen", evidence_hash:"ev",
    manifest_hash:"man", membership_hash:"members" };
  const human = { editor:"slot:damien", credential_channel:"access",
    scopes:scopes({ publisher:true }) };
  assert.equal((await publisherAuthorizeEndpoint(post(body), env, human)).status, 201);
  for (const denied of [
    { editor:"slot:damien", credential_channel:"access", scopes:scopes({ admin:true }) },
    { editor:"slot:service", credential_channel:"bearer",
      scopes:scopes({ publisher:true, admin:true }) },
    { editor:"slot:ai", service:"ai-review", credential_channel:"service",
      scopes:scopes({ publisher:true }) },
    { editor:"slot:damien", credential_channel:"cookie", scopes:scopes({ publisher:true }) },
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
    { scopes:scopes({ releaseService:true }), credential_channel:"bearer" });
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
  const auth = { editor:"service:release", credential_channel:"bearer",
    scopes:scopes({ releaseService:true }) };
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
  const humanAdmin = { editor:"slot:damien",credential_channel:"access",
    scopes:scopes({ admin:true }) };
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
  const devDaemon = { editor:"service:apply",credential_channel:"bearer",
    scopes:scopes({ admin:true }) };
  assert.equal((await productionClaimEndpoint(req("/edit/v1/prod/releases/claim", {}),env,devDaemon)).status,403);
  assert.deepEqual(calls.map((x) => x[0]),
    ["prepare","prepare","frontier","claim","restore-claim","renew","transition"]);
});

test("release observer can read only status frontier and audit", async () => {
  const calls = [];
  const stub = {
    getProductionRelease:async (id) => (calls.push(["status",id]),
      { id,state:"prepared",base_sha:"a".repeat(40),candidate_sha:"b".repeat(40),
        fencing_token:"secret-fence",authorization_key:"secret-authorization",events:[{detail:"secret"}] }),
    productionPreparationContext:async () => (calls.push(["frontier"]),
      { active_release:null,base_sha:"a".repeat(40),batches:[{ batch_id:"batch-1",
        commit_sha:"c".repeat(40),generator_id:"generator-1",suggestion_ids:["private-id"] }],
        projection:{ eligible_operation_count:0,held_operation_count:0,
          sources:[{ original_text:"secret prose" }] } }),
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
    scopes:scopes({ releaseObserver:true }) };
  const statusResponse = await publisherReleaseEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/status?id=release-1"),env,observer);
  assert.equal(statusResponse.status,200);
  const statusBody = await statusResponse.json();
  assert.deepEqual(statusBody.release,{ id:"release-1",state:"prepared",
    base_sha:"a".repeat(40),candidate_sha:"b".repeat(40) });
  assert.doesNotMatch(JSON.stringify(statusBody),/secret|fencing|authorization|events/);
  const frontierResponse = await productionPreparationContextEndpoint(new Request(
    "https://edit.example/edit/v1/prod/releases/frontier"),env,observer);
  assert.equal(frontierResponse.status,200);
  const frontierBody = await frontierResponse.json();
  assert.deepEqual(frontierBody.context,{ active_release:null,
    operation_frontier:{ pending_operation_count:0,blocked_state:"unblocked" },
    base_sha:"a".repeat(40),
    batches:[{ batch_id:"batch-1",commit_sha:"c".repeat(40),generator_id:"generator-1",
      member_count:1 }] });
  assert.doesNotMatch(JSON.stringify(frontierBody),/secret|private|projection|suggestion_ids/);
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
  const auth = { editor:"service:release",credential_channel:"bearer",
    scopes:scopes({ releaseService:true }) };
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
