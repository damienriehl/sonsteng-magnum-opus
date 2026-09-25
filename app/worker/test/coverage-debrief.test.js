import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NodeSql } from './editor-sql-helper.mjs';
import { BudgetCore } from '../src/budget-core.js';
import { completeBudgetedOneShot, generateDebriefScorecard, debriefValidationMessage, debriefInputTokenUpperBound } from '../src/debrief.js';
import { completeWithRetry } from '../src/providers/common.js';
import { parseResponse } from '../src/providers/openai.js';
import { centsForUsage, worstCaseReserveCents } from '../src/cost.js';

function card() {
  return { schema_version: '1.0.0', matter_id: 'm00', persona_id: 'm00.per.test', axis_a: { facts_elicited: [], revealed_if_asked_missed: ['untrusted model topic'], rapport_gated_unearned: [], rule_4_2_flags: [] }, axis_b: Object.fromEntries(['rapport_opening', 'listening_t_funnel', 'understanding_goals', 'explanation_next_steps', 'overall_confidence'].map(key => [key, { score: 0, comment: '' }])), ethics_score: -2, narrative: 'Ask clear questions.', self_reflection_prompt: 'What next?' };
}
const caps = { capPublicCents: 10000, capDemoCents: 10000 };
function budget(t) {
  const sql = new NodeSql(); t.after(() => sql.db.close());
  const core = new BudgetCore(sql, () => new Date('2026-09-19T12:00:00Z')); core.initSchema();
  return core;
}
function options(core, complete, extra = {}) {
  return { budget: core, reservationId: 'test-reservation', pool: 'public', caps, inputTokens: 100, maxTokens: 1200, complete, ...extra };
}

for (const pool of ['public', 'demo']) test(`debrief integrates native provider fetch, real validation and SQLite ${pool} settlement`, async t => {
  const core = budget(t);
  const usage = { prompt_tokens: 100, completion_tokens: 50 };
  const payload = { choices: [{ message: { content: JSON.stringify(card()) }, finish_reason: 'stop' }], usage };
  const result = await generateDebriefScorecard({
    complete: maxTokens => completeBudgetedOneShot(options(core, () => completeWithRetry(() => ({ url: 'data:application/json,' + encodeURIComponent(JSON.stringify(payload)), body: {} }), parseResponse), { maxTokens, pool })),
    persona: { disclosure: { revealed_if_asked: [{ fact_ref: 'm00.fact.001' }] } },
    factMap: { 'm00.fact.001': { topic_label: 'Timeline' } },
  });
  assert.equal(result.ok, true);
  assert.deepEqual(result.attempt, { count: 1, initial_stop_reason: 'stop', outcome: 'completed' });
  assert.deepEqual(result.scorecard.axis_a.revealed_if_asked_missed, ['Timeline']);
  const spent = core._one('SELECT public_cents,demo_cents FROM budget WHERE id=1');
  assert.equal(spent[pool + '_cents'], centsForUsage({ input_tokens: 100, output_tokens: 50 }));
  assert.equal(spent[(pool === 'public' ? 'demo' : 'public') + '_cents'], 0);
  assert.equal(core._one('SELECT COUNT(*) AS n FROM one_shot_reservations').n, 0);
  assert.equal(core._one('SELECT COUNT(*) AS n FROM one_shot_settlements').n, 1);
});

test('budget cap rejects before provider invocation and leaves no reservation', async t => {
  const core = budget(t);
  const result = await completeBudgetedOneShot(options(core, () => assert.fail('provider must not run'), { caps: { capPublicCents: 0, capDemoCents: 0 } }));
  assert.deepEqual(result, { ok: false, kind: 'cap' });
  assert.equal(core._one('SELECT COUNT(*) AS n FROM one_shot_reservations').n, 0);
});

test('invalid reservation id returns upstream without spending or calling provider', async t => {
  const core = budget(t);
  assert.deepEqual(await completeBudgetedOneShot(options(core, () => assert.fail('must not complete'), { reservationId: '' })), { ok: false, kind: 'upstream' });
  assert.equal(core._one('SELECT public_cents FROM budget WHERE id=1').public_cents, 0);
});

test('thrown completion releases the real reservation and returns upstream failure', async t => {
  const core = budget(t);
  const result = await completeBudgetedOneShot(options(core, () => { throw new Error('transport failed'); }));
  assert.deepEqual(result, { ok: false, kind: 'upstream' });
  assert.equal(core._one('SELECT public_cents FROM budget WHERE id=1').public_cents, 0);
  assert.equal(core._one('SELECT COUNT(*) AS n FROM one_shot_reservations').n, 0);
});

test('ambiguous attempts retain at most the reserved provider-attempt allowance', async t => {
  const core = budget(t);
  const completion = { ok: false, kind: 'upstream', ambiguous_attempts: 99 };
  assert.deepEqual(await completeBudgetedOneShot(options(core, () => completion)), completion);
  const expected = Math.max(1, worstCaseReserveCents(100, 1200)) * 2;
  assert.equal(core._one('SELECT public_cents FROM budget WHERE id=1').public_cents, expected);
});

test('terminal rejected settlement fails closed', async () => {
  let settles = 0;
  const core = { reserveOneShot: () => ({ ok: true }), settleOneShot: () => { settles++; return { ok: false, reason: 'validation_error' }; } };
  assert.deepEqual(await completeBudgetedOneShot(options(core, () => ({ ok: true, text: 'paid response' }))), { ok: false, kind: 'upstream' });
  assert.equal(settles, 1);
});

test('repeated lost reservation acknowledgments never invoke provider', async () => {
  let reserves = 0;
  const core = { reserveOneShot: () => { reserves++; throw new Error('RPC lost'); }, settleOneShot: () => assert.fail('no settlement') };
  assert.deepEqual(await completeBudgetedOneShot(options(core, () => assert.fail('no provider'))), { ok: false, kind: 'upstream' });
  assert.equal(reserves, 2);
});

for (const text of ['not JSON', 'null', 'false', '0']) test(`debrief rejects unparseable or empty model value ${text} without retry`, async () => {
  let calls = 0;
  const result = await generateDebriefScorecard({ complete: () => { calls++; return { ok: true, text }; } });
  assert.deepEqual(result, { ok: false, kind: 'validation', subtype: 'unparseable', attempt: { count: 1, outcome: 'invalid' } });
  assert.equal(calls, 1);
});

test('initial thrown provider completion becomes a safe upstream outcome', async () => {
  assert.deepEqual(await generateDebriefScorecard({ complete: () => { throw new Error('private transport detail'); } }), { ok: false, kind: 'upstream', upstreamResult: { ok: false, kind: 'upstream' }, attempt: { count: 1, outcome: 'initial_upstream_failed' } });
});

for (const [text, subtype] of [['not JSON', 'unparseable'], ['{}', 'wrong_shape']]) test(`truncation retry ending in ${subtype} preserves retry diagnostics`, async () => {
  const limits = [];
  const result = await generateDebriefScorecard({ complete: max => { limits.push(max); return limits.length === 1 ? { ok: true, stop_reason: 'max_tokens' } : { ok: true, text }; } });
  assert.equal(result.subtype, subtype);
  assert.deepEqual(result.attempt, { count: 2, initial_stop_reason: 'max_tokens', outcome: 'retry_invalid' });
  assert.deepEqual(limits, [1200, 2400]);
});

test('oracle leak in truncation retry fails closed after real JSON validation', async () => {
  const text = 'A secret transaction transferred a distinctive amount to an undisclosed account.';
  const response = card(); response.narrative = text;
  let calls = 0;
  const result = await generateDebriefScorecard({ complete: () => ++calls === 1 ? { ok: true, stop_reason: 'max_tokens' } : { ok: true, text: JSON.stringify(response) }, persona: { disclosure: { concealed: [{ fact_ref: 'm00.fact.002', text }] } } });
  assert.equal(result.subtype, 'oracle_leak'); assert.equal(result.leakField, 'narrative');
  assert.equal(result.attempt.outcome, 'retry_invalid'); assert.equal(calls, 2);
});

test('token upper bound counts serialized control characters and surrogate escapes', () => {
  const prompt = '\u0000\n"\\\ud800';
  const expected = Buffer.byteLength(JSON.stringify([{ role: 'user', content: prompt }]));
  assert.equal(debriefInputTokenUpperBound(prompt), expected);
  assert.equal(debriefValidationMessage('unknown'), debriefValidationMessage(undefined));
  assert.match(debriefValidationMessage('truncated'), /output limit/);
});

test('second truncation without provider metadata returns no invented usage fields', async () => {
  const result = await generateDebriefScorecard({ complete: () => ({ ok: true, stop_reason: 'max_tokens', text: '{}' }) });
  assert.deepEqual(result, { ok: false, kind: 'validation', subtype: 'truncated', attempt: { count: 2, initial_stop_reason: 'max_tokens', outcome: 'retry_truncated' } });
});
