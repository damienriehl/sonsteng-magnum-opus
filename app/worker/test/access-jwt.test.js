// access-jwt.test.js — the Cloudflare Access door: RS256 verification against a
// JWKS, the claim checks, the fail-closed config gate, and every rejection path.
//
// The crypto here is REAL: each test generates an RSA key pair with
// crypto.subtle.generateKey and signs an actual token, so a verifier that
// silently stopped checking signatures would fail these tests. Only the network
// is stubbed — globalThis.fetch serves the JWKS — which also lets the tests
// assert the cache/refresh behaviour by counting fetches.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  verifyAccessJwt, parseJws, readAccessConfig, normalizeIssuer, timeClaimsValid,
  __resetJwksCache, JWKS_TTL_MS,
} from "../src/access-jwt.js";

const TEAM = "sonsteng.cloudflareaccess.com";
const AUD = "aud-tag-abcdef0123456789";
const ENV = Object.freeze({
  EDIT_ACCESS_AUD: AUD,
  EDIT_ACCESS_TEAM_DOMAIN: TEAM,
  EDIT_ACCESS_HOST: "edit.sonsteng.example.com",
});

const enc = new TextEncoder();
const nowSec = () => Math.floor(Date.now() / 1000);

// ---- token forge (a real signer, plus the two attack shapes) ----------------
function b64url(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const b64urlJson = (obj) => b64url(enc.encode(JSON.stringify(obj)));

async function makeSigner(kid) {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const pub = await crypto.subtle.exportKey("jwk", pair.publicKey);
  return {
    kid,
    jwk: { kty: "RSA", use: "sig", alg: "RS256", kid, n: pub.n, e: pub.e },
    async sign(payload, headerOverrides = {}) {
      const header = { alg: "RS256", kid, typ: "JWT", ...headerOverrides };
      const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
      const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, enc.encode(signingInput)));
      return `${signingInput}.${b64url(sig)}`;
    },
  };
}

function goodClaims(overrides = {}) {
  const t = nowSec();
  return { aud: [AUD], iss: `https://${TEAM}`, email: "John@Example.com", iat: t - 10, exp: t + 3600, ...overrides };
}

// ---- JWKS network stub ------------------------------------------------------
let fetchCalls = 0;
let responder = null;
globalThis.fetch = async (...args) => {
  fetchCalls++;
  return responder(...args);
};

function serveKeys(keys) {
  responder = async () => new Response(JSON.stringify({ keys }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

// Reset the per-isolate JWKS cache AND the fetch counter before every test, so
// no test inherits another's warm cache.
function reset(keys) {
  __resetJwksCache();
  fetchCalls = 0;
  if (keys) serveKeys(keys);
}

function reqWith(token) {
  const headers = token === undefined ? {} : { "Cf-Access-Jwt-Assertion": token };
  return new Request("https://edit.sonsteng.example.com/edit/", { headers });
}

// ---- the happy path ---------------------------------------------------------
test("a genuine token verifies and yields the lowercased email", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const out = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims())));
  assert.deepEqual(out, { email: "john@example.com" });
  assert.equal(fetchCalls, 1);
});

test("aud as a bare string, and as an array containing the tag, both verify", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const asString = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ aud: AUD }))));
  assert.equal(asString.email, "john@example.com");
  const asArray = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ aud: ["other-app-tag", AUD] }))));
  assert.equal(asArray.email, "john@example.com");
});

test("an issuer written without the https:// prefix still matches", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const out = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ iss: TEAM }))));
  assert.equal(out.email, "john@example.com");
  // …and the config may be written either way too.
  const bare = { ...ENV, EDIT_ACCESS_TEAM_DOMAIN: `https://${TEAM}/` };
  reset([s.jwk]);
  const out2 = await verifyAccessJwt(bare, reqWith(await s.sign(goodClaims())));
  assert.equal(out2.email, "john@example.com");
});

// ---- claim rejections -------------------------------------------------------
test("a validly signed token for the WRONG aud is rejected", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ aud: ["some-other-app"] })))), null);
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ aud: [] })))), null);
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ aud: undefined })))), null);
});

test("a token from the WRONG issuer is rejected (the token never picks its own team)", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const forged = await s.sign(goodClaims({ iss: "https://attacker.cloudflareaccess.com" }));
  assert.equal(await verifyAccessJwt(ENV, reqWith(forged)), null);
});

test("expiry is enforced: 5 minutes stale is rejected, the 60s skew is a bound not a loophole", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const t = nowSec();
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ exp: t - 120 })))), null);
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ exp: t - 300 })))), null);
  // Inside the skew window a just-expired token is still honoured…
  const edge = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ exp: t - 30 }))));
  assert.equal(edge.email, "john@example.com");
  // …and a token with no exp at all is a permanent credential, so it is refused.
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ exp: undefined })))), null);
});

test("a not-yet-valid token (nbf in the future) is rejected", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const t = nowSec();
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ nbf: t + 600 })))), null);
  const withinSkew = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims({ nbf: t + 30 }))));
  assert.equal(withinSkew.email, "john@example.com");
});

test("a service-token assertion (common_name, no email) yields null, never a mapped identity", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const svc = await s.sign(goodClaims({ email: undefined, common_name: "ci-runner.access" }));
  assert.equal(await verifyAccessJwt(ENV, reqWith(svc)), null);
  const blank = await s.sign(goodClaims({ email: "   " }));
  assert.equal(await verifyAccessJwt(ENV, reqWith(blank)), null);
});

// ---- signature + algorithm attacks -----------------------------------------
test("a token signed by a DIFFERENT key than the kid names is rejected", async () => {
  const real = await makeSigner("key-1");
  const impostor = await makeSigner("key-1"); // same kid, different private key
  reset([real.jwk]);
  assert.equal(await verifyAccessJwt(ENV, reqWith(await impostor.sign(goodClaims()))), null);
});

test("a tampered payload invalidates the signature", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const token = await s.sign(goodClaims());
  const [h, , sig] = token.split(".");
  const swapped = `${h}.${b64urlJson(goodClaims({ email: "attacker@example.com" }))}.${sig}`;
  assert.equal(await verifyAccessJwt(ENV, reqWith(swapped)), null);
});

test("alg: none is rejected — the token never chooses the algorithm", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const claims = goodClaims();
  const unsigned = `${b64urlJson({ alg: "none", kid: "key-1", typ: "JWT" })}.${b64urlJson(claims)}.`;
  assert.equal(await verifyAccessJwt(ENV, reqWith(unsigned)), null);
  // The same shape with junk in the signature slot is equally dead.
  const junkSig = `${b64urlJson({ alg: "none", kid: "key-1" })}.${b64urlJson(claims)}.AAAA`;
  assert.equal(await verifyAccessJwt(ENV, reqWith(junkSig)), null);
});

test("HS256 signed with the RSA public key as the HMAC secret is rejected (algorithm confusion)", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  // The classic attack: take the PUBLIC key material (which the JWKS hands out
  // to anyone) and use it as a symmetric secret, betting the verifier reads
  // `alg` from the header. This verifier ignores the header entirely.
  const secret = enc.encode(s.jwk.n);
  const hmacKey = await crypto.subtle.importKey("raw", secret, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signingInput = `${b64urlJson({ alg: "HS256", kid: "key-1", typ: "JWT" })}.${b64urlJson(goodClaims())}`;
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", hmacKey, enc.encode(signingInput)));
  assert.equal(await verifyAccessJwt(ENV, reqWith(`${signingInput}.${b64url(mac)}`)), null);
});

test("a JWKS entry that is not RSA/RS256 is never imported", async () => {
  const s = await makeSigner("key-1");
  // The key set carries the right kid but an EC/oct shape: no import candidate,
  // so verification fails rather than falling back to some other algorithm.
  reset([{ kty: "EC", kid: "key-1", crv: "P-256", x: "abc", y: "def" }, { ...s.jwk, kid: "key-2", alg: "RS512" }]);
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))), null);
});

// ---- malformed input --------------------------------------------------------
test("garbage, missing, and malformed assertions are rejected without throwing", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  const junk = [
    undefined, "", "not-a-jwt", "a.b", "a.b.c.d", "....", "%%%.%%%.%%%",
    `${b64urlJson({ alg: "RS256" })}.${b64urlJson(goodClaims())}.AAAA`, // no kid
    `${b64urlJson({ alg: "RS256", kid: "key-1" })}.${b64url(enc.encode("not json"))}.AAAA`,
    `${b64url(enc.encode("[1,2,3]"))}.${b64urlJson(goodClaims())}.AAAA`,
  ];
  for (const t of junk) {
    assert.equal(await verifyAccessJwt(ENV, reqWith(t)), null, `expected null for ${String(t)}`);
  }
  // parseJws itself is total: it returns null, it does not throw.
  assert.equal(parseJws("nonsense"), null);
  assert.equal(parseJws(null), null);
});

// ---- JWKS cache + refresh ---------------------------------------------------
test("the JWKS is cached within its TTL — many verifications, one fetch", async () => {
  const s = await makeSigner("key-1");
  reset([s.jwk]);
  for (let i = 0; i < 4; i++) {
    const out = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims())));
    assert.equal(out.email, "john@example.com");
  }
  assert.equal(fetchCalls, 1);
  assert.ok(JWKS_TTL_MS >= 60 * 60 * 1000);
});

test("an unknown kid forces exactly ONE re-fetch, then rejects if still unknown", async () => {
  const s = await makeSigner("key-1");
  const rotated = await makeSigner("key-2");
  reset([s.jwk]);
  await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))); // warms the cache
  assert.equal(fetchCalls, 1);

  // The key set still does not carry key-2: one refresh, then a rejection.
  assert.equal(await verifyAccessJwt(ENV, reqWith(await rotated.sign(goodClaims()))), null);
  assert.equal(fetchCalls, 2, "exactly one forced re-fetch per verification attempt");
});

test("an unknown kid picks up a genuine key rotation on the forced re-fetch", async () => {
  const oldKey = await makeSigner("key-1");
  const newKey = await makeSigner("key-2");
  reset([oldKey.jwk]);
  await verifyAccessJwt(ENV, reqWith(await oldKey.sign(goodClaims())));
  assert.equal(fetchCalls, 1);

  serveKeys([oldKey.jwk, newKey.jwk]); // Access rotated
  const out = await verifyAccessJwt(ENV, reqWith(await newKey.sign(goodClaims())));
  assert.equal(out.email, "john@example.com");
  assert.equal(fetchCalls, 2);
});

// ---- transport failures are failures, not exceptions ------------------------
test("a JWKS fetch that rejects yields null and does not throw", async () => {
  const s = await makeSigner("key-1");
  reset();
  responder = async () => { throw new Error("network down"); };
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))), null);
  assert.equal(fetchCalls, 1);
});

test("a JWKS fetch that 500s, or returns junk, yields null", async () => {
  const s = await makeSigner("key-1");
  reset();
  responder = async () => new Response("upstream boom", { status: 500 });
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))), null);

  reset();
  responder = async () => new Response("<html>not json</html>", { status: 200 });
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))), null);

  reset();
  responder = async () => new Response(JSON.stringify({ keys: [] }), { status: 200 });
  assert.equal(await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))), null);
});

test("a FAILED refresh keeps serving the cached key set until the TTL expires", async () => {
  const s = await makeSigner("key-1");
  const rotated = await makeSigner("key-2");
  reset([s.jwk]);
  await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims()))); // warm cache

  // The Access edge goes down. An unknown kid forces a refresh, which fails —
  // but the cached key must keep working, or one blip logs everybody out.
  responder = async () => { throw new Error("edge unavailable"); };
  assert.equal(await verifyAccessJwt(ENV, reqWith(await rotated.sign(goodClaims()))), null);
  assert.equal(fetchCalls, 2);

  const stillGood = await verifyAccessJwt(ENV, reqWith(await s.sign(goodClaims())));
  assert.equal(stillGood.email, "john@example.com", "cached keys survive a failed refresh");
});

// ---- fail closed on config (this is what keeps PROD safe) -------------------
test("any of the three Access vars unset -> null, with ZERO fetch attempts", async () => {
  const s = await makeSigner("key-1");
  for (const missing of ["EDIT_ACCESS_AUD", "EDIT_ACCESS_TEAM_DOMAIN", "EDIT_ACCESS_HOST"]) {
    reset([s.jwk]);
    const env = { ...ENV };
    delete env[missing];
    assert.equal(await verifyAccessJwt(env, reqWith(await s.sign(goodClaims()))), null, `unset ${missing}`);
    assert.equal(fetchCalls, 0, `unset ${missing} must not reach the network`);

    // An empty or whitespace-only value is the same as unset.
    reset([s.jwk]);
    assert.equal(await verifyAccessJwt({ ...ENV, [missing]: "   " }, reqWith(await s.sign(goodClaims()))), null);
    assert.equal(fetchCalls, 0);
  }
  // The PROD shape: no Access config at all.
  reset([s.jwk]);
  assert.equal(await verifyAccessJwt({}, reqWith(await s.sign(goodClaims()))), null);
  assert.equal(fetchCalls, 0);
});

test("a request with no assertion header never reaches the network", async () => {
  reset([]);
  assert.equal(await verifyAccessJwt(ENV, reqWith(undefined)), null);
  assert.equal(fetchCalls, 0);
});

// ---- pure helpers -----------------------------------------------------------
test("readAccessConfig normalizes the team domain and refuses partial config", () => {
  assert.equal(readAccessConfig({ ...ENV, EDIT_ACCESS_TEAM_DOMAIN: `https://${TEAM}/` }).teamDomain, TEAM);
  assert.equal(readAccessConfig({ EDIT_ACCESS_AUD: AUD, EDIT_ACCESS_HOST: "x" }), null);
  assert.equal(readAccessConfig(undefined), null);
});

test("normalizeIssuer and timeClaimsValid behave as the verifier assumes", () => {
  assert.equal(normalizeIssuer("HTTPS://Team.CloudflareAccess.com/"), "team.cloudflareaccess.com");
  assert.equal(normalizeIssuer(42), "");
  const t = 1_800_000_000;
  assert.equal(timeClaimsValid({ exp: t + 10 }, t), true);
  assert.equal(timeClaimsValid({ exp: t - 30 }, t), true); // inside the 60s skew
  assert.equal(timeClaimsValid({ exp: t - 300 }, t), false);
  assert.equal(timeClaimsValid({ exp: t + 10, nbf: t + 600 }, t), false);
  assert.equal(timeClaimsValid({ exp: "soon" }, t), false);
});
