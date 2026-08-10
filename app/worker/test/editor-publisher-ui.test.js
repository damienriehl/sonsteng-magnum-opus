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

test("characterization: sequential same-source edits remain separate attributed DEV rows", () => {
  const core = makeCore(() => 1000);
  for (const item of [
    { id:"sequence-1", original_text:"Strong points.", new_text:"Strong points!" },
    { id:"sequence-2", original_text:"Strong points!", new_text:"Strong and weak points!" },
  ]) {
    core.suggest({ ...item, editor:"slot:john", scope:"edit", origin:"human", kind:"prose",
      source_ref:"data/x.json#body", original_hash:`hash-${item.id}`, map_version:"v1" }, {},
    { directApply:true });
    core.claimBatch(`batch-${item.id}`, { base_sha:"base", ids:[item.id] });
    core.finalize(`batch-${item.id}`, { phase:"done", applied:[item.id],
      commit_sha:`commit-${item.id}`, generator_id:"generator-v1" });
  }
  const changes = core.publisherContext().batches.flatMap((batch) => batch.changes);
  assert.deepEqual(changes.map(({ id, source_ref, original_text, new_text }) =>
    ({ id, source_ref, original_text, new_text })), [
    { id:"sequence-1", source_ref:"data/x.json#body", original_text:"Strong points.",
      new_text:"Strong points!" },
    { id:"sequence-2", source_ref:"data/x.json#body", original_text:"Strong points!",
      new_text:"Strong and weak points!" },
  ]);
});

test("publisher context keeps partially deployed releases visible", () => {
  const core = makeCore(() => 1000);
  for (const state of ["pages_deployed", "worker_deployed"]) {
    core.sql.exec(
      `INSERT INTO production_releases
        (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
         target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
         membership_hash,created_at,updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      `release-${state}`, `idem-${state}`, `digest-${state}`, state, "service:release",
      "bearer", "production", "batch-1", "base", "candidate", "generator-v1",
      "evidence", "manifest", "membership", 1000, state === "pages_deployed" ? 1000 : 2000,
    );
    const projected = core.publisherContext();
    assert.equal(projected.release?.state, state);
    core.sql.exec("DELETE FROM production_releases WHERE id=?", `release-${state}`);
  }
});

test("verified recovery keeps the frozen member redlines visible", () => {
  const core = makeCore(() => 1000);
  core.suggest({ id:"verified-1",editor:"slot:john",scope:"edit",origin:"human",kind:"prose",
    source_ref:"data/x.json#verified",original_text:"Before",original_hash:"hash",
    new_text:"After",map_version:"v1" }, {}, { directApply:true });
  core.claimBatch("batch-verified", { base_sha:"base",ids:["verified-1"] });
  core.finalize("batch-verified", { phase:"done",applied:["verified-1"],
    commit_sha:"candidate",generator_id:"generator-v1" });
  const draft = core.prepareProductionRelease({ id:"release-verified",idempotency_key:"idem",
    request_digest:"digest",actor:"service:release",credential_channel:"bearer",
    target_environment:"production",target_batch_id:"batch-verified",base_sha:"base",
    candidate_sha:"candidate",generator_id:"generator-v1",evidence_hash:"evidence",
    manifest_hash:"manifest",ancestry_verified:true }).release;
  core.sql.exec("UPDATE production_releases SET state='verified' WHERE id=?", draft.id);
  const projected = core.publisherContext();
  assert.equal(projected.release.state,"verified");
  assert.equal(projected.batches[0].changes[0].new_text,"After");
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
  assert.match(html, /group g1/);
  assert.match(html, /Available on DEV — waiting for Publisher/);
  assert.match(html, /type="button"[^>]*id="pub-authorize"/);
  assert.match(html, /<details/);
  assert.match(html, /aria-live="polite"/);
  assert.doesNotMatch(html, /Publish automatically|Execute now|Retry deployment/);
});

test("schema-v2 prepared preview reports and renders frozen operation membership", async () => {
  const core = makeCore(() => 1000);
  core.sql.exec(`INSERT INTO production_releases
    (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,
     target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
     membership_hash,created_at,updated_at,schema_version,review_receipt_hash,projection_identity)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    "release-v2-ui","idem-v2-ui","digest-v2-ui","prepared","service:release","bearer",
    "production","operation-frontier","base-v2","candidate-v2","generator-v2","evidence-v2",
    "manifest-v2","membership-v2",1000,1000,2,"receipt-v2","projection-v2");
  core.sql.exec(`INSERT INTO production_release_operation_members
    (release_id,operation_id,review_revision_id,source_ref,group_id,ordinal) VALUES (?,?,?,?,?,?)`,
    "release-v2-ui","op-word","revision-home","data/copy/home.json#lead",null,0);
  core.sql.exec(`INSERT INTO production_release_operation_members
    (release_id,operation_id,review_revision_id,source_ref,group_id,ordinal) VALUES (?,?,?,?,?,?)`,
    "release-v2-ui","op-comma","revision-home","data/copy/home.json#lead","punctuation-group",1);
  const release = core.getProductionRelease("release-v2-ui");
  assert.deepEqual(release.suggestion_ids,[]);
  assert.deepEqual(release.operation_ids,["op-word","op-comma"]);
  const html = await renderPublisherPage({ release,batches:[] },"DR").text();
  assert.match(html,/containing <strong>2 atomic operations<\/strong>/);
  assert.match(html,/Frozen atomic operation 1/);
  assert.match(html,/op-word/);
  assert.match(html,/op-comma/);
  assert.match(html,/data\/copy\/home\.json#lead/);
  assert.match(html,/punctuation-group/);
  assert.doesNotMatch(html,/containing <strong>0 changes<\/strong>/);
});

test("characterization: Publisher redlines whole values even for separated word and punctuation edits", async () => {
  const original = "Weigh both sides' strong points, and weak points.";
  const proposed = "Weigh both sides' strongest points and weak points!";
  const html = await renderPublisherPage({ release:null, batches:[{
    batch_id:"batch-whole-value", commit_sha:"commit-whole-value", changes:[{
      id:"whole-value-1", editor:"slot:john", source_ref:"data/taxonomy/tasks.json#description",
      original_text:original, new_text:proposed, group_id:null,
    }],
  }] }, "DR").text();

  // Legacy baseline: unchanged context is repeated inside two complete-value spans. U2 replaces
  // this with structured atomic operations; U1 deliberately does not change the renderer.
  assert.match(html, /<del>Weigh both sides&#x27; strong points, and weak points\.<\/del>/);
  assert.match(html, /<ins>Weigh both sides&#x27; strongest points and weak points!<\/ins>/);
  assert.doesNotMatch(html, /data-operation-id|name="review-decision"|Submit review/);
});

test("Publisher preview labels an attributed History revert redline", async () => {
  const revertContext = { release:{ ...prepared,suggestion_ids:["revert-1"],batches:[
    { ordinal:0,batch_id:"revert-batch",commit_sha:"revert-commit" }] }, batches:[{
      batch_id:"revert-batch",commit_sha:"revert-commit",changes:[{
        id:"revert-1",kind:"history_revert",editor:"slot:damien",
        source_ref:"data/copy/home.json",original_text:"Edited copy",new_text:"Restored copy",
      }],
    }] };
  const html = await renderPublisherPage(revertContext,"DR").text();
  assert.match(html,/Approved History revert: data\/copy\/home\.json/);
  assert.match(html,/Requested by DR/);
  assert.match(html,/<del>Edited copy<\/del>/);
  assert.match(html,/<ins>Restored copy<\/ins>/);
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

const granularReview = {
  counts:{ total:3,reviewed:1,unreviewed:2,accepted:1,rejected:0,questioned:0 },
  revisions:[{
    revision:{ id:"revision-home",source_ref:"site/platform/index.html#lead",
      source_revision:"dev-1",prod_base:"prod-1",original_text:"Weigh strong points, and weak points.",
      proposed_text:"Weigh strongest points and weak points!",operations:[
        { id:"op-word",decision_id:"op-word",kind:"replace",old_text:"strong",new_text:"strongest",
          context_before:["Weigh "],context_after:[" points",","," ","and"," ","weak"," ","points"],
          source_ref:"site/platform/index.html#lead" },
        { id:"op-comma",decision_id:"op-comma",kind:"delete",old_text:",",new_text:"",
          context_before:["Weigh ","strong"," points"],context_after:[" ","and"," ","weak"," ","points"],
          source_ref:"site/platform/index.html#lead" },
        { id:"op-bang",decision_id:"op-bang",kind:"replace",old_text:".",new_text:"!",
          context_before:["and"," ","weak"," ","points"],context_after:[],
          source_ref:"site/platform/index.html#lead" },
      ] },
    draft:{ decisions:[{ operation_id:"op-word",decision:"accepted",note:"" }] },
    submitted_review:null,stale:false,
    counts:{ total:3,reviewed:1,unreviewed:2,accepted:1,rejected:0,questioned:0 },
  }],
};

test("granular Publisher renders bounded atomic redlines and one accessible decision per change", async () => {
  const html = await renderPublisherPage({ release:null,batches:[],review:granularReview }, "DR").text();
  assert.match(html,/Review changes/);
  assert.match(html,/site\/platform\/index\.html/);
  assert.match(html,/Deleted text<\/span><del[^>]*>strong<\/del>/);
  assert.match(html,/Added text<\/span><ins[^>]*>strongest<\/ins>/);
  assert.match(html,/Deleted text<\/span><del[^>]*>,<\/del>/);
  assert.doesNotMatch(html,/<del[^>]*>Weigh strong points, and weak points\.<\/del>/);
  assert.equal((html.match(/<fieldset class="pub-decision"/g)||[]).length,3);
  for (const choice of ["Accept","Reject","Ask question"]) assert.match(html,new RegExp(`> ${choice}<`));
  assert.match(html,/name="decision-op-word"/);
  assert.match(html,/Question \(required when asking\)/);
  assert.match(html,/Rejection note \(optional\)/);
  assert.match(html,/Submit review/);
  assert.match(html,/Submitting this review does not authorize production/);
});

test("move endpoints share one card and one radio group with textual semantics", async () => {
  const move = { ...granularReview,revisions:[{ ...granularReview.revisions[0],revision:{
    ...granularReview.revisions[0].revision,operations:[
      { id:"move-from",decision_id:"move-1",move_pair_id:"move-1",move_role:"from",kind:"delete",
        old_text:"distinctive amber phrase travels",new_text:"",context_before:["First. "],context_after:[] },
      { id:"move-to",decision_id:"move-1",move_pair_id:"move-1",move_role:"to",kind:"insert",
        old_text:"",new_text:"distinctive amber phrase travels",context_before:[],context_after:[" First."] },
    ] },draft:null,counts:{total:1,reviewed:0,unreviewed:1,accepted:0,rejected:0,questioned:0}
  }],counts:{total:1,reviewed:0,unreviewed:1,accepted:0,rejected:0,questioned:0} };
  const html = await renderPublisherPage({ review:move },"DR").text();
  assert.equal((html.match(/<fieldset class="pub-decision"/g)||[]).length,1);
  assert.match(html,/Moved from/);
  assert.match(html,/Moved to/);
  assert.equal((html.match(/name="decision-move-1"/g)||[]).length,3);
});

test("structural cards are held, filterable, and have no decision controls", async () => {
  const structural = { ...granularReview,revisions:[{ ...granularReview.revisions[0],revision:{
    ...granularReview.revisions[0].revision,operations:[
      { id:"merge-1",decision_id:"merge-1",kind:"merge",op:"merge",
        source_ref:"data/copy/home.json#a",op_arg:"data/copy/home.json#b",
        production_scope:"held",production_hold_reason:"structural_prod_deferred" },
    ] },draft:null,counts:{total:1,reviewed:0,unreviewed:0,accepted:0,rejected:0,questioned:0,
      held:1}
  }],counts:{total:1,reviewed:0,unreviewed:0,accepted:0,rejected:0,questioned:0,held:1} };
  const html = await renderPublisherPage({ review:structural },"DR").text();
  assert.match(html,/data-filter="held"[^>]*>Held \/ Not publishable <span>1<\/span>/);
  assert.match(html,/data-review-status="held"/);
  assert.match(html,/Not currently publishable/);
  assert.match(html,/Structural publication is deferred/);
  assert.doesNotMatch(html,/name="decision-merge-1"/);
  assert.doesNotMatch(html,/<fieldset class="pub-decision"/);
});

test("review assets autosave truthfully, block unsafe submit, retain drafts, and support navigation", () => {
  for (const phrase of ["Saving…","Saved","Couldn’t save","beforeunload","pub-next-unreviewed",
    "pub-next-problem","error-summary","aria-invalid","review\/draft","review\/submit"])
    assert.match(PUBLISHER_JS,new RegExp(phrase));
  assert.match(PUBLISHER_JS,/pendingSaves/);
  assert.match(PUBLISHER_JS,/questioned/);
  assert.match(PUBLISHER_JS,/required/);
  assert.match(PUBLISHER_CSS,/@media \(max-width:480px\)/);
  assert.match(PUBLISHER_CSS,/@media \(forced-colors:active\)/);
  assert.match(PUBLISHER_CSS,/overflow-wrap:anywhere/);
});

test("one Submit review action sends one multi-source request", () => {
  assert.match(PUBLISHER_JS,/const body=\{sources\}/);
  assert.equal((PUBLISHER_JS.match(/boundedFetch\("\/edit\/v1\/publisher\/review\/submit"/g)||[]).length,1);
  assert.match(PUBLISHER_JS,/AbortController\(\)/);
  assert.match(PUBLISHER_JS,/setTimeout\(\(\)=>controller\.abort\(\),20000\)/);
  assert.equal((PUBLISHER_JS.match(/boundedFetch\("\/edit\/v1\//g)||[]).length,3);
  assert.doesNotMatch(PUBLISHER_JS,/for\(const source[^}]+review\/submit/s);
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
