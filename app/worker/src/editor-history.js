// editor-history.js — the editor-gated redline History browser pages, served at
// GET /edit/history/ (index) and GET /edit/history/<doc-slug> (per-doc). Same
// gate as /edit/v1/pending (edit OR instructor scope). Mirrors renderReviewPage:
// an ESCAPED JSON island (escapeJsonIsland — never HTML-interpolated) plus the
// external history.{js,css} assets under script-src 'self'. The history bundle is
// inlined into the Worker by bundle-editor-data.mjs (build_history.py output).
//
// The pre-rendered diffs[*].html is build-time escaped by tools/render_diff_lib
// (only <ins>/<del>/<details>/<summary> survive); the client assigns it via
// innerHTML safely. All other client text is rendered TEXT-NODE-ONLY by
// history.js — never interpolated into HTML here.

import HISTORY_BUNDLE from "../editor-data/history-bundle.generated.json" with { type: "json" };
import { escapeJsonIsland, escapeHtml } from "./editor-map.js";

const HTML_HEADERS = { "content-type": "text/html; charset=utf-8" };

// slug (data__firm__firm.json) <-> doc key (data/firm/firm.json). build_history's
// _slug is exactly relpath.replace("/", "__"); "__" only ever encodes a "/".
function slugToDoc(slug) {
  return (slug || "").replace(/__/g, "/");
}

// Look the doc up by slug against the bundle. Returns [docKey, docObj] or null.
function findDocBySlug(slug) {
  const docs = (HISTORY_BUNDLE && HISTORY_BUNDLE.docs) || {};
  const wanted = slugToDoc(slug);
  if (Object.prototype.hasOwnProperty.call(docs, wanted)) return [wanted, docs[wanted]];
  // Fallback: match on the doc object's own slug (robust to any encoding drift).
  for (const [k, v] of Object.entries(docs)) {
    if (v && v.slug === slug) return [k, v];
  }
  return null;
}

// The scope-appropriate revert hook. Editors (edit/instructor) *request*; the
// endpoint marks admin requests approved immediately (SL8). We expose the same
// endpoint URL to every scoped viewer — the button POSTs { doc, run:[first,last] }
// and the server decides request-vs-approve from the caller's scope. Null hook =>
// history.js renders the button DISABLED (its documented fallback).
const REVERT_ENDPOINT = "/edit/v1/revert-request";

// Per-doc page: island carries the doc's own bundle slice (revisions/diffs/…).
export function renderHistoryPage(docObj) {
  const island = escapeJsonIsland(docObj);
  const html =
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    "<base href=\"/edit/\">" +
    `<title>Change history — ${escapeHtml(docObj.doc)}</title>` +
    "<link rel=\"stylesheet\" href=\"/edit/assets/history.css\">" +
    "</head><body>" +
    "<div id=\"history-root\" aria-live=\"polite\">Loading…</div>" +
    `<script>window.__HX_REVERT__=${JSON.stringify(REVERT_ENDPOINT)};</script>` +
    `<script type="application/json" id="history-data">${island}</script>` +
    "<script src=\"/edit/assets/history.js\" defer></script>" +
    "</body></html>";
  return new Response(html, { status: 200, headers: HTML_HEADERS });
}

// Index page: a plain server-rendered list of every doc that has history, linking
// to /edit/history/<slug>. Doc keys are canonical relpaths (server constants), but
// we escapeHtml them anyway (defense in depth). No client island needed.
export function renderHistoryIndex() {
  const docs = (HISTORY_BUNDLE && HISTORY_BUNDLE.docs) || {};
  const entries = Object.values(docs)
    .sort((a, b) => (a.doc < b.doc ? -1 : a.doc > b.doc ? 1 : 0));
  const items = entries.map((d) => {
    const nrev = Array.isArray(d.revisions) ? d.revisions.length : 0;
    const nbl = Array.isArray(d.baselines) ? d.baselines.length : 0;
    return "<li><a href=\"/edit/history/" + escapeHtml(d.slug) + "\">" +
      escapeHtml(d.doc) + "</a> " +
      "<span class=\"hx-idx-meta\">" + nrev + " revision" + (nrev === 1 ? "" : "s") +
      (nbl ? " · " + nbl + " baseline(s)" : "") + "</span></li>";
  }).join("");
  const generated = escapeHtml(String(HISTORY_BUNDLE.generated_at || ""));
  const html =
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    "<title>Change history — Sonsteng</title>" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/history.css\">" +
    "</head><body><main id=\"history-root\">" +
    "<h1>Change history</h1><p><a href=\"/edit/publish\">Open Production Publisher</a></p>" +
    "<p>Redlined, attributed history for every canonical document. " +
    `Generated ${generated}.</p>` +
    (items ? "<ul class=\"hx-index\">" + items + "</ul>"
           : "<p>No document history available yet.</p>") +
    "</main></body></html>";
  return new Response(html, { status: 200, headers: HTML_HEADERS });
}

export { findDocBySlug };
