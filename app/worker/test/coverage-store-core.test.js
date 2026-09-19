import { test } from 'node:test';
import assert from 'node:assert/strict';
import { NodeSql, makeCore } from './editor-sql-helper.mjs';
import { EditorStoreCore } from '../src/editor-store-core.js';
import { durableObject, EditorStore } from './coverage-runtime-helper.mjs';

const scopes = { 'assessment-review': { granted: true } };
const day = 86400000;
function audit(id = 'audit') {
  const config = { source: 'instructor', version: '1' };
  const instrument = { id: 'memo', version: '1', content_hash: 'hash' };
  return { id, assessment_use: 'formative', evidence: { submission: 'Original memo' },
    result: { assessment_use: 'formative', instrument: { ...instrument },
      providers: [], threshold_configuration: { ...config }, summative_blockers: [] },
    provenance: { config, instrument, providers: [] }, summative_blockers: [], retention: { days: 1 } };
}
function override(assessment_id = 'audit', id = 'override') {
  return { id, assessment_id, author: 'slot:reviewer', scopes, override: { score: 5 } };
}
function suggestion(id, extra = {}) {
  return { id, editor: 'slot:author', scope: 'edit', origin: 'human', kind: 'prose',
    page: 'page.html', block_anchor: 'page.html:1', source_ref: `doc#${id}`,
    original_text: 'Before', original_hash: 'hash', new_text: 'After', map_version: 'v1', ...extra };
}
function database(t, now = () => 1000) {
  const core = makeCore(now);
  t.after(() => core.sql.db.close());
  return core;
}
function count(core, table) { return core.sql.exec(`SELECT COUNT(*) AS n FROM ${table}`).toArray()[0].n; }

const invalidAudits = [
  ['invalid identifier', x => { x.id = '../audit'; }, 'validation_error'],
  ['array evidence', x => { x.evidence = []; }, 'validation_error'],
  ['array result', x => { x.result = []; }, 'validation_error'],
  ['absent provenance', x => { delete x.provenance; }, 'validation_error'],
  ['absent retention', x => { delete x.retention; }, 'retention_required'],
  ['fractional retention', x => { x.retention.days = 1.5; }, 'retention_invalid'],
  ['unbounded retention', x => { x.retention.days = Infinity; }, 'retention_invalid'],
  ['unknown assessment use', x => { x.assessment_use = 'other'; }, 'assessment_contract_invalid'],
  ['different result use', x => { x.result.assessment_use = 'summative'; }, 'assessment_contract_invalid'],
  ['missing blocker list', x => { delete x.summative_blockers; }, 'assessment_contract_invalid'],
  ['missing result blockers', x => { delete x.result.summative_blockers; }, 'assessment_contract_invalid'],
  ['empty provenance source', x => { x.provenance.config.source = ''; }, 'provenance_required'],
  ['empty config version', x => { x.provenance.config.version = ''; }, 'provenance_required'],
  ['nonarray providers', x => { x.provenance.providers = {}; }, 'provenance_required'],
  ['absent result instrument', x => { delete x.result.instrument; }, 'instrument_provenance_mismatch'],
  ['short credential material', x => { x.evidence.api_key = 'abc'; }, 'credential_material_invalid'],
  ['provider mismatch', x => { x.provenance.providers = [{ provider: 'example' }]; }, 'provider_provenance_mismatch'],
  ['blocker mismatch', x => { x.summative_blockers = ['human_review']; }, 'summative_blockers_mismatch'],
];
for (const [name, mutate, reason] of invalidAudits) {
  test(`audit validation rejects ${name} without partial persistence`, t => {
    const core = database(t);
    const input = audit(); mutate(input);
    assert.deepEqual(core.recordAssessmentAudit(input), { ok: false, reason });
    assert.equal(count(core, 'assessment_audit_records'), 0);
    assert.equal(core.nextAssessmentAuditExpiry(), null);
    assert.equal(core.recordAssessmentAudit(audit()).ok, true);
    assert.equal(core.readAssessmentAudit({ id: 'audit', scopes }).record.evidence.submission, 'Original memo');
  });
}

test('audit expiry rejects an unsafe integer deadline without writing a record', t => {
  const core = database(t, () => Number.MAX_SAFE_INTEGER);
  assert.deepEqual(core.recordAssessmentAudit(audit()), { ok: false, reason: 'retention_invalid' });
  assert.equal(count(core, 'assessment_audit_records'), 0);
});

for (const operation of ['read', 'override', 'replace']) {
  test(`lazy audit expiry through ${operation} atomically removes dependent overrides`, t => {
    let now = 1000;
    const core = database(t, () => now);
    const written = core.recordAssessmentAudit(audit());
    assert.equal(core.recordAssessmentOverride(override()).ok, true);
    now = written.expires_at;
    if (operation === 'read') assert.deepEqual(core.readAssessmentAudit({ id: 'audit', scopes }), { ok: false, reason: 'not_found' });
    if (operation === 'override') assert.deepEqual(core.recordAssessmentOverride(override('audit', 'late')), { ok: false, reason: 'not_found' });
    if (operation === 'replace') {
      const changed = audit(); changed.evidence.submission = 'Replacement memo';
      const replacement = core.recordAssessmentAudit(changed);
      assert.equal(replacement.ok, true);
      assert.equal(replacement.replay, undefined);
      assert.equal(replacement.expires_at, now + day);
      assert.equal(core.readAssessmentAudit({ id: 'audit', scopes }).record.evidence.submission, 'Replacement memo');
    }
    assert.equal(count(core, 'assessment_audit_overrides'), 0);
    assert.equal(count(core, 'assessment_audit_records'), operation === 'replace' ? 1 : 0);
  });
}

for (const [name, change, reason] of [
  ['invalid id', x => { x.id = '/bad'; }, 'validation_error'],
  ['invalid parent id', x => { x.assessment_id = '/bad'; }, 'validation_error'],
  ['invalid author', x => { x.author = 'spaces are invalid'; }, 'validation_error'],
  ['array override', x => { x.override = []; }, 'validation_error'],
  ['missing parent', x => { x.assessment_id = 'missing'; }, 'not_found'],
  ['short credential', x => { x.override.api_key = 'abc'; }, 'credential_material_invalid'],
  ['only credential fields', x => { x.override = { api_key: 'fake-fixture-credential' }; }, 'validation_error'],
]) {
  test(`override rejects ${name} and preserves the original audit`, t => {
    const core = database(t); core.recordAssessmentAudit(audit());
    const input = override(); change(input);
    assert.deepEqual(core.recordAssessmentOverride(input), { ok: false, reason });
    assert.deepEqual(core.readAssessmentAudit({ id: 'audit', scopes }).record.overrides, []);
  });
}

test('invalid and missing audit IDs share a not-found response after scope validation', t => {
  const core = database(t);
  for (const id of [undefined, '', '../audit', 'missing'])
    assert.deepEqual(core.readAssessmentAudit({ id, scopes }), { ok: false, reason: 'not_found' });
  assert.deepEqual(core.readAssessmentAudit({ id: 'audit', scopes: { 'assessment-review': { granted: 1 } } }),
    { ok: false, reason: 'assessment_review_scope_required' });
});

test('fallback transaction commits deletion and rolls back child deletion on a real SQL constraint failure', t => {
  const sql = new NodeSql(); t.after(() => sql.db.close());
  const core = new EditorStoreCore(sql, () => 1000); core.initSchema();
  core.recordAssessmentAudit(audit()); core.recordAssessmentOverride(override());
  sql.db.exec("CREATE TRIGGER prevent_audit_delete BEFORE DELETE ON assessment_audit_records BEGIN SELECT RAISE(ABORT, 'fixture retention failure'); END;");
  assert.throws(() => core._deleteAssessmentAudits(['audit']), /fixture retention failure/);
  assert.equal(count(core, 'assessment_audit_records'), 1);
  assert.equal(count(core, 'assessment_audit_overrides'), 1);
  sql.db.exec('DROP TRIGGER prevent_audit_delete');
  core._deleteAssessmentAudits(['audit']);
  core._deleteAssessmentAudits([]);
  assert.equal(count(core, 'assessment_audit_records'), 0);
  assert.equal(count(core, 'assessment_audit_overrides'), 0);
});

for (const phase of ['absent', 'done', 'rolled_back', 'live']) {
  test(`reconciliation handles an expired suggestion with ${phase} batch and is idempotent`, t => {
    const core = database(t);
    core.suggest(suggestion('orphan'));
    core.decide({ id: 'orphan', decision: 'accept' });
    assert.equal(core.claimBatch('batch', { ids: ['orphan'] }).ok, true);
    core.sql.exec('UPDATE suggestions SET lease_expires_at=NULL WHERE id=?', 'orphan');
    if (phase === 'absent') core.sql.exec('DELETE FROM apply_batches WHERE batch_id=?', 'batch');
    else if (phase !== 'live') core.sql.exec('UPDATE apply_batches SET phase=? WHERE batch_id=?', phase, 'batch');
    const swept = core.reconcile();
    assert.deepEqual(swept.rolled_back, phase === 'live' ? [] : ['orphan']);
    assert.equal(core._get('orphan').status, phase === 'live' ? 'in_flight' : 'accepted');
    assert.equal(core._get('orphan').apply_batch_id, phase === 'live' ? 'batch' : null);
    assert.deepEqual(core.reconcile().rolled_back, []);
  });
}

test('EditorStore RPC writes, filters by owner and page, and retains a declined closure', async t => {
  const runtime = await durableObject(EditorStore); t.after(runtime.close);
  const store = runtime.object;
  assert.deepEqual(store.listForEditor('slot:author', 'page.html'), []);
  assert.equal(store.suggest(suggestion('first')).ok, true);
  store.suggest(suggestion('second', { source_ref: 'doc#first' }));
  store.suggest(suggestion('other-page', { page: 'other.html' }));
  store.suggest(suggestion('other-author', { editor: 'slot:other' }));
  store.decide({ id: 'second', decision: 'decline' });
  const rows = store.listForEditor('slot:author', 'page.html');
  assert.deepEqual(rows.map(row => row.id), ['second']);
  assert.equal(rows[0].status, 'declined');
  assert.deepEqual(new Set(store.listForEditor('slot:author').map(row => row.id)), new Set(['second', 'other-page']));
});

test('EditorStore audit RPC schedules earliest expiry and its alarm preserves later audit records', async t => {
  const runtime = await durableObject(EditorStore); t.after(runtime.close);
  const store = runtime.object;
  let now = 1000; store.core.now = () => now;
  const first = await store.recordAssessmentAudit(audit());
  const later = audit('later'); later.retention.days = 2;
  assert.equal((await store.recordAssessmentAudit(later)).ok, true);
  assert.deepEqual(runtime.alarms, [first.expires_at, first.expires_at]);
  store.recordAssessmentOverride(override());
  now = first.expires_at;
  await store.alarm();
  assert.equal(runtime.alarms.at(-1), 1000 + 2 * day);
  assert.deepEqual(store.readAssessmentAudit({ id: 'audit', scopes }), { ok: false, reason: 'not_found' });
  assert.equal(store.readAssessmentAudit({ id: 'later', scopes }).ok, true);
  assert.equal(runtime.sql.exec('SELECT COUNT(*) AS n FROM assessment_audit_overrides').toArray()[0].n, 0);
  now += day; await store.alarm();
  assert.equal(store.core.nextAssessmentAuditExpiry(), null);
  assert.equal(runtime.alarms.length, 3);
});

function appliedBatch(core, batchId, ids, group = null) {
  for (const id of ids) assert.equal(core.suggest(suggestion(id, { group_id: group })).ok, true);
  if (group) assert.equal(core.decide({ group_id: group, decision: 'accept' }).ok, true);
  else for (const id of ids) assert.equal(core.decide({ id, decision: 'accept' }).ok, true);
  assert.equal(core.claimBatch(batchId, { ids, base_sha: 'base' }).ok, true);
  assert.equal(core.finalize(batchId, { phase: 'done', applied: ids,
    commit_sha: `commit-${batchId}`, generator_id: 'generator' }).ok, true);
}
function release(id, batchId, extra = {}) {
  return { id, idempotency_key: `key-${id}`, request_digest: `digest-${id}`, actor: 'service:builder',
    base_sha: 'base', candidate_sha: `commit-${batchId}`, generator_id: 'generator',
    evidence_hash: 'evidence', manifest_hash: 'manifest', target_batch_id: batchId,
    target_environment: 'production', credential_channel: 'bearer', ancestry_verified: true, ...extra };
}

test('legacy publisher frontier excludes already completed batches and orders equal timestamps by batch ID', t => {
  const core = database(t);
  assert.deepEqual(core.publisherSummary(), { eligible: 0 });
  appliedBatch(core, 'batch-a', ['a']);
  appliedBatch(core, 'batch-b', ['b']);
  appliedBatch(core, 'batch-c', ['c']);
  assert.deepEqual(core.publisherSummary(), { eligible: 3 });
  assert.equal(core.prepareProductionRelease(release('release-a', 'batch-a')).ok, true);
  // Persisted completed release, as loaded after an earlier process served it.
  core.sql.exec("UPDATE production_releases SET state='complete' WHERE id=?", 'release-a');
  assert.deepEqual(core.publisherSummary(), { eligible: 2 });
  const context = core.productionPreparationContext();
  assert.equal(context.base_sha, 'commit-batch-a');
  assert.deepEqual(context.batches.map(x => x.batch_id), ['batch-b', 'batch-c']);
  assert.equal(core.prepareProductionRelease(release('release-bad', 'batch-b')).reason, 'stale_base');
  const prepared = core.prepareProductionRelease(release('release-b', 'batch-b', { base_sha: 'commit-batch-a' }));
  assert.equal(prepared.ok, true);
  assert.deepEqual(prepared.release.batches.map(x => x.batch_id), ['batch-b']);
});

test('missing persisted legacy frontier fails closed in preparation and rejects new release', t => {
  const core = database(t);
  appliedBatch(core, 'batch-a', ['a']);
  appliedBatch(core, 'batch-b', ['b']);
  assert.equal(core.prepareProductionRelease(release('release-a', 'batch-a')).ok, true);
  core.sql.exec("UPDATE production_releases SET state='complete' WHERE id=?", 'release-a');
  core.sql.exec('DELETE FROM suggestions WHERE id=?', 'a');
  assert.deepEqual(core.productionPreparationContext().batches, []);
  assert.deepEqual(core.prepareProductionRelease(release('release-b', 'batch-b', { base_sha: 'commit-batch-a' })),
    { ok: false, reason: 'stale_frontier' });
  assert.equal(core.getProductionRelease('release-b'), null);
});

test('legacy grouped membership is released together and fails closed on a partially applied group', t => {
  const core = database(t);
  appliedBatch(core, 'group-batch', ['g1', 'g2'], 'group');
  core.sql.exec('UPDATE suggestions SET status=? WHERE id=?', 'accepted', 'g2');
  assert.deepEqual(core.prepareProductionRelease(release('group-release', 'group-batch')),
    { ok: false, reason: 'partial_group' });
  assert.equal(count(core, 'production_releases'), 0);
  core.sql.exec('UPDATE suggestions SET status=? WHERE id=?', 'applied', 'g2');
  const result = core.prepareProductionRelease(release('group-release', 'group-batch'));
  assert.equal(result.ok, true);
  assert.deepEqual(result.release.suggestion_ids, ['g1', 'g2']);
});

test('release preparation rolls back parent, batch, members and event on a real SQL write failure', t => {
  const core = database(t);
  appliedBatch(core, 'batch', ['a', 'b']);
  core.sql.db.exec("CREATE TRIGGER prevent_release_event BEFORE INSERT ON production_release_events BEGIN SELECT RAISE(ABORT, 'fixture event failure'); END;");
  assert.throws(() => core.prepareProductionRelease(release('release', 'batch')), /fixture event failure/);
  for (const table of ['production_releases', 'production_release_batches', 'production_release_members', 'production_release_events'])
    assert.equal(count(core, table), 0, `${table} must roll back`);
  assert.equal(core._get('a').status, 'applied');
  core.sql.db.exec('DROP TRIGGER prevent_release_event');
  assert.equal(core.prepareProductionRelease(release('release', 'batch')).ok, true);
  assert.equal(count(core, 'production_release_members'), 2);
});
