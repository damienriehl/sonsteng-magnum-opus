// editor-scoped-endpoints.test.js — /edit/v1/scoped-request + the drafter's
// admin surface (U7). The ceiling (KTD5, settled at 100 by Damien 2026-07-28)
// refuses to file an over-radius request without explicit confirmation; the
// radius is server-enumerated, never client-supplied.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  scopedRequestEndpoint, scopedRequestsEndpoint, scopedClaimEndpoint,
  scopedResolveEndpoint, groupStatusEndpoint,
} from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";
import { enumerateScope } from "../src/editor-map.js";
import { mintScopedConfirmationToken } from "../src/scoped-confirmation.js";

const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};
const SLUG = "m03-tort-meridian";

function envWith(cap = {}) {
  return {
    ...ENV_BASE,
    EDITOR: {
      getByName() {
        return {
          async fileScopedRequest(input) {
            cap.filed = input;
            return { ok: true, request: { ...input, status: "requested",
              phase: input.level === "module" || input.level === "course" ? "canary" : "all" } };
          },
          async listScopedRequests(status) { cap.listed = status; return []; },
          async claimScopedRequest(id) { cap.claimed = id; return { ok: true, id }; },
          async resolveScopedRequest(id, patch) {
            cap.resolved = { id, ...patch }; return { ok: true, id, status: patch.status };
          },
          async groupOutcome(gid) {
            cap.group = gid;
            return { group_id: gid, total: 2, by_status: { applied: 2 } };
          },
        };
      },
    },
  };
}

async function auth(env, token) {
  return resolveAuth(env, new Request("https://worker.example.com/x", {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  }));
}

let n = 0;
async function postReq(env, body, token = ENV_BASE.EDIT_TOKEN_JOHN) {
  const req = new Request("https://worker.example.com/edit/v1/scoped-request", {
    method: "POST",
    headers: { "content-type": "application/json", "x-edit-request": "1",
               origin: "https://worker.example.com" },
    body: JSON.stringify({ id: `scoped-req-${++n}-abcd`, ...body }),
  });
  return scopedRequestEndpoint(req, env, await auth(env, token));
}

test("a small-radius request files with the server-enumerated radius", async () => {
  const cap = {};
  const res = await postReq(envWith(cap), {
    level: "part", matter: SLUG, part: "exercise",
    instruction: "Throughout this exercise, replace 'plaintiff' with 'claimant'.",
  });
  assert.equal(res.status, 200);
  const expect = enumerateScope({ level: "part", matter: SLUG, part: "exercise" });
  assert.equal(cap.filed.radius_blocks, expect.blocks);
  assert.equal(cap.filed.editor, "slot:john");
  const data = await res.json();
  assert.equal(data.ok, true);
  assert.equal(data.radius.blocks, expect.blocks);
});

test("over-ceiling challenge carries a token and matching confirmed retry files", async () => {
  const cap = {};
  const refuse = await postReq(envWith(cap), {
    level: "course",
    instruction: "Modernize the tone throughout the whole course.",
  });
  assert.equal(refuse.status, 409);
  const rdata = await refuse.json();
  assert.equal(rdata.error.code, "ceiling_confirmation_required");
  assert.ok(rdata.radius.blocks > 100);
  assert.equal(typeof rdata.confirmation_token, "string");
  assert.ok(rdata.confirmation_token.length > 40);
  assert.equal(cap.filed, undefined, "nothing may be filed on refusal");

  const okRes = await postReq(envWith(cap), {
    level: "course",
    instruction: "Modernize the tone throughout the whole course.",
    confirmed: true,
    confirmation_token: rdata.confirmation_token,
  });
  assert.equal(okRes.status, 200);
  assert.equal(cap.filed.confirmed, true);
});

test("part-scope confirmation cannot be laundered into a course-wide request", async () => {
  const cap = {};
  const challenged = await postReq(envWith(cap), {
    level: "part", matter: SLUG, part: "case-file",
    instruction: "Update this case file.",
  });
  assert.equal(challenged.status, 409);
  const challenge = await challenged.json();

  const laundered = await postReq(envWith(cap), {
    level: "course",
    instruction: "Delete every paragraph course-wide",
    confirmed: true,
    confirmation_token: challenge.confirmation_token,
  });
  assert.equal(laundered.status, 409);
  assert.equal(cap.filed, undefined, "a changed scope must not be filed");
});

test("changing the wording invalidates an otherwise matching confirmation", async () => {
  const cap = {};
  const challenged = await postReq(envWith(cap), {
    level: "course", instruction: "Modernize the tone throughout the whole course.",
  });
  const challenge = await challenged.json();
  const changed = await postReq(envWith(cap), {
    level: "course", instruction: "Delete every paragraph course-wide",
    confirmed: true, confirmation_token: challenge.confirmation_token,
  });
  assert.equal(changed.status, 409);
  assert.equal(cap.filed, undefined);
});

test("an expired confirmation token is refused and freshly challenged", async () => {
  const cap = {};
  const radius = enumerateScope({ level: "course" });
  const instruction = "Modernize the tone throughout the whole course.";
  const expired = await mintScopedConfirmationToken(ENV_BASE.SESSION_SIGNING_KEY, {
    level: "course", matter: null, part: null, module: null, instruction, radius,
  }, { expiresAt: Date.now() - 1 });
  const refused = await postReq(envWith(cap), {
    level: "course", instruction, confirmed: true, confirmation_token: expired,
  });
  assert.equal(refused.status, 409);
  const challenge = await refused.json();
  assert.equal(typeof challenge.confirmation_token, "string");
  assert.notEqual(challenge.confirmation_token, expired);
  assert.equal(cap.filed, undefined);
});

test("a bare confirmed boolean is refused above the ceiling", async () => {
  const cap = {};
  const refused = await postReq(envWith(cap), {
    level: "course", instruction: "Modernize the entire course.", confirmed: true,
  });
  assert.equal(refused.status, 409);
  assert.equal(cap.filed, undefined);
});

test("invalid scope, empty instruction, and reserved bytes are rejected", async () => {
  assert.equal((await postReq(envWith(), {
    level: "matter", matter: "m99-nope", instruction: "x y z toolong enough" })).status, 400);
  assert.equal((await postReq(envWith(), {
    level: "matter", matter: SLUG, instruction: "  " })).status, 400);
  assert.equal((await postReq(envWith(), {
    level: "matter", matter: SLUG,
    instruction: "carry a {#b:00000000} marker" })).status, 400);
});

test("scoped-request needs an editor scope; drafter surface needs admin", async () => {
  const env = envWith();
  assert.equal((await postReq(env, {
    level: "matter", matter: SLUG, instruction: "A fine instruction." },
    ENV_BASE.EDIT_TOKEN_ADMIN)).status, 403);

  const admin = await auth(env, ENV_BASE.EDIT_TOKEN_ADMIN);
  const john = await auth(env, ENV_BASE.EDIT_TOKEN_JOHN);
  const listReq = new Request("https://worker.example.com/edit/v1/scoped-requests?status=requested");
  assert.equal((await scopedRequestsEndpoint(listReq, env, john)).status, 403);
  assert.equal((await scopedRequestsEndpoint(listReq, env, admin)).status, 200);

  const post = (path, body) => new Request("https://worker.example.com" + path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-edit-request": "1",
               origin: "https://worker.example.com" },
    body: JSON.stringify(body),
  });
  assert.equal((await scopedClaimEndpoint(post("/edit/v1/scoped-claim",
    { id: "r1" }), env, admin)).status, 200);
  assert.equal((await scopedResolveEndpoint(post("/edit/v1/scoped-resolve",
    { id: "r1", status: "drafted", group_id: "g1" }), env, admin)).status, 200);
  const gs = new Request("https://worker.example.com/edit/v1/group-status?group_id=g1");
  const gr = await groupStatusEndpoint(gs, env, admin);
  assert.equal(gr.status, 200);
  assert.equal((await gr.json()).outcome.total, 2);
});
