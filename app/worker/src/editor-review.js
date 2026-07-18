// editor-review.js — the admin review page served at /edit/review (admin scope).
//
// Lists ALL outstanding suggestions grouped by source_ref (the cumulative
// "all-pending" digest — any age, one sweep), with a word-level diff, per-item /
// per-group / bulk Accept-Decline, a drift re-anchor action, and a decline-note
// field. EVERYTHING client-authored is rendered TEXT-NODE-ONLY by review.js
// (served as an asset; script-src 'self') from an escaped JSON island — never
// interpolated into HTML here. Practicum-Press styled (review.css asset).

import { escapeJsonIsland, escapeHtml } from "./editor-map.js";

export function renderReviewPage(items) {
  const island = escapeJsonIsland({ items: items || [], generated_at: Date.now() });
  const html =
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    "<base href=\"/edit/\">" +
    "<title>Suggestion Review — Sonsteng</title>" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/editor.css\">" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/review.css\">" +
    "</head><body><main>" +
    "<header class=\"rv-head\"><h1>Suggestion Review</h1>" +
    "<p class=\"rv-sub\">All outstanding suggestions, grouped by source. Review the whole sweep at once.</p></header>" +
    "<div id=\"rv-root\" aria-live=\"polite\">Loading…</div>" +
    "</main>" +
    `<script type="application/json" id="review-data">${island}</script>` +
    "<script src=\"/edit/assets/review.js\" defer></script>" +
    "</body></html>";
  return new Response(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

// escapeHtml is re-exported only to signal that server-side HTML here uses it for
// any server constant; client text never touches HTML on the server.
export { escapeHtml };
