import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeChatTransform, parseSSEFrame, pipeProviderStream } from '../src/chat-stream.js';
import { BudgetCore } from '../src/budget-core.js';
import { centsForUsage } from '../src/cost.js';
import { NodeSql } from './editor-sql-helper.mjs';

const frame = data => `data: ${JSON.stringify(data)}\n\n`;
const buildDonePayload = (reply, usage) => ({ reply, usage });
async function consume(text, options = {}, bytewise = false) {
  const bytes = new TextEncoder().encode(text);
  const stream = new ReadableStream({ start(c) {
    if (bytewise) for (const byte of bytes) c.enqueue(Uint8Array.of(byte));
    else c.enqueue(bytes);
    c.close();
  } });
  const output = await new Response(stream.pipeThrough(makeChatTransform({ buildDonePayload, ...options }))).text();
  return output.split('\n\n').filter(Boolean).map(raw => {
    const parsed = parseSSEFrame(raw);
    return { event: parsed.event, data: JSON.parse(parsed.data) };
  });
}

test('SSE fields without colon, repeated event names and extra spaces retain wire semantics', () => {
  assert.deepEqual(parseSSEFrame('\r\n:keepalive\r\nid: ignored\r\nevent: first\r\nevent\r\ndata\r\ndata:  indented\r\nretry: 100'),
    { event: '', data: '\n indented' });
  assert.equal(parseSSEFrame('id: 123\nretry: 100\nunknown'), null);
});

test('mixed CRLF/LF and un-delimited terminal tail preserve split UTF-8 text', async () => {
  const text = frame({ choices: [{ delta: { content: 'é 学習 🦉' } }] }).replaceAll('\n', '\r\n')
    + '\n\n' + frame({ usage: { prompt_tokens: 0, completion_tokens: 0 } }) + 'data: [DONE]';
  const events = await consume(text, { provider: 'openai' }, true);
  assert.deepEqual(events.map(e => e.event), ['delta', 'done']);
  assert.equal(events[1].data.reply, 'é 学習 🦉');
  assert.deepEqual(events[1].data.usage, { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0 });
});

for (const [provider, text] of [
  ['anthropic', frame({ type: 'message_delta', usage: { input_tokens: 0 } }) + frame({ type: 'message_stop' })],
  ['openai', frame({ usage: { completion_tokens: 0 } }) + 'data: [DONE]\n\n'],
  ['google', frame({ usageMetadata: { candidatesTokenCount: 0 } }) + frame({ candidates: [{ finishReason: 'STOP' }] })],
]) test(`${provider} accepts zero usage and an empty successful answer without optional callbacks`, async () => {
  const events = await consume(text, { provider });
  assert.equal(events.length, 1); assert.equal(events[0].event, 'done'); assert.equal(events[0].data.reply, '');
});

for (const [provider, text] of [
  ['anthropic', frame({ type: 'message_start', message: { usage: { input_tokens: '100', output_tokens: null, cache_read_input_tokens: false, cache_creation_input_tokens: {} } } }) + frame({ type: 'message_delta', usage: {} }) + frame({ type: 'message_stop' })],
  ['openai', frame({ usage: {} }) + 'data: [DONE]\n\n'],
  ['google', frame({ usageMetadata: {} }) + frame({ candidates: [{ finishReason: 'STOP' }] })],
]) test(`${provider} refuses terminal markers without numeric terminal usage`, async () => {
  let failed = 0;
  const events = await consume(text, { provider, onFailure: async (_usage, reply) => { failed++; assert.equal(reply, ''); } });
  assert.deepEqual(events, []); assert.equal(failed, 1);
});

test('Anthropic ignores nontext and missing deltas, replacing initial input usage with final usage', async () => {
  const events = await consume([
    { type: 'message_start' }, { type: 'message_start', message: {} },
    { type: 'message_start', message: { usage: { input_tokens: 12 } } },
    { type: 'content_block_delta' }, { type: 'content_block_delta', delta: { type: 'input_json_delta' } },
    { type: 'content_block_delta', delta: { type: 'text_delta' } },
    { type: 'message_delta' }, { type: 'message_delta', usage: { input_tokens: 22, output_tokens: 'invalid' } },
    { type: 'message_stop' },
  ].map(frame).join(''));
  assert.deepEqual(events, [{ event: 'done', data: { reply: '', usage: { input_tokens: 22 } } }]);
});

test('empty, comments-only, JSON null and unknown-provider streams close without callbacks', async () => {
  for (const text of ['', ':keepalive\n\n', 'data: null\n\n', frame({ choices: [] })]) {
    assert.deepEqual(await consume(text, { provider: 'unknown' }), []);
  }
});

test('only the first provider error is forwarded and subsequent text cannot contaminate failure accounting', async () => {
  let failure;
  const events = await consume(frame({ error: {} }) + frame({ error: { message: 'second' } })
    + frame({ choices: [{ delta: { content: 'untrusted late text' } }] }),
  { provider: 'openai', onFailure: async (usage, reply) => { failure = { usage, reply }; } });
  assert.deepEqual(events, [{ event: 'error', data: { message: 'upstream stream error' } }]);
  assert.deepEqual(failure, { usage: {}, reply: '' });
});

test('settlement rejection without a failure callback closes without claiming success', async () => {
  const events = await consume(frame({ type: 'message_delta', usage: { output_tokens: 1 } }) + frame({ type: 'message_stop' }),
    { onSettle: async () => { throw new Error('receipt lost'); } });
  assert.deepEqual(events, []);
});

test('failure-finalizer rejection propagates to the consumer', async () => {
  await assert.rejects(consume('', { onFailure: async () => { throw new Error('ledger unavailable'); } }), /ledger unavailable/);
});

test('pipeProviderStream propagates source failure without an optional finalizer', async () => {
  const upstreamBody = new ReadableStream({ start(c) { c.error(new Error('source gone')); } });
  const body = pipeProviderStream({ upstreamBody, transform: makeChatTransform({ buildDonePayload }) });
  await assert.rejects(new Response(body).text(), /source gone/);
});

for (const successful of [true, false]) test(`fragmented Anthropic stream ${successful ? 'settles replay' : 'bills known failed usage and permits retry'} through real SQLite`, async () => {
  const sql = new NodeSql();
  try {
    const core = new BudgetCore(sql, () => new Date('2026-01-01T12:00:00Z'));
    core.initSchema();
    const options = { pool: 'public', personaId: 'stream-persona', turnId: 'stream-turn', maxTurns: 2, reserveCents: 5, capPublicCents: 100, capDemoCents: 100, skipBudget: false };
    assert.equal(core.preflight('stream-session', options).ok, true);
    const usage = { input_tokens: 10_000, output_tokens: 500 };
    const wire = frame({ type: 'message_start', message: { usage: { input_tokens: usage.input_tokens } } })
      + frame({ type: 'content_block_delta', delta: { type: 'text_delta', text: 'A measured answer 🦉' } })
      + frame({ type: 'message_delta', usage: { output_tokens: usage.output_tokens } })
      + frame(successful ? { type: 'message_stop' } : { error: { message: 'provider interrupted' } });
    const events = await consume(wire.replaceAll('\n', '\r\n').trimEnd(), {
      onSettle: async (payload, actual) => core.settle('stream-session', actual, options.turnId, payload),
      onFailure: async actual => core.fail('stream-session', actual, options.turnId, options.personaId),
    }, true);
    assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, centsForUsage(usage));
    const next = core.preflight('stream-session', options);
    assert.equal(next.ok, true);
    if (successful) {
      assert.equal(next.replay, true);
      assert.deepEqual(next.result, { reply: 'A measured answer 🦉', usage });
      assert.deepEqual(events.at(-1), { event: 'done', data: next.result });
    } else {
      assert.equal(next.replay, undefined);
      assert.equal(next.turn, 1);
      assert.equal(events.at(-1).event, 'error');
      assert.equal(events.some(e => e.event === 'done'), false);
    }
  } finally { sql.db.close(); }
});
