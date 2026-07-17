// session.js — stateless HMAC-signed session token (Web Crypto only).
//
// The token authenticates a `sid` cheaply and unforgeably at GET /v1/session
// with no DO round-trip to mint. Payload is {sid, d} (d = UTC day the token was
// minted); signature = HMAC-SHA256(SESSION_SIGNING_KEY, payload). The turn count
// is NEVER in the token (the client would replay a low count) — the authoritative
// turn counter lives in the BudgetCounter DO, keyed by sid. Signed token =
// identity; DO = enforcement. sids come from crypto.randomUUID(), never Math.random.

function b64urlEncode(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(str) {
  const pad = str.length % 4 === 0 ? "" : "=".repeat(4 - (str.length % 4));
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

const enc = new TextEncoder();

async function importHmacKey(signingKey) {
  return crypto.subtle.importKey(
    "raw",
    enc.encode(signingKey),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

// Today's UTC day string "YYYY-MM-DD".
export function utcDay(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

// Mint a signed token for a fresh sid. Payload {sid, d, p} — p is the spend pool
// ("public" | "demo") decided at mint time and carried unforgeably so /chat knows
// which counter to bill (the client can't upgrade itself to the demo reserve).
// Returns { token, sid }.
export async function mintSession(signingKey, opts = {}) {
  const sid = opts.sid || crypto.randomUUID();
  const day = opts.day || utcDay();
  const pool = opts.pool === "demo" ? "demo" : "public";
  const payload = b64urlEncode(enc.encode(JSON.stringify({ sid, d: day, p: pool })));
  const key = await importHmacKey(signingKey);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(payload)));
  return { token: payload + "." + b64urlEncode(sig), sid };
}

// Verify a token. Returns { sid, d } on success, or null on any failure
// (malformed, bad signature, tampered payload). Uses crypto.subtle.verify, which
// is a constant-time comparison.
export async function verifySession(signingKey, token) {
  if (typeof token !== "string") return null;
  const dot = token.indexOf(".");
  if (dot < 1 || dot !== token.lastIndexOf(".")) return null;
  const payloadPart = token.slice(0, dot);
  const sigPart = token.slice(dot + 1);
  let sig;
  try {
    sig = b64urlDecode(sigPart);
  } catch {
    return null;
  }
  const key = await importHmacKey(signingKey);
  let ok;
  try {
    ok = await crypto.subtle.verify("HMAC", key, sig, enc.encode(payloadPart));
  } catch {
    return null;
  }
  if (!ok) return null;
  try {
    const obj = JSON.parse(new TextDecoder().decode(b64urlDecode(payloadPart)));
    if (!obj || typeof obj.sid !== "string" || typeof obj.d !== "string") return null;
    obj.p = obj.p === "demo" ? "demo" : "public";
    return obj;
  } catch {
    return null;
  }
}

// Constant-time compare of the demo bypass token against the secret. Uses
// crypto.subtle.timingSafeEqual on equal-length digests (never raw ===). Digesting
// first keeps the comparison length-independent.
export async function timingSafeEqualStr(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const [da, db] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  return crypto.subtle.timingSafeEqual(da, db);
}
