// CORS allowlist matcher, preflight, and withCors wrapping (incl. error responses).
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseAllowedOrigins, matchOrigin, handlePreflight, withCors, corsHeaders } from "../src/cors.js";

const ALLOWED = parseAllowedOrigins("https://sonsteng-dev.damienriehl.com,https://sonsteng.damienriehl.com");

function req(origin, method = "POST") {
  const headers = origin ? { Origin: origin } : {};
  return new Request("https://worker.example/v1/chat", { method, headers });
}

test("allowlist matches exactly and echoes the matched origin", () => {
  const good = "https://sonsteng-dev.damienriehl.com";
  assert.equal(matchOrigin(req(good), ALLOWED), good);
  assert.equal(corsHeaders(good)["Access-Control-Allow-Origin"], good);
  assert.equal(corsHeaders(good)["Vary"], "Origin");
});

test("non-allowlisted or missing origin does not match", () => {
  assert.equal(matchOrigin(req("https://evil.example"), ALLOWED), null);
  assert.equal(matchOrigin(req(null), ALLOWED), null);
});

test("OPTIONS preflight returns 204 + CORS for allowed, 403 bare for others", () => {
  const ok = handlePreflight(req("https://sonsteng.damienriehl.com", "OPTIONS"), ALLOWED);
  assert.equal(ok.status, 204);
  assert.equal(ok.headers.get("Access-Control-Allow-Origin"), "https://sonsteng.damienriehl.com");
  assert.ok(ok.headers.get("Access-Control-Allow-Headers").includes("x-session-token"));

  const bad = handlePreflight(req("https://evil.example", "OPTIONS"), ALLOWED);
  assert.equal(bad.status, 403);
  assert.equal(bad.headers.get("Access-Control-Allow-Origin"), null);
});

test("withCors attaches ACAO even to an error response", () => {
  const origin = "https://sonsteng.damienriehl.com";
  const err = new Response(JSON.stringify({ error: { code: "cap_exceeded" } }), { status: 429 });
  const wrapped = withCors(err, origin);
  assert.equal(wrapped.status, 429);
  assert.equal(wrapped.headers.get("Access-Control-Allow-Origin"), origin);
});

test("withCors on a null origin leaves the response header-less (browser blocks)", () => {
  const err = new Response("nope", { status: 403 });
  const wrapped = withCors(err, null);
  assert.equal(wrapped.headers.get("Access-Control-Allow-Origin"), null);
});
