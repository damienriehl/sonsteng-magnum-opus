import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as anthropic from '../src/providers/anthropic.js';
import * as google from '../src/providers/google.js';
import { completeWithRetry } from '../src/providers/common.js';
import { centsForUsage, worstCaseReserveCents } from '../src/cost.js';

function localCompletion(data, parse) {
  return completeWithRetry(() => ({ url: `data:application/json,${encodeURIComponent(JSON.stringify(data))}`, headers: {}, body: {} }), parse, 1);
}

test('Anthropic integration prices cache writes, cache reads and output from a real local completion', async () => {
  const result = await localCompletion({
    content: [{ type: 'thinking', thinking: 'excluded' }, { type: 'text', text: 'First ' }, { type: 'tool_use', input: {} }, { type: 'text', text: 'second.' }],
    usage: { input_tokens: 500_000, output_tokens: 300, cache_creation_input_tokens: 400_000, cache_read_input_tokens: 2_000_000 },
    stop_reason: 'end_turn',
  }, anthropic.parseResponse);
  assert.equal(result.ok, true); assert.equal(result.text, 'First second.');
  assert.equal(result.stop_reason, 'stop'); assert.equal(centsForUsage(result.usage), 121);
});
for (const data of [{}, { content: null, usage: null }, { content: [], usage: {} }]) {
  test(`Anthropic empty response normalizes for billing: ${JSON.stringify(data)}`, () => {
    const result = anthropic.parseResponse(data);
    assert.deepEqual(result, { text: '', usage: {}, stop_reason: null });
    assert.equal(centsForUsage(result.usage), 0);
  });
}
test('Anthropic malformed content returns an ambiguous upstream result through completion', async () => {
  for (const content of [{ text: 'wrong envelope' }, [null]]) {
    const result = await localCompletion({ content }, anthropic.parseResponse);
    assert.deepEqual(result, { ok: false, kind: 'upstream', status: 200, ambiguous_attempts: 1 });
  }
});
test('Anthropic plain system and empty cached history do not create an invalid breakpoint', () => {
  const opts = { messages: [], maxTokens: 10, providerCfg: { model: 'test', apiKey: 'fixture-only' } };
  const plain = anthropic.buildRequest({ ...opts, system: 'Plain instructions.' });
  assert.deepEqual(plain.body.system, [{ type: 'text', text: 'Plain instructions.' }]);
  const cached = anthropic.buildStreamingRequest({ ...opts, system: { prefix: 'Prefix', tail: 'Tail' } });
  assert.deepEqual(cached.body.messages, []);
  assert.equal(cached.body.stream, true);
  assert.deepEqual(cached.body.system[0], { type: 'text', text: 'Prefix\n\n', cache_control: { type: 'ephemeral' } });
});
test('Google integration normalizes cached input and thought tokens before cents calculation', async () => {
  const result = await localCompletion({
    candidates: [
      { finishReason: 'STOP', content: { parts: [{ text: 'Chosen' }, { inlineData: { mimeType: 'image/png', data: '' } }, { text: ' answer' }] } },
      { content: { parts: [{ text: 'Ignore alternative' }] } },
    ],
    usageMetadata: { promptTokenCount: 1_000_000, cachedContentTokenCount: 200_000, candidatesTokenCount: 100_000, thoughtsTokenCount: 50_000 },
  }, google.parseResponse);
  assert.equal(result.ok, true); assert.equal(result.text, 'Chosen answer');
  assert.deepEqual(result.usage, { input_tokens: 800_000, output_tokens: 100_000, cache_read_input_tokens: 200_000, thought_tokens: 50_000 });
  assert.equal(centsForUsage(result.usage), 157);
  assert.equal(result.usageMetadata, undefined);
});
for (const data of [{}, { candidates: null }, { candidates: [] }, { candidates: [null] }, { candidates: [{}] }, { candidates: [{ content: {} }] }]) {
  test(`Google empty candidate response yields zero billable usage: ${JSON.stringify(data)}`, () => {
    const result = google.parseResponse(data);
    assert.deepEqual(result, { text: '', stop_reason: null, usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0 } });
    assert.equal(centsForUsage(result.usage), 0);
  });
}
test('Google clamps thought tokens and cached-input subtraction at zero', () => {
  const result = google.parseResponse({ candidates: [{ finishReason: 'MAX_TOKENS' }], usageMetadata: { promptTokenCount: 10, cachedContentTokenCount: 20, thoughtsTokenCount: -5, totalTokenCount: -1 } });
  assert.deepEqual(result.usage, { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 20, thought_tokens: 0 });
  assert.deepEqual(result.usageMetadata, { promptTokenCount: 10, thoughtsTokenCount: 0, totalTokenCount: 0 });
});
test('Google truncation with no finite telemetry omits usageMetadata instead of exposing arbitrary provider fields', () => {
  for (const usageMetadata of [undefined, {}, { thoughtsTokenCount: '12', totalTokenCount: Infinity, responseText: 'private' }]) {
    const result = google.parseResponse({ candidates: [{ finishReason: 'MAX_TOKENS' }], usageMetadata });
    assert.equal(result.stop_reason, 'max_tokens');
    assert.equal(result.usageMetadata, undefined);
    assert.equal(result.usage.thought_tokens, undefined);
  }
});
test('Google malformed parts are contained by completion error handling', async () => {
  for (const parts of [{ text: 'wrong shape' }, [null]]) {
    const result = await localCompletion({ candidates: [{ content: { parts } }] }, google.parseResponse);
    assert.deepEqual(result, { ok: false, kind: 'upstream', status: 200, ambiguous_attempts: 1 });
  }
});
test('Google request safely encodes model path and retains explicit positive thinking budget', () => {
  const request = google.buildStreamingRequest({ system: '', messages: [], maxTokens: 100, thinkingBudget: 20, providerCfg: { model: 'model/with ?characters', apiKey: 'fixture-only', jsonMode: true } });
  assert.equal(request.url, 'https://generativelanguage.googleapis.com/v1beta/models/model%2Fwith%20%3Fcharacters:streamGenerateContent?alt=sse');
  assert.deepEqual(request.body.contents, []);
  assert.equal(request.body.systemInstruction, undefined);
  assert.deepEqual(request.body.generationConfig, { maxOutputTokens: 100, responseMimeType: 'application/json', thinkingConfig: { thinkingBudget: 20 } });
});
test('cost accepts absent usage and reservation estimates as zero', () => {
  for (const usage of [undefined, null]) assert.equal(centsForUsage(usage), 0);
  assert.equal(worstCaseReserveCents(), 0);
  assert.equal(worstCaseReserveCents(null, null), 0);
  assert.equal(worstCaseReserveCents(undefined, 2000), 1);
  assert.equal(worstCaseReserveCents(10000, undefined), 1);
});
test('cost cent boundary bills exact cents and rounds just-over-boundary usage upward', () => {
  assert.equal(centsForUsage({ input_tokens: 10000 }), 1);
  assert.equal(centsForUsage({ input_tokens: 10001 }), 2);
  assert.equal(centsForUsage({ output_tokens: 2000 }), 1);
  assert.equal(centsForUsage({ output_tokens: 2001 }), 2);
  assert.equal(worstCaseReserveCents(10000, 0), 1);
  assert.equal(worstCaseReserveCents(10001, 0), 2);
});
