// editor-http.js — HTTP helpers for the /edit surface: the edit-only CORS
// allowlist, the CSRF guard for mutations, the strict security headers every
// /edit response carries, and the uniform-404 + typed error envelope.
//
// The /edit CORS allowlist is the worker's OWN edit origin ONLY (env.EDIT_ORIGIN)
// — never the public site origins. /edit pages are same-origin with /edit/v1/*,
// so this is defense in depth; combined with the custom-header CSRF check it
// makes a cross-site POST impossible (the browser would need a preflight our CORS
// never grants, AND a custom header it cannot set cross-origin).

import { json } from "./errors.js";

// Content-Security-Policy for injected/served /edit HTML (Enhancement item 2):
// nothing loads except from our own origin; no inline script; no framing.
export const EDIT_CSP =
  "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; " +
  "img-src 'self' data:; font-src 'self'; base-uri 'self'; form-action 'self'; " +
  "frame-ancestors 'none'";

// Headers applied to EVERY /edit response (HTML and JSON, success and error).
export function editSecurityHeaders(extra = {}) {
  return {
    "Content-Security-Policy": EDIT_CSP,
    "Cache-Control": "private, no-store",
    "Vary": "Cookie",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    ...extra,
  };
}

// The single edit origin (worker's own). Falls back to the request origin's
// scheme+host when EDIT_ORIGIN is unset in dev.
export function editOrigin(env) {
  return env.EDIT_ORIGIN || "";
}

// CORS headers for an XHR from the edit page. Only the edit origin is echoed;
// any other Origin gets no ACAO (blocked). Same-origin navigations send no
// Origin and need none.
export function editCorsHeaders(env, requestOrigin) {
  const allow = editOrigin(env);
  if (!requestOrigin || !allow || requestOrigin !== allow) return {};
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, x-edit-request",
    "Access-Control-Max-Age": "600",
    "Vary": "Origin, Cookie",
  };
}

// Wrap a Response with the edit security headers (+ optional CORS for the edit
// origin). MUST wrap every /edit return, success and error.
export function withEditHeaders(response, env, requestOrigin) {
  const headers = new Headers(response.headers);
  for (const [k, v] of Object.entries(editSecurityHeaders())) headers.set(k, v);
  for (const [k, v] of Object.entries(editCorsHeaders(env, requestOrigin))) headers.set(k, v);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

// CSRF guard for state-changing /edit/v1 requests. Requires:
//   * a custom header X-Edit-Request: 1 (cannot be set cross-origin without a
//     preflight our CORS never grants for foreign origins), AND
//   * if an Origin header is present, it must equal the edit origin, AND
//   * if Sec-Fetch-Site is present, it must be "same-origin".
export function csrfOk(request, env) {
  if (request.headers.get("X-Edit-Request") !== "1") return false;
  const allow = editOrigin(env);
  const origin = request.headers.get("Origin");
  if (origin && allow && origin !== allow) return false;
  const site = request.headers.get("Sec-Fetch-Site");
  if (site && site !== "same-origin") return false;
  return true;
}

// Typed JSON error for /edit/v1 (mirrors errors.js envelope shape).
export function editError(code, message, status) {
  return json({ error: { code, message } }, status);
}

// Uniform 404 for unknown proxy paths, missing instructor docs, AND insufficient
// scope — IDENTICAL body + status for all three so nothing is an oracle.
export function uniform404() {
  return new Response("Not found.", {
    status: 404,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}
