import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { aggregateMemoPanel, runFormativeMemoPanel } from '../src/panel.js';
import { resolveAssessmentThresholdConfig } from '../src/assessment-config.js';
import { completeWithRetry } from '../src/providers/common.js';
import { parseResponse } from '../src/providers/anthropic.js';

const instrument = JSON.parse(readFileSync(new URL('../../../data/curriculum/assessment-instrument.json', import.meta.url), 'utf8'));
const ids = instrument.content.dimensions.map(d => d.id);
const submission = ids.map(id => `Evidence for ${id}.`).join('\n\n');
const graders = [
  { provider: 'anthropic', model: 'model-a', mode: 'byok', apiKey: 'fixture-only-a' },
  { provider: 'google', model: 'model-b', mode: 'byok', apiKey: '' },
];
function card(score = 4) {
  return {
    schema_version: '1.0.0', instrument_id: instrument.id,
    instrument_version: instrument.instrument_version, instrument_content_hash: instrument.content_hash,
    headings: ids.map(heading_id => ({ heading_id, score, evidence_spans: [`Evidence for ${heading_id}.`], rationale: 'Grounded evidence.' })),
  };
}
const base = { instrument, submission, thresholdConfig: resolveAssessmentThresholdConfig(undefined, instrument).config, graders };
function localCompletion(value, usage) {
  const data = { content: [{ type: 'text', text: JSON.stringify(value) }], usage, stop_reason: 'end_turn' };
  return completeWithRetry(() => ({ url: `data:application/json,${encodeURIComponent(JSON.stringify(data))}`, headers: {}, body: {} }), parseResponse, 1);
}
async function adjudicate(value) {
  return runFormativeMemoPanel({ ...base, complete: ({ kind, grader }) => localCompletion(kind === 'adjudication' ? value : card(grader === graders[0] ? 2 : 4)) });
}

test('panel integration: local HTTP completion, provider parser, validator and adjudication preserve lower clamp and usage', async () => {
  const calls = [];
  const result = await runFormativeMemoPanel({ ...base, complete: ({ kind, grader, prompt }) => {
    calls.push(kind);
    assert.ok(prompt.includes(submission));
    return localCompletion(kind === 'adjudication' ? { headings: ids.map(heading_id => ({ heading_id, score: 1 })) } : card(grader === graders[0] ? 2 : 4), { input_tokens: 3, output_tokens: 2 });
  } });
  assert.equal(result.ok, true);
  assert.deepEqual(calls, ['grader', 'grader', 'adjudication']);
  assert.deepEqual(result.usage, { input_tokens: 9, output_tokens: 6, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 });
  for (const heading of result.result.headings) {
    assert.equal(heading.score, 2);
    assert.equal(heading.median_score, 2);
    assert.deepEqual(heading.adjudication, { triggered: true, proposed_score: 1, score: 2 });
  }
});

for (const [label, value] of [['unparseable', null], ['missing headings', {}], ['non-array headings', { headings: {} }], ['empty headings', { headings: [] }]]) {
  test(`panel adjudication rejects ${label} through real completion chain`, async () => {
    const result = await adjudicate(value);
    assert.deepEqual(result, { ok: false, kind: 'validation', errors: ['adjudication headings invalid'] });
  });
}
for (const [label, invalid] of [
  ['null row', null], ['array row', []], ['primitive row', 3],
  ['unknown field', { heading_id: ids[0], score: 3, rationale: 'extra' }],
  ['unknown heading', { heading_id: 'unknown', score: 3 }],
  ['string score', { heading_id: ids[0], score: '3' }],
  ['fraction', { heading_id: ids[0], score: 3.5 }],
  ['below scale', { heading_id: ids[0], score: 0 }],
  ['above scale', { heading_id: ids[0], score: 8 }],
]) {
  test(`panel adjudication rejects ${label}`, async () => {
    const headings = ids.map(heading_id => ({ heading_id, score: 3 })); headings[0] = invalid;
    assert.deepEqual(await adjudicate({ headings }), { ok: false, kind: 'validation', errors: ['adjudication result invalid'] });
  });
}
test('panel adjudication rejects duplicate IDs even when total length matches', async () => {
  const headings = ids.map(heading_id => ({ heading_id, score: 3 })); headings[1] = headings[0];
  assert.deepEqual(await adjudicate({ headings }), { ok: false, kind: 'validation', errors: ['adjudication result invalid'] });
});
for (const value of [null, { ok: false, kind: 'config', status: 401 }]) {
  test(`panel stops on adjudicator ${value === null ? 'absent result' : 'config failure'}`, async () => {
    let calls = 0;
    const result = await runFormativeMemoPanel({ ...base, complete: ({ kind, grader }) => {
      calls++;
      return kind === 'adjudication' ? value : localCompletion(card(grader === graders[0] ? 2 : 4));
    } });
    assert.equal(calls, 3);
    assert.equal(result.ok, false); assert.equal(result.kind, 'upstream');
    assert.deepEqual(result.upstreamResult, value || { kind: 'upstream' });
    assert.equal(result.result, undefined);
  });
}
test('panel missing grader response stops immediately with upstream fallback', async () => {
  let calls = 0;
  const result = await runFormativeMemoPanel({ ...base, complete: async () => { calls++; return undefined; } });
  assert.equal(calls, 1);
  assert.equal(result.kind, 'upstream'); assert.deepEqual(result.upstreamResult, { kind: 'upstream' });
});
for (const [label, value] of [['unparseable output', 'not JSON'], ['invalid scorecard', '{}']]) {
  test(`panel rejects ${label} before another grader`, async () => {
    let calls = 0;
    const result = await runFormativeMemoPanel({ ...base, complete: async () => { calls++; return { ok: true, text: value }; } });
    assert.equal(calls, 1); assert.equal(result.kind, 'validation'); assert.ok(result.errors.length > 0);
    if (label === 'unparseable output') assert.deepEqual(result.errors, ['unparseable grader output']);
  });
}
for (const override of [{ submission: '' }, { submission: 3 }, { instrument: null }, { graders: [] }, { graders: null }, { complete: null }]) {
  test(`panel input rejects ${JSON.stringify(override)} without calling completion`, async () => {
    assert.deepEqual(await runFormativeMemoPanel({ ...base, complete: () => assert.fail('must not call'), ...override }), { ok: false, kind: 'validation', errors: ['memo panel input invalid'] });
  });
}
test('panel ignores nonfinite or missing usage fields while preserving real token counts', async () => {
  const result = await runFormativeMemoPanel({ ...base, complete: async ({ grader }) => ({ ok: true, text: JSON.stringify(card()), usage: grader === graders[0] ? { input_tokens: Infinity, output_tokens: 2, cache_read_input_tokens: '4', cache_creation_input_tokens: NaN } : undefined }) });
  assert.equal(result.ok, true);
  assert.deepEqual(result.usage, { input_tokens: 0, output_tokens: 2, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 });
  assert.equal(result.result.adjudicator, undefined);
});
test('aggregate rejects an empty panel and mismatched validated headings', () => {
  for (const entries of [[], null, {}]) assert.throws(() => aggregateMemoPanel(entries), /at least one validated grader/);
  assert.throws(() => aggregateMemoPanel([{ grader: graders[0], scorecard: card() }, { grader: graders[1], scorecard: { headings: [] } }]), /validated scorecard missing/);
});
test('aggregate ignores noninteger adjudication and copies evidence arrays', () => {
  const entries = [2, 4].map((score, i) => ({ grader: graders[i], scorecard: card(score) }));
  for (const proposal of [null, '3', 3.5, NaN, Infinity]) {
    const result = aggregateMemoPanel(entries, { [ids[0]]: proposal });
    assert.equal(result.headings[0].score, 2);
    assert.deepEqual(result.headings[0].adjudication, { triggered: true });
    result.headings[0].observations[0].evidence_spans.push('mutation');
    assert.equal(entries[0].scorecard.headings[0].evidence_spans.length, 1);
  }
});
