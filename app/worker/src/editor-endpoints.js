// editor-endpoints.js — the /edit/v1/* JSON handlers. Every mutation passes the
// CSRF guard; every source_ref/json_path is validated against the map ALLOWLIST
// server-side; `editor` and `original_text` are resolved SERVER-side (from the
// auth record + the map) and never read from the client body.

import { json } from "./errors.js";
import { csrfOk, editError } from "./editor-http.js";
import { attributionLabel } from "./editor-auth.js";
import {
  lookupBlock, lookupBlocks, validateJsonScalar, projectPendingItems, projectReviewAnnotations, MAP_VERSION,
  enumerateScope, EDITOR_MAP_FACTS, EDITOR_MAP,
} from "./editor-map.js";
import { STRUCTURAL_KINDS } from "./editor-store-core.js";
import { mintScopedConfirmation, verifyScopedConfirmation } from "./scoped-confirmation.js";
import { sha256Hex } from "./text-norm.js";

const DEFAULT_MAX_BYTES = 16 * 1024;

// "{#b:" is the durable-block-ID marker lead-in — reserved bytes that may never
// enter any suggested text (a payload carrying one could forge or corrupt block
// identity at apply time).
const RESERVED_MARKER = "{#b:";
const UNSAFE_PLAIN_TEXT = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u202a-\u202e\u2066-\u2069]/u;

export function normalizeTaxonomyPlainText(sourceRef, value) {
  if (!String(sourceRef || "").startsWith("data/taxonomy/")) return value;
  if (typeof value !== "string" || UNSAFE_PLAIN_TEXT.test(value)) return null;
  const normalized = value.normalize("NFC").replace(/\r\n?/g, "\n").trim();
  return normalized ? normalized : null;
}

// A structural payload is ONE block's plain text: single line, non-empty, no
// reserved marker bytes.
function validStructuralPayload(s) {
  return typeof s === "string" && s.trim().length > 0 &&
    !s.includes("\n") && !s.includes(RESERVED_MARKER);
}

// Validate + normalize a structural operation (U4, KTD3). `body` is the client
// request; `block` is the map-resolved anchor; `scope` is the granted scope the
// anchor resolved in. Returns { kind, new_text, op_arg } or { error: [code,
// message] }. Rules:
//   * structural ops address PROSE blocks only (never scalars/comment-only);
//   * insert_after needs a single-block payload;
//   * delete carries no payload;
//   * split carries exactly two single-block parts (stored "part1\n\npart2");
//   * merge/move carry op_arg — a second ref that must resolve in the SAME
//     scope's map, be prose, and live in the SAME source (file + body field).
function resolveStructuralOp(body, block, scope) {
  const op = body.op;
  if (!STRUCTURAL_KINDS.has(op)) return { error: ["validation_error", "Unknown operation."] };
  if (block.kind !== "prose")
    return { error: ["validation_error", "That operation applies to paragraphs only."] };

  const new_text = typeof body.new_text === "string" ? body.new_text : null;
  const new_text2 = typeof body.new_text2 === "string" ? body.new_text2 : null;
  const op_arg = typeof body.op_arg === "string" ? body.op_arg : null;

  // The base a ref must share for merge/move: file + json body field (the part
  // of the locator before the trailing .b<hex8> / #b<hex8> bid).
  const baseOf = (ref) => ref.replace(/(\.|#)b[0-9a-f]{8}$/, "");

  if (op === "insert_after") {
    if (!validStructuralPayload(new_text))
      return { error: ["validation_error", "Provide the new paragraph's text."] };
    return { kind: op, new_text: new_text.trim(), op_arg: null };
  }
  if (op === "delete") {
    if (new_text != null)
      return { error: ["validation_error", "Delete carries no text."] };
    return { kind: op, new_text: null, op_arg: null };
  }
  if (op === "split") {
    if (!validStructuralPayload(new_text) || !validStructuralPayload(new_text2))
      return { error: ["validation_error", "Provide both parts of the split."] };
    return { kind: op, new_text: new_text.trim() + "\n\n" + new_text2.trim(), op_arg: null };
  }
  // merge | move — need a second allowlisted ref in the same source.
  if (!op_arg) return { error: ["validation_error", "That operation needs a target."] };
  const target = lookupBlock(op_arg, scope);
  if (!target || target.kind !== "prose")
    return { error: ["validation_error", "That target is not editable."] };
  if (baseOf(op_arg) !== baseOf(block.source_ref))
    return { error: ["validation_error", "Both blocks must be in the same document."] };
  return { kind: op, new_text: null, op_arg };
}

function editorStub(env) {
  return env.EDITOR.getByName("global-v1");
}

function humanPublisher(auth) {
  return !!auth?.editor && auth?.scopes?.publisher?.granted === true &&
    auth.credential_channel === "access" && !auth.service;
}

// Ceilings resolved from deploy vars (fallback to the spec defaults).
function ceilingsFor(env) {
  const n = (v, d) => (parseInt(v, 10) > 0 ? parseInt(v, 10) : d);
  return {
    perEditorPending: n(env.EDIT_MAX_PENDING_PER_EDITOR, 200),
    dailyPerEditor: n(env.EDIT_MAX_DAILY_PER_EDITOR, 500),
    globalPending: n(env.EDIT_MAX_GLOBAL_PENDING, 5000),
    maxBytes: n(env.EDIT_MAX_BYTES, DEFAULT_MAX_BYTES),
    leaseMs: n(env.EDIT_LEASE_MS, 5 * 60 * 1000),
  };
}

async function readJson(request) {
  try { return await request.json(); } catch { return null; }
}

function byteLen(...strs) {
  return new TextEncoder().encode(strs.filter(Boolean).join("")).length;
}

// ---- POST /edit/v1/suggest (edit OR instructor scope) -----------------------
export async function suggestEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Missing edit request header or bad origin.", 403);

  // Scope: a block lives in exactly ONE map (public edit-map XOR instructor
  // bundle). An editor like John holds BOTH scopes, so we must NOT prefer one by
  // priority — that would send his public-page edits to the instructor index and
  // reject them. Instead resolve scope by which of his GRANTED maps actually
  // contains the source_ref (checked below, after we read the body).
  if (!auth.editor || (!auth.scopes.edit.granted && !auth.scopes.instructor.granted))
    return editError("forbidden", "No edit scope.", 403);

  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);

  const id = body.id;
  const source_ref = body.source_ref;
  const json_path = body.json_path != null ? String(body.json_path) : null;
  let new_text = typeof body.new_text === "string" ? body.new_text : null;
  const comment = typeof body.comment === "string" ? body.comment : null;
  const clientHash = typeof body.original_hash === "string" ? body.original_hash : null;
  const op = typeof body.op === "string" ? body.op : null;

  if (typeof id !== "string" || !/^[a-zA-Z0-9_-]{8,64}$/.test(id))
    return editError("validation_error", "A valid suggestion id (uuid) is required.", 400);
  if (typeof source_ref !== "string" && op !== "json_add")
    return editError("validation_error", "source_ref is required.", 400);
  if (op == null && new_text == null && comment == null)
    return editError("validation_error", "Provide new_text or a comment.", 400);
  // Reserved marker bytes may never enter suggested text (identity forgery).
  if ((new_text && new_text.includes(RESERVED_MARKER)) ||
      (comment && comment.includes(RESERVED_MARKER)))
    return editError("validation_error", "That text contains a reserved sequence.", 400);

  // Size ceiling -> graceful 413.
  const ceilings = ceilingsFor(env);
  if (byteLen(new_text, typeof body.new_text2 === "string" ? body.new_text2 : null,
              comment) > ceilings.maxBytes)
    return editError("too_large", "That change is too large. Please split it up.", 413);

  // json_add (U5): a NEW fact — no map block exists yet, so it validates
  // against the map's FACTS index instead of the block allowlist. The
  // source_ref is SERVER-constructed from the matter's facts file; the key is
  // a short slug; the value is the fact text. Queued for review (json_add is
  // not in AUTO_APPLY_KINDS — a new fact changes file shape).
  if (op === "json_add") {
    const matterSlug = typeof body.matter === "string" ? body.matter : "";
    const factKey = typeof body.fact_key === "string" ? body.fact_key : "";
    const factsMeta = (EDITOR_MAP_FACTS || {})[matterSlug];
    if (!factsMeta)
      return editError("validation_error", "That matter has no facts panel.", 400);
    if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(factKey))
      return editError("validation_error",
        "Give the fact a short name: lowercase letters, digits and dashes.", 400);
    if (!new_text || !new_text.trim())
      return editError("validation_error", "Provide the fact's text.", 400);
    const jsonPath = "custom_facts." + factKey;
    const addRef = factsMeta.file + "#" + jsonPath;
    const result = await editorStub(env).suggest({
      id,
      editor: auth.editor,
      scope: "edit",
      origin: "human",
      kind: "json_add",
      page: `matters/${matterSlug}/facts/index.html`,
      block_anchor: null,
      source_ref: addRef,
      json_path: jsonPath,
      original_text: null,
      original_hash: null,
      new_text: new_text.trim(),
      comment,
      context: "new fact: " + factKey,
      map_version: MAP_VERSION,
      group_id: null,
    }, ceilings, { directApply: env.DIRECT_APPLY === "true" });
    if (!result.ok)
      return editError(result.reason || "validation_error", "That fact could not be saved.", 400);
    return json({ ok: true, id, status: result.suggestion.status, replay: !!result.replay });
  }

  // ALLOWLIST: the source_ref MUST resolve in one of the caller's GRANTED maps.
  // Unknown in every granted scope (SSRF/forgery) -> validation_error (never a
  // write, never a file path). Resolve the scope by which granted map holds it.
  let scope = null;
  let block = null;
  if (auth.scopes.edit.granted) {
    const b = lookupBlock(source_ref, "edit");
    if (b) { scope = "edit"; block = b; }
  }
  if (!block && auth.scopes.instructor.granted) {
    const b = lookupBlock(source_ref, "instructor");
    if (b) { scope = "instructor"; block = b; }
  }
  if (!block) return editError("validation_error", "That block is not editable.", 400);

  // When the client identifies the page it is editing, resolve the exact
  // occurrence instead of attributing a shared edit to the first descriptor.
  const requestedPage = typeof body.page === "string" ? body.page : "";
  if (scope === "edit" && requestedPage) {
    const pageBlock = (lookupBlocks(source_ref, "edit") || []).find((item) =>
      item.page === requestedPage && Number.isInteger(item.index));
    if (!pageBlock)
      return editError("validation_error", "That block is not editable on this page.", 400);
    block = pageBlock;
  }

  let pageOverride = null;
  if (op === "page_override") {
    const page = requestedPage;
    const occurrences = (EDITOR_MAP.occurrences || {})[source_ref] || [];
    const surfaces = new Set(occurrences.map((item) => item.page));
    const occurrence = occurrences.find((item) => item.page === page);
    const pageBlock = occurrence && Number.isInteger(occurrence.index)
      ? (lookupBlocks(source_ref, "edit") || []).find((item) =>
          item.page === page && item.index === occurrence.index)
      : null;
    if (scope !== "edit" || block.kind === "comment_only" || surfaces.size < 2 || !pageBlock)
      return editError("validation_error", "This-page-only is available only for shared text on this page.", 400);
    if (new_text == null)
      return editError("validation_error", "Provide the page-only text.", 400);
    pageOverride = { kind: "page_override", page, index: occurrence.index };
  }
  if (op === "page_override_revert") {
    const page = requestedPage;
    const record = (EDITOR_MAP.overrides || []).find((item) =>
      item.source_ref === source_ref && item.page === page);
    const occurrence = ((EDITOR_MAP.occurrences || {})[source_ref] || [])
      .find((item) => item.page === page);
    if (scope !== "edit" || !record || block.kind !== "json_scalar" || !occurrence)
      return editError("validation_error", "That page-only copy is not available to revert.", 400);
    pageOverride = { kind: "page_override_revert", page, index: occurrence.index };
  }

  // json_scalar: json_path must match the map exactly (no path forgery).
  if (new_text != null && block.kind === "json_scalar") {
    if (!validateJsonScalar(source_ref, json_path, scope))
      return editError("validation_error", "That field cannot be edited that way.", 400);
    new_text = normalizeTaxonomyPlainText(source_ref, new_text);
    if (new_text == null)
      return editError("validation_error", "Taxonomy wording must be safe plain text.", 400);
  }
  // comment-only blocks accept comments only; prose/json_scalar accept new_text.
  if (block.kind === "comment_only" && new_text != null)
    return editError("validation_error", "That block can only be commented on.", 400);

  // Structural operation (U4): validate + normalize; the stored kind is the op.
  let structural = null;
  if (op != null && op !== "page_override" && op !== "page_override_revert") {
    structural = resolveStructuralOp(body, block, scope);
    if (structural.error)
      return editError(structural.error[0], structural.error[1], 400);
  }

  // The kind we STORE is derived from the map (+ comment/op intent), never trusted.
  const kind = pageOverride ? pageOverride.kind : structural ? structural.kind
    : new_text == null ? "comment" : block.kind;

  // Drift at suggest time: if the client's seen-hash disagrees with the map, the
  // page is stale — ask for a reload rather than pinning to old text.
  if (clientHash && clientHash !== block.original_hash)
    return editError("stale_page", "This page just updated — please reload and try again.", 409);

  const stub = editorStub(env);
  const result = await stub.suggest({
    id,
    editor: auth.editor,           // SERVER-resolved (slot identity)
    scope,
    origin: "human",
    kind,
    page: pageOverride ? pageOverride.page : block.page || null,
    block_anchor: pageOverride
      ? `${pageOverride.page}:${pageOverride.index}`
      : `${block.page || (block.matter_id + "/" + block.doc_type)}:${block.index}`,
    source_ref,                    // allowlisted
    json_path: block.kind === "json_scalar" ? block.json_path : null,
    original_text: block.original_text, // SERVER-resolved from the map
    original_hash: block.original_hash, // SERVER-authoritative
    new_text: structural ? structural.new_text : new_text,
    comment: pageOverride ? attributionLabel(auth.editor) : comment,
    context: block.context || "",
    map_version: MAP_VERSION,
    group_id: null,
    op_arg: structural ? structural.op_arg : null,
  }, ceilings, { directApply: env.DIRECT_APPLY === "true" });

  if (!result.ok) {
    const map = {
      too_large: [413, "That change is too large."],
      pending_ceiling: [429, "You have a lot of changes waiting for review already."],
      global_ceiling: [429, "The review queue is full right now. Please try later."],
      daily_cap: [429, "You've hit today's change limit. Thank you — pick back up tomorrow."],
      // id_conflict: the client reused an idempotency id with a NEW payload — it
      // must rotate the id and resend (never silently swallowed → no lost edit).
      id_conflict: [409, "That change needs a fresh send — please try again."],
      validation_error: [400, "That change could not be saved."],
    };
    const [status, message] = map[result.reason] || [400, "That change could not be saved."];
    return editError(result.reason, message, status);
  }
  return json({ ok: true, id, status: result.suggestion.status, replay: !!result.replay });
}

// ---- POST /edit/v1/system-suggest (admin/service scope) ---------------------
// The apply-engine's SYSTEM proposers (value-sync companions, ai_rewrite) post
// here. Unlike /suggest (human editors, origin:"human", edit/instructor scope),
// this endpoint requires ADMIN scope (reached only via the Bearer service token)
// and accepts ONLY system origins {companion, ai_rewrite}. Everything the store
// records — editor slot, original_text/hash, kind, page, json_path — is resolved
// SERVER-side from the map (the client body is never trusted); only origin,
// group_id, and the client uuid/new_text/comment come from the caller.
const SYSTEM_ORIGINS = new Set(["companion", "ai_rewrite"]);

export async function systemSuggestEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Missing edit request header or bad origin.", 403);

  // Admin/service scope ONLY (the apply engine's Bearer token). A human
  // edit/instructor token cannot reach this endpoint.
  if (!auth.editor || !auth.scopes.admin.granted)
    return editError("forbidden", "Admin/service scope required.", 403);

  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);

  const id = body.id;
  const source_ref = body.source_ref;
  const json_path = body.json_path != null ? String(body.json_path) : null;
  let new_text = typeof body.new_text === "string" ? body.new_text : null;
  const comment = typeof body.comment === "string" ? body.comment : null;
  const origin = typeof body.origin === "string" ? body.origin : null;
  const group_id = typeof body.group_id === "string" ? body.group_id : null;

  if (typeof id !== "string" || !/^[a-zA-Z0-9_-]{8,64}$/.test(id))
    return editError("validation_error", "A valid suggestion id (uuid) is required.", 400);
  if (typeof source_ref !== "string")
    return editError("validation_error", "source_ref is required.", 400);
  // System origins ONLY — reject "human" (that is the /suggest endpoint) and any
  // unknown origin (no client-chosen provenance beyond the two we sanction).
  if (!origin || !SYSTEM_ORIGINS.has(origin))
    return editError("validation_error", "origin must be companion or ai_rewrite.", 400);
  if (new_text == null && comment == null)
    return editError("validation_error", "Provide new_text or a comment.", 400);

  // Size ceiling -> graceful 413.
  const ceilings = ceilingsFor(env);
  if (byteLen(new_text, comment) > ceilings.maxBytes)
    return editError("too_large", "That change is too large. Please split it up.", 413);

  // ALLOWLIST: the source_ref MUST resolve in one of the maps (edit first, then
  // instructor — same dual-scope resolution as /suggest, minus the human's
  // per-scope grant gate: admin is the authority for both indices). Unknown in
  // BOTH (SSRF/forgery) -> validation_error (never a write, never a file path).
  let scope = null;
  let block = lookupBlock(source_ref, "edit");
  if (block) { scope = "edit"; }
  if (!block) {
    block = lookupBlock(source_ref, "instructor");
    if (block) { scope = "instructor"; }
  }
  if (!block) return editError("validation_error", "That block is not editable.", 400);

  // json_scalar: json_path must match the map exactly (no path forgery).
  if (new_text != null && block.kind === "json_scalar") {
    if (!validateJsonScalar(source_ref, json_path, scope))
      return editError("validation_error", "That field cannot be edited that way.", 400);
    new_text = normalizeTaxonomyPlainText(source_ref, new_text);
    if (new_text == null)
      return editError("validation_error", "Taxonomy wording must be safe plain text.", 400);
  }
  // comment-only blocks accept comments only.
  if (block.kind === "comment_only" && new_text != null)
    return editError("validation_error", "That block can only be commented on.", 400);

  // Structural operation (U5: drafted mentions of new facts arrive as
  // insert_after rows in an ai_rewrite group) — same validator as /suggest.
  let structural = null;
  if (typeof body.op === "string") {
    structural = resolveStructuralOp(body, block, scope);
    if (structural.error)
      return editError(structural.error[0], structural.error[1], 400);
  }

  // The kind we STORE is derived from the map (+ comment/op intent), never trusted.
  const kind = structural ? structural.kind
    : new_text == null ? "comment" : block.kind;

  const stub = editorStub(env);
  const result = await stub.suggest({
    id,
    editor: auth.editor,           // SERVER-resolved service slot identity
    scope,
    origin,                        // client-supplied but constrained to system set
    kind,
    page: block.page || null,
    block_anchor: `${block.page || (block.matter_id + "/" + block.doc_type)}:${block.index}`,
    source_ref,                    // allowlisted
    json_path: block.kind === "json_scalar" ? block.json_path : null,
    original_text: block.original_text, // SERVER-resolved from the map
    original_hash: block.original_hash, // SERVER-authoritative
    new_text: structural ? structural.new_text : new_text,
    comment,
    context: block.context || "",
    map_version: MAP_VERSION,
    group_id,                      // system suggestions carry the proposer's group
    op_arg: structural ? structural.op_arg : null,
  }, ceilings);

  if (!result.ok) {
    const map = {
      too_large: [413, "That change is too large."],
      pending_ceiling: [429, "The review queue for that editor is full."],
      global_ceiling: [429, "The review queue is full right now. Please try later."],
      daily_cap: [429, "Daily change limit reached."],
      validation_error: [400, "That change could not be saved."],
    };
    const [status, message] = map[result.reason] || [400, "That change could not be saved."];
    return editError(result.reason, message, status);
  }
  return json({ ok: true, id, status: result.suggestion.status, replay: !!result.replay });
}

// ---- GET /edit/v1/pending?page= (edit/instructor scope) ---------------------
export async function pendingEndpoint(request, env, auth) {
  if (!auth.editor || (!auth.scopes.edit.granted && !auth.scopes.instructor.granted))
    return editError("forbidden", "No edit scope.", 403);
  const url = new URL(request.url);
  const page = url.searchParams.get("page") || null;
  // Cross-editor overlay: for a page-scoped request, source EVERY editor's active
  // suggestions on that page (attribution stamped per-row by projectPendingItems)
  // so a co-editor's in-flight work paints too. The scope gate above (edit OR
  // instructor) already fenced this call. A page-less call keeps the caller's own
  // items — there is no page to scope a cross-editor read to.
  const stub = editorStub(env);
  const rows = page
    ? await stub.listForPage(page)
    : await stub.listForEditor(auth.editor, null);
  // Project to the client's #edits-data item shape (block_index/preview/note),
  // plus the SL6 liveness signals the client banner reads: heartbeat_age_s (null
  // if the daemon has never checked in) and direct_apply (auto-apply mode on/off).
  const heartbeat_age_s = await stub.heartbeatAgeS();
  const sourceRefs = page && EDITOR_MAP.pages?.[page]
    ? EDITOR_MAP.pages[page].map((block) => block.source_ref) : [];
  const reviewRows = sourceRefs.length && typeof stub.getDevReviewAnnotations === "function"
    ? await stub.getDevReviewAnnotations(sourceRefs) : [];
  return json({
    ok: true,
    items: projectPendingItems(rows),
    review_annotations: projectReviewAnnotations(reviewRows),
    heartbeat_age_s,
    direct_apply: env.DIRECT_APPLY === "true",
  });
}

// ---- POST /edit/v1/heartbeat (admin/service scope) --------------------------
// The home-box apply DAEMON beacons here after each run so the editor banner can
// honestly report whether auto-apply is live (SL6). Body: { ok, applied, ts }.
// Admin scope ONLY (reached via the Bearer service token) + the CSRF custom
// header — identical posture to /claim and /finalize. Age is measured from the
// worker's receive clock, so a skewed daemon clock cannot fake freshness.
export async function heartbeatEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  const beat = {
    ok: body.ok === true || body.ok === 1,
    applied: Number.isFinite(body.applied) ? body.applied : 0,
    ts: Number.isFinite(body.ts) ? body.ts : Date.now(),
  };
  const result = await editorStub(env).recordHeartbeat(beat);
  return json({ ok: true, received_at: result.received_at });
}

// ---- POST /edit/v1/revert-request (edit/instructor files; admin auto-approves)
// The History browser "Request revert" button POSTs { doc, run:[first,last] }.
// Editors (edit/instructor scope) FILE a request (status='requested'); an ADMIN
// caller's request is 'approved' immediately (SL8: editors request, admin
// executes). The home-box daemon consumes 'approved' rows on its next tick,
// git-reverts the run range on canonical, rebuilds + deploys, and marks 'done'.
// The request id is SERVER-generated (the client sends none). run shas are format-
// validated here; the daemon's git revert is the authoritative existence check.
const SHA_RE = /^[0-9a-f]{7,40}$/i;

export async function revertRequestEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Missing edit request header or bad origin.", 403);
  if (!auth.editor ||
      (!auth.scopes.edit.granted && !auth.scopes.instructor.granted && !auth.scopes.admin.granted))
    return editError("forbidden", "No edit scope.", 403);

  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  const doc = typeof body.doc === "string" ? body.doc : null;
  const run = Array.isArray(body.run) ? body.run : null;
  if (!doc || doc.length > 512 || /[\0\r\n]/.test(doc))
    return editError("validation_error", "A valid doc is required.", 400);
  if (!run || run.length !== 2)
    return editError("validation_error", "run must be [first_sha, last_sha].", 400);
  const [first, last] = run;
  if (typeof first !== "string" || typeof last !== "string" ||
      !SHA_RE.test(first) || !SHA_RE.test(last))
    return editError("validation_error", "run shas are invalid.", 400);

  // Admin scope files an already-approved request (executes on the next tick);
  // any other editor scope files a request that an admin must approve first.
  const approved = auth.scopes.admin.granted === true;
  const id = crypto.randomUUID();
  const result = await editorStub(env).fileRevertRequest({
    id, editor: auth.editor, doc, run_first: first, run_last: last, approved,
  });
  if (!result.ok) return editError(result.reason || "validation_error", "Could not file the revert request.", 400);
  return json({ ok: true, id: result.request.id, status: result.request.status, replay: !!result.replay });
}

// ---- GET /edit/v1/revert-requests?status=approved (admin/service) -----------
// The daemon polls this each tick for rows to execute (default status=approved).
export async function revertRequestsEndpoint(request, env, auth) {
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const url = new URL(request.url);
  const status = url.searchParams.get("status") || "approved";
  const items = await editorStub(env).listRevertRequests(status);
  return json({ ok: true, items });
}

// ---- POST /edit/v1/revert-resolve (admin/service) ---------------------------
// The admin approve path (requested -> approved) and the daemon's terminal write
// (approved -> done|failed). Body: { id, status, note? }.
export async function revertResolveEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.id !== "string" || typeof body.status !== "string")
    return editError("validation_error", "id and status are required.", 400);
  const note = typeof body.note === "string" ? body.note.slice(0, 2000) : null;
  const result = await editorStub(env).resolveRevertRequest(body.id, body.status, note);
  if (!result.ok) {
    const map = {
      not_found: [404, "Not found."],
      already_terminal: [409, "That request is already resolved."],
      validation_error: [400, "Invalid status."],
    };
    const [status, message] = map[result.reason] || [400, "Could not resolve the request."];
    return editError(result.reason, message, status);
  }
  return json({ ok: true, id: result.id, status: result.status });
}

export async function revertRecordEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  if (!["record","complete"].includes(body.action) ||
      typeof body.original_text !== "string" || typeof body.new_text !== "string" ||
      new TextEncoder().encode(body.original_text).byteLength > 131072 ||
      new TextEncoder().encode(body.new_text).byteLength > 131072)
    return editError("validation_error", "Invalid or oversized revert evidence.", 400);
  const binding = {
    id:body.id,batch_id:body.batch_id,actor:body.actor,kind:"history_revert",
    source_ref:body.source_ref,original_text:body.original_text,new_text:body.new_text,
    base_sha:body.base_sha,commit_sha:body.commit_sha,generator_id:body.generator_id,
    original_hash:await sha256Hex(body.original_text),new_hash:await sha256Hex(body.new_text),
    review_revision:body.review_revision && typeof body.review_revision === "object" ?
      body.review_revision : undefined,
  };
  const result = body.action === "record" ?
    await editorStub(env).recordCanonicalMutation(binding) :
    await editorStub(env).completeCanonicalMutation(binding);
  return json(result, result.ok ? (result.replay ? 200 : 201) : 409);
}

// ---- GET /edit/v1/scope (edit/instructor/admin) -----------------------------
// U6: deterministic blast-radius enumeration for the scope ladder. Read-only,
// map-derived, and cheap enough to show interactively while an editor chooses
// the scope of a broader change. Refs are capped (refs_truncated says so).
export async function scopeEndpoint(request, env, auth) {
  const anyScope = auth.editor && (auth.scopes.edit.granted ||
    auth.scopes.instructor.granted || auth.scopes.admin.granted);
  if (!anyScope) return editError("forbidden", "No edit scope.", 403);
  const url = new URL(request.url);
  const result = enumerateScope({
    level: url.searchParams.get("level"),
    matter: url.searchParams.get("matter") || undefined,
    part: url.searchParams.get("part") || undefined,
    module: url.searchParams.get("module") || undefined,
  });
  if (!result.ok)
    return editError("validation_error", "That scope could not be resolved.", 400);
  return json(result);
}

// ---- U7: scoped-change requests ---------------------------------------------
// POST /edit/v1/scoped-request (edit/instructor scope): file a natural-language
// change at a chosen scope. The radius is SERVER-enumerated (never client-
// supplied); above the ceiling the request refuses to file without explicit
// confirmation (KTD5 — ceiling settled at 100 blocks, Damien 2026-07-28).
// Drafting happens on the home box (tools/editor_scoped_drafts.py), which
// claims requests through the admin surface below and posts one ai_rewrite
// group via /system-suggest — reviewed and accepted as ONE unit (R7).
const SCOPED_CEILING_DEFAULT = 100;
const SCOPED_INSTRUCTION_MAX = 4000;

export async function scopedRequestEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Missing edit request header or bad origin.", 403);
  if (!auth.editor || (!auth.scopes.edit.granted && !auth.scopes.instructor.granted))
    return editError("forbidden", "No edit scope.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);

  const id = body.id;
  const instruction = typeof body.instruction === "string" ? body.instruction.trim() : "";
  if (typeof id !== "string" || !/^[a-zA-Z0-9_-]{8,64}$/.test(id))
    return editError("validation_error", "A valid request id is required.", 400);
  if (!instruction || instruction.length > SCOPED_INSTRUCTION_MAX)
    return editError("validation_error", "Describe the change in your own words (briefly).", 400);
  if (instruction.includes(RESERVED_MARKER))
    return editError("validation_error", "That text contains a reserved sequence.", 400);

  const radius = enumerateScope({
    level: body.level,
    matter: typeof body.matter === "string" ? body.matter : undefined,
    part: typeof body.part === "string" ? body.part : undefined,
    module: typeof body.module === "string" ? body.module : undefined,
  });
  if (!radius.ok)
    return editError("validation_error", "That scope could not be resolved.", 400);

  const n = parseInt(env.EDIT_SCOPED_CEILING, 10);
  const ceiling = n > 0 ? n : SCOPED_CEILING_DEFAULT;
  const confirmed = body.confirmed === true;
  const confirmationContext = {
    editor: auth.editor,
    scope: {
      level: body.level,
      matter: typeof body.matter === "string" ? body.matter : null,
      part: typeof body.part === "string" ? body.part : null,
      module: typeof body.module === "string" ? body.module : null,
    },
    radius: {
      blocks: radius.blocks,
      files: radius.files,
      matters: radius.matters.length,
    },
    instruction,
  };
  const confirmationValid = radius.blocks > ceiling && confirmed &&
    await verifyScopedConfirmation(
      env.SESSION_SIGNING_KEY, body.confirmation_token, confirmationContext
    );
  if (radius.blocks > ceiling && !confirmationValid) {
    // 409 carries the radius so the client can show the blast radius and ask
    // the editor to confirm. A retry must return the signed proof for this exact
    // editor, scope, radius, and wording; a stale/missing proof is re-challenged.
    const confirmationToken = await mintScopedConfirmation(
      env.SESSION_SIGNING_KEY, confirmationContext
    );
    return new Response(JSON.stringify({
      ok: false,
      error: { code: "ceiling_confirmation_required",
               message: "That change would touch " + radius.blocks +
                        " paragraphs. Please confirm you mean it that widely." },
      radius: { blocks: radius.blocks, files: radius.files,
                matters: radius.matters.length },
      confirmation_token: confirmationToken,
    }), { status: 409, headers: { "content-type": "application/json" } });
  }

  const result = await editorStub(env).fileScopedRequest({
    id,
    editor: auth.editor,                 // SERVER-resolved
    level: body.level,
    matter: typeof body.matter === "string" ? body.matter : null,
    part: typeof body.part === "string" ? body.part : null,
    module: typeof body.module === "string" ? body.module : null,
    instruction,
    radius_blocks: radius.blocks,        // SERVER-enumerated
    radius_files: radius.files,
    radius_matters: radius.matters.length,
    confirmed: confirmationValid,
  });
  if (!result.ok) return editError(result.reason || "validation_error", "Could not file the request.", 400);
  return json({ ok: true, id: result.request.id, status: result.request.status,
                phase: result.request.phase, replay: !!result.replay,
                radius: { blocks: radius.blocks, files: radius.files,
                          matters: radius.matters.length } });
}

// GET /edit/v1/scoped-requests?status= (admin/service): the drafter's poll.
export async function scopedRequestsEndpoint(request, env, auth) {
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const url = new URL(request.url);
  const status = url.searchParams.get("status") || null;
  const items = await editorStub(env).listScopedRequests(status);
  return json({ ok: true, items });
}

// POST /edit/v1/scoped-claim {id} (admin/service): requested -> drafting.
export async function scopedClaimEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.id !== "string")
    return editError("validation_error", "id required.", 400);
  const result = await editorStub(env).claimScopedRequest(body.id);
  return json(result, result.ok ? 200 : 409);
}

// POST /edit/v1/scoped-resolve {id, status, group_id?, phase?, canary_matter?,
// note?} (admin/service): every non-claim lifecycle move.
export async function scopedResolveEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.id !== "string" || typeof body.status !== "string")
    return editError("validation_error", "id and status are required.", 400);
  const result = await editorStub(env).resolveScopedRequest(body.id, {
    status: body.status,
    group_id: typeof body.group_id === "string" ? body.group_id : null,
    phase: typeof body.phase === "string" ? body.phase : null,
    canary_matter: typeof body.canary_matter === "string" ? body.canary_matter : null,
    note: typeof body.note === "string" ? body.note.slice(0, 2000) : null,
  });
  if (!result.ok) {
    const map = { not_found: [404, "Not found."],
                  illegal_transition: [409, "That request can't move to that state."] };
    const [status, message] = map[result.reason] || [400, "Could not resolve the request."];
    return editError(result.reason, message, status);
  }
  return json(result);
}

// GET /edit/v1/group-status?group_id= (admin/service): every member by status,
// terminal included — the canary gate reads this.
export async function groupStatusEndpoint(request, env, auth) {
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const url = new URL(request.url);
  const gid = url.searchParams.get("group_id");
  if (!gid) return editError("validation_error", "group_id required.", 400);
  const outcome = await editorStub(env).groupOutcome(gid);
  return json({ ok: true, outcome });
}

// ---- GET /edit/v1/review (admin; all pending grouped) -----------------------
export async function reviewJsonEndpoint(request, env, auth) {
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin scope required.", 403);
  const items = await editorStub(env).listAll();
  // Stamp a human attribution label onto each row from its server-resolved
  // `editor` identity ("slot:john" -> "JOS", "slot:roger" -> "RSH") so the review
  // surface distinguishes reviewers without exposing the slot plumbing.
  const withAttribution = items.map((it) => ({ ...it, attribution: attributionLabel(it.editor) }));
  return json({ ok: true, items: withAttribution });
}

// ---- POST /edit/v1/decide (admin) -------------------------------------------
export async function decideEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Missing edit request header or bad origin.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin scope required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);

  const action = body.action || "accept";
  const id = typeof body.id === "string" ? body.id : null;
  const group_id = typeof body.group_id === "string" ? body.group_id : null;
  const note = typeof body.note === "string" ? body.note.slice(0, 2000) : null;
  const stub = editorStub(env);

  let result;
  if (action === "reanchor") {
    if (!id) return editError("validation_error", "id required.", 400);
    result = await stub.reanchor(id, {});
  } else if (action === "accept" || action === "decline") {
    result = await stub.decide({ id, group_id, decision: action, note });
  } else {
    return editError("validation_error", "Unknown action.", 400);
  }

  if (!result.ok) {
    const map = {
      group_accept_required: [409, "Accept the whole group together."],
      illegal_transition: [409, "That item can't move to that state."],
      illegal_group_state: [409, "One item in the group can't be changed."],
      not_found: [404, "Not found."],
      validation_error: [400, "Invalid request."],
    };
    const [status, message] = map[result.reason] || [400, "Could not apply the decision."];
    return editError(result.reason, message, status);
  }
  return json({ ok: true, ...result });
}

// ---- GET /edit/v1/digest (admin) --------------------------------------------
export async function digestEndpoint(request, env, auth) {
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin scope required.", 403);
  const digest = await editorStub(env).digest();
  return json({ ok: true, digest });
}

// ---- apply-engine RPCs: claim | finalize | reconcile (admin/service) --------
export async function claimEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.batch_id !== "string")
    return editError("validation_error", "batch_id required.", 400);
  const result = await editorStub(env).claimBatch(body.batch_id, {
    base_sha: typeof body.base_sha === "string" ? body.base_sha : null,
    ids: Array.isArray(body.ids) ? body.ids : null,
  });
  return json(result, result.ok ? 200 : 409);
}

export async function finalizeEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.batch_id !== "string")
    return editError("validation_error", "batch_id required.", 400);
  const result = await editorStub(env).finalize(body.batch_id, {
    phase: body.phase,
    applied: body.applied, accepted_blocked: body.accepted_blocked,
    needs_human: body.needs_human, drift: body.drift, base_sha: body.base_sha,
    commit_sha: body.commit_sha, generator_id: body.generator_id,
    review_revisions:Array.isArray(body.review_revisions) ? body.review_revisions : undefined,
  });
  return json(result, result.ok ? 200 : 409);
}

export async function reconcileEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const result = await editorStub(env).reconcile();
  return json(result);
}

// Publisher review authority is human-only. Service credentials may later
// consume a frozen receipt to build a candidate, but may not create decisions.
export async function publisherReviewEndpoint(_request, env, auth) {
  if (!humanPublisher(auth)) return editError("forbidden", "A human Publisher using Access is required.", 403);
  return json(await editorStub(env).getPublisherReview(auth.editor));
}

export async function publisherReviewDraftEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!humanPublisher(auth)) return editError("forbidden", "A human Publisher using Access is required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  const result = await editorStub(env).savePublisherReviewDraft({ actor:auth.editor,
    review_revision_id:typeof body.review_revision_id === "string" ? body.review_revision_id : "",
    source_revision:typeof body.source_revision === "string" ? body.source_revision : "",
    prod_base:typeof body.prod_base === "string" ? body.prod_base : "",
    decisions:body.decisions });
  if (!result.ok) return editError(result.reason || "validation_error",
    "The review draft could not be saved.", ["stale_revision","stale_prod_base","revision_mismatch",
      "draft_owned","review_submitted","operation_mismatch"].includes(result.reason) ? 409 : 400);
  return json(result);
}

export async function publisherReviewSubmitEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!humanPublisher(auth)) return editError("forbidden", "A human Publisher using Access is required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  const text = (key) => typeof body[key] === "string" ? body[key] : "";
  const legacy = { review_revision_id:text("review_revision_id"),source_revision:text("source_revision"),
    prod_base:text("prod_base"),decisions:body.decisions };
  const sources = Array.isArray(body.sources) ? body.sources.map((source) => ({
    review_revision_id:typeof source?.review_revision_id === "string" ? source.review_revision_id : "",
    source_revision:typeof source?.source_revision === "string" ? source.source_revision : "",
    prod_base:typeof source?.prod_base === "string" ? source.prod_base : "",decisions:source?.decisions })) : [legacy];
  const binding = { id:text("id"),idempotency_key:text("idempotency_key"),sources };
  if (!sources.length || [binding.id,binding.idempotency_key,...sources.flatMap((source) =>
    [source.review_revision_id,source.source_revision,source.prod_base])].some((value) => !value || value.length > 256))
    return editError("validation_error", "Incomplete review binding.", 400);
  const request_digest = await sha256Hex(JSON.stringify(binding));
  const result = await editorStub(env).submitPublisherReview({ ...binding,request_digest,actor:auth.editor });
  if (!result.ok) return editError(result.reason || "validation_error",
    "The review was not submitted.", ["stale_revision","stale_prod_base","revision_mismatch",
      "draft_owned","draft_mismatch","partial_group","idempotency_conflict","review_exists"].includes(result.reason) ? 409 : 400);
  return json(result,result.replay ? 200 : 201);
}

// Human-only publication authority. Bearer credentials remain valid for the
// later executor API, but cannot mint or enlarge a production authorization.
export async function publisherAuthorizeEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!humanPublisher(auth))
    return editError("forbidden", "A human Publisher using Access is required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  const text = (key) => typeof body[key] === "string" ? body[key] : "";
  const id = text("id");
  const idempotency_key = text("idempotency_key");
  const target_batch_id = text("target_batch_id");
  const base_sha = text("base_sha");
  const candidate_sha = text("candidate_sha");
  const generator_id = text("generator_id");
  const evidence_hash = text("evidence_hash");
  const manifest_hash = text("manifest_hash");
  const membership_hash = text("membership_hash");
  if ([id,idempotency_key,target_batch_id,base_sha,candidate_sha,generator_id,
      evidence_hash,manifest_hash,membership_hash].some((value) => !value || value.length > 256))
    return editError("validation_error", "Incomplete release binding.", 400);
  const digestPayload = JSON.stringify({ id,target_batch_id,base_sha,candidate_sha,
    generator_id,evidence_hash,manifest_hash,membership_hash });
  const result = await editorStub(env).authorizeProductionRelease({
    id,idempotency_key,request_digest:await sha256Hex(digestPayload), actor:auth.editor,
    credential_channel:auth.credential_channel,target_environment:"production",target_batch_id,
    base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,
    membership_hash,
  });
  if (!result.ok) {
    const status = ["idempotency_conflict","release_exists","membership_mismatch","stale_member",
      "partial_group","stale_frontier","stale_target","stale_batch_evidence",
      "nonancestor_candidate","active_release","stale_base"].includes(result.reason) ? 409 : 400;
    return editError(result.reason || "validation_error", "Release authorization was rejected.", status);
  }
  return json(result, result.replay ? 200 : 201);
}

export async function publisherReleaseEndpoint(request, env, auth) {
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!auth?.scopes?.publisher?.granted && !auth?.scopes?.release_service?.granted)
    return editError("forbidden", "Publisher or release-service scope required.", 403);
  const id = new URL(request.url).searchParams.get("id");
  if (!id) return editError("validation_error", "id required.", 400);
  const release = await editorStub(env).getProductionRelease(id);
  return release ? json({ ok:true, release }) : editError("not_found", "Not found.", 404);
}

function releaseService(auth) {
  return auth?.credential_channel === "bearer" && auth?.scopes?.release_service?.granted;
}

export async function productionPrepareEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!releaseService(auth)) return editError("forbidden", "Release service required.", 403);
  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);
  const allowed = ["id","idempotency_key","target_batch_id","base_sha","candidate_sha",
    "generator_id","evidence_hash","manifest_hash"];
  if (allowed.some((key) => typeof body[key] !== "string" || !body[key] || body[key].length > 256))
    return editError("validation_error", "Incomplete release binding.", 400);
  const binding = Object.fromEntries(allowed.map((key) => [key,body[key]]));
  const result = await editorStub(env).prepareProductionRelease({ ...binding,
    request_digest:await sha256Hex(JSON.stringify(binding)), actor:auth.editor || "service:release",
    credential_channel:"bearer",target_environment:"production",ancestry_verified:body.ancestry_verified === true,
    expected_batch_ids:Array.isArray(body.expected_batch_ids) ? body.expected_batch_ids : undefined,
    expected_suggestion_ids:Array.isArray(body.expected_suggestion_ids) ? body.expected_suggestion_ids : undefined });
  return result.ok ? json(result, result.replay ? 200 : 201) :
    editError(result.reason || "validation_error", "Release preparation rejected.", 409);
}

export async function productionPreparationContextEndpoint(request, env, auth) {
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!releaseService(auth)) return editError("forbidden", "Release service required.", 403);
  return json({ ok:true, context:await editorStub(env).productionPreparationContext() });
}

export async function productionClaimEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!releaseService(auth)) return editError("forbidden", "Release service required.", 403);
  const body = await readJson(request) || {};
  const result = await editorStub(env).claimAuthorizedProductionRelease({
    id:typeof body.id === "string" ? body.id : undefined,
    lease_ms:Number.isInteger(body.lease_ms) ? body.lease_ms : undefined,
    actor:auth.editor || "service:release",credential_channel:"bearer" });
  return result.ok ? json(result) : editError(result.reason || "conflict", "Release claim rejected.", 409);
}

export async function productionRestoreClaimEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed","Bad request.",403);
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found","Not found.",404);
  if (!releaseService(auth)) return editError("forbidden","Release service required.",403);
  const body = await readJson(request);
  if (!body || typeof body.id !== "string")
    return editError("validation_error","Restore release id required.",400);
  const result = await editorStub(env).claimProductionRestore({ id:body.id,
    lease_ms:Number.isInteger(body.lease_ms) ? body.lease_ms : undefined,
    actor:auth.editor || "service:release",credential_channel:"bearer" });
  return result.ok ? json(result) : editError(result.reason || "conflict","Restore claim rejected.",409);
}

export async function productionRenewEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!releaseService(auth)) return editError("forbidden", "Release service required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.id !== "string" || typeof body.fencing_token !== "string")
    return editError("validation_error", "Incomplete lease renewal.", 400);
  const result = await editorStub(env).renewProductionReleaseLease({ id:body.id,
    fencing_token:body.fencing_token,
    lease_ms:Number.isInteger(body.lease_ms) ? body.lease_ms : undefined,
    actor:auth.editor || "service:release",credential_channel:"bearer" });
  return result.ok ? json(result) :
    editError(result.reason || "conflict", "Release lease renewal rejected.", 409);
}

export async function productionTransitionEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (env.PROD_RELEASE_LEDGER !== "true") return editError("not_found", "Not found.", 404);
  if (!releaseService(auth)) return editError("forbidden", "Release service required.", 403);
  const body = await readJson(request);
  if (!body || typeof body.id !== "string" || typeof body.state !== "string" ||
      typeof body.fencing_token !== "string")
    return editError("validation_error", "Incomplete transition.", 400);
  const result = await editorStub(env).transitionProductionRelease({ id:body.id,state:body.state,
    fencing_token:body.fencing_token,detail:body.detail,actor:auth.editor || "service:release",
    credential_channel:"bearer" });
  return result.ok ? json(result) : editError(result.reason || "conflict", "Release transition rejected.", 409);
}
