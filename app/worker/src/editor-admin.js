// editor-admin.js — the tokenless admin landing page served at /edit/admin
// (admin scope), plus the editorial-flags projection it renders.
//
// WHY THIS MODULE IS PURE
// -----------------------
// Everything here takes data in and returns HTML. It never reads the Durable
// Object store, never touches `env`, and does no routing or auth. The router
// (editor.js) owns the scope gate and the store read; this module owns only the
// rendering. That split is what lets the whole page be unit-tested without a DO,
// a Request, or a fake env — and it is why an under-scoped request can take the
// uniform 404 in the router without this file having any say in it.
//
// WHY THE PAGE IS A COMPOSITION, NOT A NEW INFORMATION ARCHITECTURE (KTD5)
// -----------------------------------------------------------------------
// The review queue already has a page (`renderReviewPage`, /edit/review) and it
// already renders the revert-request panel alongside the queue — that is ONE
// surface, not two. The change history already has an index
// (`renderHistoryIndex`, /edit/history/). So this page summarizes and LINKS; it
// does not re-implement either table. Duplicating the review table here would
// create a second place where "what needs a decision" is defined, and the two
// would drift.
//
// The ONE genuinely new rendering is the editorial-flags list (R5). Every
// existing "who changed what" view is admin-scoped or is Damien's ntfy digest,
// so John and Roger have no way to see each other's work today. That gap is what
// `buildEditorialFlags` closes.
//
// WHY THERE IS NO JAVASCRIPT ON THIS PAGE
// ---------------------------------------
// EDIT_CSP (editor-http.js) is `script-src 'self'` with NO 'unsafe-inline', so an
// inline <script> would be blocked outright; `style-src` DOES allow
// 'unsafe-inline', so the page's own styling is an inline <style>. But the deeper
// reason is that this is the front door: it is the first thing an editor sees
// after signing in through Access, and the last surface that should be able to
// fail. A page with zero script cannot half-load. Every link is a plain <a>, the
// counts are server-rendered text, and there is no island to hydrate.
//
// WHY EVERYTHING IS ESCAPED EVEN THOUGH IT IS SERVER-RESOLVED
// -----------------------------------------------------------
// `editor`, `status` and `source_ref` are all written server-side (the endpoint
// layer resolves `editor`; the status machine owns `status`; `source_ref` is
// validated against the block allowlist before a row is ever inserted). None of
// them is client-authored. We still route every interpolation through
// `escapeHtml` from editor-map.js — the same helper the review and history
// renderers use — because "this field is server-owned" is a property of today's
// call graph, not an invariant this file can enforce. Defense in depth, one
// helper, no second escaping idiom.

import { escapeHtml } from "./editor-map.js";
import { attributionLabel } from "./editor-auth.js";

const HTML_HEADERS = { "content-type": "text/html; charset=utf-8" };

// The review queue's own status vocabulary, in the order a reviewer thinks about
// it: things awaiting a human decision first, things already moving last. Any
// status not listed (a future state) is appended alphabetically rather than
// dropped — a count that silently disappears is worse than an unfamiliar label.
const STATUS_ORDER = [
  "pending",
  "drift",
  "needs_human",
  "accepted_blocked",
  "accepted",
  "in_flight",
];

// Human-readable one-liners for the statuses a reviewer actually acts on. A
// status with no gloss renders bare — never a wrong explanation.
const STATUS_GLOSS = Object.freeze({
  pending: "waiting on your decision",
  drift: "the underlying text moved — needs re-anchoring",
  needs_human: "the apply run could not finish on its own",
  accepted_blocked: "accepted, but the apply run was blocked",
  accepted: "accepted, waiting for the apply run",
  in_flight: "being applied right now",
});

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// ---- small pure helpers -----------------------------------------------------

// Normalize an identity to its bare slot name. The store's `editor` column is
// "slot:<name>" (server-resolved, never echoed from a client), but a caller may
// hand us a bare slot for the viewer, so accept both forms. Mirrors the parsing
// in attributionLabel so the two can never disagree about what a slot is.
function slotOf(editorOrSlot) {
  if (typeof editorOrSlot !== "string" || !editorOrSlot) return "";
  const s = editorOrSlot.startsWith("slot:") ? editorOrSlot.slice(5) : editorOrSlot;
  return s.toLowerCase();
}

// Coerce a row timestamp to epoch ms. The store writes `created_at`/`updated_at`
// as INTEGER epoch-ms, but this function is fed by whatever the caller has, so an
// ISO string is accepted too. Anything unparseable returns null and the row is
// skipped — a flag with no defensible timestamp cannot be placed in a
// "since you last looked" list at all.
function toMillis(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value) {
    const ms = Date.parse(value);
    return Number.isNaN(ms) ? null : ms;
  }
  return null;
}

// The document path a suggestion belongs to. `source_ref` is
// "<relpath>#<json pointer>" (e.g. "data/matters/m01/exercise.json#sections…"),
// so the doc is everything before the first "#". Fall back to `page` (the
// rendered page a block lives on) and then to `doc` (the revert-request shape),
// so a caller passing an adjacent row shape still gets something honest rather
// than an empty cell.
function pathOf(row) {
  const src = typeof row.source_ref === "string" ? row.source_ref : "";
  if (src) {
    const hash = src.indexOf("#");
    const doc = hash >= 0 ? src.slice(0, hash) : src;
    if (doc) return doc;
  }
  if (typeof row.page === "string" && row.page) return row.page;
  if (typeof row.doc === "string" && row.doc) return row.doc;
  return "";
}

// The history browser's slug encoding, mirrored from editor-history.js
// (build_history's `_slug` is exactly relpath.replace("/", "__")). We only build
// a link for a path that looks like a canonical repo relpath; anything else —
// including a hostile path — renders as escaped TEXT with no href at all, so a
// crafted path can never become a URL the page invites you to click.
function historyHref(path) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._\-/]*$/.test(path)) return "";
  if (path.includes("..")) return "";
  return "/edit/history/" + path.replace(/\//g, "__");
}

// A URL we are willing to put in an href. Only absolute http(s) and site-root
// relative paths pass; "javascript:" and friends resolve to "" and the caller
// renders an honest "not configured" state instead of an unclickable trap.
function safeHref(url) {
  if (typeof url !== "string" || !url) return "";
  if (/^https?:\/\/[^\s"'<>]+$/i.test(url)) return url;
  if (/^\/[^\s"'<>]*$/.test(url)) return url;
  return "";
}

// Deterministic UTC stamp — "27 Jul 2026, 14:05 UTC". Deliberately NOT
// toLocaleString: the Worker's ICU data and the test runner's need not agree, and
// a timestamp that renders differently in CI than in production is a timestamp
// nobody can reason about. Returns "" for anything unparseable.
function formatWhen(iso) {
  const ms = toMillis(iso);
  if (ms == null) return "";
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// ---- R5: editorial flags ----------------------------------------------------

/**
 * What the OTHER editors changed since this viewer last looked.
 *
 * R5's "editorial flags" is not a marker somebody sets on a document; it is the
 * per-editor answer to "what did John/Roger do while I was away". So this is a
 * projection over the review rows, filtered three ways:
 *
 *   1. NOT the viewer's own work. You already know what you changed; a list that
 *      includes it is noise, and worse, it dilutes the one signal the list
 *      exists to carry.
 *   2. Newer than `lastSeenISO`. Strictly newer — a row whose timestamp equals
 *      the last-seen mark was visible on that visit, so re-showing it would make
 *      the list never drain.
 *   3. Attributable. A row whose `editor` does not resolve to a label cannot be
 *      shown as "X changed this", and an unattributed flag is not a flag.
 *
 * FIRST VISIT RETURNS EMPTY, ON PURPOSE. With `lastSeenISO === null` there is no
 * "since" to measure from, and the honest answer is "nothing new to report",
 * NOT "here is the entire history" — dumping every edit ever made on somebody's
 * first sign-in trains them to ignore the list forever. The plan's test scenario
 * pins this: empty, not an error.
 *
 * Malformed rows are skipped, never thrown on. This runs on the front door; a
 * single bad row must not be able to 500 the page that tells you how to work.
 *
 * @param {Array<object>} items   suggestion rows as returned by the store's listAll()
 * @param {string|null} viewerSlot  the viewer's slot ("john") or identity ("slot:john")
 * @param {string|null} lastSeenISO ISO timestamp of the viewer's previous visit, or null
 * @returns {Array<{attribution: string, path: string, when: string, status: string}>}
 *          most recent first
 */
export function buildEditorialFlags(items, viewerSlot, lastSeenISO) {
  if (!Array.isArray(items) || items.length === 0) return [];
  if (typeof lastSeenISO !== "string" || !lastSeenISO) return []; // first visit
  const since = toMillis(lastSeenISO);
  if (since == null) return []; // an unreadable mark is a first visit, not an error

  const viewer = slotOf(viewerSlot);
  const rows = [];
  for (const row of items) {
    if (!row || typeof row !== "object") continue;
    const editor = typeof row.editor === "string" ? row.editor : "";
    if (!editor) continue;
    if (viewer && slotOf(editor) === viewer) continue; // your own work
    const attribution = attributionLabel(editor);
    if (!attribution) continue; // unattributable — cannot honestly be flagged
    // updated_at moves when the row's status does; created_at is when the edit
    // was made. Prefer updated_at so a decision on an old row also surfaces.
    const ms = toMillis(row.updated_at) ?? toMillis(row.created_at);
    if (ms == null || ms <= since) continue;
    rows.push({ ms, attribution, path: pathOf(row), status: String(row.status ?? "") });
  }

  rows.sort((a, b) => b.ms - a.ms);
  return rows.map((r) => ({
    attribution: r.attribution,
    path: r.path,
    when: new Date(r.ms).toISOString(),
    status: r.status,
  }));
}

// ---- rendering --------------------------------------------------------------

// Tally the queue by status, in STATUS_ORDER, with unknown statuses appended
// alphabetically so a future state is visible rather than silently uncounted.
function countByStatus(items) {
  const counts = new Map();
  for (const row of Array.isArray(items) ? items : []) {
    if (!row || typeof row !== "object") continue;
    const status = String(row.status ?? "") || "unknown";
    counts.set(status, (counts.get(status) || 0) + 1);
  }
  const known = STATUS_ORDER.filter((s) => counts.has(s));
  const extra = [...counts.keys()].filter((s) => !STATUS_ORDER.includes(s)).sort();
  return [...known, ...extra].map((status) => ({ status, n: counts.get(status) }));
}

function plural(n, one, many) {
  return n === 1 ? one : many;
}

function renderQueue(items) {
  const rows = Array.isArray(items) ? items.filter((r) => r && typeof r === "object") : [];
  const total = rows.length;
  if (total === 0) {
    return "<section class=\"ad-card\"><h2>Review queue</h2>" +
      "<p class=\"ad-empty\">The queue is clear. Nothing is waiting on a decision.</p>" +
      "<p class=\"ad-go\"><a href=\"/edit/review\">Open the review queue</a></p>" +
      "</section>";
  }
  const tallies = countByStatus(rows).map(({ status, n }) => {
    const gloss = STATUS_GLOSS[status];
    return "<li class=\"ad-tally\">" +
      "<span class=\"ad-tally-n\">" + n + "</span> " +
      "<span class=\"ad-tally-status\">" + escapeHtml(status.replace(/_/g, " ")) + "</span>" +
      (gloss ? "<span class=\"ad-tally-gloss\">" + escapeHtml(gloss) + "</span>" : "") +
      "</li>";
  }).join("");
  return "<section class=\"ad-card\"><h2>Review queue</h2>" +
    "<p class=\"ad-lede\"><strong>" + total + "</strong> " +
    plural(total, "suggestion is", "suggestions are") + " outstanding.</p>" +
    "<ul class=\"ad-tallies\">" + tallies + "</ul>" +
    "<p class=\"ad-go\"><a href=\"/edit/review\">Open the review queue</a></p>" +
    "</section>";
}

// Revert requests already render in full on /edit/review (renderReviewPage takes
// them as its second argument), so this is a count and a door — not a second
// listing that would have to be kept in step with the first.
function renderReverts(reverts) {
  const rows = Array.isArray(reverts) ? reverts.filter((r) => r && typeof r === "object") : [];
  const open = rows.filter((r) => r.status === "requested" || r.status === "approved");
  if (rows.length === 0) {
    return "<section class=\"ad-card\"><h2>Revert requests</h2>" +
      "<p class=\"ad-empty\">Nobody has asked for a change to be rolled back.</p>" +
      "</section>";
  }
  return "<section class=\"ad-card\"><h2>Revert requests</h2>" +
    "<p class=\"ad-lede\"><strong>" + rows.length + "</strong> " +
    plural(rows.length, "request", "requests") + " on file" +
    (open.length ? ", " + open.length + " still open" : ", none still open") + ".</p>" +
    "<p class=\"ad-go\"><a href=\"/edit/review\">Review them alongside the queue</a></p>" +
    "</section>";
}

// R5's editorial flags. The empty state is a sentence, not a blank region: an
// empty list and a broken list look identical if you render nothing at all.
function renderFlags(flags, viewerLabel) {
  const rows = Array.isArray(flags) ? flags.filter((f) => f && typeof f === "object") : [];
  const who = viewerLabel ? escapeHtml(String(viewerLabel)) : "you";
  const head = "<section class=\"ad-card ad-flags\"><h2>Since your last visit</h2>" +
    "<p class=\"ad-sub\">What the other editors changed while " + who + " " +
    (viewerLabel ? "was" : "were") + " away.</p>";
  if (rows.length === 0) {
    return head + "<p class=\"ad-empty\">Nothing new since your last visit.</p></section>";
  }
  const li = rows.map((f) => {
    const path = String(f.path ?? "");
    const href = historyHref(path);
    const label = path ? escapeHtml(path) : "<span class=\"ad-nopath\">(no document recorded)</span>";
    const doc = href
      ? "<a class=\"ad-flag-doc\" href=\"" + escapeHtml(href) + "\">" + label + "</a>"
      : "<span class=\"ad-flag-doc\">" + label + "</span>";
    const when = String(f.when ?? "");
    const pretty = formatWhen(when);
    const stamp = pretty
      ? "<time class=\"ad-flag-when\" datetime=\"" + escapeHtml(when) + "\">" +
        escapeHtml(pretty) + "</time>"
      : "<span class=\"ad-flag-when\">" + escapeHtml(when) + "</span>";
    const status = String(f.status ?? "");
    return "<li class=\"ad-flag\">" +
      "<span class=\"ad-attr\">" + escapeHtml(String(f.attribution ?? "")) + "</span>" +
      doc +
      (status ? "<span class=\"ad-flag-status\">" + escapeHtml(status.replace(/_/g, " ")) + "</span>" : "") +
      stamp +
      "</li>";
  }).join("");
  return head + "<ul class=\"ad-flag-list\">" + li + "</ul></section>";
}

// R8. The public practicum IS the student view — a different origin, no editing
// chrome, no instructor material. The link is prominent and it says plainly what
// it shows, because the whole value of it is checking a change the way the person
// it was written for will meet it.
function renderStudentView(studentViewUrl) {
  const href = safeHref(studentViewUrl);
  if (!href) {
    return "<section class=\"ad-student\"><h2>Student view</h2>" +
      "<p class=\"ad-empty\">The student view is not configured on this deployment.</p>" +
      "</section>";
  }
  return "<section class=\"ad-student\"><h2>Student view</h2>" +
    "<p>See the practicum exactly as a student sees it — no editing chrome, " +
    "no instructor material, nothing signed in.</p>" +
    "<p class=\"ad-go ad-go-strong\"><a href=\"" + escapeHtml(href) + "\">" +
    "Open the practicum as a student</a></p>" +
    "</section>";
}

// Inline because EDIT_CSP allows 'unsafe-inline' for styles but not for scripts,
// and because this page must render correctly even if an asset request does not.
// Colours are the Practicum-Press palette from editor.css (--pp-*), taken through
// var() with literal fallbacks so the shared stylesheet stays the single source of
// truth when it loads and the page still looks like itself when it does not.
const ADMIN_CSS = `
:root{--pp-ink:#1a2b3a;--pp-paper:#faf7f0;--pp-accent:#7a1f2b;--pp-rule:#d8cfbf;--pp-focus:#c9a227}
*{box-sizing:border-box}
body{margin:0;background:var(--pp-paper,#faf7f0);color:var(--pp-ink,#1a2b3a);
 font:18px/1.55 "Iowan Old Style","Palatino Linotype","Book Antiqua",Palatino,Georgia,serif}
main.ad{max-width:52rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
.ad-rule{height:3px;width:3.5rem;background:var(--pp-accent,#7a1f2b);margin:0 0 1.2rem}
.ad-eyebrow{margin:0;font-size:.78rem;letter-spacing:.16em;text-transform:uppercase;
 color:#8a7f6d;font-weight:700}
.ad-head h1{font-size:2rem;font-weight:600;letter-spacing:-.015em;margin:.2em 0 .35em}
.ad-head{border-bottom:2px solid var(--pp-accent,#7a1f2b);
 padding-bottom:1.4rem;margin-bottom:1.8rem}
.ad-sub{margin:0;color:#5c5346;font-size:.95rem}
.ad-card{background:#fff;border:1px solid var(--pp-rule,#d8cfbf);border-radius:8px;
 padding:1.1rem 1.25rem;margin:0 0 1.1rem}
.ad-card h2,.ad-student h2{font-size:1.05rem;letter-spacing:.02em;margin:0 0 .5em;
 text-transform:uppercase;font-weight:700;color:#3a2f22}
.ad-lede{margin:0 0 .7em;font-size:1.05rem}
.ad-empty{margin:0;color:#6b5b46;font-style:italic}
.ad-tallies{list-style:none;margin:0 0 .8rem;padding:0;display:grid;gap:.35rem}
.ad-tally{display:flex;gap:.55rem;align-items:baseline;flex-wrap:wrap;
 border-top:1px solid var(--pp-rule,#d8cfbf);padding-top:.35rem}
.ad-tally:first-child{border-top:0}
.ad-tally-n{font-weight:700;min-width:2ch;text-align:right;font-variant-numeric:tabular-nums}
.ad-tally-status{font-weight:600}
.ad-tally-gloss{color:#6b5b46;font-size:.85rem}
.ad-go{margin:0}
.ad-go a{color:var(--pp-accent,#7a1f2b);font-weight:600;text-decoration:none;
 border-bottom:1px solid rgba(122,31,43,.35)}
.ad-go a:hover,.ad-go a:focus{border-bottom-color:var(--pp-accent,#7a1f2b)}
.ad-go a::after{content:" \\2192"}
.ad-flag-list{list-style:none;margin:0;padding:0}
.ad-flag{display:flex;gap:.6rem;align-items:baseline;flex-wrap:wrap;padding:.5rem 0;
 border-top:1px solid var(--pp-rule,#d8cfbf)}
.ad-flag:first-child{border-top:0}
.ad-attr{border-radius:99px;padding:.05rem .5rem;font-size:.72rem;font-weight:700;
 letter-spacing:.04em;background:#3a2f22;color:#f4efe4}
.ad-flag-doc{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.82rem;
 color:var(--pp-ink,#1a2b3a);word-break:break-all}
.ad-flag-status{font-size:.72rem;border-radius:99px;padding:.05rem .5rem;
 background:#f3ecdd;color:#664d03}
.ad-flag-when{margin-left:auto;font-size:.8rem;color:#6b5b46;white-space:nowrap}
.ad-nopath{font-style:italic;color:#6b5b46}
.ad-student{border:1px solid var(--pp-focus,#c9a227);background:rgba(201,162,39,.1);
 border-radius:8px;padding:1.1rem 1.25rem;margin:0 0 1.1rem}
.ad-student p{margin:0 0 .6em;font-size:.95rem;color:#4a4235}
.ad-go-strong a{font-size:1.02rem}
.ad-foot{margin:2rem 0 0;color:#8a7f6d;font-size:.85rem}
`;

/**
 * The admin landing page: one tokenless door onto everything the reviewers need.
 *
 * It opens on the review queue because that is the thing that actually waits on a
 * person. Below it: what the other editors did since you last looked (R5), the
 * revert requests, the change history, and the student view (R8).
 *
 * PURE. No env, no store, no request. The caller supplies:
 * @param {object}  arg0
 * @param {Array}   arg0.items          store listAll() rows (the review queue)
 * @param {Array}   arg0.reverts        listRevertRequests() rows
 * @param {Array}   arg0.flags          output of buildEditorialFlags()
 * @param {string}  arg0.viewerLabel    attribution label of the viewer ("DR")
 * @param {string}  arg0.studentViewUrl absolute URL of the public practicum
 * @returns {Response} text/html; charset=utf-8, status 200
 */
export function renderAdminPage({ items, reverts, flags, viewerLabel, studentViewUrl } = {}) {
  const who = viewerLabel ? escapeHtml(String(viewerLabel)) : "";
  const html =
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">" +
    "<title>Editor desk — Sonsteng</title>" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/editor.css\">" +
    "<style>" + ADMIN_CSS + "</style>" +
    "</head><body><main class=\"ad\">" +
    "<header class=\"ad-head\">" +
    "<div class=\"ad-rule\"></div>" +
    "<p class=\"ad-eyebrow\">Practicum editor</p>" +
    "<h1>The editor's desk</h1>" +
    "<p class=\"ad-sub\">" +
    (who ? "Signed in as <span class=\"ad-attr\">" + who + "</span> — everything " : "Everything ") +
    "the practicum needs from you opens from this page.</p>" +
    "</header>" +
    renderStudentView(studentViewUrl) +
    renderQueue(items) +
    renderFlags(flags, viewerLabel) +
    renderReverts(reverts) +
    "<section class=\"ad-card\"><h2>Change history</h2>" +
    "<p class=\"ad-lede\">Every canonical document, redlined and attributed, " +
    "revision by revision.</p>" +
    "<p class=\"ad-go\"><a href=\"/edit/history/\">Browse the change history</a></p>" +
    "</section>" +
    "<section class=\"ad-card\"><h2>Production Publisher</h2>" +
    "<p class=\"ad-lede\">Approved copy remains on DEV until a human Publisher authorizes an immutable production batch.</p>" +
    "<p class=\"ad-go\"><a href=\"/edit/publish\">Open Production Publisher</a></p></section>" +
    "<p class=\"ad-foot\">This page carries no secret in its address. " +
    "Bookmark it — signing in again is a code sent to your email.</p>" +
    "</main></body></html>";
  return new Response(html, { status: 200, headers: HTML_HEADERS });
}
