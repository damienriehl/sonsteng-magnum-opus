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

import { buildUpstreamUrl, escapeJsonIsland, pageBlockDescriptors, projectPendingItems, resolvePagePath, MAP_VERSION } from "./editor-map.js";

// The shared site assets the wrapped student pages reference. Each maps its
// served basename (/edit/site-assets/<name>) to its EDIT_UPSTREAM-relative path.
// theme.css/fonts.css live under assets/; platform.css sits at the site root.
// This is a fixed allowlist — no client value ever picks the upstream path.
export const SITE_ASSET_UPSTREAM = {
  "theme.css": "assets/theme.css",
  "fonts.css": "assets/fonts.css",
  "platform.css": "platform.css",
};
const SITE_ASSET_NAMES = new Set(Object.keys(SITE_ASSET_UPSTREAM));

// Build a clean upstream subrequest that forwards NOTHING sensitive.
function cleanSubrequest(url, accept = "text/html") {
  return new Request(url.toString(), {
    method: "GET",
    headers: { accept, "user-agent": "sonsteng-editor-proxy" },
    redirect: "manual",
    cf: { cacheEverything: false, cacheTtl: 0 },
  });
}

// Serve a shared site asset (theme.css/fonts.css/platform.css) by proxying the
// clean subrequest to EDIT_UPSTREAM. Returns a Response or null (router -> 404).
// Same SSRF discipline as buildUpstreamUrl: stay inside the upstream origin+prefix,
// no query/hash, and we build our own Response so no upstream header survives.
export async function serveSiteAsset(env, name) {
  const rel = SITE_ASSET_UPSTREAM[name];
  if (!rel) return null;
  let base, url;
  try {
    base = new URL(env.EDIT_UPSTREAM);
    url = new URL(rel, base);
  } catch {
    return null;
  }
  if (url.origin !== base.origin) return null;
  const basePath = base.pathname.endsWith("/") ? base.pathname : base.pathname + "/";
  if (!url.pathname.startsWith(basePath)) return null;
  let resp;
  try {
    resp = await fetch(cleanSubrequest(url, "text/css,*/*"));
  } catch {
    return null;
  }
  if (resp.status >= 300 && resp.status < 400) return null; // never follow
  if (!resp.ok) return null;
  // We only allow-list .css assets; force the type so upstream sniffing can't
  // reclassify it. Body is streamed; no upstream Set-Cookie/CSP/cache survives.
  return new Response(resp.body, {
    status: 200,
    headers: { "content-type": "text/css; charset=utf-8" },
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
//
// A link is only pulled into /edit if the editor can actually host it — i.e. the
// path resolves against the map allowlist. Anything else (the chat surfaces, a
// data file, a directory with no page) keeps its real absolute URL and opens on
// the public site. Rewriting unconditionally is what put "Matter Library" and
// "Platform home" on dead /edit paths: the allowlist is the authority on what
// /edit can serve, so the rewriter has to ask it rather than assume.
export class LinkRewriter {
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
    // Only pages the allowlist can serve move into /edit; everything else keeps
    // its real URL so the reader lands on a working page instead of a 404.
    if (!resolvePagePath(rel)) {
      el.setAttribute("href", abs.toString());
      return;
    }
    el.setAttribute("href", "/edit/" + rel + (abs.hash || ""));
  }
}

// HTMLRewriter handler that rewrites the wrapped page's shared-stylesheet <link>s
// (../assets/theme.css, ../assets/fonts.css, ../platform.css — resolved by
// basename) into the same-origin /edit/site-assets/ proxy route so they load
// under the strict CSP. Non-stylesheet links (icon, preload) are left untouched.
export class AssetLinkRewriter {
  element(el) {
    const rel = (el.getAttribute("rel") || "").toLowerCase();
    if (!/\bstylesheet\b/.test(rel)) return;
    const href = el.getAttribute("href");
    if (!href) return;
    const m = href.match(/([^/?#]+\.css)(?:[?#].*)?$/i);
    if (!m) return;
    const name = m[1];
    if (SITE_ASSET_NAMES.has(name)) el.setAttribute("href", "/edit/site-assets/" + name);
  }
}

// HTMLRewriter handler that STRIPS the wrapped page's OWN scripts (inline and
// external, e.g. platform.js scroll-spy/toggles). Edit mode does not need them,
// and the races review wanted the origin page's interactive handlers neutralized
// (they fight contenteditable + mutate the DOM under the anchors). Removing them
// lets script-src stay STRICT 'self' — only editor.js runs. JSON data islands
// (type=application/json / ld+json) are DATA, not executed, so they are kept.
// NOTE: content the injector appends to <head> (editor.js + the islands) is added
// AFTER parsing and is NOT re-processed by this handler, so it is never stripped.
export class ScriptStripper {
  element(el) {
    const type = (el.getAttribute("type") || "").trim().toLowerCase();
    if (type === "application/json" || type === "application/ld+json") return; // data — keep
    el.remove();
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
export async function handleEditPage(env, { pageKey, blocks, overrides = [], pending, heartbeatAgeS = null, directApply = false }) {
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
  const mapIsland = escapeJsonIsland({ version: MAP_VERSION, page: pageKey,
    blocks: pageBlockDescriptors(blocks), overrides });
  const editsIsland = escapeJsonIsland({
    items: projectPendingItems(pending),
    heartbeat_age_s: heartbeatAgeS,
    direct_apply: directApply,
    environment: env.EDIT_ENVIRONMENT === "production" ? "production" : "dev",
    manifest_epoch: env.EDIT_ENVIRONMENT === "production" &&
      typeof env.EDIT_PROD_MANIFEST_EPOCH === "string" ? env.EDIT_PROD_MANIFEST_EPOCH : "",
  });

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
    .on("link[rel]", new AssetLinkRewriter())
    .on("script", new ScriptStripper())
    .transform(clean);
  return rewritten;
}
