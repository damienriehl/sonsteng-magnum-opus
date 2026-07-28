// editor-facts.test.js — U5's worker surface: adding a NEW fact (json_add)
// through /suggest, validated against the map's facts index; and the
// system-suggest endpoint accepting structural ops (drafted mentions of new
// facts arrive as insert_after rows in an ai_rewrite group).
import { test } from "node:test";
import assert from "node:assert/strict";
import { suggestEndpoint, systemSuggestEndpoint } from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";
import { EDITOR_MAP } from "../src/editor-map.js";

const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};
const SLUG = "m03-tort-meridian";

function proseBlock() {
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) {
      if (b.kind === "prose" && b.source_ref.split("#")[0].endsWith(".md")) return b;
    }
  }
  throw new Error("no prose block");
}

function envWith(cap = {}) {
  return {
    ...ENV_BASE,
    EDITOR: {
      getByName() {
        return {
          async suggest(input, _c, opts) {
            cap.input = input; cap.opts = opts;
            return { ok: true, suggestion: { status: "pending" } };
          },
        };
      },
    },
  };
}

let n = 0;
async function post(env, endpoint, body, token) {
  const auth = await resolveAuth(env, new Request("https://worker.example.com/x", {
    headers: { Authorization: `Bearer ${token}` },
  }));
  const req = new Request("https://worker.example.com/edit/v1/x", {
    method: "POST",
    headers: { "content-type": "application/json", "x-edit-request": "1",
               origin: "https://worker.example.com" },
    body: JSON.stringify({ id: `facts-${++n}-abcdef`, ...body }),
  });
  return endpoint(req, env, auth);
}

test("json_add: a valid new fact files against the matter's facts index", async () => {
  const cap = {};
  const res = await post(envWith(cap), suggestEndpoint, {
    op: "json_add", matter: SLUG, fact_key: "deadline-note",
    new_text: "The response is due within 21 days.",
  }, ENV_BASE.EDIT_TOKEN_JOHN);
  assert.equal(res.status, 200);
  assert.equal(cap.input.kind, "json_add");
  assert.equal(cap.input.source_ref,
    `data/matters/${SLUG}/matter.json#custom_facts.deadline-note`);
  assert.equal(cap.input.json_path, "custom_facts.deadline-note");
  assert.equal(cap.input.new_text, "The response is due within 21 days.");
});

test("json_add rejects unknown matters, bad keys, and empty values", async () => {
  for (const body of [
    { op: "json_add", matter: "m99-nope", fact_key: "k", new_text: "v vv vv" },
    { op: "json_add", matter: SLUG, fact_key: "Bad Key!", new_text: "v vv vv" },
    { op: "json_add", matter: SLUG, fact_key: "ok-key", new_text: "   " },
    { op: "json_add", matter: SLUG, fact_key: "ok-key",
      new_text: "sneaky {#b:00000000}" },
  ]) {
    const res = await post(envWith(), suggestEndpoint, body, ENV_BASE.EDIT_TOKEN_JOHN);
    assert.equal(res.status, 400, JSON.stringify(body));
  }
});

test("system-suggest accepts a structural insert_after for drafted mentions", async () => {
  const cap = {};
  const anchor = proseBlock();
  const res = await post(envWith(cap), systemSuggestEndpoint, {
    origin: "ai_rewrite", group_id: "scoped-fact-mentions-1",
    source_ref: anchor.source_ref, op: "insert_after",
    new_text: "A drafted mention of the new fact.",
  }, ENV_BASE.EDIT_TOKEN_ADMIN);
  assert.equal(res.status, 200);
  assert.equal(cap.input.kind, "insert_after");
  assert.equal(cap.input.origin, "ai_rewrite");
  assert.equal(cap.input.group_id, "scoped-fact-mentions-1");
  // structural + system origin => queued for the admin gate, never fast-pathed
  assert.equal(cap.opts, undefined);
});

test("system-suggest still rejects structural ops on non-prose anchors", async () => {
  let scalar = null;
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) if (b.kind === "json_scalar") { scalar = b; break; }
    if (scalar) break;
  }
  const res = await post(envWith(), systemSuggestEndpoint, {
    origin: "ai_rewrite", source_ref: scalar.source_ref,
    op: "insert_after", new_text: "Nope.",
  }, ENV_BASE.EDIT_TOKEN_ADMIN);
  assert.equal(res.status, 400);
});
