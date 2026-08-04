// Stateless, short-lived proof that an editor confirmed the exact scoped-change
// challenge the server showed. The client treats this value as opaque.

const enc = new TextEncoder();
const dec = new TextDecoder();
const TOKEN_VERSION = 1;
export const SCOPED_CONFIRMATION_TTL_MS = 10 * 60 * 1000;

function b64urlEncode(bytes) {
  let bin = "";
  for (const byte of bytes) bin += String.fromCharCode(byte);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(value) {
  const pad = value.length % 4 === 0 ? "" : "=".repeat(4 - (value.length % 4));
  const bin = atob(value.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (char) => char.charCodeAt(0));
}

async function signingKey(secret) {
  return crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]
  );
}

async function contextDigest(context) {
  const bytes = await crypto.subtle.digest("SHA-256", enc.encode(JSON.stringify(context)));
  return b64urlEncode(new Uint8Array(bytes));
}

export async function mintScopedConfirmation(secret, context, expiresAt = Date.now() + SCOPED_CONFIRMATION_TTL_MS) {
  const payload = b64urlEncode(enc.encode(JSON.stringify({
    v: TOKEN_VERSION,
    exp: expiresAt,
    context: await contextDigest(context),
  })));
  const signature = new Uint8Array(await crypto.subtle.sign(
    "HMAC", await signingKey(secret), enc.encode(payload)
  ));
  return payload + "." + b64urlEncode(signature);
}

export async function verifyScopedConfirmation(secret, token, context, now = Date.now()) {
  if (typeof secret !== "string" || !secret || typeof token !== "string") return false;
  const dot = token.indexOf(".");
  if (dot < 1 || dot !== token.lastIndexOf(".")) return false;
  const payload = token.slice(0, dot);
  try {
    const valid = await crypto.subtle.verify(
      "HMAC", await signingKey(secret), b64urlDecode(token.slice(dot + 1)), enc.encode(payload)
    );
    if (!valid) return false;
    const claims = JSON.parse(dec.decode(b64urlDecode(payload)));
    if (claims?.v !== TOKEN_VERSION || !Number.isFinite(claims.exp) || claims.exp <= now)
      return false;
    return claims.context === await contextDigest(context);
  } catch {
    return false;
  }
}
