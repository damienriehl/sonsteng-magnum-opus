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

import { STATUS, TERMINAL, ALLOWED_TRANSITIONS, canTransition } from "./editor-status.js";

// Kind vocabularies (U4, KTD3). Structural operations are ordinary suggestion
// rows carried through the ONE pipeline — but they never take the DIRECT_APPLY
// fast path (the plan's execution note: a structural op is single-block, and
// the gate is risk, not scope), and they never supersede (two inserts after
// one anchor are two distinct intents, not a re-edit).
export const STRUCTURAL_KINDS = new Set([
  "insert_after", "delete", "split", "merge", "move",
]);

function classifyProductionScope(sources = []) {
  const structuralGroups = new Set(), structuralRefs = new Set();
  for (const source of sources) for (const operation of source.operations || []) {
    if (!STRUCTURAL_KINDS.has(operation.op)) continue;
    if (operation.group_id) structuralGroups.add(operation.group_id);
    for (const ref of [source.source_ref,operation.source_ref,operation.op_arg])
      if (typeof ref === "string" && ref) structuralRefs.add(ref);
  }
  return sources.map((source) => ({ ...source,operations:(source.operations || []).map((operation) => {
    const structural = STRUCTURAL_KINDS.has(operation.op);
    const dependent = !structural &&
      ((operation.group_id && structuralGroups.has(operation.group_id)) ||
       structuralRefs.has(operation.source_ref || source.source_ref));
    const production_hold_reason = structural ? "structural_prod_deferred" :
      dependent ? "depends_on_structural_prod_deferred" : null;
    return { ...operation,production_scope:production_hold_reason ? "held" : "prose",
      production_hold_reason };
  }) }));
}
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
  productionLeaseMs: 15 * 60 * 1000, // bounded provider-operation fence
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
    commit_sha TEXT,
    generator_id TEXT,
    phase TEXT NOT NULL,
    lease_expires_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS canonical_mutations (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL,
    kind TEXT NOT NULL, source_ref TEXT NOT NULL, original_text TEXT NOT NULL,
    new_text TEXT NOT NULL, original_hash TEXT NOT NULL, new_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );

  CREATE TABLE IF NOT EXISTS production_releases (
    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL, authorization_key TEXT UNIQUE,
    authorization_digest TEXT, state TEXT NOT NULL, actor TEXT NOT NULL,
    credential_channel TEXT NOT NULL, target_environment TEXT NOT NULL,
    target_batch_id TEXT NOT NULL, base_sha TEXT NOT NULL,
    candidate_sha TEXT NOT NULL, generator_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL, manifest_hash TEXT NOT NULL,
    membership_hash TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_release_batches (
    release_id TEXT NOT NULL, ordinal INTEGER NOT NULL, batch_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL, PRIMARY KEY (release_id, ordinal), UNIQUE (release_id, batch_id)
  );
  CREATE TABLE IF NOT EXISTS production_release_members (
    release_id TEXT NOT NULL, suggestion_id TEXT NOT NULL, batch_id TEXT NOT NULL,
    PRIMARY KEY (release_id, suggestion_id)
  );
  CREATE TABLE IF NOT EXISTS production_release_operation_members (
    release_id TEXT NOT NULL, operation_id TEXT NOT NULL, review_revision_id TEXT NOT NULL,
    source_ref TEXT NOT NULL, affected_source_refs_json TEXT NOT NULL DEFAULT '[]',
    group_id TEXT, ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (release_id, operation_id)
  );
  CREATE TABLE IF NOT EXISTS production_release_held_exclusions (
    release_id TEXT NOT NULL, operation_id TEXT NOT NULL, decision TEXT NOT NULL,
    reason TEXT NOT NULL, PRIMARY KEY (release_id, operation_id)
  );
  CREATE TABLE IF NOT EXISTS production_published_operations (
    operation_id TEXT PRIMARY KEY, release_id TEXT NOT NULL, review_revision_id TEXT NOT NULL,
    source_ref TEXT NOT NULL, source_revision TEXT NOT NULL, candidate_sha TEXT NOT NULL,
    published_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_published_operation_sources (
    operation_id TEXT NOT NULL, source_ref TEXT NOT NULL, release_id TEXT NOT NULL,
    candidate_sha TEXT NOT NULL, published_at INTEGER NOT NULL,
    PRIMARY KEY(operation_id, source_ref)
  );
  CREATE TABLE IF NOT EXISTS production_release_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, release_id TEXT NOT NULL, type TEXT NOT NULL,
    actor TEXT NOT NULL, detail_json TEXT NOT NULL, created_at INTEGER NOT NULL
  );

  -- Production review is deliberately separate from the terminal DEV suggestion
  -- lifecycle. Revision and receipt rows are immutable; only one actor-bound
  -- draft per revision may be replaced before submission.
  CREATE TABLE IF NOT EXISTS production_review_revisions (
    id TEXT PRIMARY KEY, source_ref TEXT NOT NULL, source_revision TEXT NOT NULL,
    prod_base TEXT NOT NULL, commit_sha TEXT NOT NULL, original_hash TEXT NOT NULL,
    proposed_hash TEXT NOT NULL, original_text TEXT NOT NULL, proposed_text TEXT NOT NULL,
    source_original_text TEXT, source_proposed_text TEXT,
    suggestion_ids_json TEXT NOT NULL, operations_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL, created_at INTEGER NOT NULL,
    UNIQUE(source_ref, source_revision, prod_base)
  );
  CREATE INDEX IF NOT EXISTS idx_review_revision_source
    ON production_review_revisions(source_ref, created_at, id);
  CREATE TABLE IF NOT EXISTS production_review_drafts (
    review_revision_id TEXT PRIMARY KEY, actor TEXT NOT NULL,
    source_revision TEXT NOT NULL, prod_base TEXT NOT NULL,
    decisions_json TEXT NOT NULL, payload_digest TEXT NOT NULL, updated_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_reviews (
    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL, actor TEXT NOT NULL, review_revision_id TEXT NOT NULL UNIQUE,
    source_revision TEXT NOT NULL, prod_base TEXT NOT NULL,
    receipt_hash TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_review_decisions (
    review_id TEXT NOT NULL, operation_id TEXT NOT NULL, decision TEXT NOT NULL,
    note TEXT NOT NULL, operation_digest TEXT NOT NULL, group_id TEXT,
    PRIMARY KEY(review_id, operation_id)
  );
  CREATE TABLE IF NOT EXISTS production_review_submissions (
    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL, actor TEXT NOT NULL,
    receipt_hash TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_review_submission_sources (
    review_id TEXT NOT NULL, review_revision_id TEXT NOT NULL UNIQUE,
    source_revision TEXT NOT NULL, prod_base TEXT NOT NULL, evidence_digest TEXT NOT NULL,
    PRIMARY KEY(review_id, review_revision_id)
  );
  CREATE TABLE IF NOT EXISTS production_review_submission_decisions (
    review_id TEXT NOT NULL, review_revision_id TEXT NOT NULL,
    operation_id TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL,
    operation_digest TEXT NOT NULL, group_id TEXT,
    PRIMARY KEY(review_id, review_revision_id, operation_id)
  );
  CREATE TABLE IF NOT EXISTS production_review_operations (
    operation_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
    review_id TEXT NOT NULL, review_revision_id TEXT NOT NULL,
    source_ref TEXT NOT NULL, group_id TEXT, decision TEXT NOT NULL,
    note TEXT NOT NULL, lifecycle_state TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_review_operation_frontier
    ON production_review_operations(lifecycle_state, decision);
  CREATE INDEX IF NOT EXISTS idx_review_operation_group
    ON production_review_operations(group_id, lifecycle_state);
  CREATE INDEX IF NOT EXISTS idx_review_operation_source
    ON production_review_operations(source_ref, lifecycle_state);
  CREATE INDEX IF NOT EXISTS idx_review_operation_revision
    ON production_review_operations(review_revision_id, lifecycle_state);
  CREATE INDEX IF NOT EXISTS idx_published_operation_source
    ON production_published_operations(source_ref, published_at DESC, operation_id DESC);
  CREATE INDEX IF NOT EXISTS idx_published_operation_all_sources
    ON production_published_operation_sources(source_ref, published_at DESC, operation_id DESC);
  CREATE TABLE IF NOT EXISTS editor_schema_migrations (
    id TEXT PRIMARY KEY, applied_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_review_migrations (
    id TEXT PRIMARY KEY, prod_base TEXT NOT NULL, evidence_digest TEXT NOT NULL,
    revision_count INTEGER NOT NULL, actor TEXT NOT NULL, created_at INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS production_legacy_exclusions (
    suggestion_id TEXT PRIMARY KEY,
    migration_id TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    apply_batch_id TEXT NOT NULL,
    apply_commit TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
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

  -- Reconstructable formative assessment evidence (U12). Payloads are
  -- canonicalized and credential-sanitized before they cross this SQL seam.
  -- Retention is mandatory per record; expiry deletes both the record and its
  -- append-only human override history.
  CREATE TABLE IF NOT EXISTS assessment_audit_records (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    assessment_use TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    config_provenance_json TEXT NOT NULL,
    instrument_provenance_json TEXT NOT NULL,
    provider_provenance_json TEXT NOT NULL,
    summative_blockers_json TEXT NOT NULL,
    record_digest TEXT NOT NULL,
    retention_days INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_assessment_audit_expiry
    ON assessment_audit_records(expires_at, id);
  CREATE TABLE IF NOT EXISTS assessment_audit_overrides (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    author TEXT NOT NULL,
    override_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_assessment_override_record
    ON assessment_audit_overrides(assessment_id, created_at, id);
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

const ASSESSMENT_REVIEW_SCOPE = "assessment-review";
const ASSESSMENT_AUDIT_SCHEMA_VERSION = "assessment-audit/v1";
const ASSESSMENT_OVERRIDE_SCHEMA_VERSION = "assessment-override/v1";
const ASSESSMENT_RETENTION_MAX_DAYS = 365;
const DAY_MS = 24 * 60 * 60 * 1000;

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function normalizedField(key) {
  return String(key).toLowerCase().replace(/[^a-z0-9]/g, "");
}

function credentialField(key) {
  const normalized = normalizedField(key);
  return normalized === "authorization" || normalized === "proxyauthorization" ||
    normalized === "auth" || normalized === "authheader" ||
    normalized.endsWith("authorization") || normalized === "bearer" ||
    normalized === "byok" || normalized === "credentialvalues" ||
    normalized === "apikey" || normalized.endsWith("apikey") ||
    normalized === "key" || normalized.endsWith("accesskey") ||
    normalized === "token" || normalized.endsWith("token") ||
    normalized === "credential" || normalized === "credentials" ||
    normalized.startsWith("credential") || normalized === "secret" ||
    normalized.endsWith("secret") || normalized === "password" ||
    normalized.endsWith("password") || normalized === "cookie" ||
    normalized === "setcookie" || normalized.endsWith("sessioncookie");
}

function collectCredentialStrings(value, secrets, force = false, seen = new WeakSet()) {
  if (typeof value === "string") {
    if (force && value) {
      secrets.add(value);
      const scheme = value.match(/^\s*[A-Za-z][A-Za-z0-9_-]*\s+(.+?)\s*$/);
      if (scheme && scheme[1]) secrets.add(scheme[1]);
    }
    return;
  }
  if (!value || typeof value !== "object" || seen.has(value)) return;
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) collectCredentialStrings(item, secrets, force, seen);
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    // A `byok` object contains useful non-secret provider/model strings. Only
    // its credential-shaped descendants are collected as live secret values.
    const sensitive = credentialField(key);
    collectCredentialStrings(child, secrets,
      force || (sensitive && normalizedField(key) !== "byok"), seen);
  }
}

function sanitizedPayload(value, secrets, seen = new WeakSet()) {
  if (typeof value === "string") {
    let safe = value;
    for (const secret of [...secrets].sort((a, b) => b.length - a.length)) {
      if (secret.length >= 4) safe = safe.replaceAll(secret, "[REDACTED]");
    }
    return safe;
  }
  if (value === null || typeof value === "boolean" || typeof value === "number") return value;
  if (Array.isArray(value)) return value.map((item) => sanitizedPayload(item, secrets, seen));
  if (!isRecord(value) || seen.has(value)) return null;
  seen.add(value);
  const safe = {};
  for (const [key, child] of Object.entries(value)) {
    if (credentialField(key) || child === undefined) continue;
    safe[key] = sanitizedPayload(child, secrets, seen);
  }
  return safe;
}

export class EditorStoreCore {
  constructor(sql, now = () => Date.now(), transactionSync = null) {
    this.sql = sql;
    this.now = now;
    this.transactionSync = transactionSync || ((callback) => {
      this.sql.exec("BEGIN IMMEDIATE");
      try { const result = callback(); this.sql.exec("COMMIT"); return result; }
      catch (error) { this.sql.exec("ROLLBACK"); throw error; }
    });
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
    this._ensureColumn("apply_batches", "commit_sha", "TEXT");
    this._ensureColumn("apply_batches", "generator_id", "TEXT");
    this._ensureColumn("production_releases", "authorization_key", "TEXT");
    this._ensureColumn("production_releases", "authorization_digest", "TEXT");
    this._ensureColumn("production_releases", "fencing_token", "TEXT");
    this._ensureColumn("production_releases", "lease_expires_at", "INTEGER");
    this._ensureColumn("production_releases", "provider_json", "TEXT");
    this._ensureColumn("production_releases", "schema_version", "INTEGER");
    this._ensureColumn("production_releases", "review_receipt_hash", "TEXT");
    this._ensureColumn("production_releases", "projection_identity", "TEXT");
    this._ensureColumn("production_release_operation_members", "ordinal", "INTEGER NOT NULL DEFAULT 0");
    this._ensureColumn("production_release_operation_members", "affected_source_refs_json", "TEXT NOT NULL DEFAULT '[]'");
    this._ensureColumn("suggestions", "production_release_id", "TEXT");
    this._ensureColumn("suggestions", "production_published_at", "INTEGER");
    this._ensureColumn("production_review_revisions", "source_original_text", "TEXT");
    this._ensureColumn("production_review_revisions", "source_proposed_text", "TEXT");
    this._backfillReviewOperations();
    this.sql.exec(`INSERT OR IGNORE INTO production_published_operation_sources
      (operation_id,source_ref,release_id,candidate_sha,published_at)
      SELECT operation_id,source_ref,release_id,candidate_sha,published_at
      FROM production_published_operations`);
  }

  _backfillReviewOperations() {
    const migrationId = "production-review-operations-v1";
    if (this._one("SELECT id FROM editor_schema_migrations WHERE id=?",migrationId)) return;
    this.transactionSync(() => {
      this.sql.exec(`INSERT OR IGNORE INTO production_review_operations
      (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state)
      SELECT json_extract(operation.value,'$.id'),
        COALESCE(json_extract(operation.value,'$.decision_id'),json_extract(operation.value,'$.id')),
        submission.id,revision.id,revision.source_ref,
        COALESCE(json_extract(operation.value,'$.group_id'),json_extract(operation.value,'$.move_pair_id')),
        COALESCE(decision.decision,'unanswered'),COALESCE(decision.note,''),
        CASE WHEN published.operation_id IS NOT NULL THEN 'published'
          WHEN EXISTS (SELECT 1 FROM production_review_submission_sources newer_source
            JOIN production_review_submissions newer ON newer.id=newer_source.review_id
            WHERE newer_source.review_revision_id IN
              (SELECT id FROM production_review_revisions WHERE source_ref=revision.source_ref)
              AND (newer.created_at>submission.created_at OR
                (newer.created_at=submission.created_at AND newer.id>submission.id)))
          THEN 'superseded' ELSE 'unpublished' END
      FROM production_review_submissions submission
      JOIN production_review_submission_sources source ON source.review_id=submission.id
      JOIN production_review_revisions revision ON revision.id=source.review_revision_id
      JOIN json_each(revision.operations_json) operation
      LEFT JOIN production_review_submission_decisions decision
        ON decision.review_id=submission.id AND decision.review_revision_id=revision.id
        AND decision.operation_id=COALESCE(json_extract(operation.value,'$.decision_id'),json_extract(operation.value,'$.id'))
      LEFT JOIN production_published_operations published
        ON published.operation_id=json_extract(operation.value,'$.id')`);
      this.sql.exec(`INSERT OR IGNORE INTO production_review_operations
      (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state)
      SELECT json_extract(operation.value,'$.id'),
        COALESCE(json_extract(operation.value,'$.decision_id'),json_extract(operation.value,'$.id')),
        review.id,revision.id,revision.source_ref,
        COALESCE(json_extract(operation.value,'$.group_id'),json_extract(operation.value,'$.move_pair_id')),
        COALESCE(decision.decision,'unanswered'),COALESCE(decision.note,''),
        CASE WHEN published.operation_id IS NOT NULL THEN 'published'
          WHEN EXISTS (SELECT 1 FROM production_review_submission_sources newer_source
            JOIN production_review_submissions newer ON newer.id=newer_source.review_id
            JOIN production_review_revisions newer_revision ON newer_revision.id=newer_source.review_revision_id
            WHERE newer_revision.source_ref=revision.source_ref
              AND (newer.created_at>review.created_at OR
                (newer.created_at=review.created_at AND newer.id>review.id)))
            OR EXISTS (SELECT 1 FROM production_reviews newer
              JOIN production_review_revisions newer_revision ON newer_revision.id=newer.review_revision_id
              WHERE newer_revision.source_ref=revision.source_ref
                AND (newer.created_at>review.created_at OR
                  (newer.created_at=review.created_at AND newer.id>review.id)))
          THEN 'superseded' ELSE 'unpublished' END
      FROM production_reviews review
      JOIN production_review_revisions revision ON revision.id=review.review_revision_id
      JOIN json_each(revision.operations_json) operation
      LEFT JOIN production_review_decisions decision ON decision.review_id=review.id
        AND decision.operation_id=COALESCE(json_extract(operation.value,'$.decision_id'),json_extract(operation.value,'$.id'))
      LEFT JOIN production_published_operations published
        ON published.operation_id=json_extract(operation.value,'$.id')`);
      this.sql.exec("INSERT INTO editor_schema_migrations (id,applied_at) VALUES (?,?)",
        migrationId,this.now());
    });
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

  _canonical(value) {
    if (Array.isArray(value)) return `[${value.map((item) => this._canonical(item)).join(",")}]`;
    if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${this._canonical(value[key])}`).join(",")}}`;
    return JSON.stringify(value);
  }

  _digest(value) { return this._fingerprint("review-v1", this._canonical(value), null); }

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
    // The production frontier is Worker-owned release evidence. Never let an
    // apply client infer it from ambient DEV HEAD/base_sha. A null frontier is
    // an explicit bootstrap state: finalize may apply DEV, but must omit review
    // revisions until the trusted migration establishes production ancestry.
    const production = this._one(
      "SELECT candidate_sha FROM production_releases WHERE state='complete' ORDER BY updated_at DESC,id DESC LIMIT 1"
    );
    return { ok: true, batch_id: batchId, claimed: [...claimIds], lease_expires_at: leaseExp,
      prod_base: production?.candidate_sha || null };
  }

  // Journal a phase transition, and (on outcome) resolve the batch's suggestions.
  // outcome for whole batch: undefined = just record phase; or per-status maps.
  finalize(batchId, { phase, applied, accepted_blocked, needs_human, drift, base_sha,
    commit_sha, generator_id, review_revisions } = {}) {
    try { return this.transactionSync(() => {
    const now = this.now();
    const b = this._one("SELECT batch_id FROM apply_batches WHERE batch_id=?", batchId);
    if (!b) return { ok: false, reason: "no_batch" };

    if (phase) {
      const sets = ["phase=?", "updated_at=?"];
      const binds = [phase, now];
      if (base_sha != null) { sets.push("base_sha=?"); binds.push(base_sha); }
      if (commit_sha != null) { sets.push("commit_sha=?"); binds.push(commit_sha); }
      if (generator_id != null) { sets.push("generator_id=?"); binds.push(generator_id); }
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
    // New apply clients may attach already-derived U2 operation evidence. Older
    // clients remain valid, but create no review revision and therefore remain
    // fail-closed/unreviewable until the explicit migration backfills evidence.
    if (phase === "done" && Array.isArray(review_revisions)) {
      for (const revision of review_revisions) {
        if (revision.commit_sha !== commit_sha) throw { reviewFailure:{ ok:false,reason:"revision_mismatch" } };
        const recorded = this.recordReviewRevision(revision);
        if (!recorded.ok) throw { reviewFailure:recorded };
      }
    }
    return { ok: true };
    }); } catch (error) {
      if (error?.reviewFailure) return error.reviewFailure;
      throw error;
    }
  }

  recordCanonicalMutation(input = {}) {
    const required = ["id","batch_id","actor","kind","source_ref","original_text",
      "new_text","original_hash","new_hash","base_sha","commit_sha","generator_id"];
    if (required.some((key) => typeof input[key] !== "string") ||
        required.filter((key) => !["original_text","new_text"].includes(key)).some((key) => !input[key]))
      return { ok:false, reason:"validation_error" };
    if (input.kind !== "history_revert" || input.original_text === input.new_text ||
        new TextEncoder().encode(input.original_text).byteLength > 131072 ||
        new TextEncoder().encode(input.new_text).byteLength > 131072 ||
        !/^data\//.test(input.source_ref) || /(^|\/)(?:twin-secrets|\.secrets)(?:\/|$)|\.(?:pem|key)$/i.test(input.source_ref))
      return { ok:false, reason:"validation_error" };
    const request = this._one("SELECT editor,status FROM revert_requests WHERE id=?", input.id);
    if (!request || request.status !== REVERT_STATUS.APPROVED || request.editor !== input.actor)
      return { ok:false, reason:"revert_not_approved" };
    const existing = this._one("SELECT * FROM canonical_mutations WHERE id=?", input.id);
    if (existing) {
      const batch = this._one("SELECT base_sha,commit_sha,generator_id,phase FROM apply_batches WHERE batch_id=?",
        existing.batch_id);
      return existing.batch_id === input.batch_id && existing.actor === input.actor &&
        existing.source_ref === input.source_ref && existing.original_text === input.original_text &&
        existing.new_text === input.new_text && existing.original_hash === input.original_hash &&
        existing.new_hash === input.new_hash && batch?.commit_sha === input.commit_sha &&
        batch?.generator_id === input.generator_id && batch?.base_sha === input.base_sha ?
        { ok:true,replay:true,phase:batch.phase } :
        { ok:false,reason:"idempotency_conflict" };
    }
    if (this._one("SELECT batch_id FROM apply_batches WHERE batch_id=?", input.batch_id))
      return { ok:false,reason:"batch_exists" };
    const now = this.now();
    this.transactionSync(() => {
      this.sql.exec("INSERT INTO apply_batches (batch_id,base_sha,commit_sha,generator_id,phase,lease_expires_at,created_at,updated_at) VALUES (?,?,?,?, 'merged',NULL,?,?)",
        input.batch_id,input.base_sha,input.commit_sha,input.generator_id,now,now);
      this.sql.exec("INSERT INTO canonical_mutations (id,batch_id,actor,kind,source_ref,original_text,new_text,original_hash,new_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        input.id,input.batch_id,input.actor,input.kind,input.source_ref,input.original_text,input.new_text,
        input.original_hash,input.new_hash,now);
    });
    return { ok:true,batch_id:input.batch_id,phase:"merged" };
  }

  completeCanonicalMutation(input = {}) {
    const mutation = this._one("SELECT * FROM canonical_mutations WHERE id=?", input.id || "");
    if (!mutation) return { ok:false,reason:"not_found" };
    const batch = this._one("SELECT * FROM apply_batches WHERE batch_id=?", mutation.batch_id);
    for (const key of ["batch_id","actor","source_ref","original_text","new_text","original_hash","new_hash"])
      if (input[key] !== (key === "batch_id" ? mutation.batch_id : mutation[key]))
        return { ok:false,reason:"idempotency_conflict" };
    for (const key of ["base_sha","commit_sha","generator_id"])
      if (input[key] !== batch[key]) return { ok:false,reason:"idempotency_conflict" };
    const production = this._one(
      "SELECT id FROM production_releases WHERE state='complete' ORDER BY updated_at DESC,id DESC LIMIT 1");
    if (production && !input.review_revision)
      return { ok:false,reason:"missing_revision_evidence" };
    if (batch.phase === "done") {
      if (input.review_revision) {
        const recorded = this.recordReviewRevision(input.review_revision);
        if (!recorded.ok) return recorded;
      }
      return { ok:true,replay:true,phase:"done" };
    }
    if (batch.phase !== "merged") return { ok:false,reason:"invalid_phase" };
    if (input.review_revision && input.review_revision.commit_sha !== batch.commit_sha)
      return { ok:false,reason:"revision_mismatch" };
    const now = this.now();
    try { this.transactionSync(() => {
      this.sql.exec("UPDATE apply_batches SET phase='done',updated_at=? WHERE batch_id=?",
        now,batch.batch_id);
      this.sql.exec("UPDATE revert_requests SET status=?,updated_at=? WHERE id=? AND status=?",
        REVERT_STATUS.DONE,now,input.id,REVERT_STATUS.APPROVED);
      if (input.review_revision) {
        const recorded = this.recordReviewRevision(input.review_revision);
        if (!recorded.ok) throw { reviewFailure:recorded };
      }
    }); } catch (error) {
      if (error?.reviewFailure) return error.reviewFailure;
      throw error;
    }
    return { ok:true,batch_id:batch.batch_id,phase:"done" };
  }

  prepareProductionRelease(input = {}) {
    const required = ["id", "idempotency_key", "request_digest", "actor", "base_sha",
      "candidate_sha", "generator_id", "evidence_hash", "manifest_hash", "target_batch_id"];
    if (required.some((k) => typeof input[k] !== "string" || !input[k]))
      return { ok:false, reason:"validation_error" };
    if (input.target_environment !== "production") return { ok:false, reason:"wrong_target" };
    if (input.credential_channel !== "bearer") return { ok:false, reason:"service_bearer_required" };
    if (input.ancestry_verified !== true) return { ok:false, reason:"nonancestor_candidate" };
    if (input.schema_version === 2) return this._prepareOperationRelease(input);
    if (this._one("SELECT batch_id FROM apply_batches WHERE phase='evidence_missing' LIMIT 1"))
      return { ok:false, reason:"missing_batch_evidence" };

    const priorKey = this._one(
      "SELECT id,request_digest FROM production_releases WHERE idempotency_key=?", input.idempotency_key);
    if (priorKey) {
      if (priorKey.id === input.id && priorKey.request_digest === input.request_digest)
        return { ok:true, replay:true, release:this.getProductionRelease(priorKey.id) };
      return { ok:false, reason:"idempotency_conflict" };
    }
    if (this._one("SELECT id FROM production_releases WHERE id=?", input.id))
      return { ok:false, reason:"release_exists" };

    if (this._one("SELECT id FROM production_releases WHERE state NOT IN ('complete','restored') LIMIT 1"))
      return { ok:false, reason:"active_release" };

    const frontier = this._one(
      "SELECT target_batch_id,candidate_sha FROM production_releases WHERE state='complete' ORDER BY updated_at DESC,id DESC LIMIT 1");
    const allDone = this._all(
      "SELECT batch_id,commit_sha,generator_id,created_at FROM apply_batches WHERE phase='done' ORDER BY created_at,batch_id");
    const done = allDone.filter((batch) => this._one(
      "SELECT id FROM suggestions WHERE apply_batch_id=? AND status=? LIMIT 1",
      batch.batch_id, STATUS.APPLIED) || this._one(
      "SELECT id FROM canonical_mutations WHERE batch_id=? LIMIT 1", batch.batch_id));
    let start = 0;
    if (frontier) {
      if (frontier.candidate_sha !== input.base_sha) return { ok:false, reason:"stale_base" };
      const at = done.findIndex((b) => b.batch_id === frontier.target_batch_id);
      if (at < 0) return { ok:false, reason:"stale_frontier" };
      start = at + 1;
    }
    const end = done.findIndex((b, i) => i >= start && b.batch_id === input.target_batch_id);
    if (end < start) return { ok:false, reason:"stale_target" };
    const batches = done.slice(start, end + 1);
    if (!batches.length) return { ok:false, reason:"empty_membership" };
    if (batches.some((b) => !b.commit_sha || b.generator_id !== input.generator_id))
      return { ok:false, reason:"stale_batch_evidence" };
    if (batches[batches.length - 1].commit_sha !== input.candidate_sha)
      return { ok:false, reason:"stale_candidate" };

    const members = [];
    const groups = new Map();
    for (const batch of batches) {
      const rows = this._all(
        "SELECT id,group_id,status FROM suggestions WHERE apply_batch_id=? AND status=? ORDER BY id",
        batch.batch_id, STATUS.APPLIED);
      const mutations = this._all(
        "SELECT id FROM canonical_mutations WHERE batch_id=? ORDER BY id", batch.batch_id);
      if (!rows.length && !mutations.length) return { ok:false, reason:"stale_member" };
      for (const row of rows) {
        if (row.group_id) {
          if (!groups.has(row.group_id)) groups.set(row.group_id, this._all(
            "SELECT apply_batch_id,status FROM suggestions WHERE group_id=?", row.group_id));
          const group = groups.get(row.group_id);
          if (group.some((g) => g.apply_batch_id !== batch.batch_id || g.status !== STATUS.APPLIED))
            return { ok:false, reason:"partial_group" };
        }
        members.push({ suggestion_id:row.id, batch_id:batch.batch_id });
      }
      for (const mutation of mutations)
        members.push({ suggestion_id:mutation.id, batch_id:batch.batch_id });
    }
    const batchIds = batches.map((b) => b.batch_id);
    const memberIds = members.map((m) => m.suggestion_id);
    if (input.expected_batch_ids && JSON.stringify(input.expected_batch_ids) !== JSON.stringify(batchIds))
      return { ok:false, reason:"membership_mismatch" };
    if (input.expected_suggestion_ids &&
        JSON.stringify([...input.expected_suggestion_ids].sort()) !== JSON.stringify([...memberIds].sort()))
      return { ok:false, reason:"membership_mismatch" };
    const membershipHash = this._fingerprint(JSON.stringify(batchIds), JSON.stringify(memberIds),
      [input.base_sha,input.candidate_sha,input.generator_id,input.evidence_hash,input.manifest_hash].join("\0"));
    const now = this.now();
    this.transactionSync(() => {
    this.sql.exec("INSERT INTO production_releases (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,membership_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      input.id,input.idempotency_key,input.request_digest,"prepared",input.actor,input.credential_channel,
      "production",input.target_batch_id,input.base_sha,input.candidate_sha,input.generator_id,
      input.evidence_hash,input.manifest_hash,membershipHash,now,now);
    batches.forEach((b, ordinal) => this.sql.exec(
      "INSERT INTO production_release_batches (release_id,ordinal,batch_id,commit_sha) VALUES (?,?,?,?)",
      input.id,ordinal,b.batch_id,b.commit_sha));
    members.forEach((m) => this.sql.exec(
      "INSERT INTO production_release_members (release_id,suggestion_id,batch_id) VALUES (?,?,?)",
      input.id,m.suggestion_id,m.batch_id));
    this.sql.exec("INSERT INTO production_release_events (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)",
      input.id,"prepared",input.actor,JSON.stringify({ membership_hash:membershipHash,
        evidence_hash:input.evidence_hash, manifest_hash:input.manifest_hash,
        target_environment:"production" }),now);
    });
    return { ok:true, release:this.getProductionRelease(input.id) };
  }

  _prepareOperationRelease(input) {
    for (const key of ["review_receipt_hash","projection_identity"])
      if (typeof input[key] !== "string" || !input[key]) return { ok:false,reason:"validation_error" };
    if (!Array.isArray(input.accepted_operation_ids) || !input.accepted_operation_ids.length)
      return { ok:false,reason:"empty_membership" };
    const priorKey = this._one(
      "SELECT id,request_digest FROM production_releases WHERE idempotency_key=?",input.idempotency_key);
    if (priorKey) return priorKey.id === input.id && priorKey.request_digest === input.request_digest ?
      { ok:true,replay:true,release:this.getProductionRelease(priorKey.id) } :
      { ok:false,reason:"idempotency_conflict" };
    if (this._one("SELECT id FROM production_releases WHERE id=?",input.id))
      return { ok:false,reason:"release_exists" };
    if (this._one("SELECT id FROM production_releases WHERE state NOT IN ('complete','restored') LIMIT 1"))
      return { ok:false,reason:"active_release" };

    const context = this.productionPreparationContext();
    const projection = context.projection || {};
    if (projection.blocked_reason) return { ok:false,reason:projection.blocked_reason };
    const receiptHashes = (projection.review_receipts || []).map((item) => item.receipt_hash).sort();
    if (Array.isArray(input.review_receipts) &&
        JSON.stringify([...input.review_receipts].sort()) !== JSON.stringify(receiptHashes))
      return { ok:false,reason:"review_receipt_mismatch" };
    if (receiptHashes.length === 1 && input.review_receipt_hash !== receiptHashes[0] &&
        !Array.isArray(input.review_receipts))
      return { ok:false,reason:"review_receipt_mismatch" };

    const heldGroups = this._heldReviewGroups(projection.sources);
    const members = [],held = [];
    for (const source of projection.sources || []) {
      const decisions = new Map((source.decisions || []).map((item) => [item.operation_id,item]));
      for (const operation of source.operations || []) {
        const decision = decisions.get(operation.decision_id || operation.id);
        const groupId = operation.group_id || operation.move_pair_id;
        if (operation.production_scope !== "held" && !source.stale &&
            !heldGroups.has(groupId) && decision?.decision === "accepted") {
          const affectedSourceRefs = [...new Set([source.source_ref,operation.op_arg]
            .filter((value) => typeof value === "string" && value))].sort();
          members.push({ operation_id:operation.id,review_revision_id:source.review_revision_id,
            source_ref:source.source_ref,source_revision:source.source_revision,
            affected_source_refs:affectedSourceRefs,
            group_id:decision.group_id || operation.group_id || null });
        } else {
          const reason = operation.production_hold_reason || (source.stale ? "stale" :
            heldGroups.has(groupId) ? "group_held" : decision?.decision || "unanswered");
          held.push({ operation_id:operation.id,decision:decision?.decision || "unanswered",reason });
        }
      }
    }
    held.sort((a,b) => a.operation_id.localeCompare(b.operation_id));
    const expected = [...input.accepted_operation_ids];
    if (JSON.stringify(expected) !== JSON.stringify(members.map((item) => item.operation_id)))
      return { ok:false,reason:"operation_membership_mismatch" };
    if (Array.isArray(input.held_exclusions)) {
      const supplied = input.held_exclusions.map((item) =>
        ({ operation_id:item.operation_id,decision:item.decision })).sort((a,b) =>
          a.operation_id.localeCompare(b.operation_id));
      const authoritative = held.map(({operation_id,decision}) => ({operation_id,decision}));
      if (JSON.stringify(supplied) !== JSON.stringify(authoritative))
        return { ok:false,reason:"held_exclusion_mismatch" };
    }
    const membershipHash = this._fingerprint(JSON.stringify(members),JSON.stringify(held),
      [input.base_sha,input.candidate_sha,input.generator_id,input.evidence_hash,input.manifest_hash,
        input.review_receipt_hash,input.projection_identity].join("\0"));
    const now = this.now();
    this.transactionSync(() => {
      this.sql.exec("INSERT INTO production_releases (id,idempotency_key,request_digest,state,actor,credential_channel,target_environment,target_batch_id,base_sha,candidate_sha,generator_id,evidence_hash,manifest_hash,membership_hash,created_at,updated_at,schema_version,review_receipt_hash,projection_identity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        input.id,input.idempotency_key,input.request_digest,"prepared",input.actor,input.credential_channel,
        "production",input.target_batch_id,input.base_sha,input.candidate_sha,input.generator_id,
        input.evidence_hash,input.manifest_hash,membershipHash,now,now,2,input.review_receipt_hash,
        input.projection_identity);
      members.forEach((item,ordinal) => this.sql.exec(
        "INSERT INTO production_release_operation_members (release_id,operation_id,review_revision_id,source_ref,affected_source_refs_json,group_id,ordinal) VALUES (?,?,?,?,?,?,?)",
        input.id,item.operation_id,item.review_revision_id,item.source_ref,
        JSON.stringify(item.affected_source_refs),item.group_id,ordinal));
      for (const item of held) this.sql.exec(
        "INSERT INTO production_release_held_exclusions (release_id,operation_id,decision,reason) VALUES (?,?,?,?)",
        input.id,item.operation_id,item.decision,item.reason);
      this.sql.exec("INSERT INTO production_release_events (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)",
        input.id,"prepared",input.actor,JSON.stringify({ membership_hash:membershipHash,
          review_receipt_hash:input.review_receipt_hash,projection_identity:input.projection_identity,
          evidence_hash:input.evidence_hash,manifest_hash:input.manifest_hash,target_environment:"production" }),now);
    });
    return { ok:true,release:this.getProductionRelease(input.id) };
  }

  authorizeProductionRelease(input = {}) {
    if (!input.id || !input.idempotency_key || !input.request_digest || !input.actor)
      return { ok:false, reason:"validation_error" };
    if (input.credential_channel !== "access") return { ok:false, reason:"human_access_required" };
    const release = this._one("SELECT * FROM production_releases WHERE id=?", input.id);
    if (!release) return { ok:false, reason:"not_found" };
    if (release.authorization_key) {
      if (release.authorization_key === input.idempotency_key &&
          release.authorization_digest === input.request_digest)
        return { ok:true, replay:true, release:this.getProductionRelease(input.id) };
      return { ok:false, reason:"idempotency_conflict" };
    }
    if (release.state !== "prepared") return { ok:false, reason:"stale_draft" };
    if ((release.schema_version || 1) >= 2) {
      const revisions = this._all("SELECT DISTINCT review_revision_id FROM production_release_operation_members WHERE release_id=?",release.id);
      if (revisions.some((item) => {
        const revision = this._reviewRevision(item.review_revision_id);
        return !revision || this._reviewStaleReason(revision);
      })) return { ok:false,reason:"stale_review" };
    }
    for (const key of ["target_batch_id","base_sha","candidate_sha","generator_id","evidence_hash","manifest_hash","membership_hash","review_receipt_hash","projection_identity"]) {
      if (input[key] != null && input[key] !== release[key]) return { ok:false, reason:"stale_draft" };
    }
    const now = this.now();
    this.transactionSync(() => {
    this.sql.exec("UPDATE production_releases SET state='authorized',actor=?,credential_channel=?,authorization_key=?,authorization_digest=?,updated_at=? WHERE id=? AND state='prepared'",
      input.actor,input.credential_channel,input.idempotency_key,input.request_digest,now,input.id);
    this.sql.exec("INSERT INTO production_release_events (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)",
      input.id,"authorized",input.actor,JSON.stringify({ membership_hash:release.membership_hash,
        evidence_hash:release.evidence_hash,manifest_hash:release.manifest_hash,target_environment:"production" }),now);
    });
    return { ok:true, release:this.getProductionRelease(input.id) };
  }

  getProductionRelease(id) {
    const row = this._one("SELECT * FROM production_releases WHERE id=?", id);
    if (!row) return null;
    const batches = this._all(
      "SELECT ordinal,batch_id,commit_sha FROM production_release_batches WHERE release_id=? ORDER BY ordinal", id);
    const suggestion_ids = this._all(
      "SELECT suggestion_id FROM production_release_members WHERE release_id=? ORDER BY suggestion_id", id)
      .map((r) => r.suggestion_id);
    const operation_members = this._all(
      "SELECT operation_id,review_revision_id,source_ref,affected_source_refs_json,group_id,ordinal FROM production_release_operation_members WHERE release_id=? ORDER BY ordinal,operation_id",id)
      .map((item) => ({ ...item,
        affected_source_refs:JSON.parse(item.affected_source_refs_json || "[]") }));
    const held_exclusions = this._all(
      "SELECT operation_id,decision,reason FROM production_release_held_exclusions WHERE release_id=? ORDER BY operation_id",id);
    const published_operation_ids = this._all(
      "SELECT operation_id FROM production_published_operations WHERE release_id=? ORDER BY operation_id",id)
      .map((item) => item.operation_id);
    const events = this._all(
      "SELECT type,actor,detail_json,created_at FROM production_release_events WHERE release_id=? ORDER BY id", id)
      .map((event) => ({ type:event.type, actor:event.actor,
        detail:JSON.parse(event.detail_json), created_at:event.created_at }));
    return { ...row,schema_version:row.schema_version || 1,batches,suggestion_ids,
      operation_ids:operation_members.map((item) => item.operation_id),operation_members,
      held_exclusions,published_operation_ids,events };
  }

  // Production execution is a separate lifecycle. This claim can consume only
  // a release carrying a prior human authorization; it never scans accepted or
  // applied suggestions and therefore cannot enlarge frozen membership.
  claimAuthorizedProductionRelease(input = {}) {
    if (!input.actor || input.credential_channel !== "bearer")
      return { ok:false, reason:"service_bearer_required" };
    const now = this.now();
    let release = input.id ? this._one("SELECT * FROM production_releases WHERE id=?", input.id) :
      this._one("SELECT * FROM production_releases WHERE state='authorized' OR (state IN ('executing','pages_deployed','worker_deployed','verified') AND lease_expires_at<=?) ORDER BY updated_at,id LIMIT 1", now);
    if (!release) return { ok:true, release:null };
    if (['executing','pages_deployed','worker_deployed','verified'].includes(release.state) &&
        release.lease_expires_at > now)
      return { ok:false, reason:"lease_active" };
    if (!['authorized','executing','pages_deployed','worker_deployed','verified'].includes(release.state))
      return { ok:false, reason:"not_authorized" };
    if ((release.schema_version || 1) >= 2 && release.state === "authorized") {
      const revisions = this._all("SELECT DISTINCT review_revision_id FROM production_release_operation_members WHERE release_id=?",release.id);
      if (revisions.some((item) => {
        const revision = this._reviewRevision(item.review_revision_id);
        return !revision || this._reviewStaleReason(revision);
      })) return { ok:false,reason:"stale_review" };
    }
    const token = this._fingerprint(release.id, release.manifest_hash, `${now}:${input.actor}`);
    const lease = now + Math.max(1000, Math.min(
      input.lease_ms || CEILINGS.leaseMs, CEILINGS.productionLeaseMs));
    this.sql.exec("UPDATE production_releases SET state=?,fencing_token=?,lease_expires_at=?,updated_at=? WHERE id=?",
      release.state === "authorized" ? "executing" : release.state,token,lease,now,release.id);
    this.sql.exec("INSERT INTO production_release_events (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)",
      release.id,"executing",input.actor,JSON.stringify({ fencing_token:token,
        manifest_hash:release.manifest_hash }),now);
    return { ok:true, release:this.getProductionRelease(release.id) };
  }

  claimProductionRestore(input = {}) {
    if (!input.id || !input.actor || input.credential_channel !== "bearer")
      return { ok:false,reason:"service_bearer_required" };
    const release = this._one("SELECT * FROM production_releases WHERE id=?",input.id);
    if (!release) return { ok:false,reason:"not_found" };
    const now = this.now();
    if (release.state === "restoring" && release.lease_expires_at > now)
      return { ok:false,reason:"lease_active" };
    if (!['failed_fenced','restoring'].includes(release.state))
      return { ok:false,reason:"not_fenced" };
    const token = this._fingerprint(release.id,release.fencing_token || "",
      `${now}:${input.actor}:restore`);
    const lease = now + Math.max(1000,Math.min(
      input.lease_ms || CEILINGS.productionLeaseMs,CEILINGS.productionLeaseMs));
    this.transactionSync(() => {
      this.sql.exec("UPDATE production_releases SET state='restoring',fencing_token=?,lease_expires_at=?,updated_at=? WHERE id=?",
        token,lease,now,release.id);
      this.sql.exec("INSERT INTO production_release_events (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)",
        release.id,"restoring",input.actor,JSON.stringify({ restore_claim:true }),now);
    });
    return { ok:true,release:this.getProductionRelease(release.id) };
  }

  renewProductionReleaseLease(input = {}) {
    const release = this._one("SELECT * FROM production_releases WHERE id=?", input.id || "");
    if (!release) return { ok:false, reason:"not_found" };
    if (input.credential_channel !== "bearer" || !input.actor)
      return { ok:false, reason:"service_bearer_required" };
    if (!input.fencing_token || input.fencing_token !== release.fencing_token)
      return { ok:false, reason:"stale_fence" };
    if (!['executing','pages_deployed','worker_deployed','verified','restoring'].includes(release.state))
      return { ok:false, reason:"not_executing" };
    const now = this.now();
    if (!release.lease_expires_at || release.lease_expires_at <= now)
      return { ok:false, reason:"lease_expired" };
    const lease = now + Math.max(1000,
      Math.min(input.lease_ms || CEILINGS.productionLeaseMs, CEILINGS.productionLeaseMs));
    this.sql.exec("UPDATE production_releases SET lease_expires_at=?,updated_at=? WHERE id=? AND fencing_token=?",
      lease,now,release.id,release.fencing_token);
    return { ok:true, lease_expires_at:lease };
  }

  transitionProductionRelease(input = {}) {
    const release = this._one("SELECT * FROM production_releases WHERE id=?", input.id || "");
    if (!release) return { ok:false, reason:"not_found" };
    if (input.credential_channel !== "bearer" || !input.actor)
      return { ok:false, reason:"service_bearer_required" };
    if (!input.fencing_token || input.fencing_token !== release.fencing_token)
      return { ok:false, reason:"stale_fence" };
    if (['executing','pages_deployed','worker_deployed','verified','restoring'].includes(release.state) &&
        (!release.lease_expires_at || release.lease_expires_at <= this.now()))
      return { ok:false, reason:"lease_expired" };
    const replayable = new Set(["executing","pages_deployed","worker_deployed","verified","complete"]);
    const prior = replayable.has(input.state) && this._one(
      "SELECT detail_json FROM production_release_events WHERE release_id=? AND type=? LIMIT 1", release.id,input.state);
    if (prior) {
      const requested = JSON.stringify(input.detail && typeof input.detail === "object" ? input.detail : {});
      if (prior.detail_json !== requested) return { ok:false, reason:"idempotency_conflict" };
      return { ok:true, replay:true, release:this.getProductionRelease(release.id) };
    }
    if (input.state === "verified") {
      const pages = this._one("SELECT id FROM production_release_events WHERE release_id=? AND type='pages_deployed'", release.id);
      const worker = this._one("SELECT id FROM production_release_events WHERE release_id=? AND type='worker_deployed'", release.id);
      if (!pages || !worker) return { ok:false, reason:"targets_incomplete" };
    }
    const allowed = {
      executing:new Set(["pages_deployed","worker_deployed","failed_fenced"]),
      pages_deployed:new Set(["worker_deployed","verified","failed_fenced"]),
      worker_deployed:new Set(["pages_deployed","verified","failed_fenced"]),
      verified:new Set(["complete","failed_fenced"]),
      failed_fenced:new Set(), restoring:new Set(["restored","failed_fenced"]),
    };
    if (!allowed[release.state]?.has(input.state)) return { ok:false, reason:"invalid_transition" };
    const detail = input.detail && typeof input.detail === "object" ? input.detail : {};
    const forbidden = JSON.stringify(detail).match(/new_text|original_text|authorization|credential|token/i);
    if (forbidden) return { ok:false, reason:"unsafe_evidence" };
    const now = this.now();
    this.transactionSync(() => {
    this.sql.exec("UPDATE production_releases SET state=?,provider_json=?,lease_expires_at=?,updated_at=? WHERE id=?",
      input.state,JSON.stringify(detail),input.state === "complete" ? null :
        now + CEILINGS.leaseMs,now,release.id);
    this.sql.exec("INSERT INTO production_release_events (release_id,type,actor,detail_json,created_at) VALUES (?,?,?,?,?)",
      release.id,input.state,input.actor,JSON.stringify(detail),now);
    if (input.state === "complete") {
      if ((release.schema_version || 1) >= 2) {
        const members = this._all("SELECT operation_id,review_revision_id,source_ref,affected_source_refs_json FROM production_release_operation_members WHERE release_id=?",release.id);
        for (const member of members) {
          const revision = this._one("SELECT source_revision FROM production_review_revisions WHERE id=?",member.review_revision_id);
          this.sql.exec("INSERT OR IGNORE INTO production_published_operations (operation_id,release_id,review_revision_id,source_ref,source_revision,candidate_sha,published_at) VALUES (?,?,?,?,?,?,?)",
            member.operation_id,release.id,member.review_revision_id,member.source_ref,
            revision?.source_revision || "",release.candidate_sha,now);
          const affected = JSON.parse(member.affected_source_refs_json || "[]");
          for (const sourceRef of affected.length ? affected : [member.source_ref])
            this.sql.exec("INSERT OR IGNORE INTO production_published_operation_sources (operation_id,source_ref,release_id,candidate_sha,published_at) VALUES (?,?,?,?,?)",
              member.operation_id,sourceRef,release.id,release.candidate_sha,now);
          this.sql.exec("UPDATE production_review_operations SET lifecycle_state='published' WHERE operation_id=?",
            member.operation_id);
        }
      } else {
        const members = this._all("SELECT suggestion_id FROM production_release_members WHERE release_id=?", release.id);
        for (const member of members) this.sql.exec(
          "UPDATE suggestions SET production_release_id=?,production_published_at=? WHERE id=? AND status=? AND (production_release_id IS NULL OR production_release_id=?)",
          release.id,now,member.suggestion_id,STATUS.APPLIED,release.id);
      }
    }
    });
    return { ok:true, release:this.getProductionRelease(release.id) };
  }

  // Trusted apply/migration seam. The operation payload is stored once and is
  // thereafter the authority; browser draft/submit calls can name an operation
  // but can never redefine its ranges, text, ancestry, or grouping.
  recordReviewRevision(input = {}) {
    const required = ["id","source_ref","source_revision","prod_base","commit_sha",
      "original_hash","proposed_hash","original_text","proposed_text"];
    if (required.some((key) => typeof input[key] !== "string" || !input[key]) ||
        !Array.isArray(input.suggestion_ids) || !Array.isArray(input.operations) ||
        input.operations.length === 0) return { ok:false, reason:"validation_error" };
    const decisionIds = new Set();
    for (const operation of input.operations) {
      const decisionId = operation?.decision_id || operation?.id;
      if (!operation || typeof operation.id !== "string" || !operation.id ||
          typeof decisionId !== "string" || !decisionId ||
          operation.source_ref !== input.source_ref ||
          operation.source_revision !== input.source_revision || operation.prod_base !== input.prod_base)
        return { ok:false, reason:"operation_mismatch" };
      decisionIds.add(decisionId);
    }
    if (decisionIds.size === 0) return { ok:false, reason:"missing_revision_evidence" };
    const evidence = { source_ref:input.source_ref, source_revision:input.source_revision,
      prod_base:input.prod_base, commit_sha:input.commit_sha, original_hash:input.original_hash,
      proposed_hash:input.proposed_hash, suggestion_ids:[...input.suggestion_ids].sort(),
      source_original_text:input.source_original_text || input.original_text,
      source_proposed_text:input.source_proposed_text || input.proposed_text,
      operations:input.operations };
    const digest = this._digest(evidence);
    const existing = this._one("SELECT evidence_digest FROM production_review_revisions WHERE id=?", input.id);
    if (existing) return existing.evidence_digest === digest ? { ok:true,replay:true,id:input.id } :
      { ok:false,reason:"idempotency_conflict" };
    if (this._one("SELECT id FROM production_review_revisions WHERE source_ref=? AND source_revision=? AND prod_base=?",
      input.source_ref,input.source_revision,input.prod_base)) return { ok:false,reason:"revision_exists" };
    const now = this.now();
    this.sql.exec("INSERT INTO production_review_revisions (id,source_ref,source_revision,prod_base,commit_sha,original_hash,proposed_hash,original_text,proposed_text,source_original_text,source_proposed_text,suggestion_ids_json,operations_json,evidence_digest,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      input.id,input.source_ref,input.source_revision,input.prod_base,input.commit_sha,input.original_hash,
      input.proposed_hash,input.original_text,input.proposed_text,
      input.source_original_text || input.original_text,input.source_proposed_text || input.proposed_text,
      this._canonical([...input.suggestion_ids].sort()),
      this._canonical(input.operations),digest,now);
    return { ok:true,id:input.id };
  }

  // One-time trusted migration seam for applied rows created before finalize()
  // could attach review evidence. It only records immutable review revisions:
  // it never creates a draft, decision, review, release member, or authority.
  // The whole migration is transactional so one suspect legacy row cannot leave
  // a partially reviewable frontier.
  backfillReviewRevisions(input = {}) {
    if (typeof input.migration_id !== "string" || !input.migration_id ||
        typeof input.prod_base !== "string" || !input.prod_base ||
        new TextEncoder().encode(input.migration_id).byteLength > 256 ||
        new TextEncoder().encode(input.prod_base).byteLength > 256 ||
        !Array.isArray(input.revisions) || input.revisions.length === 0)
      return { ok:false,reason:"validation_error" };
    const evidence = { prod_base:input.prod_base,revisions:input.revisions };
    const digest = this._digest(evidence);
    const prior = this._one("SELECT evidence_digest,revision_count FROM production_review_migrations WHERE id=?",
      input.migration_id);
    if (prior) return prior.evidence_digest === digest ?
      { ok:true,migration_id:input.migration_id,inserted:0,replayed:prior.revision_count,replay:true } :
      { ok:false,reason:"idempotency_conflict" };
    if (this._one("SELECT id FROM production_review_migrations LIMIT 1"))
      return { ok:false,reason:"migration_closed" };
    try { return this.transactionSync(() => {
      for (const revision of input.revisions) {
        if (revision?.prod_base !== input.prod_base) throw { backfillFailure:"prod_base_mismatch" };
        if (!Array.isArray(revision.suggestion_ids) || revision.suggestion_ids.length === 0)
          throw { backfillFailure:"missing_revision_evidence" };
        if (!Array.isArray(revision.batch_chain) || revision.batch_chain.length === 0 ||
            !Array.isArray(revision.suggestion_evidence) ||
            revision.suggestion_evidence.length !== revision.suggestion_ids.length)
          throw { backfillFailure:"missing_apply_evidence" };
        let expectedBase = input.prod_base;
        const batches = new Map();
        for (const item of revision.batch_chain) {
          if (!item || typeof item.batch_id !== "string" || !item.batch_id ||
              item.base_sha !== expectedBase || typeof item.commit_sha !== "string" || !item.commit_sha ||
              batches.has(item.batch_id)) throw { backfillFailure:"legacy_batch_chain_mismatch" };
          const batch = this._one("SELECT base_sha,commit_sha,phase FROM apply_batches WHERE batch_id=?",item.batch_id);
          if (!batch || batch.phase !== "done" || batch.base_sha !== item.base_sha ||
              batch.commit_sha !== item.commit_sha) throw { backfillFailure:"legacy_batch_chain_mismatch" };
          batches.set(item.batch_id,item.commit_sha);
          expectedBase = item.commit_sha;
        }
        if (expectedBase !== revision.commit_sha) throw { backfillFailure:"legacy_commit_mismatch" };
        const evidenceById = new Map();
        for (const item of revision.suggestion_evidence) {
          if (!item || typeof item.suggestion_id !== "string" || !item.suggestion_id ||
              typeof item.batch_id !== "string" || !item.batch_id ||
              typeof item.commit_sha !== "string" || !item.commit_sha || evidenceById.has(item.suggestion_id))
            throw { backfillFailure:"missing_apply_evidence" };
          evidenceById.set(item.suggestion_id,item);
        }
        if (evidenceById.size !== new Set(revision.suggestion_ids).size)
          throw { backfillFailure:"missing_apply_evidence" };
        for (const suggestionId of revision.suggestion_ids) {
          if (this._one("SELECT suggestion_id FROM production_legacy_exclusions WHERE suggestion_id=?",suggestionId))
            throw { backfillFailure:"legacy_evidence_mismatch" };
          const item = evidenceById.get(suggestionId);
          if (!item || batches.get(item.batch_id) !== item.commit_sha)
            throw { backfillFailure:"legacy_commit_mismatch" };
          const row = this._one("SELECT source_ref,status,apply_batch_id FROM suggestions WHERE id=?",suggestionId);
          if (!row || row.status !== STATUS.APPLIED) throw { backfillFailure:"legacy_suggestion_not_applied" };
          if (row.source_ref !== revision.source_ref) throw { backfillFailure:"legacy_source_mismatch" };
          if (row.apply_batch_id !== item.batch_id)
            throw { backfillFailure:"legacy_commit_mismatch" };
        }
        const authoritativeIds = [];
        for (const batchId of batches.keys()) {
          authoritativeIds.push(...this._all(
            "SELECT id FROM suggestions WHERE source_ref=? AND apply_batch_id=? AND status=? ORDER BY id",
            revision.source_ref,batchId,STATUS.APPLIED).map((row) => row.id));
        }
        if (this._canonical([...new Set(authoritativeIds)].sort()) !==
            this._canonical([...new Set(revision.suggestion_ids)].sort()))
          throw { backfillFailure:"missing_revision_evidence" };
        const recorded = this.recordReviewRevision(revision);
        if (!recorded.ok) throw { backfillFailure:recorded.reason };
      }
      this.sql.exec("INSERT INTO production_review_migrations (id,prod_base,evidence_digest,revision_count,actor,created_at) VALUES (?,?,?,?,?,?)",
        input.migration_id,input.prod_base,digest,input.revisions.length,input.actor || "migration-service",this.now());
      return { ok:true,migration_id:input.migration_id,inserted:input.revisions.length,replayed:0 };
    }); } catch (error) {
      if (error?.backfillFailure) return { ok:false,reason:error.backfillFailure };
      throw error;
    }
  }

  reconcileLegacyReview(input = {}) {
    if (typeof input.migration_id !== "string" || !input.migration_id ||
        typeof input.prod_base !== "string" || !/^[0-9a-f]{40}$/.test(input.prod_base) ||
        new TextEncoder().encode(input.migration_id).byteLength > 256 ||
        !Array.isArray(input.exclusions) || !Array.isArray(input.revisions) ||
        input.exclusions.length > 500 || input.revisions.length === 0 || input.revisions.length > 200 ||
        input.revisions.some((revision) => !Array.isArray(revision?.suggestion_ids) ||
          revision.suggestion_ids.length > 500 || !Array.isArray(revision?.operations) ||
          revision.operations.length > 1000)) return { ok:false,reason:"validation_error" };
    const evidence = { prod_base:input.prod_base,exclusions:input.exclusions,revisions:input.revisions };
    const digest = this._digest(evidence);
    const prior = this._one("SELECT evidence_digest,revision_count FROM production_review_migrations WHERE id=?",input.migration_id);
    if (prior) return prior.evidence_digest === digest ?
      { ok:true,migration_id:input.migration_id,inserted:0,replayed:prior.revision_count,replay:true } :
      { ok:false,reason:"idempotency_conflict" };
    if (this._one("SELECT id FROM production_review_migrations LIMIT 1"))
      return { ok:false,reason:"migration_closed" };
    try { return this.transactionSync(() => {
      const appliedRows = this._all(
        "SELECT id,source_ref,status,apply_batch_id,created_at FROM suggestions WHERE status=? ORDER BY id",STATUS.APPLIED);
      const appliedById = new Map(appliedRows.map((row) => [row.id,row]));
      const reviewedIds = new Set(this._all(
        "SELECT DISTINCT j.value AS id FROM production_review_revisions r JOIN json_each(r.suggestion_ids_json) j")
        .map((row) => row.id));
      const excludedIds = new Set(this._all(
        "SELECT suggestion_id AS id FROM production_legacy_exclusions").map((row) => row.id));
      const covered = new Set();
      for (const exclusion of input.exclusions) {
        if (!exclusion || typeof exclusion.suggestion_id !== "string" ||
            !/^[0-9a-f]{40}$/.test(exclusion.apply_commit || "") ||
            !/^[0-9a-f]{40}$/.test(exclusion.apply_base || "") ||
            exclusion.reason !== "reverted_legacy_uat" ||
            !/^[0-9a-f]{64}$/.test(exclusion.evidence_hash || ""))
          throw { legacyFailure:"validation_error" };
        const row = appliedById.get(exclusion.suggestion_id);
        const batch = this._one("SELECT base_sha,commit_sha,phase FROM apply_batches WHERE batch_id=?",
          exclusion.apply_batch_id);
        if (!row || row.source_ref !== exclusion.source_ref ||
            row.apply_batch_id !== exclusion.apply_batch_id || covered.has(exclusion.suggestion_id) ||
            reviewedIds.has(exclusion.suggestion_id))
          throw { legacyFailure:"legacy_evidence_mismatch" };
        if (!batch || batch.phase !== "done" || batch.base_sha !== exclusion.apply_base ||
            (batch.commit_sha && batch.commit_sha !== exclusion.apply_commit))
          throw { legacyFailure:"legacy_batch_evidence_mismatch" };
        if (!batch.commit_sha) this.sql.exec("UPDATE apply_batches SET commit_sha=? WHERE batch_id=? AND commit_sha IS NULL",
          exclusion.apply_commit,exclusion.apply_batch_id);
        covered.add(exclusion.suggestion_id);
      }
      for (const revision of input.revisions) {
        if (revision?.prod_base !== input.prod_base || !Array.isArray(revision.suggestion_ids) ||
            !Array.isArray(revision.suggestion_evidence) ||
            revision.suggestion_evidence.length !== revision.suggestion_ids.length ||
            revision.commit_sha !== revision.source_revision)
          throw { legacyFailure:"prod_base_mismatch" };
        const evidenceById = new Map();
        for (const item of revision.suggestion_evidence) {
          if (!item || typeof item.suggestion_id !== "string" || typeof item.batch_id !== "string" ||
              !/^[0-9a-f]{40}$/.test(item.base_sha || "") ||
              !/^[0-9a-f]{40}$/.test(item.commit_sha || "") || evidenceById.has(item.suggestion_id))
            throw { legacyFailure:"legacy_batch_evidence_mismatch" };
          evidenceById.set(item.suggestion_id,item);
        }
        const ordered = [];
        for (const id of revision.suggestion_ids) {
          const row = appliedById.get(id);
          const item = evidenceById.get(id);
          const batch = item && this._one(
            "SELECT base_sha,commit_sha,phase FROM apply_batches WHERE batch_id=?",item.batch_id);
          if (!row || row.source_ref !== revision.source_ref || covered.has(id) || excludedIds.has(id))
            throw { legacyFailure:"legacy_evidence_mismatch" };
          if (!item || row.apply_batch_id !== item.batch_id || !batch || batch.phase !== "done" ||
              batch.base_sha !== item.base_sha || batch.commit_sha !== item.commit_sha)
            throw { legacyFailure:"legacy_batch_evidence_mismatch" };
          ordered.push({ ...item,created_at:Number(row.created_at || 0) });
          covered.add(id);
        }
        ordered.sort((a,b) => a.created_at-b.created_at || a.suggestion_id.localeCompare(b.suggestion_id));
        if (!ordered.length || ordered.at(-1).commit_sha !== revision.commit_sha)
          throw { legacyFailure:"legacy_batch_evidence_mismatch" };
      }
      if (this._canonical([...covered].sort()) !== this._canonical([...appliedById.keys()]))
        throw { legacyFailure:"incomplete_legacy_coverage" };
      for (const revision of input.revisions) {
        const result = this.recordReviewRevision(revision);
        if (!result.ok) throw { legacyFailure:result.reason };
      }
      for (const exclusion of input.exclusions) this.sql.exec(
        "INSERT INTO production_legacy_exclusions (suggestion_id,migration_id,source_ref,apply_batch_id,apply_commit,reason,evidence_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
        exclusion.suggestion_id,input.migration_id,exclusion.source_ref,exclusion.apply_batch_id,
        exclusion.apply_commit,exclusion.reason,exclusion.evidence_hash,this.now());
      this.sql.exec("INSERT INTO production_review_migrations (id,prod_base,evidence_digest,revision_count,actor,created_at) VALUES (?,?,?,?,?,?)",
        input.migration_id,input.prod_base,digest,input.revisions.length,input.actor || "migration-service",this.now());
      return { ok:true,migration_id:input.migration_id,inserted:input.revisions.length,
        excluded:input.exclusions.length,replayed:0 };
    }); } catch (error) {
      if (error?.legacyFailure) return { ok:false,reason:error.legacyFailure };
      throw error;
    }
  }

  getLegacyBackfillEvidence(throughBatchId) {
    if (this._one("SELECT COUNT(*) AS n FROM production_review_migrations").n)
      return { ok:false,reason:"migration_closed" };
    if (typeof throughBatchId !== "string" || !throughBatchId || throughBatchId.length > 256)
      return { ok:false,reason:"validation_error" };
    const target = this._one("SELECT created_at,batch_id FROM apply_batches WHERE batch_id=? AND phase='done'",throughBatchId);
    if (!target) return { ok:false,reason:"unknown_batch" };
    return {
      ok:true,
      schema_version:1,
      suggestions:this._all("SELECT s.id,s.editor,s.kind,s.source_ref,s.original_text,s.new_text,s.group_id,s.apply_batch_id,s.created_at FROM suggestions s JOIN apply_batches b ON b.batch_id=s.apply_batch_id WHERE s.status=? AND b.phase='done' AND (b.created_at < ? OR (b.created_at=? AND b.batch_id<=?)) ORDER BY s.created_at,s.id",STATUS.APPLIED,target.created_at,target.created_at,target.batch_id),
      batches:this._all("SELECT batch_id,base_sha,commit_sha,generator_id,phase,created_at,updated_at FROM apply_batches WHERE phase='done' AND (created_at < ? OR (created_at=? AND batch_id<=?)) ORDER BY created_at,batch_id",target.created_at,target.created_at,target.batch_id),
    };
  }

  _reviewRevision(id) {
    const row = this._one("SELECT * FROM production_review_revisions WHERE id=?", id || "");
    if (!row) return null;
    return { ...row, suggestion_ids:JSON.parse(row.suggestion_ids_json),
      operations:JSON.parse(row.operations_json) };
  }

  _reviewStaleReason(revision) {
    const latest = this._one("SELECT id FROM production_review_revisions WHERE source_ref=? ORDER BY created_at DESC,id DESC LIMIT 1",
      revision.source_ref);
    if (!latest || latest.id !== revision.id) return "stale_revision";
    const sourceProd = this._one("SELECT candidate_sha FROM production_published_operation_sources WHERE source_ref=? ORDER BY published_at DESC,operation_id DESC LIMIT 1",revision.source_ref);
    if (sourceProd && sourceProd.candidate_sha !== revision.prod_base) return "stale_prod_base";
    // Legacy releases cannot prove source-level independence, so retain their
    // historical global-frontier fail-closed behavior. Versioned operation
    // releases advance only the sources whose operations actually shipped.
    const legacyProd = this._one("SELECT candidate_sha FROM production_releases WHERE state IN ('verified','complete') AND COALESCE(schema_version,1)=1 ORDER BY updated_at DESC,id DESC LIMIT 1");
    if (!sourceProd && legacyProd && legacyProd.candidate_sha !== revision.prod_base)
      return "stale_prod_base";
    return null;
  }

  _normalizeReviewDecisions(revision, decisions) {
    if (!Array.isArray(decisions)) return { reason:"validation_error" };
    const units = new Map();
    for (const operation of revision.operations) {
      const id = operation.decision_id || operation.id;
      const prior = units.get(id);
      const digest = this._digest(operation);
      if (prior && prior.operation_digest !== digest) {
        // Move endpoints intentionally share a decision id but bind both exact
        // payloads into one combined digest.
        prior.operation_digest = this._digest([prior.operation_digest,digest]);
      } else if (!prior) units.set(id, { operation_id:id, operation_digest:digest,
        group_id:operation.group_id || operation.move_pair_id || null });
    }
    const normalized = [];
    const seen = new Set();
    for (const item of decisions) {
      const id = item?.operation_id;
      const unit = units.get(id);
      if (!unit || seen.has(id)) return { reason:"operation_mismatch" };
      if (!["accepted","rejected","questioned"].includes(item.decision))
        return { reason:"invalid_decision" };
      const note = typeof item.note === "string" ? item.note.trim() : "";
      if (item.decision === "questioned" && !note) return { reason:"question_required" };
      if (note.length > 4096) return { reason:"note_too_large" };
      seen.add(id);
      normalized.push({ ...unit, decision:item.decision, note });
    }
    normalized.sort((a,b) => a.operation_id.localeCompare(b.operation_id));
    return { decisions:normalized, unit_count:units.size };
  }

  savePublisherReviewDraft(input = {}) {
    if (typeof input.actor !== "string" || !input.actor) return { ok:false,reason:"validation_error" };
    const revision = this._reviewRevision(input.review_revision_id);
    if (!revision) return { ok:false,reason:"missing_revision_evidence" };
    if (input.source_revision !== revision.source_revision || input.prod_base !== revision.prod_base)
      return { ok:false,reason:"revision_mismatch" };
    const stale = this._reviewStaleReason(revision);
    if (stale) return { ok:false,reason:stale };
    if (this._one("SELECT id FROM production_reviews WHERE review_revision_id=?", revision.id) ||
      this._one("SELECT review_id FROM production_review_submission_sources WHERE review_revision_id=?", revision.id))
      return { ok:false,reason:"review_submitted" };
    const existing = this._one("SELECT actor FROM production_review_drafts WHERE review_revision_id=?", revision.id);
    if (existing && existing.actor !== input.actor) return { ok:false,reason:"draft_owned" };
    const normalized = this._normalizeReviewDecisions(revision,input.decisions);
    if (normalized.reason) return { ok:false,reason:normalized.reason };
    const payload = normalized.decisions.map(({ operation_digest,group_id,...decision }) => decision);
    const digest = this._digest({ revision:revision.evidence_digest, decisions:normalized.decisions });
    const now = this.now();
    this.sql.exec("INSERT INTO production_review_drafts (review_revision_id,actor,source_revision,prod_base,decisions_json,payload_digest,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(review_revision_id) DO UPDATE SET decisions_json=excluded.decisions_json,payload_digest=excluded.payload_digest,updated_at=excluded.updated_at WHERE actor=excluded.actor",
      revision.id,input.actor,revision.source_revision,revision.prod_base,this._canonical(payload),digest,now);
    return { ok:true,draft:{ review_revision_id:revision.id,actor:input.actor,
      source_revision:revision.source_revision,prod_base:revision.prod_base,decisions:payload,updated_at:now } };
  }

  getPublisherReview(actor) {
    const revisionRows = this._all(`SELECT id,source_ref FROM (
      SELECT id,source_ref,ROW_NUMBER() OVER
        (PARTITION BY source_ref ORDER BY created_at DESC,id DESC) AS row_num
      FROM production_review_revisions) WHERE row_num=1 ORDER BY source_ref LIMIT 501`);
    if (revisionRows.length > 500) return { blocked_reason:"review_frontier_too_large",
      revisions:[],revision:null,draft:null,submitted_review:null,
      counts:{ total:0,reviewed:0,unreviewed:0,accepted:0,rejected:0,questioned:0,held:0 } };
    if (!revisionRows.length) return { blocked_reason:"missing_revision_evidence",revisions:[],revision:null,draft:null,
      submitted_review:null,counts:{ total:0,reviewed:0,unreviewed:0,accepted:0,rejected:0,questioned:0,held:0 } };
    const revisions = revisionRows.map((revisionRow) => {
      const revision = this._reviewRevision(revisionRow.id);
      const stale_reason = this._reviewStaleReason(revision);
      const draftRow = this._one("SELECT * FROM production_review_drafts WHERE review_revision_id=? AND actor=?",
        revision.id,actor || "");
      const reviewRow = this._one("SELECT s.id,s.actor,s.created_at,s.receipt_hash FROM production_review_submissions s JOIN production_review_submission_sources r ON r.review_id=s.id WHERE r.review_revision_id=?", revision.id) ||
        this._one("SELECT id,actor,created_at,receipt_hash FROM production_reviews WHERE review_revision_id=?", revision.id);
      let submitted = reviewRow ? this._all("SELECT operation_id,decision,note,group_id FROM production_review_submission_decisions WHERE review_id=? AND review_revision_id=? ORDER BY operation_id",
        reviewRow.id,revision.id) : [];
      if (reviewRow && !submitted.length) submitted = this._all("SELECT operation_id,decision,note,group_id FROM production_review_decisions WHERE review_id=? ORDER BY operation_id",reviewRow.id);
      const draft = draftRow ? { review_revision_id:revision.id, actor:draftRow.actor,
        source_revision:draftRow.source_revision,prod_base:draftRow.prod_base,
        decisions:JSON.parse(draftRow.decisions_json),updated_at:draftRow.updated_at } : null;
      const total = new Set(revision.operations.map((op) => op.decision_id || op.id)).size;
      return { revision:{ id:revision.id,source_ref:revision.source_ref,
        source_revision:revision.source_revision,prod_base:revision.prod_base,
        original_hash:revision.original_hash,proposed_hash:revision.proposed_hash,
        original_text:revision.original_text,proposed_text:revision.proposed_text,
        suggestion_ids:revision.suggestion_ids,operations:revision.operations,evidence_digest:revision.evidence_digest },
        stale:!!stale_reason,stale_reason,draft,
        submitted_review:reviewRow ? { id:reviewRow.id,actor:reviewRow.actor,
          created_at:reviewRow.created_at,receipt_hash:reviewRow.receipt_hash,decisions:submitted } : null,
        counts:{ total } };
    });
    const classified = classifyProductionScope(revisions.map((item) => ({
      source_ref:item.revision.source_ref,operations:item.revision.operations,
    })));
    revisions.forEach((item,index) => {
      item.revision.operations = classified[index].operations;
      const heldIds = new Set(item.revision.operations.filter((operation) =>
        operation.production_scope === "held").map((operation) => operation.decision_id || operation.id));
      const visibleDecisions = item.submitted_review?.decisions || item.draft?.decisions || [];
      item.counts.held = heldIds.size;
      item.counts.reviewed = visibleDecisions.filter((decision) => !heldIds.has(decision.operation_id)).length;
      for (const decision of ["accepted","rejected","questioned"])
        item.counts[decision] = visibleDecisions.filter((item) =>
          !heldIds.has(item.operation_id) && item.decision === decision).length;
      item.counts.unreviewed = Math.max(0,item.counts.total-item.counts.held-item.counts.reviewed);
    });
    const counts = revisions.reduce((sum,item) => {
      for (const key of Object.keys(sum)) sum[key] += item.counts[key];
      return sum;
    }, { total:0,reviewed:0,unreviewed:0,accepted:0,rejected:0,questioned:0,held:0 });
    return { revisions, ...revisions[0], counts };
  }

  // Authenticated DEV projection. Unlike getPublisherReview this exposes no
  // private drafts: only immutable submitted decisions may annotate John's
  // editing view. When a newer revision exists, retain the reviewed evidence as
  // stale rather than silently attaching its offsets to the newer prose.
  getDevReviewAnnotations(sourceRefs = []) {
    if (!Array.isArray(sourceRefs)) return [];
    const refs = [...new Set(sourceRefs.filter((ref) => typeof ref === "string" && ref))].slice(0, 1000);
    const out = [];
    const legacyProd = this._one("SELECT candidate_sha FROM production_releases WHERE state IN ('verified','complete') AND COALESCE(schema_version,1)=1 ORDER BY updated_at DESC,id DESC LIMIT 1");
    for (let offset = 0; offset < refs.length; offset += 400) {
      const chunk = refs.slice(offset,offset + 400), marks = chunk.map(() => "?").join(",");
      const currentBySource = new Map(this._all(`SELECT id,source_ref,proposed_hash FROM (
        SELECT id,source_ref,proposed_hash,ROW_NUMBER() OVER (PARTITION BY source_ref ORDER BY created_at DESC,id DESC) AS row_num
        FROM production_review_revisions WHERE source_ref IN (${marks}))
        WHERE row_num=1`,...chunk).map((row) => [row.source_ref,row]));
      const submitted = this._all(`SELECT * FROM (
        SELECT r.id,r.source_ref,r.source_revision,r.prod_base,r.proposed_hash,r.operations_json,
          s.id AS review_id,s.actor,s.created_at,
          ROW_NUMBER() OVER (PARTITION BY r.source_ref ORDER BY s.created_at DESC,s.id DESC) AS row_num
        FROM production_review_revisions r
        JOIN production_review_submission_sources x ON x.review_revision_id=r.id
        JOIN production_review_submissions s ON s.id=x.review_id
        WHERE r.source_ref IN (${marks})) WHERE row_num=1`,...chunk);
      const legacy = this._all(`SELECT * FROM (
        SELECT r.id,r.source_ref,r.source_revision,r.prod_base,r.proposed_hash,r.operations_json,
          s.id AS review_id,s.actor,s.created_at,
          ROW_NUMBER() OVER (PARTITION BY r.source_ref ORDER BY s.created_at DESC,s.id DESC) AS row_num
        FROM production_review_revisions r JOIN production_reviews s ON s.review_revision_id=r.id
        WHERE r.source_ref IN (${marks})) WHERE row_num=1`,...chunk);
      const reviewedBySource = new Map();
      for (const reviewed of [...submitted,...legacy]) {
        const prior = reviewedBySource.get(reviewed.source_ref);
        if (!prior || reviewed.created_at > prior.created_at ||
            (reviewed.created_at === prior.created_at && reviewed.review_id > prior.review_id))
          reviewedBySource.set(reviewed.source_ref,reviewed);
      }
      const reviewIds = [...new Set([...reviewedBySource.values()].map((row) => row.review_id))];
      const decisionMarks = reviewIds.map(() => "?").join(",");
      const submittedDecisions = reviewIds.length ? this._all(
        `SELECT review_id,review_revision_id,operation_id,decision,note FROM production_review_submission_decisions WHERE review_id IN (${decisionMarks}) ORDER BY operation_id`,...reviewIds) : [];
      const legacyDecisions = reviewIds.length ? this._all(
        `SELECT review_id,operation_id,decision,note FROM production_review_decisions WHERE review_id IN (${decisionMarks}) ORDER BY operation_id`,...reviewIds) : [];
      const decisionsByReview = new Map();
      for (const item of [...submittedDecisions,...legacyDecisions]) {
        const key = `${item.review_id}\0${item.review_revision_id || ""}`;
        if (!decisionsByReview.has(key)) decisionsByReview.set(key,[]);
        decisionsByReview.get(key).push({ operation_id:item.operation_id,decision:item.decision,note:item.note });
      }
      const publishedBySource = new Map(this._all(`SELECT source_ref,candidate_sha FROM (
        SELECT source_ref,candidate_sha,ROW_NUMBER() OVER (PARTITION BY source_ref ORDER BY published_at DESC,operation_id DESC) AS row_num
        FROM production_published_operation_sources WHERE source_ref IN (${marks}))
        WHERE row_num=1`,...chunk).map((row) => [row.source_ref,row]));
      for (const sourceRef of chunk) {
        const current = currentBySource.get(sourceRef),reviewed = reviewedBySource.get(sourceRef);
        if (!current || !reviewed) continue;
        const sourceProd = publishedBySource.get(sourceRef);
        const staleReason = current.id !== reviewed.id ? "stale_revision" :
          sourceProd && sourceProd.candidate_sha !== reviewed.prod_base ? "stale_prod_base" :
          !sourceProd && legacyProd && legacyProd.candidate_sha !== reviewed.prod_base ? "stale_prod_base" : null;
        const decisions = decisionsByReview.get(`${reviewed.review_id}\0${submitted.includes(reviewed) ? reviewed.id : ""}`) || [];
        out.push({ source_ref:sourceRef,review_revision_id:reviewed.id,
          source_revision:reviewed.source_revision,proposed_hash:reviewed.proposed_hash,
          current_proposed_hash:current.proposed_hash,reviewer:reviewed.actor,
          submitted_at:reviewed.created_at,stale:!!staleReason,stale_reason:staleReason,
          operations:JSON.parse(reviewed.operations_json),decisions });
      }
    }
    return out;
  }

  _submittedReview(id) {
    const submission = this._one("SELECT * FROM production_review_submissions WHERE id=?", id || "");
    if (submission) {
      const sources = this._all("SELECT review_revision_id,source_revision,prod_base,evidence_digest FROM production_review_submission_sources WHERE review_id=? ORDER BY review_revision_id",submission.id)
        .map((source) => ({ ...source,decisions:this._all("SELECT operation_id,decision,note,group_id FROM production_review_submission_decisions WHERE review_id=? AND review_revision_id=? ORDER BY operation_id",submission.id,source.review_revision_id) }));
      return { id:submission.id,actor:submission.actor,created_at:submission.created_at,
        receipt_hash:submission.receipt_hash,sources,decisions:sources.flatMap((source) => source.decisions) };
    }
    const row = this._one("SELECT * FROM production_reviews WHERE id=?", id || "");
    if (!row) return null;
    return { id:row.id,actor:row.actor,created_at:row.created_at,receipt_hash:row.receipt_hash,
      decisions:this._all("SELECT operation_id,decision,note,group_id FROM production_review_decisions WHERE review_id=? ORDER BY operation_id", row.id) };
  }

  submitPublisherReview(input = {}) {
    const required = ["id","idempotency_key","request_digest","actor"];
    if (required.some((key) => typeof input[key] !== "string" || !input[key]))
      return { ok:false,reason:"validation_error" };
    const sources = Array.isArray(input.sources) ? input.sources : [{ review_revision_id:input.review_revision_id,
      source_revision:input.source_revision,prod_base:input.prod_base,decisions:input.decisions }];
    if (!sources.length || sources.some((source) => !source ||
      ["review_revision_id","source_revision","prod_base"].some((key) => typeof source[key] !== "string" || !source[key])) ||
      new Set(sources.map((source) => source.review_revision_id)).size !== sources.length)
      return { ok:false,reason:"validation_error" };
    const prior = this._one("SELECT id,request_digest,actor FROM production_review_submissions WHERE idempotency_key=?",
      input.idempotency_key) || this._one("SELECT id,request_digest,actor FROM production_reviews WHERE idempotency_key=?",input.idempotency_key);
    if (prior) return prior.id === input.id && prior.request_digest === input.request_digest &&
      prior.actor === input.actor ? { ok:true,replay:true,review:this._submittedReview(prior.id) } :
      { ok:false,reason:"idempotency_conflict" };
    if (this._one("SELECT id FROM production_review_submissions WHERE id=?", input.id) ||
      this._one("SELECT id FROM production_reviews WHERE id=?", input.id))
      return { ok:false,reason:"review_exists" };
    const prepared = [];
    for (const source of sources) {
      const revision = this._reviewRevision(source.review_revision_id);
      if (!revision) return { ok:false,reason:"missing_revision_evidence" };
      if (source.source_revision !== revision.source_revision || source.prod_base !== revision.prod_base)
        return { ok:false,reason:"revision_mismatch" };
      const stale = this._reviewStaleReason(revision);
      if (stale) return { ok:false,reason:stale };
      if (this._one("SELECT review_id FROM production_review_submission_sources WHERE review_revision_id=?",revision.id) ||
        this._one("SELECT id FROM production_reviews WHERE review_revision_id=?",revision.id))
        return { ok:false,reason:"review_submitted" };
      const draft = this._one("SELECT * FROM production_review_drafts WHERE review_revision_id=?",revision.id);
      if (!draft) return { ok:false,reason:"draft_missing" };
      if (draft.actor !== input.actor) return { ok:false,reason:"draft_owned" };
      const normalized = this._normalizeReviewDecisions(revision,source.decisions);
      if (normalized.reason) return { ok:false,reason:normalized.reason };
      if (this._digest({ revision:revision.evidence_digest,decisions:normalized.decisions }) !== draft.payload_digest)
        return { ok:false,reason:"draft_mismatch" };
      const groupMembers = new Map();
      for (const operation of revision.operations) if (operation.group_id) {
        if (!groupMembers.has(operation.group_id)) groupMembers.set(operation.group_id,new Set());
        groupMembers.get(operation.group_id).add(operation.decision_id || operation.id);
      }
      for (const [groupId,members] of groupMembers) {
        const answered = normalized.decisions.filter((unit) => unit.group_id === groupId);
        if (answered.length && (answered.length !== members.size || new Set(answered.map((item) => item.decision)).size !== 1))
          return { ok:false,reason:"partial_group" };
      }
      prepared.push({ revision,decisions:normalized.decisions });
    }
    const submittedGroups = new Map();
    for (const { revision,decisions } of prepared) {
      for (const operation of revision.operations) {
        if (!operation.group_id) continue;
        if (!submittedGroups.has(operation.group_id))
          submittedGroups.set(operation.group_id,{ members:0,answered:0,decisions:new Set() });
        const group = submittedGroups.get(operation.group_id);
        group.members += 1;
        const decision = decisions.find((item) => item.operation_id ===
          (operation.decision_id || operation.id));
        if (decision) {
          group.answered += 1;
          group.decisions.add(decision.decision);
        }
      }
    }
    for (const group of submittedGroups.values())
      if (group.answered && (group.answered !== group.members || group.decisions.size !== 1))
        return { ok:false,reason:"partial_group" };
    const now = this.now();
    const receipt = { review_id:input.id,actor:input.actor,created_at:now,
      sources:prepared.map(({ revision,decisions }) => ({ review_revision_id:revision.id,
        source_revision:revision.source_revision,prod_base:revision.prod_base,
        evidence_digest:revision.evidence_digest,decisions })) };
    const receiptHash = this._digest(receipt);
    this.transactionSync(() => {
      this.sql.exec("INSERT INTO production_review_submissions (id,idempotency_key,request_digest,actor,receipt_hash,receipt_json,created_at) VALUES (?,?,?,?,?,?,?)",
        input.id,input.idempotency_key,input.request_digest,input.actor,receiptHash,this._canonical(receipt),now);
      for (const { revision,decisions } of prepared) {
        this.sql.exec("UPDATE production_review_operations SET lifecycle_state='superseded' WHERE source_ref=? AND lifecycle_state='unpublished'",
          revision.source_ref);
        this.sql.exec("INSERT INTO production_review_submission_sources (review_id,review_revision_id,source_revision,prod_base,evidence_digest) VALUES (?,?,?,?,?)",
          input.id,revision.id,revision.source_revision,revision.prod_base,revision.evidence_digest);
        for (const decision of decisions) this.sql.exec(
          "INSERT INTO production_review_submission_decisions (review_id,review_revision_id,operation_id,decision,note,operation_digest,group_id) VALUES (?,?,?,?,?,?,?)",
          input.id,revision.id,decision.operation_id,decision.decision,decision.note,decision.operation_digest,decision.group_id);
        const decisionsById = new Map(decisions.map((decision) => [decision.operation_id,decision]));
        for (const operation of revision.operations) {
          const decisionId = operation.decision_id || operation.id;
          const decision = decisionsById.get(decisionId);
          this.sql.exec(`INSERT INTO production_review_operations
            (operation_id,decision_id,review_id,review_revision_id,source_ref,group_id,decision,note,lifecycle_state)
            VALUES (?,?,?,?,?,?,?,?,?)`,operation.id,decisionId,input.id,revision.id,
            revision.source_ref,operation.group_id || operation.move_pair_id || null,
            decision?.decision || "unanswered",decision?.note || "","unpublished");
        }
        this.sql.exec("DELETE FROM production_review_drafts WHERE review_revision_id=? AND actor=?",revision.id,input.actor);
      }
    });
    return { ok:true,review:this._submittedReview(input.id) };
  }

  // Read-only projection for the human Publisher surface. A selectable target
  // is always a complete apply batch after the last verified production
  // frontier; each target implicitly encloses every earlier returned batch.
  publisherContext() {
    const activeRow = this._one(
      "SELECT id FROM production_releases WHERE state IN ('prepared','authorized','executing','pages_deployed','worker_deployed','delayed','failed_fenced','restoring','verified') ORDER BY updated_at DESC LIMIT 1");
    const release = activeRow ? this.getProductionRelease(activeRow.id) : null;
    const frontier = this._one(
      "SELECT target_batch_id FROM production_releases WHERE state IN ('verified','complete') ORDER BY updated_at DESC,id DESC LIMIT 1");
    const done = this._all(
      "SELECT batch_id,commit_sha,generator_id,created_at,updated_at FROM apply_batches WHERE phase='done' ORDER BY created_at,batch_id");
    let start = 0;
    if (frontier) {
      const at = done.findIndex((b) => b.batch_id === frontier.target_batch_id);
      start = at < 0 ? done.length : at + 1;
    }
    const visible = release ? release.batches.map((frozen) =>
      done.find((batch) => batch.batch_id === frozen.batch_id)).filter(Boolean) : done.slice(start);
    const batches = visible.map((batch) => ({ ...batch, changes: [...this._all(
      "SELECT id,editor,origin,kind,page,source_ref,original_text,new_text,comment,group_id,status,apply_batch_id,created_at,updated_at FROM suggestions WHERE apply_batch_id=? AND status=? ORDER BY id",
      batch.batch_id, STATUS.APPLIED), ...this._all(
      "SELECT id,actor AS editor,'human' AS origin,kind,NULL AS page,source_ref,original_text,new_text,NULL AS comment,NULL AS group_id,'applied' AS status,batch_id AS apply_batch_id,created_at,created_at AS updated_at FROM canonical_mutations WHERE batch_id=? ORDER BY id",
      batch.batch_id)] }));
    return { release, batches };
  }

  _heldReviewGroups(sources) {
    const statesByGroup = new Map();
    for (const source of sources || []) {
      const decisions = new Map((source.decisions || []).map((item) =>
        [item.operation_id,item.decision]));
      for (const operation of source.operations || []) {
        const groupId = operation.group_id || operation.move_pair_id;
        if (!groupId) continue;
        if (!statesByGroup.has(groupId)) statesByGroup.set(groupId,[]);
        statesByGroup.get(groupId).push({ stale:source.stale,
          decision:decisions.get(operation.decision_id || operation.id) });
      }
    }
    return new Set([...statesByGroup].filter(([_groupId,states]) =>
      states.some((state) => state.decision === "accepted") &&
      states.some((state) => state.stale || state.decision !== "accepted"))
      .map(([groupId]) => groupId));
  }

  _operationFrontierProjection({ summaryOnly=false } = {}) {
    const evidenceColumns = summaryOnly ? ",r.operations_json" : `,r.original_hash,r.proposed_hash,
        r.original_text,r.source_original_text,r.operations_json`;
    const submittedRows = this._all(`SELECT * FROM (WITH active_groups AS (
        SELECT DISTINCT group_id FROM production_review_operations
        WHERE lifecycle_state='unpublished' AND decision='accepted' AND group_id IS NOT NULL
          AND operation_id NOT IN (SELECT operation_id FROM production_published_operations)
      ), candidate_revisions AS (
        SELECT DISTINCT review_revision_id FROM production_review_operations
        WHERE (lifecycle_state='unpublished' AND decision='accepted'
          AND operation_id NOT IN (SELECT operation_id FROM production_published_operations))
           OR group_id IN (SELECT group_id FROM active_groups)
      )
      SELECT s.id,s.actor,s.created_at,s.receipt_hash,r.id AS review_revision_id,
        r.source_ref,r.source_revision,r.prod_base${evidenceColumns}
      FROM candidate_revisions candidate
      JOIN production_review_revisions r ON r.id=candidate.review_revision_id
      JOIN production_review_submission_sources x ON x.review_revision_id=r.id
      JOIN production_review_submissions s ON s.id=x.review_id
      ORDER BY r.source_ref,s.created_at,s.id,r.id)`);
    if (!submittedRows.length) return { review_receipts:[],sources:[],eligible_operation_count:0 };

    const revisionIds = [...new Set(submittedRows.map((row) => row.review_revision_id))];
    const revisionMarks = revisionIds.map(() => "?").join(",");
    const operationRows = this._all(`SELECT operation_id,decision_id,review_id,review_revision_id,
      group_id,decision,note,lifecycle_state FROM production_review_operations
      WHERE review_revision_id IN (${revisionMarks}) ORDER BY operation_id`,...revisionIds);
    const lifecycleByOperation = new Map(operationRows.map((row) =>
      [row.operation_id,row.lifecycle_state]));
    const publishedOperations = new Set(this._all(
      "SELECT operation_id FROM production_published_operations").map((row) => row.operation_id));
    const operationsByRevision = new Map();
    const decisionsByRevision = new Map();
    for (const operation of operationRows) {
      if (!operationsByRevision.has(operation.review_revision_id))
        operationsByRevision.set(operation.review_revision_id,new Map());
      operationsByRevision.get(operation.review_revision_id).set(operation.operation_id,operation);
      if (!decisionsByRevision.has(operation.review_revision_id))
        decisionsByRevision.set(operation.review_revision_id,new Map());
      decisionsByRevision.get(operation.review_revision_id).set(operation.decision_id,{
        operation_id:operation.decision_id,decision:operation.decision,note:operation.note,
        group_id:operation.group_id });
    }

    const sourceRefs = [...new Set(submittedRows.map((row) => row.source_ref))];
    const sourceMarks = sourceRefs.map(() => "?").join(",");
    const latestBySource = new Map(this._all(`SELECT source_ref,id FROM (
      SELECT source_ref,id,ROW_NUMBER() OVER (PARTITION BY source_ref ORDER BY created_at DESC,id DESC) AS row_num
      FROM production_review_revisions WHERE source_ref IN (${sourceMarks})) WHERE row_num=1`,...sourceRefs)
      .map((row) => [row.source_ref,row.id]));
    const publishedBySource = new Map(this._all(`SELECT source_ref,candidate_sha FROM (
      SELECT source_ref,candidate_sha,ROW_NUMBER() OVER (PARTITION BY source_ref ORDER BY published_at DESC,operation_id DESC) AS row_num
      FROM production_published_operation_sources WHERE source_ref IN (${sourceMarks})) WHERE row_num=1`,...sourceRefs)
      .map((row) => [row.source_ref,row.candidate_sha]));
    const legacyProd = this._one("SELECT candidate_sha FROM production_releases WHERE state IN ('verified','complete') AND COALESCE(schema_version,1)=1 ORDER BY updated_at DESC,id DESC LIMIT 1");
    const candidateOperationIds = [...lifecycleByOperation.keys()];
    const candidateMarks = candidateOperationIds.map(() => "?").join(",");
    const frozen = new Set(this._all(`SELECT member.operation_id FROM production_release_operation_members member
      JOIN production_releases release ON release.id=member.release_id
      WHERE release.state IN ('prepared','authorized','executing','pages_deployed','worker_deployed',
        'delayed','failed_fenced','restoring','verified')
        AND member.operation_id IN (${candidateMarks})`,
      ...candidateOperationIds).map((row) => row.operation_id));

    const receiptById = new Map();
    const projectionSources = [];
    for (const row of submittedRows) {
      const sourcePublished = publishedBySource.get(row.source_ref);
      const stale = latestBySource.get(row.source_ref) !== row.review_revision_id ||
        (sourcePublished ? sourcePublished !== row.prod_base :
          !!legacyProd && legacyProd.candidate_sha !== row.prod_base);
      const operationState = operationsByRevision.get(row.review_revision_id) || new Map();
      const recordedOperations = JSON.parse(row.operations_json);
      const operations = (summaryOnly ? recordedOperations.map((operation) => ({
        id:operation.id,decision_id:operation.decision_id || operation.id,
        group_id:operation.group_id || null,move_pair_id:operation.move_pair_id || null,
        source_ref:operation.source_ref || row.source_ref,op:operation.op || null,
        op_arg:operation.op_arg || null })) : recordedOperations).filter((operation) =>
          operationState.get(operation.id)?.lifecycle_state !== "published" &&
          !publishedOperations.has(operation.id));
      if (!operations.length) continue;
      receiptById.set(row.id,{ id:row.id,actor:row.actor,created_at:row.created_at,
        receipt_hash:row.receipt_hash });
      projectionSources.push({ review_id:row.id,review_revision_id:row.review_revision_id,
        source_ref:row.source_ref,source_revision:row.source_revision,prod_base:row.prod_base,
        ...(summaryOnly ? {} : { original_hash:row.original_hash,proposed_hash:row.proposed_hash,
          original_text:row.original_text,
          source_original_text:row.source_original_text || row.original_text }),
        operations,stale,decisions:[...(decisionsByRevision.get(row.review_revision_id)?.values() || [])] });
    }

    const classifiedSources = classifyProductionScope(projectionSources);
    const heldGroups = this._heldReviewGroups(classifiedSources);
    let eligibleOperationCount = 0, heldOperationCount = 0;
    for (const source of classifiedSources) {
      const decisions = new Map(source.decisions.map((decision) => [decision.operation_id,decision.decision]));
      for (const operation of source.operations) {
        const groupId = operation.group_id || operation.move_pair_id;
        if (operation.production_scope === "held") heldOperationCount += 1;
        if (operation.production_scope !== "held" && !source.stale &&
            lifecycleByOperation.get(operation.id) === "unpublished" && !frozen.has(operation.id) &&
            decisions.get(operation.decision_id || operation.id) === "accepted" &&
            !heldGroups.has(groupId)) eligibleOperationCount += 1;
      }
    }
    return { review_receipts:[...receiptById.values()].sort((a,b) =>
      a.created_at-b.created_at || a.id.localeCompare(b.id)),sources:classifiedSources,
      eligible_operation_count:eligibleOperationCount,held_operation_count:heldOperationCount };
  }

  publisherSummary() {
    if (this._one("SELECT operation_id FROM production_review_operations LIMIT 1"))
      return { eligible:this._operationFrontierProjection({ summaryOnly:true }).eligible_operation_count };
    const frontier = this._one(
      "SELECT b.created_at,b.batch_id FROM production_releases r JOIN apply_batches b ON b.batch_id=r.target_batch_id WHERE r.state IN ('verified','complete') ORDER BY r.updated_at DESC,r.id DESC LIMIT 1");
    const row = frontier ? this._one(
      "SELECT COUNT(*) AS count FROM suggestions s JOIN apply_batches b ON b.batch_id=s.apply_batch_id WHERE s.status=? AND b.phase='done' AND (b.created_at>? OR (b.created_at=? AND b.batch_id>?))",
      STATUS.APPLIED,frontier.created_at,frontier.created_at,frontier.batch_id) : this._one(
      "SELECT COUNT(*) AS count FROM suggestions s JOIN apply_batches b ON b.batch_id=s.apply_batch_id WHERE s.status=? AND b.phase='done'",
      STATUS.APPLIED);
    const mutationRow = frontier ? this._one(
      "SELECT COUNT(*) AS count FROM canonical_mutations m JOIN apply_batches b ON b.batch_id=m.batch_id WHERE b.phase='done' AND (b.created_at>? OR (b.created_at=? AND b.batch_id>?))",
      frontier.created_at,frontier.created_at,frontier.batch_id) : this._one(
      "SELECT COUNT(*) AS count FROM canonical_mutations m JOIN apply_batches b ON b.batch_id=m.batch_id WHERE b.phase='done'");
    return { eligible:Number(row?.count || 0) + Number(mutationRow?.count || 0) };
  }

  // Text-free, read-only evidence for config-off migration and rollout gates.
  // This deliberately returns counts, immutable receipt identities, and broken
  // relationship counts only. It never returns authored copy or decisions.
  productionReleaseAudit() {
    const count = (sql, ...args) => Number(this._one(sql, ...args)?.count || 0);
    const counts = {
      apply_batches_done:count("SELECT COUNT(*) AS count FROM apply_batches WHERE phase='done'"),
      applied_suggestions:count("SELECT COUNT(*) AS count FROM suggestions WHERE status=?", STATUS.APPLIED),
      canonical_mutations:count("SELECT COUNT(*) AS count FROM canonical_mutations"),
      review_migrations:count("SELECT COUNT(*) AS count FROM production_review_migrations"),
      legacy_exclusions:count("SELECT COUNT(*) AS count FROM production_legacy_exclusions"),
      review_revisions:count("SELECT COUNT(*) AS count FROM production_review_revisions"),
      review_operations:count("SELECT COUNT(*) AS count FROM production_review_operations"),
      review_submissions:count("SELECT COUNT(*) AS count FROM production_review_submissions"),
      production_releases:count("SELECT COUNT(*) AS count FROM production_releases"),
      published_operations:count("SELECT COUNT(*) AS count FROM production_published_operations"),
    };
    const invariants = {
      legacy_receipts_without_operations:count(
        "SELECT COUNT(*) AS count FROM production_reviews r WHERE NOT EXISTS (SELECT 1 FROM production_review_operations o WHERE o.review_revision_id=r.review_revision_id)"),
      submitted_sources_without_revision:count(
        "SELECT COUNT(*) AS count FROM production_review_submission_sources s WHERE NOT EXISTS (SELECT 1 FROM production_review_revisions r WHERE r.id=s.review_revision_id)"),
      submitted_revision_operations_missing:count(
        "SELECT COUNT(*) AS count FROM production_review_submission_sources s JOIN production_review_revisions r ON r.id=s.review_revision_id JOIN json_each(r.operations_json) j WHERE NOT EXISTS (SELECT 1 FROM production_review_operations o WHERE o.review_id=s.review_id AND o.review_revision_id=s.review_revision_id AND o.operation_id=json_extract(j.value,'$.id'))"),
      decisions_without_operation:count(
        "SELECT COUNT(*) AS count FROM production_review_submission_decisions d WHERE NOT EXISTS (SELECT 1 FROM production_review_operations o WHERE o.review_id=d.review_id AND o.review_revision_id=d.review_revision_id AND o.decision_id=d.operation_id)"),
      operations_without_revision:count(
        "SELECT COUNT(*) AS count FROM production_review_operations o WHERE NOT EXISTS (SELECT 1 FROM production_review_revisions r WHERE r.id=o.review_revision_id)"),
      published_operations_without_release:count(
        "SELECT COUNT(*) AS count FROM production_published_operations p WHERE NOT EXISTS (SELECT 1 FROM production_releases r WHERE r.id=p.release_id AND r.state='complete')"),
      oversized_migration_fields:count(
        "SELECT COUNT(*) AS count FROM production_review_migrations WHERE length(CAST(id AS BLOB))>256 OR length(CAST(prod_base AS BLOB))>256"),
      unreconciled_applied_suggestions:count(
        "WITH covered(id) AS (SELECT suggestion_id FROM production_legacy_exclusions UNION SELECT j.value FROM production_review_revisions r JOIN json_each(r.suggestion_ids_json) j) SELECT COUNT(*) AS count FROM suggestions s LEFT JOIN covered c ON c.id=s.id WHERE s.status=? AND c.id IS NULL",STATUS.APPLIED),
      legacy_exclusion_review_overlap:count(
        "SELECT COUNT(*) AS count FROM production_legacy_exclusions e WHERE EXISTS (SELECT 1 FROM production_review_revisions r JOIN json_each(r.suggestion_ids_json) j WHERE j.value=e.suggestion_id)"),
    };
    const migrations = this._all(
      "SELECT CASE WHEN length(CAST(id AS BLOB))<=256 THEN id END AS id,CASE WHEN length(CAST(prod_base AS BLOB))<=256 THEN prod_base END AS prod_base,evidence_digest,revision_count,actor,created_at FROM production_review_migrations ORDER BY created_at DESC,id DESC LIMIT 100").reverse();
    const release_states = this._all(
      "SELECT state,COUNT(*) AS count FROM production_releases GROUP BY state ORDER BY state")
      .map((row) => ({ state:row.state,count:Number(row.count || 0) }));
    const activeRows = this._all(
      "SELECT id,state,base_sha,candidate_sha,schema_version,updated_at FROM production_releases WHERE state NOT IN ('complete','restored') ORDER BY updated_at DESC,id DESC LIMIT 21");
    const active_releases = activeRows.slice(0,20).reverse();
    return { schema_version:1,counts,invariants,migrations,
      migrations_truncated:counts.review_migrations > migrations.length,
      release_states,active_releases,active_releases_truncated:activeRows.length > 20 };
  }

  // Minimal, text-free projection for the trusted candidate builder.  The
  // service receives immutable IDs and commit evidence; edited copy remains
  // confined to the human Publisher preview.
  productionPreparationContext() {
    const activeRow = this._one(
      "SELECT id FROM production_releases WHERE state NOT IN ('complete','restored') ORDER BY updated_at DESC,id DESC LIMIT 1");
    if (activeRow) return { active_release:this.getProductionRelease(activeRow.id), batches:[] };
    const missing = this._one(
      "SELECT batch_id FROM apply_batches WHERE phase='evidence_missing' ORDER BY created_at,batch_id LIMIT 1");
    if (missing) return { active_release:null, batches:[], blocked_reason:"missing_batch_evidence",
      blocked_batch_id:missing.batch_id };
    const frontier = this._one(
      "SELECT target_batch_id,candidate_sha FROM production_releases WHERE state='complete' ORDER BY updated_at DESC,id DESC LIMIT 1");
    const allDone = this._all(
      "SELECT batch_id,commit_sha,generator_id,created_at FROM apply_batches WHERE phase='done' ORDER BY created_at,batch_id");
    const done = allDone.filter((batch) => this._one(
      "SELECT id FROM suggestions WHERE apply_batch_id=? AND status=? LIMIT 1",
      batch.batch_id, STATUS.APPLIED) || this._one(
      "SELECT id FROM canonical_mutations WHERE batch_id=? LIMIT 1", batch.batch_id));
    let start = 0;
    if (frontier) {
      const at = done.findIndex((batch) => batch.batch_id === frontier.target_batch_id);
      start = at < 0 ? done.length : at + 1;
    }
    const batches = done.slice(start).map((batch) => ({ ...batch,
      suggestion_ids:[...this._all(
        "SELECT id FROM suggestions WHERE apply_batch_id=? AND status=? ORDER BY id",
        batch.batch_id, STATUS.APPLIED).map((row) => row.id), ...this._all(
        "SELECT id FROM canonical_mutations WHERE batch_id=? ORDER BY id", batch.batch_id)
        .map((row) => row.id)] }));
    const projection = this._operationFrontierProjection();
    return { active_release:null, base_sha:frontier?.candidate_sha || null, batches, projection };
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
        const evidence = this._one(
          "SELECT commit_sha,generator_id FROM apply_batches WHERE batch_id=?", b.batch_id);
        const terminalPhase = evidence?.commit_sha && evidence?.generator_id ? "done" : "evidence_missing";
        this.sql.exec(
          "UPDATE apply_batches SET phase=?, updated_at=? WHERE batch_id=?",
          terminalPhase, now, b.batch_id
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
        "SELECT r.*,m.batch_id,m.actor AS mutation_actor,m.source_ref,m.original_text,m.new_text,m.original_hash,m.new_hash,b.base_sha,b.commit_sha,b.generator_id,b.phase AS mutation_phase FROM revert_requests r LEFT JOIN canonical_mutations m ON m.id=r.id LEFT JOIN apply_batches b ON b.batch_id=m.batch_id WHERE r.status=? ORDER BY r.created_at ASC", status);
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

  // ---- assessment audit records (U12) ------------------------------------
  // `scopes` is the server-resolved auth record. Only the Access-authenticated
  // human review endpoints construct this capability; knowing an id is not enough.
  _hasAssessmentReviewScope(scopes) {
    return scopes?.[ASSESSMENT_REVIEW_SCOPE]?.granted === true;
  }

  _validAssessmentId(value) {
    return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$/.test(value);
  }

  _assessmentSummary(row) {
    return {
      ok: true,
      id: row.id,
      schema_version: row.schema_version,
      expires_at: row.expires_at,
    };
  }

  recordAssessmentAudit(input = {}) {
    if (!this._validAssessmentId(input.id) || !isRecord(input.evidence) ||
        !isRecord(input.result) || !isRecord(input.provenance)) {
      return { ok: false, reason: "validation_error" };
    }
    if (!isRecord(input.retention)) return { ok: false, reason: "retention_required" };
    const retentionDays = input.retention.days;
    if (!Number.isInteger(retentionDays) || retentionDays < 1 ||
        retentionDays > ASSESSMENT_RETENTION_MAX_DAYS) {
      return { ok: false, reason: "retention_invalid" };
    }
    if (!["formative", "summative"].includes(input.assessment_use) ||
        input.result.assessment_use !== input.assessment_use ||
        !Array.isArray(input.summative_blockers) ||
        !Array.isArray(input.result.summative_blockers)) {
      return { ok: false, reason: "assessment_contract_invalid" };
    }

    const config = input.provenance.config;
    const instrument = input.provenance.instrument;
    const providers = input.provenance.providers;
    if (!isRecord(config) || typeof config.source !== "string" || !config.source ||
        typeof config.version !== "string" || !config.version || !isRecord(instrument) ||
        !Array.isArray(providers)) {
      return { ok: false, reason: "provenance_required" };
    }
    const resultInstrument = input.result.instrument;
    if (!isRecord(resultInstrument) || instrument.id !== resultInstrument.id ||
        instrument.version !== resultInstrument.version ||
        instrument.content_hash !== resultInstrument.content_hash) {
      return { ok: false, reason: "instrument_provenance_mismatch" };
    }

    const secrets = new Set();
    collectCredentialStrings(input, secrets);
    if ([...secrets].some((secret) => secret.length < 4)) {
      return { ok: false, reason: "credential_material_invalid" };
    }
    const safeEvidence = sanitizedPayload(input.evidence, secrets);
    const safeResult = sanitizedPayload(input.result, secrets);
    const safeConfig = sanitizedPayload(config, secrets);
    const safeInstrument = sanitizedPayload(instrument, secrets);
    const safeProviders = sanitizedPayload(providers, secrets);
    const safeBlockers = sanitizedPayload(input.summative_blockers, secrets);
    if (this._canonical(safeProviders) !== this._canonical(safeResult.providers || [])) {
      return { ok: false, reason: "provider_provenance_mismatch" };
    }
    if (this._canonical(safeConfig) !==
        this._canonical(safeResult.threshold_configuration || {})) {
      return { ok: false, reason: "config_provenance_mismatch" };
    }
    if (this._canonical(safeBlockers) !==
        this._canonical(safeResult.summative_blockers || [])) {
      return { ok: false, reason: "summative_blockers_mismatch" };
    }

    const evidenceJson = this._canonical(safeEvidence);
    const resultJson = this._canonical(safeResult);
    const configJson = this._canonical(safeConfig);
    const instrumentJson = this._canonical(safeInstrument);
    const providersJson = this._canonical(safeProviders);
    const blockersJson = this._canonical(safeBlockers);
    const digest = this._digest({ assessment_use: input.assessment_use, evidenceJson,
      resultJson, configJson, instrumentJson, providersJson, blockersJson, retentionDays });
    let existing = this._one("SELECT * FROM assessment_audit_records WHERE id=?", input.id);
    if (existing && existing.expires_at <= this.now()) {
      this._deleteAssessmentAudits([input.id]);
      existing = null;
    }
    if (existing) {
      if (existing.record_digest !== digest) return { ok: false, reason: "id_conflict" };
      return { ...this._assessmentSummary(existing), replay: true };
    }

    const now = this.now();
    const expiresAt = now + retentionDays * DAY_MS;
    if (!Number.isSafeInteger(expiresAt)) return { ok: false, reason: "retention_invalid" };
    this.sql.exec(`INSERT INTO assessment_audit_records
      (id,schema_version,assessment_use,evidence_json,result_json,config_provenance_json,
       instrument_provenance_json,provider_provenance_json,summative_blockers_json,
       record_digest,retention_days,expires_at,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, input.id,ASSESSMENT_AUDIT_SCHEMA_VERSION,
      input.assessment_use,evidenceJson,resultJson,configJson,instrumentJson,providersJson,
      blockersJson,digest,retentionDays,expiresAt,now);
    return { ok: true, id: input.id, schema_version: ASSESSMENT_AUDIT_SCHEMA_VERSION,
      expires_at: expiresAt };
  }

  readAssessmentAudit({ id, scopes } = {}) {
    if (!this._hasAssessmentReviewScope(scopes)) {
      return { ok: false, reason: "assessment_review_scope_required" };
    }
    if (!this._validAssessmentId(id)) return { ok: false, reason: "not_found" };
    const row = this._one("SELECT * FROM assessment_audit_records WHERE id=?", id);
    if (!row) return { ok: false, reason: "not_found" };
    if (row.expires_at <= this.now()) {
      this._deleteAssessmentAudits([id]);
      return { ok: false, reason: "not_found" };
    }
    const overrides = this._all(`SELECT id,schema_version,author,override_json,created_at
      FROM assessment_audit_overrides WHERE assessment_id=? ORDER BY created_at,id`,id)
      .map((item) => ({ id:item.id,schema_version:item.schema_version,author:item.author,
        created_at:item.created_at,value:JSON.parse(item.override_json) }));
    return { ok: true, record: {
      id: row.id,
      schema_version: row.schema_version,
      assessment_use: row.assessment_use,
      evidence: JSON.parse(row.evidence_json),
      result: JSON.parse(row.result_json),
      provenance: {
        config: JSON.parse(row.config_provenance_json),
        instrument: JSON.parse(row.instrument_provenance_json),
        providers: JSON.parse(row.provider_provenance_json),
      },
      summative_blockers: JSON.parse(row.summative_blockers_json),
      retention: { days: row.retention_days, expires_at: row.expires_at },
      created_at: row.created_at,
      overrides,
    } };
  }

  recordAssessmentOverride(input = {}) {
    if (!this._hasAssessmentReviewScope(input.scopes)) {
      return { ok: false, reason: "assessment_review_scope_required" };
    }
    if (!this._validAssessmentId(input.id) || !this._validAssessmentId(input.assessment_id) ||
        typeof input.author !== "string" || !/^[A-Za-z0-9][A-Za-z0-9:@._-]{0,127}$/.test(input.author) ||
        !isRecord(input.override)) {
      return { ok: false, reason: "validation_error" };
    }
    const parent = this._one("SELECT expires_at FROM assessment_audit_records WHERE id=?",
      input.assessment_id);
    if (!parent || parent.expires_at <= this.now()) {
      if (parent) this._deleteAssessmentAudits([input.assessment_id]);
      return { ok: false, reason: "not_found" };
    }
    const secrets = new Set();
    collectCredentialStrings(input, secrets);
    if ([...secrets].some((secret) => secret.length < 4)) {
      return { ok: false, reason: "credential_material_invalid" };
    }
    const safeOverride = sanitizedPayload(input.override, secrets);
    if (!isRecord(safeOverride) || Object.keys(safeOverride).length === 0) {
      return { ok: false, reason: "validation_error" };
    }
    const overrideJson = this._canonical(safeOverride);
    const existing = this._one("SELECT * FROM assessment_audit_overrides WHERE id=?",input.id);
    if (existing) {
      if (existing.assessment_id !== input.assessment_id || existing.author !== input.author ||
          existing.override_json !== overrideJson) return { ok:false,reason:"id_conflict" };
      return { ok:true,id:existing.id,assessment_id:existing.assessment_id,
        author:existing.author,created_at:existing.created_at,replay:true };
    }
    const now = this.now();
    this.sql.exec(`INSERT INTO assessment_audit_overrides
      (id,assessment_id,schema_version,author,override_json,created_at)
      VALUES (?,?,?,?,?,?)`,input.id,input.assessment_id,ASSESSMENT_OVERRIDE_SCHEMA_VERSION,
      input.author,overrideJson,now);
    return { ok:true,id:input.id,assessment_id:input.assessment_id,author:input.author,
      created_at:now };
  }

  _deleteAssessmentAudits(ids) {
    if (!ids.length) return;
    this.transactionSync(() => {
      for (const id of ids) {
        this.sql.exec("DELETE FROM assessment_audit_overrides WHERE assessment_id=?",id);
        this.sql.exec("DELETE FROM assessment_audit_records WHERE id=?",id);
      }
    });
  }

  expireAssessmentAudits() {
    const cutoff = this.now();
    const deleted = Number(this._one(
      "SELECT COUNT(*) AS n FROM assessment_audit_records WHERE expires_at<=?", cutoff
    )?.n || 0);
    if (deleted) {
      this.transactionSync(() => {
        this.sql.exec(`DELETE FROM assessment_audit_overrides WHERE assessment_id IN
          (SELECT id FROM assessment_audit_records WHERE expires_at<=?)`, cutoff);
        this.sql.exec("DELETE FROM assessment_audit_records WHERE expires_at<=?", cutoff);
      });
    }
    return { ok: true, deleted };
  }

  nextAssessmentAuditExpiry() {
    const row = this._one("SELECT MIN(expires_at) AS expires_at FROM assessment_audit_records");
    return Number.isFinite(row?.expires_at) ? row.expires_at : null;
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
