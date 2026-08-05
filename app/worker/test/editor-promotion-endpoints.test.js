import { test } from "node:test";
import assert from "node:assert/strict";
import {
  promotionSaveEndpoint, promotionCandidateEndpoint, promotionDecisionEndpoint,
} from "../src/editor-endpoints.js";

const scopes = (edit = false, admin = false) => ({
  edit: { granted: edit }, instructor: { granted: false }, admin: { granted: admin },
});
function auth(editor, edit = false, admin = false) { return { editor, scopes: scopes(edit, admin) }; }
function envWith(stub, environment = "production") { return {
  EDITOR: { getByName: () => stub }, EDIT_ORIGIN: "https://edit.example",
  EDIT_ENVIRONMENT: environment,
}; }
function post(url, body, headers = {}) {
  return new Request(url, { method: "POST", headers: {
    "Content-Type": "application/json", "X-Edit-Request": "1",
    Origin: "https://edit.example", "Sec-Fetch-Site": "same-origin", ...headers,
  }, body: JSON.stringify(body) });
}

test("promotion save binds authenticated principal and server-computed digest", async () => {
  let received;
  const stub = { createPromotionCandidate: async (input) => (received = input, { ok: true, candidate: { id: input.id } }) };
  const response = await promotionSaveEndpoint(post("https://edit.example/edit/v1/prod/candidates", {
    candidate_id: "candidate-123", idempotency_key: "idem-123", source_ref: "data/x#y",
    content: { new_text: "safe" },
  }), envWith(stub), auth("slot:john", true));
  assert.equal(response.status, 201);
  assert.equal(received.principal, "slot:john");
  assert.match(received.request_digest, /^[0-9a-f]{64}$/);
  assert.equal(received.environment, "production");
});

test("PROD promotion API fails closed outside the production Worker environment", async () => {
  let called = false;
  const stub = { createPromotionCandidate: async () => { called = true; return { ok: true }; } };
  const body = { candidate_id: "candidate-123", idempotency_key: "idem-123",
    source_ref: "data/x#y", content: { new_text: "safe" } };
  for (const identity of ["dev", "", "PRODUCTION"]) {
    const response = await promotionSaveEndpoint(post("https://edit.example/edit/v1/prod/candidates", body),
      envWith(stub, identity), auth("slot:john", true));
    assert.equal(response.status, 404);
  }
  const missing = envWith(stub);
  delete missing.EDIT_ENVIRONMENT;
  assert.equal((await promotionSaveEndpoint(post("https://edit.example/edit/v1/prod/candidates", body),
    missing, auth("slot:john", true))).status, 404);
  assert.equal(called, false);
});

test("promotion mutations reject missing CSRF and unsafe content type", async () => {
  const env = envWith({ decidePromotion: async () => ({ ok: true }) });
  const noCsrf = await promotionDecisionEndpoint(new Request("https://edit.example/edit/v1/prod/decision", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }), env, auth("slot:admin", false, true));
  assert.equal(noCsrf.status, 403);
  const form = await promotionDecisionEndpoint(post("https://edit.example/edit/v1/prod/decision", {}, {
    "Content-Type": "text/plain",
  }), env, auth("slot:admin", false, true));
  assert.equal(form.status, 403);
});

test("candidate read permits originator/admin and gives other editor uniform denial", async () => {
  const candidate = { id: "c1", principal: "slot:john", stage: "saved" };
  const env = envWith({ getPromotionCandidate: async (id) => id === "c1" ? candidate : null });
  const own = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=c1"), env, auth("slot:john", true));
  const admin = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=c1"), env, auth("slot:admin", false, true));
  const denied = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=c1"), env, auth("slot:roger", true));
  const unknown = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=missing"), env, auth("slot:roger", true));
  assert.equal(own.status, 200);
  assert.equal(admin.status, 200);
  assert.equal(denied.status, 404);
  assert.equal(await denied.text(), await unknown.text());
});
