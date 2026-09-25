import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BudgetCounter, EditorStore, durableObject } from './coverage-runtime-helper.mjs';

const turn = { personaId: 'fixture.persona', pool: 'public', capPublicCents: 700,
  capDemoCents: 300, maxTurns: 3, reserveCents: 5, skipBudget: false, turnId: 't1' };
async function fixture(t, Class) {
  const result = await durableObject(Class);
  t.after(result.close);
  return result;
}

test('BudgetCounter initialization and failure RPCs execute real SQL refund accounting', async t => {
  const { object, sql } = await fixture(t, BudgetCounter);
  assert.equal(object.preflight('s', turn).ok, true);
  assert.equal(object.fail('s', { input_tokens: 10_000 }, 't1', turn.personaId).ok, true);
  assert.equal(object.committedTurnsForPersona('s', turn.personaId), 0);
  assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, 1);
  assert.equal(object.preflight('s', { ...turn, turnId: 't2' }).turn, 1);
  assert.equal(object.rollback('s', 't2', turn.personaId).ok, true);
  assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, 1);
});

test('BudgetCounter one-shot RPCs enforce validation, reserve, settle and replay', async t => {
  const { object, sql } = await fixture(t, BudgetCounter);
  assert.deepEqual(object.reserveOneShot('', turn), { ok: false, reason: 'validation_error' });
  assert.equal(object.reserveOneShot('one', turn).ok, true);
  assert.equal(object.settleOneShot('one', { input_tokens: 10_000 }, 2).ok, true);
  assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, 3);
  assert.equal(object.settleOneShot('one', { input_tokens: 10_000 }, 2).replay, true);
  assert.equal(sql.exec('SELECT public_cents FROM budget').toArray()[0].public_cents, 3);
  assert.equal(object.checkPool('public', 3, 300).ok, false);
  assert.equal(object.checkPool('demo', 3, 300).ok, true);
});

test('BudgetCounter assessment quota rejects invalid requests and counts each allowed attempt', async t => {
  const { object } = await fixture(t, BudgetCounter);
  for (const [sid, max] of [['', 1], ['s', 0], ['s', 1.5], [null, 1]]) {
    assert.deepEqual(object.claimAssessmentRequest(sid, max), { ok: false, reason: 'validation_error' });
  }
  assert.deepEqual(object.claimAssessmentRequest('s', 1), { ok: true, count: 1, remaining: 0 });
  assert.deepEqual(object.claimAssessmentRequest('s', 1), { ok: false, reason: 'rate_limited' });
});

function audit(id, days = 1) {
  const config = { source: 'default', version: '1' };
  const instrument = { id: 'fixture-instrument', version: '1', content_hash: 'fixture-hash' };
  return { id, assessment_use: 'formative', evidence: { submission: 'Synthetic fixture memo' },
    result: { assessment_use: 'formative', instrument, threshold_configuration: config,
      providers: [], summative_blockers: [] },
    provenance: { config, instrument, providers: [] }, summative_blockers: [], retention: { days } };
}
const scopes = { 'assessment-review': { granted: true, ver: 1 } };

test('EditorStore starts empty without scheduling an alarm and survives an empty alarm', async t => {
  const { object, alarms } = await fixture(t, EditorStore);
  assert.deepEqual(object.listAll(), []);
  assert.deepEqual(object.listForPage('missing'), []);
  assert.deepEqual(object.listForEditor('slot:fixture', null), []);
  assert.deepEqual(alarms, []);
  await object.alarm();
  assert.deepEqual(alarms, []);
  assert.deepEqual(object.expireAssessmentAudits(), { ok: true, deleted: 0 });
});

test('EditorStore audit RPC schedules earliest expiry and alarm removes child overrides atomically', async t => {
  const { object, sql, alarms } = await fixture(t, EditorStore);
  const now = Date.UTC(2026, 8, 19);
  object.core.now = () => now;
  const first = await object.recordAssessmentAudit(audit('first', 1));
  const second = await object.recordAssessmentAudit(audit('second', 2));
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.deepEqual(alarms, [first.expires_at, first.expires_at]);
  assert.equal(object.recordAssessmentOverride({ id: 'override', assessment_id: 'first', author: 'slot:reviewer',
    scopes, override: { rationale: 'Human review' } }).ok, true);
  assert.equal(object.readAssessmentAudit({ id: 'first', scopes }).record.overrides.length, 1);
  object.core.now = () => first.expires_at;
  await object.alarm();
  assert.equal(alarms.at(-1), second.expires_at);
  assert.equal(object.readAssessmentAudit({ id: 'first', scopes }).reason, 'not_found');
  assert.equal(object.readAssessmentAudit({ id: 'second', scopes }).ok, true);
  assert.equal(sql.exec('SELECT COUNT(*) AS n FROM assessment_audit_overrides').toArray()[0].n, 0);
});

test('invalid EditorStore audit writes do not schedule alarms or persist rows', async t => {
  const { object, sql, alarms } = await fixture(t, EditorStore);
  assert.deepEqual(await object.recordAssessmentAudit({}), { ok: false, reason: 'validation_error' });
  assert.deepEqual(await object.recordAssessmentAudit(audit('invalid', 0)), { ok: false, reason: 'retention_invalid' });
  assert.deepEqual(alarms, []);
  assert.equal(sql.exec('SELECT COUNT(*) AS n FROM assessment_audit_records').toArray()[0].n, 0);
});

test('EditorStore revert RPCs preserve terminal state through real persisted transitions', async t => {
  const { object } = await fixture(t, EditorStore);
  assert.equal(object.fileRevertRequest({ id: 'revert', editor: 'slot:john', doc: 'fixture.json',
    run_first: 'a'.repeat(40), run_last: 'b'.repeat(40) }).ok, true);
  assert.equal(object.listRevertRequests('requested').length, 1);
  assert.equal(object.resolveRevertRequest('revert', 'approved').ok, true);
  assert.equal(object.resolveRevertRequest('revert', 'done', 'Complete').ok, true);
  assert.equal(object.resolveRevertRequest('revert', 'requested').reason, 'already_terminal');
  const stored = object.listRevertRequests()[0];
  assert.equal(stored.status, 'done');
  assert.equal(stored.note, 'Complete');
});

test('EditorStore scoped-request RPCs claim once and expose persisted drafting state', async t => {
  const { object } = await fixture(t, EditorStore);
  assert.equal(object.fileScopedRequest({ id: 'scope', editor: 'slot:john', level: 'matter',
    matter: 'm01', instruction: 'Clarify the fixture wording', radius_blocks: 1 }).ok, true);
  assert.equal(object.claimScopedRequest('scope').ok, true);
  assert.equal(object.claimScopedRequest('scope').reason, 'not_claimable');
  const rows = object.listScopedRequests('drafting');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, 'scope');
  assert.equal(object.resolveScopedRequest('scope', { status: 'failed', note: 'Fixture failure' }).ok, true);
  assert.equal(object.listScopedRequests('failed')[0].note, 'Fixture failure');
});
