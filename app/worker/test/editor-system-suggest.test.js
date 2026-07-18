// editor-system-suggest.test.js — POST /edit/v1/system-suggest, the admin-scoped
// SYSTEM proposer endpoint (value-sync companions + ai_rewrite). Unlike the human
// /suggest endpoint (edit/instructor scope, origin hardcoded to "human"), this
// endpoint requires ADMIN scope (reached only via the Bearer service token) and
// accepts ONLY system origins {companion, ai_rewrite}. Everything the store
// records is resolved SERVER-side from the map — the client body is never trusted.
import { test } from "node:test";
import assert from "node:assert/strict";
import { systemSuggestEndpoint } from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";
import { EDITOR_MAP } from "../src/editor-map.js";

const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1, instructor: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};

// A real, editable, non-formatted prose block from the bundled map (server-truth).
function pickProseRef() {
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) {
      if (b.kind === "prose" && !b.has_inline_formatting) return b;
    }
  }
  throw new Error("no editable prose block in the map fixture");
}
const PROSE = pickProseRef();

// A capturing EDITOR DO stub: records the resolved input the endpoint passes to
// stub.suggest() and returns a stored-row shape.
function envWith(captured) {
  return {
    ...ENV_BASE,
    EDITOR: {
      getByName() {
        return {
          async suggest(input) {
            captured.input = input;
            return { ok: true, suggestion: { status: "pending" } };
          },
        };
      },
    },
  };
}

function req(body, headers = {}) {
  return new Request("https://worker.example.com/edit/v1/system-suggest", {
    method: "POST",
    headers: { "X-Edit-Request": "1", "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

const goodBody = (over = {}) => ({
  id: "companion-sys-0001",
  source_ref: PROSE.source_ref,
  origin: "companion",
  new_text: "A clean paraphrase of the block for syndication.",
  group_id: "vs-batch-abc",
  ...over,
});

async function authFor(env, token) {
  return resolveAuth(env, new Request("https://worker.example.com/edit/v1/system-suggest", {
    headers: { Authorization: `Bearer ${token}` },
  }));
}

test("system-suggest requires admin scope — an edit/instructor token is 403", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "john-opaque-token-value-123"); // edit+instructor, NOT admin
  const resp = await systemSuggestEndpoint(req(goodBody()), env, auth);
  assert.equal(resp.status, 403);
  const j = await resp.json();
  assert.equal(j.error.code, "forbidden");
  assert.equal(captured.input, undefined); // never reached the store
});

test("system-suggest rejects origin:human (that is the human /suggest endpoint)", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const resp = await systemSuggestEndpoint(req(goodBody({ origin: "human" })), env, auth);
  assert.equal(resp.status, 400);
  const j = await resp.json();
  assert.equal(j.error.code, "validation_error");
  assert.equal(captured.input, undefined);
});

test("system-suggest rejects an unknown origin", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const resp = await systemSuggestEndpoint(req(goodBody({ origin: "bogus" })), env, auth);
  assert.equal(resp.status, 400);
  assert.equal((await resp.json()).error.code, "validation_error");
});

test("system-suggest accepts origin:companion with a valid source_ref (admin)", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const resp = await systemSuggestEndpoint(req(goodBody()), env, auth);
  assert.equal(resp.status, 200);
  const j = await resp.json();
  assert.equal(j.ok, true);
  assert.equal(j.status, "pending");
  // Server-resolved provenance: origin honored, but editor/original_text/hash come
  // from the server (the map + service slot), never the client body.
  assert.equal(captured.input.origin, "companion");
  assert.equal(captured.input.editor, "slot:admin");
  assert.equal(captured.input.group_id, "vs-batch-abc");
  assert.equal(captured.input.source_ref, PROSE.source_ref);
  assert.equal(captured.input.original_text, PROSE.original_text);
  assert.equal(captured.input.original_hash, PROSE.original_hash);
});

test("system-suggest accepts origin:ai_rewrite", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const resp = await systemSuggestEndpoint(req(goodBody({ origin: "ai_rewrite" })), env, auth);
  assert.equal(resp.status, 200);
  assert.equal(captured.input.origin, "ai_rewrite");
});

test("system-suggest rejects an unknown source_ref (SSRF/forgery) with validation_error", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const resp = await systemSuggestEndpoint(
    req(goodBody({ source_ref: "data/matters/m99-fake/exercise/exercise.json#not.a.real.path" })),
    env, auth);
  assert.equal(resp.status, 400);
  assert.equal((await resp.json()).error.code, "validation_error");
  assert.equal(captured.input, undefined);
});

test("system-suggest enforces the CSRF header", async () => {
  const captured = {};
  const env = envWith(captured);
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const noCsrf = new Request("https://worker.example.com/edit/v1/system-suggest", {
    method: "POST",
    headers: { "content-type": "application/json" }, // no X-Edit-Request
    body: JSON.stringify(goodBody()),
  });
  const resp = await systemSuggestEndpoint(noCsrf, env, auth);
  assert.equal(resp.status, 403);
  assert.equal((await resp.json()).error.code, "csrf_failed");
});
