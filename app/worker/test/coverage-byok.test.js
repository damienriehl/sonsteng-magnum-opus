import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveUpstream, resolvePanelUpstreams } from '../src/byok.js';
import { getProvider } from '../src/providers/registry.js';

const credentials = () => ['anthropic', 'openai', 'google'].map(provider => ({
  provider, api_key: `  fixture-${provider}-credential  `,
}));

test('panel rejects empty entries without substituting a configured hosted key', () => {
  for (const missing of [null, undefined]) {
    const panel = credentials();
    panel[1] = missing;
    const result = resolvePanelUpstreams({ ANTHROPIC_API_KEY: 'fixture-hosted' }, { byokPanel: panel });
    assert.equal(result.ok, false);
    assert.equal(result.status, 400);
    assert.match(result.message, /Every byok_panel entry/);
    assert.equal(result.graders, undefined);
  }
});

test('panel propagates invalid entry failures without leaking any credential', () => {
  for (const invalid of [{ provider: 'other', api_key: 'private-fixture' },
    { provider: 'openai', api_key: 'short' },
    { provider: 'google', api_key: 'private-fixture', model: 'not-allowed' }, false]) {
    const panel = credentials();
    panel[2] = invalid;
    const result = resolvePanelUpstreams({}, { byokPanel: panel });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'validation_error');
    assert.equal(result.status, 400);
    assert.doesNotMatch(JSON.stringify(result), /private-fixture|fixture-anthropic-credential/);
  }
});

test('model and credential validation is exact at type and whitespace boundaries', () => {
  const env = { MODEL_DEFAULT_OPENAI: 'custom', MODEL_ALLOW_OPENAI: 'custom, alternate ,,' };
  assert.equal(resolveUpstream(env, { provider: 'openai', api_key: ' 12345678 ', model: 'alternate' }).ok, true);
  for (const model of [0, {}, ' alternate', 'ALTERNATE', '']) {
    assert.equal(resolveUpstream(env, { provider: 'openai', api_key: '12345678', model }).ok, false);
  }
  assert.equal(resolveUpstream(env, { provider: 'openai', api_key: ' 1234567 ' }).ok, false);
});

test('resolved panel drives real registry adapters with isolated credentials and models', () => {
  const input = credentials();
  const before = structuredClone(input);
  const result = resolvePanelUpstreams({}, { byokPanel: input });
  assert.equal(result.ok, true);
  const expected = {
    anthropic: ['x-api-key', 'fixture-anthropic-credential', 'claude-haiku-4-5'],
    openai: ['authorization', 'Bearer fixture-openai-credential', 'gpt-4o-mini'],
    google: ['x-goog-api-key', 'fixture-google-credential', 'gemini-2.5-flash'],
  };
  for (const grader of result.graders) {
    const request = getProvider(grader.provider).buildRequest({
      system: 'Return JSON', messages: [{ role: 'user', content: 'Review this memo.' }],
      maxTokens: 100, providerCfg: { ...grader, jsonMode: true },
    });
    const [header, value, model] = expected[grader.provider];
    assert.equal(request.headers[header], value);
    assert.equal(grader.model, model);
    assert.equal(grader.skipBudget, true);
    assert.equal(grader.mode, 'byok');
    assert.doesNotMatch(request.url, /credential/);
    assert.doesNotMatch(JSON.stringify(request.body), /credential/);
    if (grader.provider === 'google') {
      assert.ok(request.url.endsWith(`${model}:generateContent`));
      assert.equal(request.body.generationConfig.responseMimeType, 'application/json');
    } else {
      assert.equal(request.body.model, model);
    }
  }
  assert.deepEqual(input, before);
});
