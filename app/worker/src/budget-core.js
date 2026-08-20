// budget-core.js — ALL BudgetCounter logic, extracted behind a minimal SQL
// interface so it runs both inside the Durable Object (ctx.storage.sql) and
// under node:sqlite in unit tests. budget.js is the thin DO wrapper.
//
// The sql adapter contract: sql.exec(query, ...binds) -> { toArray(): rows }.
// Multi-statement schema strings are executed with no binds.
//
// Accounting model (bounds overshoot to ~one turn):
//   preflight — gates BEFORE the upstream call: turn_id dedupe, turn cap, and
//               (unless opts.skipBudget — the BYOK path, where the user pays
//               with their own key) the pool spend cap + a worst-case reserve.
//   settle    — AFTER the reply: replaces the reserve with the ACTUAL cost
//               (usage=null on BYOK: nothing billed) + stores the replay result.
//   fail      — streamed failure: replaces reserve with any known actual cost,
//               clears replay state, and decrements the turn.
//   rollback  — pre-response failure: fail with no known usage.
// BYOK skips MONEY only: turn caps, dedupe, per-persona counts, mint throttle,
// and the ≥6-turn debrief guard all still apply.

import { centsForUsage } from "./cost.js";

export const SCHEMA_SQL = `
  CREATE TABLE IF NOT EXISTS budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    day TEXT NOT NULL,
    public_cents INTEGER NOT NULL,
    demo_cents INTEGER NOT NULL
  );
  CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    turns INTEGER NOT NULL,
    last_turn_id TEXT,
    last_reserve INTEGER NOT NULL DEFAULT 0,
    last_pool TEXT,
    last_result TEXT
  );
  CREATE TABLE IF NOT EXISTS persona_turns (
    sid TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    day TEXT NOT NULL,
    turns INTEGER NOT NULL,
    PRIMARY KEY (sid, persona_id)
  );
  CREATE TABLE IF NOT EXISTS mints (day TEXT PRIMARY KEY, count INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS ip_mints (
    iphash TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (iphash, day)
  );
  CREATE TABLE IF NOT EXISTS one_shot_reservations (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    pool TEXT NOT NULL,
    reserve_cents INTEGER NOT NULL
  );
`;

export class BudgetCore {
  constructor(sql, now = () => new Date()) {
    this.sql = sql;
    this.now = now;
  }

  initSchema() {
    this.sql.exec(SCHEMA_SQL);
  }

  _today() {
    return this.now().toISOString().slice(0, 10);
  }

  _one(query, ...binds) {
    return this.sql.exec(query, ...binds).toArray()[0];
  }

  _rollover(today) {
    const row = this._one("SELECT day FROM budget WHERE id=1");
    if (!row) {
      this.sql.exec("INSERT INTO budget (id,day,public_cents,demo_cents) VALUES (1,?,0,0)", today);
    } else if (row.day !== today) {
      this.sql.exec("UPDATE budget SET day=?, public_cents=0, demo_cents=0 WHERE id=1", today);
      this.sql.exec("DELETE FROM one_shot_reservations WHERE day<>?", today);
    }
  }

  _poolCol(pool) {
    return pool === "demo" ? "demo_cents" : "public_cents";
  }

  // ---- Session-mint throttling (the real DoS vector) -----------------------
  mint(ipHash, opts) {
    const today = this._today();
    const { maxSessionsPerDay, perIpCeiling, bypass } = opts;

    const m = this._one("SELECT count FROM mints WHERE day=?", today);
    const mintCount = m ? m.count : 0;
    if (!bypass && mintCount >= maxSessionsPerDay) {
      return { ok: false, reason: "rate_limited" };
    }

    if (!bypass) {
      const ipRow = this._one("SELECT count FROM ip_mints WHERE iphash=? AND day=?", ipHash, today);
      const ipCount = ipRow ? ipRow.count : 0;
      if (ipCount >= perIpCeiling) {
        return { ok: false, reason: "rate_limited" };
      }
      this.sql.exec(
        "INSERT INTO ip_mints (iphash,day,count) VALUES (?,?,1) " +
          "ON CONFLICT(iphash,day) DO UPDATE SET count = count + 1",
        ipHash,
        today
      );
    }

    this.sql.exec(
      "INSERT INTO mints (day,count) VALUES (?,1) ON CONFLICT(day) DO UPDATE SET count = count + 1",
      today
    );
    return { ok: true };
  }

  // ---- Chat turn accounting ------------------------------------------------
  // opts: { personaId, pool, capPublicCents, capDemoCents, maxTurns,
  //         reserveCents, turnId, skipBudget }
  preflight(sid, opts) {
    const today = this._today();
    this._rollover(today);
    const {
      personaId, pool, capPublicCents, capDemoCents, maxTurns,
      reserveCents, turnId, skipBudget,
    } = opts;

    const sess = this._one("SELECT * FROM sessions WHERE sid=?", sid);
    const sameDay = sess && sess.day === today;

    // turn_id dedupe. Same turn_id + same day is a retry/double-submit: if the
    // first call already settled, replay its result without re-billing; if it is
    // still in flight (no stored result yet), reject the duplicate rather than
    // increment the turn again — this closes the pre-settle double-count race.
    if (turnId && sameDay && sess.last_turn_id === turnId) {
      if (sess.last_result) return { ok: true, replay: true, result: JSON.parse(sess.last_result) };
      return { ok: false, reason: "duplicate" };
    }

    // Spend gate — hosted pools only. BYOK (skipBudget) pays with its own key.
    if (!skipBudget) {
      const b = this._one("SELECT public_cents,demo_cents FROM budget WHERE id=1");
      const spent = pool === "demo" ? b.demo_cents : b.public_cents;
      const cap = pool === "demo" ? capDemoCents : capPublicCents;
      if (spent >= cap) return { ok: false, reason: "cap_exceeded" };
    }

    const turns = sameDay ? sess.turns : 0;
    if (turns >= maxTurns) return { ok: false, reason: "turn_limit", turn: turns };

    const effectiveReserve = skipBudget ? 0 : reserveCents;
    const newTurns = turns + 1;
    this.sql.exec(
      "INSERT INTO sessions (sid,day,turns,last_turn_id,last_reserve,last_pool,last_result) " +
        "VALUES (?,?,?,?,?,?,NULL) " +
        "ON CONFLICT(sid) DO UPDATE SET " +
        "turns = CASE WHEN day=? THEN turns+1 ELSE 1 END, " +
        "day=?, last_turn_id=?, last_reserve=?, last_pool=?, last_result=NULL",
      sid, today, newTurns, turnId || null, effectiveReserve, pool,
      today, today, turnId || null, effectiveReserve, pool
    );

    this.sql.exec(
      "INSERT INTO persona_turns (sid,persona_id,day,turns) VALUES (?,?,?,1) " +
        "ON CONFLICT(sid,persona_id) DO UPDATE SET turns = CASE WHEN day=? THEN turns+1 ELSE 1 END, day=?",
      sid, personaId, today, today, today
    );

    if (effectiveReserve > 0) {
      const col = this._poolCol(pool);
      this.sql.exec(`UPDATE budget SET ${col} = ${col} + ? WHERE id=1`, effectiveReserve);
    }

    return { ok: true, turn: newTurns, remaining: Math.max(0, maxTurns - newTurns) };
  }

  // Replace this sid's outstanding reserve with the ACTUAL settled cost and store
  // the result for turn_id replay. usage=null (BYOK) bills nothing but still
  // stores the replay result.
  settle(sid, usage, turnId, result) {
    const today = this._today();
    this._rollover(today);
    const actual = usage ? centsForUsage(usage) : 0;
    const sess = this._one("SELECT * FROM sessions WHERE sid=?", sid);
    const reserve = sess && sess.day === today && sess.last_turn_id === turnId ? sess.last_reserve : 0;
    const pool = (sess && sess.last_pool) || "public";
    if (reserve !== 0 || actual !== 0) {
      const col = this._poolCol(pool);
      this.sql.exec(`UPDATE budget SET ${col} = MAX(0, ${col} - ? + ?) WHERE id=1`, reserve, actual);
    }
    this.sql.exec(
      "UPDATE sessions SET last_reserve=0, last_result=? WHERE sid=?",
      JSON.stringify(result), sid
    );
    return { ok: true, actualCents: actual };
  }

  // Finalize a failed in-flight turn. Known usage (for a provider-declared
  // terminal error) is billed, but no replay result is stored and the turn is
  // returned to the caller. Unknown usage (transport abort / pre-response
  // failure) refunds the reserve. Matching last_turn_id makes this idempotent.
  fail(sid, usage, turnId, personaId) {
    const today = this._today();
    this._rollover(today);
    const sess = this._one("SELECT * FROM sessions WHERE sid=?", sid);
    if (!sess || sess.day !== today || sess.last_turn_id !== turnId) return { ok: true };
    const actual = usage ? centsForUsage(usage) : 0;
    const pool = sess.last_pool || "public";
    if (sess.last_reserve !== 0 || actual !== 0) {
      const col = this._poolCol(pool);
      this.sql.exec(
        `UPDATE budget SET ${col} = MAX(0, ${col} - ? + ?) WHERE id=1`,
        sess.last_reserve,
        actual
      );
    }
    this.sql.exec(
      "UPDATE sessions SET turns = MAX(0, turns - 1), last_reserve=0, " +
        "last_turn_id=NULL, last_pool=NULL, last_result=NULL WHERE sid=?",
      sid
    );
    if (personaId) {
      this.sql.exec(
        "UPDATE persona_turns SET turns = MAX(0, turns - 1) " +
          "WHERE sid=? AND persona_id=? AND day=?",
        sid,
        personaId,
        today
      );
    }
    return { ok: true, actualCents: actual };
  }

  // Undo a preflight when no provider usage is available.
  rollback(sid, turnId, personaId) {
    return this.fail(sid, null, turnId, personaId);
  }

  // Debrief-oracle guard: committed turns this sid has with this persona today.
  committedTurnsForPersona(sid, personaId) {
    const today = this._today();
    const row = this._one(
      "SELECT day,turns FROM persona_turns WHERE sid=? AND persona_id=?",
      sid, personaId
    );
    if (!row || row.day !== today) return 0;
    return row.turns;
  }

  // Settle-only spend for the one-shot /critique and /debrief hosted calls.
  charge(pool, usage) {
    const today = this._today();
    this._rollover(today);
    const actual = centsForUsage(usage);
    const col = this._poolCol(pool);
    this.sql.exec(`UPDATE budget SET ${col} = ${col} + ? WHERE id=1`, actual);
    return { ok: true, actualCents: actual };
  }

  // Pool-cap gate for the one-shot hosted endpoints (no turn increment).
  checkPool(pool, capPublicCents, capDemoCents) {
    const today = this._today();
    this._rollover(today);
    const b = this._one("SELECT public_cents,demo_cents FROM budget WHERE id=1");
    const spent = pool === "demo" ? b.demo_cents : b.public_cents;
    const cap = pool === "demo" ? capDemoCents : capPublicCents;
    return { ok: spent < cap };
  }

  reserveOneShot(id, opts) {
    const today = this._today();
    this._rollover(today);
    const { pool, capPublicCents, capDemoCents, reserveCents } = opts;
    if (typeof id !== "string" || !id || !Number.isInteger(reserveCents) || reserveCents < 1) {
      return { ok: false, reason: "validation_error" };
    }
    if (this._one("SELECT id FROM one_shot_reservations WHERE id=?", id)) {
      return { ok: false, reason: "duplicate" };
    }
    const budget = this._one("SELECT public_cents,demo_cents FROM budget WHERE id=1");
    const spent = pool === "demo" ? budget.demo_cents : budget.public_cents;
    const cap = pool === "demo" ? capDemoCents : capPublicCents;
    if (spent + reserveCents > cap) return { ok: false, reason: "cap_exceeded" };
    this.sql.exec(
      "INSERT INTO one_shot_reservations (id,day,pool,reserve_cents) VALUES (?,?,?,?)",
      id, today, pool, reserveCents
    );
    const col = this._poolCol(pool);
    this.sql.exec(`UPDATE budget SET ${col} = ${col} + ? WHERE id=1`, reserveCents);
    return { ok: true };
  }

  settleOneShot(id, usage) {
    const row = this._one("SELECT * FROM one_shot_reservations WHERE id=?", id);
    if (!row) return { ok: true, replay: true };
    const actual = usage ? centsForUsage(usage) : 0;
    const col = this._poolCol(row.pool);
    this.sql.exec(
      `UPDATE budget SET ${col} = MAX(0, ${col} - ? + ?) WHERE id=1`,
      row.reserve_cents, actual
    );
    this.sql.exec("DELETE FROM one_shot_reservations WHERE id=?", id);
    return { ok: true, actualCents: actual };
  }
}
