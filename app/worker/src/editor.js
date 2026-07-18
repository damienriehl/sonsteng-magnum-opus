// editor.js — the /edit/* router. index.js delegates every /edit request here.
// Wires the proxy-injector, instructor view, review page, /edit/v1 endpoints, the
// asset server, and the ?t= cookie exchange, wrapping EVERY return with the edit
// security headers (+ edit-origin CORS). The /edit CORS allowlist is the worker's
// OWN edit origin only.

import { withEditHeaders, uniform404, editSecurityHeaders } from "./editor-http.js";
import { resolveAuth, resolveOpaqueToken, mintCookieValue, buildSetCookie } from "./editor-auth.js";
import { resolvePagePath, resolveInstructorDoc } from "./editor-map.js";
import { handleEditPage } from "./editor-inject.js";
import { renderInstructorDoc } from "./editor-instructor.js";
import { renderReviewPage } from "./editor-review.js";
import { serveAsset } from "./editor-assets.js";
import {
  suggestEndpoint, systemSuggestEndpoint, pendingEndpoint, reviewJsonEndpoint,
  decideEndpoint, digestEndpoint, claimEndpoint, finalizeEndpoint, reconcileEndpoint,
} from "./editor-endpoints.js";

function editorStub(env) {
  return env.EDITOR.getByName("global-v1");
}

// 302 to the same URL with ?t stripped, setting the signed scope cookie.
function redirectClean(url, setCookie) {
  const clean = new URL(url.toString());
  clean.searchParams.delete("t");
  const headers = new Headers({ Location: clean.pathname + (clean.search || "") });
  if (setCookie) headers.set("Set-Cookie", setCookie);
  return new Response(null, { status: 302, headers });
}

// This editor's pending items for an instructor doc (page is null on instructor
// suggests, so filter by the doc's source_ref prefix).
function forDocPrefix(items, sourceRef) {
  return (items || []).filter((it) => it.source_ref && it.source_ref.startsWith(sourceRef));
}

export async function editorFetch(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname; // starts with /edit
  const reqOrigin = request.headers.get("Origin");
  const wrap = (resp) => withEditHeaders(resp, env, reqOrigin);

  // ---- CORS preflight for /edit/v1 XHRs (edit origin only) ------------------
  if (request.method === "OPTIONS") {
    return wrap(new Response(null, { status: 204 }));
  }

  // ---- ?t=<opaque> one-time cookie exchange ---------------------------------
  if (url.searchParams.has("t")) {
    const presented = url.searchParams.get("t");
    const matched = await resolveOpaqueToken(env, presented);
    let setCookie = null;
    if (matched) {
      const value = await mintCookieValue(env.SESSION_SIGNING_KEY, { slot: matched.slot, stamp: matched.stamp });
      setCookie = buildSetCookie(value);
    }
    // Always strip ?t from the URL (keeps it out of logs/history); set the cookie
    // only on a valid token. Invalid tokens simply land unauthenticated -> 404.
    return wrap(redirectClean(url, setCookie));
  }

  const auth = await resolveAuth(env, request);

  // ---- static assets (no auth; referenced by injected pages) ----------------
  if (path.startsWith("/edit/assets/")) {
    if (request.method !== "GET") return wrap(uniform404());
    const name = path.slice("/edit/assets/".length);
    const asset = serveAsset(name);
    return wrap(asset || uniform404());
  }

  // ---- /edit/v1/* JSON endpoints --------------------------------------------
  if (path === "/edit/v1/suggest" && request.method === "POST")
    return wrap(await suggestEndpoint(request, env, auth));
  if (path === "/edit/v1/system-suggest" && request.method === "POST")
    return wrap(await systemSuggestEndpoint(request, env, auth));
  if (path === "/edit/v1/pending" && request.method === "GET")
    return wrap(await pendingEndpoint(request, env, auth));
  if (path === "/edit/v1/review" && request.method === "GET")
    return wrap(await reviewJsonEndpoint(request, env, auth));
  if (path === "/edit/v1/decide" && request.method === "POST")
    return wrap(await decideEndpoint(request, env, auth));
  if (path === "/edit/v1/digest" && request.method === "GET")
    return wrap(await digestEndpoint(request, env, auth));
  if (path === "/edit/v1/claim" && request.method === "POST")
    return wrap(await claimEndpoint(request, env, auth));
  if (path === "/edit/v1/finalize" && request.method === "POST")
    return wrap(await finalizeEndpoint(request, env, auth));
  if (path === "/edit/v1/reconcile" && request.method === "POST")
    return wrap(await reconcileEndpoint(request, env, auth));

  // ---- admin review page ----------------------------------------------------
  if (path === "/edit/review") {
    if (request.method !== "GET" || !auth.scopes.admin.granted) return wrap(uniform404());
    const items = await editorStub(env).listAll();
    return wrap(renderReviewPage(items));
  }

  // ---- instructor view ------------------------------------------------------
  if (path.startsWith("/edit/instructor/")) {
    const rest = path.slice("/edit/instructor/".length).replace(/\/+$/, "");
    const parts = rest.split("/");
    const doc = parts.length === 2 ? resolveInstructorDoc(parts[0], parts[1]) : null;
    // Uniform 404 for BOTH missing doc AND insufficient scope (no oracle).
    if (request.method !== "GET" || !doc || !auth.scopes.instructor.granted) return wrap(uniform404());
    const all = await editorStub(env).listForEditor(auth.editor, null);
    return wrap(renderInstructorDoc(doc, forDocPrefix(all, doc.source_ref)));
  }

  // ---- proxy-injector (edit scope) ------------------------------------------
  if (path.startsWith("/edit/") && request.method === "GET") {
    const tail = path.slice("/edit/".length);
    const resolved = resolvePagePath(tail);
    // Uniform 404 for unknown path AND insufficient scope (no upstream fetch).
    if (!resolved || !auth.scopes.edit.granted) return wrap(uniform404());
    const pending = await editorStub(env).listForEditor(auth.editor, resolved.pageKey);
    return wrap(await handleEditPage(env, { ...resolved, pending }));
  }

  return wrap(uniform404());
}

export { editSecurityHeaders };
