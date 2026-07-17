// BudgetCore DO logic under real SQLite (node:sqlite): BYOK skips spend but NOT
// turn caps; hosted path reserves/settles; turn_id dedupe; mint throttling.
import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";

import { BudgetCore } from "../src/budget-core.js";

// Adapter matching the DO's ctx.storage.sql surface for the queries BudgetCore
// issues: exec(query, ...binds) -> { toArray() }.
class NodeSql {
  constructor() {
    this.db = new DatabaseSync(":memory:");
  }
  exec(query, ...binds) {
    if (binds.length === 0 && query.includes(";")) {
      this.db.exec(query); // multi-statement schema
      return { toArray: () => [] };
    }
    const st = this.db.prepare(query);
    if (/^\s*select/i.test(query)) {
      return { toArray: () => st.all(...binds) };
    }
    st.run(...binds);
    return { toArray: () => [] };
  }
}

function makeCore() {
  const core = new BudgetCore(new NodeSql());
  core.initSchema();
  return core;
}

function spent(core, pool = "public") {
  const col = pool === "demo" ? "demo_cents" : "public_cents";
  const row = core._one(`SELECT ${col} AS c FROM budget WHERE id=1`);
  return row ? row.c : 0;
}

const BASE = {
  personaId: "m00.per.tester",
  pool: "public",
  capPublicCents: 700,
  capDemoCents: 300,
  maxTurns: 3,
  reserveCents: 5,
};

test("hosted preflight reserves worst-case cents; settle replaces with actual", () => {
  const core = makeCore();
  const pre = core.preflight("sid-1", { ...BASE, turnId: "t1", skipBudget: false });
  assert.ok(pre.ok);
  assert.equal(pre.turn, 1);
  assert.equal(spent(core), 5); // reserve added
  core.settle("sid-1", { input_tokens: 20_000, output_tokens: 300 }, "t1", { reply: "hi" });
  // actual = ceil((20000*100 + 300*500)/1e6) = ceil(2.15) = 3 cents
  assert.equal(spent(core), 3); // reserve 5 removed, actual 3 recorded
});

test("BYOK skipBudget: no spend gate, no reserve — but turn cap still bites", () => {
  const core = makeCore();
  // Exhaust the public pool completely.
  core.charge("public", { input_tokens: 7_000_000 }); // 700 cents
  assert.ok(spent(core) >= 700);

  // Hosted request is now blocked...
  const hosted = core.preflight("sid-h", { ...BASE, turnId: "h1", skipBudget: false });
  assert.equal(hosted.ok, false);
  assert.equal(hosted.reason, "cap_exceeded");

  // ...but BYOK sails through the money gate.
  for (let i = 1; i <= 3; i++) {
    const pre = core.preflight("sid-b", { ...BASE, turnId: "b" + i, skipBudget: true });
    assert.ok(pre.ok, "byok turn " + i);
    assert.equal(pre.turn, i);
    core.settle("sid-b", null, "b" + i, { reply: "r" + i }); // usage=null: bill nothing
  }
  // Pool unchanged by three BYOK turns.
  assert.equal(spent(core), 700);

  // Turn cap (maxTurns=3) still enforced for BYOK.
  const over = core.preflight("sid-b", { ...BASE, turnId: "b4", skipBudget: true });
  assert.equal(over.ok, false);
  assert.equal(over.reason, "turn_limit");
});

test("turn_id dedupe: settled turn replays without re-billing; in-flight duplicate rejected", () => {
  const core = makeCore();
  const pre = core.preflight("sid-d", { ...BASE, turnId: "dup", skipBudget: false });
  assert.ok(pre.ok);
  // Duplicate while in flight (not yet settled) -> rejected, turn NOT incremented.
  const inflight = core.preflight("sid-d", { ...BASE, turnId: "dup", skipBudget: false });
  assert.equal(inflight.ok, false);
  assert.equal(inflight.reason, "duplicate");

  core.settle("sid-d", { input_tokens: 100, output_tokens: 10 }, "dup", { reply: "cached" });
  const before = spent(core);
  const replay = core.preflight("sid-d", { ...BASE, turnId: "dup", skipBudget: false });
  assert.ok(replay.ok);
  assert.equal(replay.replay, true);
  assert.deepEqual(replay.result, { reply: "cached" });
  assert.equal(spent(core), before); // no re-billing
  // And the turn counter did not advance on the replay.
  const next = core.preflight("sid-d", { ...BASE, turnId: "next", skipBudget: false });
  assert.equal(next.turn, 2);
});

test("rollback: upstream failure burns no turn and refunds the reserve", () => {
  const core = makeCore();
  core.preflight("sid-r", { ...BASE, turnId: "r1", skipBudget: false });
  assert.equal(spent(core), 5);
  core.rollback("sid-r", "r1");
  assert.equal(spent(core), 0);
  const pre = core.preflight("sid-r", { ...BASE, turnId: "r2", skipBudget: false });
  assert.equal(pre.turn, 1); // the failed turn was not burned
});

test("debrief-oracle guard counts committed per-persona turns", () => {
  const core = makeCore();
  for (let i = 1; i <= 6; i++) {
    core.preflight("sid-g", { ...BASE, maxTurns: 20, turnId: "g" + i, skipBudget: true });
  }
  assert.equal(core.committedTurnsForPersona("sid-g", "m00.per.tester"), 6);
  assert.equal(core.committedTurnsForPersona("sid-g", "m00.per.other"), 0);
  assert.equal(core.committedTurnsForPersona("sid-unknown", "m00.per.tester"), 0);
});

test("mint throttling: per-IP ceiling and global cap; bypass skips the IP brake", () => {
  const core = makeCore();
  const opts = { maxSessionsPerDay: 100, perIpCeiling: 2, bypass: false };
  assert.ok(core.mint("ip-a", opts).ok);
  assert.ok(core.mint("ip-a", opts).ok);
  const third = core.mint("ip-a", opts);
  assert.equal(third.ok, false);
  assert.equal(third.reason, "rate_limited");
  // Bypass (demo) skips the per-IP ceiling.
  assert.ok(core.mint("ip-a", { ...opts, bypass: true }).ok);
  // Global cap.
  const tight = makeCore();
  assert.ok(tight.mint("ip-1", { maxSessionsPerDay: 1, perIpCeiling: 10, bypass: false }).ok);
  assert.equal(tight.mint("ip-2", { maxSessionsPerDay: 1, perIpCeiling: 10, bypass: false }).ok, false);
});

test("checkPool gates hosted one-shots by pool", () => {
  const core = makeCore();
  assert.ok(core.checkPool("public", 700, 300).ok);
  core.charge("public", { input_tokens: 7_000_000 });
  assert.equal(core.checkPool("public", 700, 300).ok, false);
  assert.ok(core.checkPool("demo", 700, 300).ok); // demo pool untouched
});
