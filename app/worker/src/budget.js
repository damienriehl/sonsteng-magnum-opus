// budget.js — BudgetCounter, the single global SQLite Durable Object. All logic
// lives in budget-core.js (node-testable behind a SQL adapter); this class is
// the thin DO shell: schema init once via blockConcurrencyWhile, then every RPC
// method delegates to the core synchronously (the DO's single-threaded model is
// the atomicity guarantee — no await between a core method's reads and writes).
//
// One global instance, routed by constant name: env.BUDGET.getByName("global-v1").
// A single daily cap requires a single authority; state persists to SQLite so a
// reschedule loses nothing. See budget-core.js for the accounting model (incl.
// the BYOK skipBudget path: money skipped, every other cap still enforced).

import { DurableObject } from "cloudflare:workers";
import { BudgetCore } from "./budget-core.js";

export class BudgetCounter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new BudgetCore(ctx.storage.sql);
    ctx.blockConcurrencyWhile(async () => {
      this.core.initSchema();
    });
  }

  mint(ipHash, opts) {
    return this.core.mint(ipHash, opts);
  }

  preflight(sid, opts) {
    return this.core.preflight(sid, opts);
  }

  settle(sid, usage, turnId, result) {
    return this.core.settle(sid, usage, turnId, result);
  }

  fail(sid, usage, turnId, personaId) {
    return this.core.fail(sid, usage, turnId, personaId);
  }

  rollback(sid, turnId, personaId) {
    return this.core.rollback(sid, turnId, personaId);
  }

  committedTurnsForPersona(sid, personaId) {
    return this.core.committedTurnsForPersona(sid, personaId);
  }

  charge(pool, usage) {
    return this.core.charge(pool, usage);
  }

  checkPool(pool, capPublicCents, capDemoCents) {
    return this.core.checkPool(pool, capPublicCents, capDemoCents);
  }
}
