import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mintSession, verifySession, utcDay } from '../src/session.js';
import { BudgetCore } from '../src/budget-core.js';
import { NodeSql } from './editor-sql-helper.mjs';

const SECRET = 'coverage-session-fixture-only';

// Independently sign arbitrary wire payloads to test the verifier after HMAC,
// rather than only feeding it payloads accepted by the production mint helper.
async function signedPayload(text) {
  const payload = Buffer.from(text).toString('base64url');
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(SECRET),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(payload));
  return `${payload}.${Buffer.from(signature).toString('base64url')}`;
}

test('session verifier rejects nonstrings and invalid base64 signatures', async () => {
  for (const value of [null, undefined, 1, {}, [], 'payload.%', 'payload.💥']) {
    assert.equal(await verifySession(SECRET, value), null);
  }
});

test('authenticated malformed JSON and invalid claim types are rejected', async () => {
  for (const text of ['{', 'null', '[]', '{}', '{"sid":1,"d":"2026-01-01"}',
    '{"sid":"s","d":1}']) {
    assert.equal(await verifySession(SECRET, await signedPayload(text)), null, text);
  }
});

test('legacy signed sessions and unknown pools default to public', async () => {
  for (const p of [undefined, null, 'admin', 'DEMO', 1]) {
    const token = await signedPayload(JSON.stringify({ sid: 'legacy', d: '2026-01-01', p }));
    assert.deepEqual(await verifySession(SECRET, token), { sid: 'legacy', d: '2026-01-01', p: 'public' });
  }
});

test('mint preserves Unicode identity and only permits the explicit demo pool', async () => {
  const { token, sid } = await mintSession(SECRET, { sid: '学習者-é', day: '2026-01-01', pool: 'admin' });
  assert.equal(sid, '学習者-é');
  assert.match(token, /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
  assert.deepEqual(await verifySession(SECRET, token), { sid, d: '2026-01-01', p: 'public' });
});

test('UTC day follows the UTC date across local midnight boundaries', () => {
  assert.equal(utcDay(new Date('2026-01-01T23:59:59-06:00')), '2026-01-02');
  assert.equal(utcDay(new Date('2026-01-01T00:00:00+09:00')), '2025-12-31');
});

test('verified session identity and pool drive real SQLite reservation, settlement and replay', async () => {
  const sql = new NodeSql();
  try {
    const core = new BudgetCore(sql, () => new Date('2026-01-01T12:00:00Z'));
    core.initSchema();
    const { token } = await mintSession(SECRET, { sid: 'budget-session', pool: 'demo', day: '2026-01-01' });
    const session = await verifySession(SECRET, token);
    const options = { pool: session.p, personaId: 'fixture.persona', turnId: 'turn-one',
      maxTurns: 1, reserveCents: 5, capPublicCents: 0, capDemoCents: 10, skipBudget: false };
    assert.equal(core.preflight(session.sid, options).ok, true);
    core.settle(session.sid, { input_tokens: 10_000 }, options.turnId, { reply: 'stored reply' });
    assert.deepEqual(sql.exec('SELECT public_cents, demo_cents FROM budget').toArray().map(r => ({ ...r })),
      [{ public_cents: 0, demo_cents: 1 }]);
    const replay = core.preflight(session.sid, options);
    assert.equal(replay.replay, true);
    assert.deepEqual(replay.result, { reply: 'stored reply' });
    assert.equal(core.preflight(session.sid, { ...options, turnId: 'turn-two' }).reason, 'turn_limit');
  } finally {
    sql.db.close();
  }
});
