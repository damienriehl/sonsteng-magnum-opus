// editor.js — the /edit/* router. index.js delegates every /edit request here.
// Wires the proxy-injector, instructor view, review page, /edit/v1 endpoints, the
// asset server, and the ?t= cookie exchange, wrapping EVERY return with the edit
// security headers (+ edit-origin CORS). The /edit CORS allowlist is the worker's
// OWN edit origin only.

import { withEditHeaders, uniform404, editSecurityHeaders } from "./editor-http.js";
import { resolveAuth, resolveOpaqueToken, mintCookieValue, buildSetCookie } from "./editor-auth.js";
import { resolvePagePath, resolveInstructorDoc } from "./editor-map.js";
import { handleEditPage, serveSiteAsset } from "./editor-inject.js";
import { renderInstructorDoc } from "./editor-instructor.js";
import { renderReviewPage } from "./editor-review.js";
import { renderHistoryPage, renderHistoryIndex, findDocBySlug } from "./editor-history.js";
import { serveAsset } from "./editor-assets.js";
import {
  suggestEndpoint, systemSuggestEndpoint, pendingEndpoint, reviewJsonEndpoint,
  decideEndpoint, digestEndpoint, claimEndpoint, finalizeEndpoint, reconcileEndpoint,
  heartbeatEndpoint, revertRequestEndpoint, revertRequestsEndpoint, revertResolveEndpoint,
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

  // ---- shared SITE assets (theme/fonts/platform CSS proxied from upstream) ---
  // No auth: same public CSS the origin page serves; referenced by wrapped pages.
  if (path.startsWith("/edit/site-assets/")) {
    if (request.method !== "GET") return wrap(uniform404());
    const name = path.slice("/edit/site-assets/".length);
    const asset = await serveSiteAsset(env, name);
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
  if (path === "/edit/v1/heartbeat" && request.method === "POST")
    return wrap(await heartbeatEndpoint(request, env, auth));
  if (path === "/edit/v1/revert-request" && request.method === "POST")
    return wrap(await revertRequestEndpoint(request, env, auth));
  if (path === "/edit/v1/revert-requests" && request.method === "GET")
    return wrap(await revertRequestsEndpoint(request, env, auth));
  if (path === "/edit/v1/revert-resolve" && request.method === "POST")
    return wrap(await revertResolveEndpoint(request, env, auth));

  // ---- editor-gated redline History browser (edit/instructor scope) ---------
  // Same gate as /edit/v1/pending. Index + per-doc slice from the inlined bundle.
  if (path === "/edit/history/" || path === "/edit/history") {
    if (request.method !== "GET" ||
        (!auth.scopes.edit.granted && !auth.scopes.instructor.granted))
      return wrap(uniform404());
    return wrap(renderHistoryIndex());
  }
  if (path.startsWith("/edit/history/")) {
    if (request.method !== "GET" ||
        (!auth.scopes.edit.granted && !auth.scopes.instructor.granted))
      return wrap(uniform404());
    const slug = decodeURIComponent(path.slice("/edit/history/".length).replace(/\/+$/, ""));
    const found = findDocBySlug(slug);
    if (!found) return wrap(uniform404());
    return wrap(renderHistoryPage(found[1]));
  }

  // ---- admin review page ----------------------------------------------------
  if (path === "/edit/review") {
    if (request.method !== "GET" || !auth.scopes.admin.granted) return wrap(uniform404());
    const stub = editorStub(env);
    const items = await stub.listAll();
    const reverts = await stub.listRevertRequests(null);
    return wrap(renderReviewPage(items, reverts));
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
    // Cross-editor overlay: the island carries EVERY editor's active suggestions
    // for this page (attribution stamped per-row by projectPendingItems), not just
    // the caller's own. The edit-scope gate above already fenced non-editors out —
    // only an edit-scope holder (admin preview included) ever reaches this source.
    const stub = editorStub(env);
    const pending = await stub.listForPage(resolved.pageKey);
    // SL6 liveness for the injected island (same signals GET /pending carries):
    // the daemon-heartbeat age + whether auto-apply (DIRECT_APPLY) is on, so the
    // banner reads honestly on first paint (before any repoll).
    const heartbeatAgeS = await stub.heartbeatAgeS();
    const directApply = env.DIRECT_APPLY === "true";
    return wrap(await handleEditPage(env, { ...resolved, pending, heartbeatAgeS, directApply }));
  }

  return wrap(uniform404());
}

export { editSecurityHeaders };
