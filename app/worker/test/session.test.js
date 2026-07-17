// HMAC session token: sign/verify round-trip + tamper rejection.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mintSession, verifySession, utcDay } from "../src/session.js";

const KEY = "test-signing-key-0123456789abcdef";

test("mint then verify round-trips sid, day, and pool", async () => {
  const { token, sid } = await mintSession(KEY, { pool: "demo", day: "2026-07-17" });
  const v = await verifySession(KEY, token);
  assert.ok(v);
  assert.equal(v.sid, sid);
  assert.equal(v.d, "2026-07-17");
  assert.equal(v.p, "demo");
});

test("default pool is public", async () => {
  const { token } = await mintSession(KEY);
  const v = await verifySession(KEY, token);
  assert.equal(v.p, "public");
  assert.equal(v.d, utcDay());
});

test("a token signed with a different key is rejected", async () => {
  const { token } = await mintSession(KEY);
  assert.equal(await verifySession("some-other-key", token), null);
});

test("tampering with the payload is rejected", async () => {
  const { token } = await mintSession(KEY, { sid: "aaaa", pool: "public" });
  const [payload, sig] = token.split(".");
  // Flip a byte in the payload; signature no longer matches.
  const forgedPayload = payload.slice(0, -1) + (payload.slice(-1) === "A" ? "B" : "A");
  assert.equal(await verifySession(KEY, forgedPayload + "." + sig), null);
});

test("a client cannot upgrade public -> demo by editing the payload", async () => {
  const { token } = await mintSession(KEY, { pool: "public" });
  const [payload, sig] = token.split(".");
  // Re-encode a demo payload but keep the old (public) signature.
  const demoPayload = Buffer.from(JSON.stringify({ sid: "x", d: utcDay(), p: "demo" }))
    .toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  assert.equal(await verifySession(KEY, demoPayload + "." + sig), null);
});

test("malformed tokens are rejected, not thrown", async () => {
  for (const bad of ["", "no-dot", "a.b.c", "....", "x."]) {
    assert.equal(await verifySession(KEY, bad), null);
  }
});
