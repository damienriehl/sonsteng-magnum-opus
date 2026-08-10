import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { makeCore } from "./editor-sql-helper.mjs";
import { publisherReviewEndpoint, publisherReviewDraftEndpoint,
  publisherReviewSubmitEndpoint,reviewBackfillEndpoint,claimEndpoint } from "../src/editor-endpoints.js";

const operations = [
  { id:"op-word", decision_id:"op-word", kind:"replace", source_ref:"data/copy/home.json#lead",
    source_revision:"dev-1", prod_base:"prod-1", base_range:[4,7], proposed_range:[4,8],
    old_text:"bad", new_text:"good", context_before:["The "], context_after:[" idea"] },
  { id:"op-comma", decision_id:"op-comma", kind:"insert", source_ref:"data/copy/home.json#lead",
    source_revision:"dev-1", prod_base:"prod-1", base_range:[12,12], proposed_range:[13,14],
    old_text:"", new_text:",", context_before:[" idea"], context_after:[] },
];

function revision(over = {}) {
  return { id:"revision-1", source_ref:"data/copy/home.json#lead", source_revision:"dev-1",
    prod_base:"prod-1", original_hash:"old-hash", proposed_hash:"new-hash",
    original_text:"The bad idea", proposed_text:"The good idea,", commit_sha:"dev-1",
    suggestion_ids:["suggestion-1"], operations, ...over };
}

function publisher(actor = "slot:damien") {
  return { editor:actor, service:null, credential_channel:"access",
    scopes:{ publisher:{ granted:true }, admin:{ granted:false }, edit:{ granted:false } } };
}

function request(path, method = "GET", body) {
  return new Request(`https://edit.example.com${path}`, { method,
    headers:{ "content-type":"application/json", "x-edit-request":"1", origin:"https://edit.example.com" },
    body:body == null ? undefined : JSON.stringify(body) });
}

const envFor = (core) => ({ EDITOR:{ getByName:() => core }, EDIT_ORIGIN:"https://edit.example.com" });

test("Publisher drafts are actor-bound, reloadable, replaceable, and unanswered stays held", () => {
  const core = makeCore(() => 1000);
  assert.equal(core.recordReviewRevision(revision()).ok, true);
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien", review_revision_id:"revision-1",
    source_revision:"dev-1", prod_base:"prod-1", decisions:[
      { operation_id:"op-word", decision:"accepted" },
    ] }).ok, true);
  assert.deepEqual(core.getPublisherReview("slot:damien").draft.decisions,
    [{ operation_id:"op-word", decision:"accepted", note:"" }]);
  assert.equal(core.getPublisherReview("slot:other").draft, null);
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:other", review_revision_id:"revision-1",
    source_revision:"dev-1", prod_base:"prod-1", decisions:[] }).reason, "draft_owned");
  assert.equal(core.submitPublisherReview({ id:"other-review",idempotency_key:"other-submit",
    request_digest:"other-digest",actor:"slot:other",review_revision_id:"revision-1",
    source_revision:"dev-1",prod_base:"prod-1",decisions:[] }).reason,"draft_owned");
  assert.equal(core.getPublisherReview("slot:damien").counts.unreviewed, 1);
});

test("read projection keeps the latest reviewable revision for every source", () => {
  const core = makeCore(() => 1500);
  core.recordReviewRevision(revision());
  const otherRef = "data/copy/skills.json#lead";
  core.recordReviewRevision(revision({ id:"revision-skills",source_ref:otherRef,
    operations:operations.map((op) => ({ ...op,id:`skills-${op.id}`,decision_id:`skills-${op.id}`,
      source_ref:otherRef })) }));
  const view = core.getPublisherReview("slot:damien");
  assert.deepEqual(view.revisions.map((item) => item.revision.source_ref).sort(),
    ["data/copy/home.json#lead",otherRef].sort());
  assert.equal(view.counts.total,4);
  assert.equal(view.counts.unreviewed,4);
});

test("submission freezes exact evidence, validates questions, and replays only identically", () => {
  const core = makeCore(() => 2000);
  core.recordReviewRevision(revision());
  const binding = { id:"review-1", idempotency_key:"submit-1", request_digest:"digest-1",
    actor:"slot:damien", review_revision_id:"revision-1", source_revision:"dev-1", prod_base:"prod-1",
    decisions:[{ operation_id:"op-word", decision:"accepted" },
      { operation_id:"op-comma", decision:"questioned", note:"Why this comma?" }] };
  core.savePublisherReviewDraft(binding);
  assert.equal(core.submitPublisherReview({ ...binding,
    decisions:[binding.decisions[0], { operation_id:"op-comma", decision:"questioned", note:"" }] }).reason,
    "question_required");
  const submitted = core.submitPublisherReview(binding);
  assert.equal(submitted.ok, true);
  assert.deepEqual(submitted.review.decisions.map((d) => d.decision), ["questioned", "accepted"]);
  assert.equal(core.submitPublisherReview(binding).replay, true);
  assert.equal(core.submitPublisherReview({ ...binding, request_digest:"different" }).reason,
    "idempotency_conflict");
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien", review_revision_id:"revision-1",
    source_revision:"dev-1", prod_base:"prod-1", decisions:[] }).reason, "review_submitted");
});

test("one multi-source submission is atomic when any exact source binding is stale", () => {
  const core = makeCore(() => 2500);
  const skillsRef = "data/copy/skills.json#lead";
  const skillsRevision = revision({ id:"revision-skills",source_ref:skillsRef,
    operations:operations.map((op) => ({ ...op,id:`skills-${op.id}`,decision_id:`skills-${op.id}`,
      source_ref:skillsRef })) });
  core.recordReviewRevision(revision());
  core.recordReviewRevision(skillsRevision);
  const home = { review_revision_id:"revision-1",source_revision:"dev-1",prod_base:"prod-1",
    decisions:[{ operation_id:"op-word",decision:"accepted" }] };
  const skills = { review_revision_id:"revision-skills",source_revision:"dev-1",prod_base:"prod-1",
    decisions:[{ operation_id:"skills-op-word",decision:"rejected",note:"Keep the original." }] };
  core.savePublisherReviewDraft({ actor:"slot:damien",...home });
  core.savePublisherReviewDraft({ actor:"slot:damien",...skills });
  core.recordReviewRevision(revision({ id:"revision-skills-2",source_ref:skillsRef,
    source_revision:"dev-2",commit_sha:"dev-2",original_hash:"new-hash",proposed_hash:"newer-hash",
    operations:operations.map((op) => ({ ...op,id:`skills-next-${op.id}`,
      decision_id:`skills-next-${op.id}`,source_ref:skillsRef,source_revision:"dev-2" })) }));

  const result = core.submitPublisherReview({ id:"review-multi",idempotency_key:"submit-multi",
    request_digest:"digest-multi",actor:"slot:damien",sources:[home,skills] });
  assert.equal(result.reason,"stale_revision");
  const view = core.getPublisherReview("slot:damien");
  assert.equal(view.revisions.find((item) => item.revision.id === "revision-1").submitted_review,null);
  assert.equal(view.revisions.find((item) => item.revision.id === "revision-skills-2").submitted_review,null);
  assert.ok(view.revisions.find((item) => item.revision.id === "revision-1").draft);
});

test("one multi-source receipt preserves every binding and replays only the exact request", () => {
  const core = makeCore(() => 2750);
  const skillsRef = "data/copy/skills.json#lead";
  core.recordReviewRevision(revision());
  core.recordReviewRevision(revision({ id:"revision-skills",source_ref:skillsRef,
    operations:operations.map((op) => ({ ...op,id:`skills-${op.id}`,decision_id:`skills-${op.id}`,
      source_ref:skillsRef })) }));
  const sources = [
    { review_revision_id:"revision-1",source_revision:"dev-1",prod_base:"prod-1",
      decisions:[{ operation_id:"op-word",decision:"accepted" }] },
    { review_revision_id:"revision-skills",source_revision:"dev-1",prod_base:"prod-1",
      decisions:[{ operation_id:"skills-op-word",decision:"questioned",note:"Confirm this term?" }] },
  ];
  for (const source of sources) assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",...source }).ok,true);
  const binding = { id:"review-multi",idempotency_key:"submit-multi",request_digest:"digest-multi",
    actor:"slot:damien",sources };
  const submitted = core.submitPublisherReview(binding);
  assert.equal(submitted.ok,true);
  assert.deepEqual(submitted.review.sources.map((source) => source.review_revision_id),
    ["revision-1","revision-skills"]);
  assert.equal(new Set(core.getPublisherReview("slot:damien").revisions.map((item) =>
    item.submitted_review?.id)).size,1);
  assert.equal(core.submitPublisherReview(binding).replay,true);
  assert.equal(core.submitPublisherReview({ ...binding,request_digest:"changed" }).reason,
    "idempotency_conflict");
});

test("stale, missing, tampered, and split-group evidence fail closed", () => {
  const core = makeCore(() => 3000);
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien", review_revision_id:"missing",
    source_revision:"dev-1", prod_base:"prod-1", decisions:[] }).reason, "missing_revision_evidence");
  core.recordReviewRevision(revision({ operations:operations.map((op) => ({ ...op, group_id:"group-1" })) }));
  assert.equal(core.recordReviewRevision(revision({ operations:operations.map((op,index) => index ? op :
    { ...op,new_text:"tampered" }) })).reason,
    "idempotency_conflict");
  const base = { actor:"slot:damien", review_revision_id:"revision-1", source_revision:"dev-1",
    prod_base:"prod-1" };
  assert.equal(core.savePublisherReviewDraft({ ...base,
    decisions:[{ operation_id:"unknown", decision:"accepted" }] }).reason, "operation_mismatch");
  core.savePublisherReviewDraft({ ...base, decisions:[{ operation_id:"op-word", decision:"accepted" },
    { operation_id:"op-comma", decision:"rejected" }] });
  assert.equal(core.submitPublisherReview({ ...base, id:"review-split", idempotency_key:"split",
    request_digest:"split", decisions:[{ operation_id:"op-word", decision:"accepted" },
      { operation_id:"op-comma", decision:"rejected" }] }).reason, "partial_group");
  core.recordReviewRevision(revision({ id:"revision-2", source_revision:"dev-2", commit_sha:"dev-2",
    original_hash:"new-hash", proposed_hash:"newer-hash",
    original_text:"The good idea,", proposed_text:"A better idea," , operations:operations.map((op) => ({
      ...op, id:`next-${op.id}`, decision_id:`next-${op.id}`, source_revision:"dev-2" })) }));
  assert.equal(core.savePublisherReviewDraft({ ...base, decisions:[] }).reason, "stale_revision");
});

test("a verified PROD frontier advance stales drafts even when DEV did not change", () => {
  const core = makeCore(() => 3250);
  core.recordReviewRevision(revision());
  core.sql.exec("INSERT INTO production_releases (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,membership_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "release-new","release-key","release-digest","complete","slot:damien","access","production",
    "batch-new","prod-1","prod-2","generator-v1","evidence","manifest","membership",3250,3250);
  assert.equal(core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:"revision-1",
    source_revision:"dev-1",prod_base:"prod-1",decisions:[] }).reason,"stale_prod_base");
});

test("schema migration is repeatable and the Durable Object forwards every review RPC", () => {
  const core = makeCore(() => 3500);
  assert.doesNotThrow(() => core.initSchema());
  const wrapper = readFileSync(new URL("../src/editor-store.js", import.meta.url), "utf8");
  for (const method of ["recordReviewRevision","backfillReviewRevisions","getPublisherReview","savePublisherReviewDraft",
    "submitPublisherReview"]) {
    assert.match(wrapper, new RegExp(`${method}\\(.*this\\.core\\.${method}\\(`));
  }
  const router = readFileSync(new URL("../src/editor.js", import.meta.url), "utf8");
  for (const path of ["/edit/v1/publisher/review","/edit/v1/publisher/review/draft",
    "/edit/v1/publisher/review/submit","/edit/v1/publisher/review/backfill"])
    assert.match(router, new RegExp(path));
});

test("legacy backfill is atomic, idempotent, audited, and never assigns a decision", () => {
  const core = makeCore(() => 3600);
  for (const [id, source_ref] of [["suggestion-1","data/copy/home.json#lead"],
    ["suggestion-2","data/copy/skills.json#lead"]]) {
    core.suggest({ id,editor:"slot:john",scope:"edit",origin:"human",kind:"prose",source_ref,
      original_text:"Old copy",original_hash:"old-hash",new_text:"New copy",map_version:"v1" },
      {}, { directApply:true });
    core.sql.exec("UPDATE suggestions SET status='applied',apply_batch_id=? WHERE id=?",`batch-${id}`,id);
    core.sql.exec("INSERT INTO apply_batches (batch_id,base_sha,commit_sha,generator_id,phase,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
      `batch-${id}`,"prod-1",`dev-${id}`,"generator-v1","done",3500,3500);
  }
  const withApplyEvidence = (item, suggestionId) => ({ ...item,
    batch_chain:[{ batch_id:`batch-${suggestionId}`,base_sha:"prod-1",commit_sha:`dev-${suggestionId}` }],
    suggestion_evidence:[{ suggestion_id:suggestionId,batch_id:`batch-${suggestionId}`,
      commit_sha:`dev-${suggestionId}` }] });
  const revisions = [revision(), revision({ id:"revision-skills",source_ref:"data/copy/skills.json#lead",
    commit_sha:"dev-suggestion-2",suggestion_ids:["suggestion-2"],operations:operations.map((op) => ({
      ...op,id:`skills-${op.id}`,decision_id:`skills-${op.id}`,source_ref:"data/copy/skills.json#lead" })) })];
  revisions[0] = withApplyEvidence(revision({ commit_sha:"dev-suggestion-1" }),"suggestion-1");
  revisions[1] = withApplyEvidence(revisions[1],"suggestion-2");
  const payload = { migration_id:"legacy-20260810",prod_base:"prod-1",revisions };
  assert.deepEqual(core.backfillReviewRevisions(payload),
    { ok:true,migration_id:"legacy-20260810",inserted:2,replayed:0 });
  assert.deepEqual(core.backfillReviewRevisions(payload),
    { ok:true,migration_id:"legacy-20260810",inserted:0,replayed:2,replay:true });
  const view = core.getPublisherReview("slot:damien");
  assert.equal(view.counts.total,4);
  assert.equal(view.counts.unreviewed,4);
  assert.equal(view.counts.accepted,0);
  assert.equal(core._one("SELECT COUNT(*) AS n FROM production_review_migrations").n,1);
  assert.equal(core._one("SELECT COUNT(*) AS n FROM production_review_decisions").n,0);
  assert.equal(core._one("SELECT COUNT(*) AS n FROM production_review_submission_decisions").n,0);
  assert.equal(core.backfillReviewRevisions({ ...payload,revisions:[revision({
    commit_sha:"dev-suggestion-1",proposed_hash:"tampered" }),revisions[1]] }).reason,
    "idempotency_conflict");
});

test("legacy backfill rejects non-applied or mismatched evidence without partial writes", () => {
  const core = makeCore(() => 3700);
  core.suggest({ id:"pending-1",editor:"slot:john",scope:"edit",origin:"human",kind:"prose",
    source_ref:"data/copy/home.json#lead",original_text:"Old",original_hash:"old-hash",
    new_text:"New",map_version:"v1" }, {}, { directApply:false });
  core.sql.exec("INSERT INTO apply_batches (batch_id,base_sha,commit_sha,generator_id,phase,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
    "batch-pending","prod-1","dev-1","generator-v1","done",3600,3600);
  const result = core.backfillReviewRevisions({ migration_id:"bad-migration",prod_base:"prod-1",
    revisions:[revision({ suggestion_ids:["pending-1"],batch_chain:[{
      batch_id:"batch-pending",base_sha:"prod-1",commit_sha:"dev-1" }],
      suggestion_evidence:[{ suggestion_id:"pending-1",batch_id:"batch-pending",commit_sha:"dev-1" }] })] });
  assert.equal(result.reason,"legacy_suggestion_not_applied");
  assert.equal(core._one("SELECT COUNT(*) AS n FROM production_review_revisions").n,0);
  assert.equal(core._one("SELECT COUNT(*) AS n FROM production_review_migrations").n,0);
});

test("legacy backfill binds cumulative same-source edits through the exact batch chain", () => {
  const core = makeCore(() => 3800);
  const sourceRef = "data/copy/home.json#lead";
  for (const [id,batchId,base,commit] of [
    ["suggestion-1","batch-1","prod-1","dev-1"],
    ["suggestion-2","batch-2","dev-1","dev-2"],
  ]) {
    core.suggest({ id,editor:"slot:john",scope:"edit",origin:"human",kind:"prose",source_ref:sourceRef,
      original_text:"Old",original_hash:"old-hash",new_text:"New",map_version:"v1" },{}, { directApply:true });
    core.sql.exec("UPDATE suggestions SET status='applied',apply_batch_id=? WHERE id=?",batchId,id);
    core.sql.exec("INSERT INTO apply_batches (batch_id,base_sha,commit_sha,generator_id,phase,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
      batchId,base,commit,"generator-v1","done",3700,3700);
  }
  const migrated = revision({ commit_sha:"dev-2",suggestion_ids:["suggestion-1","suggestion-2"],
    batch_chain:[
      { batch_id:"batch-1",base_sha:"prod-1",commit_sha:"dev-1" },
      { batch_id:"batch-2",base_sha:"dev-1",commit_sha:"dev-2" },
    ],suggestion_evidence:[
      { suggestion_id:"suggestion-1",batch_id:"batch-1",commit_sha:"dev-1" },
      { suggestion_id:"suggestion-2",batch_id:"batch-2",commit_sha:"dev-2" },
    ] });

  assert.equal(core.backfillReviewRevisions({ migration_id:"cumulative",prod_base:"prod-1",
    revisions:[migrated] }).ok,true);
  const second = makeCore(() => 3800);
  for (const [id,batchId,base,commit] of [
    ["suggestion-1","batch-1","prod-1","dev-1"],
    ["suggestion-2","batch-2","dev-1","dev-2"],
  ]) {
    second.suggest({ id,editor:"slot:john",scope:"edit",origin:"human",kind:"prose",source_ref:sourceRef,
      original_text:"Old",original_hash:"old-hash",new_text:"New",map_version:"v1" },{}, { directApply:true });
    second.sql.exec("UPDATE suggestions SET status='applied',apply_batch_id=? WHERE id=?",batchId,id);
    second.sql.exec("INSERT INTO apply_batches (batch_id,base_sha,commit_sha,generator_id,phase,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
      batchId,base,commit,"generator-v1","done",3700,3700);
  }
  assert.equal(second.backfillReviewRevisions({ migration_id:"omitted",prod_base:"prod-1",
    revisions:[{ ...migrated,suggestion_ids:["suggestion-2"],suggestion_evidence:[migrated.suggestion_evidence[1]] }] }).reason,
    "missing_revision_evidence");
});

test("legacy backfill endpoint is bearer-only and never grants browser migration authority", async () => {
  const core = makeCore(() => 3725);
  const req = request("/edit/v1/publisher/review/backfill","POST",{
    migration_id:"legacy-http",prod_base:"prod-1",revisions:[revision()] });
  assert.equal((await reviewBackfillEndpoint(req,envFor(core),publisher())).status,403);
  assert.equal((await reviewBackfillEndpoint(req,envFor(core),{
    ...publisher("service:apply"),credential_channel:"bearer",service:"apply",
    scopes:{ admin:{ granted:true } } })).status,400);
});

test("apply finalization records revision evidence atomically and rolls back a bad binding", () => {
  const core = makeCore(() => 3750);
  core.suggest({ id:"suggestion-1",editor:"slot:john",scope:"edit",origin:"human",kind:"prose",
    source_ref:"data/copy/home.json#lead",original_text:"The bad idea",original_hash:"old-hash",
    new_text:"The good idea,",map_version:"v1" }, {}, { directApply:true });
  assert.equal(core.claimBatch("batch-1",{ base_sha:"dev-base",ids:["suggestion-1"] }).ok,true);
  assert.equal(core.finalize("batch-1",{ phase:"done",applied:["suggestion-1"],commit_sha:"dev-1",
    generator_id:"generator-v1",review_revisions:[revision({ commit_sha:"wrong" })] }).reason,
    "revision_mismatch");
  assert.equal(core.listAll().find((item) => item.id === "suggestion-1").status,"in_flight");
  assert.equal(core.getPublisherReview("slot:damien").blocked_reason,"missing_revision_evidence");
  assert.equal(core.finalize("batch-1",{ phase:"done",applied:["suggestion-1"],commit_sha:"dev-1",
    generator_id:"generator-v1",review_revisions:[revision()] }).ok,true);
  assert.equal(core.getPublisherReview("slot:damien").revision.id,"revision-1");
});

test("claim endpoint returns the Worker-owned completed PROD frontier", async () => {
  const core = makeCore(() => 3775);
  core.sql.exec(`INSERT INTO production_releases
    (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
     target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
     membership_hash,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,"release-complete","release-key","release-digest",
    "complete","service:release","service","PROD","release-batch","dev-ambient","prod-trusted",
    "generator","evidence","manifest","membership",3700,3700);
  core.suggest({ id:"claimable-1",editor:"slot:john",scope:"edit",origin:"human",kind:"prose",
    source_ref:"data/copy/home.json#lead",original_text:"Old",original_hash:"old-hash",
    new_text:"New",map_version:"v1" }, {}, { directApply:true });
  const response = await claimEndpoint(request("/edit/v1/claim","POST",{
    batch_id:"claim-endpoint",base_sha:"dev-ambient",ids:["claimable-1"] }),envFor(core),{
    editor:"service:apply",credential_channel:"bearer",scopes:{ admin:{ granted:true } } });

  assert.equal(response.status,200);
  assert.equal((await response.json()).prod_base,"prod-trusted");
});

test("canonical-mutation completion records the same immutable review evidence", () => {
  const core = makeCore(() => 3900);
  core.fileRevertRequest({ id:"revert-1",editor:"slot:damien",doc:"data/copy/home.json",
    run_first:"aaaaaaa",run_last:"bbbbbbb",approved:true });
  const mutation = { id:"revert-1",batch_id:"revert-revert-1",actor:"slot:damien",
    kind:"history_revert",source_ref:"data/copy/home.json#lead",original_text:"The bad idea",
    new_text:"The good idea,",original_hash:"old-hash",new_hash:"new-hash",base_sha:"prod-1",
    commit_sha:"dev-1",generator_id:"generator-v1" };
  assert.equal(core.recordCanonicalMutation(mutation).ok,true);
  assert.equal(core.completeCanonicalMutation({ ...mutation,review_revision:revision() }).ok,true);
  assert.equal(core.getPublisherReview("slot:damien").revision.id,"revision-1");
  assert.equal(core.completeCanonicalMutation({ ...mutation,review_revision:revision({ proposed_hash:"tampered" }) }).reason,
    "idempotency_conflict");
});

test("Publisher review endpoints require a current human Access Publisher and CSRF", async () => {
  const core = makeCore(() => 4000);
  core.recordReviewRevision(revision());
  const env = envFor(core);
  assert.equal((await publisherReviewEndpoint(request("/edit/v1/publisher/review"), env,
    publisher())).status, 200);
  const draft = { review_revision_id:"revision-1", source_revision:"dev-1", prod_base:"prod-1",
    decisions:[{ operation_id:"op-word", decision:"rejected", note:"Too informal" }] };
  assert.equal((await publisherReviewDraftEndpoint(request("/edit/v1/publisher/review/draft", "POST", draft),
    env, publisher())).status, 200);
  const submission = { ...draft,id:"review-http-1",idempotency_key:"review-http-key-1" };
  assert.equal((await publisherReviewSubmitEndpoint(request("/edit/v1/publisher/review/submit", "POST",
    submission),env,publisher())).status,201);
  assert.equal((await publisherReviewSubmitEndpoint(request("/edit/v1/publisher/review/submit", "POST",
    submission),env,publisher())).status,200);
  assert.equal((await publisherReviewSubmitEndpoint(request("/edit/v1/publisher/review/submit", "POST",
    { ...submission,id:"review-http-2",idempotency_key:"review-http-key-2" }),env,publisher())).status,409);
  assert.equal((await publisherReviewDraftEndpoint(new Request("https://edit.example.com/edit/v1/publisher/review/draft",
    { method:"POST", headers:{ "content-type":"application/json" }, body:JSON.stringify(draft) }),
    env, publisher())).status, 403);
  for (const denied of [
    { ...publisher(), credential_channel:"bearer", service:"review-service" },
    { ...publisher(), editor:"slot:ai", credential_channel:"service", service:"ai" },
    { ...publisher(), editor:"slot:admin", scopes:{ publisher:{granted:false}, admin:{granted:true} } },
    { ...publisher(), editor:"slot:editor", scopes:{ publisher:{granted:false}, edit:{granted:true} } },
    { ...publisher(), editor:"slot:approver", scopes:{ publisher:{granted:false}, admin:{granted:true} } },
  ]) assert.equal((await publisherReviewSubmitEndpoint(request("/edit/v1/publisher/review/submit", "POST",
    { ...draft, id:"review-1", idempotency_key:"key-1" }), env, denied)).status, 403);
});

test("HTTP submit preflights every source before creating the shared receipt", async () => {
  const core = makeCore(() => 4250);
  const skillsRef = "data/copy/skills.json#lead";
  core.recordReviewRevision(revision());
  core.recordReviewRevision(revision({ id:"revision-skills",source_ref:skillsRef,
    operations:operations.map((op) => ({ ...op,id:`skills-${op.id}`,decision_id:`skills-${op.id}`,
      source_ref:skillsRef })) }));
  const home = { review_revision_id:"revision-1",source_revision:"dev-1",prod_base:"prod-1",
    decisions:[{ operation_id:"op-word",decision:"accepted" }] };
  const skills = { review_revision_id:"revision-skills",source_revision:"dev-1",prod_base:"prod-1",
    decisions:[{ operation_id:"skills-op-word",decision:"rejected" }] };
  core.savePublisherReviewDraft({ actor:"slot:damien",...home });
  core.savePublisherReviewDraft({ actor:"slot:damien",...skills });
  core.recordReviewRevision(revision({ id:"revision-skills-next",source_ref:skillsRef,
    source_revision:"dev-2",commit_sha:"dev-2",original_hash:"new-hash",proposed_hash:"newer-hash",
    operations:operations.map((op) => ({ ...op,id:`next-${op.id}`,decision_id:`next-${op.id}`,
      source_ref:skillsRef,source_revision:"dev-2" })) }));
  const response = await publisherReviewSubmitEndpoint(request("/edit/v1/publisher/review/submit","POST",{
    id:"review-http-multi",idempotency_key:"review-http-multi-key",sources:[home,skills],
  }),envFor(core),publisher());
  assert.equal(response.status,409);
  const view = core.getPublisherReview("slot:damien");
  assert.equal(view.revisions.find((item) => item.revision.id === "revision-1").submitted_review,null);
  assert.ok(view.revisions.find((item) => item.revision.id === "revision-1").draft);
});
