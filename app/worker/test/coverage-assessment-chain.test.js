import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildAssessmentAuditInput, persistAssessmentAudit } from '../src/assessment-audit.js';
import { assessmentPageEndpoint, assessmentOverrideEndpoint } from '../src/assessment-endpoints.js';
import { assessmentViewModel, renderAssessmentReviewPage } from '../src/assessment-view.js';
import { resolveAssessmentThresholdConfig, validResolvedAssessmentThresholdConfig } from '../src/assessment-config.js';
import { EditorStore, durableObject } from './coverage-runtime-helper.mjs';
import bundle from '../personas/personas.generated.json' with { type: 'json' };

const instrument = bundle.assessment_instrument;
const config = resolveAssessmentThresholdConfig(undefined, instrument).config;
const origin = 'https://editor.example.test';
const auth = { slot: 'damienadmin', editor: 'slot:damienadmin', credential_channel: 'access',
  scopes: { admin: { granted: true }, instructor: { granted: true } } };
const scoped = { 'assessment-review': { granted: true, ver: 1 } };
function input(extra = {}) {
  return buildAssessmentAuditInput({ id: 'audit-chain', submission: 'Synthetic memo evidence.', instrument,
    result: { assessment_use: 'formative', instrument: { id: instrument.id,
      version: instrument.instrument_version, content_hash: instrument.content_hash },
    threshold_configuration: config, providers: [], summative_blockers: [],
    headings: [{ heading_id: 'issues', score: 3, observations: [{}] }] }, ...extra });
}
async function fixture(t) {
  const fixture = await durableObject(EditorStore);
  t.after(fixture.close);
  return { ...fixture, env: { EDIT_ORIGIN: origin, EDITOR: { getByName(name) {
    assert.equal(name, 'global-v1'); return fixture.object;
  } } } };
}
function override(body) {
  return new Request(origin + '/edit/v1/assessment-override', { method: 'POST',
    headers: { Origin: origin, 'X-Edit-Request': '1', 'Content-Type': 'application/json' },
    body: typeof body === 'string' ? body : JSON.stringify(body) });
}

test('audit builder normalizes retention limits and deduplicates only usable credential values', () => {
  for (const [retentionDays, expected] of [[undefined, 30], ['invalid', 30], [0, 30], [366, 30], [1, 1], ['365', 365]]) {
    const record = input({ retentionDays, sessionToken: 'fixture-secret',
      graders: [null, {}, { apiKey: 'abc' }, { apiKey: 'fixture-secret' }, { apiKey: 'second-fixture-secret' }] });
    assert.equal(record.retention.days, expected);
    assert.deepEqual(record.credential_values, ['fixture-secret', 'second-fixture-secret']);
    assert.doesNotMatch(JSON.stringify(record.evidence), /fixture-secret/);
  }
});

test('audit builder tolerates absent result metadata without inventing provenance', () => {
  const record = input({ result: undefined, sessionToken: 1, graders: null });
  assert.deepEqual(record.provenance.providers, []);
  assert.deepEqual(record.summative_blockers, []);
  assert.deepEqual(record.credential_values, []);
  assert.equal(record.provenance.config, undefined);
});

test('audit builder, persistence wrapper, SQLite and read view form a real evidence chain', async t => {
  const { env, object } = await fixture(t);
  const written = await persistAssessmentAudit(env, input());
  assert.equal(written.ok, true);
  assert.equal(written.assessment_audit_id, 'audit-chain');
  const stored = object.readAssessmentAudit({ id: written.assessment_audit_id, scopes: scoped });
  assert.equal(stored.record.evidence.submission, 'Synthetic memo evidence.');
  assert.equal(stored.record.provenance.config.source, 'default');
  const response = await assessmentPageEndpoint(new Request(origin + '/edit/assessments/audit-chain/'), env, auth);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /Synthetic memo evidence/);
});

test('audit persistence propagates actual storage validation failures without success identifiers', async t => {
  const { env } = await fixture(t);
  assert.deepEqual(await persistAssessmentAudit(env, {}), { ok: false, reason: 'validation_error' });
});

test('assessment page rejects malformed percent encoding and unrelated paths uniformly', async t => {
  const { env } = await fixture(t);
  for (const path of ['/edit/assessments/%zz', '/different/path', '/edit/assessments/missing']) {
    const response = await assessmentPageEndpoint(new Request(origin + path), env, auth);
    assert.equal(response.status, 404);
  }
});

test('assessment override rejects malformed JSON, empty notes and out-of-range scores', async t => {
  const { env } = await fixture(t);
  const base = { id: 'override-1', assessment_id: 'audit-chain', heading_id: 'issues', score: 4, note: 'Reason' };
  for (const body of ['{', { ...base, note: ' ' }, { ...base, note: 'x'.repeat(4001) },
    { ...base, score: 0 }, { ...base, score: 8 }, { ...base, heading_id: 'unknown' }]) {
    assert.equal((await assessmentOverrideEndpoint(override(body), env, auth)).status, 400);
  }
});

test('assessment override reports missing parents and real id conflicts without altering the prior decision', async t => {
  const { env, object } = await fixture(t);
  const body = { id: 'override-1', assessment_id: 'audit-chain', heading_id: 'issues', score: 4, note: 'Reason' };
  assert.equal((await assessmentOverrideEndpoint(override(body), env, auth)).status, 404);
  assert.equal((await persistAssessmentAudit(env, input())).ok, true);
  assert.equal((await assessmentOverrideEndpoint(override(body), env, auth)).status, 200);
  const conflict = await assessmentOverrideEndpoint(override({ ...body, score: 5 }), env, auth);
  assert.equal(conflict.status, 409);
  assert.equal((await conflict.json()).error.code, 'id_conflict');
  const record = object.readAssessmentAudit({ id: 'audit-chain', scopes: scoped }).record;
  assert.equal(record.overrides.length, 1);
  assert.equal(record.overrides[0].value.score, 4);
  const page = await renderAssessmentReviewPage(record, 'Reviewer').text();
  assert.match(page, /Human override by DR/);
  assert.match(page, /The derived score was 3/);
  assert.match(page, /Reason/);
});

test('assessment view has a usable empty state and canonical default thresholds', async () => {
  const vm = assessmentViewModel();
  assert.deepEqual(vm.headings, []);
  assert.equal(vm.competence_score, 4);
  assert.equal(vm.redo_eligible_below, 6);
  assert.match(await renderAssessmentReviewPage({}).text(), /No human overrides have been recorded/);
});

test('assessment view ignores invalid overrides and applies the latest valid heading decision', () => {
  const record = { result: { headings: [{ heading_id: 'issues', score: 2 }] }, overrides: [
    null, {}, { value: { heading_id: 'issues', score: 0 } },
    { value: { heading_id: 'issues', score: 8 } }, { value: { heading_id: 'issues', score: '6' } },
    { value: { heading_id: 'issues', score: 4 } }, { value: { heading_id: 'issues', score: 7 } },
  ] };
  const before = structuredClone(record);
  const heading = assessmentViewModel(record).headings[0];
  assert.equal(heading.base_score, 2);
  assert.equal(heading.score, 7);
  assert.equal(heading.competent, true);
  assert.equal(heading.redo_eligible, false);
  assert.deepEqual(record, before);
});

test('assessment view removes legacy translations recursively while escaping hostile evidence', async () => {
  const attack = '</pre><script>alert(1)</script>';
  const record = { evidence: { nested: [{ 'LETTER-GRADE': 'secret grade', keep: attack }, null, 4] },
    result: { headings: [{ heading_id: 'some__heading', score: 1, observations: [{}] }] },
    provenance: { config: { locally_supplied: true } } };
  const vm = assessmentViewModel(record);
  assert.deepEqual(vm.evidence.nested, [{ keep: attack }, null, 4]);
  assert.equal(vm.headings[0].label, 'Some Heading');
  const html = await renderAssessmentReviewPage(record, attack).text();
  assert.doesNotMatch(html, /<script>alert|secret grade/);
  assert.match(html, /source not identified/);
  assert.match(html, /Not yet competent/);
});

test('resolved threshold validation rejects authority and local-source inconsistencies', () => {
  for (const patch of [{ locally_supplied: true }, { authority_status: 'claimed_locally_supplied' },
    { verified_institutional_authority: true }, { source_id: 'wrong-instrument' }, { competence_score: 1 }]) {
    assert.equal(validResolvedAssessmentThresholdConfig({ ...config, ...patch }, instrument), false);
  }
});

test('locally resolved thresholds propagate through audit construction and the assessment view', () => {
  const resolved = resolveAssessmentThresholdConfig({ schema_version: 'memo-assessment-threshold-config/v1',
    instructor: { id: 'fixture-instructor', competence_score: 5, redo_eligible_below: 7 } }, instrument);
  assert.equal(resolved.ok, true);
  const original = input();
  const record = input({ result: { ...original.result, threshold_configuration: resolved.config,
    headings: [{ heading_id: 'issues', score: 4 }] } });
  const view = assessmentViewModel(record);
  assert.equal(view.competence_score, 5);
  assert.equal(view.redo_eligible_below, 7);
  assert.equal(view.headings[0].competent, false);
  assert.equal(view.headings[0].redo_eligible, true);
});
