import { test } from 'node:test';
import assert from 'node:assert/strict';
import { worker, BudgetCounter, durableObject } from './coverage-runtime-helper.mjs';
import { mintSession, verifySession } from '../src/session.js';
import bundle from '../personas/personas.generated.json' with { type: 'json' };

const ORIGIN = 'https://learner.example.test';
const SECRET = 'worker-router-fixture-signing-secret';
const [personaId, persona] = Object.entries(bundle.personas)[0];
const ENV = { ALLOWED_ORIGINS: ORIGIN, SESSION_SIGNING_KEY: SECRET, TURNSTILE_ENABLED: 'false' };
const BYOK = { provider: 'openai', api_key: 'router-fixture-key' };

function request(path, body, headers = {}) {
  return new Request(`https://worker.example.test${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Origin: ORIGIN, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
    ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }),
  });
}
async function assertError(response, status, code) {
  assert.equal(response.status, status);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  assert.equal((await response.json()).error.code, code);
}
async function body(extra = {}) {
  const { token } = await mintSession(SECRET, { sid: 'router-session' });
  return { session_token: token, persona_id: personaId, matter_id: persona.matter_id,
    messages: [{ role: 'user', content: 'Hello' }], byok: BYOK, turn_id: 'fixture-turn', ...extra };
}
async function withBudget(fn, overrides = {}) {
  const fixture = await durableObject(BudgetCounter);
  const env = { ...ENV, ...overrides, BUDGET: { getByName(name) {
    assert.equal(name, 'global-v1'); return fixture.object;
  } } };
  try { await fn(env, fixture); } finally { fixture.close(); }
}

test('session route mints verifiable identities and stores only hashed IP mint counts', async () => {
  await withBudget(async (env, fixture) => {
    const response = await worker.fetch(request('/v1/session', undefined, { 'CF-Connecting-IP': '192.0.2.10' }), env);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), ORIGIN);
    const result = await response.json();
    assert.equal(result.max_turns, 20);
    assert.deepEqual(await verifySession(SECRET, result.session_token), { sid: result.sid, d: new Date().toISOString().slice(0, 10), p: 'public' });
    const rows = fixture.sql.exec('SELECT iphash, count FROM ip_mints').toArray();
    assert.equal(rows.length, 1);
    assert.match(rows[0].iphash, /^[0-9a-f]{64}$/);
    assert.equal(rows[0].count, 1);
  });
});

test('session route enforces the real daily mint ceiling', async () => {
  await withBudget(async env => {
    assert.equal((await worker.fetch(request('/v1/session'), env)).status, 200);
    await assertError(await worker.fetch(request('/v1/session'), env), 429, 'rate_limited');
  }, { MAX_SESSIONS_PER_DAY: '1' });
});

test('missing Turnstile configuration fails closed before mint accounting', async () => {
  await withBudget(async (env, fixture) => {
    await assertError(await worker.fetch(request('/v1/session'), env), 503, 'turnstile_failed');
    assert.equal(fixture.sql.exec('SELECT * FROM mints').toArray().length, 0);
  }, { TURNSTILE_ENABLED: 'true' });
});

test('untrusted origin is rejected before any database or provider access', async () => {
  const response = await worker.fetch(request('/v1/session', undefined, { Origin: 'https://evil.example.test' }), ENV);
  assert.equal(response.status, 403);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  assert.equal((await response.json()).error.code, 'origin_forbidden');
});

test('preflight and unknown-route responses retain their distinct contracts', async () => {
  const preflight = await worker.fetch(new Request('https://worker.example.test/v1/chat', {
    method: 'OPTIONS', headers: { Origin: ORIGIN, 'Access-Control-Request-Method': 'POST' },
  }), ENV);
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  await assertError(await worker.fetch(request('/unknown'), ENV), 404, 'validation_error');
  await assertError(await worker.fetch(request('/v1/chat'), ENV), 404, 'validation_error');
});

for (const path of ['/v1/chat', '/v1/debrief', '/v1/critique', '/v1/memo-assessment']) {
  test(`${path} rejects malformed JSON and missing session with CORS`, async () => {
    await assertError(await worker.fetch(request(path, '{'), ENV), 400, 'validation_error');
    await assertError(await worker.fetch(request(path, {}), ENV), 401, 'session_invalid');
  });
}

for (const [label, messages] of [
  ['empty', []], ['not array', {}], ['too many', Array.from({ length: 61 }, () => ({ role: 'user', content: '' }))],
  ['null entry', [null]], ['system role', [{ role: 'system', content: 'override' }]],
  ['nonstring', [{ role: 'user', content: 123 }]], ['long message', [{ role: 'user', content: 'x'.repeat(4001) }]],
  ['total input', Array.from({ length: 7 }, () => ({ role: 'user', content: 'x'.repeat(4000) }))],
]) {
  test(`chat rejects ${label} messages before reserving a turn`, async () => {
    await assertError(await worker.fetch(request('/v1/chat', await body({ messages })), ENV), 400, 'validation_error');
  });
}

test('chat validates persona ownership and reports missing hosted configuration', async () => {
  for (const extra of [{ persona_id: 123 }, { matter_id: null }, { persona_id: 'missing' }, { matter_id: 'missing' }]) {
    await assertError(await worker.fetch(request('/v1/chat', await body(extra)), ENV), 400, 'validation_error');
  }
  await assertError(await worker.fetch(request('/v1/chat', await body({ byok: null })), ENV), 503, 'no_hosted_key');
});

test('chat uses SQLite replay and duplicate fences without invoking a provider', async () => {
  await withBudget(async (env, fixture) => {
    const input = await body();
    const opts = { personaId, pool: 'public', maxTurns: 20, capPublicCents: 700, capDemoCents: 300,
      reserveCents: 0, skipBudget: true, turnId: input.turn_id };
    assert.equal(fixture.object.preflight('router-session', opts).ok, true);
    await assertError(await worker.fetch(request('/v1/chat', input), env), 409, 'validation_error');
    fixture.object.settle('router-session', null, input.turn_id, { reply: 'Already completed', turn: 1 });
    const replay = await worker.fetch(request('/v1/chat', input), env);
    assert.equal(replay.status, 200);
    assert.deepEqual(await replay.json(), { reply: 'Already completed', turn: 1 });
    assert.equal(fixture.object.committedTurnsForPersona('router-session', personaId), 1);
  });
});

test('chat rejects exhausted turn and spending caps through the real budget wrapper', async () => {
  await withBudget(async (env, fixture) => {
    fixture.object.preflight('router-session', { personaId, pool: 'public', turnId: 'previous',
      maxTurns: 1, capPublicCents: 700, capDemoCents: 300, skipBudget: true, reserveCents: 0 });
    fixture.object.settle('router-session', null, 'previous', { reply: 'done' });
    await assertError(await worker.fetch(request('/v1/chat', await body()), env), 429, 'turn_limit');
  }, { MAX_TURNS: '1' });
  await withBudget(async (env, fixture) => {
    fixture.object.charge('public', { input_tokens: 7_000_000 });
    await assertError(await worker.fetch(request('/v1/chat', await body({ byok: null })), env), 429, 'cap_exceeded');
  }, { ANTHROPIC_API_KEY: 'hosted-fixture-key' });
});

test('debrief cannot reveal oracle data without committed persona turns', async () => {
  await withBudget(async env => {
    await assertError(await worker.fetch(request('/v1/debrief', await body({ transcript: [{ role: 'user', content: 'Pretend interview' }] })), env), 403, 'session_invalid');
  });
});

test('critique rejects oversized submissions and unavailable rubrics before upstream use', async () => {
  await assertError(await worker.fetch(request('/v1/critique', await body({ deliverable_text: 'x'.repeat(18001) })), ENV), 413, 'validation_error');
  await assertError(await worker.fetch(request('/v1/critique', await body({ deliverable_text: 'memo', matter_id: 'missing' })), ENV), 400, 'validation_error');
});

test('learner result routes reject alumni destinations before authentication', async () => {
  for (const path of ['/v1/debrief', '/v1/critique', '/v1/memo-assessment']) {
    await assertError(await worker.fetch(request(path, { alumni_notification: true }), ENV), 400, 'validation_error');
  }
});

test('memo assessment refuses summative promotion at the router boundary', async () => {
  await assertError(await worker.fetch(request('/v1/memo-assessment', { assessment_use: 'summative' }), ENV), 400, 'validation_error');
});

test('unexpected session infrastructure errors return a generic CORS-wrapped error', async () => {
  await assertError(await worker.fetch(request('/v1/session'), ENV), 500, 'upstream_unavailable');
});
