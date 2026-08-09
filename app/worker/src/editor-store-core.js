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
  CREATE TABLE IF NOT EXISTS production_release_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, release_id TEXT NOT NULL, type TEXT NOT NULL,
    actor TEXT NOT NULL, detail_json TEXT NOT NULL, created_at INTEGER NOT NULL
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
    this._ensureColumn("suggestions", "production_release_id", "TEXT");
    this._ensureColumn("suggestions", "production_published_at", "INTEGER");
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
  finalize(batchId, { phase, applied, accepted_blocked, needs_human, drift, base_sha,
    commit_sha, generator_id } = {}) {
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
    return { ok: true };
  }

  prepareProductionRelease(input = {}) {
    const required = ["id", "idempotency_key", "request_digest", "actor", "base_sha",
      "candidate_sha", "generator_id", "evidence_hash", "manifest_hash", "target_batch_id"];
    if (required.some((k) => typeof input[k] !== "string" || !input[k]))
      return { ok:false, reason:"validation_error" };
    if (input.target_environment !== "production") return { ok:false, reason:"wrong_target" };
    if (input.credential_channel !== "bearer") return { ok:false, reason:"service_bearer_required" };
    if (input.ancestry_verified !== true) return { ok:false, reason:"nonancestor_candidate" };
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
      batch.batch_id, STATUS.APPLIED));
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
      if (!rows.length) return { ok:false, reason:"stale_member" };
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
    for (const key of ["target_batch_id","base_sha","candidate_sha","generator_id","evidence_hash","manifest_hash","membership_hash"]) {
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
    const events = this._all(
      "SELECT type,actor,detail_json,created_at FROM production_release_events WHERE release_id=? ORDER BY id", id)
      .map((event) => ({ type:event.type, actor:event.actor,
        detail:JSON.parse(event.detail_json), created_at:event.created_at }));
    return { ...row, batches, suggestion_ids, events };
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

  renewProductionReleaseLease(input = {}) {
    const release = this._one("SELECT * FROM production_releases WHERE id=?", input.id || "");
    if (!release) return { ok:false, reason:"not_found" };
    if (input.credential_channel !== "bearer" || !input.actor)
      return { ok:false, reason:"service_bearer_required" };
    if (!input.fencing_token || input.fencing_token !== release.fencing_token)
      return { ok:false, reason:"stale_fence" };
    if (!['executing','pages_deployed','worker_deployed','verified'].includes(release.state))
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
    if (['executing','pages_deployed','worker_deployed','verified'].includes(release.state) &&
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
      failed_fenced:new Set(["restoring"]), restoring:new Set(["restored","failed_fenced"]),
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
      const members = this._all("SELECT suggestion_id FROM production_release_members WHERE release_id=?", release.id);
      for (const member of members) this.sql.exec(
        "UPDATE suggestions SET production_release_id=?,production_published_at=? WHERE id=? AND status=? AND (production_release_id IS NULL OR production_release_id=?)",
        release.id,now,member.suggestion_id,STATUS.APPLIED,release.id);
    }
    });
    return { ok:true, release:this.getProductionRelease(release.id) };
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
    const batches = visible.map((batch) => ({ ...batch, changes: this._all(
      "SELECT id,editor,origin,kind,page,source_ref,original_text,new_text,comment,group_id,status,apply_batch_id,created_at,updated_at FROM suggestions WHERE apply_batch_id=? AND status=? ORDER BY id",
      batch.batch_id, STATUS.APPLIED) }));
    return { release, batches };
  }

  publisherSummary() {
    const frontier = this._one(
      "SELECT b.created_at,b.batch_id FROM production_releases r JOIN apply_batches b ON b.batch_id=r.target_batch_id WHERE r.state IN ('verified','complete') ORDER BY r.updated_at DESC,r.id DESC LIMIT 1");
    const row = frontier ? this._one(
      "SELECT COUNT(*) AS count FROM suggestions s JOIN apply_batches b ON b.batch_id=s.apply_batch_id WHERE s.status=? AND b.phase='done' AND (b.created_at>? OR (b.created_at=? AND b.batch_id>?))",
      STATUS.APPLIED,frontier.created_at,frontier.created_at,frontier.batch_id) : this._one(
      "SELECT COUNT(*) AS count FROM suggestions s JOIN apply_batches b ON b.batch_id=s.apply_batch_id WHERE s.status=? AND b.phase='done'",
      STATUS.APPLIED);
    return { eligible:Number(row?.count || 0) };
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
      batch.batch_id, STATUS.APPLIED));
    let start = 0;
    if (frontier) {
      const at = done.findIndex((batch) => batch.batch_id === frontier.target_batch_id);
      start = at < 0 ? done.length : at + 1;
    }
    const batches = done.slice(start).map((batch) => ({ ...batch,
      suggestion_ids:this._all(
        "SELECT id FROM suggestions WHERE apply_batch_id=? AND status=? ORDER BY id",
        batch.batch_id, STATUS.APPLIED).map((row) => row.id) }));
    return { active_release:null, base_sha:frontier?.candidate_sha || null, batches };
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
