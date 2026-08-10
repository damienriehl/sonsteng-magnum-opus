// editor-revert.test.js — revert-v1 (History browser "Request revert", SL8):
// the store's revert_requests lifecycle + the /edit/v1/revert-request,
// /revert-requests, /revert-resolve endpoints. Editors FILE (status=requested);
// admin FILES approved (executes on the daemon's next tick); the daemon consumes
// approved rows and marks them done/failed. Reverts are visible in the digest.
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { REVERT_STATUS } from "../src/editor-store-core.js";
import {
  revertRequestEndpoint, revertRequestsEndpoint, revertResolveEndpoint, revertRecordEndpoint,
} from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";

const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1, instructor: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};

// A DO stub backed by a real EditorStoreCore so the endpoint->store path is real.
function envWithCore(over = {}) {
  const core = makeCore();
  const stub = {
    async fileRevertRequest(input) { return core.fileRevertRequest(input); },
    async listRevertRequests(status) { return core.listRevertRequests(status); },
    async resolveRevertRequest(id, status, note) { return core.resolveRevertRequest(id, status, note); },
    async recordCanonicalMutation(input) { return core.recordCanonicalMutation(input); },
    async completeCanonicalMutation(input) { return core.completeCanonicalMutation(input); },
    async digest() { return core.digest(); },
  };
  return { env: { ...ENV_BASE, ...over, EDITOR: { getByName() { return stub; } } }, core };
}

const authBearer = (env, token) =>
  resolveAuth(env, new Request("https://worker.example.com/x", {
    headers: { Authorization: `Bearer ${token}` },
  }));

const RUN = ["a1b2c3d4e5", "f6e7d8c9b0"];
function revReq(body, method = "POST", url = "https://worker.example.com/edit/v1/revert-request") {
  return new Request(url, {
    method,
    headers: { "X-Edit-Request": "1", "content-type": "application/json" },
    body: method === "GET" ? undefined : JSON.stringify(body),
  });
}

// ---- store-core lifecycle ---------------------------------------------------
test("store: editor request lands 'requested'; admin lands 'approved'", () => {
  const core = makeCore();
  const a = core.fileRevertRequest({ id: "r1", editor: "slot:john", doc: "data/firm/firm.json", run_first: "aa", run_last: "bb" });
  assert.equal(a.ok, true);
  assert.equal(a.request.status, REVERT_STATUS.REQUESTED);
  const b = core.fileRevertRequest({ id: "r2", editor: "slot:admin", doc: "data/firm/firm.json", run_first: "aa", run_last: "bb", approved: true });
  assert.equal(b.request.status, REVERT_STATUS.APPROVED);
});

test("store: listRevertRequests filters by status; daemon reads approved", () => {
  const core = makeCore();
  core.fileRevertRequest({ id: "r1", editor: "slot:john", doc: "d", run_first: "aa", run_last: "bb" });
  core.fileRevertRequest({ id: "r2", editor: "slot:admin", doc: "d", run_first: "aa", run_last: "bb", approved: true });
  assert.equal(core.listRevertRequests(REVERT_STATUS.APPROVED).length, 1);
  assert.equal(core.listRevertRequests(REVERT_STATUS.APPROVED)[0].id, "r2");
  assert.equal(core.listRevertRequests(null).length, 2);
});

test("store: resolve moves requested->approved->done; terminal is guarded", () => {
  const core = makeCore();
  core.fileRevertRequest({ id: "r1", editor: "slot:john", doc: "d", run_first: "aa", run_last: "bb" });
  assert.equal(core.resolveRevertRequest("r1", REVERT_STATUS.APPROVED).ok, true);
  assert.equal(core.resolveRevertRequest("r1", REVERT_STATUS.DONE).ok, true);
  const again = core.resolveRevertRequest("r1", REVERT_STATUS.FAILED);
  assert.equal(again.ok, false);
  assert.equal(again.reason, "already_terminal");
});

test("store: idempotent replay by id never double-inserts", () => {
  const core = makeCore();
  const a = core.fileRevertRequest({ id: "r1", editor: "slot:john", doc: "d", run_first: "aa", run_last: "bb" });
  const b = core.fileRevertRequest({ id: "r1", editor: "slot:john", doc: "d", run_first: "aa", run_last: "bb" });
  assert.equal(a.ok, true);
  assert.equal(b.replay, true);
  assert.equal(core.listRevertRequests(null).length, 1);
});

test("store: digest surfaces open reverts", () => {
  const core = makeCore();
  core.fileRevertRequest({ id: "r1", editor: "slot:admin", doc: "d", run_first: "aa", run_last: "bb", approved: true });
  const d = core.digest();
  assert.equal(d.reverts_by_status[REVERT_STATUS.APPROVED], 1);
  assert.equal(d.reverts_open.length, 1);
  assert.equal(d.reverts_open[0].doc, "d");
});

// ---- endpoints --------------------------------------------------------------
test("endpoint: edit scope files a 'requested' revert", async () => {
  const { env } = envWithCore();
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await revertRequestEndpoint(revReq({ doc: "data/firm/firm.json", run: RUN }), env, auth);
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.status, REVERT_STATUS.REQUESTED);
});

test("endpoint: admin scope files an 'approved' revert (executes next tick)", async () => {
  const { env } = envWithCore();
  const auth = await authBearer(env, "admin-opaque-token-value-999");
  const resp = await revertRequestEndpoint(revReq({ doc: "data/firm/firm.json", run: RUN }), env, auth);
  const body = await resp.json();
  assert.equal(body.status, REVERT_STATUS.APPROVED);
});

test("endpoint: bad run range / bad sha -> 400", async () => {
  const { env } = envWithCore();
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const r1 = await revertRequestEndpoint(revReq({ doc: "d", run: ["only-one"] }), env, auth);
  assert.equal(r1.status, 400);
  const r2 = await revertRequestEndpoint(revReq({ doc: "d", run: ["nothex!!", "f6e7d8c9b0"] }), env, auth);
  assert.equal(r2.status, 400);
});

test("endpoint: no scope -> 403; missing CSRF header -> 403", async () => {
  const { env } = envWithCore();
  const anon = await authBearer(env, "not-a-real-token");
  const r403 = await revertRequestEndpoint(revReq({ doc: "d", run: RUN }), env, anon);
  assert.equal(r403.status, 403);
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const noCsrf = new Request("https://worker.example.com/edit/v1/revert-request", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ doc: "d", run: RUN }),
  });
  assert.equal((await revertRequestEndpoint(noCsrf, env, auth)).status, 403);
});

test("endpoint: /revert-requests is admin-only and returns approved rows", async () => {
  const { env, core } = envWithCore();
  core.fileRevertRequest({ id: "r1", editor: "slot:admin", doc: "d", run_first: "aa", run_last: "bb", approved: true });
  const editorAuth = await authBearer(env, "john-opaque-token-value-123");
  assert.equal((await revertRequestsEndpoint(revReq(null, "GET", "https://worker.example.com/edit/v1/revert-requests"), env, editorAuth)).status, 403);
  const adminAuth = await authBearer(env, "admin-opaque-token-value-999");
  const resp = await revertRequestsEndpoint(revReq(null, "GET", "https://worker.example.com/edit/v1/revert-requests?status=approved"), env, adminAuth);
  const body = await resp.json();
  assert.equal(body.items.length, 1);
  assert.equal(body.items[0].id, "r1");
});

test("endpoint: /revert-resolve marks done (daemon path), admin-only", async () => {
  const { env, core } = envWithCore();
  core.fileRevertRequest({ id: "r1", editor: "slot:admin", doc: "d", run_first: "aa", run_last: "bb", approved: true });
  const adminAuth = await authBearer(env, "admin-opaque-token-value-999");
  const resp = await revertResolveEndpoint(revReq({ id: "r1", status: "done" }), env, adminAuth);
  assert.equal(resp.status, 200);
  assert.equal(core.listRevertRequests(REVERT_STATUS.DONE).length, 1);
  // editor scope cannot resolve
  const editorAuth = await authBearer(env, "john-opaque-token-value-123");
  assert.equal((await revertResolveEndpoint(revReq({ id: "r1", status: "failed" }), env, editorAuth)).status, 403);
});

test("endpoint: revert journal record and completion bind exact evidence", async () => {
  const { env, core } = envWithCore();
  core.fileRevertRequest({ id:"r1",editor:"slot:admin",doc:"data/public.json",
    run_first:"aa",run_last:"bb",approved:true });
  const adminAuth = await authBearer(env,"admin-opaque-token-value-999");
  const evidence = { id:"r1",batch_id:"revert-r1",actor:"slot:admin",
    source_ref:"data/public.json",original_text:"after",new_text:"before",
    base_sha:"base",commit_sha:"commit",generator_id:"generator-v1" };
  const record = await revertRecordEndpoint(revReq({ ...evidence,action:"record" },"POST",
    "https://worker.example.com/edit/v1/revert-record"),env,adminAuth);
  assert.equal(record.status,201);
  const mutation = core.sql.exec("SELECT original_hash,new_hash FROM canonical_mutations WHERE id='r1'").toArray()[0];
  assert.equal(mutation.original_hash.length,64);
  assert.equal(mutation.new_hash.length,64);
  const mismatch = await revertRecordEndpoint(revReq({ ...evidence,action:"complete",
    new_text:"tampered" },"POST","https://worker.example.com/edit/v1/revert-record"),env,adminAuth);
  assert.equal(mismatch.status,409);
  const complete = await revertRecordEndpoint(revReq({ ...evidence,action:"complete" },"POST",
    "https://worker.example.com/edit/v1/revert-record"),env,adminAuth);
  assert.equal(complete.status,201);
  assert.equal(core.listRevertRequests(REVERT_STATUS.DONE).length,1);
});
