// editor.js — the /edit/* router. index.js delegates every /edit request here.
// Wires the proxy-injector, instructor view, review page, /edit/v1 endpoints, the
// asset server, and the ?t= cookie exchange, wrapping EVERY return with the edit
// security headers (+ edit-origin CORS). The /edit CORS allowlist is the worker's
// OWN edit origin only.

import { withEditHeaders, uniform404, editSecurityHeaders } from "./editor-http.js";
import {
  resolveAuth, resolveOpaqueToken, mintCookieValue, buildSetCookie,
  readCookie, attributionLabel,
} from "./editor-auth.js";
import { renderAdminPage, buildEditorialFlags } from "./editor-admin.js";
import { resolvePagePath, resolveInstructorDoc } from "./editor-map.js";
import { handleEditPage, serveSiteAsset } from "./editor-inject.js";
import { renderInstructorDoc } from "./editor-instructor.js";
import { renderReviewPage } from "./editor-review.js";
import { renderHistoryPage, renderHistoryIndex, findDocBySlug } from "./editor-history.js";
import { serveAsset } from "./editor-assets.js";
import {
  suggestEndpoint, systemSuggestEndpoint, pendingEndpoint, reviewJsonEndpoint,
  scopeEndpoint, scopedRequestEndpoint, scopedRequestsEndpoint,
  scopedClaimEndpoint, scopedResolveEndpoint, groupStatusEndpoint,
  decideEndpoint, digestEndpoint, claimEndpoint, finalizeEndpoint, reconcileEndpoint,
  heartbeatEndpoint, revertRequestEndpoint, revertRequestsEndpoint, revertResolveEndpoint,
  publisherAuthorizeEndpoint, publisherReleaseEndpoint,
} from "./editor-endpoints.js";

function editorStub(env) {
  return env.EDITOR.getByName("global-v1");
}

// A bare same-origin 302. Kept no-store so a scope-dependent landing decision is
// never cached and replayed for a different identity.
function redirectTo(location) {
  return new Response(null, {
    status: 302,
    headers: { Location: location, "Cache-Control": "private, no-store" },
  });
}

// ---- the bare-hostname doorway (KTD7) --------------------------------------
// Exported and pure so it can be tested: index.js imports `cloudflare:workers`
// (for the Durable Object base class) and therefore cannot be loaded by
// `node --test` at all, so anything left inline in that file is untestable by
// construction. Returns a Response to send, or null to fall through.
//
// `edit.sonsteng.damienriehl.com` has to land somewhere useful when someone just
// types it — that memorability is the entire point of the hostname — but the
// editor surface lives under /edit. So the root forwards into it and the
// scope-aware landing there picks the destination. Bound to EDIT_ACCESS_HOST so
// the workers.dev root is untouched.
export function accessDoorwayRedirect(env, url) {
  if (!env || !env.EDIT_ACCESS_HOST) return null;
  if (url.host !== env.EDIT_ACCESS_HOST || url.pathname !== "/") return null;
  return redirectTo("/edit/");
}

// 302 to the same URL with ?t stripped, setting the signed scope cookie.
function redirectClean(url, setCookie) {
  const clean = new URL(url.toString());
  clean.searchParams.delete("t");
  const headers = new Headers({ Location: clean.pathname + (clean.search || "") });
  if (setCookie) headers.set("Set-Cookie", setCookie);
  return new Response(null, { status: 302, headers });
}

// ---- "since you last looked" marker for the editorial flags (R5) -----------
// A cookie, deliberately, not store state. The EditorStore has no generic
// key/value surface and its Durable Object migrations are APPEND-ONLY, so
// persisting a per-editor bookmark server-side would mean a schema migration —
// far and away the riskiest change available — to remember a timestamp. The
// tradeoff is that "since you last looked" means "on this device"; that is
// honest for what the feature is, and a first visit on a new device shows an
// empty list rather than replaying months of history (which is also exactly the
// first-visit behaviour buildEditorialFlags specifies).
//
// It is NOT a credential: it carries a timestamp and nothing else, it grants no
// access, and it is scoped to the admin page. Path is /edit/admin so it never
// rides along with the editing or endpoint traffic.
const SEEN_COOKIE = "edit_seen";

function buildSeenCookie(nowISO) {
  return `${SEEN_COOKIE}=${encodeURIComponent(nowISO)}; HttpOnly; Secure; ` +
    `SameSite=Lax; Path=/edit/admin; Max-Age=${60 * 60 * 24 * 365}`;
}

// Returns the previous visit's ISO stamp, or null on a first visit / anything
// unparseable. Never throws — a corrupt cookie must degrade to "first visit",
// not to an error page.
function readLastSeen(request) {
  const raw = readCookie(request, SEEN_COOKIE);
  if (!raw) return null;
  let decoded;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  const t = Date.parse(decoded);
  return Number.isFinite(t) ? decoded : null;
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
  if (path === "/edit/v1/scoped-request" && request.method === "POST")
    return wrap(await scopedRequestEndpoint(request, env, auth));
  if (path === "/edit/v1/scoped-requests" && request.method === "GET")
    return wrap(await scopedRequestsEndpoint(request, env, auth));
  if (path === "/edit/v1/scoped-claim" && request.method === "POST")
    return wrap(await scopedClaimEndpoint(request, env, auth));
  if (path === "/edit/v1/scoped-resolve" && request.method === "POST")
    return wrap(await scopedResolveEndpoint(request, env, auth));
  if (path === "/edit/v1/group-status" && request.method === "GET")
    return wrap(await groupStatusEndpoint(request, env, auth));
  if (path === "/edit/v1/scope" && request.method === "GET")
    return wrap(await scopeEndpoint(request, env, auth));
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
  if (path === "/edit/v1/prod/releases/authorize" && request.method === "POST")
    return wrap(await publisherAuthorizeEndpoint(request, env, auth));
  if (path === "/edit/v1/prod/releases/status" && request.method === "GET")
    return wrap(await publisherReleaseEndpoint(request, env, auth));
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

  // ---- the editor's own landing (KTD7) --------------------------------------
  // index.js 302s the BARE Access hostname here, so this is where "typing the
  // address" actually arrives. It is a doorway, not a page: `/edit/` resolves to
  // no page key of its own (resolvePagePath("") is null), so without this branch
  // everyone — including John — would land on the uniform 404 immediately after
  // completing a sign-in, which is the dead end this whole plan exists to close.
  // Scope decides the destination; no identity still takes the uniform 404, so
  // the doorway is not an oracle either.
  if (path === "/edit/" || path === "/edit") {
    if (request.method !== "GET") return wrap(uniform404());
    if (auth.scopes.admin.granted) return wrap(redirectTo("/edit/admin"));
    if (auth.scopes.edit.granted || auth.scopes.instructor.granted)
      return wrap(redirectTo("/edit/index.html"));
    return wrap(uniform404());
  }

  // ---- tokenless admin dashboard (R5, R8, KTD5) -----------------------------
  // Lives UNDER /edit on purpose. The prefix is what index.js already delegates
  // here, so this page inherits the router, the withEditHeaders wrapper, the
  // uniform 404 and the Path=/edit cookie scope with no new code — and an
  // under-scoped request returns bytes identical to any unknown /edit path,
  // which serving it at the bare root could not have done.
  if (path === "/edit/admin") {
    if (request.method !== "GET" || !auth.scopes.admin.granted) return wrap(uniform404());
    const stub = editorStub(env);
    const items = await stub.listAll();
    const reverts = await stub.listRevertRequests(null);
    const lastSeen = readLastSeen(request);
    const flags = buildEditorialFlags(items, auth.slot, lastSeen);
    const nowISO = new Date().toISOString();
    const page = renderAdminPage({
      items,
      reverts,
      flags,
      viewerLabel: attributionLabel(auth.editor),
      // The public practicum IS the student view — a different origin,
      // unauthenticated, with the editing layer absent and instructor material
      // reachable only through /edit. EDIT_UPSTREAM is exactly that origin, so
      // the link is derived rather than hardcoded and cannot drift from the site
      // the injector actually wraps.
      studentViewUrl: env.EDIT_UPSTREAM || "",
    });
    const withSeen = new Response(page.body, {
      status: page.status,
      statusText: page.statusText,
      headers: new Headers(page.headers),
    });
    withSeen.headers.set("Set-Cookie", buildSeenCookie(nowISO));
    return wrap(withSeen);
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
