// editor-inject.js — the /edit/<path> proxy-injector (edit scope).
//
// SECURITY (docs/research/editor-apply-spec.md + Enhancement item 2 — all
// non-negotiable):
//   * The path is resolved ONLY against the editor-map allowlist (in editor.js,
//     via resolvePagePath); an unknown path is a uniform 404 with NO upstream
//     fetch — this handler is only reached for allowlisted pages.
//   * The upstream URL is built with buildUpstreamUrl (same-origin assertion +
//     prefix containment) and fetched as a CLEAN subrequest: fresh Request so no
//     cookie/authorization is forwarded, no ?t, redirect:"manual", and
//     cf:{cacheEverything:false,cacheTtl:0}. Upstream Set-Cookie/CSP/cache
//     headers are dropped (we read only the body and build our own Response).
//   * Injected at serve time: a server-constant <base href> INTO /edit space,
//     the worker-served editor.css/js (NOT inline), this page's block map, and
//     John's pending items — the last two as ESCAPED JSON islands (never
//     interpolated into HTML). Same-origin <a href>s are rewritten into /edit.
//   * Response carries the strict CSP + no-store + Vary:Cookie + Referrer-Policy
//     (added by withEditHeaders in editor.js).

import { buildUpstreamUrl, escapeJsonIsland, pageBlockDescriptors, projectPendingItems, MAP_VERSION } from "./editor-map.js";

// Build a clean upstream subrequest that forwards NOTHING sensitive.
function cleanSubrequest(url) {
  return new Request(url.toString(), {
    method: "GET",
    headers: { accept: "text/html", "user-agent": "sonsteng-editor-proxy" },
    redirect: "manual",
    cf: { cacheEverything: false, cacheTtl: 0 },
  });
}

// The base href into /edit space for a page key ("…/index.html" -> its dir).
function baseHrefFor(pageKey) {
  const dir = pageKey.endsWith("/index.html")
    ? pageKey.slice(0, -"index.html".length)
    : pageKey.replace(/[^/]*$/, "");
  return "/edit/" + dir;
}

// Friendly plain-language page for any non-serveable upstream result (still
// no-store + CSP via the router's withEditHeaders wrap).
function friendly(status, message) {
  const html =
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/editor.css\"></head><body><main>" +
    "<h1>This page just updated</h1><p>" + message + "</p>" +
    "<p>Please reload, or text Damien if it keeps happening.</p></main></body></html>";
  return new Response(html, { status, headers: { "content-type": "text/html; charset=utf-8" } });
}

// HTMLRewriter handler that rewrites same-origin <a href> into /edit space.
class LinkRewriter {
  constructor(upstreamBase) {
    this.base = upstreamBase; // URL of the upstream page (for resolving relative hrefs)
    this.basePath = upstreamBase.pathname.replace(/[^/]*$/, ""); // upstream dir
    // The site prefix (e.g. "/platform/") — links under it map into /edit.
    const originBase = new URL("/", upstreamBase);
    this.originPrefix = new URL(upstreamBase).origin;
  }
  element(el) {
    const href = el.getAttribute("href");
    if (!href) return;
    if (/^(#|mailto:|tel:|javascript:)/i.test(href)) return; // leave in place
    let abs;
    try {
      abs = new URL(href, this.base);
    } catch {
      return;
    }
    if (abs.origin !== this.originPrefix) return; // external -> leave
    // Map the site path into /edit space. The public site is served under a
    // prefix (EDIT_UPSTREAM path); strip it so /edit/<relpath> lines up with the
    // allowlist keys.
    const upstreamRootPath = new URL(".", this.base).pathname; // not used directly
    let rel = abs.pathname;
    // Strip the EDIT_UPSTREAM prefix (everything up to and including the site root).
    const prefix = new URL(this.base).pathname.split("/").slice(0, 2).join("/") + "/"; // "/platform/"
    if (rel.startsWith(prefix)) rel = rel.slice(prefix.length);
    else if (rel.startsWith("/")) rel = rel.slice(1);
    el.setAttribute("href", "/edit/" + rel + (abs.hash || ""));
  }
}

// HTMLRewriter handler injecting our chrome into <head>.
class HeadInjector {
  constructor(html) { this.html = html; }
  element(el) {
    el.prepend(this.html.base, { html: true });
    el.append(this.html.tail, { html: true });
  }
}

// Serve an allowlisted page: fetch clean, inject, return HTML. `pending` is the
// array of this editor's pending items for THIS page (resolved by the router
// from the DO). Returns a Response (headers finalized by the router wrap).
export async function handleEditPage(env, { pageKey, blocks, pending }) {
  const upstream = buildUpstreamUrl(pageKey, env.EDIT_UPSTREAM);
  if (!upstream) return friendly(404, "That page is not available for editing.");

  let resp;
  try {
    resp = await fetch(cleanSubrequest(upstream));
  } catch {
    return friendly(502, "We could not load that page right now.");
  }
  // redirect:"manual" surfaces 3xx as an opaqueredirect/other — never follow.
  if (resp.status >= 300 && resp.status < 400) return friendly(409, "That page moved.");
  if (!resp.ok) return friendly(resp.status === 404 ? 404 : 502, "That page is not available right now.");
  const ct = resp.headers.get("content-type") || "";
  if (!/text\/html/i.test(ct)) return friendly(415, "That page cannot be edited.");

  const base = baseHrefFor(pageKey);
  const mapIsland = escapeJsonIsland({ version: MAP_VERSION, page: pageKey, blocks: pageBlockDescriptors(blocks) });
  const editsIsland = escapeJsonIsland({ items: projectPendingItems(pending) });

  const headHtml = {
    base:
      `<base href="${base}">` +
      `<meta name="editor-map-version" content="${MAP_VERSION}">` +
      `<link rel="stylesheet" href="/edit/assets/editor.css">`,
    tail:
      `<script type="application/json" id="editor-map-data">${mapIsland}</script>` +
      `<script type="application/json" id="edits-data">${editsIsland}</script>` +
      `<script src="/edit/assets/editor.js" defer></script>`,
  };

  // Build a fresh Response so NO upstream header (Set-Cookie/CSP/cache) survives.
  const clean = new Response(resp.body, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
  const rewritten = new HTMLRewriter()
    .on("head", new HeadInjector(headHtml))
    .on("a[href]", new LinkRewriter(upstream))
    .transform(clean);
  return rewritten;
}
