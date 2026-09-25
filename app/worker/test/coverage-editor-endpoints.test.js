import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as endpoints from '../src/editor-endpoints.js';
import { EDITOR_MAP } from '../src/editor-map.js';
import { resolveAuth } from '../src/editor-auth.js';
import { durableObject, EditorStore } from './coverage-runtime-helper.mjs';

const origin = 'https://endpoint-coverage.example.test';
const block = Object.values(EDITOR_MAP.pages).flat().find(x => x.kind === 'prose');
async function fixture(t, overrides = {}) {
  const db = await durableObject(EditorStore); t.after(db.close);
  const env = { EDIT_ORIGIN: origin, SESSION_SIGNING_KEY: 'endpoint-coverage-secret',
    EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
    EDIT_TOKEN_JOHN: 'endpoint-john-credential', EDIT_TOKEN_ADMIN: 'endpoint-admin-credential',
    EDITOR: { getByName: () => db.object }, ...overrides };
  const auth = {};
  for (const slot of ['john', 'admin']) auth[slot] = await resolveAuth(env, new Request(origin, { headers: { authorization: `Bearer ${env['EDIT_TOKEN_' + slot.toUpperCase()]}` } }));
  const call = async (name, body, slot = 'admin', query = '') => {
    const response = await endpoints[name + 'Endpoint'](new Request(origin + '/' + query, body === undefined ? {} : {
      method: 'POST', headers: { origin, 'x-edit-request': '1' }, body: JSON.stringify(body),
    }), env, auth[slot]);
    return { httpStatus: response.status, ...(await response.json()) };
  };
  return { db, env, auth, call };
}
const suggestion = (id, extra = {}) => ({ id, source_ref: block.source_ref, new_text: 'Coverage exercises actual storage.', origin: 'companion', ...extra });

for (const [setting, reason] of [['EDIT_MAX_PENDING_PER_EDITOR', 'pending_ceiling'], ['EDIT_MAX_GLOBAL_PENDING', 'global_ceiling'], ['EDIT_MAX_DAILY_PER_EDITOR', 'daily_cap']]) {
  test(`system queue maps actual ${reason} to 429 without persisting a rejected row`, async t => {
    const { call } = await fixture(t, { [setting]: '1' });
    assert.equal((await call('systemSuggest', suggestion('coverage-system-001'))).httpStatus, 200);
    const rejected = await call('systemSuggest', suggestion('coverage-system-002'));
    assert.equal(rejected.httpStatus, 429); assert.equal(rejected.error.code, reason);
    const review = await call('reviewJson'); assert.equal(review.items.length, 1);
    assert.equal(review.items[0].id, 'coverage-system-001');
  });
}

test('real grouped review decisions preserve atomicity, terminal states, and attribution', async t => {
  const { call } = await fixture(t);
  assert.deepEqual((await call('reviewJson')).items, []);
  for (const id of ['coverage-group-001', 'coverage-group-002']) {
    assert.equal((await call('systemSuggest', suggestion(id, { group_id: 'coverage-group' }))).httpStatus, 200);
  }
  let result = await call('decide', { id: 'coverage-group-001' });
  assert.equal(result.httpStatus, 409); assert.equal(result.error.code, 'group_accept_required');
  assert.equal((await call('decide', { id: 'coverage-group-001', action: 'decline', note: 'x'.repeat(2100) })).httpStatus, 200);
  result = await call('decide', { group_id: 'coverage-group' });
  assert.equal(result.httpStatus, 409); assert.equal(result.error.code, 'illegal_group_state');
  const outcome = await call('groupStatus', undefined, 'admin', '?group_id=coverage-group');
  assert.deepEqual(outcome.outcome.by_status, { pending: 1, declined: 1 });
  result = await call('decide', { id: 'coverage-group-001', action: 'reanchor' });
  assert.equal(result.httpStatus, 409); assert.equal(result.error.code, 'illegal_transition');
  assert.equal((await call('reviewJson')).items[0].attribution, 'ADM');
});

for (const [body, status, code] of [[{}, 400, 'validation_error'], [{ id: 'absent-id' }, 404, 'not_found'], [{ action: 'reanchor' }, 400, 'validation_error'], [{ action: 'unknown' }, 400, 'validation_error'], [{ group_id: 'absent-group' }, 404, 'not_found']]) {
  test(`review decision ${JSON.stringify(body)} returns ${code} from real storage`, async t => {
    const { call } = await fixture(t); const result = await call('decide', body);
    assert.equal(result.httpStatus, status); assert.equal(result.error.code, code);
  });
}

test('revert request chain records truncated notes and maps missing, invalid and terminal resolutions', async t => {
  const { call } = await fixture(t);
  const filed = await call('revertRequest', { doc: block.source_ref, run: ['abcdef0', 'abcdef1'] }, 'john');
  assert.equal(filed.status, 'requested');
  for (const [body, status, code] of [[{ id: 'missing', status: 'approved' }, 404, 'not_found'], [{ id: filed.id, status: 'unknown' }, 400, 'validation_error']]) {
    const response = await call('revertResolve', body); assert.equal(response.httpStatus, status); assert.equal(response.error.code, code);
  }
  const done = await call('revertResolve', { id: filed.id, status: 'done', note: 'n'.repeat(2200) });
  assert.equal(done.status, 'done');
  const retry = await call('revertResolve', { id: filed.id, status: 'approved' });
  assert.equal(retry.httpStatus, 409); assert.equal(retry.error.code, 'already_terminal');
  const listed = await call('revertRequests', undefined, 'admin', '?status=done');
  assert.equal(listed.items.length, 1); assert.equal(listed.items[0].note.length, 2000);
});

test('scoped lifecycle rejects missing and illegal states and persists drafting metadata', async t => {
  const { call } = await fixture(t);
  let result = await call('scopedResolve', { id: 'missing', status: 'drafted' });
  assert.equal(result.httpStatus, 404); assert.equal(result.error.code, 'not_found');
  const id = 'coverage-scoped-001';
  result = await call('scopedRequest', { id, level: 'part', matter: 'm03-tort-meridian', part: 'exercise', instruction: 'Clarify the exercise.' }, 'john');
  assert.equal(result.ok, true);
  result = await call('scopedResolve', { id, status: 'done' });
  assert.equal(result.httpStatus, 409); assert.equal(result.error.code, 'illegal_transition');
  assert.equal((await call('scopedClaim', { id })).ok, true);
  assert.equal((await call('scopedClaim', { id })).httpStatus, 409);
  assert.equal((await call('scopedResolve', { id, status: 'drafted', group_id: 'draft-group', phase: 'all', canary_matter: 'm03-tort-meridian', note: 'n'.repeat(2100) })).ok, true);
  const rows = (await call('scopedRequests')).items;
  assert.equal(rows[0].note.length, 2000); assert.equal(rows[0].group_id, 'draft-group');
  assert.equal((await call('scopedResolve', { id, status: 'done' })).ok, true);
});

for (const [label, body] of [['invalid UTF-8', new Uint8Array([0xff])], ['malformed JSON', '{'], ['empty body', undefined]]) {
  test(`bounded migration rejects ${label} without recording evidence`, async t => {
    const { env, auth } = await fixture(t);
    const response = await endpoints.reviewLegacyReconcileEndpoint(new Request(origin, { method: 'POST', headers: { origin, 'x-edit-request': '1' }, body }), env, auth.admin);
    assert.equal(response.status, 400); assert.match((await response.json()).error.message, /Malformed JSON/);
  });
}

test('bounded migration catches a read error and cancels an oversized stream', async t => {
  const { env, auth } = await fixture(t);
  const broken = new ReadableStream({ pull(controller) { controller.error(new Error('fixture transport closed')); } });
  let canceled = false;
  const oversized = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(1024 * 1024 + 1)); }, cancel() { canceled = true; } });
  for (const [body, status] of [[broken, 400], [oversized, 413]]) {
    const response = await endpoints.reviewLegacyReconcileEndpoint(new Request(origin, { method: 'POST', duplex: 'half', headers: { origin, 'x-edit-request': '1' }, body }), env, auth.admin);
    assert.equal(response.status, status);
  }
  assert.equal(canceled, true);
});

for (const name of ['decide', 'claim', 'finalize', 'reviewBackfill', 'reviewLegacyReconcile', 'scopedResolve', 'revertResolve']) {
  test(`${name} enforces CSRF, real editor authorization and malformed JSON before storage`, async t => {
    const { env, auth } = await fixture(t);
    const endpoint = endpoints[name + 'Endpoint'];
    const request = (headers, body = '{}') => new Request(origin, { method: 'POST', headers, body });
    const denied = await endpoint(request({}), env, auth.admin);
    assert.equal(denied.status, 403); assert.equal((await denied.json()).error.code, 'csrf_failed');
    const headers = { origin, 'x-edit-request': '1' };
    const forbidden = await endpoint(request(headers), env, auth.john);
    assert.equal(forbidden.status, 403); assert.equal((await forbidden.json()).error.code, 'forbidden');
    const malformed = await endpoint(request(headers, '{'), env, auth.admin);
    assert.equal(malformed.status, 400); assert.equal((await malformed.json()).error.code, 'validation_error');
  });
}

for (const name of ['reviewJson', 'digest', 'reviewBackfillEvidence', 'scopedRequests', 'groupStatus', 'revertRequests']) {
  test(`${name} rejects a real editor bearer lacking administrator scope`, async t => {
    const { call } = await fixture(t);
    const response = await call(name, undefined, 'john');
    assert.equal(response.httpStatus, 403); assert.equal(response.error.code, 'forbidden');
  });
}

test('empty admin queue exposes digest and group state and refuses to create an empty apply batch', async t => {
  const { call } = await fixture(t);
  const digest = await call('digest'); assert.equal(digest.httpStatus, 200); assert.equal(digest.ok, true);
  const group = await call('groupStatus', undefined, 'admin', '?group_id=absent');
  assert.deepEqual(group.outcome, { group_id: 'absent', total: 0, by_status: {} });
  const claim = await call('claim', { batch_id: 'coverage-empty-batch' });
  assert.equal(claim.httpStatus, 409); assert.equal(claim.reason, 'nothing_to_claim');
  const duplicate = await call('claim', { batch_id: 'coverage-empty-batch' });
  assert.equal(duplicate.httpStatus, 409); assert.equal(duplicate.reason, 'nothing_to_claim');
  const absent = await call('finalize', { batch_id: 'absent-batch', phase: 'failed' });
  assert.equal(absent.httpStatus, 409); assert.equal(absent.ok, false);
});

for (const body of [{}, { migration_id: 12, prod_base: 'base' }, { migration_id: 'x'.repeat(257), prod_base: 'base' }, { migration_id: 'migration', prod_base: 'é'.repeat(129) }, { migration_id: 'migration', prod_base: 'base', revisions: {} }]) {
  test(`migration backfill rejects invalid identity or revisions ${JSON.stringify(body).slice(0, 90)}`, async t => {
    const { call } = await fixture(t);
    const result = await call('reviewBackfill', body);
    assert.equal(result.httpStatus, 400); assert.equal(result.error?.code || result.reason, 'validation_error');
  });
}

test('migration evidence rejects an absent frontier through the real ledger', async t => {
  const { call } = await fixture(t);
  const result = await call('reviewBackfillEvidence', undefined, 'admin', '?through_batch_id=absent');
  assert.equal(result.httpStatus, 409); assert.equal(result.error.code, 'unknown_batch');
});
