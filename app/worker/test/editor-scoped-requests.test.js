// editor-scoped-requests.test.js — the scoped-change REQUEST lifecycle (U7).
// A request records an editor's natural-language instruction plus its
// enumerated blast radius; the home-box drafter claims it, drafts one edit per
// matched block as ONE ai_rewrite group, and the request tracks the canary ->
// remainder progression for module/course scopes (KTD5).
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { STATUS } from "../src/editor-status.js";

function file(core, over = {}) {
  return core.fileScopedRequest({
    id: over.id || "req-" + Math.random().toString(36).slice(2, 10),
    editor: "slot:john",
    level: over.level || "matter",
    matter: over.matter !== undefined ? over.matter : "m03-tort-meridian",
    part: over.part || null,
    module: over.module || null,
    instruction: over.instruction || "Change the filing deadline from 14 to 30 days.",
    radius_blocks: over.radius_blocks != null ? over.radius_blocks : 42,
    radius_files: 7,
    radius_matters: over.radius_matters != null ? over.radius_matters : 1,
    confirmed: !!over.confirmed,
  });
}

test("file -> requested, with canary phase only for module/course", () => {
  const core = makeCore();
  const a = file(core, { id: "r1", level: "matter" });
  assert.equal(a.ok, true);
  assert.equal(a.request.status, "requested");
  assert.equal(a.request.phase, "all");
  const b = file(core, { id: "r2", level: "module", module: "M2", matter: null,
                         radius_matters: 20 });
  assert.equal(b.request.phase, "canary");
  const c = file(core, { id: "r3", level: "course", matter: null,
                         radius_matters: 20 });
  assert.equal(c.request.phase, "canary");
});

test("filing is idempotent by id; replay returns the stored row", () => {
  const core = makeCore();
  file(core, { id: "r1" });
  const again = file(core, { id: "r1", instruction: "different" });
  assert.equal(again.ok, true);
  assert.equal(again.replay, true);
  assert.match(again.request.instruction, /filing deadline/);
});

test("claim is the sole requested->drafting writer and refuses double-claims", () => {
  const core = makeCore();
  file(core, { id: "r1" });
  assert.equal(core.claimScopedRequest("r1").ok, true);
  assert.equal(core.listScopedRequests("drafting").length, 1);
  assert.equal(core.claimScopedRequest("r1").ok, false);
  assert.equal(core.claimScopedRequest("nope").ok, false);
});

test("resolve enforces the lifecycle: drafting->drafted->done|declined; drafted->drafting for remainder", () => {
  const core = makeCore();
  file(core, { id: "r1", level: "module", module: "M2", matter: null });
  core.claimScopedRequest("r1");
  const d = core.resolveScopedRequest("r1", { status: "drafted", group_id: "grp-r1-canary",
                                              canary_matter: "m01-arbitration-meridian" });
  assert.equal(d.ok, true);
  const row = core.listScopedRequests("drafted")[0];
  assert.equal(row.group_id, "grp-r1-canary");
  assert.equal(row.canary_matter, "m01-arbitration-meridian");
  // canary verified -> remainder drafting
  const back = core.resolveScopedRequest("r1", { status: "drafting", phase: "remainder" });
  assert.equal(back.ok, true);
  assert.equal(core.listScopedRequests("drafting")[0].phase, "remainder");
  core.resolveScopedRequest("r1", { status: "drafted", group_id: "grp-r1-rest" });
  assert.equal(core.resolveScopedRequest("r1", { status: "done" }).ok, true);
  // terminal is terminal
  assert.equal(core.resolveScopedRequest("r1", { status: "drafting" }).ok, false);
  // illegal jumps are refused
  file(core, { id: "r2" });
  assert.equal(core.resolveScopedRequest("r2", { status: "drafted" }).ok, false);
});

test("groupOutcome counts EVERY member including terminal ones", () => {
  const core = makeCore();
  const input = (id, status) => {
    core.suggest({
      id, editor: "slot:ai", scope: "edit", origin: "ai_rewrite", kind: "prose",
      page: "p", block_anchor: "p:1",
      source_ref: "data/matters/m03-tort-meridian/case-file/x.md#b" + id.slice(-8),
      json_path: null, original_text: "o", original_hash: "h", new_text: "n",
      comment: null, context: "", map_version: "v", group_id: "grp-1", op_arg: null,
    });
  };
  input("aaaa0001", null);
  input("aaaa0002", null);
  core.decide({ group_id: "grp-1", decision: "accept" });
  // simulate one member applied via the batch machinery
  core.claimBatch("b1", { ids: ["aaaa0001", "aaaa0002"] });
  core.finalize("b1", { phase: "merged", applied: ["aaaa0001", "aaaa0002"] });
  const g = core.groupOutcome("grp-1");
  assert.equal(g.total, 2);
  assert.equal(g.by_status[STATUS.APPLIED], 2);
});
