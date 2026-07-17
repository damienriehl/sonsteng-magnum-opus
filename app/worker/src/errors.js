// errors.js — the typed error envelope + JSON response helpers. No cloudflare
// imports, so it is unit-testable under node. Every non-200 the Worker returns
// uses errorEnvelope, and (for allowlisted origins) is wrapped by withCors so the
// browser never masks it as an opaque CORS error.
//
// Envelope shape (API-CONTRACTS.md): { error: { code, message, in_character? } }.
// in_character is supplied for cap_exceeded / turn_limit / upstream_unavailable so
// the UI can show the client's in-character line instead of a raw error.

export const ERROR_CODES = new Set([
  "cap_exceeded",
  "turn_limit",
  "rate_limited",
  "validation_error",
  "upstream_unavailable",
  "origin_forbidden",
  "session_invalid",
  "no_hosted_key",
]);

export const IN_CHARACTER = {
  cap_exceeded: "I'm sorry — I've run out of time today and need to get going. Thank you, this was helpful.",
  turn_limit: "I've kept you a while and I really should get going. Thank you — this gave me a lot to think about.",
  upstream_unavailable: "Sorry — you're cutting out, I can barely hear you. This connection is terrible. Can we try again in a moment?",
};

export function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function errorEnvelope(code, message, status, extra = {}) {
  const err = { code, message };
  if (IN_CHARACTER[code]) err.in_character = IN_CHARACTER[code];
  if (extra.in_character) err.in_character = extra.in_character;
  return json({ error: err }, status);
}
