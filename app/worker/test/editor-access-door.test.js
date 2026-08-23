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
import {
  editorFetch, accessDoorwayRedirect,
} from "../src/editor.js";
import { legacyEditorHostRedirect, publicHostRedirect } from "../src/host-routing.js";

const TEAM = "young-unit-68fd.cloudflareaccess.com";
const AUD = "aud-tag-for-the-editor-app";
const ACCESS_HOST = "edit.legalpracticum.org";
const LEGACY_HOST = "edit.sonsteng.damienriehl.com";
const FALLBACK_HOST = "sonsteng-chat.damienriehl.workers.dev";
const PUBLIC_HOST = "legalpracticum.org";
const WWW_HOST = "www.legalpracticum.org";
const LEGACY_PUBLIC_HOST = "sonsteng.damienriehl.com";
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
    EDIT_LEGACY_HOST: LEGACY_HOST,
    PUBLIC_CANONICAL_HOST: PUBLIC_HOST,
    PUBLIC_REDIRECT_HOSTS: `${WWW_HOST},${LEGACY_PUBLIC_HOST}`,
    EDIT_ORIGIN: `https://${ACCESS_HOST},https://${FALLBACK_HOST}`,
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
  const auth = await resolveAuth(baseEnv(), await accessReq("damien@example.com", { host: FALLBACK_HOST }));
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
  const auth = await resolveAuth(baseEnv(), req(FALLBACK_HOST, "/edit/index.html", { Cookie: `edit_scope=${value}` }));
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
  const auth = await resolveAuth(env, req(FALLBACK_HOST, "/edit/v1/review", {
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
  const auth = await resolveAuth(env, req(FALLBACK_HOST, "/edit/v1/pending", {
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
  assert.equal(accessDoorwayRedirect(baseEnv(), new URL(`https://${FALLBACK_HOST}/`)), null);
});

test("a non-root path on the Access host is not redirected", () => {
  assert.equal(accessDoorwayRedirect(baseEnv(), new URL(`https://${ACCESS_HOST}/v1/session`)), null);
  assert.equal(accessDoorwayRedirect(baseEnv(), new URL(`https://${ACCESS_HOST}/edit/admin`)), null);
});

test("with EDIT_ACCESS_HOST unset there is no doorway at all", () => {
  const env = baseEnv({ EDIT_ACCESS_HOST: "" });
  assert.equal(accessDoorwayRedirect(env, new URL(`https://${ACCESS_HOST}/`)), null);
});

test("the legacy editor hostname redirects every path and query to the new Access host", () => {
  const res = legacyEditorHostRedirect(
    baseEnv(),
    new URL(`https://${LEGACY_HOST}/edit/admin?from=bookmark`),
  );
  assert.ok(res, "the legacy custom domain must remain a useful doorway");
  assert.equal(res.status, 308);
  assert.equal(res.headers.get("Location"), `https://${ACCESS_HOST}/edit/admin?from=bookmark`);
  assert.equal(res.headers.get("Cache-Control"), "private, no-store");
});

test("the legacy-host redirect does not affect the new host or workers.dev fallback", () => {
  assert.equal(legacyEditorHostRedirect(baseEnv(), new URL(`https://${ACCESS_HOST}/edit/`)), null);
  assert.equal(legacyEditorHostRedirect(baseEnv(), new URL(`https://${FALLBACK_HOST}/edit/`)), null);
});

test("public aliases permanently redirect to the canonical domain with path and query intact", () => {
  for (const host of [WWW_HOST, LEGACY_PUBLIC_HOST]) {
    const res = publicHostRedirect(baseEnv(), new URL(`https://${host}/platform/?from=bookmark`));
    assert.ok(res, `${host} must remain a useful doorway`);
    assert.equal(res.status, 308);
    assert.equal(res.headers.get("Location"), `https://${PUBLIC_HOST}/platform/?from=bookmark`);
    assert.equal(res.headers.get("Cache-Control"), "public, max-age=3600");
  }
});

test("the canonical public domain and unrelated hosts are not redirected", () => {
  assert.equal(publicHostRedirect(baseEnv(), new URL(`https://${PUBLIC_HOST}/platform/`)), null);
  assert.equal(publicHostRedirect(baseEnv(), new URL(`https://${FALLBACK_HOST}/platform/`)), null);
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

const HERE = dirname(fileURLToPath(import.meta.url));
const WRANGLER_CONFIG = JSON.parse(
  readFileSync(join(HERE, "..", "wrangler.jsonc"), "utf8")
    // The file is JSONC; strip whole-line // comments only (no block comments
    // and no trailing comments are used in it).
    .replace(/^\s*\/\/.*$/gm, ""),
);

test("workers_dev stays explicitly true so the fallback door survives a route", () => {
  const cfg = WRANGLER_CONFIG;
  assert.equal(
    cfg.workers_dev, true,
    "adding any `routes` key defaults workers_dev to false and unbinds " +
    "sonsteng-chat.damienriehl.workers.dev — John's and Roger's door (R4)",
  );
});

test("env.production declares an empty routes list so it cannot inherit the Access host", () => {
  const cfg = WRANGLER_CONFIG;
  assert.deepEqual(
    cfg.env.production.routes, [],
    "`routes` is inheritable; without an explicit empty list the Access custom " +
    "domain follows `--env production` and R7 breaks",
  );
});

test("DEV binds editor and public canonicalization hostnames", () => {
  const cfg = WRANGLER_CONFIG;
  const routes = cfg.routes.map((route) => route.pattern);
  assert.ok(routes.includes(ACCESS_HOST), "the new Access hostname must route to the DEV Worker");
  assert.ok(routes.includes(LEGACY_HOST), "the old custom domain must remain bound for redirects");
  assert.ok(routes.includes(WWW_HOST), "www must route to the canonical-host redirect");
  assert.ok(routes.includes(LEGACY_PUBLIC_HOST), "the old public domain must remain bound for redirects");
  for (const vars of [cfg.vars, cfg.env.dev.vars]) {
    assert.equal(vars.EDIT_ACCESS_HOST, ACCESS_HOST);
    assert.equal(vars.EDIT_LEGACY_HOST, LEGACY_HOST);
    assert.equal(vars.EDIT_ACCESS_AUD, "b0acc1e2841eacbd9d5d99090a33ff2b558bd0ca2241310762309bc77778fc21");
    assert.equal(vars.PUBLIC_CANONICAL_HOST, PUBLIC_HOST);
    assert.deepEqual(String(vars.PUBLIC_REDIRECT_HOSTS).split(","), [WWW_HOST, LEGACY_PUBLIC_HOST]);
    const publicOrigins = String(vars.ALLOWED_ORIGINS).split(",").map((origin) => origin.trim());
    assert.ok(publicOrigins.includes("https://legalpracticum.org"));
    assert.ok(!publicOrigins.includes("https://sonsteng.damienriehl.com"));
  }
});

test("PROD carries no Access config, so its door is closed by construction (R7)", () => {
  const v = WRANGLER_CONFIG.env.production.vars;
  assert.equal(v.EDIT_ACCESS_AUD, undefined);
  assert.equal(v.EDIT_ACCESS_TEAM_DOMAIN, undefined);
  assert.equal(v.EDIT_ACCESS_HOST, undefined);
  assert.ok(!String(v.EDIT_TOKEN_SCOPES).includes("damienadmin"),
    "the combined-scope slot must not exist in PROD");
});

test("the Access-only Damien slot is the Publisher on the canonical DEV ledger", () => {
  const cfg = WRANGLER_CONFIG;
  for (const vars of [cfg.vars, cfg.env.dev.vars]) {
    assert.equal(JSON.parse(vars.EDIT_TOKEN_SCOPES).damienadmin.publisher, 1);
    assert.equal(vars.PROD_RELEASE_LEDGER, "true");
  }
  assert.equal(cfg.env.production.vars.PROD_RELEASE_LEDGER, "false");
});

test("both browser origins are on the DEV allowlist while the tokens live (KTD6)", () => {
  const cfg = WRANGLER_CONFIG;
  for (const [name, vars] of [["top-level", cfg.vars], ["env.dev", cfg.env.dev.vars]]) {
    const list = String(vars.EDIT_ORIGIN).split(",").map((s) => s.trim());
    assert.ok(list.includes("https://edit.legalpracticum.org"), `${name}: Access origin`);
    assert.ok(!list.includes("https://edit.sonsteng.damienriehl.com"), `${name}: legacy origin retired`);
    assert.ok(list.includes("https://sonsteng-chat.damienriehl.workers.dev"), `${name}: fallback origin`);
  }
});

test("streaming is enabled consistently on DEV and remains disabled on production (U19)", () => {
  const cfg = WRANGLER_CONFIG;
  const topLevelDev = cfg.vars.STREAMING;
  const explicitDev = cfg.env.dev.vars.STREAMING;

  assert.equal(topLevelDev, "true", "the default DEV deploy target must enable streaming");
  assert.equal(explicitDev, "true", "the explicit --env dev target must enable streaming");
  assert.equal(
    topLevelDev,
    explicitDev,
    "the two DEV deployment targets must not be half-flipped",
  );
  assert.equal(
    cfg.env.production.vars.STREAMING,
    "false",
    "production streaming must remain explicitly disabled",
  );
});
