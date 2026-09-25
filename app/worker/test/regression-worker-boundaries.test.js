import { test } from 'node:test';
import assert from 'node:assert/strict';
import { serveAsset } from '../src/editor-assets.js';
import { centsForUsage } from '../src/cost.js';
import { parseResponse } from '../src/providers/google.js';
import { worker, BudgetCounter, durableObject } from './coverage-runtime-helper.mjs';

for (const name of ['constructor', '__proto__', 'toString', 'hasOwnProperty']) {
  test(`asset router rejects inherited property ${name}`, async () => {
    assert.equal(serveAsset(name), null);
    const response = await worker.fetch(new Request(`https://editor.example.test/edit/assets/${name}`), {}, {});
    assert.equal(response.status, 404);
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  });
}

for (const value of [-100_000, Infinity, -Infinity, NaN, '2000', true, {}, 1.5, Number.MAX_VALUE]) {
  test(`billing ignores invalid token count ${String(value)}`, () => {
    for (const field of ['input_tokens', 'output_tokens', 'thought_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']) {
      assert.equal(centsForUsage({ [field]: value }), 0, field);
    }
    assert.equal(centsForUsage({ output_tokens: value, thought_tokens: 2000 }), 1);
  });
}

test('Google usage normalization excludes invalid cache, output and prompt counts', () => {
  for (const invalid of [-20, Infinity, '20', true, 1.5]) {
    const result = parseResponse({ usageMetadata: { promptTokenCount: 10000, cachedContentTokenCount: invalid, candidatesTokenCount: invalid } });
    assert.deepEqual(result.usage, { input_tokens: 10000, output_tokens: 0, cache_read_input_tokens: 0 });
    const badPrompt = parseResponse({ usageMetadata: { promptTokenCount: invalid } });
    assert.equal(badPrompt.usage.input_tokens, 0);
  }
});

test('malformed provider usage cannot credit or corrupt real budget balances', async t => {
  const { object, sql, close } = await durableObject(BudgetCounter);
  t.after(close);
  object.charge('public', { input_tokens: 100000 });
  object.charge('public', { output_tokens: -100000, cache_read_input_tokens: Infinity });
  const response = parseResponse({ usageMetadata: { promptTokenCount: 10000, cachedContentTokenCount: -10000, candidatesTokenCount: '2000', thoughtsTokenCount: 2000 } });
  assert.equal(object.charge('public', response.usage).actualCents, 2);
  assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, 12);
  assert.equal(object.checkPool('public', 12, 300).ok, false);
});

test('settlement refunds a malformed usage reserve without undoing prior spending', async t => {
  const { object, sql, close } = await durableObject(BudgetCounter);
  t.after(close);
  object.charge('public', { input_tokens: 100000 });
  const options = { personaId: 'fixture.persona', pool: 'public', capPublicCents: 700,
    capDemoCents: 300, maxTurns: 3, reserveCents: 5, skipBudget: false, turnId: 'turn' };
  assert.equal(object.preflight('session', options).ok, true);
  assert.equal(object.settle('session', { output_tokens: -100000 }, 'turn', { text: 'answer' }).actualCents, 0);
  assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, 10);
  assert.equal(object.committedTurnsForPersona('session', options.personaId), 1);
});
