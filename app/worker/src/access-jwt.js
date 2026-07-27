// access-jwt.js — verify a Cloudflare Access identity assertion
// (the `Cf-Access-Jwt-Assertion` header) with WebCrypto only.
//
// WHY HAND-ROLLED (KTD1): the Worker is a zero-build, zero-dependency bundle and
// adding an npm package on the AUTH path is a supply-chain decision, not a
// convenience one. RS256-against-a-JWKS is a small amount of code with
// crypto.subtle.importKey('jwk', …) + verify, and the module already lives next
// to editor-auth.js, which does fixed-algorithm HMAC the same way.
//
// WHAT A LIBRARY WOULD HAVE DONE FOR US, AND WE MUST DO OURSELVES:
//   * PIN THE ALGORITHM. The token's own `alg` header is attacker-controlled and
//     is IGNORED here, always. We import only RSA/RS256 JWKS entries and verify
//     with RSASSA-PKCS1-v1_5 + SHA-256. A verifier that reads `alg` from the
//     token accepts `alg: none` and the HS256-signed-with-the-public-key
//     confusion attack — both are covered by tests.
//   * NEVER LET THE TOKEN NAME ITS OWN KEY SOURCE. The team domain comes from
//     `EDIT_ACCESS_TEAM_DOMAIN` (config), never from the token's `iss` claim; a
//     token that chose its own JWKS host would simply sign itself.
//
// FAIL CLOSED ON CONFIG (KTD8): `EDIT_ACCESS_AUD`, `EDIT_ACCESS_TEAM_DOMAIN` and
// `EDIT_ACCESS_HOST` are declared vars. If ANY is missing we return null before
// touching the network. That is what keeps the PROD Worker — which deliberately
// declares none of them — safe rather than accidentally Access-authenticated.
//
// NEVER THROWS. A JWKS fetch that rejects, times out or returns non-200 is a
// failure, not an exception: an uncaught rejection here would surface as
// index.js's plain-text 500 instead of the editor's uniform 404, both leaking
// that the path exists and handing a signed-in user a hard error.
//
// NEVER LOGS. Same discipline as editor-auth.js: the assertion is a bearer
// credential for a human identity, so no part of it — not the token, not the
// email, not the kid — is ever written to a log line. There is no `console.` in
// this file, by design.

const enc = new TextEncoder();

// Access rotates its signing keys on the order of weeks, so an hour-long cache
// is safe; the unknown-`kid` force-refresh below is what actually covers a
// rotation, and the TTL is only a backstop (KTD4).
export const JWKS_TTL_MS = 60 * 60 * 1000;

// This fetch runs INLINE on the request path inside resolveAuth. A hung
// connection to the Access edge must fail the request closed in seconds, not
// stall an authenticated user for the platform's subrequest timeout.
export const JWKS_FETCH_TIMEOUT_MS = 3000;

// Clocks drift between the Access edge and the Worker isolate. 60s is a bound,
// not a loophole: a token expired by five minutes is still rejected.
export const CLOCK_SKEW_SEC = 60;

// Module-level (per-isolate) JWKS cache: { url, keys: Map<kid, JsonWebKey>, fetchedAt }.
// Keyed by URL so a config change can never serve keys from the previous team
// domain. Nothing secret lives here — a JWKS is public by definition.
let jwksCache = null;

// Tests reach into the module cache; production code never calls this.
export function __resetJwksCache() {
  jwksCache = null;
}

// ---- config (declared, never derived from the token) ------------------------
// Returns { aud, teamDomain, host } or null when ANY of the three is missing.
// All three are read together so a half-configured Worker behaves exactly like
// an unconfigured one — there is no partially-armed state.
export function readAccessConfig(env) {
  const aud = typeof env?.EDIT_ACCESS_AUD === "string" ? env.EDIT_ACCESS_AUD.trim() : "";
  const rawDomain = typeof env?.EDIT_ACCESS_TEAM_DOMAIN === "string" ? env.EDIT_ACCESS_TEAM_DOMAIN.trim() : "";
  const host = typeof env?.EDIT_ACCESS_HOST === "string" ? env.EDIT_ACCESS_HOST.trim() : "";
  if (!aud || !rawDomain || !host) return null;
  const teamDomain = normalizeIssuer(rawDomain);
  if (!teamDomain) return null;
  return { aud, teamDomain, host };
}

// Strip scheme and trailing slash so a config value written as
// "team.cloudflareaccess.com" compares equal to an `iss` of
// "https://team.cloudflareaccess.com". Lowercased: hostnames are case-insensitive.
export function normalizeIssuer(value) {
  if (typeof value !== "string") return "";
  return value.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/+$/, "");
}

// ---- base64url + JWS parsing (pure, total, never throws) --------------------
function b64urlDecode(str) {
  const pad = str.length % 4 === 0 ? "" : "=".repeat(4 - (str.length % 4));
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64urlJson(str) {
  return JSON.parse(new TextDecoder().decode(b64urlDecode(str)));
}

// Split a compact JWS into its parts WITHOUT trusting any of them yet. Returns
// { header, payload, signature, signingInput } or null for anything malformed —
// garbage in the header must be a rejection, never an exception.
export function parseJws(token) {
  if (typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [h, p, s] = parts;
  if (!h || !p) return null; // an empty signature is allowed here; verify() rejects it
  if (!/^[A-Za-z0-9_-]+$/.test(h) || !/^[A-Za-z0-9_-]+$/.test(p)) return null;
  if (s && !/^[A-Za-z0-9_-]+$/.test(s)) return null;
  let header, payload, signature;
  try {
    header = b64urlJson(h);
    payload = b64urlJson(p);
    signature = b64urlDecode(s);
  } catch {
    return null;
  }
  if (!header || typeof header !== "object" || Array.isArray(header)) return null;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  return { header, payload, signature, signingInput: `${h}.${p}` };
}

// ---- JWKS retrieval ---------------------------------------------------------
// Fetch + shape-check the key set. Returns a Map<kid, JsonWebKey> or null on ANY
// transport or shape failure. Only RSA keys usable with RS256 are kept: an EC or
// oct entry that slipped into the key set must never become an import candidate,
// because the import parameters are what pin the algorithm.
async function fetchJwks(url) {
  let res;
  try {
    res = await fetch(url, {
      signal: AbortSignal.timeout(JWKS_FETCH_TIMEOUT_MS),
      headers: { Accept: "application/json" },
    });
  } catch {
    return null;
  }
  if (!res || !res.ok) return null;
  let body;
  try {
    body = await res.json();
  } catch {
    return null;
  }
  const list = Array.isArray(body?.keys) ? body.keys : null;
  if (!list) return null;
  const keys = new Map();
  for (const jwk of list) {
    if (!jwk || typeof jwk !== "object") continue;
    if (jwk.kty !== "RSA") continue;
    if (typeof jwk.alg === "string" && jwk.alg !== "RS256") continue;
    if (typeof jwk.use === "string" && jwk.use !== "sig") continue;
    if (typeof jwk.kid !== "string" || !jwk.kid) continue;
    if (typeof jwk.n !== "string" || typeof jwk.e !== "string") continue;
    if (!keys.has(jwk.kid)) keys.set(jwk.kid, jwk);
  }
  return keys.size > 0 ? keys : null;
}

// Return the key set for a team domain, fetching when the cache is cold, stale,
// or a force-refresh was requested (unknown `kid` -> a rotation may have landed).
// `attempt.fetched` makes "at most one network round trip per verification" a
// property of the code rather than of the call sites.
//
// A FAILED REFRESH KEEPS THE OLD KEYS until their TTL expires: an Access edge
// blip must not lock out every signed-in editor for as long as it lasts.
async function getJwks(teamDomain, attempt, force) {
  const url = `https://${teamDomain}/cdn-cgi/access/certs`;
  const now = Date.now();
  const cached = jwksCache && jwksCache.url === url ? jwksCache : null;
  const fresh = cached && now - cached.fetchedAt < JWKS_TTL_MS;
  if (fresh && !force) return cached.keys;
  if (attempt.fetched) return cached && fresh ? cached.keys : null;
  attempt.fetched = true;
  const keys = await fetchJwks(url);
  if (keys) {
    jwksCache = { url, keys, fetchedAt: now };
    return keys;
  }
  return fresh ? cached.keys : null;
}

// ---- signature verification (algorithm pinned, not negotiated) --------------
async function verifyRs256(jwk, signature, signingInput) {
  try {
    // The import parameters — NOT the token header — decide the algorithm. We
    // strip whatever `alg`/`key_ops`/`ext` the key set carried and restate the
    // one algorithm this module will ever accept.
    const key = await crypto.subtle.importKey(
      "jwk",
      { kty: "RSA", n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["verify"],
    );
    return await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, signature, enc.encode(signingInput));
  } catch {
    return false;
  }
}

// ---- claim checks -----------------------------------------------------------
// Portable constant-time string compare, mirroring editor-auth.js: digest both
// sides to a fixed length and XOR-accumulate, never a length-leaking early
// return. The AUD tag is not a password, but it IS the value that decides which
// application a token is good for, so it gets the same treatment as the other
// secret-adjacent comparisons in this codebase.
async function constantTimeEqualStr(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const [da, db] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  const x = new Uint8Array(da);
  const y = new Uint8Array(db);
  let diff = x.length ^ y.length;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

// `aud` is a string OR an array of strings per RFC 7519. Every candidate is
// compared without short-circuiting.
async function audienceMatches(aud, expected) {
  const list = typeof aud === "string" ? [aud] : Array.isArray(aud) ? aud : [];
  let matched = false;
  for (const candidate of list) {
    const eq = await constantTimeEqualStr(candidate, expected);
    matched = matched || eq;
  }
  return matched;
}

// exp is REQUIRED: a token with no expiry is a permanent credential and this
// module will not mint one by omission. nbf is optional but honored when present.
export function timeClaimsValid(payload, nowSec = Math.floor(Date.now() / 1000)) {
  const { exp, nbf } = payload;
  if (typeof exp !== "number" || !Number.isFinite(exp)) return false;
  if (nowSec > exp + CLOCK_SKEW_SEC) return false;
  if (nbf !== undefined) {
    if (typeof nbf !== "number" || !Number.isFinite(nbf)) return false;
    if (nowSec < nbf - CLOCK_SKEW_SEC) return false;
  }
  return true;
}

// ---- the door ---------------------------------------------------------------
// Returns { email } for a genuine, current Access identity; null for EVERY other
// outcome. Deliberately no error detail and no distinguishable timing story:
// "bad signature", "wrong audience" and "no header at all" are the same answer,
// because the caller turns any of them into the uniform 404 (R6).
export async function verifyAccessJwt(env, request) {
  try {
    // Config first: an unconfigured Worker must not even resolve DNS.
    const cfg = readAccessConfig(env);
    if (!cfg) return null;

    const token = request?.headers?.get?.("Cf-Access-Jwt-Assertion");
    if (typeof token !== "string" || !token) return null;

    const jws = parseJws(token);
    if (!jws) return null;
    if (jws.signature.length === 0) return null; // `alg: none` shaped tokens die here too
    const kid = jws.header.kid;
    if (typeof kid !== "string" || !kid) return null;

    // One network round trip per verification, at most: cold/stale cache fetches
    // once; a warm cache that lacks the kid forces exactly one refresh.
    const attempt = { fetched: false };
    let keys = await getJwks(cfg.teamDomain, attempt, false);
    let jwk = keys ? keys.get(kid) : null;
    if (!jwk) {
      keys = await getJwks(cfg.teamDomain, attempt, true);
      jwk = keys ? keys.get(kid) : null;
    }
    if (!jwk) return null;

    if (!(await verifyRs256(jwk, jws.signature, jws.signingInput))) return null;

    const payload = jws.payload;
    if (!(await audienceMatches(payload.aud, cfg.aud))) return null;
    if (normalizeIssuer(payload.iss) !== cfg.teamDomain) return null;
    if (!timeClaimsValid(payload)) return null;

    // A service-token assertion is genuine but carries `common_name`, not
    // `email` — there is no human to map to a slot, so it is not an identity
    // this door opens. Never return a verified-but-unmapped state.
    const email = typeof payload.email === "string" ? payload.email.trim().toLowerCase() : "";
    if (!email) return null;

    return { email };
  } catch {
    // Belt and braces: every internal path already returns null, and this catch
    // exists so a future edit cannot make this module throw into index.js.
    return null;
  }
}
