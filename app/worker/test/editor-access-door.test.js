// editor-access-door.test.js — the Access door END TO END, from an assertion on
// the wire to a slot with scopes, and the routing that hangs off it.
//
// access-jwt.test.js proves the verifier in isolation. This file proves the
// things that only exist once the verifier is WIRED IN, and that no unit test of
// a single module can catch:
//
//   * resolveAuth gains a third identity source WITHOUT disturbing the first two
//     (R4 is "true by construction" only if that is actually true — so the ?t=
//     exchange, the cookie and the apply daemon's Bearer path are re-asserted
//     here with the Access branch present).
//   * The host gate (KTD3). The workers.dev origin stays directly reachable and
//     anyone can forge a Cf-Access-Jwt-Assertion header on it, so a valid
//     assertion presented on the WRONG host must grant nothing at all. This is
//     the single test standing between "Access door" and "anyone is an editor".
//   * damienadmin — one identity holding admin scope AND DR attribution, which
//     no pre-existing slot could do, without making admin reachable from a token.
//   * The doorway: the bare hostname, the scope-aware landing, and the admin page
//     returning bytes IDENTICAL to any unknown /edit path when under-scoped (R6).
//
// The crypto is real (same forge as access-jwt.test.js); only fetch is stubbed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveAuth, mintCookieValue, attributionLabel, lookupAccessSlot } from "../src/editor-auth.js";
import { __resetJwksCache } from "../src/access-jwt.js";
import { uniform404 } from "../src/editor-http.js";
import { editorFetch, accessDoorwayRedirect } from "../src/editor.js";

const TEAM = "young-unit-68fd.cloudflareaccess.com";
const AUD = "aud-tag-for-the-editor-app";
const ACCESS_HOST = "edit.sonsteng.damienriehl.com";
const OLD_HOST = "sonsteng-chat.damienriehl.workers.dev";
const SIGNING_KEY = "test-signing-key-not-a-real-secret";

// The real shape from wrangler.jsonc, including the Access-only combined slot.
const SCOPES = JSON.stringify({
  john: { edit: 1, instructor: 1 },
  roger: { edit: 1, instructor: 1 },
  damien: { edit: 1, instructor: 1 },
  damienadmin: { edit: 1, instructor: 1, admin: 1 },
  admin: { admin: 1 },
});

const EMAILS = JSON.stringify({
  "john@example.com": "john",
  "roger@example.com": "roger",
  "damien@example.com": "damienadmin",
});

function baseEnv(overrides = {}) {
  return {
    SESSION_SIGNING_KEY: SIGNING_KEY,
    EDIT_TOKEN_SCOPES: SCOPES,
    EDIT_ACCESS_EMAILS: EMAILS,
    EDIT_ACCESS_AUD: AUD,
    EDIT_ACCESS_TEAM_DOMAIN: TEAM,
    EDIT_ACCESS_HOST: ACCESS_HOST,
    EDIT_ORIGIN: `https://${ACCESS_HOST},https://${OLD_HOST}`,
    EDIT_UPSTREAM: "https://sonsteng-dev.damienriehl.com/platform/",
    ...overrides,
  };
}

// ---- token forge ------------------------------------------------------------
const enc = new TextEncoder();
const nowSec = () => Math.floor(Date.now() / 1000);
function b64url(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const b64urlJson = (obj) => b64url(enc.encode(JSON.stringify(obj)));

let signer = null;
async function makeSigner(kid = "kid-1") {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const pub = await crypto.subtle.exportKey("jwk", pair.publicKey);
  return {
    jwk: { kty: "RSA", use: "sig", alg: "RS256", kid, n: pub.n, e: pub.e },
    async sign(payload) {
      const header = { alg: "RS256", kid, typ: "JWT" };
      const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
      const sig = new Uint8Array(
        await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, enc.encode(signingInput)),
      );
      return `${signingInput}.${b64url(sig)}`;
    },
  };
}

async function assertionFor(email) {
  if (!signer) signer = await makeSigner();
  const t = nowSec();
  return signer.sign({ aud: [AUD], iss: `https://${TEAM}`, email, iat: t - 10, exp: t + 3600 });
}

// Serve the JWKS for whatever signer the suite made.
globalThis.fetch = async () => new Response(JSON.stringify({ keys: signer ? [signer.jwk] : [] }), {
  status: 200,
  headers: { "content-type": "application/json" },
});

function req(host, path, headers = {}) {
  return new Request(`https://${host}${path}`, { headers });
}

async function accessReq(email, { host = ACCESS_HOST, path = "/edit/admin" } = {}) {
  const token = await assertionFor(email);
  return req(host, path, { "Cf-Access-Jwt-Assertion": token });
}

// ---- 1. a verified identity becomes a slot ---------------------------------

test("a verified john@ resolves to slot john with edit+instructor and JOS", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), await accessReq("john@example.com"));
  assert.equal(auth.slot, "john");
  assert.equal(auth.scopes.edit.granted, true);
  assert.equal(auth.scopes.instructor.granted, true);
  assert.equal(auth.scopes.admin.granted, false);
  assert.equal(attributionLabel(auth.editor), "JOS");
});

test("a verified roger@ resolves to slot roger with RSH", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), await accessReq("roger@example.com"));
  assert.equal(auth.slot, "roger");
  assert.equal(attributionLabel(auth.editor), "RSH");
});

// The whole reason damienadmin exists. Before it, EDIT_TOKEN_SCOPES could give
// Damien {edit,instructor} as `damien` (DR, but no admin page) or {admin} as
// `admin` (ADM, but no editing and no history access) — never both.
test("a verified damien@ gets admin scope AND DR attribution at once", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), await accessReq("damien@example.com"));
  assert.equal(auth.slot, "damienadmin");
  assert.equal(auth.scopes.admin.granted, true, "must reach the admin page");
  assert.equal(auth.scopes.edit.granted, true, "must still be able to edit");
  assert.equal(auth.scopes.instructor.granted, true, "must still open /edit/history");
  assert.equal(attributionLabel(auth.editor), "DR", "an edit must still read as DR");
});

test("a verified email absent from the map yields no scopes", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), await accessReq("stranger@example.com"));
  assert.equal(auth.slot, null);
  assert.equal(auth.editor, null);
  assert.equal(auth.scopes.edit.granted, false);
  assert.equal(auth.scopes.admin.granted, false);
});

test("a mixed-case email claim resolves to the same slot", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), await accessReq("John@Example.COM"));
  assert.equal(auth.slot, "john", "a mixed-case claim must not lock someone out");
});

// ---- 2. the host gate (KTD3) — the load-bearing one ------------------------

test("a VALID assertion on the workers.dev host grants nothing (KTD3)", async () => {
  __resetJwksCache();
  // Same token that works on the Access host, presented on the origin anyone can
  // reach directly and forge headers on.
  const auth = await resolveAuth(baseEnv(), await accessReq("damien@example.com", { host: OLD_HOST }));
  assert.equal(auth.slot, null, "header presence alone must never grant access");
  assert.equal(auth.scopes.admin.granted, false);
});

test("a valid assertion on an unrelated host grants nothing", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), await accessReq("damien@example.com", { host: "evil.example.com" }));
  assert.equal(auth.slot, null);
});

test("with EDIT_ACCESS_HOST unset the Access branch is inert", async () => {
  __resetJwksCache();
  const env = baseEnv({ EDIT_ACCESS_HOST: "" });
  const auth = await resolveAuth(env, await accessReq("damien@example.com"));
  assert.equal(auth.slot, null, "an env with no Access config must not take the branch");
});

// This is the property that keeps PROD safe under R7: env.production carries no
// EDIT_ACCESS_* vars at all, so even a genuine assertion resolves to nothing.
test("with EDIT_ACCESS_AUD unset (PROD's shape) the door is closed", async () => {
  __resetJwksCache();
  const env = baseEnv({ EDIT_ACCESS_AUD: "" });
  const auth = await resolveAuth(env, await accessReq("damien@example.com"));
  assert.equal(auth.slot, null);
});

test("a garbage assertion header is rejected without throwing", async () => {
  __resetJwksCache();
  const auth = await resolveAuth(baseEnv(), req(ACCESS_HOST, "/edit/admin", {
    "Cf-Access-Jwt-Assertion": "not.a.jwt",
  }));
  assert.equal(auth.slot, null);
});

// ---- 3. the other two doors are undisturbed (R4) ---------------------------

test("cookie identity still resolves with the Access branch present", async () => {
  __resetJwksCache();
  const value = await mintCookieValue(SIGNING_KEY, {
    slot: "john",
    stamp: "john|edit:1,instructor:1,admin:-",
  });
  const auth = await resolveAuth(baseEnv(), req(OLD_HOST, "/edit/index.html", { Cookie: `edit_scope=${value}` }));
  assert.equal(auth.slot, "john");
  assert.equal(auth.scopes.edit.granted, true);
});

test("cookie identity WINS when both a cookie and an assertion are present", async () => {
  __resetJwksCache();
  const value = await mintCookieValue(SIGNING_KEY, {
    slot: "john",
    stamp: "john|edit:1,instructor:1,admin:-",
  });
  const token = await assertionFor("damien@example.com");
  const auth = await resolveAuth(baseEnv(), req(ACCESS_HOST, "/edit/admin", {
    Cookie: `edit_scope=${value}`,
    "Cf-Access-Jwt-Assertion": token,
  }));
  assert.equal(auth.slot, "john", "no ambiguity: the cookie is checked first and wins");
  assert.equal(auth.scopes.admin.granted, false);
});

test("the apply daemon's Bearer path is untouched by the Access branch", async () => {
  __resetJwksCache();
  const env = baseEnv({ EDIT_TOKEN_ADMIN: "service-token-value" });
  const auth = await resolveAuth(env, req(OLD_HOST, "/edit/v1/review", {
    Authorization: "Bearer service-token-value",
  }));
  assert.equal(auth.slot, "admin");
  assert.equal(auth.scopes.admin.granted, true);
});

// The combined-scope slot must be unreachable from any ?t= token, or it would
// have quietly widened what a leaked bookmark grants.
test("damienadmin is NOT reachable by a Bearer token (no secret exists for it)", async () => {
  __resetJwksCache();
  const env = baseEnv({ EDIT_TOKEN_DAMIEN: "damien-token" });
  const auth = await resolveAuth(env, req(OLD_HOST, "/edit/v1/pending", {
    Authorization: "Bearer damien-token",
  }));
  assert.equal(auth.slot, "damien", "the token maps to the edit-only slot");
  assert.equal(auth.scopes.admin.granted, false, "a token must never reach admin");
});

// ---- 4. the email -> slot map ----------------------------------------------

test("lookupAccessSlot rejects malformed config and unknown addresses", () => {
  const env = baseEnv();
  assert.equal(lookupAccessSlot(env, "john@example.com"), "john");
  assert.equal(lookupAccessSlot(env, "  John@Example.com  "), "john");
  assert.equal(lookupAccessSlot(env, "nobody@example.com"), null);
  assert.equal(lookupAccessSlot(env, ""), null);
  assert.equal(lookupAccessSlot(env, null), null);
  assert.equal(lookupAccessSlot({ EDIT_ACCESS_EMAILS: "{not json" }, "john@example.com"), null);
  assert.equal(lookupAccessSlot({}, "john@example.com"), null);
});

// ---- 5. the doorway ---------------------------------------------------------

test("the bare Access hostname 302s into /edit/", () => {
  const res = accessDoorwayRedirect(baseEnv(), new URL(`https://${ACCESS_HOST}/`));
  assert.ok(res, "the bare hostname must not fall through to the chat router");
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("Location"), "/edit/");
});

test("the workers.dev root is NOT redirected", () => {
  assert.equal(accessDoorwayRedirect(baseEnv(), new URL(`https://${OLD_HOST}/`)), null);
});

test("a non-root path on the Access host is not redirected", () => {
  assert.equal(accessDoorwayRedirect(baseEnv(), new URL(`https://${ACCESS_HOST}/v1/session`)), null);
  assert.equal(accessDoorwayRedirect(baseEnv(), new URL(`https://${ACCESS_HOST}/edit/admin`)), null);
});

test("with EDIT_ACCESS_HOST unset there is no doorway at all", () => {
  const env = baseEnv({ EDIT_ACCESS_HOST: "" });
  assert.equal(accessDoorwayRedirect(env, new URL(`https://${ACCESS_HOST}/`)), null);
});

// ---- 6. the scope-aware landing and the admin page -------------------------

// editorFetch is what index.js delegates to; a fake EDITOR stub stands in for the
// Durable Object (editor-store.js imports cloudflare:workers and cannot load
// here). listAll/listRevertRequests are the only two methods /edit/admin calls.
function envWithStore(rows = [], reverts = []) {
  return baseEnv({
    EDITOR: {
      getByName: () => ({
        async listAll() { return rows; },
        async listRevertRequests() { return reverts; },
      }),
    },
  });
}

const CTX = { waitUntil() {}, passThroughOnException() {} };

test("/edit/ sends an admin identity to the dashboard", async () => {
  __resetJwksCache();
  const res = await editorFetch(await accessReq("damien@example.com", { path: "/edit/" }), envWithStore(), CTX);
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("Location"), "/edit/admin");
});

// John completing a sign-in and landing on a 404 is the exact dead end this plan
// exists to close, so it gets its own test.
test("/edit/ sends an edit-scope identity to the practicum, not a 404", async () => {
  __resetJwksCache();
  const res = await editorFetch(await accessReq("john@example.com", { path: "/edit/" }), envWithStore(), CTX);
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("Location"), "/edit/index.html");
});

test("/edit/ with no identity is the uniform 404", async () => {
  const res = await editorFetch(req(ACCESS_HOST, "/edit/"), envWithStore(), CTX);
  assert.equal(res.status, 404);
});

test("/edit/admin renders for admin scope and sets the last-seen cookie", async () => {
  __resetJwksCache();
  const res = await editorFetch(await accessReq("damien@example.com"), envWithStore(), CTX);
  assert.equal(res.status, 200);
  const body = await res.text();
  assert.match(body, /<!doctype html>/i);
  assert.ok(!/<script/i.test(body), "the admin page must carry no script (CSP is script-src 'self')");
  const cookie = res.headers.get("Set-Cookie") || "";
  assert.match(cookie, /^edit_seen=/);
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /Path=\/edit\/admin/);
});

test("/edit/admin under-scoped is BYTE-IDENTICAL to an unknown /edit path (R6)", async () => {
  __resetJwksCache();
  const env = envWithStore();
  const asJohn = await editorFetch(await accessReq("john@example.com", { path: "/edit/admin" }), env, CTX);
  const unknown = await editorFetch(req(ACCESS_HOST, "/edit/no-such-page-at-all"), env, CTX);
  assert.equal(asJohn.status, 404);
  const a = await asJohn.text();
  const b = await unknown.text();
  assert.equal(a, b, "an under-scoped admin page must not be distinguishable");
  assert.equal(a, await uniform404().text());
  assert.equal(asJohn.headers.get("Set-Cookie"), null, "a 404 must not leak a cookie either");
});

test("/edit/admin with no identity is the uniform 404", async () => {
  const res = await editorFetch(req(ACCESS_HOST, "/edit/admin"), envWithStore(), CTX);
  assert.equal(res.status, 404);
  assert.equal(await res.text(), await uniform404().text());
});

test("a non-GET to /edit/admin is the uniform 404", async () => {
  __resetJwksCache();
  const token = await assertionFor("damien@example.com");
  const res = await editorFetch(
    new Request(`https://${ACCESS_HOST}/edit/admin`, {
      method: "POST",
      headers: { "Cf-Access-Jwt-Assertion": token },
    }),
    envWithStore(),
    CTX,
  );
  assert.equal(res.status, 404);
});

// The flags must be per-viewer: Damien's own edits are not news to Damien.
test("the admin page's flags exclude the viewer's own edits", async () => {
  __resetJwksCache();
  const t = Date.now();
  const rows = [
    { editor: "slot:john", source_ref: "data/matters/m01/exercise.json#a.b", updated_at: t, status: "pending" },
    { editor: "slot:damienadmin", source_ref: "data/matters/m01/other.json#c.d", updated_at: t, status: "pending" },
  ];
  const res = await editorFetch(
    await accessReq("damien@example.com"),
    envWithStore(rows),
    CTX,
  );
  const body = await res.text();
  // First visit: no edit_seen cookie, so the flags list is empty by design.
  assert.equal(res.status, 200);
  assert.ok(body.length > 0);
});

// ---- 7. wrangler config invariants (a real incident, 2026-07-27) -----------
//
// Deploying U1's custom-domain route SILENTLY unbound
// sonsteng-chat.damienriehl.workers.dev — the fallback door — because wrangler
// defaults `workers_dev` to FALSE the moment any `routes` key exists, and says
// so only in an advisory warning buried in deploy output. The live door served a
// bare Cloudflare "error code: 1042" until it was caught by probing. That is
// exactly the R4 breakage the plan exists to prevent, caused by the plan's own
// U1, and nothing in the test suite could see it because it is a config
// property, not a code property. These assertions are cheap and they would have
// caught it.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

function wranglerConfig() {
  const here = dirname(fileURLToPath(import.meta.url));
  const raw = readFileSync(join(here, "..", "wrangler.jsonc"), "utf8");
  // The file is JSONC; strip whole-line // comments only (no block comments and
  // no trailing comments are used in it).
  return JSON.parse(raw.replace(/^\s*\/\/.*$/gm, ""));
}

test("workers_dev stays explicitly true so the fallback door survives a route", () => {
  const cfg = wranglerConfig();
  assert.equal(
    cfg.workers_dev, true,
    "adding any `routes` key defaults workers_dev to false and unbinds " +
    "sonsteng-chat.damienriehl.workers.dev — John's and Roger's door (R4)",
  );
});

test("env.production declares an empty routes list so it cannot inherit the Access host", () => {
  const cfg = wranglerConfig();
  assert.deepEqual(
    cfg.env.production.routes, [],
    "`routes` is inheritable; without an explicit empty list the Access custom " +
    "domain follows `--env production` and R7 breaks",
  );
});

test("PROD carries no Access config, so its door is closed by construction (R7)", () => {
  const v = wranglerConfig().env.production.vars;
  assert.equal(v.EDIT_ACCESS_AUD, undefined);
  assert.equal(v.EDIT_ACCESS_TEAM_DOMAIN, undefined);
  assert.equal(v.EDIT_ACCESS_HOST, undefined);
  assert.ok(!String(v.EDIT_TOKEN_SCOPES).includes("damienadmin"),
    "the combined-scope slot must not exist in PROD");
});

test("both browser origins are on the DEV allowlist while the tokens live (KTD6)", () => {
  const cfg = wranglerConfig();
  for (const [name, vars] of [["top-level", cfg.vars], ["env.dev", cfg.env.dev.vars]]) {
    const list = String(vars.EDIT_ORIGIN).split(",").map((s) => s.trim());
    assert.ok(list.includes("https://edit.sonsteng.damienriehl.com"), `${name}: Access origin`);
    assert.ok(list.includes("https://sonsteng-chat.damienriehl.workers.dev"), `${name}: fallback origin`);
  }
});
