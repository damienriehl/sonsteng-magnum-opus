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
// everything same-origin. SCRIPTS stay STRICT ('self' only, NO unsafe-inline) —
// the injector strips the wrapped page's own scripts, so only the worker-served
// editor.js runs. STYLE is relaxed to 'unsafe-inline' because the generated
// student pages legitimately carry inline <style> blocks and style= attributes;
// this does NOT reopen the suggestion-XSS hole (suggestion/comment content is
// rendered by editor.js via textContent from the JSON island, never inline-
// interpolated). Fonts are base64 data: URIs inside fonts.css (font-src data:);
// the shared theme/fonts/platform CSS is proxied same-origin under /edit/site-
// assets/ so style-src 'self' still covers it.
export const EDIT_CSP =
  "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; " +
  "base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'";

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
//
// The body is uniform, NOT silent. It used to be the two words "Not found.",
// which is the correct answer to a probe and the wrong answer to Prof. Sonsteng:
// the ?t= token is stripped from the address bar once the session cookie is set,
// so anyone who bookmarks the page AFTER arriving has bookmarked a URL that works
// only until the cookie lapses — and then meets a blank white "Not found." with
// nothing to do about it. (Damien hit exactly this on 2026-07-27.)
//
// The page below is byte-identical for a valid path, an invalid path, a hostile
// path and an under-scoped request, so it reveals nothing an attacker can use —
// it just tells the person who is *supposed* to be here how to get back in.
const NOT_FOUND_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reopen your editing link</title>
<style>
 :root{color-scheme:light}
 body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
   background:#f4efe4;color:#1d1a16;
   font:17px/1.6 "Iowan Old Style","Palatino Linotype","Book Antiqua",Palatino,Georgia,serif;
   padding:2rem}
 main{max-width:34rem}
 h1{font-size:1.5rem;font-weight:600;margin:0 0 .6em;letter-spacing:-.01em}
 p{margin:0 0 1em;color:#544d43}
 .rule{height:3px;width:3.5rem;background:#7c1e2b;margin:0 0 1.4rem}
 .hint{font-size:.95rem;color:#8a7f6d;border-left:3px solid #a9822f;
   background:rgba(169,130,47,.1);padding:.7em .9em;border-radius:3px}
</style></head><body><main>
<div class="rule"></div>
<h1>Please reopen your editing link</h1>
<p>This page is part of the practicum editor, and it only opens through the
personal link Damien sent you. Open that link again — from your text message,
your email, or your saved bookmark — and you will land right back here, able to
edit.</p>
<p class="hint">If you bookmarked this page after it was already open, the
bookmark is missing the part of the address that signs you in. Use Damien's
original link instead, and bookmark that one. Nothing you wrote has been lost.</p>
</main></body></html>`;

export function uniform404() {
  return new Response(NOT_FOUND_HTML, {
    status: 404,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}
