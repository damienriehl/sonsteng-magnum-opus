// editor-store.test.js — EditorStoreCore behavior under real node:sqlite:
// status-machine transitions (incl. in_flight/supersede/terminal enforcement),
// idempotent dedupe, ceilings, group atomic accept + lone-member reject,
// decide-is-sole-accept-writer, claim/finalize/reconcile lease + journal.
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { STATUS } from "../src/editor-status.js";

let seq = 0;
function suggestInput(over = {}) {
  seq++;
  return {
    id: over.id || `sug-${seq}-${Math.random().toString(36).slice(2)}`,
    editor: over.editor || "slot:john",
    scope: over.scope || "edit",
    origin: over.origin || "human",
    kind: over.kind || "prose",
    page: over.page || "matters/m01/index.html",
    block_anchor: over.block_anchor || "matters/m01/index.html:3",
    source_ref: over.source_ref || "data/matters/m01/exercise.json#sections.intro.body_md.p0",
    json_path: over.json_path || null,
    original_text: over.original_text || "The original text.",
    original_hash: over.original_hash || "hash0",
    new_text: over.new_text !== undefined ? over.new_text : "The improved text.",
    comment: over.comment !== undefined ? over.comment : null,
    context: over.context || "intro",
    map_version: over.map_version || "v-test",
    group_id: over.group_id || null,
  };
}

test("suggest inserts a pending row and returns it", () => {
  const core = makeCore();
  const r = core.suggest(suggestInput({ id: "s1" }));
  assert.equal(r.ok, true);
  assert.equal(r.suggestion.status, STATUS.PENDING);
  assert.equal(r.suggestion.editor, "slot:john");
});

test("idempotent dedupe: replaying the same id never double-inserts", () => {
  const core = makeCore();
  const input = suggestInput({ id: "dup1", new_text: "first" });
  const a = core.suggest(input);
  const b = core.suggest({ ...input, new_text: "second (ignored)" });
  assert.equal(a.ok, true);
  assert.equal(b.ok, true);
  assert.equal(b.replay, true);
  assert.equal(b.suggestion.new_text, "first"); // stored row unchanged
  assert.equal(core.listAll().length, 1);
});

test("supersede: same editor re-edits same source_ref -> prior goes superseded", () => {
  const core = makeCore();
  const sref = "data/matters/m01/exercise.json#x.p0";
  const first = core.suggest(suggestInput({ id: "f1", source_ref: sref, new_text: "v1" }));
  const second = core.suggest(suggestInput({ id: "f2", source_ref: sref, new_text: "v2" }));
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.equal(core._get("f1").status, STATUS.SUPERSEDED);
  assert.equal(core._get("f2").status, STATUS.PENDING);
  assert.equal(core._get("f2").supersedes, "f1");
});

test("decide is the SOLE writer of accepted; accept moves pending->accepted", () => {
  const core = makeCore();
  core.suggest(suggestInput({ id: "a1" }));
  const r = core.decide({ id: "a1", decision: "accept" });
  assert.equal(r.ok, true);
  assert.equal(core._get("a1").status, STATUS.ACCEPTED);
});

test("decline records the note and is terminal", () => {
  const core = makeCore();
  core.suggest(suggestInput({ id: "d1" }));
  const r = core.decide({ id: "d1", decision: "decline", note: "not now" });
  assert.equal(r.ok, true);
  assert.equal(core._get("d1").status, STATUS.DECLINED);
  assert.equal(core._get("d1").decision_note, "not now");
  // terminal: a further accept is rejected
  const again = core.decide({ id: "d1", decision: "accept" });
  assert.equal(again.ok, false);
});

test("terminal enforcement: applied/superseded/declined cannot transition", () => {
  const core = makeCore();
  // applied via claim+finalize
  core.suggest(suggestInput({ id: "t1" }));
  core.decide({ id: "t1", decision: "accept" });
  core.claimBatch("b-t1", { ids: ["t1"] });
  core.finalize("b-t1", { phase: "done", applied: ["t1"] });
  assert.equal(core._get("t1").status, STATUS.APPLIED);
  assert.equal(core.markDrift("t1").ok, false); // no leaving applied
  assert.equal(core.decide({ id: "t1", decision: "decline" }).ok, false);
});

test("group accept is one atomic txn; lone-member accept is rejected", () => {
  const core = makeCore();
  const gid = "grp-1";
  core.suggest(suggestInput({ id: "g1", origin: "companion", group_id: gid, source_ref: "src#a" }));
  core.suggest(suggestInput({ id: "g2", origin: "companion", group_id: gid, source_ref: "src#b" }));
  // lone-member accept forbidden
  const lone = core.decide({ id: "g1", decision: "accept" });
  assert.equal(lone.ok, false);
  assert.equal(lone.reason, "group_accept_required");
  assert.equal(core._get("g1").status, STATUS.PENDING);
  // whole-group accept moves both
  const grp = core.decide({ group_id: gid, decision: "accept" });
  assert.equal(grp.ok, true);
  assert.equal(core._get("g1").status, STATUS.ACCEPTED);
  assert.equal(core._get("g2").status, STATUS.ACCEPTED);
});

test("ceilings: per-editor pending cap rejects beyond the limit", () => {
  const core = makeCore();
  const ceil = { perEditorPending: 3, dailyPerEditor: 999, globalPending: 999, maxBytes: 16384, leaseMs: 1000 };
  for (let i = 0; i < 3; i++) assert.equal(core.suggest(suggestInput({ source_ref: "s#" + i }), ceil).ok, true);
  const over = core.suggest(suggestInput({ source_ref: "s#over" }), ceil);
  assert.equal(over.ok, false);
  assert.equal(over.reason, "pending_ceiling");
});

test("ceilings: daily cap rejects beyond the daily limit", () => {
  const core = makeCore();
  const ceil = { perEditorPending: 999, dailyPerEditor: 2, globalPending: 999, maxBytes: 16384, leaseMs: 1000 };
  assert.equal(core.suggest(suggestInput({ source_ref: "d#1" }), ceil).ok, true);
  assert.equal(core.suggest(suggestInput({ source_ref: "d#2" }), ceil).ok, true);
  const over = core.suggest(suggestInput({ source_ref: "d#3" }), ceil);
  assert.equal(over.ok, false);
  assert.equal(over.reason, "daily_cap");
});

test("ceilings: 16KB size cap rejects oversized new_text", () => {
  const core = makeCore();
  const big = "x".repeat(17 * 1024);
  const r = core.suggest(suggestInput({ new_text: big }));
  assert.equal(r.ok, false);
  assert.equal(r.reason, "too_large");
});

test("claim: accepted -> in_flight for whole groups only, with a lease", () => {
  const core = makeCore();
  core.suggest(suggestInput({ id: "c1", source_ref: "c#1" }));
  core.decide({ id: "c1", decision: "accept" });
  const r = core.claimBatch("batch-1", { base_sha: "abc123", ids: ["c1"] });
  assert.equal(r.ok, true);
  assert.deepEqual(r.claimed, ["c1"]);
  assert.equal(core._get("c1").status, STATUS.IN_FLIGHT);
  assert.equal(core._get("c1").apply_batch_id, "batch-1");
  assert.ok(core._get("c1").lease_expires_at > 0);
});

test("claim never partially claims a group (all members must be accepted)", () => {
  const core = makeCore();
  const gid = "grp-claim";
  core.suggest(suggestInput({ id: "cg1", origin: "companion", group_id: gid, source_ref: "cg#a" }));
  core.suggest(suggestInput({ id: "cg2", origin: "companion", group_id: gid, source_ref: "cg#b" }));
  core.decide({ group_id: gid, decision: "accept" });
  // decline one AFTER accept is illegal (accepted has no ->declined), so simulate
  // a not-fully-accepted group by adding a fresh pending member.
  core.suggest(suggestInput({ id: "cg3", origin: "companion", group_id: gid, source_ref: "cg#c" }));
  const r = core.claimBatch("b-partial", { ids: ["cg1", "cg2", "cg3"] });
  // cg3 is pending -> group not fully accepted -> nothing claimed
  assert.equal(r.ok, false);
  assert.equal(r.reason, "nothing_to_claim");
  assert.equal(core._get("cg1").status, STATUS.ACCEPTED);
});

test("finalize journals phases and moves in_flight to terminal states", () => {
  const core = makeCore();
  core.suggest(suggestInput({ id: "fi1", source_ref: "fi#1" }));
  core.suggest(suggestInput({ id: "fi2", source_ref: "fi#2" }));
  core.decide({ id: "fi1", decision: "accept" });
  core.decide({ id: "fi2", decision: "accept" });
  core.claimBatch("b-fin", { ids: ["fi1", "fi2"] });
  core.finalize("b-fin", { phase: "validated" });
  core.finalize("b-fin", { phase: "done", applied: ["fi1"], accepted_blocked: ["fi2"] });
  assert.equal(core._get("fi1").status, STATUS.APPLIED);
  assert.equal(core._get("fi2").status, STATUS.ACCEPTED_BLOCKED);
});

test("reconcile rolls back a crashed pre-merged batch (in_flight -> accepted)", () => {
  let clock = 1_000_000;
  const core = makeCore(() => clock);
  core.suggest(suggestInput({ id: "rc1", source_ref: "rc#1" }));
  core.decide({ id: "rc1", decision: "accept" });
  core.claimBatch("b-crash", { ids: ["rc1"], leaseMs: 1000 });
  assert.equal(core._get("rc1").status, STATUS.IN_FLIGHT);
  core.finalize("b-crash", { phase: "patched" }); // crashed before merge
  clock += 5000; // lease expires
  const rep = core.reconcile();
  assert.equal(core._get("rc1").status, STATUS.ACCEPTED); // re-queued
  assert.ok(rep.rolled_back.includes("rc1"));
});

test("reconcile completes a post-merged crashed batch (in_flight -> applied)", () => {
  let clock = 2_000_000;
  const core = makeCore(() => clock);
  core.suggest(suggestInput({ id: "rc2", source_ref: "rc#2" }));
  core.decide({ id: "rc2", decision: "accept" });
  core.claimBatch("b-merged", { ids: ["rc2"], leaseMs: 1000 });
  core.finalize("b-merged", { phase: "merged" }); // crashed after merge, before finalize-applied
  clock += 5000;
  core.reconcile();
  assert.equal(core._get("rc2").status, STATUS.APPLIED);
});

test("drift -> pending only via re-anchor (never straight to accepted)", () => {
  const core = makeCore();
  core.suggest(suggestInput({ id: "dr1" }));
  assert.equal(core.markDrift("dr1").ok, true);
  assert.equal(core._get("dr1").status, STATUS.DRIFT);
  // accept directly from drift is illegal
  assert.equal(core.decide({ id: "dr1", decision: "accept" }).ok, false);
  // re-anchor forces re-review
  assert.equal(core.reanchor("dr1", {}).ok, true);
  assert.equal(core._get("dr1").status, STATUS.PENDING);
});

test("digest summarizes counts by status", () => {
  const core = makeCore();
  core.suggest(suggestInput({ id: "dg1", source_ref: "dg#1" }));
  core.suggest(suggestInput({ id: "dg2", source_ref: "dg#2" }));
  core.decide({ id: "dg1", decision: "accept" });
  const d = core.digest();
  assert.equal(d.by_status[STATUS.PENDING], 1);
  assert.equal(d.by_status[STATUS.ACCEPTED], 1);
});

test("listForEditor hides superseded (drafts) but shows closure statuses", () => {
  const core = makeCore();
  const sref = "le#1";
  core.suggest(suggestInput({ id: "le1", source_ref: sref, new_text: "v1" }));
  core.suggest(suggestInput({ id: "le2", source_ref: sref, new_text: "v2" })); // supersedes le1
  const items = core.listForEditor("slot:john");
  const ids = items.map((i) => i.id);
  assert.ok(ids.includes("le2"));
  assert.ok(!ids.includes("le1")); // superseded hidden
});
