// editor-overlay.test.js — projectPendingItems now carries the fields the
// WYSIWYG pending-overlay client needs to reproduce the just-after-save visual
// state on reload: the FULL proposed new_text (edit kinds), the suggestion's
// baseline hash + map_version (client stale guard), and the author attribution
// (JOS/RSH) — the same "suggested by" signal the admin review surface stamps.
import { test } from "node:test";
import assert from "node:assert/strict";
import { projectPendingItems } from "../src/editor-map.js";
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
