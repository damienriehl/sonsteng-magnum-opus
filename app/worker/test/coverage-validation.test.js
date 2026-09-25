import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateCritiqueScorecard, validateDebriefScorecard, parseModelJson, validateLearnerResultRequest, detectDebriefOracleLeak } from '../src/validate.js';
import { parseResponse } from '../src/providers/openai.js';

function critique() {
  return { schema_version: '1.0.0', matter_id: 'm00', rubric_id: 'm00.rub', criteria: [{ criterion_id: 'm00.rub.c01.s02.s03', score: 0, weight_points: 2.5, evidence: '', suggestions: '' }], total: { earned: 0, possible: 2.5 }, narrative: '', revise_resubmit_note: '' };
}

test('critique integrates provider response parsing, fenced JSON extraction and structural validation', () => {
  const expected = critique();
  const response = parseResponse({ choices: [{ message: { content: '```json\n' + JSON.stringify(expected) + '\n```' }, finish_reason: 'stop' }] });
  const parsed = parseModelJson(response.text);
  assert.deepEqual(parsed, expected);
  assert.deepEqual(validateCritiqueScorecard(parsed), { ok: true, errors: [] });
});

const invalidCritiques = [
  ['schema version', o => { delete o.schema_version; }, 'schema_version invalid'],
  ['matter id', o => { o.matter_id = 'm0'; }, 'matter_id invalid'],
  ['rubric id', o => { o.rubric_id = 'm00.rub.c01'; }, 'rubric_id invalid'],
  ['missing criteria', o => { delete o.criteria; }, 'criteria must be a non-empty array'],
  ['empty criteria', o => { o.criteria = []; }, 'criteria must be a non-empty array'],
  ['nonobject criterion', o => { o.criteria = [null]; }, 'criteria[0] must be an object'],
  ['criterion reference', o => { o.criteria[0].criterion_id = 'm00.rub.c1'; }, 'criteria[0].criterion_id invalid'],
  ['negative score', o => { o.criteria[0].score = -0.1; }, 'criteria[0].score invalid'],
  ['infinite score', o => { o.criteria[0].score = Infinity; }, 'criteria[0].score invalid'],
  ['string score', o => { o.criteria[0].score = '1'; }, 'criteria[0].score invalid'],
  ['negative weight', o => { o.criteria[0].weight_points = -1; }, 'criteria[0].weight_points invalid'],
  ['NaN weight', o => { o.criteria[0].weight_points = NaN; }, 'criteria[0].weight_points invalid'],
  ['nonstring evidence', o => { o.criteria[0].evidence = []; }, 'criteria[0].evidence must be a string'],
  ['missing suggestions', o => { delete o.criteria[0].suggestions; }, 'criteria[0].suggestions must be a string'],
  ['missing total', o => { delete o.total; }, 'total missing'],
  ['invalid earned total', o => { o.total.earned = -1; }, 'total.earned invalid'],
  ['infinite possible total', o => { o.total.possible = Infinity; }, 'total.possible invalid'],
  ['nonstring narrative', o => { o.narrative = null; }, 'narrative must be a string'],
  ['nonstring resubmit note', o => { o.revise_resubmit_note = false; }, 'revise_resubmit_note must be a string'],
];
for (const [name, mutate, error] of invalidCritiques) test(`critique rejects ${name} with an actionable field error`, () => {
  const value = critique(); mutate(value);
  assert.deepEqual(validateCritiqueScorecard(value), { ok: false, errors: [error] });
});

test('both scorecard validators reject nonobject roots', () => {
  for (const validate of [validateCritiqueScorecard, validateDebriefScorecard]) {
    for (const value of [null, [], 'text', 7, false]) assert.deepEqual(validate(value), { ok: false, errors: ['root must be an object'] });
  }
});

test('model JSON extraction handles surrounding prose and fails closed on competing objects', () => {
  assert.deepEqual(parseModelJson('Result follows: {"ok":true} End.'), { ok: true });
  assert.deepEqual(parseModelJson('```\n{"ok":true}\n```'), { ok: true });
  for (const value of [undefined, {}, '', 'not JSON', '{broken}', 'first {"a":1} second {"b":2}']) assert.equal(parseModelJson(value), null);
});

for (const field of ['alumni_assessor', 'alumni_reviewer', 'alumni_recipient', 'alumni_notification', 'alumni_feedback_destination']) test(`learner result rejects explicit ${field} even when null`, () => {
  assert.deepEqual(validateLearnerResultRequest({ [field]: null }), { ok: false, error: 'Alumni routing fields are not supported.' });
});

test('learner result accepts an empty request but rejects nonobjects', () => {
  assert.deepEqual(validateLearnerResultRequest({}), { ok: true });
  for (const value of [null, [], '']) assert.equal(validateLearnerResultRequest(value).ok, false);
});

test('oracle detector tolerates malformed optional fields without overlooking a later valid flag', () => {
  const text = 'This concealed sentence describes a distinctive undisclosed circumstance.';
  const persona = { disclosure: { concealed: [null, { text: 1 }, { text }] } };
  const score = { axis_a: { facts_elicited: [null], rule_4_2_flags: [null, 1, text] }, axis_b: { rapport_opening: null, listening_t_funnel: { comment: 1 } }, narrative: null };
  assert.equal(detectDebriefOracleLeak(score, persona, {}), 'axis_a.rule_4_2_flags[2]');
  assert.equal(detectDebriefOracleLeak(null, persona, {}), null);
  assert.equal(detectDebriefOracleLeak({ axis_a: {} }, undefined, undefined), null);
});

function debrief() {
  return { schema_version: '1.0.0', matter_id: 'm00', persona_id: 'm00.per.test', axis_a: { facts_elicited: [], revealed_if_asked_missed: [], rapport_gated_unearned: [], rule_4_2_flags: [] }, axis_b: Object.fromEntries(['rapport_opening', 'listening_t_funnel', 'understanding_goals', 'explanation_next_steps', 'overall_confidence'].map(key => [key, { score: 10, comment: '' }])), ethics_score: 2, narrative: '', self_reflection_prompt: '' };
}

const invalidDebriefs = [
  ['persona ref', o => { o.persona_id = 'm00.per.UPPER'; }, 'persona_id invalid'],
  ['missing axis A', o => { o.axis_a = null; }, 'axis_a missing'],
  ['fact reference', o => { o.axis_a.facts_elicited = ['m00.fact.1']; }, 'axis_a.facts_elicited must be fact_ref[]'],
  ['missed topic type', o => { o.axis_a.revealed_if_asked_missed = [1]; }, 'axis_a.revealed_if_asked_missed must be string[]'],
  ['missing rapport array', o => { o.axis_a.rapport_gated_unearned = {}; }, 'axis_a.rapport_gated_unearned must be array'],
  ['invalid rapport trigger', o => { o.axis_a.rapport_gated_unearned = [{ topic: 'Timeline', trigger_needed: 'invented' }]; }, 'axis_a.rapport_gated_unearned[0] invalid'],
  ['nonobject rapport item', o => { o.axis_a.rapport_gated_unearned = [null]; }, 'axis_a.rapport_gated_unearned[0] invalid'],
  ['nonstring rule flags', o => { o.axis_a.rule_4_2_flags = [false]; }, 'axis_a.rule_4_2_flags must be string[]'],
  ['missing axis B', o => { o.axis_b = []; }, 'axis_b missing'],
  ['missing rating', o => { delete o.axis_b.rapport_opening; }, 'axis_b.rapport_opening must be an object'],
  ['fractional rating', o => { o.axis_b.rapport_opening.score = 0.5; }, 'axis_b.rapport_opening.score must be int 0-10'],
  ['rating above max', o => { o.axis_b.rapport_opening.score = 11; }, 'axis_b.rapport_opening.score must be int 0-10'],
  ['rating below min', o => { o.axis_b.rapport_opening.score = -1; }, 'axis_b.rapport_opening.score must be int 0-10'],
  ['nonstring rating comment', o => { o.axis_b.rapport_opening.comment = false; }, 'axis_b.rapport_opening.comment must be a string'],
  ['ethics above max', o => { o.ethics_score = 3; }, 'ethics_score must be int -2..2'],
  ['ethics below min', o => { o.ethics_score = -3; }, 'ethics_score must be int -2..2'],
  ['fractional ethics', o => { o.ethics_score = 0.5; }, 'ethics_score must be int -2..2'],
  ['missing narrative', o => { delete o.narrative; }, 'narrative must be a string'],
  ['missing reflection', o => { delete o.self_reflection_prompt; }, 'self_reflection_prompt must be a string'],
];
for (const [name, mutate, error] of invalidDebriefs) test(`debrief structure rejects ${name}`, () => {
  const value = debrief(); mutate(value);
  assert.deepEqual(validateDebriefScorecard(value), { ok: false, errors: [error] });
});

test('debrief rating and ethics boundaries accept empty feedback and disclosure arrays', () => {
  const value = debrief();
  assert.deepEqual(validateDebriefScorecard(value), { ok: true, errors: [] });
  value.ethics_score = -2;
  for (const rating of Object.values(value.axis_b)) rating.score = 0;
  assert.deepEqual(validateDebriefScorecard(value), { ok: true, errors: [] });
});
