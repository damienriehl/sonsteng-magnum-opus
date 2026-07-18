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
import { AssetLinkRewriter, ScriptStripper, SITE_ASSET_UPSTREAM } from "../src/editor-inject.js";
import { EDIT_CSP } from "../src/editor-http.js";

// Minimal stand-in for HTMLRewriter's Element.
function stubEl(attrs) {
  return {
    _attrs: { ...attrs },
    removed: false,
    getAttribute(name) { return name in this._attrs ? this._attrs[name] : null; },
    setAttribute(name, value) { this._attrs[name] = value; },
    remove() { this.removed = true; },
  };
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
