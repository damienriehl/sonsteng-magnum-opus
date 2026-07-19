// editor-overlay.test.js — projectPendingItems now carries the fields the
// WYSIWYG pending-overlay client needs to reproduce the just-after-save visual
// state on reload: the FULL proposed new_text (edit kinds), the suggestion's
// baseline hash + map_version (client stale guard), and the author attribution
// (JOS/RSH) — the same "suggested by" signal the admin review surface stamps.
import { test } from "node:test";
import assert from "node:assert/strict";
import { projectPendingItems } from "../src/editor-map.js";

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
