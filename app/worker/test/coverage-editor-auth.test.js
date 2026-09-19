import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveOpaqueToken, mintCookieValue, verifyCookieValue, resolveRequestScopes,
  resolveAuth, buildSetCookie } from '../src/editor-auth.js';
import { editorFetch } from '../src/editor.js';

const SECRET = 'coverage-editor-auth-signing-secret';
const ENV = { SESSION_SIGNING_KEY: SECRET, EDIT_ORIGIN: 'https://editor.example.test',
  EDIT_TOKEN_SCOPES: JSON.stringify({ editor: { edit: 1 } }), EDIT_TOKEN_EDITOR: 'coverage-opaque-editor-token' };
const request = (cookie, extra = {}) => new Request(ENV.EDIT_ORIGIN + '/edit/history/', {
  headers: { cookie: `edit_scope=${cookie}`, ...extra },
});
async function signed(text) {
  const payload = Buffer.from(text).toString('base64url');
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(SECRET),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(payload));
  return `${payload}.${Buffer.from(signature).toString('base64url')}`;
}
function denied(scopes) { assert.ok(Object.values(scopes).every(scope => scope.granted === false)); }

test('malformed scope configuration denies both opaque and signed-cookie paths', async () => {
  const token = await mintCookieValue(SECRET, { slot: 'editor', stamp: 'untrusted' });
  const env = { ...ENV, EDIT_TOKEN_SCOPES: '{broken' };
  assert.equal(await resolveOpaqueToken(env, ENV.EDIT_TOKEN_EDITOR), null);
  denied(await resolveRequestScopes(env, request(token)));
  assert.equal((await resolveAuth(env, request(token))).editor, null);
});

test('cookie verifier rejects invalid base64 and signed invalid JSON or claim types', async () => {
  assert.equal(await verifyCookieValue(SECRET, 'payload.%'), null);
  for (const text of ['{', 'null', '[]', '{}', '{"slot":1,"stamp":"x"}', '{"slot":"editor","stamp":1}']) {
    assert.equal(await verifyCookieValue(SECRET, await signed(text)), null, text);
  }
});

test('scope resolver rejects absent, malformed, revoked and version-rotated cookies', async () => {
  const match = await resolveOpaqueToken(ENV, ENV.EDIT_TOKEN_EDITOR);
  const token = await mintCookieValue(SECRET, match);
  denied(await resolveRequestScopes(ENV, new Request(ENV.EDIT_ORIGIN)));
  denied(await resolveRequestScopes(ENV, request('malformed')));
  denied(await resolveRequestScopes({ ...ENV, EDIT_TOKEN_SCOPES: '{}' }, request(token)));
  denied(await resolveRequestScopes({ ...ENV, EDIT_TOKEN_SCOPES: '{"editor":{"edit":2}}' }, request(token)));
  assert.equal((await resolveRequestScopes(ENV, request(token))).edit.granted, true);
});

test('real token exchange feeds the cookie-only scope resolver and protected history route', async () => {
  const exchange = await editorFetch(new Request(`${ENV.EDIT_ORIGIN}/edit/history/?t=${ENV.EDIT_TOKEN_EDITOR}`), ENV, {});
  assert.equal(exchange.status, 302);
  const cookie = exchange.headers.get('set-cookie').split(';')[0];
  const req = new Request(ENV.EDIT_ORIGIN + exchange.headers.get('location'), { headers: { cookie } });
  const scopes = await resolveRequestScopes(ENV, req);
  assert.equal(scopes.edit.granted, true);
  assert.equal(scopes.admin.granted, false);
  const page = await editorFetch(req, ENV, {});
  assert.equal(page.status, 200);
  assert.match(await page.text(), /Change history/);
});

test('invalid cookie permits independently valid bearer authentication', async () => {
  const cookie = await signed('{"slot":"revoked","stamp":"old"}');
  const auth = await resolveAuth(ENV, request(cookie, { authorization: `Bearer ${ENV.EDIT_TOKEN_EDITOR}` }));
  assert.equal(auth.credential_channel, 'bearer');
  assert.equal(auth.editor, 'slot:editor');
  assert.equal(auth.scopes.edit.granted, true);
});

test('cookie attributes permit explicit expiration without changing scope path or security flags', () => {
  assert.equal(buildSetCookie('expired', { maxAge: 0 }),
    'edit_scope=expired; HttpOnly; Secure; SameSite=Lax; Path=/edit; Max-Age=0');
});
