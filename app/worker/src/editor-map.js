// editor-map.js — the UNIVERSAL ALLOWLIST (docs/research/editor-apply-spec.md,
// prime invariant #1). Every client-influenced reference — the /edit/<path>
// proxy path, every source_ref, every json_path — is validated here, server-side,
// against the generator-emitted maps. Unknown => null (the caller returns a
// uniform 404 / validation_error). No client value ever reaches a fetch, file
// path, or JSON write unchecked.
//
// The two bundles are inlined at build time (scripts/bundle-editor-data.mjs copies
// build/*.generated.json into editor-data/). They are SERVER-ONLY (the instructor
// bundle carries answer keys) and never shipped to the public site.

import EDITOR_MAP from "../editor-data/editor-map.generated.json" with { type: "json" };
import INSTRUCTOR_BUNDLE from "../editor-data/instructor-bundle.generated.json" with { type: "json" };
import { attributionLabel } from "./editor-auth.js";

// spine_build_id pins map<->page compatibility. The injector stamps it into the
// page so a mismatched (stale) client reload is caught gracefully.
export const MAP_VERSION = EDITOR_MAP.spine_build_id || "";
// U5: the per-matter facts index (file + editable paths + addable slots) the
// json_add validation reads. Build-time truth from compute_scope_index's
// sibling FACTS_INDEX in build_site.py.
export const EDITOR_MAP_FACTS = EDITOR_MAP.facts || {};
export const INSTRUCTOR_VERSION = INSTRUCTOR_BUNDLE.spine_build_id || "";

// ---- Page-path allowlist (proxy SSRF guard) ---------------------------------
// The map's page keys are site-relative, e.g. "matters/m01-.../index.html".
// A request may address a page as the key itself, its directory form (trailing
// "index.html" dropped), or with/without a trailing slash. We build an exact
// lookup of every acceptable request form -> canonical page key. Nothing else
// resolves — no traversal, no wildcard, no upstream guess.
const PAGE_KEYS = Object.keys(EDITOR_MAP.pages || {});
const PATH_TO_PAGE = new Map();
for (const key of PAGE_KEYS) {
  PATH_TO_PAGE.set(key, key); // full "…/index.html"
  if (key.endsWith("/index.html")) {
    const dir = key.slice(0, -"index.html".length); // "matters/m01-.../"
    PATH_TO_PAGE.set(dir, key); // trailing slash
    PATH_TO_PAGE.set(dir.replace(/\/$/, ""), key); // no trailing slash
  }
}

// A path is structurally hostile if it contains traversal, a scheme, a protocol-
// relative prefix, a backslash, an embedded NUL/control char, or a leading slash
// beyond the single one we strip. These are rejected BEFORE any lookup.
function isHostilePath(raw) {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 512) return true;
  if (raw.includes("\\")) return true; // backslash
  if (/[\u0000-\u001f\u007f]/.test(raw)) return true; // control chars
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return true; // scheme: http:, javascript:, data:
  if (raw.startsWith("//")) return true; // protocol-relative //evil
  if (raw.split("/").some((seg) => seg === "..")) return true; // traversal
  return false;
}

// Resolve a raw /edit/<path> tail to { pageKey, blocks } or null. Decodes percent-
// encoding first (so %2e%2e traversal is caught), strips ONE leading slash.
export function resolvePagePath(rawPath) {
  let path = rawPath;
  try {
    path = decodeURIComponent(rawPath);
  } catch {
    return null; // malformed percent-encoding
  }
  if (path.startsWith("/")) path = path.slice(1);
  if (isHostilePath(path)) return null;
  const pageKey = PATH_TO_PAGE.get(path);
  if (!pageKey) return null;
  return {
    pageKey,
    blocks: EDITOR_MAP.pages[pageKey] || [],
    overrides: (EDITOR_MAP.overrides || []).filter((item) => item.page === pageKey),
  };
}

// Build the upstream URL for an allowlisted page key and assert it stays inside
// the EDIT_UPSTREAM origin+prefix (defense in depth over the allowlist). Returns
// a URL or null. editUpstream e.g. "https://sonsteng-dev.damienriehl.com/platform/".
export function buildUpstreamUrl(pageKey, editUpstream) {
  let base;
  try {
    base = new URL(editUpstream);
  } catch {
    return null;
  }
  const url = new URL(pageKey, base);
  // Cloudflare Pages canonicalizes explicit index.html URLs to their directory
  // with a 308. The injector deliberately refuses redirects, so request the
  // canonical directory form directly while retaining the map's stable key.
  if (url.pathname.endsWith("/index.html")) {
    url.pathname = url.pathname.slice(0, -"index.html".length);
  }
  if (url.origin !== base.origin) return null; // never leave the origin
  // pathname must stay under the upstream prefix (the base path).
  const basePath = base.pathname.endsWith("/") ? base.pathname : base.pathname + "/";
  if (!url.pathname.startsWith(basePath)) return null;
  if (url.search || url.hash) return null; // no query/fragment smuggling
  return url;
}

// ---- source_ref / json_path allowlist (suggest + apply validation) ----------
// Index every editable block by source_ref, with every page + descriptor. A
// source_ref that is not in this index cannot be suggested against. The first
// descriptor remains the server-authoritative source metadata used by existing
// suggest/apply flows; the full list preserves every render site.
const BLOCK_BY_SRCREF = new Map();
for (const [pageKey, blocks] of Object.entries(EDITOR_MAP.pages || {})) {
  for (const b of blocks) {
    const descriptors = BLOCK_BY_SRCREF.get(b.source_ref) || [];
    descriptors.push({ ...b, page: pageKey });
    BLOCK_BY_SRCREF.set(b.source_ref, descriptors);
  }
}

// Instructor blocks live in the instructor bundle (their own allowlist).
const INSTR_BLOCK_BY_SRCREF = new Map();
const INSTR_DOC_BY_KEY = new Map(); // "m01/facts" -> doc
for (const doc of INSTRUCTOR_BUNDLE.docs || []) {
  INSTR_DOC_BY_KEY.set(`${doc.matter_id}/${doc.doc_type}`, doc);
  for (const b of doc.blocks || []) {
    if (!INSTR_BLOCK_BY_SRCREF.has(b.source_ref)) {
      INSTR_BLOCK_BY_SRCREF.set(b.source_ref, { ...b, matter_id: doc.matter_id, doc_type: doc.doc_type });
    }
  }
}

// Look up an editable block for a suggest, honoring scope. Returns the block
// descriptor (incl. server-authoritative original_text/original_hash/kind/
// json_path) or null. This is the server-side gate — the client's proposed
// source_ref/json_path never bypass it.
export function lookupBlock(source_ref, scope) {
  const blocks = lookupBlocks(source_ref, scope);
  return blocks ? blocks[0] : null;
}

// Return every public render-site descriptor for a source_ref. Instructor docs
// do not share the public page occurrence contract, so their legacy descriptor
// is represented as a one-item list.
export function lookupBlocks(source_ref, scope) {
  const map = scope === "instructor" ? INSTR_BLOCK_BY_SRCREF : BLOCK_BY_SRCREF;
  const indexed = map.get(source_ref);
  if (!indexed) return null;
  return Array.isArray(indexed) ? indexed : [indexed];
}

// Validate a json_scalar suggest: the block must be kind json_scalar and its
// json_path must equal the map's json_path for that source_ref (no path forgery).
export function validateJsonScalar(source_ref, json_path, scope) {
  const blocks = lookupBlocks(source_ref, scope);
  const block = blocks && blocks[0];
  if (!block || block.kind !== "json_scalar") return null;
  if (blocks.some((candidate) =>
    candidate.kind !== "json_scalar" || candidate.json_path !== block.json_path)) return null;
  if (json_path != null && json_path !== block.json_path) return null;
  return block;
}

// ---- Instructor doc resolution ---------------------------------------------
// Resolve (matter, docType) to the pre-rendered doc, or null. doc_type is
// normalized: "instructor-notes"/"instructor_notes"/"notes" -> instructor_notes,
// "answer-key"/"answer_key"/"key" -> answer_key, "facts" -> facts.
const DOC_TYPE_ALIASES = {
  facts: "facts",
  "instructor-notes": "instructor_notes",
  instructor_notes: "instructor_notes",
  notes: "instructor_notes",
  "answer-key": "answer_key",
  answer_key: "answer_key",
  key: "answer_key",
};

export function resolveInstructorDoc(matter, docType) {
  if (typeof matter !== "string" || typeof docType !== "string") return null;
  if (!/^m\d{2}$/.test(matter)) return null; // strict matter id form
  const canonical = DOC_TYPE_ALIASES[docType.toLowerCase()];
  if (!canonical) return null;
  return INSTR_DOC_BY_KEY.get(`${matter}/${canonical}`) || null;
}

// ---- JSON island escaping (XSS-safe embedding) ------------------------------
// Serialize `obj` for embedding inside <script type="application/json">…</script>.
// Escapes the characters that could break out of the script element or the JSON
// text: <, >, & (so "</script>" and HTML entities are inert), and U+2028/U+2029
// (valid JSON, but invalid raw in a script/JS context). NEVER interpolate client
// text into HTML — this is the only sanctioned embedding path.
export function escapeJsonIsland(obj) {
  return JSON.stringify(obj)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

// Escape a plain string for safe insertion as HTML TEXT content (not attributes).
// Used only for server-owned constants; client text is never HTML-interpolated.
export function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

// Per-page block descriptor exposed to the editor client (the allowlist for that
// page). No secrets — original_text is the same text the page renders.
export function pageBlockDescriptors(blocks) {
  return blocks.map((b) => {
    const descriptor = {
      index: b.index,
      kind: b.kind,
      source_ref: b.source_ref,
      json_path: b.json_path || null,
      original_text: b.original_text,
      original_hash: b.original_hash,
      has_inline_formatting: !!b.has_inline_formatting,
      context: b.context || "",
    };
    const occurrences = (EDITOR_MAP.occurrences || {})[b.source_ref] || [];
    if (occurrences.length > 1) descriptor.occurrences = occurrences;
    return descriptor;
  });
}

// Project raw DO suggestion rows into the injected #edits-data item shape the
// editor client reads. Base shape: { block_index, source_ref, status, kind,
// preview, note? }. block_index is the trailing segment of block_anchor
// ("page:index"); preview is the comment text (comments) or the proposed
// new_text (edits), CAPPED (tooltip/summary).
//
// WYSIWYG-across-reloads overlay (pending-overlay client) — additive fields:
//   * new_text     — the FULL proposed text for EDIT kinds (prose/json_scalar),
//                    so the client can paint it into the block on load. Comments
//                    never carry it (they render as margin bubbles, not text).
//   * base_hash    — the suggestion's original_hash: the baseline it was authored
//                    against. The client stale-guards it against the block's
//                    CURRENT original_hash (map island); a moved source skips
//                    hydration and falls back to the pill-only status.
//   * map_version  — the suggestion's map_version, a second stale guard against
//                    the injected map island's version (spine_build_id drift).
//   * attribution  — JOS/RSH/… from the server-resolved editor identity (the
//                    Google-Docs "suggested by" signal; stamped exactly like the
//                    admin review surface, never trusted from the client body).
// All fields are additive; older clients ignore what they don't read.
export function projectPendingItems(items, previewMax = 200) {
  return (items || []).map((it) => {
    const anchor = it.block_anchor || "";
    const idx = parseInt(anchor.slice(anchor.lastIndexOf(":") + 1), 10);
    const isComment = it.kind === "comment";
    const preview = (isComment ? it.comment : it.new_text) || "";
    const out = {
      block_index: Number.isFinite(idx) ? idx : null,
      source_ref: it.source_ref,
      status: it.status,
      kind: it.kind,
      preview: preview.length > previewMax ? preview.slice(0, previewMax) : preview,
    };
    if (!isComment && typeof it.new_text === "string") out.new_text = it.new_text;
    if (it.original_hash != null) out.base_hash = it.original_hash;
    if (it.map_version != null) out.map_version = it.map_version;
    const attr = attributionLabel(it.editor);
    if (attr) out.attribution = attr;
    if (it.decision_note) out.note = it.decision_note;
    return out;
  });
}

export { EDITOR_MAP, INSTRUCTOR_BUNDLE };

// ---- U6: the scope ladder — deterministic enumeration -----------------------
// part -> matter -> module -> course resolve to exact block sets from THIS
// bundled map + its build-time `scopes` index (compute_scope_index in
// build_site.py — task-derived module membership). Enumeration runs BEFORE any
// model does (KTD4): the blast radius (matters/files/blocks) is known before a
// single token is spent. Pure + synchronous; nothing here touches the store.
const SCOPES = EDITOR_MAP.scopes || { matters: {}, modules: {} };
const SCOPE_REFS_CAP = 200;

function _allBlocks() {
  const out = [];
  for (const [page, blocks] of Object.entries(EDITOR_MAP.pages || {})) {
    for (const b of blocks) out.push({ page, ref: b.source_ref });
  }
  return out;
}

function _summarize(level, picked) {
  const files = new Set();
  const matters = new Set();
  const pages = new Set();
  for (const x of picked) {
    const f = x.ref.split("#", 1)[0];
    files.add(f);
    pages.add(x.page);
    const m = f.match(/^data\/matters\/([^/]+)\//);
    if (m) matters.add(m[1]);
  }
  const refs = picked.slice(0, SCOPE_REFS_CAP).map((x) => x.ref);
  return {
    ok: true, level,
    blocks: picked.length,
    files: files.size,
    pages: pages.size,
    matters: [...matters].sort(),
    refs,
    refs_truncated: picked.length > refs.length,
  };
}

export function enumerateScope({ level, matter, part, module: mod } = {}) {
  const bad = { ok: false, reason: "validation_error" };
  const all = _allBlocks();

  if (level === "course") {
    const r = _summarize(level, all);
    // course radius counts every matter, even ones whose blocks are all
    // currently outside data/matters (defensive — today they never are).
    r.matters = Object.keys(SCOPES.matters).sort();
    return r;
  }

  if (level === "matter") {
    if (!matter || !SCOPES.matters[matter]) return bad;
    return _summarize(level,
      all.filter((x) => x.ref.startsWith(`data/matters/${matter}/`)));
  }

  if (level === "part") {
    const meta = matter ? SCOPES.matters[matter] : null;
    if (!meta || !part || !meta.parts.includes(part)) return bad;
    const prefix = part === "matter"
      ? `data/matters/${matter}/matter.json#`
      : `data/matters/${matter}/${part}/`;
    return _summarize(level, all.filter((x) => x.ref.startsWith(prefix)));
  }

  if (level === "module") {
    const meta = mod ? SCOPES.modules[mod] : null;
    if (!meta) return bad;
    const prefixes = [meta.curriculum + "#"]
      .concat(meta.matters.map((s) => `data/matters/${s}/exercise/`));
    const picked = all.filter((x) => prefixes.some((p) => x.ref.startsWith(p)));
    const r = _summarize(level, picked);
    r.matters = meta.matters.slice();  // the module's members ARE the radius
    return r;
  }

  return bad;
}
