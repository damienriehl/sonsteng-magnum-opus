// editor-instructor.js — /edit/instructor/<matter>/<doc> (instructor scope).
//
// Serves the pre-rendered instructor HTML from the bundled instructor-bundle
// (facts / instructor notes / ANSWER KEYS). The doc HTML already carries
// data-ebsrc anchors (emitted by tools/build_instructor_bundle.py), so the same
// map mechanism makes it editable. SECURITY: the router returns a UNIFORM 404 for
// BOTH a missing doc AND insufficient scope (no oracle); this module only renders
// once the router has authorized. Same no-store/CSP/injection rules as the proxy.

import { escapeJsonIsland, escapeHtml, projectPendingItems, INSTRUCTOR_VERSION } from "./editor-map.js";

// Block descriptors for the instructor client (keyed by source_ref; the HTML is
// pre-anchored with data-ebsrc). No secrets beyond what the doc already shows to
// an instructor-scoped viewer.
function instructorBlockDescriptors(blocks) {
  return (blocks || []).map((b) => ({
    index: b.index,
    kind: b.kind,
    source_ref: b.source_ref,
    json_path: b.json_path || null,
    original_text: b.original_text,
    original_hash: b.original_hash,
    has_inline_formatting: !!b.has_inline_formatting,
    context: b.context || "",
  }));
}

// Render the instructor doc shell. `doc` is the bundle entry {matter_id, doc_type,
// source_ref, html, blocks}; `pending` is this editor's pending items for the doc.
export function renderInstructorDoc(doc, pending) {
  const base = `/edit/instructor/${escapeHtml(doc.matter_id)}/${escapeHtml(doc.doc_type)}/`;
  const mapIsland = escapeJsonIsland({
    version: INSTRUCTOR_VERSION,
    scope: "instructor",
    source_ref: doc.source_ref,
    blocks: instructorBlockDescriptors(doc.blocks),
  });
  const editsIsland = escapeJsonIsland({ items: projectPendingItems(pending) });
  const title = `${escapeHtml(doc.matter_id)} — ${escapeHtml(doc.doc_type)}`;

  // doc.html is server-generated, already-escaped HTML from the bundle. Client
  // suggestion text is NEVER interpolated here — only the escaped islands carry it.
  const html =
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    `<base href="${base}">` +
    `<meta name="editor-map-version" content="${escapeHtml(INSTRUCTOR_VERSION)}">` +
    `<title>${title}</title>` +
    "<link rel=\"stylesheet\" href=\"/edit/assets/editor.css\">" +
    "</head><body><main>" +
    doc.html +
    "</main>" +
    `<script type="application/json" id="editor-map-data">${mapIsland}</script>` +
    `<script type="application/json" id="edits-data">${editsIsland}</script>` +
    "<script src=\"/edit/assets/editor.js\" defer></script>" +
    "</body></html>";

  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}
