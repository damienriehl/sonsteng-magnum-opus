// editor-store-core.js — ALL EditorStore logic behind the minimal SQL adapter,
// so it runs both inside the Durable Object (ctx.storage.sql) and under
// node:sqlite in unit tests. editor-store.js is the thin DO wrapper.
//
// The sql adapter contract (same as budget-core.js): sql.exec(query, ...binds)
// -> { toArray(): rows }. Multi-statement schema strings run with no binds.
//
// Invariants enforced here (docs/research/editor-apply-spec.md):
//   * The DO is single-threaded: no await between a method's reads and writes,
//     so every method below is one atomic read-modify-write.
//   * `editor` and `original_text` are written ONLY from what the endpoint layer
//     resolved server-side — never echoed from the client (the endpoint passes
//     them; this core never accepts a client-supplied editor/original_text path).
//   * decide() is the SOLE writer of `accepted`. claim() is the sole writer of
//     `in_flight`. finalize()/reconcile() own the apply-time terminal states.
//   * Status machine + terminal enforcement is centralized in _transition().

import {
  STATUS, TERMINAL, ALLOWED_TRANSITIONS, canTransition,
  PROMOTION_STAGE, PROMOTION_TRANSITIONS,
} from "./editor-status.js";

// Kind vocabularies (U4, KTD3). Structural operations are ordinary suggestion
// rows carried through the ONE pipeline — but they never take the DIRECT_APPLY
// fast path (the plan's execution note: a structural op is single-block, and
// the gate is risk, not scope), and they never supersede (two inserts after
// one anchor are two distinct intents, not a re-edit).
export const STRUCTURAL_KINDS = new Set([
  "insert_after", "delete", "split", "merge", "move",
]);
// The ONLY kinds DIRECT_APPLY may auto-accept at suggest time.
export const AUTO_APPLY_KINDS = new Set([
  "prose", "json_scalar", "page_override", "page_override_revert",
]);

// Ceilings (docs/research/editor-apply-spec.md §Ceilings). Overridable per call.
export const CEILINGS = {
  perEditorPending: 200,
  dailyPerEditor: 500,
  globalPending: 5000,
  maxBytes: 16 * 1024, // 16KB new_text + comment, pre-insert
  leaseMs: 5 * 60 * 1000, // apply lease window
};

export const SCHEMA_SQL = `
  CREATE TABLE IF NOT EXISTS suggestions (
    id TEXT PRIMARY KEY,
    editor TEXT NOT NULL,
    scope TEXT NOT NULL,
    origin TEXT NOT NULL,
    kind TEXT NOT NULL,
    page TEXT,
    block_anchor TEXT,
    source_ref TEXT NOT NULL,
    json_path TEXT,
    original_text TEXT,
    original_hash TEXT,
    new_text TEXT,
    comment TEXT,
    context TEXT,
    map_version TEXT,
    group_id TEXT,
    supersedes TEXT,
    op_arg TEXT,
    status TEXT NOT NULL,
    decision_note TEXT,
    apply_batch_id TEXT,
    lease_expires_at INTEGER,
    client_fp TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_sug_status ON suggestions(status);
  CREATE INDEX IF NOT EXISTS idx_sug_srcref_status ON suggestions(source_ref, status);
  CREATE INDEX IF NOT EXISTS idx_sug_group ON suggestions(group_id);
  CREATE INDEX IF NOT EXISTS idx_sug_page_status ON suggestions(page, status);
  CREATE INDEX IF NOT EXISTS idx_sug_editor_status_created ON suggestions(editor, status, created_at);
  CREATE INDEX IF NOT EXISTS idx_sug_batch ON suggestions(apply_batch_id);

  CREATE TABLE IF NOT EXISTS suggest_counts (
    editor TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (editor, day)
  );

  CREATE TABLE IF NOT EXISTS apply_batches (
    batch_id TEXT PRIMARY KEY,
    base_sha TEXT,
    phase TEXT NOT NULL,
    lease_expires_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );

  -- Single-row apply-daemon liveness beacon (SL6 heartbeat). id is pinned to 1
  -- so recordHeartbeat is an upsert of the ONE latest run. received_at is the
  -- worker's own clock at record time — age is derived from it (never the
  -- daemon-supplied ts), so a skewed daemon clock can't fake freshness.
  CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ok INTEGER,
    applied INTEGER,
    ts INTEGER,
    received_at INTEGER NOT NULL
  );

  -- Revert requests (History browser "Request revert", SL8). Editors FILE a
  -- request (status='requested'); an ADMIN caller's request is 'approved' on
  -- arrival. The home-box apply daemon consumes 'approved' rows, git-reverts the
  -- run range on canonical, and marks them 'done' (or 'failed'). Visible on the
  -- review page + digest. run_first/run_last are the coalesced revision's [first,
  -- last] commit shas from the history bundle.
  -- Scoped-change requests (U7, KTD4/KTD5). An editor's natural-language
  -- instruction at a chosen scope, with its enumerated blast radius recorded at
  -- request time. The home-box drafter claims a request, drafts one edit per
  -- matched block as ONE ai_rewrite group, and module/course requests progress
  -- canary -> remainder only after the canary matter verifies clean.
  CREATE TABLE IF NOT EXISTS scoped_requests (
    id TEXT PRIMARY KEY,
    editor TEXT NOT NULL,
    level TEXT NOT NULL,
    matter TEXT,
    part TEXT,
    module TEXT,
    instruction TEXT NOT NULL,
    radius_blocks INTEGER NOT NULL,
    radius_files INTEGER,
    radius_matters INTEGER,
    confirmed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    group_id TEXT,
    canary_matter TEXT,
    note TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_scoped_status ON scoped_requests(status, created_at);

  CREATE TABLE IF NOT EXISTS revert_requests (
    id TEXT PRIMARY KEY,
    editor TEXT NOT NULL,
    doc TEXT NOT NULL,
    run_first TEXT NOT NULL,
    run_last TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_revert_status ON revert_requests(status, created_at);

  -- PROD promotion is deliberately separate from DEV suggestions/apply_batches.
  CREATE TABLE IF NOT EXISTS promotion_candidates (
    id TEXT PRIMARY KEY, principal TEXT NOT NULL, environment TEXT NOT NULL,
    source_ref TEXT NOT NULL, content_bytes INTEGER NOT NULL, content_json TEXT NOT NULL,
    storage_bytes INTEGER NOT NULL,
    stage TEXT NOT NULL, stage_at INTEGER NOT NULL, active_attempt_id TEXT NOT NULL,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_promotion_queue
    ON promotion_candidates(stage, created_at, id);
  CREATE TABLE IF NOT EXISTS promotion_attempts (
    id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, number INTEGER NOT NULL,
    prior_attempt_id TEXT, base_sha TEXT, evidence_hash TEXT, manifest_hash TEXT,
    created_at INTEGER NOT NULL, UNIQUE(candidate_id, number)
  );
  CREATE TABLE IF NOT EXISTS promotion_projections (
    attempt_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
    base_sha TEXT NOT NULL, evidence_hash TEXT NOT NULL, manifest_hash TEXT NOT NULL,
    preview_html TEXT NOT NULL, evidence_json TEXT NOT NULL,
    score_json TEXT, ai_json TEXT, projection_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS promotion_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL, type TEXT NOT NULL, from_stage TEXT, to_stage TEXT,
    actor TEXT NOT NULL, detail TEXT, created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_promotion_events ON promotion_events(candidate_id, seq);
  CREATE TABLE IF NOT EXISTS promotion_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL, decision TEXT NOT NULL, principal TEXT NOT NULL,
    rationale TEXT, base_sha TEXT NOT NULL, evidence_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL, created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS promotion_receipts (
    idempotency_key TEXT PRIMARY KEY, principal TEXT NOT NULL,
    environment TEXT NOT NULL, operation TEXT NOT NULL, resource TEXT NOT NULL,
    request_digest TEXT NOT NULL, evidence_hash TEXT, response TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS promotion_lane (
    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
    paused INTEGER NOT NULL, health TEXT NOT NULL, reason_code TEXT,
    lease_owner TEXT, lease_expires_at INTEGER, fencing_token INTEGER NOT NULL,
    active_candidate_id TEXT, updated_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS promotion_observations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, kind TEXT NOT NULL,
    resource TEXT NOT NULL, observed_id TEXT, digest TEXT, created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS promotion_releases (
    manifest_hash TEXT PRIMARY KEY, candidate_id TEXT, attempt_id TEXT,
    base_sha TEXT NOT NULL, commit_sha TEXT NOT NULL, pages_preview_id TEXT,
    pages_production_id TEXT, worker_version_id TEXT, editor_map_id TEXT,
    contract_hashes TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL
  );
`;

// Revert-request lifecycle states.
export const REVERT_STATUS = {
  REQUESTED: "requested", // editor filed; awaiting admin approval
  APPROVED: "approved",   // ready for the daemon to execute
  DONE: "done",           // daemon reverted + published
  FAILED: "failed",       // daemon could not revert (conflict/overlap) — never partial
};
const REVERT_TERMINAL = new Set([REVERT_STATUS.DONE, REVERT_STATUS.FAILED]);

// Fields returned to callers (never leak internal-only columns beyond these).
const SELECT_COLS =
  "id, editor, scope, origin, kind, page, block_anchor, source_ref, json_path, " +
  "original_text, original_hash, new_text, comment, context, map_version, " +
  "group_id, supersedes, op_arg, status, decision_note, apply_batch_id, lease_expires_at, " +
  "created_at, updated_at";

export class EditorStoreCore {
  constructor(sql, now = () => Date.now(), transaction = (fn) => fn()) {
    this.sql = sql;
    this.now = now;
    this.transaction = transaction;
  }

  initSchema() {
    this.sql.exec(SCHEMA_SQL);
    // Append-only column migration for DOs whose suggestions table predates the
    // client_fp column. Fresh tables already have it (SCHEMA_SQL) so the ALTER is
    // a no-op that throws "duplicate column" — swallowed. An existing pre-fp table
    // gets the column added once. Portable across the DO's sql.exec and the
    // node:sqlite test adapter (both throw a catchable error on duplicate).
    this._ensureColumn("suggestions", "client_fp", "TEXT");
    // Structural-operation operand (merge's second ref / move's destination).
    this._ensureColumn("suggestions", "op_arg", "TEXT");
    this._ensureColumn("promotion_candidates", "content_json", "TEXT NOT NULL DEFAULT '{}'");
    this._ensureColumn("promotion_candidates", "storage_bytes", "INTEGER NOT NULL DEFAULT 0");
    this.sql.exec("INSERT OR IGNORE INTO promotion_lane (id,version,paused,health,fencing_token,updated_at) VALUES (1,0,0,'healthy',0,?)", this.now());
  }

  _promotionReceipt(key) {
    return this._one("SELECT * FROM promotion_receipts WHERE idempotency_key=?", key);
  }

  _receipt(input, response, evidenceHash = null) {
    this.sql.exec(`INSERT INTO promotion_receipts
      (idempotency_key,principal,environment,operation,resource,request_digest,evidence_hash,response,created_at)
      VALUES (?,?,?,?,?,?,?,?,?)`, input.idempotency_key, input.principal,
      input.environment, input.operation, input.resource, input.request_digest,
      evidenceHash, JSON.stringify(response), this.now());
  }

  _replay(input, evidenceHash = null) {
    const row = this._promotionReceipt(input.idempotency_key);
    if (!row) return null;
    const same = row.principal === input.principal && row.environment === input.environment &&
      row.operation === input.operation && row.resource === input.resource &&
      row.request_digest === input.request_digest && (row.evidence_hash || null) === (evidenceHash || null);
    return same ? { ...JSON.parse(row.response), replay: true }
      : { ok: false, reason: "idempotency_conflict" };
  }

  _promotionCandidate(id) {
    return this._one("SELECT * FROM promotion_candidates WHERE id=?", id);
  }

  _promotionAttempt(id) {
    return this._one("SELECT * FROM promotion_attempts WHERE id=?", id);
  }

  _promotionEvent(candidateId, attemptId, type, actor, from = null, to = null, detail = null) {
    this.sql.exec(`INSERT INTO promotion_events
      (candidate_id,attempt_id,type,from_stage,to_stage,actor,detail,created_at)
      VALUES (?,?,?,?,?,?,?,?)`, candidateId, attemptId, type, from, to,
      actor || "system", detail == null ? null : JSON.stringify(detail), this.now());
  }

  createPromotionCandidate(input, limits = {}) {
    const cfg = { maxBytes: 16 * 1024, maxQueued: 5000,
      maxStoredBytes: 64 * 1024 * 1024, perPrincipalPerMinute: 60, ...limits };
    if (!input || !input.id || !input.principal || !input.idempotency_key || !input.request_digest ||
        typeof input.content_json !== "string")
      return { ok: false, reason: "validation_error" };
    if (input.id.length > 128 || input.idempotency_key.length > 256 ||
        String(input.source_ref || "").length > 512 ||
        new TextEncoder().encode(input.content_json).byteLength !== input.content_bytes ||
        !Number.isInteger(input.storage_bytes) || input.storage_bytes < input.content_bytes)
      return { ok: false, reason: "validation_error" };
    const identity = { ...input, operation: "create", resource: `${input.id}:${input.source_ref || ""}` };
    const replay = this._replay(identity);
    if (replay) return replay;
    if (input.environment !== "production" || !input.source_ref ||
        !Number.isInteger(input.content_bytes) || input.content_bytes < 0)
      return { ok: false, reason: "validation_error" };
    if (input.content_bytes > cfg.maxBytes) return { ok: false, reason: "too_large" };
    const since = this.now() - 60_000;
    const recent = this._one("SELECT COUNT(*) AS n FROM promotion_candidates WHERE principal=? AND created_at>=?", input.principal, since);
    if (Number(recent.n) >= cfg.perPrincipalPerMinute) return { ok: false, reason: "rate_exceeded" };
    const queued = this._one("SELECT COUNT(*) AS n, COALESCE(SUM(content_bytes),0) AS bytes FROM promotion_candidates WHERE stage NOT IN ('published','failed')");
    if (Number(queued.n) >= cfg.maxQueued) return { ok: false, reason: "queue_full" };
    const stored = this._one("SELECT COALESCE(SUM(storage_bytes),0) AS bytes FROM promotion_candidates");
    if (Number(stored.bytes) + input.storage_bytes > cfg.maxStoredBytes) return { ok: false, reason: "storage_full" };
    if (this._promotionCandidate(input.id)) return { ok: false, reason: "candidate_conflict" };
    const now = this.now();
    const attemptId = `${input.id}:1`;
    return this.transaction(() => {
      this.sql.exec(`INSERT INTO promotion_candidates
        (id,principal,environment,source_ref,content_bytes,content_json,storage_bytes,stage,stage_at,active_attempt_id,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,'saved',?,?,?,?)`, input.id, input.principal, input.environment,
        input.source_ref, input.content_bytes, input.content_json, input.storage_bytes, now, attemptId, now, now);
      this.sql.exec(`INSERT INTO promotion_attempts
        (id,candidate_id,number,created_at) VALUES (?,?,1,?)`, attemptId, input.id, now);
      this._promotionEvent(input.id, attemptId, "saved", input.principal, null, PROMOTION_STAGE.SAVED);
      const { content_json: _content, ...candidate } = this._promotionCandidate(input.id);
      const response = { ok: true, candidate, attempt: this._promotionAttempt(attemptId) };
      this._receipt(identity, response);
      return response;
    });
  }

  listPromotionCandidates(principal = null) {
    return principal
      ? this._all("SELECT * FROM promotion_candidates WHERE principal=? ORDER BY created_at,id", principal)
      : this._all("SELECT * FROM promotion_candidates ORDER BY created_at,id");
  }

  listPromotionSummaries(principal = null, limit = 100) {
    const bounded = Math.max(1, Math.min(100, Number(limit) || 100));
    const where = principal ? "WHERE c.principal=?" : "";
    const binds = principal ? [principal, bounded] : [bounded];
    return this._all(`SELECT c.id,c.principal,c.environment,c.source_ref,c.stage,c.stage_at,
      c.active_attempt_id,c.created_at,c.updated_at,a.id AS attempt_id,a.base_sha,
      a.evidence_hash,a.manifest_hash
      FROM promotion_candidates c JOIN promotion_attempts a ON a.id=c.active_attempt_id
      ${where} ORDER BY c.created_at DESC,c.id DESC LIMIT ?`, ...binds).map((row) => ({
        id: row.id, principal: row.principal, environment: row.environment,
        source_ref: row.source_ref, stage: row.stage, stage_at: row.stage_at,
        active_attempt_id: row.active_attempt_id, created_at: row.created_at,
        updated_at: row.updated_at, attempt: { id: row.attempt_id, base_sha: row.base_sha,
          evidence_hash: row.evidence_hash, manifest_hash: row.manifest_hash },
      }));
  }

  getPromotionCandidate(id) {
    const candidate = this._promotionCandidate(id);
    if (!candidate) return null;
    const attempt = this._promotionAttempt(candidate.active_attempt_id);
    const projection = attempt ? this._one("SELECT * FROM promotion_projections WHERE attempt_id=?", attempt.id) : null;
    const bound = projection && projection.candidate_id === candidate.id &&
      projection.base_sha === attempt.base_sha && projection.evidence_hash === attempt.evidence_hash &&
      projection.manifest_hash === attempt.manifest_hash ? {
        preview_html: projection.preview_html,
        evidence: JSON.parse(projection.evidence_json),
        score: projection.score_json ? JSON.parse(projection.score_json) : null,
        ai: projection.ai_json ? JSON.parse(projection.ai_json) : null,
      } : {};
    return { ...candidate, attempt, ...bound, events: this.listPromotionEvents(id) };
  }

  listPromotionEvents(id) {
    return this._all("SELECT * FROM promotion_events WHERE candidate_id=? ORDER BY seq", id)
      .map((row) => ({ ...row, detail: row.detail ? JSON.parse(row.detail) : null }));
  }

  claimPromotion(owner, leaseMs = 300000) {
    if (!owner || !Number.isFinite(leaseMs) || leaseMs <= 0) return { ok: false, reason: "validation_error" };
    const now = this.now();
    const lane = this.getPromotionLane();
    if (lane.paused || lane.health !== "healthy") return { ok: false, reason: "lane_unavailable" };
    if (lane.lease_owner && lane.lease_expires_at > now) return { ok: false, reason: "lease_held" };
    let candidate = lane.active_candidate_id ? this._promotionCandidate(lane.active_candidate_id) : null;
    if (!candidate || [PROMOTION_STAGE.PUBLISHED, PROMOTION_STAGE.FAILED].includes(candidate.stage))
      candidate = this._one("SELECT * FROM promotion_candidates WHERE stage='saved' ORDER BY created_at,id LIMIT 1");
    if (!candidate) return { ok: false, reason: "nothing_to_claim" };
    const token = Number(lane.fencing_token) + 1;
    this.sql.exec(`UPDATE promotion_lane SET lease_owner=?,lease_expires_at=?,fencing_token=?,
      active_candidate_id=?,version=version+1,updated_at=? WHERE id=1`, owner, now + leaseMs, token, candidate.id, now);
    if (candidate.stage === PROMOTION_STAGE.SAVED) {
      this.sql.exec("UPDATE promotion_candidates SET stage='validating',stage_at=?,updated_at=? WHERE id=? AND stage='saved'", now, now, candidate.id);
      this._promotionEvent(candidate.id, candidate.active_attempt_id, "transition", owner,
        PROMOTION_STAGE.SAVED, PROMOTION_STAGE.VALIDATING);
    }
    return { ok: true, candidate: this._promotionCandidate(candidate.id),
      attempt: this._promotionAttempt(candidate.active_attempt_id), fencing_token: token,
      lease_expires_at: now + leaseMs };
  }

  renewPromotionLease(owner, fencingToken, leaseMs = 300000) {
    const lane = this.getPromotionLane();
    if (lane.lease_owner !== owner || Number(lane.fencing_token) !== Number(fencingToken) || lane.lease_expires_at <= this.now())
      return { ok: false, reason: "stale_fence" };
    const expires = this.now() + leaseMs;
    this.sql.exec("UPDATE promotion_lane SET lease_expires_at=?,updated_at=? WHERE id=1", expires, this.now());
    return { ok: true, lease_expires_at: expires, fencing_token: Number(fencingToken) };
  }

  transitionPromotion(input) {
    const candidate = this._promotionCandidate(input.candidate_id);
    if (!candidate || candidate.active_attempt_id !== input.attempt_id) return { ok: false, reason: "not_found" };
    if (input.fencing_token > 0 && Number(this.getPromotionLane().fencing_token) !== Number(input.fencing_token))
      return { ok: false, reason: "stale_fence" };
    if (candidate.stage !== input.expected_stage) return { ok: false, reason: "stale_state" };
    const allowed = PROMOTION_TRANSITIONS[candidate.stage];
    if (!allowed || allowed.size === 0) return { ok: false, reason: "terminal_state" };
    if (!allowed.has(input.to)) return { ok: false, reason: "illegal_transition" };
    const now = this.now();
    this.sql.exec("UPDATE promotion_candidates SET stage=?,stage_at=?,updated_at=? WHERE id=? AND stage=?",
      input.to, now, now, candidate.id, input.expected_stage);
    this._promotionEvent(candidate.id, input.attempt_id, "transition", input.actor, candidate.stage, input.to, input.detail);
    return { ok: true, candidate: this._promotionCandidate(candidate.id) };
  }

  bindPromotionEvidence(input) {
    const c = this._promotionCandidate(input.candidate_id);
    if (!c || c.active_attempt_id !== input.attempt_id) return { ok: false, reason: "not_found" };
    if (![input.base_sha, input.evidence_hash, input.manifest_hash].every((x) => typeof x === "string" && x))
      return { ok: false, reason: "validation_error" };
    this.sql.exec("UPDATE promotion_attempts SET base_sha=?,evidence_hash=?,manifest_hash=? WHERE id=? AND base_sha IS NULL",
      input.base_sha, input.evidence_hash, input.manifest_hash, input.attempt_id);
    const attempt = this._promotionAttempt(input.attempt_id);
    if (attempt.base_sha !== input.base_sha || attempt.evidence_hash !== input.evidence_hash || attempt.manifest_hash !== input.manifest_hash)
      return { ok: false, reason: "immutable_evidence" };
    this._promotionEvent(c.id, input.attempt_id, "evidence_bound", input.actor, null, null,
      { base_sha: input.base_sha, evidence_hash: input.evidence_hash, manifest_hash: input.manifest_hash });
    return { ok: true, attempt };
  }

  bindPromotionProjection(input, limits = {}) {
    const maxBytes = Number.isInteger(limits.maxBytes) ? limits.maxBytes : 512 * 1024;
    const c = this._promotionCandidate(input?.candidate_id);
    const a = this._promotionAttempt(input?.attempt_id);
    if (!c || !a || c.active_attempt_id !== a.id) return { ok: false, reason: "not_found" };
    if (a.base_sha !== input.base_sha) return { ok: false, reason: "stale_base" };
    if (a.evidence_hash !== input.evidence_hash) return { ok: false, reason: "stale_evidence" };
    if (a.manifest_hash !== input.manifest_hash) return { ok: false, reason: "stale_manifest" };
    if (typeof input.preview_html !== "string") return { ok: false, reason: "validation_error" };
    // Reject recognizable secret material in the opaque candidate artifact.
    // Structured evidence below is allowlisted, so unrecognized provider-error
    // and credential fields are never serialized at all.
    if (/(?:authorization|api[_-]?key|cookie|secret)\s*[:=]\s*["']?[^\s"'<]{6,}|bearer\s+[a-z0-9._~-]{6,}/i.test(input.preview_html))
      return { ok: false, reason: "credential_material" };
    const gates = input.evidence?.gates;
    const reasons = input.ai?.reasons;
    if ((gates != null && (!Array.isArray(gates) || gates.length > 100)) ||
        (reasons != null && (!Array.isArray(reasons) || reasons.length > 20)))
      return { ok: false, reason: "projection_too_large" };
    const text = (value, max) => String(value ?? "").slice(0, max)
      .replace(/bearer\s+[a-z0-9._~-]{6,}/ig, "[redacted]")
      .replace(/(?:authorization|api[_-]?key|cookie|secret)\s*[:=]\s*[^\s,;]{4,}/ig, "[redacted]");
    const evidence = { gates: (gates || []).map((gate) => ({
      name: text(gate?.name || "gate", 200), status: text(gate?.status || "unknown", 40),
      ...(typeof gate?.summary === "string" ? { summary: text(gate.summary, 1000) } : {}),
    })) };
    const score = input.score && typeof input.score === "object" ? {
      confidence: Number.isFinite(input.score.confidence) ? input.score.confidence : null,
      deterministic_score: Number.isFinite(input.score.deterministic_score) ? input.score.deterministic_score : null,
      risk_delta: Number.isFinite(input.score.risk_delta) ? input.score.risk_delta : null,
    } : null;
    const ai = input.ai && typeof input.ai === "object" ? {
      disposition: text(input.ai.disposition || "unavailable", 40),
      reasons: (reasons || []).map((reason) => text(reason, 1000)),
    } : null;
    const normalized = { preview_html: input.preview_html, evidence, score, ai };
    const evidenceJson = JSON.stringify(evidence);
    const scoreJson = score ? JSON.stringify(score) : null;
    const aiJson = ai ? JSON.stringify(ai) : null;
    const projectionBytes = new TextEncoder().encode(JSON.stringify(normalized)).byteLength;
    if (projectionBytes > maxBytes)
      return { ok: false, reason: "projection_too_large" };
    const prior = this._one("SELECT * FROM promotion_projections WHERE attempt_id=?", a.id);
    if (prior) return prior.candidate_id === c.id && prior.base_sha === a.base_sha &&
      prior.evidence_hash === a.evidence_hash && prior.manifest_hash === a.manifest_hash &&
      prior.preview_html === normalized.preview_html && prior.evidence_json === evidenceJson &&
      (prior.score_json || null) === scoreJson && (prior.ai_json || null) === aiJson ? { ok: true, replay: true } :
      { ok: false, reason: "immutable_projection" };
    this.sql.exec(`INSERT INTO promotion_projections
      (attempt_id,candidate_id,base_sha,evidence_hash,manifest_hash,preview_html,
      evidence_json,score_json,ai_json,projection_bytes,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)`, a.id, c.id, a.base_sha, a.evidence_hash, a.manifest_hash,
      normalized.preview_html, evidenceJson, scoreJson, aiJson, projectionBytes, this.now());
    this._promotionEvent(c.id, a.id, "projection_bound", input.actor || "service:prod");
    return { ok: true };
  }

  decidePromotion(input) {
    if (!input || !input.idempotency_key || !input.request_digest)
      return { ok: false, reason: "validation_error" };
    const identity = { ...input, environment: "production", operation: "decision", resource: input.candidate_id };
    const replay = this._replay(identity, input.evidence_hash);
    if (replay) return replay;
    const c = this._promotionCandidate(input.candidate_id);
    const a = this._promotionAttempt(input.attempt_id);
    if (!c || !a || c.active_attempt_id !== a.id) return { ok: false, reason: "not_found" };
    if (c.stage !== PROMOTION_STAGE.AWAITING_APPROVAL) return { ok: false, reason: "stale_state" };
    if (a.base_sha !== input.base_sha) return { ok: false, reason: "stale_base" };
    if (a.evidence_hash !== input.evidence_hash) return { ok: false, reason: "stale_evidence" };
    if (a.manifest_hash !== input.manifest_hash) return { ok: false, reason: "stale_manifest" };
    if (!input.principal || !["approve", "decline"].includes(input.decision)) return { ok: false, reason: "validation_error" };
    return this.transaction(() => {
      const now = this.now();
      this.sql.exec(`INSERT INTO promotion_decisions
        (candidate_id,attempt_id,decision,principal,rationale,base_sha,evidence_hash,manifest_hash,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)`, c.id, a.id, input.decision, input.principal,
        input.rationale || null, input.base_sha, input.evidence_hash, input.manifest_hash, now);
      const decision = this._one("SELECT * FROM promotion_decisions WHERE id=last_insert_rowid()");
      const to = input.decision === "approve" ? PROMOTION_STAGE.PUBLISHING : PROMOTION_STAGE.FAILED;
      this.sql.exec("UPDATE promotion_candidates SET stage=?,stage_at=?,updated_at=? WHERE id=?", to, now, now, c.id);
      this._promotionEvent(c.id, a.id, input.decision === "approve" ? "approved" : "declined", input.principal, c.stage, to,
        { rationale: input.rationale || null, evidence_hash: input.evidence_hash });
      const response = { ok: true, decision, candidate: this._promotionCandidate(c.id) };
      this._receipt(identity, response, input.evidence_hash);
      return response;
    });
  }

  retryPromotion(input) {
    if (!input || !input.idempotency_key || !input.request_digest || !input.principal)
      return { ok: false, reason: "validation_error" };
    const identity = { ...input, environment: "production", operation: "retry", resource: input.candidate_id };
    const replay = this._replay(identity);
    if (replay) return replay;
    const c = this._promotionCandidate(input.candidate_id);
    const prior = this._promotionAttempt(input.prior_attempt_id);
    if (!c || !prior || c.active_attempt_id !== prior.id || c.stage !== PROMOTION_STAGE.FAILED)
      return { ok: false, reason: "stale_state" };
    return this.transaction(() => {
      const number = Number(prior.number) + 1;
      const id = `${c.id}:${number}`;
      const now = this.now();
      this.sql.exec("INSERT INTO promotion_attempts (id,candidate_id,number,prior_attempt_id,created_at) VALUES (?,?,?,?,?)",
        id, c.id, number, prior.id, now);
      this.sql.exec("UPDATE promotion_candidates SET stage='saved',stage_at=?,active_attempt_id=?,updated_at=? WHERE id=?",
        now, id, now, c.id);
      this._promotionEvent(c.id, id, "retry_authorized", input.principal,
        PROMOTION_STAGE.FAILED, PROMOTION_STAGE.SAVED, { prior_attempt_id: prior.id });
      const response = { ok: true, attempt: this._promotionAttempt(id), candidate: this._promotionCandidate(c.id) };
      this._receipt(identity, response);
      return response;
    });
  }

  getPromotionLane() { return this._one("SELECT * FROM promotion_lane WHERE id=1"); }

  setPromotionLane(input) {
    const lane = this.getPromotionLane();
    if (Number(input.expected_version) !== Number(lane.version)) return { ok: false, reason: "stale_state" };
    const health = input.health || lane.health;
    if (!["healthy", "stalled", "unavailable", "restore_failed"].includes(health)) return { ok: false, reason: "validation_error" };
    const now = this.now();
    this.sql.exec("UPDATE promotion_lane SET version=version+1,paused=?,health=?,reason_code=?,updated_at=? WHERE id=1",
      input.paused == null ? lane.paused : (input.paused ? 1 : 0), health, input.reason_code || null, now);
    this._promotionEvent(lane.active_candidate_id || "lane", "lane", "lane_changed", input.actor || "system", null, null,
      { paused: input.paused, health, reason_code: input.reason_code || null });
    return { ok: true, lane: this.getPromotionLane() };
  }

  recordPromotionObservation(input) {
    if (!input.actor || !input.kind || !input.resource) return { ok: false, reason: "validation_error" };
    this.sql.exec(`INSERT INTO promotion_observations
      (actor,kind,resource,observed_id,digest,created_at) VALUES (?,?,?,?,?,?)`,
      input.actor, input.kind, input.resource, input.observed_id || null, input.digest || null, this.now());
    return { ok: true };
  }

  listPromotionObservations() { return this._all("SELECT * FROM promotion_observations ORDER BY seq"); }

  recordPromotionRelease(input) {
    const required = ["manifest_hash", "base_sha", "commit_sha", "contract_hashes", "state"];
    if (!input || required.some((key) => typeof input[key] !== "string" || !input[key]))
      return { ok: false, reason: "validation_error" };
    const prior = this._one("SELECT * FROM promotion_releases WHERE manifest_hash=?", input.manifest_hash);
    const normalizedContracts = typeof input.contract_hashes === "string"
      ? input.contract_hashes : JSON.stringify(input.contract_hashes);
    if (prior) {
      const same = prior.base_sha === input.base_sha && prior.commit_sha === input.commit_sha &&
        prior.contract_hashes === normalizedContracts && prior.state === input.state;
      return same ? { ok: true, replay: true, release: prior }
        : { ok: false, reason: "immutable_release" };
    }
    this.sql.exec(`INSERT INTO promotion_releases
      (manifest_hash,candidate_id,attempt_id,base_sha,commit_sha,pages_preview_id,
       pages_production_id,worker_version_id,editor_map_id,contract_hashes,state,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, input.manifest_hash, input.candidate_id || null,
      input.attempt_id || null, input.base_sha, input.commit_sha, input.pages_preview_id || null,
      input.pages_production_id || null, input.worker_version_id || null,
      input.editor_map_id || null, normalizedContracts, input.state, this.now());
    return { ok: true, release: this.getPromotionRelease(input.manifest_hash) };
  }

  getPromotionRelease(manifestHash) {
    const row = this._one("SELECT * FROM promotion_releases WHERE manifest_hash=?", manifestHash);
    if (!row) return null;
    try { return { ...row, contract_hashes: JSON.parse(row.contract_hashes) }; }
    catch { return row; }
  }

  _ensureColumn(table, col, type) {
    try {
      this.sql.exec(`ALTER TABLE ${table} ADD COLUMN ${col} ${type}`);
    } catch { /* column already present — nothing to do */ }
  }

  // Payload fingerprint for the idempotency guard (SL — mirror of the fence fix):
  // a stable, order-sensitive digest of the CLIENT-authored payload (source_ref +
  // new_text + comment). Same id + same fingerprint = a true idempotent replay;
  // same id + DIFFERENT fingerprint = a client-id collision that must NOT be
  // silently swallowed (would lose the newer edit). FNV-1a 32-bit hex — no crypto,
  // synchronous (the DO forbids awaits inside a method).
  _fingerprint(source_ref, new_text, comment) {
    const s = `${source_ref || ""}\u0000${new_text == null ? "" : new_text}\u0000${comment == null ? "" : comment}`;
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return ("0000000" + h.toString(16)).slice(-8);
  }

  _day(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  _one(query, ...binds) {
    return this.sql.exec(query, ...binds).toArray()[0];
  }

  _all(query, ...binds) {
    return this.sql.exec(query, ...binds).toArray();
  }

  _get(id) {
    return this._one(`SELECT ${SELECT_COLS} FROM suggestions WHERE id=?`, id);
  }

  // Centralized status writer with terminal + allowed-transition enforcement.
  // Returns false (no write) if the transition is illegal or the row is terminal.
  _transition(id, to, extra = {}) {
    const row = this._one("SELECT status FROM suggestions WHERE id=?", id);
    if (!row) return false;
    if (!canTransition(row.status, to)) return false;
    const now = this.now();
    const sets = ["status=?", "updated_at=?"];
    const binds = [to, now];
    for (const [k, v] of Object.entries(extra)) {
      sets.push(`${k}=?`);
      binds.push(v);
    }
    binds.push(id);
    this.sql.exec(`UPDATE suggestions SET ${sets.join(", ")} WHERE id=?`, ...binds);
    return true;
  }

  // ---- suggest -------------------------------------------------------------
  // input carries ONLY server-resolved values (the endpoint resolved editor +
  // original_text from the scope record + the map — never from the client body).
  // ceilings overridable for tests. opts.directApply (DIRECT_APPLY mode) makes a
  // human EDIT (non-comment) transition straight to `accepted` at suggest-time.
  // Returns { ok, replay?, reason?, suggestion }.
  suggest(input, ceilings = CEILINGS, opts = {}) {
    const now = this.now();
    const day = this._day(now);
    const directApply = !!opts.directApply;
    const {
      id, editor, scope, origin, kind, page, block_anchor, source_ref, json_path,
      original_text, original_hash, new_text, comment, context, map_version, group_id,
      op_arg,
    } = input;
    const effKind = kind || "prose";
    const effOrigin = origin || "human";

    if (typeof id !== "string" || !id) return { ok: false, reason: "validation_error" };
    if (typeof editor !== "string" || !editor) return { ok: false, reason: "validation_error" };
    if (typeof source_ref !== "string" || !source_ref) return { ok: false, reason: "validation_error" };

    const fp = this._fingerprint(source_ref, new_text, comment);

    // Idempotent dedupe keyed by client uuid — but fingerprint-guarded so a reused
    // id carrying a DIFFERENT payload is NOT silently swallowed (that would drop
    // the newer edit). Same id + same payload = a true replay (network retry):
    // return the stored row. Same id + different payload = id_conflict; the client
    // must rotate its idempotency id and resend (never a second insert, never a
    // silent loss). Legacy rows with a NULL fingerprint fall back to plain replay.
    const existing = this._get(id);
    if (existing) {
      const fpRow = this._one("SELECT client_fp FROM suggestions WHERE id=?", id);
      const priorFp = fpRow ? fpRow.client_fp : null;
      if (priorFp != null && priorFp !== fp)
        return { ok: false, reason: "id_conflict", suggestion: existing };
      return { ok: true, replay: true, suggestion: existing };
    }

    // Size ceiling (bytes of the client-authored fields).
    const bytes =
      new TextEncoder().encode((new_text || "") + (comment || "")).length;
    if (bytes > ceilings.maxBytes) return { ok: false, reason: "too_large" };

    // Per-editor pending ceiling.
    const pendRow = this._one(
      "SELECT COUNT(*) AS n FROM suggestions WHERE editor=? AND status=?",
      editor, STATUS.PENDING
    );
    if (pendRow && pendRow.n >= ceilings.perEditorPending)
      return { ok: false, reason: "pending_ceiling" };

    // Global pending ceiling.
    const gRow = this._one("SELECT COUNT(*) AS n FROM suggestions WHERE status=?", STATUS.PENDING);
    if (gRow && gRow.n >= ceilings.globalPending)
      return { ok: false, reason: "global_ceiling" };

    // Daily cap (suggest_counts).
    const cRow = this._one("SELECT count FROM suggest_counts WHERE editor=? AND day=?", editor, day);
    const dailyCount = cRow ? cRow.count : 0;
    if (dailyCount >= ceilings.dailyPerEditor)
      return { ok: false, reason: "daily_cap" };

    // Atomic supersede: a fresh human edit by the SAME editor on the SAME
    // source_ref supersedes their still-pending prior edit (last edit wins).
    // Under DIRECT_APPLY the prior may already be `accepted` (auto-accepted, not
    // yet claimed by the daemon) — supersede that too so the daemon never applies
    // stale intent (an in_flight/leased row is left alone; the machine forbids it).
    // Companion/ai_rewrite suggestions never supersede (they are group members).
    const structuralNames = [...STRUCTURAL_KINDS].map((k) => `'${k}'`).join(",");
    const targetClause = effKind === "page_override"
      ? " AND kind='page_override' AND page=?"
      : " AND kind!='page_override'";
    const targetArgs = effKind === "page_override" ? [page] : [];
    if (effOrigin === "human" && !STRUCTURAL_KINDS.has(effKind)) {
      const priors = this._all(
        "SELECT id FROM suggestions WHERE editor=? AND source_ref=? AND status=? AND origin='human' " +
          `AND kind NOT IN (${structuralNames})${targetClause}`,
        editor, source_ref, STATUS.PENDING, ...targetArgs
      );
      for (const p of priors) this._transition(p.id, STATUS.SUPERSEDED);
      if (directApply) {
        const accs = this._all(
          "SELECT id FROM suggestions WHERE editor=? AND source_ref=? AND status=? AND origin='human' " +
            `AND kind NOT IN (${structuralNames})${targetClause}`,
          editor, source_ref, STATUS.ACCEPTED, ...targetArgs
        );
        for (const a of accs) this._transition(a.id, STATUS.SUPERSEDED);
      }
    }

    const supersedes = effOrigin === "human" && !STRUCTURAL_KINDS.has(effKind)
      ? (this._one(
          "SELECT id FROM suggestions WHERE editor=? AND source_ref=? AND status=?" +
            targetClause + " ORDER BY updated_at DESC LIMIT 1",
          editor, source_ref, STATUS.SUPERSEDED, ...targetArgs
        ) || {}).id || null
      : null;

    this.sql.exec(
      `INSERT INTO suggestions
       (id, editor, scope, origin, kind, page, block_anchor, source_ref, json_path,
        original_text, original_hash, new_text, comment, context, map_version,
        group_id, supersedes, op_arg, status, decision_note, apply_batch_id, lease_expires_at,
        client_fp, created_at, updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,?,?)`,
      id, editor, scope || "edit", effOrigin, effKind,
      page || null, block_anchor || null, source_ref, json_path || null,
      original_text != null ? original_text : null, original_hash || null,
      new_text != null ? new_text : null, comment != null ? comment : null,
      context || null, map_version || null, group_id || null, supersedes,
      op_arg || null, STATUS.PENDING, fp, now, now
    );

    this.sql.exec(
      "INSERT INTO suggest_counts (editor, day, count) VALUES (?,?,1) " +
        "ON CONFLICT(editor, day) DO UPDATE SET count = count + 1",
      editor, day
    );

    // DIRECT_APPLY (SL2): a human EDIT auto-accepts at suggest-time — the daemon
    // then claims + applies it. Comments and system origins (companion/ai_rewrite)
    // are NEVER auto-accepted; they stay pending for the admin decide gate. The
    // client never clears optimistically — this server-written status is the truth.
    if (directApply && effOrigin === "human" && AUTO_APPLY_KINDS.has(effKind)) {
      this._transition(id, STATUS.ACCEPTED);
    }

    return { ok: true, suggestion: this._get(id) };
  }

  // ---- heartbeat (SL6 apply-daemon liveness) -------------------------------
  // Upsert the single beacon row. Age is measured from received_at (this call's
  // clock), never the daemon-supplied ts.
  recordHeartbeat({ ok, applied, ts } = {}) {
    const now = this.now();
    this.sql.exec(
      "INSERT INTO heartbeat (id, ok, applied, ts, received_at) VALUES (1,?,?,?,?) " +
        "ON CONFLICT(id) DO UPDATE SET ok=excluded.ok, applied=excluded.applied, ts=excluded.ts, received_at=excluded.received_at",
      ok ? 1 : 0,
      Number.isFinite(applied) ? applied : 0,
      Number.isFinite(ts) ? ts : now,
      now
    );
    return { ok: true, received_at: now };
  }

  getHeartbeat() {
    const row = this._one("SELECT ok, applied, ts, received_at FROM heartbeat WHERE id=1");
    if (!row) return null;
    return { ok: !!row.ok, applied: row.applied, ts: row.ts, received_at: row.received_at };
  }

  // Age of the last heartbeat in whole seconds, or null if none recorded yet.
  heartbeatAgeS() {
    const row = this._one("SELECT received_at FROM heartbeat WHERE id=1");
    if (!row || !row.received_at) return null;
    return Math.max(0, Math.round((this.now() - row.received_at) / 1000));
  }

  // ---- reads ---------------------------------------------------------------
  // John's inline closure view: his own items in a non-superseded state, newest
  // first, optionally filtered by page. (superseded rows are hidden — drafts.)
  listForEditor(editor, page) {
    const hidden = [STATUS.SUPERSEDED];
    const params = [editor];
    let q = `SELECT ${SELECT_COLS} FROM suggestions WHERE editor=? AND status NOT IN ('${hidden.join("','")}')`;
    if (page) {
      q += " AND page=?";
      params.push(page);
    }
    q += " ORDER BY created_at DESC";
    return this._all(q, ...params);
  }

  // Cross-editor page overlay: EVERY editor's active (non-superseded) suggestions
  // on one page, newest first — the source for the WYSIWYG pending-overlay so an
  // editor sees co-editors' in-flight suggestions on the same page. Each row keeps
  // its own `editor` identity, so projectPendingItems stamps the right attribution
  // (JOS/RSH/…) downstream. Page is REQUIRED: this is a page-scoped read, never a
  // global dump — scope enforcement (edit/instructor) happens at the router before
  // this is ever called.
  listForPage(page) {
    // What the PAGE shows, which is not the same as what the review queue shows.
    //
    // DECLINED is hidden here (added 2026-07-27). A set-aside suggestion already
    // reverts the paragraph to its canonical wording, so all that remained was a
    // permanent "Not used · JOS" pill sitting on that block for every reader,
    // forever — Damien saw one left over from an E2E test and reasonably asked
    // what it meant. The outcome belongs in the review page and the digest, where
    // it is addressed to the person who needs it. The editor guide already makes
    // this promise: "anything Damien has set aside quietly returns to the original
    // wording." Quietly.
    //
    // SUPERSEDED was already hidden for the same reason: it is history, not state.
    const hidden = [STATUS.SUPERSEDED];
    return this._all(
      `SELECT ${SELECT_COLS} FROM suggestions WHERE page=? ` +
      `AND status NOT IN ('${hidden.join("','")}') ` +
      // A declined EDIT leaves nothing to show: the paragraph is already back to
      // its canonical wording, so only the pill remained. A declined COMMENT is
      // the opposite — the margin bubble IS the conversation, and dropping it
      // would erase what the reviewer said. Kind decides.
      `AND NOT (status='${STATUS.DECLINED}' AND kind<>'comment') ` +
      `ORDER BY created_at DESC`,
      page
    );
  }

  // Admin review: everything that needs a human decision or is mid-apply, so the
  // reviewer sweeps all outstanding work at once (the cumulative digest view).
  listAll() {
    const active = [
      STATUS.PENDING, STATUS.DRIFT, STATUS.NEEDS_HUMAN,
      STATUS.ACCEPTED_BLOCKED, STATUS.ACCEPTED, STATUS.IN_FLIGHT,
    ];
    return this._all(
      `SELECT ${SELECT_COLS} FROM suggestions WHERE status IN ('${active.join("','")}') ` +
        "ORDER BY source_ref ASC, created_at ASC"
    );
  }

  // ---- decide (SOLE writer of accepted) ------------------------------------
  // decision ∈ "accept" | "decline". For a grouped suggestion, accept MUST be a
  // whole-group operation: pass group_id. A lone-member accept (passing a single
  // id that belongs to a group) is rejected. Decline may target a single id.
  decide({ id, group_id, decision, note }) {
    const now = this.now();
    if (decision !== "accept" && decision !== "decline")
      return { ok: false, reason: "validation_error" };

    if (group_id) {
      const members = this._all(
        `SELECT id, status FROM suggestions WHERE group_id=?`, group_id
      );
      if (members.length === 0) return { ok: false, reason: "not_found" };
      const target = decision === "accept" ? STATUS.ACCEPTED : STATUS.DECLINED;
      // Group accept/decline is one atomic txn: every eligible member moves; if
      // ANY member cannot legally transition, the whole group is rejected.
      for (const m of members) {
        if (m.status === target) continue;
        if (!canTransition(m.status, target)) return { ok: false, reason: "illegal_group_state" };
      }
      const changed = [];
      for (const m of members) {
        if (m.status === target) continue;
        const extra = decision === "decline" && note ? { decision_note: note } : {};
        if (this._transition(m.id, target, extra)) changed.push(m.id);
      }
      return { ok: true, changed, group_id };
    }

    if (!id) return { ok: false, reason: "validation_error" };
    const row = this._one("SELECT id, group_id, status FROM suggestions WHERE id=?", id);
    if (!row) return { ok: false, reason: "not_found" };

    // Lone-member-of-group accept is forbidden: the group syndicates together.
    if (decision === "accept" && row.group_id) {
      const n = this._one("SELECT COUNT(*) AS n FROM suggestions WHERE group_id=?", row.group_id);
      if (n && n.n > 1) return { ok: false, reason: "group_accept_required" };
    }

    const target = decision === "accept" ? STATUS.ACCEPTED : STATUS.DECLINED;
    const extra = decision === "decline" && note ? { decision_note: note } : {};
    if (!this._transition(id, target, extra)) return { ok: false, reason: "illegal_transition" };
    return { ok: true, id, status: target };
  }

  // Drift recheck: pending/accepted -> drift (hash no longer matches source).
  markDrift(id) {
    return this._transition(id, STATUS.DRIFT) ? { ok: true } : { ok: false, reason: "illegal_transition" };
  }

  // Re-anchor a drifted item: drift -> pending (forces RE-REVIEW; never straight
  // to accepted). Optionally refresh the server-resolved original_text/hash.
  reanchor(id, { original_text, original_hash, map_version } = {}) {
    const extra = {};
    if (original_text != null) extra.original_text = original_text;
    if (original_hash != null) extra.original_hash = original_hash;
    if (map_version != null) extra.map_version = map_version;
    return this._transition(id, STATUS.PENDING, extra)
      ? { ok: true } : { ok: false, reason: "illegal_transition" };
  }

  // ---- apply-engine RPCs ---------------------------------------------------
  // Claim a batch: accepted -> in_flight for WHOLE groups only, stamp the lease +
  // apply_batch_id, and open the apply_batches journal (phase=claimed). If any
  // accepted item is a group member, all its group members must be accepted or
  // the group is skipped (never partially claimed).
  claimBatch(batchId, { base_sha, ids, leaseMs = CEILINGS.leaseMs } = {}) {
    const now = this.now();
    const existing = this._one("SELECT batch_id, phase FROM apply_batches WHERE batch_id=?", batchId);
    if (existing) return { ok: false, reason: "batch_exists", phase: existing.phase };

    const leaseExp = now + leaseMs;
    let candidates;
    if (Array.isArray(ids) && ids.length) {
      const ph = ids.map(() => "?").join(",");
      candidates = this._all(
        `SELECT id, group_id, status FROM suggestions WHERE id IN (${ph})`, ...ids
      );
    } else {
      candidates = this._all(
        "SELECT id, group_id, status FROM suggestions WHERE status=?", STATUS.ACCEPTED
      );
    }

    // Expand to whole groups and verify every member is accepted.
    const claimIds = new Set();
    const groups = new Set();
    for (const c of candidates) {
      if (c.status !== STATUS.ACCEPTED) continue;
      if (c.group_id) groups.add(c.group_id);
      else claimIds.add(c.id);
    }
    for (const g of groups) {
      const members = this._all("SELECT id, status FROM suggestions WHERE group_id=?", g);
      if (members.every((m) => m.status === STATUS.ACCEPTED)) {
        for (const m of members) claimIds.add(m.id);
      }
      // else: group not fully accepted -> skip (never partially claim a group).
    }

    if (claimIds.size === 0) return { ok: false, reason: "nothing_to_claim" };

    this.sql.exec(
      "INSERT INTO apply_batches (batch_id, base_sha, phase, lease_expires_at, created_at, updated_at) " +
        "VALUES (?,?,?,?,?,?)",
      batchId, base_sha || null, "claimed", leaseExp, now, now
    );
    for (const id of claimIds) {
      this._transition(id, STATUS.IN_FLIGHT, { apply_batch_id: batchId, lease_expires_at: leaseExp });
    }
    return { ok: true, batch_id: batchId, claimed: [...claimIds], lease_expires_at: leaseExp };
  }

  // Journal a phase transition, and (on outcome) resolve the batch's suggestions.
  // outcome for whole batch: undefined = just record phase; or per-status maps.
  finalize(batchId, { phase, applied, accepted_blocked, needs_human, drift, base_sha } = {}) {
    const now = this.now();
    const b = this._one("SELECT batch_id FROM apply_batches WHERE batch_id=?", batchId);
    if (!b) return { ok: false, reason: "no_batch" };

    if (phase) {
      const sets = ["phase=?", "updated_at=?"];
      const binds = [phase, now];
      if (base_sha != null) { sets.push("base_sha=?"); binds.push(base_sha); }
      binds.push(batchId);
      this.sql.exec(`UPDATE apply_batches SET ${sets.join(", ")} WHERE batch_id=?`, ...binds);
    }

    const move = (ids, to, extra = {}) => {
      for (const id of ids || []) this._transition(id, to, extra);
    };
    move(applied, STATUS.APPLIED, { lease_expires_at: null });
    move(accepted_blocked, STATUS.ACCEPTED_BLOCKED, { lease_expires_at: null });
    move(needs_human, STATUS.NEEDS_HUMAN, { lease_expires_at: null });
    move(drift, STATUS.DRIFT, { lease_expires_at: null });
    return { ok: true };
  }

  // Startup crash reconciliation: for every batch with an EXPIRED lease that is
  // not yet done, resolve limbo. pre-`merged` phases roll their in_flight items
  // BACK to accepted (re-queue); post-`merged` phases complete them to applied.
  // Any orphan in_flight row whose lease expired and whose batch is gone/rolled
  // is also swept back to accepted. (No await: pure DO-local read-modify-write.)
  reconcile() {
    const now = this.now();
    const preMerged = new Set(["claimed", "patched", "validated", "built", "parity_ok", "deployed"]);
    const swept = { rolled_back: [], completed: [], batches: 0 };

    const batches = this._all(
      "SELECT batch_id, phase, lease_expires_at FROM apply_batches WHERE phase NOT IN ('done','rolled_back')"
    );
    for (const b of batches) {
      const expired = (b.lease_expires_at || 0) <= now;
      if (!expired) continue;
      swept.batches++;
      const items = this._all(
        "SELECT id, status FROM suggestions WHERE apply_batch_id=?", b.batch_id
      );
      if (preMerged.has(b.phase)) {
        for (const it of items) {
          if (it.status === STATUS.IN_FLIGHT) {
            this._transition(it.id, STATUS.ACCEPTED, { apply_batch_id: null, lease_expires_at: null });
            swept.rolled_back.push(it.id);
          }
        }
        this.sql.exec(
          "UPDATE apply_batches SET phase='rolled_back', updated_at=? WHERE batch_id=?",
          now, b.batch_id
        );
      } else {
        // merged/done-adjacent: complete to applied (canonical already changed).
        for (const it of items) {
          if (it.status === STATUS.IN_FLIGHT) {
            this._transition(it.id, STATUS.APPLIED, { lease_expires_at: null });
            swept.completed.push(it.id);
          }
        }
        this.sql.exec(
          "UPDATE apply_batches SET phase='done', updated_at=? WHERE batch_id=?",
          now, b.batch_id
        );
      }
    }

    // Orphan in_flight rows (lease expired, no live batch) -> back to accepted.
    const orphans = this._all(
      "SELECT id FROM suggestions WHERE status=? AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
      STATUS.IN_FLIGHT, now
    );
    for (const o of orphans) {
      const live = this._one(
        "SELECT batch_id FROM apply_batches WHERE batch_id=(SELECT apply_batch_id FROM suggestions WHERE id=?) AND phase NOT IN ('done','rolled_back')",
        o.id
      );
      if (!live) {
        this._transition(o.id, STATUS.ACCEPTED, { apply_batch_id: null, lease_expires_at: null });
        swept.rolled_back.push(o.id);
      }
    }
    return { ok: true, ...swept };
  }

  // ---- revert requests (History browser "Request revert", SL8) -------------
  // File a request. `approved` (admin caller) short-circuits the approval gate so
  // the daemon executes it on the next tick; otherwise it lands 'requested' for an
  // admin to approve. Idempotent by client id (a replay returns the stored row).
  fileRevertRequest({ id, editor, doc, run_first, run_last, approved = false, note = null } = {}) {
    const now = this.now();
    if (typeof id !== "string" || !id) return { ok: false, reason: "validation_error" };
    if (typeof editor !== "string" || !editor) return { ok: false, reason: "validation_error" };
    if (typeof doc !== "string" || !doc) return { ok: false, reason: "validation_error" };
    if (typeof run_first !== "string" || !run_first ||
        typeof run_last !== "string" || !run_last)
      return { ok: false, reason: "validation_error" };
    const existing = this._one("SELECT * FROM revert_requests WHERE id=?", id);
    if (existing) return { ok: true, replay: true, request: existing };
    const status = approved ? REVERT_STATUS.APPROVED : REVERT_STATUS.REQUESTED;
    this.sql.exec(
      "INSERT INTO revert_requests (id, editor, doc, run_first, run_last, status, note, created_at, updated_at) " +
        "VALUES (?,?,?,?,?,?,?,?,?)",
      id, editor, doc, run_first, run_last, status, note || null, now, now
    );
    return { ok: true, request: this._one("SELECT * FROM revert_requests WHERE id=?", id) };
  }

  // List revert requests, optionally filtered by status (the daemon reads
  // status='approved'; the review page/digest read all active).
  listRevertRequests(status = null) {
    if (status) {
      return this._all(
        "SELECT * FROM revert_requests WHERE status=? ORDER BY created_at ASC", status);
    }
    return this._all("SELECT * FROM revert_requests ORDER BY created_at DESC");
  }

  // Resolve a request to a terminal (or approved) state. Used by the admin approve
  // path and by the daemon (done/failed). Never moves a terminal row.
  resolveRevertRequest(id, status, note = null) {
    const valid = new Set(Object.values(REVERT_STATUS));
    if (!valid.has(status)) return { ok: false, reason: "validation_error" };
    const row = this._one("SELECT status FROM revert_requests WHERE id=?", id);
    if (!row) return { ok: false, reason: "not_found" };
    if (REVERT_TERMINAL.has(row.status)) return { ok: false, reason: "already_terminal" };
    const now = this.now();
    if (note != null) {
      this.sql.exec("UPDATE revert_requests SET status=?, note=?, updated_at=? WHERE id=?",
        status, note, now, id);
    } else {
      this.sql.exec("UPDATE revert_requests SET status=?, updated_at=? WHERE id=?",
        status, now, id);
    }
    return { ok: true, id, status };
  }

  // ---- scoped-change requests (U7) -----------------------------------------
  // Lifecycle: requested -> drafting -> drafted -> (drafting again for the
  // remainder phase) -> done | failed | declined. claim() is the sole
  // requested->drafting writer; resolve() owns every other move and refuses
  // illegal jumps and terminal rewrites.
  fileScopedRequest({ id, editor, level, matter, part, module, instruction,
                      radius_blocks, radius_files, radius_matters,
                      confirmed } = {}) {
    const now = this.now();
    if (typeof id !== "string" || !id) return { ok: false, reason: "validation_error" };
    if (typeof editor !== "string" || !editor) return { ok: false, reason: "validation_error" };
    if (typeof level !== "string" || !level) return { ok: false, reason: "validation_error" };
    if (typeof instruction !== "string" || !instruction.trim())
      return { ok: false, reason: "validation_error" };
    if (!Number.isFinite(radius_blocks)) return { ok: false, reason: "validation_error" };
    const existing = this._one("SELECT * FROM scoped_requests WHERE id=?", id);
    if (existing) return { ok: true, replay: true, request: existing };
    const phase = (level === "module" || level === "course") ? "canary" : "all";
    this.sql.exec(
      "INSERT INTO scoped_requests (id, editor, level, matter, part, module, " +
        "instruction, radius_blocks, radius_files, radius_matters, confirmed, " +
        "status, phase, group_id, canary_matter, note, created_at, updated_at) " +
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,?)",
      id, editor, level, matter || null, part || null, module || null,
      instruction, radius_blocks, radius_files ?? null, radius_matters ?? null,
      confirmed ? 1 : 0, "requested", phase, now, now
    );
    return { ok: true, request: this._one("SELECT * FROM scoped_requests WHERE id=?", id) };
  }

  listScopedRequests(status = null) {
    if (status) {
      return this._all(
        "SELECT * FROM scoped_requests WHERE status=? ORDER BY created_at ASC", status);
    }
    return this._all("SELECT * FROM scoped_requests ORDER BY created_at DESC");
  }

  claimScopedRequest(id) {
    const row = this._one("SELECT status FROM scoped_requests WHERE id=?", id);
    if (!row || row.status !== "requested") return { ok: false, reason: "not_claimable" };
    this.sql.exec("UPDATE scoped_requests SET status='drafting', updated_at=? WHERE id=?",
      this.now(), id);
    return { ok: true, id };
  }

  resolveScopedRequest(id, { status, group_id, phase, canary_matter, note } = {}) {
    const ALLOWED = {
      drafting: new Set(["drafted", "failed"]),
      drafted: new Set(["drafting", "done", "failed", "declined"]),
    };
    const row = this._one("SELECT status FROM scoped_requests WHERE id=?", id);
    if (!row) return { ok: false, reason: "not_found" };
    const from = row.status;
    if (!ALLOWED[from] || !ALLOWED[from].has(status))
      return { ok: false, reason: "illegal_transition" };
    const sets = ["status=?", "updated_at=?"];
    const binds = [status, this.now()];
    if (group_id != null) { sets.push("group_id=?"); binds.push(group_id); }
    if (phase != null) { sets.push("phase=?"); binds.push(phase); }
    if (canary_matter != null) { sets.push("canary_matter=?"); binds.push(canary_matter); }
    if (note != null) { sets.push("note=?"); binds.push(note); }
    binds.push(id);
    this.sql.exec(`UPDATE scoped_requests SET ${sets.join(", ")} WHERE id=?`, ...binds);
    return { ok: true, id, status };
  }

  // Every member of a group by status — TERMINAL ones included (listAll hides
  // them, but the canary gate needs to see applied/declined rows).
  groupOutcome(group_id) {
    const rows = this._all(
      "SELECT status, COUNT(*) AS n FROM suggestions WHERE group_id=? GROUP BY status",
      group_id);
    const by_status = {};
    let total = 0;
    for (const r of rows) { by_status[r.status] = r.n; total += r.n; }
    return { group_id, total, by_status };
  }

  // ---- digest --------------------------------------------------------------
  // Admin-only summary: counts by status + per-source_ref pending tallies +
  // outstanding revert requests (so the review surface + digest ping surface them).
  digest() {
    const byStatus = {};
    for (const r of this._all("SELECT status, COUNT(*) AS n FROM suggestions GROUP BY status")) {
      byStatus[r.status] = r.n;
    }
    const bySource = this._all(
      "SELECT source_ref, COUNT(*) AS n FROM suggestions WHERE status=? GROUP BY source_ref ORDER BY n DESC",
      STATUS.PENDING
    );
    const revertByStatus = {};
    for (const r of this._all("SELECT status, COUNT(*) AS n FROM revert_requests GROUP BY status")) {
      revertByStatus[r.status] = r.n;
    }
    const revertOpen = this._all(
      "SELECT id, editor, doc, run_first, run_last, status, created_at FROM revert_requests " +
        "WHERE status IN (?,?) ORDER BY created_at ASC",
      REVERT_STATUS.REQUESTED, REVERT_STATUS.APPROVED
    );
    return {
      by_status: byStatus, pending_by_source: bySource,
      reverts_by_status: revertByStatus, reverts_open: revertOpen,
      generated_at: this.now(),
    };
  }

  // Retention purge: delete rows in a terminal status older than `days` days.
  purge(days = 30) {
    const cutoff = this.now() - days * 86400 * 1000;
    const term = TERMINAL.map((s) => `'${s}'`).join(",");
    const before = this._one("SELECT COUNT(*) AS n FROM suggestions").n;
    this.sql.exec(
      `DELETE FROM suggestions WHERE status IN (${term}) AND updated_at < ?`, cutoff
    );
    const after = this._one("SELECT COUNT(*) AS n FROM suggestions").n;
    return { ok: true, purged: before - after };
  }
}
