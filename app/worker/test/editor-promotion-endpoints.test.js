import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import {
  promotionSaveEndpoint, promotionCandidateEndpoint, promotionDecisionEndpoint,
  promotionRetryEndpoint, promotionPauseEndpoint, promotionPreviewEndpoint,
  promotionCandidatesEndpoint,
} from "../src/editor-endpoints.js";

const scopes = (edit = false, admin = false) => ({
  edit: { granted: edit }, instructor: { granted: false }, admin: { granted: admin },
});
function auth(editor, edit = false, admin = false) { return { editor, scopes: scopes(edit, admin) }; }
function envWith(stub, environment = "production") { return {
  EDITOR: { getByName: () => stub }, EDIT_ORIGIN: "https://edit.example",
  EDIT_ENVIRONMENT: environment, EDIT_PROD_MANIFEST_EPOCH: "live:manifest-1",
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
    content: { new_text: "safe" }, manifest_epoch: "live:manifest-1",
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
    source_ref: "data/x#y", content: { new_text: "safe" }, manifest_epoch: "live:manifest-1" };
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
  const candidate = { id: "c1", principal: "slot:john", stage: "awaiting_approval",
    active_attempt_id: "c1:1", attempt: { id: "c1:1", base_sha: "base-1", evidence_hash: "ev-1", manifest_hash: "mf-1" },
    events: [{ actor: "secret", detail: { credential: "do-not-leak" } }], preview_html: "<p>candidate</p>" };
  const env = envWith({ getPromotionCandidate: async (id) => id === "c1" ? candidate : null });
  const bound = "id=c1&attempt_id=c1%3A1&base_sha=base-1&evidence_hash=ev-1&manifest_hash=mf-1";
  const own = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?" + bound), env, auth("slot:john", true));
  const admin = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?" + bound), env, auth("slot:admin", false, true));
  const denied = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?" + bound), env, auth("slot:roger", true));
  const unknown = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=missing"), env, auth("slot:roger", true));
  assert.equal(own.status, 200);
  assert.equal(admin.status, 200);
  assert.equal(denied.status, 404);
  assert.equal(await denied.text(), await unknown.text());
  const projected = await own.json();
  assert.equal(projected.candidate.principal, undefined);
  assert.equal(projected.candidate.events, undefined);
  assert.doesNotMatch(JSON.stringify(projected), /do-not-leak|secret/);
});

test("candidate binding becomes uniformly stale when attempt evidence manifest or base changes", async () => {
  const candidate = { id: "c1", principal: "slot:john", stage: "awaiting_approval", active_attempt_id: "c1:2",
    attempt: { id: "c1:2", base_sha: "base-2", evidence_hash: "ev-2", manifest_hash: "mf-2" } };
  const env = envWith({ getPromotionCandidate: async () => candidate });
  const stale = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=c1&attempt_id=c1%3A1&base_sha=base-1&evidence_hash=ev-1&manifest_hash=mf-1"), env, auth("slot:john", true));
  assert.equal(stale.status, 404);
});

test("preview uses the same exact binding and originator/admin authorization", async () => {
  const candidate = { id: "c1", principal: "slot:john", stage: "awaiting_approval", active_attempt_id: "c1:1",
    attempt: { id: "c1:1", base_sha: "b1", evidence_hash: "e1", manifest_hash: "m1" },
    preview_html: "<p>candidate only</p>" };
  const env = envWith({ getPromotionCandidate: async () => candidate });
  const url = "https://edit.example/edit/v1/prod/preview?id=c1&attempt_id=c1%3A1&base_sha=b1&evidence_hash=e1&manifest_hash=m1";
  assert.equal((await promotionPreviewEndpoint(new Request(url), env, auth("slot:john", true))).status, 200);
  assert.equal((await promotionPreviewEndpoint(new Request(url), env, auth("slot:admin", false, true))).status, 200);
  assert.equal((await promotionPreviewEndpoint(new Request(url), env, auth("slot:roger", true))).status, 404);
  assert.doesNotMatch(url, /token|authorization|cookie|secret/i);
});

test("automation discovery returns only role-permitted bound summaries", async () => {
  const rows = [
    { id:"own", principal:"slot:john" },
    { id:"other", principal:"slot:roger" },
  ];
  const full = (row) => ({ ...row, stage:"awaiting_approval", source_ref:"data/x#y",
    active_attempt_id:row.id+":1", attempt:{ id:row.id+":1", base_sha:"b", evidence_hash:"e", manifest_hash:"m" },
    events:[{ detail:{ secret:"hidden" } }] });
  const stub = {
    listPromotionCandidates: async (principal) => principal ? rows.filter((r) => r.principal === principal) : rows,
    getPromotionCandidate: async (id) => full(rows.find((r) => r.id === id)),
  };
  const editorResponse = await promotionCandidatesEndpoint(new Request("https://edit.example/edit/v1/prod/candidates"),
    envWith(stub), auth("slot:john", true));
  assert.deepEqual((await editorResponse.json()).candidates.map((c) => c.id), ["own"]);
  const adminResponse = await promotionCandidatesEndpoint(new Request("https://edit.example/edit/v1/prod/candidates"),
    envWith(stub), auth("slot:admin", false, true));
  const adminBody = await adminResponse.json();
  assert.deepEqual(adminBody.candidates.map((c) => c.id), ["own", "other"]);
  assert.doesNotMatch(JSON.stringify(adminBody), /hidden|secret|principal/);
  assert.match(adminBody.candidates[0].preview_href, /^\/edit\/v1\/prod\/preview\?/);
});

test("fresh saved candidates remain discoverable without granting preview or evidence access", async () => {
  const saved = { id:"fresh", principal:"slot:john", stage:"saved", source_ref:"data/x#y",
    active_attempt_id:"fresh:1", attempt:{ id:"fresh:1", base_sha:null, evidence_hash:null, manifest_hash:null } };
  const stub = { listPromotionCandidates: async () => [saved], getPromotionCandidate: async () => saved };
  const response = await promotionCandidatesEndpoint(new Request("https://edit.example/edit/v1/prod/candidates"),
    envWith(stub), auth("slot:john", true));
  const body = await response.json();
  assert.equal(body.candidates.length, 1);
  assert.deepEqual(body.candidates[0], {
    id:"fresh", stage:"saved", source_ref:"data/x#y", attempt_id:"fresh:1", preview_href:null,
  });
  const detail = await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?id=fresh"),
    envWith(stub), auth("slot:john", true));
  assert.equal(detail.status, 404);
});

test("real store-bound projection flows through discovery, detail, and preview endpoints", async () => {
  const core = makeCore(() => 1000);
  const made = core.createPromotionCandidate({ id:"real", principal:"slot:john", environment:"production",
    idempotency_key:"real-idem", request_digest:"real-digest", content_bytes:10, source_ref:"data/x#y" });
  const tuple = { candidate_id:"real", attempt_id:made.attempt.id, base_sha:"base", evidence_hash:"ev", manifest_hash:"man" };
  core.bindPromotionEvidence({ ...tuple, actor:"service:prod" });
  core.bindPromotionProjection({ ...tuple, preview_html:"<p>real candidate</p>",
    evidence:{ gates:[{ name:"tests", status:"pass" }] }, score:{ confidence:.94 },
    ai:{ disposition:"allow", reasons:["bounded"] } });
  const env = envWith(core);
  const discovery = await (await promotionCandidatesEndpoint(new Request("https://edit.example/edit/v1/prod/candidates"),
    env, auth("slot:john", true))).json();
  assert.equal(discovery.candidates.length, 1);
  const href = discovery.candidates[0].preview_href;
  const query = href.slice(href.indexOf("?") + 1);
  const detail = await (await promotionCandidateEndpoint(new Request("https://edit.example/edit/v1/prod/candidate?" + query),
    env, auth("slot:john", true))).json();
  assert.equal(detail.candidate.preview_html, "<p>real candidate</p>");
  assert.equal(detail.candidate.evidence.gates[0].status, "pass");
  const preview = await promotionPreviewEndpoint(new Request("https://edit.example" + href), env, auth("slot:john", true));
  assert.match(await preview.text(), /real candidate/);
});

test("every PROD mutation rejects missing stale candidate and restoring manifest epochs", async () => {
  let calls = 0;
  const stub = {
    createPromotionCandidate: async () => (calls++, { ok: true }),
    decidePromotion: async () => (calls++, { ok: true }),
    retryPromotion: async () => (calls++, { ok: true }),
    setPromotionLane: async () => (calls++, { ok: true }),
  };
  const env = envWith(stub);
  const cases = [
    [promotionSaveEndpoint, { candidate_id:"c", idempotency_key:"i", source_ref:"x", content:"x" }],
    [promotionDecisionEndpoint, { candidate_id:"c" }],
    [promotionRetryEndpoint, { candidate_id:"c" }],
    [promotionPauseEndpoint, { paused:true }],
  ];
  for (const [fn, body] of cases) {
    for (const epoch of [undefined, "live:stale", "candidate:manifest-1", "restoring:manifest-1"]) {
      const response = await fn(post("https://edit.example/edit/v1/prod/mutation", { ...body, ...(epoch ? { manifest_epoch: epoch } : {}) }), env, auth("slot:admin", true, true));
      assert.equal(response.status, 409);
      assert.equal((await response.json()).error.code, "stale_manifest_epoch");
    }
  }
  assert.equal(calls, 0);
});

test("admin decision binds server digest and exact attempt evidence tuple; AI identity cannot mutate", async () => {
  let received;
  const env = envWith({ decidePromotion: async (input) => (received = input, { ok: true, receipt: { id: "r1" } }) });
  const body = { candidate_id:"c1", attempt_id:"c1:1", decision:"approve", base_sha:"b1",
    evidence_hash:"e1", manifest_hash:"m1", idempotency_key:"idem", manifest_epoch:"live:manifest-1" };
  const ok = await promotionDecisionEndpoint(post("https://edit.example/edit/v1/prod/decision", body), env, auth("slot:admin", false, true));
  assert.equal(ok.status, 200);
  assert.match(received.request_digest, /^[0-9a-f]{64}$/);
  assert.equal(received.principal, "slot:admin");
  const ai = await promotionDecisionEndpoint(post("https://edit.example/edit/v1/prod/decision", body), env,
    { editor: null, service: "ai-review", scopes: scopes(false, false) });
  assert.equal(ai.status, 404);
});
