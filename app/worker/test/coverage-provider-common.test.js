import { test } from 'node:test';
import assert from 'node:assert/strict';
import { completeWithRetry, normalizeStopReason, systemToString } from '../src/providers/common.js';
import { parseResponse } from '../src/providers/openai.js';

const request = () => ({ url: 'https://unused.invalid', headers: {}, body: { prompt: 'test' } });
const parse = data => ({ text: data.answer });
const success = () => new Response('{"answer":"recovered"}');

// Real fetch can exercise the request/JSON/parser chain without sockets or any network.
test('common completion integrates native data-URL fetch with the real OpenAI parser', async () => {
  const payload = { choices: [{ message: { content: 'Complete answer' }, finish_reason: 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 2 } };
  const result = await completeWithRetry(() => ({ url: 'data:application/json,' + encodeURIComponent(JSON.stringify(payload)), body: {}, headers: {} }), parseResponse);
  assert.deepEqual(result, { ok: true, text: 'Complete answer', usage: { input_tokens: 10, output_tokens: 2, cache_read_input_tokens: 0 }, stop_reason: 'stop' });
});

test('native fetch of malformed JSON becomes an ambiguous upstream failure', async () => {
  assert.deepEqual(await completeWithRetry(() => ({ url: 'data:text/plain,broken', body: {} }), parse), { ok: false, kind: 'upstream', status: 200, ambiguous_attempts: 1 });
});

async function sequence(t, replies, maxAttempts, parser = parse) {
  let calls = 0;
  const delays = [];
  t.mock.method(globalThis, 'fetch', async () => {
    const reply = replies[calls++];
    assert.ok(reply, 'no unplanned provider request');
    if (reply instanceof Error) throw reply;
    return reply();
  });
  t.mock.method(globalThis, 'setTimeout', callback => { callback(); return 0; });
  // Capture backoffs separately; timeout signals use the native implementation.
  globalThis.setTimeout.mock.mockImplementation((callback, ms) => { delays.push(ms); callback(); return 0; });
  const result = await completeWithRetry(request, parser, maxAttempts);
  return { result, calls, delays };
}

for (const status of [400, 401, 403, 404, 422]) test(`provider ${status} is configuration failure without retry`, async t => {
  const { result, calls } = await sequence(t, [() => new Response('', { status })]);
  assert.deepEqual(result, { ok: false, kind: 'config', status });
  assert.equal(calls, 1);
});
for (const [header, wait] of [['-1', 0], ['0', 0], ['10', 2000], ['nonsense', 1000], [null, 1000]]) test(`429 retry-after ${header} uses bounded delay and recovers`, async t => {
  const { result, calls, delays } = await sequence(t, [() => new Response('', { status: 429, headers: header === null ? {} : { 'retry-after': header } }), success]);
  assert.deepEqual(result, { ok: true, text: 'recovered', usage: {} });
  assert.equal(calls, 2);
  assert.deepEqual(delays, [wait]);
});

test('failed second response parser is counted as ambiguous', async t => {
  const { result, calls } = await sequence(t, [() => new Response('', { status: 500 }), success], 2, () => { throw new Error('bad shape'); });
  assert.deepEqual(result, { ok: false, kind: 'upstream', status: 200, ambiguous_attempts: 1 });
  assert.equal(calls, 2);
});

test('network rejection after retryable HTTP status retains one ambiguous attempt', async t => {
  const { result } = await sequence(t, [() => new Response('', { status: 503 }), new Error('connection lost')]);
  assert.deepEqual(result, { ok: false, kind: 'upstream', ambiguous_attempts: 1 });
});

test('consecutive network rejections retain both ambiguous attempts', async t => {
  const { result, calls } = await sequence(t, [new Error('lost'), new Error('lost again')]);
  assert.deepEqual(result, { ok: false, kind: 'upstream', ambiguous_attempts: 2 });
  assert.equal(calls, 2);
});

test('single-attempt budget never retries a network rejection', async t => {
  const { result, calls, delays } = await sequence(t, [new Error('lost')], 1);
  assert.deepEqual(result, { ok: false, kind: 'upstream', ambiguous_attempts: 1 });
  assert.equal(calls, 1); assert.deepEqual(delays, []);
});

test('configuration rejection after a retry keeps its classification', async t => {
  const { result } = await sequence(t, [() => new Response('', { status: 503 }), () => new Response('', { status: 403 })]);
  assert.deepEqual(result, { ok: false, kind: 'config', status: 403 });
});

for (const limit of [0, -1, 1.5, '2', null]) test(`invalid attempt limit ${limit} falls back to bounded defaults`, async t => {
  const { result, calls } = await sequence(t, [new Error('lost'), () => new Response('', { status: 503 }), success], limit);
  assert.equal(result.ok, true); assert.equal(result.ambiguous_attempts, 1); assert.equal(calls, 3);
});

test('normalization keeps safety and tool reasons while ignoring absent values', () => {
  for (const [input, expected] of [[' LENGTH ', 'max_tokens'], ['MAX_TOKENS', 'max_tokens'], [' end_turn ', 'stop'], [' STOP ', 'stop'], [' SAFETY ', 'safety'], ['TOOL_CALLS', 'tool_calls'], ['', null], [null, null], [42, null]]) assert.equal(normalizeStopReason(input), expected);
  assert.equal(systemToString(undefined), null);
  assert.equal(systemToString({ prefix: '', tail: '' }), '\n\n');
});

test('two explicit retryable HTTP failures exhaust without ambiguous billing', async t => {
  const { result, calls } = await sequence(t, [() => new Response('', { status: 500 }), () => new Response('', { status: 529 })]);
  assert.deepEqual(result, { ok: false, kind: 'upstream', status: 529 });
  assert.equal(calls, 2);
});
