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

// Candidate previews are deliberately not the ordinary editor overlay. The
// candidate artifact is put in a unique-origin iframe with *no* sandbox
// capabilities; its own CSP denies every fetch and active behavior. Escaping
// the complete child document for srcdoc preserves the candidate bytes for
// rendering without letting them become parent-page markup.
export function renderPromotionPreview(projection) {
  const childCsp = "default-src 'none'; base-uri 'none'; form-action 'none'; " +
    "frame-ancestors 'none'; navigate-to 'none'; object-src 'none'";
  const child = "<!doctype html><html><head><meta charset=\"utf-8\">" +
    "<meta http-equiv=\"Content-Security-Policy\" content=\"" + escapeHtml(childCsp) + "\">" +
    "<meta name=\"referrer\" content=\"no-referrer\"></head><body>" +
    String(projection?.preview_html || "") + "</body></html>";
  const gates = Array.isArray(projection?.evidence?.gates) ? projection.evidence.gates : [];
  const reasons = Array.isArray(projection?.ai?.reasons) ? projection.ai.reasons : [];
  const evidence = gates.map((gate) => "<li>" + escapeHtml(String(gate?.name || "gate")) +
    ": " + escapeHtml(String(gate?.status || "unknown")) + "</li>").join("");
  const reasonRows = reasons.map((reason) => "<li>" + escapeHtml(String(reason)) + "</li>").join("");
  const confidence = projection?.score && Number.isFinite(projection.score.confidence)
    ? String(projection.score.confidence) : "unavailable";
  const html = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"referrer\" content=\"no-referrer\"><title>Bound candidate preview</title></head><body>" +
    "<main><h1>Candidate preview</h1><p>Attempt " + escapeHtml(projection?.attempt_id || "") +
    "</p><iframe title=\"Candidate content\" sandbox=\"\" referrerpolicy=\"no-referrer\" srcdoc=\"" +
    escapeHtml(child) + "\"></iframe><section><h2>Validation evidence</h2><ul>" + evidence +
    "</ul><p>Confidence: " + escapeHtml(confidence) + "</p><h2>AI review reasons</h2><ul>" +
    reasonRows + "</ul></section></main></body></html>";
  return new Response(html, { status: 200, headers: {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
  } });
}

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

export function renderReviewPage(items, reverts, promotions = [], lane = null, manifestEpoch = "") {
  // Stamp the human attribution label ("slot:roger" -> "RSH", "slot:john" ->
  // "JOS") onto every row from its server-resolved `editor` identity. This is the
  // SURFACE Damien actually reviews from — REVIEW_JS reads this embedded island
  // (it never fetches /edit/v1/review), so attribution must ride the page, not
  // only the JSON API. Additive: the raw slot identity is preserved.
  const withAttribution = (items || []).map((it) => ({ ...it, attribution: attributionLabel(it.editor) }));
  const island = escapeJsonIsland({ items: withAttribution, generated_at: Date.now() });
  const promotionIsland = escapeJsonIsland({ candidates: promotions, lane, manifest_epoch: manifestEpoch });
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
    "<section class=\"pr-review\" aria-labelledby=\"pr-heading\"><header><h2 id=\"pr-heading\">Promotion review</h2>" +
    "<p>Oldest candidates needing attention appear first.</p></header>" +
    "<div id=\"pr-alert\" class=\"pr-alert\" role=\"alert\" tabindex=\"-1\" hidden></div>" +
    "<div id=\"pr-root\" aria-live=\"polite\">Loading promotion queue…</div></section>" +
    renderRevertPanel(reverts) +
    "</main>" +
    `<script type="application/json" id="review-data">${island}</script>` +
    `<script type="application/json" id="promotion-data">${promotionIsland}</script>` +
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
