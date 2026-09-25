import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as endpoints from '../src/editor-endpoints.js';
import { EDITOR_MAP, INSTRUCTOR_BUNDLE } from '../src/editor-map.js';
import { durableObject, EditorStore } from './coverage-runtime-helper.mjs';

const origin = 'https://release-chain.example.test';
const block = Object.values(EDITOR_MAP.pages).flat().find(item => item.kind === 'prose');
const scopes = { admin: { granted: true }, publisher: { granted: true }, release_service: { granted: true }, edit: { granted: true }, instructor: { granted: false } };
const service = { editor: 'service:release', credential_channel: 'bearer', scopes };
const human = { editor: 'slot:publisher', credential_channel: 'access', scopes };
async function fixture(t) {
  const db = await durableObject(EditorStore); t.after(db.close);
  const env = { EDIT_ORIGIN: origin, PROD_RELEASE_LEDGER: 'true', EDITOR: { getByName: () => db.object } };
  async function call(name, body, auth = service, query = '') {
    const response = await endpoints[name + 'Endpoint'](new Request(origin + '/' + query, body === undefined ? {} : {
      method: 'POST', headers: { origin, 'x-edit-request': '1' }, body: JSON.stringify(body),
    }), env, auth);
    return { httpStatus: response.status, ...await response.json() };
  }
  async function applied(id = 'release-suggestion', batch = 'release-batch') {
    assert.equal((await call('systemSuggest', { id, source_ref: block.source_ref, origin: 'companion', new_text: 'New text' })).ok, true);
    assert.equal((await call('decide', { id })).ok, true);
    assert.equal((await call('claim', { batch_id: batch, base_sha: 'base', ids: [id] })).ok, true);
    assert.equal((await call('finalize', { batch_id: batch, phase: 'done', applied: [id], commit_sha: 'commit', generator_id: 'generator' })).ok, true);
  }
  return { db, env, call, applied };
}
function revision() {
  return { id: 'revision', source_ref: block.source_ref, source_revision: 'commit', prod_base: 'base', commit_sha: 'commit',
    original_hash: 'old-hash', proposed_hash: 'new-hash', original_text: 'Old text', proposed_text: 'New text',
    suggestion_ids: ['release-suggestion'], batch_chain: [{ batch_id: 'release-batch', base_sha: 'base', commit_sha: 'commit' }],
    suggestion_evidence: [{ suggestion_id: 'release-suggestion', batch_id: 'release-batch', commit_sha: 'commit' }],
    operations: [{ id: 'operation', decision_id: 'operation', kind: 'replace', source_ref: block.source_ref,
      source_revision: 'commit', prod_base: 'base', base_range: [0, 3], proposed_range: [0, 3], old_text: 'Old', new_text: 'New' }] };
}

test('endpoint apply and backfill chain freezes review evidence, replays, rejects conflict and closes export', async t => {
  const { call, applied } = await fixture(t); await applied();
  const evidence = await call('reviewBackfillEvidence', undefined, service, '?through_batch_id=release-batch');
  assert.equal(evidence.httpStatus, 200); assert.equal(evidence.evidence.suggestions[0].id, 'release-suggestion');
  const payload = { migration_id: 'migration', prod_base: 'base', revisions: [revision()] };
  const first = await call('reviewBackfill', payload); assert.equal(first.httpStatus, 201); assert.equal(first.inserted, 1);
  const replay = await call('reviewBackfill', payload); assert.equal(replay.httpStatus, 200); assert.equal(replay.replay, true);
  const conflict = await call('reviewBackfill', { ...payload, revisions: [{ ...revision(), proposed_hash: 'changed' }] });
  assert.equal(conflict.httpStatus, 409); assert.equal(conflict.reason, 'idempotency_conflict');
  const closed = await call('reviewBackfillEvidence', undefined, service, '?through_batch_id=release-batch');
  assert.equal(closed.httpStatus, 409); assert.equal(closed.error.code, 'migration_closed');
  const review = await call('publisherReview', undefined, human);
  assert.equal(review.counts.unreviewed, 1); assert.equal(review.counts.accepted, 0);
  const binding = { review_revision_id: 'revision', source_revision: 'commit', prod_base: 'base', decisions: [{ operation_id: 'operation', decision: 'accepted' }] };
  assert.equal((await call('publisherReviewDraft', binding, human)).httpStatus, 200);
  const submission = { ...binding, id: 'review', idempotency_key: 'review-key' };
  assert.equal((await call('publisherReviewSubmit', submission, human)).httpStatus, 201);
  assert.equal((await call('publisherReviewSubmit', submission, human)).httpStatus, 200);
  assert.equal((await call('publisherReviewDraft', binding, human)).error.code, 'review_submitted');
});

test('real release endpoints prepare, authorize, fence, renew and complete an applied batch', async t => {
  const { call, applied } = await fixture(t); await applied();
  const binding = { id: 'release', idempotency_key: 'prepare-key', target_batch_id: 'release-batch', base_sha: 'base',
    candidate_sha: 'commit', generator_id: 'generator', evidence_hash: 'evidence', manifest_hash: 'manifest', ancestry_verified: true };
  const prepared = await call('productionPrepare', binding); assert.equal(prepared.httpStatus, 201);
  assert.equal((await call('productionPrepare', binding)).httpStatus, 200);
  assert.equal((await call('productionClaim', {})).release, null);
  const authorization = { ...binding, idempotency_key: 'authorize-key', membership_hash: prepared.release.membership_hash };
  assert.equal((await call('publisherAuthorize', authorization, human)).httpStatus, 201);
  assert.equal((await call('publisherAuthorize', authorization, human)).httpStatus, 200);
  const claimed = await call('productionClaim', { id: 'release', lease_ms: 10000 });
  assert.equal(claimed.release.state, 'executing');
  assert.equal((await call('productionClaim', { id: 'release' })).error.code, 'lease_active');
  const lease = { id: 'release', fencing_token: claimed.release.fencing_token };
  assert.equal((await call('productionRenew', { ...lease, fencing_token: 'stale' })).error.code, 'stale_fence');
  assert.equal((await call('productionRenew', { ...lease, lease_ms: 20000 })).httpStatus, 200);
  assert.equal((await call('productionTransition', { ...lease, state: 'verified' })).error.code, 'targets_incomplete');
  for (const state of ['pages_deployed', 'worker_deployed', 'verified', 'complete']) {
    const result = await call('productionTransition', { ...lease, state, detail: { candidate_sha: 'commit' } });
    assert.equal(result.httpStatus, 200, JSON.stringify(result));
  }
  assert.equal((await call('publisherRelease', undefined, human, '?id=release')).release.state, 'complete');
  assert.equal((await call('productionPreparationContext')).context.active_release, null);
  assert.equal((await call('productionAudit')).audit.counts.applied_suggestions, 1);
});

test('finalize rolls back batch and item mutations when attached review evidence mismatches', async t => {
  const { call, db } = await fixture(t);
  await call('systemSuggest', { id: 'release-suggestion', source_ref: block.source_ref, origin: 'companion', new_text: 'New text' });
  await call('decide', { id: 'release-suggestion' }); await call('claim', { batch_id: 'release-batch' });
  const failed = await call('finalize', { batch_id: 'release-batch', phase: 'done', applied: ['release-suggestion'], commit_sha: 'other', review_revisions: [revision()] });
  assert.equal(failed.httpStatus, 409); assert.equal(failed.reason, 'revision_mismatch');
  assert.equal(db.sql.exec('SELECT status FROM suggestions').toArray()[0].status, 'in_flight');
  assert.equal(db.sql.exec('SELECT phase FROM apply_batches').toArray()[0].phase, 'claimed');
  const done = await call('finalize', { batch_id: 'release-batch', phase: 'done', applied: ['release-suggestion'], commit_sha: 'commit', review_revisions: [revision()] });
  assert.equal(done.httpStatus, 200);
  assert.equal((await call('publisherReview', undefined, human)).counts.unreviewed, 1);
});

test('reconcile endpoint recovers expired claims and leaves active claims intact', async t => {
  const { call, db } = await fixture(t);
  await call('systemSuggest', { id: 'release-suggestion', source_ref: block.source_ref, origin: 'companion', new_text: 'New text' });
  await call('decide', { id: 'release-suggestion' }); await call('claim', { batch_id: 'release-batch' });
  assert.equal((await call('reconcile', {})).batches, 0);
  db.sql.exec('UPDATE apply_batches SET lease_expires_at=0');
  const recovered = await call('reconcile', {});
  assert.deepEqual(recovered.rolled_back, ['release-suggestion']);
  assert.equal((await call('reviewJson')).items[0].status, 'accepted');
  assert.equal((await call('reconcile', {})).batches, 0);
});

test('legacy reconciliation endpoint records exact applied evidence and closes subsequent migrations', async t => {
  const { call, applied } = await fixture(t); await applied();
  const base = 'a'.repeat(40), commit = 'b'.repeat(40);
  assert.equal((await call('finalize', { batch_id: 'release-batch', phase: 'done', base_sha: base, commit_sha: commit })).ok, true);
  const rev = { ...revision(), prod_base: base, source_revision: commit, commit_sha: commit,
    operations: revision().operations.map(op => ({ ...op, prod_base: base, source_revision: commit })),
    suggestion_evidence: [{ suggestion_id: 'release-suggestion', batch_id: 'release-batch', base_sha: base, commit_sha: commit }] };
  const payload = { migration_id: 'reconcile', prod_base: base, exclusions: [], revisions: [rev] };
  const first = await call('reviewLegacyReconcile', payload);
  assert.equal(first.httpStatus, 201); assert.equal(first.inserted, 1);
  const replay = await call('reviewLegacyReconcile', payload);
  assert.equal(replay.httpStatus, 200); assert.equal(replay.replay, true);
  const conflict = await call('reviewLegacyReconcile', { ...payload, revisions: [{ ...rev, proposed_hash: 'changed' }] });
  assert.equal(conflict.httpStatus, 409); assert.equal(conflict.reason, 'idempotency_conflict');
  const closed = await call('reviewLegacyReconcile', { ...payload, migration_id: 'another' });
  assert.equal(closed.httpStatus, 409); assert.equal(closed.reason, 'migration_closed');
  assert.equal((await call('productionAudit')).audit.invariants.unreconciled_applied_suggestions, 0);
});

for (const [name, body] of [['publisherReviewDraft', {}], ['publisherReviewSubmit', { sources: [null] }],
  ['publisherReviewSubmit', { sources: [] }], ['publisherAuthorize', { id: 4 }],
  ['productionPrepare', {}], ['productionRenew', {}], ['productionTransition', {}], ['productionRestoreClaim', {}]]) {
  test(`${name} rejects incomplete bindings without creating a ledger record: ${JSON.stringify(body)}`, async t => {
    const { call, db } = await fixture(t);
    const result = await call(name, body, name.startsWith('publisher') ? human : service);
    assert.equal(result.httpStatus, 400);
    assert.equal(db.sql.exec('SELECT COUNT(*) AS n FROM production_releases').toArray()[0].n, 0);
    assert.equal(db.sql.exec('SELECT COUNT(*) AS n FROM production_reviews').toArray()[0].n, 0);
  });
}

test('empty release storage reports no authorization or preparation and missing IDs consistently', async t => {
  const { call } = await fixture(t);
  assert.equal((await call('productionClaim', {})).release, null);
  assert.deepEqual((await call('productionPreparationContext')).context.batches, []);
  assert.equal((await call('publisherRelease', undefined, human)).httpStatus, 400);
  assert.equal((await call('publisherRelease', undefined, human, '?id=absent')).httpStatus, 404);
  assert.equal((await call('productionRenew', { id: 'absent', fencing_token: 'absent' })).httpStatus, 409);
  assert.equal((await call('productionRestoreClaim', { id: 'absent' })).httpStatus, 409);
  const audit = (await call('productionAudit')).audit;
  assert.deepEqual(audit.active_releases, []); assert.equal(audit.counts.applied_suggestions, 0);
});

test('instructor suggestion reaches its separate allowlist and page-less pending remains caller-specific', async t => {
  const { call, db } = await fixture(t);
  const instructorBlock = INSTRUCTOR_BUNDLE.docs.flatMap(doc => doc.blocks).find(item => item.kind === 'prose');
  assert.ok(instructorBlock);
  const instructor = { ...human, editor: 'slot:instructor', scopes: { ...scopes, edit: { granted: false }, instructor: { granted: true } } };
  const payload = { id: 'instructor-edit', source_ref: instructorBlock.source_ref, new_text: 'Instructor clarification' };
  assert.equal((await call('suggest', payload, instructor)).httpStatus, 200);
  const row = db.sql.exec('SELECT scope,original_text FROM suggestions WHERE id=?', payload.id).toArray()[0];
  assert.equal(row.scope, 'instructor'); assert.equal(row.original_text, instructorBlock.original_text);
  assert.equal((await call('pending', undefined, instructor)).items.length, 1);
  assert.deepEqual((await call('pending', undefined, human)).items, []);
  assert.equal((await call('suggest', { ...payload, id: 'public-instructor' }, human)).httpStatus, 400);
});
