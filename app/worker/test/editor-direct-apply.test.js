// editor-direct-apply.test.js — the /edit endpoint wiring for canonical
// direct-apply mode (SL1/SL2/SL6): the DIRECT_APPLY flag reaches the store, the
// id_conflict guard surfaces as a 409, GET /pending carries the heartbeat +
// direct_apply liveness signals, and POST /edit/v1/heartbeat is admin-only.
import { test } from "node:test";
import assert from "node:assert/strict";
import { suggestEndpoint, pendingEndpoint, heartbeatEndpoint } from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";
import { EDITOR_MAP } from "../src/editor-map.js";

const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1, instructor: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};

function pickProseRef() {
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) if (b.kind === "prose" && !b.has_inline_formatting) return b;
  }
  throw new Error("no editable prose block in the map fixture");
}
const PROSE = pickProseRef();

// A capturing/stubbed EDITOR DO: records suggest() opts, serves heartbeat age.
function envWith(over = {}, cap = {}) {
  return {
    ...ENV_BASE,
    ...over,
    EDITOR: {
      getByName() {
        return {
          async suggest(input, ceilings, opts) {
            cap.opts = opts;
            cap.input = input;
            return { ok: true, suggestion: { status: opts && opts.directApply ? "accepted" : "pending" } };
          },
          async listForPage() { return []; },
          async listForEditor() { return []; },
          async heartbeatAgeS() { return cap.age === undefined ? null : cap.age; },
          async recordHeartbeat(beat) { cap.beat = beat; return { ok: true, received_at: 111 }; },
        };
      },
    },
  };
}

async function authBearer(env, token) {
  return resolveAuth(env, new Request("https://worker.example.com/x", {
    headers: { Authorization: `Bearer ${token}` },
  }));
}
async function authCookie(env, token) {
  // mint a cookie by exercising resolveAuth with the Bearer (edit scope) — simpler
  // to just use the Bearer path; suggest/pending only read auth.scopes + editor.
  return authBearer(env, token);
}

function suggestReq(body) {
  return new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: { "X-Edit-Request": "1", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
const goodBody = (over = {}) => ({
  id: "human-edit-000001",
  source_ref: PROSE.source_ref,
  new_text: "A clean human edit of the block.",
  ...over,
});

test("DIRECT_APPLY=true forwards { directApply:true } to the store and returns accepted", async () => {
  const cap = {};
  const env = envWith({ DIRECT_APPLY: "true" }, cap);
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await suggestEndpoint(suggestReq(goodBody()), env, auth);
  assert.equal(resp.status, 200);
  const j = await resp.json();
  assert.equal(j.status, "accepted");
  assert.equal(cap.opts.directApply, true);
});

test("DIRECT_APPLY unset forwards { directApply:false } (classic suggestion mode)", async () => {
  const cap = {};
  const env = envWith({}, cap); // no DIRECT_APPLY
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await suggestEndpoint(suggestReq(goodBody()), env, auth);
  const j = await resp.json();
  assert.equal(j.status, "pending");
  assert.equal(cap.opts.directApply, false);
});

test("a shared json_scalar suggestion stores one server-authoritative json_path", async () => {
  const sharedRef = Object.keys(EDITOR_MAP.occurrences).find((ref) => {
    if (EDITOR_MAP.occurrences[ref].length < 2) return false;
    return Object.values(EDITOR_MAP.pages).some((blocks) =>
      blocks.some((b) => b.source_ref === ref && b.kind === "json_scalar"));
  });
  const block = Object.values(EDITOR_MAP.pages).flat()
    .find((b) => b.source_ref === sharedRef);
  assert.ok(block, "fixture must contain a shared json_scalar block");

  const cap = {};
  const env = envWith({}, cap);
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await suggestEndpoint(suggestReq({
    id: "shared-json-0001",
    source_ref: sharedRef,
    json_path: block.json_path,
    new_text: "Shared replacement text.",
  }), env, auth);

  assert.equal(resp.status, 200);
  assert.equal(cap.input.source_ref, sharedRef);
  assert.equal(cap.input.json_path, block.json_path);
  assert.equal(Array.isArray(cap.input.json_path), false);

  const forgedCap = {};
  const forgedEnv = envWith({}, forgedCap);
  const forgedAuth = await authBearer(forgedEnv, "john-opaque-token-value-123");
  const forgedResp = await suggestEndpoint(suggestReq({
    id: "shared-json-0002",
    source_ref: sharedRef,
    json_path: "attacker.controlled.path",
    new_text: "Forged replacement text.",
  }), forgedEnv, forgedAuth);
  assert.equal(forgedResp.status, 400);
  assert.equal((await forgedResp.json()).error.code, "validation_error");
  assert.equal(forgedCap.input, undefined, "forged path must never reach storage");
});

test("id_conflict from the store surfaces as a 409 (client must rotate its id, no silent loss)", async () => {
  const cap = {};
  const env = {
    ...ENV_BASE, DIRECT_APPLY: "true",
    EDITOR: { getByName() { return { async suggest() { return { ok: false, reason: "id_conflict" }; } }; } },
  };
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await suggestEndpoint(suggestReq(goodBody()), env, auth);
  assert.equal(resp.status, 409);
  assert.equal((await resp.json()).error.code, "id_conflict");
});

test("GET /pending carries heartbeat_age_s + direct_apply liveness signals", async () => {
  const cap = { age: 123 };
  const env = envWith({ DIRECT_APPLY: "true" }, cap);
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await pendingEndpoint(
    new Request("https://worker.example.com/edit/v1/pending?page=matters/m01/index.html"), env, auth);
  const j = await resp.json();
  assert.equal(j.ok, true);
  assert.equal(j.heartbeat_age_s, 123);
  assert.equal(j.direct_apply, true);
  assert.ok(Array.isArray(j.items));
});

test("GET /pending reports null heartbeat_age_s when the daemon has never checked in", async () => {
  const cap = {}; // age undefined -> null
  const env = envWith({ DIRECT_APPLY: "true" }, cap);
  const auth = await authBearer(env, "john-opaque-token-value-123");
  const resp = await pendingEndpoint(
    new Request("https://worker.example.com/edit/v1/pending?page=matters/m01/index.html"), env, auth);
  const j = await resp.json();
  assert.equal(j.heartbeat_age_s, null);
});

test("POST /heartbeat requires admin scope — an edit token is 403", async () => {
  const cap = {};
  const env = envWith({}, cap);
  const auth = await authBearer(env, "john-opaque-token-value-123"); // edit, NOT admin
  const req = new Request("https://worker.example.com/edit/v1/heartbeat", {
    method: "POST", headers: { "X-Edit-Request": "1", "content-type": "application/json" },
    body: JSON.stringify({ ok: true, applied: 2, ts: 5 }),
  });
  const resp = await heartbeatEndpoint(req, env, auth);
  assert.equal(resp.status, 403);
  assert.equal(cap.beat, undefined);
});

test("POST /heartbeat (admin) records the beacon and normalizes the body", async () => {
  const cap = {};
  const env = envWith({}, cap);
  const auth = await authBearer(env, "admin-opaque-token-value-999");
  const req = new Request("https://worker.example.com/edit/v1/heartbeat", {
    method: "POST", headers: { "X-Edit-Request": "1", "content-type": "application/json" },
    body: JSON.stringify({ ok: true, applied: 4, ts: 1700 }),
  });
  const resp = await heartbeatEndpoint(req, env, auth);
  assert.equal(resp.status, 200);
  assert.equal((await resp.json()).ok, true);
  assert.deepEqual(cap.beat, { ok: true, applied: 4, ts: 1700 });
});

test("POST /heartbeat enforces the CSRF header", async () => {
  const cap = {};
  const env = envWith({}, cap);
  const auth = await authBearer(env, "admin-opaque-token-value-999");
  const req = new Request("https://worker.example.com/edit/v1/heartbeat", {
    method: "POST", headers: { "content-type": "application/json" }, // no X-Edit-Request
    body: JSON.stringify({ ok: true }),
  });
  const resp = await heartbeatEndpoint(req, env, auth);
  assert.equal(resp.status, 403);
  assert.equal((await resp.json()).error.code, "csrf_failed");
});
