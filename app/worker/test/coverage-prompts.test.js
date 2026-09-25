import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderPersona, buildSystemPrompt, buildTierData, buildDebriefPrompt,
  buildCritiquePrompt, rubricCriteriaLabels } from '../src/prompts.js';
import { buildRequest } from '../src/providers/anthropic.js';
import bundle from '../personas/personas.generated.json' with { type: 'json' };

function persona() {
  return { identity: { name: 'Fixture Person', role: 'client' }, background: 'Fixture background',
    personality: 'Cautious', emotional_state: 'Calm', communication_style: 'Brief', disposition: 'cooperative',
    knowledge_boundary: { unknown_response_style: 'I do not know.' } };
}

test('minimal persona omits optional identity and fear details without losing disclosure boundaries', () => {
  const rendered = renderPersona(persona());
  assert.match(rendered, /You are Fixture Person\. In this matter you are the client\./);
  assert.doesNotMatch(rendered, /Your pronouns|What you are afraid of|harmless texture/);
  assert.equal(rendered.match(/\(nothing in this tier\)/g).length, 5);
  assert.ok(rendered.endsWith('Stay this person. Speak only as them.'));
});

for (const [item, expected] of [
  [{ text: 'Sensitive fact', min_turns: 0 }, 'at least 0 turns have passed.'],
  [{ text: 'Sensitive fact', requires: ['custom_trigger'] }, 'genuinely custom_trigger.'],
  [{ text: 'Sensitive fact', min_turns: 2, requires: ['custom_trigger'] }, 'at least 2 turns have passed AND'],
]) {
  test(`rapport prompt preserves the configured gate: ${expected}`, () => {
    const p = persona(); p.disclosure = { rapport_gated: [item] };
    assert.ok(renderPersona(p).includes(expected));
  });
}

test('represented persona without counsel name still carries the representation guard', () => {
  const p = persona(); p.rule_4_2 = { applies: true };
  assert.match(renderPersona(p), /You are represented by your own lawyer, and the person/);
});

test('tier data handles absent maps and empty disclosure with explicit ground-truth markers', () => {
  const empty = buildTierData({}, undefined);
  assert.equal(empty.match(/\(none\)/g).length, 5);
  const p = { disclosure: { rapport_gated: [{ fact_ref: 'fixture.1', text: 'fact', min_turns: 0, requires: [] }] } };
  assert.match(buildTierData(p, {}), /topic_label: \(unlabeled topic\).*min_turns=0/);
});

test('debrief prompt represents missing transcript and counsel with explicit boolean fields', () => {
  const result = buildDebriefPrompt('{{RULE_4_2}}\n{{TRANSCRIPT}}\n{{DISPOSITION}}', {
    matterId: 'm00', personaId: 'fixture', persona: {}, interviewerOnOpposingSide: true,
  });
  assert.equal(result, 'represented_by_counsel: false\nrule_4_2.applies: false\ninterviewer_on_opposing_side: true\n\n');
});

test('critique template accepts pre-serialized rubrics and preserves literal dollar syntax', () => {
  const text = buildCritiquePrompt('{{MATTER_ID}} {{RUBRIC_ID}} {{RUBRIC_JSON}} {{DELIVERABLE}} {{DELIVERABLE}}', {
    matterId: 'm01', rubricId: 'm01.rub', rubric: '{"raw":true}', deliverable: '$& $1 <memo>',
  });
  assert.equal(text, 'm01 m01.rub {"raw":true} $& $1 <memo> $& $1 <memo>');
});

test('rubric labels ignore malformed entries but retain valid nested labels', () => {
  assert.deepEqual(rubricCriteriaLabels(null), {});
  assert.deepEqual(rubricCriteriaLabels({ criteria: [null, {}, { id: 1, name: 'bad', subcriteria: [
    null, { id: 'valid', name: 'Nested' }, { id: 'bad', name: 3 },
  ] }] }), { valid: 'Nested' });
});

test('real bundled persona prompt reaches the provider request without altering the cache prefix', () => {
  const p = bundle.personas['m00.per.tester'];
  const tail = renderPersona(p);
  const full = buildSystemPrompt(bundle.segment_a, p);
  const request = buildRequest({ system: { prefix: bundle.segment_a, tail },
    messages: [{ role: 'user', content: 'Tell me why you came today.' }], maxTokens: 300,
    providerCfg: { apiKey: 'fixture-only', model: 'fixture-model' } });
  assert.equal(request.body.system[0].text + request.body.system[1].text + '\n', full);
  assert.deepEqual(request.body.system[0].cache_control, { type: 'ephemeral' });
  assert.equal(request.body.messages[0].content[0].text, 'Tell me why you came today.');
  assert.doesNotMatch(tail, /m00\.fact\.\d+/);
});
