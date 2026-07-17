// budget.js — BudgetCounter, the single global SQLite Durable Object that is the
// one authority for the $10/day spend cap, per-session turn counts, session-mint
// throttling, turn_id idempotency, and the debrief ≥6-turn oracle guard.
//
// Why one global instance: a single daily cap REQUIRES a single authority — that
// is exactly what a DO is for. Routed with a constant name (env.BUDGET.getByName
// ("global-v1")). At demo scale (~single-digit req/s) it is nowhere near a DO's
// throughput ceiling, and state persists to SQLite so a reschedule loses nothing.
//
// Atomicity comes from the DO's single-threaded model: every read-modify-write is
// done inside ONE method with synchronous sql.exec calls and NO await between the
// read and the write. blockConcurrencyWhile is used ONLY for one-time schema init.
//
// Accounting model (bounds overshoot to ~one turn):
//   preflight  — gates BEFORE the Anthropic call: dedupes on turn_id, checks the
//                pool cap + turn cap, then increments the turn and RESERVES the
//                worst-case cents into the pool.
//   settle     — AFTER the reply: replaces the reserve with the ACTUAL cost
//                (incl. cache token types) and stores the result for turn_id replay.
//   rollback   — on Anthropic failure: removes the reserve and decrements the turn
//                so NO turn is burned and NO spend is recorded.

import { DurableObject } from "cloudflare:workers";
import { centsForUsage } from "./cost.js";

export class BudgetCounter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    ctx.blockConcurrencyWhile(async () => {
      this.sql.exec(`
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
      `);
    });
  }

  _today() {
    return new Date().toISOString().slice(0, 10);
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
    }
  }

  _poolCol(pool) {
    return pool === "demo" ? "demo_cents" : "public_cents";
  }

  // ---- Session-mint throttling (the real DoS vector) -----------------------
  // Free unlimited minting would drain the $7 pool by script. Cap global issues/day
  // (maxSessionsPerDay) + a per-IP issuance ceiling (perIpCeiling). A valid demo
  // bypass skips the per-IP ceiling (John & Roger) but is still recorded.
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
  // opts: { personaId, pool, capPublicCents, capDemoCents, maxTurns, reserveCents, turnId }
  preflight(sid, opts) {
    const today = this._today();
    this._rollover(today);
    const { personaId, pool, capPublicCents, capDemoCents, maxTurns, reserveCents, turnId } = opts;

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

    const b = this._one("SELECT public_cents,demo_cents FROM budget WHERE id=1");
    const spent = pool === "demo" ? b.demo_cents : b.public_cents;
    const cap = pool === "demo" ? capDemoCents : capPublicCents;
    if (spent >= cap) return { ok: false, reason: "cap_exceeded" };

    const turns = sameDay ? sess.turns : 0;
    if (turns >= maxTurns) return { ok: false, reason: "turn_limit", turn: turns };

    const newTurns = turns + 1;
    this.sql.exec(
      "INSERT INTO sessions (sid,day,turns,last_turn_id,last_reserve,last_pool,last_result) " +
        "VALUES (?,?,?,?,?,?,NULL) " +
        "ON CONFLICT(sid) DO UPDATE SET " +
        "turns = CASE WHEN day=? THEN turns+1 ELSE 1 END, " +
        "day=?, last_turn_id=?, last_reserve=?, last_pool=?, last_result=NULL",
      sid, today, newTurns, turnId || null, reserveCents, pool,
      today, today, turnId || null, reserveCents, pool
    );

    this.sql.exec(
      "INSERT INTO persona_turns (sid,persona_id,day,turns) VALUES (?,?,?,1) " +
        "ON CONFLICT(sid,persona_id) DO UPDATE SET turns = CASE WHEN day=? THEN turns+1 ELSE 1 END, day=?",
      sid, personaId, today, today, today
    );

    const col = this._poolCol(pool);
    this.sql.exec(`UPDATE budget SET ${col} = ${col} + ? WHERE id=1`, reserveCents);

    return { ok: true, turn: newTurns, remaining: Math.max(0, maxTurns - newTurns) };
  }

  // Replace this sid's outstanding reserve with the ACTUAL settled cost, and store
  // the result for turn_id replay. usage carries the cache token types.
  settle(sid, usage, turnId, result) {
    const today = this._today();
    this._rollover(today);
    const actual = centsForUsage(usage);
    const sess = this._one("SELECT * FROM sessions WHERE sid=?", sid);
    const reserve = sess && sess.day === today && sess.last_turn_id === turnId ? sess.last_reserve : 0;
    const pool = (sess && sess.last_pool) || "public";
    const col = this._poolCol(pool);
    this.sql.exec(
      `UPDATE budget SET ${col} = MAX(0, ${col} - ? + ?) WHERE id=1`,
      reserve, actual
    );
    this.sql.exec(
      "UPDATE sessions SET last_reserve=0, last_result=? WHERE sid=?",
      JSON.stringify(result), sid
    );
    return { ok: true, actualCents: actual };
  }

  // Undo the last preflight for this sid on an upstream failure: remove the reserve
  // and decrement the turn counters. No turn burned, no spend recorded.
  rollback(sid, turnId) {
    const today = this._today();
    this._rollover(today);
    const sess = this._one("SELECT * FROM sessions WHERE sid=?", sid);
    if (!sess || sess.day !== today || sess.last_turn_id !== turnId) return { ok: true };
    const pool = sess.last_pool || "public";
    const col = this._poolCol(pool);
    this.sql.exec(`UPDATE budget SET ${col} = MAX(0, ${col} - ?) WHERE id=1`, sess.last_reserve);
    this.sql.exec(
      "UPDATE sessions SET turns = MAX(0, turns - 1), last_reserve=0, last_turn_id=NULL WHERE sid=?",
      sid
    );
    return { ok: true };
  }

  // Debrief-oracle guard: how many committed turns this sid has with this persona.
  committedTurnsForPersona(sid, personaId) {
    const today = this._today();
    const row = this._one(
      "SELECT day,turns FROM persona_turns WHERE sid=? AND persona_id=?",
      sid, personaId
    );
    if (!row || row.day !== today) return 0;
    return row.turns;
  }

  // A settle-only spend increment for the one-shot /critique and /debrief calls,
  // which have no reserve to reconcile. Charges the pool the actual cost.
  charge(pool, usage) {
    const today = this._today();
    this._rollover(today);
    const actual = centsForUsage(usage);
    const col = this._poolCol(pool);
    this.sql.exec(`UPDATE budget SET ${col} = ${col} + ? WHERE id=1`, actual);
    return { ok: true, actualCents: actual };
  }

  // Pool-cap gate for the one-shot endpoints (no turn increment).
  checkPool(pool, capPublicCents, capDemoCents) {
    const today = this._today();
    this._rollover(today);
    const b = this._one("SELECT public_cents,demo_cents FROM budget WHERE id=1");
    const spent = pool === "demo" ? b.demo_cents : b.public_cents;
    const cap = pool === "demo" ? capDemoCents : capPublicCents;
    return { ok: spent < cap };
  }
}
