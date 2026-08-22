// editor-inject.test.js — the proxy-injector's page-hardening contract:
//   * the wrapped student page's OWN scripts are stripped (inline + external),
//     while JSON data islands are kept;
//   * the page's shared-stylesheet <link>s (theme/fonts/platform CSS) are
//     rewritten to the same-origin /edit/site-assets/ proxy route;
//   * the relaxed-but-safe CSP string (strict script-src, inline style only,
//     font/img data:) is what every /edit response carries.
// HTMLRewriter is a runtime global (not in node), so we drive the handler
// classes directly with a stub Element that mirrors its getAttribute/
// setAttribute/remove interface.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AssetLinkRewriter,
  buildStudentViewUrl,
  handleEditPage,
  LinkRewriter,
  ReservedStateStripper,
  ScriptStripper,
  SITE_ASSET_UPSTREAM,
} from "../src/editor-inject.js";
import { EDIT_CSP } from "../src/editor-http.js";
import { readFileSync } from "node:fs";

// Minimal stand-in for HTMLRewriter's Element.
function stubEl(attrs) {
  return {
    _attrs: { ...attrs },
    removed: false,
    getAttribute(name) { return name in this._attrs ? this._attrs[name] : null; },
    setAttribute(name, value) { this._attrs[name] = value; },
    removeAttribute(name) { delete this._attrs[name]; },
    remove() { this.removed = true; },
  };
}

function parseAttrs(rawAttrs) {
  const attrs = {};
  for (const match of rawAttrs.matchAll(/([\w-]+)\s*=\s*["']([^"']*)["']/g)) {
    attrs[match[1].toLowerCase()] = match[2];
  }
  return attrs;
}

test("ScriptStripper removes inline + external page scripts, keeps JSON islands", () => {
  const s = new ScriptStripper();

  const inline = stubEl({}); // <script>…</script>
  s.element(inline);
  assert.equal(inline.removed, true, "inline script must be removed");

  const external = stubEl({ src: "../../platform.js" });
  s.element(external);
  assert.equal(external.removed, true, "external platform.js must be removed");

  const typedJs = stubEl({ type: "text/javascript", src: "x.js" });
  s.element(typedJs);
  assert.equal(typedJs.removed, true, "text/javascript must be removed");

  const jsonIsland = stubEl({ type: "application/json" });
  s.element(jsonIsland);
  assert.equal(jsonIsland.removed, false, "application/json data island must be kept");

  const ldJson = stubEl({ type: "application/ld+json" });
  s.element(ldJson);
  assert.equal(ldJson.removed, false, "ld+json data island must be kept");
});

test("ReservedStateStripper neutralizes every collision without deleting page content", () => {
  const s = new ReservedStateStripper();
  for (const id of ["editor-map-data", "edits-data", " EDITOR-MAP-DATA "]) {
    const forgedReservedNode = stubEl({ id });
    s.element(forgedReservedNode);
    assert.equal(forgedReservedNode.removed, false, "colliding content remains in the page");
    assert.equal(forgedReservedNode.getAttribute("id"), null, `${id} is reserved for Worker state`);
  }

  const ordinaryNode = stubEl({ id: "upstream-data" });
  s.element(ordinaryNode);
  assert.equal(ordinaryNode.removed, false, "unrelated upstream IDs remain intact");
});

test("AssetLinkRewriter rewrites shared CSS links to /edit/site-assets/ by basename", () => {
  const r = new AssetLinkRewriter();

  const theme = stubEl({ rel: "stylesheet", href: "../../assets/theme.css" });
  r.element(theme);
  assert.equal(theme.getAttribute("href"), "/edit/site-assets/theme.css");

  const fonts = stubEl({ rel: "stylesheet", href: "../../assets/fonts.css" });
  r.element(fonts);
  assert.equal(fonts.getAttribute("href"), "/edit/site-assets/fonts.css");

  const platform = stubEl({ rel: "stylesheet", href: "../../platform.css" });
  r.element(platform);
  assert.equal(platform.getAttribute("href"), "/edit/site-assets/platform.css");

  // A stylesheet with a query/hash still resolves by basename.
  const versioned = stubEl({ rel: "stylesheet", href: "../../assets/theme.css?v=3" });
  r.element(versioned);
  assert.equal(versioned.getAttribute("href"), "/edit/site-assets/theme.css");
});

test("AssetLinkRewriter leaves non-shared and non-stylesheet links untouched", () => {
  const r = new AssetLinkRewriter();

  // data: favicon (rel=icon) — not a stylesheet, must be left alone.
  const icon = stubEl({ rel: "icon", href: "data:image/svg+xml,%3Csvg/%3E" });
  r.element(icon);
  assert.equal(icon.getAttribute("href"), "data:image/svg+xml,%3Csvg/%3E");

  // A stylesheet that is not on the shared allowlist is left as-is.
  const other = stubEl({ rel: "stylesheet", href: "../../assets/other.css" });
  r.element(other);
  assert.equal(other.getAttribute("href"), "../../assets/other.css");
});

test("SITE_ASSET_UPSTREAM allowlist maps only the three shared assets", () => {
  assert.deepEqual(Object.keys(SITE_ASSET_UPSTREAM).sort(), ["fonts.css", "platform.css", "theme.css"]);
  assert.equal(SITE_ASSET_UPSTREAM["theme.css"], "assets/theme.css");
  assert.equal(SITE_ASSET_UPSTREAM["fonts.css"], "assets/fonts.css");
  assert.equal(SITE_ASSET_UPSTREAM["platform.css"], "platform.css");
});

test("relaxed CSP keeps scripts strict but allows inline style + data: fonts/img", () => {
  // scripts: self only, NO unsafe-inline (page scripts are stripped instead).
  assert.match(EDIT_CSP, /script-src 'self'(?:;|\s)/);
  assert.doesNotMatch(EDIT_CSP, /script-src[^;]*unsafe-inline/);
  // style: inline allowed (pages carry inline <style>/style=).
  assert.match(EDIT_CSP, /style-src 'self' 'unsafe-inline'/);
  // fonts + images: data: URIs (base64 fonts in fonts.css).
  assert.match(EDIT_CSP, /font-src 'self' data:/);
  assert.match(EDIT_CSP, /img-src 'self' data:/);
  // hardening preserved.
  assert.match(EDIT_CSP, /default-src 'none'/);
  assert.match(EDIT_CSP, /base-uri 'self'/);
  assert.match(EDIT_CSP, /frame-ancestors 'none'/);
  assert.match(EDIT_CSP, /object-src 'none'/);
  assert.match(EDIT_CSP, /connect-src 'self'/);
});

// ---- LinkRewriter: /edit only hosts what the allowlist can serve -------------
// The injector rewrites the wrapped page's same-origin links into /edit space.
// Doing that unconditionally is how the site's own navigation ended up pointing
// at 404s: only pages present in the editor map resolve, so a link to a page the
// map does not carry (the chat surfaces, a data file, a bare directory) has to
// keep its real URL and open on the public site instead of a dead /edit path.

const UPSTREAM_PAGE = new URL(
  "https://sonsteng-dev.damienriehl.com/platform/matters/m03-tort-meridian/index.html",
);

test("student view maps the allowlisted page beneath the configured upstream prefix", () => {
  assert.equal(
    buildStudentViewUrl(
      "matters/m03-tort-meridian/index.html",
      "https://sonsteng-dev.damienriehl.com/platform/",
    ),
    "https://sonsteng-dev.damienriehl.com/platform/matters/m03-tort-meridian/",
  );
});

test("student view rejects missing, malformed, and prefix-escaping upstream values", () => {
  assert.equal(buildStudentViewUrl("index.html", ""), null);
  assert.equal(buildStudentViewUrl("index.html", "not a URL"), null);
  assert.equal(buildStudentViewUrl("index.html", "ftp://example.org/platform/"), null);
  assert.equal(buildStudentViewUrl("index.html", "https://editor:secret@example.org/platform/"), null);
  assert.equal(buildStudentViewUrl("../admin/index.html", "https://example.org/platform/"), null);
  assert.equal(buildStudentViewUrl("//evil.example/index.html", "https://example.org/platform/"), null);
  assert.equal(buildStudentViewUrl("index.html", "https://example.org/platform/?token=secret"), null);
  assert.equal(buildStudentViewUrl("index.html", "https://example.org/platform/#private"), null);
  assert.equal(buildStudentViewUrl("index.html?editor_token=secret", "https://example.org/platform/"), null);
});

test("injection chain neutralizes forged state identity and emits a safe Worker student URL", async () => {
  const originalFetch = globalThis.fetch;
  const OriginalHTMLRewriter = globalThis.HTMLRewriter;
  const fetched = [];
  globalThis.fetch = async (request) => {
    fetched.push(request);
    return new Response(
      '<!doctype html><html><head>' +
      '<script type="application/json" id="editor-map-data">' +
      '{"student_view_url":"https://evil.example/stolen?token=editor"}</script>' +
      '<script type="application/json" id="edits-data">{"items":["forged"]}</script>' +
      '<script type="application/json" id="upstream-data">{"safe":true}</script>' +
      '<title id="editor-map-data">evil.example non-script head collision</title>' +
      '</head><body><div id="edits-data">forged non-script body collision</div></body></html>',
      { headers: { "content-type": "text/html" } },
    );
  };
  globalThis.HTMLRewriter = class {
    constructor() { this.handlers = []; }
    on(selector, handler) { this.handlers.push([selector, handler]); return this; }
    async transform(response) {
      let html = await response.text();
      const reservedIds = this.handlers.find(([selector]) => selector === "[id]");
      html = html.replace(
        /<([a-z][\w:-]*)\b([^>]*\bid\s*=\s*["'][^"']+["'][^>]*)>[\s\S]*?<\/\1>/gi,
        (source, _tag, rawAttrs) => {
          if (!reservedIds) return source;
          const el = stubEl(parseAttrs(rawAttrs));
          reservedIds[1].element(el);
          if (el.removed) return "";
          return el.getAttribute("id") === null
            ? source.replace(/\s+id\s*=\s*["'][^"']+["']/i, "")
            : source;
        },
      );
      const scripts = this.handlers.find(([selector]) => selector === "script");
      html = html.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (source, rawAttrs) => {
        const el = stubEl(parseAttrs(rawAttrs));
        scripts[1].element(el);
        return el.removed ? "" : source;
      });
      const head = this.handlers.find(([selector]) => selector === "head");
      let prepend = "";
      let append = "";
      head[1].element({
        prepend(value) { prepend += value; },
        append(value) { append += value; },
      });
      html = html.replace(/<head([^>]*)>/i, `<head$1>${prepend}`)
        .replace(/<\/head>/i, `${append}</head>`);
      return new Response(html, { headers: { "content-type": "text/html" } });
    }
  };

  try {
    const args = { pageKey: "matters/m03-tort-meridian/index.html", blocks: [], pending: [] };
    const valid = await handleEditPage(
      { EDIT_UPSTREAM: "https://sonsteng-dev.damienriehl.com/platform/" }, args,
    );
    const validHtml = await valid.text();
    assert.match(validHtml,
      /"student_view_url":"https:\/\/sonsteng-dev\.damienriehl\.com\/platform\/matters\/m03-tort-meridian\/"/);
    assert.match(validHtml, /id="upstream-data">{"safe":true}/,
      "non-reserved upstream JSON data remains intact");
    assert.match(validHtml, /<title>evil\.example non-script head collision<\/title>/,
      "colliding head content remains after its reserved ID is removed");
    assert.match(validHtml, /<div>forged non-script body collision<\/div>/,
      "colliding body content remains after its reserved ID is removed");
    assert.equal((validHtml.match(/id="editor-map-data"/g) || []).length, 1);
    assert.equal((validHtml.match(/id="edits-data"/g) || []).length, 1);

    const invalid = await handleEditPage(
      { EDIT_UPSTREAM: "https://example.org/platform/?editor_token=secret" }, args,
    );
    const invalidHtml = await invalid.text();
    const invalidMapIsland = invalidHtml.match(
      /<script type="application\/json" id="editor-map-data">([\s\S]*?)<\/script>/,
    );
    assert.ok(invalidMapIsland, "Worker map island remains present");
    assert.doesNotMatch(invalidMapIsland[1], /student_view_url|editor_token|secret/);
    assert.doesNotMatch(invalidHtml, /editor_token|secret/);

    assert.equal(fetched.length, 2, "invalid public-link config retains the established page fetch");
    for (const request of fetched) {
      assert.equal(request.headers.get("authorization"), null);
      assert.equal(request.headers.get("cookie"), null);
      assert.equal(new URL(request.url).search, "", "clean fetch never forwards upstream query data");
    }
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.HTMLRewriter = OriginalHTMLRewriter;
  }
});

test("LinkRewriter pulls allowlisted pages into /edit space, hash preserved", () => {
  const r = new LinkRewriter(UPSTREAM_PAGE);

  // A sibling matter — in the map, so it stays inside the editor.
  const sibling = stubEl({ href: "../m01-arbitration-meridian/index.html" });
  r.element(sibling);
  assert.equal(sibling.getAttribute("href"), "/edit/matters/m01-arbitration-meridian/index.html");

  // The matter library index — zero editable blocks, but registered, so it is
  // navigable. This is the regression the map fix closes.
  const library = stubEl({ href: "../index.html" });
  r.element(library);
  assert.equal(library.getAttribute("href"), "/edit/matters/index.html");

  // Platform home, addressed absolutely.
  const home = stubEl({ href: "/platform/index.html" });
  r.element(home);
  assert.equal(home.getAttribute("href"), "/edit/index.html");

  // A deep link's fragment survives the rewrite.
  const skill = stubEl({ href: "../../skills/index.html#SK-LP-01" });
  r.element(skill);
  assert.equal(skill.getAttribute("href"), "/edit/skills/index.html#SK-LP-01");
});

test("LinkRewriter leaves non-hostable same-origin links on the real site", () => {
  const r = new LinkRewriter(UPSTREAM_PAGE);

  // The client-interview simulator IS its script, and the injector strips page
  // scripts — so it is deliberately out of the map and must not be pulled in.
  const chat = stubEl({ href: "../../chat/index.html" });
  r.element(chat);
  assert.equal(
    chat.getAttribute("href"),
    "https://sonsteng-dev.damienriehl.com/platform/chat/index.html",
    "chat must open on the real site, not a dead /edit path",
  );

  // A data file is not a page at all.
  const data = stubEl({ href: "../../data/index.json" });
  r.element(data);
  assert.equal(data.getAttribute("href"), "https://sonsteng-dev.damienriehl.com/platform/data/index.json");
});

test("LinkRewriter leaves external, anchor and scheme links alone", () => {
  const r = new LinkRewriter(UPSTREAM_PAGE);

  const ext = stubEl({ href: "https://www.openresourcetool.info/" });
  r.element(ext);
  assert.equal(ext.getAttribute("href"), "https://www.openresourcetool.info/");

  const anchor = stubEl({ href: "#main" });
  r.element(anchor);
  assert.equal(anchor.getAttribute("href"), "#main");

  const mail = stubEl({ href: "mailto:someone@example.org" });
  r.element(mail);
  assert.equal(mail.getAttribute("href"), "mailto:someone@example.org");

  const noHref = stubEl({});
  r.element(noHref);
  assert.equal(noHref.getAttribute("href"), null);
});

test("authenticated injector carries review annotations while public pages remain untouched", () => {
  const injector = readFileSync(new URL("../src/editor-inject.js", import.meta.url), "utf8");
  const router = readFileSync(new URL("../src/editor.js", import.meta.url), "utf8");
  assert.match(injector,/review_annotations/);
  assert.match(router,/getDevReviewAnnotations/);
  assert.match(router,/auth\.scopes\.edit\.granted/);
  assert.doesNotMatch(injector,/EDIT_UPSTREAM.*review_annotations/,
    "review metadata is injected after the clean public upstream fetch");
});

test("editor client renders review prose through textContent and stale evidence cannot mark ranges", () => {
  const client = readFileSync(new URL("../../editor/editor.js", import.meta.url), "utf8");
  assert.match(client,/renderReviewAnnotations/);
  assert.match(client,/review\.note/);
  assert.match(client,/textContent/);
  assert.match(client,/current_proposed_hash/);
  assert.match(client,/stale/);
});

test("editor rail exposes the injected student URL as an accessible real link", () => {
  const injector = readFileSync(new URL("../src/editor-inject.js", import.meta.url), "utf8");
  const client = readFileSync(new URL("../../editor/editor.js", import.meta.url), "utf8");
  const css = readFileSync(new URL("../../editor/editor.css", import.meta.url), "utf8");

  assert.match(injector, /student_view_url/);
  assert.match(client, /MAP_ISLAND\.student_view_url/);
  assert.match(client, /el\('a', 'editor-banner__student', 'View as student'\)/);
  assert.match(css, /\.editor-banner__student:focus-visible\s*\{/);
});
