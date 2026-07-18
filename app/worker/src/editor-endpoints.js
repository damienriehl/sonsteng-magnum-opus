// editor-endpoints.js — the /edit/v1/* JSON handlers. Every mutation passes the
// CSRF guard; every source_ref/json_path is validated against the map ALLOWLIST
// server-side; `editor` and `original_text` are resolved SERVER-side (from the
// auth record + the map) and never read from the client body.

import { json } from "./errors.js";
import { csrfOk, editError } from "./editor-http.js";
import {
  lookupBlock, validateJsonScalar, projectPendingItems, MAP_VERSION,
} from "./editor-map.js";

const DEFAULT_MAX_BYTES = 16 * 1024;

function editorStub(env) {
  return env.EDITOR.getByName("global-v1");
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

  // Scope: edit-scope suggests target the public map; instructor-scope suggests
  // target the instructor bundle. A caller with neither is uniform-rejected.
  const scope = auth.scopes.instructor.granted ? "instructor"
    : auth.scopes.edit.granted ? "edit" : null;
  if (!scope || !auth.editor) return editError("forbidden", "No edit scope.", 403);

  const body = await readJson(request);
  if (!body) return editError("validation_error", "Malformed JSON body.", 400);

  const id = body.id;
  const source_ref = body.source_ref;
  const json_path = body.json_path != null ? String(body.json_path) : null;
  const new_text = typeof body.new_text === "string" ? body.new_text : null;
  const comment = typeof body.comment === "string" ? body.comment : null;
  const clientHash = typeof body.original_hash === "string" ? body.original_hash : null;

  if (typeof id !== "string" || !/^[a-zA-Z0-9_-]{8,64}$/.test(id))
    return editError("validation_error", "A valid suggestion id (uuid) is required.", 400);
  if (typeof source_ref !== "string")
    return editError("validation_error", "source_ref is required.", 400);
  if (new_text == null && comment == null)
    return editError("validation_error", "Provide new_text or a comment.", 400);

  // Size ceiling -> graceful 413.
  const ceilings = ceilingsFor(env);
  if (byteLen(new_text, comment) > ceilings.maxBytes)
    return editError("too_large", "That change is too large. Please split it up.", 413);

  // ALLOWLIST: the source_ref MUST resolve in the correct scope's map. Unknown
  // (SSRF/forgery) -> validation_error (never a write, never a file path).
  const requestedKind = comment != null && new_text == null ? "comment" : null;
  const block = lookupBlock(source_ref, scope);
  if (!block) return editError("validation_error", "That block is not editable.", 400);

  // json_scalar: json_path must match the map exactly (no path forgery).
  if (new_text != null && block.kind === "json_scalar") {
    if (!validateJsonScalar(source_ref, json_path, scope))
      return editError("validation_error", "That field cannot be edited that way.", 400);
  }
  // comment-only blocks accept comments only; prose/json_scalar accept new_text.
  if (block.kind === "comment_only" && new_text != null)
    return editError("validation_error", "That block can only be commented on.", 400);

  // The kind we STORE is derived from the map (+ comment intent), never trusted.
  const kind = new_text == null ? "comment" : block.kind;

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
    page: block.page || null,
    block_anchor: `${block.page || (block.matter_id + "/" + block.doc_type)}:${block.index}`,
    source_ref,                    // allowlisted
    json_path: block.kind === "json_scalar" ? block.json_path : null,
    original_text: block.original_text, // SERVER-resolved from the map
    original_hash: block.original_hash, // SERVER-authoritative
    new_text,
    comment,
    context: block.context || "",
    map_version: MAP_VERSION,
    group_id: null,
  }, ceilings);

  if (!result.ok) {
    const map = {
      too_large: [413, "That change is too large."],
      pending_ceiling: [429, "You have a lot of changes waiting for review already."],
      global_ceiling: [429, "The review queue is full right now. Please try later."],
      daily_cap: [429, "You've hit today's change limit. Thank you — pick back up tomorrow."],
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
  const rows = await editorStub(env).listForEditor(auth.editor, page);
  // Project to the client's #edits-data item shape (block_index/preview/note).
  return json({ ok: true, items: projectPendingItems(rows) });
}

// ---- GET /edit/v1/review (admin; all pending grouped) -----------------------
export async function reviewJsonEndpoint(request, env, auth) {
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin scope required.", 403);
  const items = await editorStub(env).listAll();
  return json({ ok: true, items });
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
  });
  return json(result, result.ok ? 200 : 409);
}

export async function reconcileEndpoint(request, env, auth) {
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  if (!auth.scopes.admin.granted) return editError("forbidden", "Admin/service scope required.", 403);
  const result = await editorStub(env).reconcile();
  return json(result);
}
