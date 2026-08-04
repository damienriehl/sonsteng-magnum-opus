// Stateless confirmation tokens for over-ceiling scoped changes. The payload is
// client-opaque and HMAC-authenticated with a domain-separated use of the
// session signing key. It binds approval to the exact normalized request the
// server enumerated, and expires without requiring Durable Object storage.

const enc = new TextEncoder();
const dec = new TextDecoder();
const TOKEN_VERSION = 1;
export const SCOPED_CONFIRMATION_TTL_MS = 10 * 60 * 1000;

function b64urlEncode(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(value) {
  const pad = value.length % 4 === 0 ? "" : "=".repeat(4 - (value.length % 4));
  const bin = atob(value.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function keyFor(signingKey) {
  return crypto.subtle.importKey(
    "raw", enc.encode("scoped-confirmation-v1:" + signingKey),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]
  );
}

function committedRequest({ level, matter, part, module, instruction, radius }) {
  return {
    level,
    matter: matter ?? null,
    part: part ?? null,
    module: module ?? null,
    instruction,
    radius: {
      blocks: radius.blocks,
      files: radius.files,
      matters: radius.matters.length,
    },
  };
}

export async function mintScopedConfirmationToken(signingKey, request, opts = {}) {
  const now = opts.now ?? Date.now();
  const expiresAt = opts.expiresAt ?? now + SCOPED_CONFIRMATION_TTL_MS;
  const payload = b64urlEncode(enc.encode(JSON.stringify({
    v: TOKEN_VERSION,
    exp: expiresAt,
    request: committedRequest(request),
  })));
  const key = await keyFor(signingKey);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(payload)));
  return payload + "." + b64urlEncode(signature);
}

export async function verifyScopedConfirmationToken(signingKey, token, request, now = Date.now()) {
  if (typeof token !== "string") return false;
  const dot = token.indexOf(".");
  if (dot < 1 || dot !== token.lastIndexOf(".")) return false;
  const payloadPart = token.slice(0, dot);
  let signature;
  try { signature = b64urlDecode(token.slice(dot + 1)); } catch { return false; }

  const key = await keyFor(signingKey);
  try {
    if (!await crypto.subtle.verify("HMAC", key, signature, enc.encode(payloadPart))) return false;
  } catch { return false; }

  try {
    const payload = JSON.parse(dec.decode(b64urlDecode(payloadPart)));
    if (payload?.v !== TOKEN_VERSION || !Number.isFinite(payload.exp) || payload.exp <= now)
      return false;
    return JSON.stringify(payload.request) === JSON.stringify(committedRequest(request));
  } catch { return false; }
}
