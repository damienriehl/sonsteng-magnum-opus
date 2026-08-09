// editor-auth.js — opaque bookmark token -> server-side scope record, and the
// ?t=<opaque> -> HttpOnly cookie exchange.
//
// SCOPE MODEL (Decision 2 + Enhancement item 2):
//   * One opaque token maps to a scope record:
//       { edit: {granted, ver}, instructor: {granted, ver}, admin: {granted, ver} }
//   * John's single bookmark carries edit+instructor (one token, two scopes);
//     Damien's admin token carries admin only. admin is NEVER reachable from an
//     edit/instructor token (separate secret, separate record).
//   * Scopes rotate INDEPENDENTLY via a per-scope version. Rotating a scope (bump
//     its ver in EDIT_TOKEN_SCOPES) invalidates every already-issued cookie for
//     that token (the mint stamp no longer matches) — John re-clicks his magic
//     link, which carries the rotated secret.
//
// WHERE STORED: tokens are deploy SECRETS, not DO/KV rows (documented in
//   API-CONTRACTS.md). A slot NAME (e.g. "john", "admin") maps to a secret
//   env["EDIT_TOKEN_" + NAME.toUpperCase()] (the opaque value) and to a scope
//   grant in the JSON var EDIT_TOKEN_SCOPES:
//     { "john": {"edit":1,"instructor":1}, "admin": {"admin":1} }
//   The cookie carries only the slot name + a version stamp, HMAC-signed with
//   SESSION_SIGNING_KEY — never the raw token.
//
// The raw token and the cookie value are NEVER logged (enforced by the source
// scan in test/editor-security.test.js).

// The Access door's JWT verifier. It fails closed on unset config, so importing
// it here costs nothing in an env that has no Access application (plan KTD8).
import { verifyAccessJwt } from "./access-jwt.js";

const enc = new TextEncoder();

// Portable constant-time string compare (Workers AND node): digest both inputs
// to fixed-length SHA-256, then XOR-accumulate every byte (never a length-leaking
// early return). Avoids the Workers-only crypto.subtle.timingSafeEqual so the
// same code path is exercised in unit tests and in production.
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

const EMPTY_SCOPES = Object.freeze({
  edit: { granted: false, ver: 0 },
  instructor: { granted: false, ver: 0 },
  admin: { granted: false, ver: 0 },
  publisher: { granted: false, ver: 0 },
});

// Parse EDIT_TOKEN_SCOPES (JSON) into a Map slot -> { edit?:ver, instructor?:ver, admin?:ver }.
function parseScopeConfig(env) {
  let cfg;
  try {
    cfg = JSON.parse(env.EDIT_TOKEN_SCOPES || "{}");
  } catch {
    return new Map();
  }
  const out = new Map();
  for (const [slot, scopes] of Object.entries(cfg)) {
    if (!/^[a-z0-9_]+$/i.test(slot)) continue; // slot names are simple identifiers
    const rec = {};
    for (const s of Object.keys(EMPTY_SCOPES)) {
      if (scopes && typeof scopes[s] === "number") rec[s] = scopes[s];
    }
    out.set(slot.toLowerCase(), rec);
  }
  return out;
}

// Build the { edit, instructor, admin } record from a slot's granted scopes.
function scopeRecord(grants) {
  const rec = Object.fromEntries(Object.keys(EMPTY_SCOPES)
    .map((scope) => [scope, { granted: false, ver: 0 }]));
  for (const s of Object.keys(EMPTY_SCOPES)) {
    if (grants && typeof grants[s] === "number") rec[s] = { granted: true, ver: grants[s] };
  }
  return rec;
}

// A short stable stamp binding a slot to its current scope versions. Bumping any
// version changes the stamp, invalidating cookies minted under the old versions.
function scopeStamp(slot, grants) {
  // Preserve pre-Publisher cookies for slots whose grant record did not change;
  // append the new scope only when it exists, while still binding its version.
  const names = ["edit", "instructor", "admin"];
  if (grants.publisher != null) names.push("publisher");
  const parts = names.map((s) => `${s}:${grants[s] ?? "-"}`);
  return `${slot}|${parts.join(",")}`;
}

// ---- opaque token -> scope record (constant-time) ---------------------------
// Compares the presented token against EVERY configured slot secret WITHOUT
// short-circuiting (so timing does not reveal which slot, or whether any,
// matched). Returns { slot, record, stamp } or null.
export async function resolveOpaqueToken(env, presented) {
  if (typeof presented !== "string" || presented.length === 0) return null;
  const config = parseScopeConfig(env);
  let matched = null;
  for (const [slot, grants] of config) {
    const secret = env["EDIT_TOKEN_" + slot.toUpperCase()];
    if (typeof secret !== "string" || secret.length === 0) {
      // Still spend a comparison to keep the loop timing uniform.
      await constantTimeEqualStr(presented, presented + "\u0000");
      continue;
    }
    const eq = await constantTimeEqualStr(presented, secret);
    if (eq && !matched) {
      matched = { slot, record: scopeRecord(grants), stamp: scopeStamp(slot, grants) };
    }
  }
  return matched;
}

// ---- signed cookie (slot + stamp, never the raw token) ----------------------
async function importKey(signingKey) {
  return crypto.subtle.importKey(
    "raw", enc.encode(signingKey), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]
  );
}
function b64url(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64urlDecode(str) {
  const pad = str.length % 4 === 0 ? "" : "=".repeat(4 - (str.length % 4));
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export const COOKIE_NAME = "edit_scope";

// Mint the cookie VALUE: b64url(JSON{slot, stamp, d}) + "." + b64url(HMAC).
export async function mintCookieValue(signingKey, { slot, stamp }) {
  const payload = b64url(enc.encode(JSON.stringify({ slot, stamp, d: new Date().toISOString().slice(0, 10) })));
  const key = await importKey(signingKey);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(payload)));
  return payload + "." + b64url(sig);
}

// Verify + parse the cookie value. Returns { slot, stamp, d } or null.
export async function verifyCookieValue(signingKey, value) {
  if (typeof value !== "string") return null;
  const dot = value.indexOf(".");
  if (dot < 1 || dot !== value.lastIndexOf(".")) return null;
  const payloadPart = value.slice(0, dot);
  let sig;
  try {
    sig = b64urlDecode(value.slice(dot + 1));
  } catch {
    return null;
  }
  const key = await importKey(signingKey);
  let ok;
  try {
    ok = await crypto.subtle.verify("HMAC", key, sig, enc.encode(payloadPart));
  } catch {
    return null;
  }
  if (!ok) return null;
  try {
    const obj = JSON.parse(new TextDecoder().decode(b64urlDecode(payloadPart)));
    if (!obj || typeof obj.slot !== "string" || typeof obj.stamp !== "string") return null;
    return obj;
  } catch {
    return null;
  }
}

// Read the edit_scope cookie from a request. Returns the raw value or null.
export function readCookie(request, name = COOKIE_NAME) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(/;\s*/)) {
    const eq = part.indexOf("=");
    if (eq > 0 && part.slice(0, eq) === name) return part.slice(eq + 1);
  }
  return null;
}

// The Set-Cookie header string for the exchange: HttpOnly; Secure; SameSite=Strict;
// Path=/edit so it reaches BOTH the /edit pages AND /edit/v1/* endpoints.
export function buildSetCookie(value, { maxAge = 60 * 60 * 24 * 14 } = {}) {
  // SameSite=Lax (not Strict): John reaches the editor by clicking a bookmark to
  // /edit/…?t=<token>, which 302-redirects to the clean URL. Real browsers do NOT
  // send a just-set Strict cookie across that redirect hop, so Strict silently
  // 404s the injected page (found in live browser UAT; curl's cookie jar ignores
  // SameSite and masked it). Lax sends on top-level GET navigations (the bookmark
  // + redirect) but STILL blocks cross-site POST — and the mutation endpoints
  // carry independent CSRF defense anyway (the X-Edit-Request custom header +
  // Origin/Sec-Fetch-Site check in editor-http.js), so the CSRF posture is
  // unchanged. HttpOnly + Secure + Path=/edit are retained.
  return `${COOKIE_NAME}=${value}; HttpOnly; Secure; SameSite=Lax; Path=/edit; Max-Age=${maxAge}`;
}

// Resolve the CURRENT scope record for a request from its cookie. Re-derives
// grants from live config each request (independent rotation): if the cookie's
// stamp no longer matches the slot's current stamp, the cookie is stale -> no
// scopes. Returns the { edit, instructor, admin } record (all-false if none).
export async function resolveRequestScopes(env, request) {
  const raw = readCookie(request);
  if (!raw) return { ...EMPTY_SCOPES };
  const parsed = await verifyCookieValue(env.SESSION_SIGNING_KEY, raw);
  if (!parsed) return { ...EMPTY_SCOPES };
  const config = parseScopeConfig(env);
  const grants = config.get(parsed.slot);
  if (!grants) return { ...EMPTY_SCOPES };
  if (scopeStamp(parsed.slot, grants) !== parsed.stamp) return { ...EMPTY_SCOPES }; // rotated
  return scopeRecord(grants);
}

// Full auth context for a request: the scope record, the slot name, and the
// server-controlled editor identity ("slot:<name>"). The editor id is NEVER
// taken from the client body — suggest() stamps it from here. Returns all-false
// scopes + editor:null when there is no valid cookie.
export async function resolveAuth(env, request) {
  // 1) Browser auth: the HMAC-signed cookie from the ?t= exchange (wins when
  //    valid). 2) Service/CI auth: an opaque token via `Authorization: Bearer`
  //    (the apply engine). Bearer is CSRF-safe — a browser never auto-attaches
  //    it — so a leaked-cookie CSRF cannot forge it. We fall through to Bearer
  //    when the cookie is ABSENT *or INVALID* (a service client may also carry a
  //    non-signed cookie, which must not shadow its Bearer credential).
  const raw = readCookie(request);
  if (raw) {
    const parsed = await verifyCookieValue(env.SESSION_SIGNING_KEY, raw);
    if (parsed) {
      const config = parseScopeConfig(env);
      const grants = config.get(parsed.slot);
      if (grants && scopeStamp(parsed.slot, grants) === parsed.stamp) {
        return { scopes: scopeRecord(grants), slot: parsed.slot, editor: `slot:${parsed.slot}`,
          credential_channel: "cookie" };
      }
    }
  }
  const authz = request.headers.get("authorization") || "";
  const m = /^Bearer\s+(.+)$/i.exec(authz);
  if (m) {
    const svc = await resolveOpaqueToken(env, m[1].trim());
    if (svc) return { scopes: svc.record, slot: svc.slot, editor: `slot:${svc.slot}`,
      credential_channel: "bearer" };
  }
  // 3) Cloudflare Access: a verified identity on the Access-gated hostname. This
  //    is a THIRD way to arrive at a slot, not a replacement — everything above
  //    is byte-for-byte what it was, which is what makes the both-doors promise
  //    true by construction rather than by discipline (plan KTD2, R4). It runs
  //    LAST so neither the cookie nor the apply daemon's Bearer credential can be
  //    shadowed by it.
  //
  //    The host gate is load-bearing, not decoration (plan KTD3): the workers.dev
  //    origin stays directly reachable and anyone can forge a
  //    Cf-Access-Jwt-Assertion header on it, so header presence ALONE must never
  //    grant anything. Only a request that actually arrived on the Access
  //    hostname — where Cloudflare, not the caller, sets that header — is
  //    eligible. verifyAccessJwt also fails closed when the Access config vars
  //    are unset, so an env without them (PROD, R7) can never take this branch.
  //
  //    No cookie is minted here. Identity is re-verified per request, which is
  //    what makes removing an address from EDIT_ACCESS_EMAILS an INSTANT
  //    revocation lever — an Access policy edit alone leaves an existing session
  //    working for the life of its 30-day session.
  const accessHost = env.EDIT_ACCESS_HOST;
  if (accessHost) {
    let host = "";
    try {
      host = new URL(request.url).host;
    } catch {
      host = "";
    }
    if (host && host === accessHost) {
      const identity = await verifyAccessJwt(env, request);
      if (identity && identity.email) {
        const slot = lookupAccessSlot(env, identity.email);
        if (slot) {
          const config = parseScopeConfig(env);
          const grants = config.get(slot);
          // A mapped email whose slot has no scope record grants nothing — the
          // same all-false result as any unauthorized request, so a stale map
          // entry can never become a silent partial grant.
          if (grants) {
            return { scopes: scopeRecord(grants), slot, editor: `slot:${slot}`,
              credential_channel: "access" };
          }
        }
      }
    }
  }
  return { scopes: { ...EMPTY_SCOPES }, slot: null, editor: null,
    credential_channel: "none" };
}

// ---- Access email -> slot ---------------------------------------------------
// EDIT_ACCESS_EMAILS is a SECRET (`wrangler secret put`), not a var: it holds
// collaborators' personal addresses, and this repository is headed for public
// release while git history is permanent. It is JSON mapping a lowercased email
// to one of the slot names EDIT_TOKEN_SCOPES already grants scopes to.
//
// Returns the slot name, or null for any address that is absent, for malformed
// config, or for a non-string input — never a partial or guessed match.
export function lookupAccessSlot(env, email) {
  if (typeof email !== "string" || !email) return null;
  let map;
  try {
    map = JSON.parse(env.EDIT_ACCESS_EMAILS || "{}");
  } catch {
    return null;
  }
  if (!map || typeof map !== "object") return null;
  // Lowercase BOTH sides. A mixed-case `email` claim is the same person; failing
  // to normalize would lock them out rather than expose anything, but a lockout
  // on first sign-in is exactly the failure this door exists to remove.
  const key = email.trim().toLowerCase();
  const slot = map[key];
  if (typeof slot !== "string" || !/^[a-z0-9_]+$/i.test(slot)) return null;
  return slot.toLowerCase();
}

// ---- attribution labels (slot -> reviewer initials) -------------------------
// The suggestion row's server-resolved `editor` identity is "slot:<name>" (e.g.
// "slot:john"). The admin review surface shows a short human attribution — John
// O. Sonsteng -> "JOS", Roger S. Haydock -> "RSH". Unknown slots fall back to the
// upper-cased slot name so a new editor is never mis-attributed to someone else.
// This is the SOLE place slot identity becomes a display label; the review
// endpoint stamps `attribution` onto each row from here (never trusts the client).
// `damienadmin` is the Access-only combined-scope slot (edit+instructor+admin).
// It shares Damien's DR attribution deliberately: it is the same human, reached
// through a different door, and an edit he makes must read as DR whether he
// arrived by token or by Access — R3 requires attribution to be unchanged.
const EDITOR_LABELS = Object.freeze({
  john: "JOS",
  roger: "RSH",
  damien: "DR",
  damienadmin: "DR",
  admin: "ADM",
});

// Derive the attribution label from an `editor` identity ("slot:<name>") or a
// bare slot name. Returns "" for a missing/invalid identity.
export function attributionLabel(editorOrSlot) {
  if (typeof editorOrSlot !== "string" || !editorOrSlot) return "";
  const slot = (editorOrSlot.startsWith("slot:") ? editorOrSlot.slice(5) : editorOrSlot).toLowerCase();
  if (!slot) return "";
  return EDITOR_LABELS[slot] || slot.toUpperCase();
}

export { parseScopeConfig, scopeStamp, scopeRecord, EMPTY_SCOPES, EDITOR_LABELS };
