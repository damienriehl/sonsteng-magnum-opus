// editor-structural.test.js — structural suggestion kinds (insert_after,
// delete, split, merge, move) through EditorStoreCore (U4 of the word-like-
// editing plan). The load-bearing rules:
//   * structural kinds are ordinary rows in the ONE pipeline (KTD3);
//   * they NEVER take the DIRECT_APPLY fast path, even though single-block
//     (the plan's execution note: scope is not risk);
//   * they never supersede (two inserts after one anchor are two intents);
//   * op_arg (merge's second ref / move's destination) round-trips.
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { STATUS } from "../src/editor-status.js";
import { STRUCTURAL_KINDS, AUTO_APPLY_KINDS } from "../src/editor-store-core.js";

let seq = 0;
function input(over = {}) {
  seq++;
  return {
    id: over.id || `st-${seq}-${Math.random().toString(36).slice(2)}`,
    editor: over.editor || "slot:john",
    scope: "edit",
    origin: over.origin || "human",
    kind: over.kind || "insert_after",
    page: "matters/m01/index.html",
    block_anchor: "matters/m01/index.html:3",
    source_ref: over.source_ref || "data/matters/m01/case-file/notes.md#b3fa9c21e",
    json_path: null,
    original_text: over.original_text || "Anchor paragraph text.",
    original_hash: "hash0",
    new_text: over.new_text !== undefined ? over.new_text : "A new paragraph.",
    comment: null,
    context: "intro",
    map_version: "v-test",
    group_id: over.group_id || null,
    op_arg: over.op_arg !== undefined ? over.op_arg : null,
  };
}

test("kind vocabulary: the five structural kinds are declared and disjoint from auto-apply", () => {
  assert.deepEqual(
    [...STRUCTURAL_KINDS].sort(),
    ["delete", "insert_after", "merge", "move", "split"]);
  for (const k of STRUCTURAL_KINDS) assert.equal(AUTO_APPLY_KINDS.has(k), false);
  assert.equal(AUTO_APPLY_KINDS.has("prose"), true);
  assert.equal(AUTO_APPLY_KINDS.has("json_scalar"), true);
  assert.equal(AUTO_APPLY_KINDS.has("comment"), false);
});

test("a structural suggestion lands pending and round-trips op_arg", () => {
  const core = makeCore();
  const r = core.suggest(input({
    id: "mv1", kind: "move",
    op_arg: "data/matters/m01/case-file/notes.md#b9c2e77a1",
  }));
  assert.equal(r.ok, true);
  assert.equal(r.suggestion.status, STATUS.PENDING);
  assert.equal(r.suggestion.kind, "move");
  assert.equal(r.suggestion.op_arg, "data/matters/m01/case-file/notes.md#b9c2e77a1");
  // and the review read carries it too
  const all = core.listAll();
  assert.equal(all.find((x) => x.id === "mv1").op_arg,
    "data/matters/m01/case-file/notes.md#b9c2e77a1");
});

test("DIRECT_APPLY never fast-paths a structural kind; prose still auto-accepts", () => {
  const core = makeCore();
  for (const kind of STRUCTURAL_KINDS) {
    const r = core.suggest(
      input({ kind, new_text: kind === "delete" ? null : "payload" }),
      undefined, { directApply: true });
    assert.equal(r.ok, true, kind);
    assert.equal(r.suggestion.status, STATUS.PENDING,
      `${kind} must queue for review, never auto-apply`);
  }
  const p = core.suggest(input({ kind: "prose" }), undefined, { directApply: true });
  assert.equal(p.suggestion.status, STATUS.ACCEPTED);
});

test("structural kinds do not supersede: two inserts after one anchor both stand", () => {
  const core = makeCore();
  const a = core.suggest(input({ id: "in1", kind: "insert_after", new_text: "First add." }));
  const b = core.suggest(input({ id: "in2", kind: "insert_after", new_text: "Second add." }));
  assert.equal(a.ok, true);
  assert.equal(b.ok, true);
  const rows = core.listAll().filter((r) => r.kind === "insert_after");
  assert.deepEqual(rows.map((r) => r.status), [STATUS.PENDING, STATUS.PENDING]);
  assert.equal(rows.every((r) => r.supersedes == null), true);
});

test("a prose edit on the same source_ref still supersedes prose only", () => {
  const core = makeCore();
  const sref = "data/matters/m01/case-file/notes.md#b3fa9c21e";
  core.suggest(input({ id: "pr1", kind: "prose", source_ref: sref, new_text: "v1" }));
  core.suggest(input({ id: "del1", kind: "delete", source_ref: sref, new_text: null }));
  core.suggest(input({ id: "pr2", kind: "prose", source_ref: sref, new_text: "v2" }));
  const byId = Object.fromEntries(core._all(
    "SELECT id, status FROM suggestions").map((r) => [r.id, r.status]));
  assert.equal(byId.pr1, STATUS.SUPERSEDED);   // prose superseded by prose
  assert.equal(byId.del1, STATUS.PENDING);     // structural untouched
  assert.equal(byId.pr2, STATUS.PENDING);
});

test("decide + claim treat structural rows exactly like any other row", () => {
  const core = makeCore();
  core.suggest(input({ id: "d1", kind: "delete", new_text: null }));
  assert.equal(core.decide({ id: "d1", decision: "accept" }).ok, true);
  const c = core.claimBatch("batch-1", {});
  assert.equal(c.ok, true);
  assert.deepEqual(c.claimed, ["d1"]);
  assert.equal(core.finalize("batch-1", { phase: "merged", applied: ["d1"] }).ok, true);
  assert.equal(core._get("d1").status, STATUS.APPLIED);
});

test("a delete needs no new_text and passes the suggest validation", () => {
  const core = makeCore();
  const r = core.suggest(input({ id: "d2", kind: "delete", new_text: null }));
  assert.equal(r.ok, true);
  assert.equal(r.suggestion.new_text, null);
});
