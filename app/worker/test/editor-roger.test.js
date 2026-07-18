// editor-roger.test.js — Roger Haydock as a second editor (WP2).
//
// Proves: (1) EDIT_TOKEN_ROGER mints an EDIT (+ instructor) scope and the
// server-controlled identity "slot:roger" — never admin; (2) the attribution
// label derived from that identity is "RSH" (mirrors John -> "JOS"); (3) a
// suggestion submitted with Roger's token is stamped editor "slot:roger"
// server-side; (4) the admin review surface carries "RSH" on Roger's rows and
// "JOS" on John's. Roger's opaque token value is a deploy SECRET — this test uses
// a throwaway fixture value, never the real token.
import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveAuth, attributionLabel } from "../src/editor-auth.js";
import { suggestEndpoint, reviewJsonEndpoint } from "../src/editor-endpoints.js";
import { EDITOR_MAP } from "../src/editor-map.js";

// Mirrors the shipped EDIT_TOKEN_SCOPES: roger gets edit+instructor (same as
// John), admin stays isolated behind its own token.
const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({
    john: { edit: 1, instructor: 1 },
    roger: { edit: 1, instructor: 1 },
    admin: { admin: 1 },
  }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ROGER: "roger-opaque-token-value-abc",
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

async function authFor(env, token) {
  return resolveAuth(env, new Request("https://worker.example.com/edit/v1/suggest", {
    headers: { Authorization: `Bearer ${token}` },
  }));
}

// ---- (1) Roger's token mints an EDIT scope + server identity ----------------
test("EDIT_TOKEN_ROGER mints edit+instructor scope, identity slot:roger, NOT admin", async () => {
  const auth = await authFor(ENV_BASE, "roger-opaque-token-value-abc");
  assert.equal(auth.editor, "slot:roger");   // server-controlled identity
  assert.equal(auth.slot, "roger");
  assert.equal(auth.scopes.edit.granted, true);
  assert.equal(auth.scopes.instructor.granted, true);
  assert.equal(auth.scopes.admin.granted, false); // admin never reachable from roger
});

// ---- (2) attribution label derivation ---------------------------------------
test("attributionLabel derives RSH for Roger and JOS for John", () => {
  assert.equal(attributionLabel("slot:roger"), "RSH");
  assert.equal(attributionLabel("slot:john"), "JOS");
  assert.equal(attributionLabel("roger"), "RSH");        // bare slot form
  assert.equal(attributionLabel("slot:newperson"), "NEWPERSON"); // safe fallback
  assert.equal(attributionLabel(""), "");
  assert.equal(attributionLabel(null), "");
});

// ---- (3) a Roger suggestion is stamped editor "slot:roger" server-side ------
test("suggest with Roger's token stores editor slot:roger (server-resolved)", async () => {
  const captured = {};
  const env = {
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
  const auth = await authFor(env, "roger-opaque-token-value-abc");
  const request = new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: { "X-Edit-Request": "1", "content-type": "application/json" },
    body: JSON.stringify({
      id: "roger-suggestion-0001",
      source_ref: PROSE.source_ref,
      new_text: "A crisper phrasing of the block, suggested by Roger.",
    }),
  });
  const resp = await suggestEndpoint(request, env, auth);
  assert.equal(resp.status, 200);
  assert.equal((await resp.json()).status, "pending");
  assert.equal(captured.input.editor, "slot:roger");     // NOT from the client body
  assert.equal(captured.input.origin, "human");
  assert.equal(attributionLabel(captured.input.editor), "RSH");
});

// ---- (4) the admin review surface carries RSH (Roger) + JOS (John) ----------
test("review endpoint stamps attribution RSH on Roger rows and JOS on John rows", async () => {
  const rows = [
    { id: "a1", editor: "slot:roger", source_ref: PROSE.source_ref, status: "pending", kind: "prose", origin: "human" },
    { id: "b2", editor: "slot:john", source_ref: PROSE.source_ref, status: "pending", kind: "prose", origin: "human" },
  ];
  const env = {
    ...ENV_BASE,
    EDITOR: { getByName() { return { async listAll() { return rows; } }; } },
  };
  const auth = await authFor(env, "admin-opaque-token-value-999");
  const resp = await reviewJsonEndpoint(
    new Request("https://worker.example.com/edit/v1/review"), env, auth);
  assert.equal(resp.status, 200);
  const j = await resp.json();
  const byId = Object.fromEntries(j.items.map((it) => [it.id, it]));
  assert.equal(byId.a1.attribution, "RSH"); // Roger
  assert.equal(byId.b2.attribution, "JOS"); // John
  // The underlying slot identity is preserved (attribution is additive).
  assert.equal(byId.a1.editor, "slot:roger");
});
