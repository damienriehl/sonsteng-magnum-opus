// Turnstile bot-gate (WP6): siteverify + session-mint gate decision.
// The network fetch to Cloudflare siteverify is mocked so no live challenge is
// needed; tests assert BOTH the ok/reject outcome AND whether siteverify was
// called (bypass/disabled must NOT call it).
import { test } from "node:test";
import assert from "node:assert/strict";
import { turnstileEnabled, siteverify, gateSessionMint } from "../src/turnstile.js";

const SECRET = "0xTEST_SECRET";

// A mock fetch that records calls and returns a canned siteverify JSON.
function mockFetch({ success = true, errorCodes = null, throwErr = false, badJson = false } = {}) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    if (throwErr) throw new Error("network down");
    return {
      json: async () => {
        if (badJson) throw new Error("not json");
        return { success, ...(errorCodes ? { "error-codes": errorCodes } : {}) };
      },
    };
  };
  fn.calls = calls;
  return fn;
}

// ---- turnstileEnabled -------------------------------------------------------
test("turnstileEnabled: only the literal string 'false' disables (fail-closed)", () => {
  assert.equal(turnstileEnabled({ TURNSTILE_ENABLED: "true" }), true);
  assert.equal(turnstileEnabled({ TURNSTILE_ENABLED: "false" }), false);
  assert.equal(turnstileEnabled({}), true); // unset -> enabled
  assert.equal(turnstileEnabled({ TURNSTILE_ENABLED: "1" }), true);
  assert.equal(turnstileEnabled({ TURNSTILE_ENABLED: "" }), true);
});

// ---- siteverify -------------------------------------------------------------
test("siteverify: success true -> {success:true}, posts secret+response", async () => {
  const f = mockFetch({ success: true });
  const r = await siteverify(SECRET, "good-token", "1.2.3.4", f);
  assert.equal(r.success, true);
  assert.equal(f.calls.length, 1);
  const body = f.calls[0].opts.body;
  assert.match(body, /secret=0xTEST_SECRET/);
  assert.match(body, /response=good-token/);
  assert.match(body, /remoteip=1.2.3.4/);
});

test("siteverify: success false surfaces error-codes as reason", async () => {
  const f = mockFetch({ success: false, errorCodes: ["invalid-input-response"] });
  const r = await siteverify(SECRET, "bad-token", "1.2.3.4", f);
  assert.equal(r.success, false);
  assert.equal(r.reason, "invalid-input-response");
});

test("siteverify: empty/missing token short-circuits without a network call", async () => {
  const f = mockFetch({ success: true });
  const r = await siteverify(SECRET, "", "1.2.3.4", f);
  assert.equal(r.success, false);
  assert.equal(r.reason, "missing_token");
  assert.equal(f.calls.length, 0);
});

test("siteverify: 'unknown' ip is omitted from the form", async () => {
  const f = mockFetch({ success: true });
  await siteverify(SECRET, "good-token", "unknown", f);
  assert.doesNotMatch(f.calls[0].opts.body, /remoteip/);
});

test("siteverify: a network throw resolves to success:false (never throws)", async () => {
  const f = mockFetch({ throwErr: true });
  const r = await siteverify(SECRET, "good-token", "1.2.3.4", f);
  assert.equal(r.success, false);
  assert.equal(r.reason, "network_error");
});

test("siteverify: an unparseable body resolves to success:false", async () => {
  const f = mockFetch({ badJson: true });
  const r = await siteverify(SECRET, "good-token", "1.2.3.4", f);
  assert.equal(r.success, false);
  assert.equal(r.reason, "parse_error");
});

// ---- gateSessionMint --------------------------------------------------------
test("gate: valid token -> ok, siteverify called once", async () => {
  const f = mockFetch({ success: true });
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "true", TURNSTILE_SECRET: SECRET },
    token: "good-token", isDemo: false, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, true);
  assert.equal(f.calls.length, 1);
});

test("gate: invalid token -> 403 turnstile_failed", async () => {
  const f = mockFetch({ success: false, errorCodes: ["invalid-input-response"] });
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "true", TURNSTILE_SECRET: SECRET },
    token: "bad-token", isDemo: false, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, false);
  assert.equal(g.status, 403);
  assert.equal(g.code, "turnstile_failed");
});

test("gate: missing token -> 403 turnstile_failed (no live siteverify success)", async () => {
  const f = mockFetch({ success: true }); // even a 'success' mock: empty token short-circuits
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "true", TURNSTILE_SECRET: SECRET },
    token: "", isDemo: false, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, false);
  assert.equal(g.status, 403);
  assert.equal(f.calls.length, 0); // short-circuited on empty token
});

test("gate: demo bypass -> ok and NO siteverify call (keyless flows preserved)", async () => {
  const f = mockFetch({ success: false }); // would reject if called
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "true", TURNSTILE_SECRET: SECRET },
    token: "", isDemo: true, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, true);
  assert.equal(g.skipped, "bypass");
  assert.equal(f.calls.length, 0);
});

test("gate: TURNSTILE_ENABLED='false' -> ok and NO siteverify call", async () => {
  const f = mockFetch({ success: false });
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "false", TURNSTILE_SECRET: SECRET },
    token: "", isDemo: false, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, true);
  assert.equal(g.skipped, "disabled");
  assert.equal(f.calls.length, 0);
});

test("gate: enabled but no secret -> 503 (fail retryable, not open)", async () => {
  const f = mockFetch({ success: true });
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "true" }, // no TURNSTILE_SECRET
    token: "good-token", isDemo: false, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, false);
  assert.equal(g.status, 503);
  assert.equal(g.code, "turnstile_failed");
  assert.equal(g.reason, "no_secret");
  assert.equal(f.calls.length, 0);
});

test("gate: bypass wins even when the gate is disabled (both carve-outs, still ok)", async () => {
  const f = mockFetch({ success: false });
  const g = await gateSessionMint({
    env: { TURNSTILE_ENABLED: "false" },
    token: "", isDemo: true, ip: "1.2.3.4", fetchImpl: f,
  });
  assert.equal(g.ok, true);
  assert.equal(g.skipped, "bypass");
  assert.equal(f.calls.length, 0);
});
