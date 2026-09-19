import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mintScopedConfirmation, verifyScopedConfirmation, SCOPED_CONFIRMATION_TTL_MS } from '../src/scoped-confirmation.js';
import { scopedRequestEndpoint, scopedRequestsEndpoint } from '../src/editor-endpoints.js';
import { resolveAuth } from '../src/editor-auth.js';
import { durableObject, EditorStore } from './coverage-runtime-helper.mjs';

const secret = 'coverage-only-hmac-secret';
const context = { editor: 'slot:john', scope: { level: 'course' }, instruction: 'Keep café punctuation.' };
async function signed(payload) {
  const encoded = Buffer.from(typeof payload === 'string' ? payload : JSON.stringify(payload)).toString('base64url');
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(encoded));
  return encoded + '.' + Buffer.from(signature).toString('base64url');
}

for (const [label, payload] of [
  ['invalid JSON', '{'], ['null claims', 'null'], ['wrong version', { v: 2, exp: 2000 }],
  ['missing expiry', { v: 1 }], ['string expiry', { v: 1, exp: '2000' }],
  ['null expiry', { v: 1, exp: null }], ['exact expiry', { v: 1, exp: 1000 }],
  ['past expiry', { v: 1, exp: 999 }], ['missing context', { v: 1, exp: 2000 }],
]) {
  test(`authentically signed ${label} is rejected`, async () => {
    assert.equal(await verifyScopedConfirmation(secret, await signed(payload), context, 1000), false);
  });
}

for (const token of [null, 1, {}, '', '.signature', 'payload', 'one.two.three', 'payload.%', 'payload.']) {
  test(`malformed confirmation ${JSON.stringify(token)} fails closed`, async () => {
    assert.equal(await verifyScopedConfirmation(secret, token, context), false);
  });
}

test('minted token honors millisecond expiration and binds secret, Unicode text and actor', async () => {
  const token = await mintScopedConfirmation(secret, context, 2000);
  assert.equal(await verifyScopedConfirmation(secret, token, context, 1999), true);
  assert.equal(await verifyScopedConfirmation(secret, token, context, 2000), false);
  assert.equal(await verifyScopedConfirmation('other-secret', token, context, 1999), false);
  for (const invalid of ['', null, 42]) assert.equal(await verifyScopedConfirmation(invalid, token, context, 1999), false);
  for (const changed of [{ ...context, editor: 'slot:roger' }, { ...context, instruction: 'Keep cafe punctuation.' }, { ...context, scope: { level: 'part' } }]) {
    assert.equal(await verifyScopedConfirmation(secret, token, changed, 1999), false);
  }
  const circular = {}; circular.self = circular;
  assert.equal(await verifyScopedConfirmation(secret, token, circular, 1999), false);
});

test('default confirmation lifetime is bounded by the actual mint clock', async () => {
  const before = Date.now();
  const token = await mintScopedConfirmation(secret, context);
  const after = Date.now();
  const claims = JSON.parse(Buffer.from(token.split('.')[0], 'base64url').toString());
  assert.ok(claims.exp >= before + SCOPED_CONFIRMATION_TTL_MS);
  assert.ok(claims.exp <= after + SCOPED_CONFIRMATION_TTL_MS);
  assert.equal(await verifyScopedConfirmation(secret, token, context), true);
});

test('real auth, challenge HMAC and SQLite request filing reject altered wording before accepting and replaying exact confirmation', async t => {
  const db = await durableObject(EditorStore); t.after(db.close);
  const origin = 'https://confirmation.example.test';
  const env = { SESSION_SIGNING_KEY: secret, EDIT_ORIGIN: origin,
    EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
    EDIT_TOKEN_JOHN: 'confirmation-john-token', EDIT_TOKEN_ADMIN: 'confirmation-admin-token',
    EDITOR: { getByName: () => db.object } };
  const auth = token => resolveAuth(env, new Request(origin, { headers: { authorization: `Bearer ${token}` } }));
  const editor = await auth(env.EDIT_TOKEN_JOHN), admin = await auth(env.EDIT_TOKEN_ADMIN);
  const body = { id: 'coverage-confirmation-001', level: 'course', instruction: 'Use clear language throughout.' };
  const post = value => scopedRequestEndpoint(new Request(origin, { method: 'POST', headers: { origin, 'x-edit-request': '1' }, body: JSON.stringify(value) }), env, editor);
  const challenge = await post(body); assert.equal(challenge.status, 409);
  const { confirmation_token } = await challenge.json();
  assert.equal((await post({ ...body, confirmed: true, confirmation_token, instruction: 'Different instruction' })).status, 409);
  const empty = await scopedRequestsEndpoint(new Request(origin), env, admin);
  assert.deepEqual((await empty.json()).items, []);
  const accepted = await post({ ...body, confirmed: true, confirmation_token });
  assert.equal(accepted.status, 200); assert.equal((await accepted.json()).replay, false);
  assert.equal((await (await post({ ...body, confirmed: true, confirmation_token })).json()).replay, true);
  const rows = (await (await scopedRequestsEndpoint(new Request(origin), env, admin)).json()).items;
  assert.equal(rows.length, 1); assert.equal(rows[0].confirmed, 1);
  assert.equal(rows[0].editor, 'slot:john'); assert.equal(rows[0].instruction, body.instruction);
});
