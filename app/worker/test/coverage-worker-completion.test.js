import { test } from 'node:test';
import assert from 'node:assert/strict';
import { worker, BudgetCounter, EditorStore, durableObject } from './coverage-runtime-helper.mjs';
import { mintSession } from '../src/session.js';
import { centsForUsage } from '../src/cost.js';
import bundle from '../personas/personas.generated.json' with { type: 'json' };

const ORIGIN = 'https://completion.example.test';
const SECRET = 'completion-test-session-secret';
const SID = 'completion-fixture-session';
const [personaId, persona] = Object.entries(bundle.personas)[0];
const byok = { provider: 'openai', api_key: 'completion-transport-fixture' };
const usage = { input_tokens: 100, output_tokens: 25, cache_read_input_tokens: 50 };
function upstream(text, hosted = false, stop = 'stop') {
  return Response.json(hosted ? { content: [{ type: 'text', text }], usage, stop_reason: stop === 'stop' ? 'end_turn' : stop }
    : { choices: [{ message: { content: text }, finish_reason: stop }], usage: { prompt_tokens: 150, completion_tokens: 25, prompt_tokens_details: { cached_tokens: 50 } } });
}
async function setup(t, overrides = {}) {
  const budget = await durableObject(BudgetCounter);
  t.after(() => budget.close());
  const { token } = await mintSession(SECRET, { sid: SID });
  // Durable Object RPC returns promises; proxy only that platform boundary,
  // leaving the actual wrapper, core, transactions and SQLite state intact.
  const env = { ALLOWED_ORIGINS: ORIGIN, SESSION_SIGNING_KEY: SECRET, BUDGET: { getByName: () => new Proxy(budget.object, { get(target, key) { const value = target[key]; return typeof value === 'function' ? async (...args) => value.apply(target, args) : value; } }) }, ...overrides };
  const input = { session_token: token, persona_id: personaId, matter_id: persona.matter_id, messages: [{ role: 'user', content: 'Tell me what happened.', injected: 'discard' }], byok, turn_id: 'completion-turn' };
  return { budget, env, input, async call(path = '/v1/chat', extra = {}) {
    return worker.fetch(new Request(`https://worker.example.test${path}`, { method: 'POST', headers: { Origin: ORIGIN, 'Content-Type': 'application/json' }, body: JSON.stringify({ ...input, ...extra }) }), env);
  } };
}
function spent(budget) { return budget.sql.exec('SELECT public_cents FROM budget WHERE id=1').toArray()[0]?.public_cents || 0; }
function seedTurns(budget, n) {
  for (let i = 0; i < n; i++) {
    const id = `seed-${i}`;
    assert.equal(budget.object.preflight(SID, { personaId, pool: 'public', maxTurns: 30, reserveCents: 0, skipBudget: true, turnId: id }).ok, true);
    budget.object.settle(SID, null, id, { reply: 'Earlier interview turn' });
  }
}
async function error(response, status, code) { assert.equal(response.status, status); assert.equal((await response.json()).error.code, code); }

for (const hosted of [false, true]) test(`chat ${hosted ? 'hosted' : 'BYOK'} completes through provider parser, SQLite settlement and replay`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture-key' });
  const transport = t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.match(url, hosted ? /api.anthropic.com/ : /api.openai.com/);
    const request = JSON.parse(options.body);
    assert.equal(request.max_tokens, 300);
    assert.equal(hosted ? request.messages.at(-1).content[0].text : request.messages.at(-1).content, 'Tell me what happened.');
    assert.equal(request.messages.at(-1).injected, undefined);
    assert.ok(hosted ? request.system : request.messages[0].role === 'system');
    return upstream('I need some advice.', hosted);
  });
  const extra = { byok: hosted ? null : byok, system: 'malicious override', max_tokens: 90000 };
  const result = await f.call('/v1/chat', extra);
  assert.equal(result.status, 200);
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  const payload = await result.json();
  assert.deepEqual(payload, { reply: 'I need some advice.', turn: 1, remaining: 19, state: 'active', usage });
  assert.equal(f.budget.object.committedTurnsForPersona(SID, personaId), 1);
  assert.equal(spent(f.budget), hosted ? centsForUsage(usage) : 0);
  assert.deepEqual(await (await f.call('/v1/chat', extra)).json(), payload);
  assert.equal(transport.mock.callCount(), 1);
});

for (const [n, max, state] of [[14, 20, 'warning'], [19, 20, 'ended'], [0, 1, 'ended']]) test(`chat turn ${n + 1} returns ${state} and accurate remaining turns`, async t => {
  const f = await setup(t, { MAX_TURNS: String(max) }); seedTurns(f.budget, n);
  t.mock.method(globalThis, 'fetch', async () => upstream('Next answer'));
  const payload = await (await f.call()).json();
  assert.equal(payload.turn, n + 1); assert.equal(payload.state, state); assert.equal(payload.remaining, max - n - 1);
});

for (const hosted of [false, true]) for (const streaming of [false, true]) test(`chat config failure rolls back ${hosted ? 'hosted' : 'BYOK'} ${streaming ? 'stream' : 'JSON'} reservation and permits retry`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture', STREAMING: String(streaming) });
  t.mock.method(globalThis, 'fetch', async () => new Response('Rejected', { status: 401 }));
  await error(await f.call('/v1/chat', { byok: hosted ? null : byok }), hosted ? 503 : 400, hosted ? 'upstream_unavailable' : 'validation_error');
  assert.equal(spent(f.budget), 0);
  assert.equal(f.budget.object.committedTurnsForPersona(SID, personaId), 0);
  assert.equal(f.budget.object.preflight(SID, { personaId, pool: 'public', maxTurns: 20, skipBudget: true, reserveCents: 0, turnId: f.input.turn_id }).ok, true);
});

function sse({ hosted = false, complete = true, fail = false } = {}) {
  const frame = (data, event) => `${event ? `event: ${event}\n` : ''}data: ${JSON.stringify(data)}\n\n`;
  if (hosted) return frame({ type: 'message_start', message: { usage } }, 'message_start') + frame({ type: 'content_block_delta', delta: { type: 'text_delta', text: 'Hello client' } }, 'content_block_delta') + frame({ type: 'message_delta', usage: { output_tokens: 25 } }, 'message_delta') + (fail ? frame({ type: 'error', error: { type: 'overloaded_error' } }, 'error') : '') + (complete ? frame({ type: 'message_stop' }, 'message_stop') : '');
  return frame({ choices: [{ delta: { content: 'Hello client' } }] }) + frame({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + frame({ choices: [], usage: { prompt_tokens: 150, completion_tokens: 25, prompt_tokens_details: { cached_tokens: 50 } } }) + (fail ? frame({ error: { message: 'overloaded' } }) : '') + (complete ? 'data: [DONE]\n\n' : '');
}
for (const hosted of [false, true]) test(`stream ${hosted ? 'hosted' : 'BYOK'} settles normalized deltas and replays as JSON`, async t => {
  const f = await setup(t, { STREAMING: 'true', ANTHROPIC_API_KEY: 'hosted-fixture' });
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response(sse({ hosted }), { headers: { 'Content-Type': 'text/event-stream' } }));
  const extra = { byok: hosted ? null : byok };
  const response = await f.call('/v1/chat', extra);
  assert.equal(response.headers.get('x-sonsteng-stream'), '1');
  const text = await response.text(); assert.match(text, /event: delta/); assert.match(text, /event: done/); assert.match(text, /Hello client/);
  assert.equal(f.budget.object.committedTurnsForPersona(SID, personaId), 1);
  assert.equal(spent(f.budget), hosted ? centsForUsage(usage) : 0);
  const replay = await f.call('/v1/chat', extra);
  assert.equal(replay.headers.get('x-sonsteng-stream'), null);
  assert.equal((await replay.json()).reply, 'Hello client'); assert.equal(fetch.mock.callCount(), 1);
});
test('stream explicit provider error clears committed turn', async t => {
  const f = await setup(t, { STREAMING: 'true' });
  t.mock.method(globalThis, 'fetch', async () => new Response(sse({ complete: true, fail: true })));
  const text = await (await f.call()).text(); assert.match(text, /event: error/); assert.doesNotMatch(text, /event: done/);
  assert.equal(f.budget.object.committedTurnsForPersona(SID, personaId), 0); assert.equal(spent(f.budget), 0);
});

test('router replay is a full SQLite integration with no substituted transport', async t => {
  const f = await setup(t); seedTurns(f.budget, 1);
  assert.deepEqual(await (await f.call('/v1/chat', { turn_id: 'seed-0' })).json(), { reply: 'Earlier interview turn' });
});

function debriefCard() { return { schema_version: '1.0.0', matter_id: persona.matter_id, persona_id: personaId, axis_a: { facts_elicited: [], revealed_if_asked_missed: [], rapport_gated_unearned: [], rule_4_2_flags: [] }, axis_b: Object.fromEntries(['rapport_opening', 'listening_t_funnel', 'understanding_goals', 'explanation_next_steps', 'overall_confidence'].map(k => [k, { score: 5, comment: 'A clear discussion.' }])), ethics_score: 0, narrative: 'A clear discussion.', self_reflection_prompt: 'What would you ask next?' }; }
for (const hosted of [false, true]) test(`debrief ${hosted ? 'hosted' : 'BYOK'} validates real scorecard after committed interview`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture' }); seedTurns(f.budget, 6);
  t.mock.method(globalThis, 'fetch', async () => upstream(JSON.stringify(debriefCard()), hosted));
  const response = await f.call('/v1/debrief', { byok: hosted ? null : byok, transcript: f.input.messages });
  assert.equal(response.status, 200); const result = (await response.json()).scorecard; assert.deepEqual(result.axis_b, debriefCard().axis_b); assert.deepEqual(result.axis_a.facts_elicited, []); assert.ok(result.axis_a.revealed_if_asked_missed.length > 0);
  assert.equal(spent(f.budget), hosted ? centsForUsage(usage) : 0);
  assert.equal(f.budget.sql.exec('SELECT * FROM one_shot_reservations').toArray().length, 0);
});
for (const text of ['not JSON', '{}']) test(`debrief invalid provider payload ${text} is rejected after actual generation`, async t => {
  const f = await setup(t); seedTurns(f.budget, 6);
  t.mock.method(globalThis, 'fetch', async () => upstream(text));
  await error(await f.call('/v1/debrief', { transcript: f.input.messages }), 502, 'validation_error');
});
test('debrief truncation invokes larger second generation and settles both hosted calls', async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture' }); seedTurns(f.budget, 6);
  const limits = [];
  t.mock.method(globalThis, 'fetch', async (_url, opts) => { limits.push(JSON.parse(opts.body).max_tokens); return limits.length === 1 ? upstream('{', true, 'max_tokens') : upstream(JSON.stringify(debriefCard()), true); });
  assert.equal((await f.call('/v1/debrief', { byok: null, transcript: f.input.messages })).status, 200);
  assert.deepEqual(limits, [1200, 2400]); assert.equal(spent(f.budget), 2 * centsForUsage(usage));
});

const critiqueMatter = Object.keys(bundle.rubrics)[0];
function critiqueCard() { return { schema_version: '1.0.0', matter_id: critiqueMatter, rubric_id: `${critiqueMatter}.rub`, criteria: [{ criterion_id: `${critiqueMatter}.rub.c01`, score: 1, weight_points: 2, evidence: 'Discussion', suggestions: 'Expand reasoning' }], total: { earned: 1, possible: 2 }, narrative: 'Good start', revise_resubmit_note: 'Revise' }; }
for (const hosted of [false, true]) test(`critique ${hosted ? 'hosted' : 'BYOK'} returns rubric labels and valid scorecard`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture' });
  t.mock.method(globalThis, 'fetch', async () => upstream(JSON.stringify(critiqueCard()), hosted));
  const response = await f.call('/v1/critique', { byok: hosted ? null : byok, matter_id: critiqueMatter, deliverable_text: 'My legal memo' });
  assert.equal(response.status, 200); const result = await response.json(); assert.deepEqual(result.scorecard, critiqueCard()); assert.ok(Object.keys(result.criteria_labels).length);
  assert.equal(spent(f.budget), hosted ? centsForUsage(usage) : 0);
});
for (const text of ['unparseable', '{}']) test(`critique rejects provider output ${text}`, async t => {
  const f = await setup(t); t.mock.method(globalThis, 'fetch', async () => upstream(text));
  await error(await f.call('/v1/critique', { matter_id: critiqueMatter, deliverable_text: 'My memo' }), 502, 'validation_error');
});

const instrument = bundle.assessment_instrument;
const headings = instrument.content.dimensions.map(d => d.id);
const submission = headings.map(id => `Evidence for ${id}.`).join('\n\n');
function memoCard() { return { schema_version: '1.0.0', instrument_id: instrument.id, instrument_version: instrument.instrument_version, instrument_content_hash: instrument.content_hash, headings: headings.map(heading_id => ({ heading_id, evidence_spans: [`Evidence for ${heading_id}.`], rationale: 'The submission supports this score.', score: 4 })) }; }
for (const hosted of [false, true]) test(`memo assessment ${hosted ? 'hosted' : 'BYOK'} persists real panel and audit before returning`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture' });
  const editor = await durableObject(EditorStore); t.after(() => editor.close());
  f.env.EDITOR = { getByName: () => editor.object };
  const transport = t.mock.method(globalThis, 'fetch', async () => upstream(JSON.stringify(memoCard()), hosted));
  const response = await f.call('/v1/memo-assessment', { byok: hosted ? null : byok, deliverable_text: submission });
  assert.equal(response.status, 200); const result = await response.json();
  assert.equal(result.assessment.assessment_use, 'formative'); assert.equal(result.assessment.summative_eligible, false);
  assert.match(result.assessment_audit_id, /^memo-assessment-/);
  assert.equal(result.assessment.headings.length, headings.length);
  assert.equal(transport.mock.callCount(), 1);
  assert.equal(spent(f.budget), hosted ? centsForUsage(usage) : 0);
  const audit = editor.object.readAssessmentAudit({ id: result.assessment_audit_id, scopes: { 'assessment-review': { granted: true, ver: 1 } } });
  assert.equal(audit.ok, true); assert.equal(audit.record.evidence.submission, submission);
  assert.equal(JSON.stringify(audit).includes(byok.api_key), false);
  assert.equal(JSON.stringify(audit).includes(f.input.session_token), false);
});
for (const hosted of [false, true]) test(`memo ${hosted ? 'hosted' : 'BYOK'} provider rejection refunds spending but consumes rate claim`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture', MAX_ASSESSMENTS_PER_SESSION_DAY: '1' });
  const transport = t.mock.method(globalThis, 'fetch', async () => new Response('Rejected', { status: 401 }));
  const extra = { byok: hosted ? null : byok, deliverable_text: submission };
  await error(await f.call('/v1/memo-assessment', extra), hosted ? 503 : 400, hosted ? 'upstream_unavailable' : 'validation_error');
  assert.equal(spent(f.budget), 0);
  await error(await f.call('/v1/memo-assessment', extra), 429, 'rate_limited');
  assert.equal(transport.mock.callCount(), 1);
});
test('memo malformed scorecard is rejected before audit persistence', async t => {
  const f = await setup(t); t.mock.method(globalThis, 'fetch', async () => upstream('{}'));
  await error(await f.call('/v1/memo-assessment', { deliverable_text: submission }), 502, 'validation_error');
});
for (const path of ['/v1/debrief', '/v1/critique', '/v1/memo-assessment']) test(`${path} hosted spending cap blocks transport through real SQLite ledger`, async t => {
  const f = await setup(t, { ANTHROPIC_API_KEY: 'hosted-fixture' }); seedTurns(f.budget, 6);
  f.budget.object.charge('public', { input_tokens: 7_000_000 });
  await error(await f.call(path, { byok: null, matter_id: path === '/v1/critique' ? critiqueMatter : persona.matter_id, transcript: f.input.messages, deliverable_text: submission }), 429, 'cap_exceeded');
});
for (const path of ['/v1/debrief', '/v1/critique']) test(`${path} BYOK rejection is actionable after real request construction`, async t => {
  const f = await setup(t); seedTurns(f.budget, 6);
  t.mock.method(globalThis, 'fetch', async () => new Response('Unauthorized', { status: 401 }));
  await error(await f.call(path, { matter_id: path === '/v1/critique' ? critiqueMatter : persona.matter_id, transcript: f.input.messages, deliverable_text: submission }), 400, 'validation_error');
});

test('Google BYOK chat uses the actual adapter and canonical usage settlement', async t => {
  const f = await setup(t);
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.match(url, /generativelanguage.googleapis.com/);
    const body = JSON.parse(options.body); assert.equal(body.generationConfig.maxOutputTokens, 300); assert.ok(body.systemInstruction);
    return Response.json({ candidates: [{ content: { parts: [{ text: 'Google reply' }] }, finishReason: 'STOP' }], usageMetadata: { promptTokenCount: 150, candidatesTokenCount: 25, cachedContentTokenCount: 50 } });
  });
  const response = await f.call('/v1/chat', { byok: { provider: 'google', api_key: 'google-fixture-key' } });
  assert.equal(response.status, 200); const payload = await response.json(); assert.equal(payload.reply, 'Google reply'); assert.deepEqual(payload.usage, usage);
  assert.equal(f.budget.object.committedTurnsForPersona(SID, personaId), 1); assert.equal(spent(f.budget), 0);
});
test('Google stream normalizes terminal usage and commits a replay record', async t => {
  const f = await setup(t, { STREAMING: 'true' });
  t.mock.method(globalThis, 'fetch', async () => new Response('data: ' + JSON.stringify({ candidates: [{ content: { parts: [{ text: 'Stream reply' }] }, finishReason: 'STOP' }], usageMetadata: { promptTokenCount: 150, candidatesTokenCount: 25, cachedContentTokenCount: 50 } }) + '\n\n'));
  const extra = { byok: { provider: 'google', api_key: 'google-fixture-key' } };
  assert.match(await (await f.call('/v1/chat', extra)).text(), /event: done/);
  const replay = await (await f.call('/v1/chat', extra)).json(); assert.equal(replay.reply, 'Stream reply'); assert.deepEqual(replay.usage, usage);
});
test('stream transport abort clears hosted reserve before rejection reaches client', async t => {
  const f = await setup(t, { STREAMING: 'true', ANTHROPIC_API_KEY: 'hosted-fixture' });
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({ start(controller) { controller.error(new Error('fixture transport abort')); } })));
  const response = await f.call('/v1/chat', { byok: null });
  await assert.rejects(response.text(), /fixture transport abort/);
  assert.equal(spent(f.budget), 0); assert.equal(f.budget.object.committedTurnsForPersona(SID, personaId), 0);
});
test('legacy chat without turn_id receives generated reservation identity and settles normally', async t => {
  const f = await setup(t); t.mock.method(globalThis, 'fetch', async () => upstream('Legacy client answer'));
  assert.equal((await f.call('/v1/chat', { turn_id: null })).status, 200);
  const row = f.budget.sql.exec('SELECT last_turn_id, last_result FROM sessions WHERE sid=?', SID).toArray()[0];
  assert.match(row.last_turn_id, /^[a-f0-9-]{36}$/); assert.equal(JSON.parse(row.last_result).reply, 'Legacy client answer');
});
test('memo multi-provider panel aggregates actual parsed scorecards and persists provider provenance', async t => {
  const f = await setup(t); const editor = await durableObject(EditorStore); t.after(() => editor.close()); f.env.EDITOR = { getByName: () => editor.object };
  const transport = t.mock.method(globalThis, 'fetch', async url => {
    const text = JSON.stringify(memoCard());
    if (url.includes('googleapis')) return Response.json({ candidates: [{ content: { parts: [{ text }] }, finishReason: 'STOP' }], usageMetadata: {} });
    return upstream(text, url.includes('anthropic'));
  });
  const response = await f.call('/v1/memo-assessment', { byok: null, byok_panel: ['openai', 'anthropic', 'google'].map(provider => ({ provider, api_key: `${provider}-panel-fixture-key` })), deliverable_text: submission });
  assert.equal(response.status, 200); const result = await response.json();
  assert.equal(transport.mock.callCount(), 3); assert.equal(result.assessment.providers.length, 3);
  for (const heading of result.assessment.headings) assert.equal(heading.score, 4);
  const audit = editor.object.readAssessmentAudit({ id: result.assessment_audit_id, scopes: { 'assessment-review': { granted: true, ver: 1 } } });
  assert.equal(audit.ok, true); assert.equal(audit.record.provenance.providers.length, 3);
});
