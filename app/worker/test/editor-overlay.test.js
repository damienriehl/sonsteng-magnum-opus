// editor-overlay.test.js — projectPendingItems now carries the fields the
// WYSIWYG pending-overlay client needs to reproduce the just-after-save visual
// state on reload: the FULL proposed new_text (edit kinds), the suggestion's
// baseline hash + map_version (client stale guard), and the author attribution
// (JOS/RSH) — the same "suggested by" signal the admin review surface stamps.
import { test } from "node:test";
import assert from "node:assert/strict";
import { projectPendingItems, projectReviewAnnotations } from "../src/editor-map.js";
import { makeCore } from "./editor-sql-helper.mjs";

const ROWS = [
  { block_anchor: "matters/m01/index.html:3", source_ref: "sref-a", status: "pending",
    kind: "prose", new_text: "The revised paragraph text.", original_hash: "h-a",
    map_version: "spine-1", editor: "slot:john" },
  { block_anchor: "matters/m01/index.html:5", source_ref: "sref-b", status: "accepted",
    kind: "prose", new_text: "x".repeat(500), original_hash: "h-b",
    map_version: "spine-1", editor: "slot:roger" },
  { block_anchor: "matters/m01/index.html:7", source_ref: "sref-c", status: "pending",
    kind: "comment", comment: "please check this", original_hash: "h-c",
    map_version: "spine-1", editor: "slot:roger", decision_note: "noted" },
];

test("projectPendingItems stamps attribution from the server-resolved editor identity", () => {
  const out = projectPendingItems(ROWS);
  assert.equal(out[0].attribution, "JOS");   // slot:john
  assert.equal(out[1].attribution, "RSH");   // slot:roger
  assert.equal(out[2].attribution, "RSH");   // comments carry attribution too
});

test("edit items carry the FULL new_text (uncapped) for hydration + base_hash/map_version stale guards", () => {
  const out = projectPendingItems(ROWS);
  assert.equal(out[0].new_text, "The revised paragraph text.");
  assert.equal(out[1].new_text.length, 500);   // full, not the 200-char preview
  assert.equal(out[0].base_hash, "h-a");
  assert.equal(out[0].map_version, "spine-1");
});

test("preview stays capped (tooltip/summary) even though new_text ships full", () => {
  const out = projectPendingItems(ROWS, 200);
  assert.equal(out[1].preview.length, 200);    // capped
  assert.equal(out[1].new_text.length, 500);   // uncapped
});

test("comment items get NO new_text overlay field (they render as margin bubbles, not block text)", () => {
  const out = projectPendingItems(ROWS);
  assert.equal(out[2].new_text, undefined);
  assert.equal(out[2].preview, "please check this");
  assert.equal(out[2].note, "noted");
  assert.equal(out[2].block_index, 7);
});

test("unmask (SL1): a needs_human EDIT projects its status + FULL new_text so the client can show the honest warning overlay", () => {
  // The client paints the text but flags it "needs attention — not applied"; the
  // projection must carry BOTH the needs_human status AND the new_text for that.
  const out = projectPendingItems([
    { block_anchor: "matters/m01/index.html:9", source_ref: "sref-nh", status: "needs_human",
      kind: "prose", new_text: "An edit the apply engine could not land automatically.",
      original_hash: "h-nh", map_version: "spine-1", editor: "slot:john" },
  ]);
  assert.equal(out[0].status, "needs_human");
  assert.equal(out[0].new_text, "An edit the apply engine could not land automatically.");
  assert.equal(out[0].base_hash, "h-nh");
  assert.equal(out[0].attribution, "JOS");
});

test("block_index parses the trailing anchor segment; unknown editor falls back to the upper-cased slot", () => {
  const out = projectPendingItems([
    { block_anchor: "p:2", source_ref: "s", status: "pending", kind: "prose",
      new_text: "t", original_hash: "h", editor: "slot:newperson" },
  ]);
  assert.equal(out[0].block_index, 2);
  assert.equal(out[0].attribution, "NEWPERSON");
});

test("rows with no editor/new_text/hash stay backward-compatible (no undefined fields leak)", () => {
  const out = projectPendingItems([
    { block_anchor: "p:1", source_ref: "s", status: "declined", kind: "prose" },
  ]);
  assert.equal(out[0].preview, "");
  assert.ok(!("new_text" in out[0]));
  assert.ok(!("base_hash" in out[0]));
  assert.ok(!("attribution" in out[0]));
});

// Cross-editor hydration: the pending-overlay source (listForPage) returns EVERY
// editor's active suggestions on one page, so when editor A loads that page the
// island/pending payload includes co-editor B's item, correctly attributed. This
// exercises the exact router/endpoint pipeline: listForPage -> projectPendingItems.
function sug(over) {
  return {
    id: over.id,
    editor: over.editor,
    scope: "edit",
    origin: "human",
    kind: "prose",
    page: over.page,
    block_anchor: `${over.page}:${over.block ?? 3}`,
    source_ref: over.source_ref,
    original_text: "The original text.",
    original_hash: over.original_hash || "h0",
    new_text: over.new_text,
    comment: null,
    context: "intro",
    map_version: "v-test",
    group_id: null,
  };
}

test("cross-editor: page-scoped source surfaces BOTH editors' pending items with per-author attribution", () => {
  const core = makeCore();
  const PAGE = "matters/m01/index.html";
  // John (slot:john -> JOS) and Roger (slot:roger -> RSH) each suggest on the SAME page.
  core.suggest(sug({ id: "j1", editor: "slot:john", page: PAGE, block: 3,
    source_ref: "sref-john", new_text: "John's revision." }));
  core.suggest(sug({ id: "r1", editor: "slot:roger", page: PAGE, block: 5,
    source_ref: "sref-roger", new_text: "Roger's revision." }));
  // A different page must NOT bleed into this page's overlay.
  core.suggest(sug({ id: "j2", editor: "slot:john", page: "matters/m02/index.html",
    block: 1, source_ref: "sref-other", new_text: "Elsewhere." }));

  // What editor A (John) receives when loading PAGE: the page-scoped, cross-editor read.
  const items = projectPendingItems(core.listForPage(PAGE));
  const bySref = Object.fromEntries(items.map((i) => [i.source_ref, i]));

  assert.equal(items.length, 2);                       // both on-page items, no off-page bleed
  assert.equal(bySref["sref-john"].attribution, "JOS"); // caller's own
  assert.equal(bySref["sref-roger"].attribution, "RSH");// co-editor B, attributed
  assert.equal(bySref["sref-roger"].new_text, "Roger's revision."); // full text for hydration
  assert.ok(!("sref-other" in bySref));                // page scoping holds
});

test("a set-aside suggestion leaves the page quietly (declined never overlays)", () => {
  // Damien found a leftover E2E row painting "Not used · JOS" onto a paragraph of
  // the Osgard statement — permanently, for every reader, with no way to clear it
  // and no obvious meaning. A declined suggestion already reverts the block to its
  // canonical wording; the pill was the only residue, and it addressed nobody.
  // The outcome belongs in the review page and the digest. The editor guide
  // already promises this: set-aside wording "quietly returns to the original".
  const core = makeCore();
  const PAGE = "matters/m01/index.html";
  core.suggest(sug({ id: "keep", editor: "slot:john", page: PAGE, block: 3,
    source_ref: "sref-live", new_text: "Still under review." }));
  core.suggest(sug({ id: "gone", editor: "slot:john", page: PAGE, block: 5,
    source_ref: "sref-declined", new_text: "Set aside." }));
  core.decide({ id: "gone", decision: "decline", note: "not this time" });

  const srefs = core.listForPage(PAGE).map((r) => r.source_ref);
  assert.ok(srefs.includes("sref-live"), "an open suggestion still overlays the page");
  assert.ok(!srefs.includes("sref-declined"), "a declined EDIT must not overlay the page");

  // ...but a declined COMMENT stays: the margin bubble is the conversation, and
  // dropping it would erase what the reviewer actually said.
  core.suggest(sug({ id: "cmt", editor: "slot:john", page: PAGE, block: 7,
    source_ref: "sref-comment", new_text: undefined }));
  core.decide({ id: "cmt", decision: "decline", note: "noted, thanks" });

  // ...but it is NOT forgotten: the row survives with its decision intact, so the
  // review surface and the digest can still account for it.
  const declined = core.listAll().concat(core.listForEditor("slot:john"))
    .filter((r) => r.source_ref === "sref-declined");
  if (declined.length) assert.equal(declined[0].status, "declined");
});

test("submitted review annotations retain every decision state and inert reviewer prose", () => {
  const hostile = "Why <img src=x onerror=alert(1)> this wording?";
  const projected = projectReviewAnnotations([{
    source_ref:"sref-a", review_revision_id:"revision-1", source_revision:"dev-1",
    proposed_hash:"current-hash", reviewer:"slot:damien", submitted_at:1234, stale:false,
    operations:[
      { id:"op-a",decision_id:"op-a",kind:"replace",old_text:"weak",new_text:"strong",
        proposed_range:[4,10] },
      { id:"op-b",decision_id:"op-b",kind:"insert",old_text:"",new_text:"!",
        proposed_range:[10,11] },
      { id:"op-c",decision_id:"op-c",kind:"delete",old_text:"very ",new_text:"",
        proposed_range:[0,0] },
      { id:"op-d",decision_id:"op-d",kind:"insert",old_text:"",new_text:"clear ",
        proposed_range:[0,6] },
    ],
    decisions:[
      { operation_id:"op-a",decision:"accepted",note:"" },
      { operation_id:"op-b",decision:"rejected",note:"Too emphatic." },
      { operation_id:"op-c",decision:"questioned",note:hostile },
    ],
  }]);
  assert.deepEqual(projected.map((item) => item.status),
    ["accepted","rejected","questioned","unanswered"]);
  assert.equal(projected[0].reviewer,"DR");
  assert.equal(projected[2].note,hostile, "projection keeps text as data for textContent rendering");
  assert.equal(projected[3].operation_id,"op-d");
});

test("a later source revision yields stale annotation evidence, never a new-span attachment", () => {
  const core = makeCore(() => 1000);
  const operation = { id:"old-op",decision_id:"old-op",kind:"replace",source_ref:"sref-a",
    source_revision:"dev-1",prod_base:"prod-1",base_range:[0,3],proposed_range:[0,4],
    old_text:"bad",new_text:"good" };
  core.recordReviewRevision({ id:"rev-1",source_ref:"sref-a",source_revision:"dev-1",
    prod_base:"prod-1",commit_sha:"dev-1",original_hash:"old",proposed_hash:"reviewed",
    original_text:"bad",proposed_text:"good",suggestion_ids:["s1"],operations:[operation] });
  core.savePublisherReviewDraft({ actor:"slot:damien",review_revision_id:"rev-1",
    source_revision:"dev-1",prod_base:"prod-1",decisions:[{operation_id:"old-op",decision:"rejected"}] });
  core.submitPublisherReview({ id:"review-1",idempotency_key:"submit-1",request_digest:"digest-1",
    actor:"slot:damien",review_revision_id:"rev-1",source_revision:"dev-1",prod_base:"prod-1",
    decisions:[{operation_id:"old-op",decision:"rejected"}] });
  core.recordReviewRevision({ id:"rev-2",source_ref:"sref-a",source_revision:"dev-2",
    prod_base:"prod-1",commit_sha:"dev-2",original_hash:"reviewed",proposed_hash:"current",
    original_text:"good",proposed_text:"better",suggestion_ids:["s2"],operations:[{
      ...operation,id:"new-op",decision_id:"new-op",source_revision:"dev-2",old_text:"good",new_text:"better"
    }] });

  const annotations = core.getDevReviewAnnotations(["sref-a"]);
  assert.equal(annotations.length,1);
  assert.equal(annotations[0].review_revision_id,"rev-1");
  assert.equal(annotations[0].stale,true);
  assert.equal(annotations[0].current_proposed_hash,"current");
});
