// editor-http.js — HTTP helpers for the /edit surface: the edit-only CORS
// allowlist, the CSRF guard for mutations, the strict security headers every
// /edit response carries, and the uniform-404 + typed error envelope.
//
// The /edit CORS allowlist is the worker's OWN edit origins ONLY (env.EDIT_ORIGIN)
// — never the public site origins. /edit pages are same-origin with /edit/v1/*,
// so this is defense in depth; combined with the custom-header CSRF check it
// makes a cross-site POST impossible (the browser would need a preflight our CORS
// never grants, AND a custom header it cannot set cross-origin).
//
// EDIT_ORIGIN is a COMMA-SEPARATED LIST, not one value (KTD6). One deployment now
// answers on two browser origins at once — the Cloudflare Access hostname
// (edit.legalpracticum.org) and the older workers.dev fallback that every
// already-sent link points at — and BOTH are served by the same env. The list is
// enforced twice, and the second one is the one that bites: editCorsHeaders
// withholds CORS headers from an unlisted origin, and csrfOk REJECTS any request
// whose Origin is unlisted. So swapping this to a single new value (rather than
// widening it to a list) would have been silently catastrophic: every existing
// bookmark would keep LOADING pages normally, because navigations send no Origin
// header, while every SAVE from those pages returned 403 csrf_failed. Collapse
// back to a single entry only once the old door is actually retired.
//
// The parsing/matching idiom deliberately mirrors parseAllowedOrigins/matchOrigin
// in cors.js — one pattern for "allowlisted origin, echo the match" in this
// codebase, not two. As there, we echo the SINGLE MATCHED origin and never "*":
// these responses carry Access-Control-Allow-Credentials: true, which makes a
// wildcard both invalid per spec and unsafe in fact.

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

// The edit-origin allowlist as a Set (the worker's own origins). Parsed from the
// comma-separated env.EDIT_ORIGIN, whitespace trimmed, empty entries dropped —
// exactly parseAllowedOrigins() in cors.js. An UNSET or empty EDIT_ORIGIN yields
// an EMPTY set, which callers must keep reading as "no allowlist configured"
// (dev): editCorsHeaders emits no CORS headers and csrfOk skips the Origin check
// rather than rejecting everything. That has always been the dev behaviour and
// several test fixtures depend on it — do not tighten it here.
export function editOrigin(env) {
  return new Set(
    String(env?.EDIT_ORIGIN || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
}

// The request's Origin iff it is in the allowlist, else null (cf. matchOrigin in
// cors.js). Callers use the RETURNED value, never the list, when echoing.
export function matchEditOrigin(env, requestOrigin) {
  if (!requestOrigin) return null;
  return editOrigin(env).has(requestOrigin) ? requestOrigin : null;
}

// CORS headers for an XHR from an edit page. Only a LISTED origin is echoed, and
// only that one — never the whole list, never "*" (these responses send
// credentials). Any other Origin gets no ACAO at all, so the browser blocks the
// read. Same-origin navigations send no Origin and need none.
export function editCorsHeaders(env, requestOrigin) {
  const allow = matchEditOrigin(env, requestOrigin);
  if (!allow) return {};
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
//   * if an Origin header is present, it must be ONE OF the allowlisted edit
//     origins (any entry matches — both doors are legitimate, KTD6), AND
//   * if Sec-Fetch-Site is present, it must be "same-origin".
//
// The two conditionals stay guarded the same way as before: an absent Origin is
// fine (navigations and same-origin fetches in some browsers send none), and an
// EMPTY allowlist (dev, EDIT_ORIGIN unset) skips the Origin check entirely.
export function csrfOk(request, env) {
  if (request.headers.get("X-Edit-Request") !== "1") return false;
  const allow = editOrigin(env);
  const origin = request.headers.get("Origin");
  if (origin && allow.size && !allow.has(origin)) return false;
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
// the ?t= parameter is stripped from the address bar once the session cookie is
// set, so anyone who bookmarks the page AFTER arriving has bookmarked a URL that
// works only until the cookie lapses — and then meets a blank white "Not found."
// with nothing to do about it. (Damien hit exactly this on 2026-07-27.)
//
// The copy now has to serve TWO doors at once, because there are two. Someone can
// reach this page by typing edit.legalpracticum.org and being turned away at
// the Access check, having never held a personal link in their life; or by opening
// a lapsed bookmark from the older link-based door. The previous wording told
// EVERY such person to "reopen the personal link Damien sent you" — recovery
// instructions for a door the first reader never used, and one that stops existing
// entirely when the links retire. So the address is named first, as the durable way
// back in, and the link is mentioned second, as the thing that still works today.
//
// The page below is byte-identical for a valid path, an invalid path, a hostile
// path and an under-scoped request, so it reveals nothing an attacker can use —
// it just tells the person who is *supposed* to be here how to get back in.
// NOTHING here may vary per request: no path echo, no reason, no timestamp. That
// uniformity is the whole security property (R6), and it is asserted in
// editor-security.test.js. Words only — the serif/cream/crimson treatment matches
// the practicum on purpose.
const NOT_FOUND_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Getting back into the editor</title>
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
<h1>Let's get you back into the editor</h1>
<p>This page is part of the practicum editor, and it opens only for people who
have been let in. To pick your work back up, go to
<strong>edit.legalpracticum.org</strong> and sign in with your email
address. You will be sent a short code to confirm it is you, and then you will
land right back here, able to edit.</p>
<p>Already have an editing link from Damien, sent by text or email? You can
still reopen your editing link from that message and come straight in. What
does not work is a bookmark saved after the page was already open: it is
missing the part of the address that signs you in, which is the usual reason
this page appears.</p>
<p class="hint">Either way, nothing you wrote has been lost. Your edits were
saved as you made them, and your work will be waiting exactly where you left
it.</p>
</main></body></html>`;

export function uniform404() {
  return new Response(NOT_FOUND_HTML, {
    status: 404,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}
