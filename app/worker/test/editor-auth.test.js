// editor-auth.test.js — opaque token -> scope record, the signed cookie, scope
// rotation invalidation, admin isolation, and the CSRF guard.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resolveOpaqueToken, mintCookieValue, verifyCookieValue, buildSetCookie,
  resolveAuth, resolveRequestScopes,
} from "../src/editor-auth.js";
import { csrfOk } from "../src/editor-http.js";

const SIGNING = "test-signing-key-abc";
const ENV = {
  SESSION_SIGNING_KEY: SIGNING,
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1, instructor: 1 }, admin: { admin: 1 },
    release: { release_service: 1 }, observer: { release_observer: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
  EDIT_TOKEN_RELEASE: "release-opaque-token-value-456",
  EDIT_TOKEN_OBSERVER: "observer-opaque-token-value-789",
};

function reqWithCookie(value, headers = {}) {
  return new Request("https://worker.example.com/edit/v1/pending", {
    headers: { Cookie: `edit_scope=${value}`, ...headers },
  });
}

test("opaque token resolves to the correct scope record", async () => {
  const john = await resolveOpaqueToken(ENV, "john-opaque-token-value-123");
  assert.ok(john);
  assert.equal(john.slot, "john");
  assert.equal(john.record.edit.granted, true);
  assert.equal(john.record.instructor.granted, true);
  assert.equal(john.record.admin.granted, false); // NEVER admin from john's token

  const admin = await resolveOpaqueToken(ENV, "admin-opaque-token-value-999");
  assert.equal(admin.slot, "admin");
  assert.equal(admin.record.admin.granted, true);
  assert.equal(admin.record.edit.granted, false);
  assert.equal(admin.record.release_service.granted, false);

  const release = await resolveOpaqueToken(ENV, "release-opaque-token-value-456");
  assert.equal(release.slot, "release");
  assert.equal(release.record.release_service.granted, true);
  assert.equal(release.record.admin.granted, false);

  const observer = await resolveOpaqueToken(ENV, "observer-opaque-token-value-789");
  assert.equal(observer.record.release_observer.granted, true);
  assert.equal(observer.record.release_service.granted, false);
  assert.equal(observer.record.admin.granted, false);
});

test("an unknown / empty token resolves to nothing", async () => {
  assert.equal(await resolveOpaqueToken(ENV, "not-a-real-token"), null);
  assert.equal(await resolveOpaqueToken(ENV, ""), null);
  assert.equal(await resolveOpaqueToken(ENV, null), null);
});

test("a secret duplicated across slots fails closed regardless of slot order", async () => {
  const duplicate = "shared-observer-and-release-secret";
  for (const scopes of [
    { observer:{ release_observer:1 }, release:{ release_service:1 } },
    { release:{ release_service:1 }, observer:{ release_observer:1 } },
  ]) {
    const env = { ...ENV,EDIT_TOKEN_SCOPES:JSON.stringify(scopes),
      EDIT_TOKEN_OBSERVER:duplicate,EDIT_TOKEN_RELEASE:duplicate };
    assert.equal(await resolveOpaqueToken(env, duplicate), null);
  }
});

test("release observer authority cannot be combined with another scope", async () => {
  for (const extra of ["release_service","admin","publisher","edit","instructor"]) {
    const env = { ...ENV,EDIT_TOKEN_SCOPES:JSON.stringify({
      observer:{ release_observer:1,[extra]:1 },
    }) };
    assert.equal(await resolveOpaqueToken(env, ENV.EDIT_TOKEN_OBSERVER), null);
  }
});

test("cookie round-trips and carries only the slot + stamp (not the raw token)", async () => {
  const matched = await resolveOpaqueToken(ENV, "john-opaque-token-value-123");
  const value = await mintCookieValue(SIGNING, { slot: matched.slot, stamp: matched.stamp });
  assert.ok(!value.includes("john-opaque-token-value-123")); // raw token never in cookie
  const parsed = await verifyCookieValue(SIGNING, value);
  assert.equal(parsed.slot, "john");
  assert.equal(parsed.stamp, matched.stamp);
});

test("a tampered cookie fails verification", async () => {
  const matched = await resolveOpaqueToken(ENV, "john-opaque-token-value-123");
  const value = await mintCookieValue(SIGNING, { slot: matched.slot, stamp: matched.stamp });
  const tampered = value.slice(0, -2) + (value.slice(-2) === "AA" ? "BB" : "AA");
  assert.equal(await verifyCookieValue(SIGNING, tampered), null);
});

test("resolveAuth grants scopes from a valid cookie; server-side editor id", async () => {
  const matched = await resolveOpaqueToken(ENV, "john-opaque-token-value-123");
  const value = await mintCookieValue(SIGNING, { slot: matched.slot, stamp: matched.stamp });
  const auth = await resolveAuth(ENV, reqWithCookie(value));
  assert.equal(auth.scopes.edit.granted, true);
  assert.equal(auth.editor, "slot:john"); // server-controlled identity
});

test("scope rotation invalidates an already-issued cookie (independent rotation)", async () => {
  const matched = await resolveOpaqueToken(ENV, "john-opaque-token-value-123");
  const value = await mintCookieValue(SIGNING, { slot: matched.slot, stamp: matched.stamp });
  // Rotate the instructor scope version -> stamp changes -> old cookie stale.
  const rotated = {
    ...ENV,
    EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1, instructor: 2 }, admin: { admin: 1 } }),
  };
  const auth = await resolveAuth(rotated, reqWithCookie(value));
  assert.equal(auth.scopes.edit.granted, false);
  assert.equal(auth.scopes.instructor.granted, false);
  assert.equal(auth.editor, null);
});

test("no cookie => all-false scopes", async () => {
  const scopes = await resolveRequestScopes(ENV, new Request("https://worker.example.com/edit/v1/pending"));
  assert.equal(scopes.edit.granted, false);
  assert.equal(scopes.admin.granted, false);
});

test("CSRF guard: requires X-Edit-Request:1 and a same-origin/absent Origin", () => {
  const good = new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: { "X-Edit-Request": "1", Origin: "https://worker.example.com" },
  });
  assert.equal(csrfOk(good, ENV), true);

  const noHeader = new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: { Origin: "https://worker.example.com" },
  });
  assert.equal(csrfOk(noHeader, ENV), false);

  const foreignOrigin = new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: { "X-Edit-Request": "1", Origin: "https://evil.example.com" },
  });
  assert.equal(csrfOk(foreignOrigin, ENV), false);

  const crossSite = new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: { "X-Edit-Request": "1", "Sec-Fetch-Site": "cross-site" },
  });
  assert.equal(csrfOk(crossSite, ENV), false);
});

test("service auth: admin opaque token via Authorization Bearer resolves admin scope (CSRF-safe, no cookie)", async () => {
  const req = new Request("https://worker.example.com/edit/v1/reconcile", {
    method: "POST",
    headers: { Authorization: "Bearer admin-opaque-token-value-999", "X-Edit-Request": "1" },
  });
  const auth = await resolveAuth(ENV, req);
  assert.equal(auth.slot, "admin");
  assert.equal(auth.scopes.admin.granted, true);
  assert.equal(auth.scopes.edit.granted, false);
});

test("service auth: a bogus Bearer token grants nothing", async () => {
  const req = new Request("https://worker.example.com/edit/v1/reconcile", {
    method: "POST",
    headers: { Authorization: "Bearer not-a-real-token", "X-Edit-Request": "1" },
  });
  const auth = await resolveAuth(ENV, req);
  assert.equal(auth.slot, null);
  assert.equal(auth.scopes.admin.granted, false);
});

test("service auth: a valid cookie still wins and Bearer is only the no-cookie fallback", async () => {
  const cookie = await mintCookieValue(SIGNING, { slot: "john", stamp: (await resolveOpaqueToken(ENV, "john-opaque-token-value-123")).stamp });
  const req = new Request("https://worker.example.com/edit/v1/pending", {
    headers: { Cookie: `edit_scope=${cookie}`, Authorization: "Bearer admin-opaque-token-value-999" },
  });
  const auth = await resolveAuth(ENV, req);
  assert.equal(auth.slot, "john"); // cookie wins; Bearer not consulted
});
