// editor-review.js — the admin review page served at /edit/review (admin scope).
//
// Lists ALL outstanding suggestions grouped by source_ref (the cumulative
// "all-pending" digest — any age, one sweep), with a word-level diff, per-item /
// per-group / bulk Accept-Decline, a drift re-anchor action, and a decline-note
// field. EVERYTHING client-authored is rendered TEXT-NODE-ONLY by review.js
// (served as an asset; script-src 'self') from an escaped JSON island — never
// interpolated into HTML here. Practicum-Press styled (review.css asset).

import { escapeJsonIsland, escapeHtml } from "./editor-map.js";
import { attributionLabel } from "./editor-auth.js";

// Server-rendered revert-request panel (History browser "Request revert"). Rows
// are server constants (doc paths + hex shas + slot-derived attribution), each
// escapeHtml'd defense-in-depth. Editors file 'requested'; admin files (and the
// daemon resolves) 'approved'/'done'/'failed'. This makes reverts visible on the
// review surface, per the revert-v1 contract.
function renderRevertPanel(reverts) {
  const rows = reverts || [];
  if (!rows.length) return "";
  const shortSha = (s) => (s && /^[0-9a-f]{7,}$/i.test(s) ? s.slice(0, 8) : String(s || ""));
  const li = rows.map((r) => {
    const who = attributionLabel(r.editor);
    return "<li class=\"rv-revert-row\" data-status=\"" + escapeHtml(r.status) + "\">" +
      "<span class=\"rv-revert-status\">" + escapeHtml(r.status) + "</span> " +
      "<span class=\"rv-revert-doc\">" + escapeHtml(r.doc) + "</span> " +
      "<span class=\"rv-revert-run\">" + escapeHtml(shortSha(r.run_first)) + "…" +
      escapeHtml(shortSha(r.run_last)) + "</span> " +
      "<span class=\"rv-revert-who\">" + escapeHtml(who) + "</span></li>";
  }).join("");
  return "<section class=\"rv-reverts\"><h2>Revert requests</h2>" +
    "<ul class=\"rv-revert-list\">" + li + "</ul></section>";
}

export function renderReviewPage(items, reverts) {
  // Stamp the human attribution label ("slot:roger" -> "RSH", "slot:john" ->
  // "JOS") onto every row from its server-resolved `editor` identity. This is the
  // SURFACE Damien actually reviews from — REVIEW_JS reads this embedded island
  // (it never fetches /edit/v1/review), so attribution must ride the page, not
  // only the JSON API. Additive: the raw slot identity is preserved.
  const withAttribution = (items || []).map((it) => ({ ...it, attribution: attributionLabel(it.editor) }));
  const island = escapeJsonIsland({ items: withAttribution, generated_at: Date.now() });
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
    renderRevertPanel(reverts) +
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
