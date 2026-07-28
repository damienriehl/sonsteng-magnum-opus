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
export const AUTO_APPLY_KINDS = new Set(["prose", "json_scalar"]);

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
  constructor(sql, now = () => Date.now()) {
    this.sql = sql;
    this.now = now;
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
    if (effOrigin === "human" && !STRUCTURAL_KINDS.has(effKind)) {
      const priors = this._all(
        "SELECT id FROM suggestions WHERE editor=? AND source_ref=? AND status=? AND origin='human' " +
          `AND kind NOT IN (${structuralNames})`,
        editor, source_ref, STATUS.PENDING
      );
      for (const p of priors) this._transition(p.id, STATUS.SUPERSEDED);
      if (directApply) {
        const accs = this._all(
          "SELECT id FROM suggestions WHERE editor=? AND source_ref=? AND status=? AND origin='human' " +
            `AND kind NOT IN (${structuralNames})`,
          editor, source_ref, STATUS.ACCEPTED
        );
        for (const a of accs) this._transition(a.id, STATUS.SUPERSEDED);
      }
    }

    const supersedes = effOrigin === "human" && !STRUCTURAL_KINDS.has(effKind)
      ? (this._one(
          "SELECT id FROM suggestions WHERE editor=? AND source_ref=? AND status=? ORDER BY updated_at DESC LIMIT 1",
          editor, source_ref, STATUS.SUPERSEDED
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
