import { test } from 'node:test';
import assert from 'node:assert/strict';
import { worker, durableObject, EditorStore } from './coverage-runtime-helper.mjs';

const origin = 'https://editor.example.test';
async function fixture(t) {
  const store = await durableObject(EditorStore);
  t.after(store.close);
  const env = {
    SESSION_SIGNING_KEY: 'coverage-routing-signing', EDIT_ORIGIN: origin,
    EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
    EDIT_TOKEN_JOHN: 'coverage-routing-john', EDIT_TOKEN_ADMIN: 'coverage-routing-admin',
    EDITOR: { getByName(name) { assert.equal(name, 'global-v1'); return store.object; } },
  };
  const request = (path, cookie = '', method = 'GET') => worker.fetch(new Request(origin + path, { method, headers: { cookie } }), env, {});
  const login = async slot => {
    const response = await request('/edit/?t=' + env[`EDIT_TOKEN_${slot.toUpperCase()}`]);
    assert.equal(response.status, 302);
    return response.headers.get('set-cookie').split(';')[0];
  };
  return { env, store, request, login };
}

test('token integration: exchange removes every token while preserving unrelated query parameters', async (t) => {
  const { request, env } = await fixture(t);
  const response = await request(`/edit/admin?filter=pending&t=${env.EDIT_TOKEN_ADMIN}&t=other&label=a%20b`);
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('location'), '/edit/admin?filter=pending&label=a+b');
  assert.match(response.headers.get('set-cookie'), /HttpOnly; Secure; SameSite=Lax; Path=\/edit/);
  const cookie = response.headers.get('set-cookie').split(';')[0];
  const dashboard = await request('/edit/admin', cookie);
  assert.equal(dashboard.status, 200);
  assert.match(await dashboard.text(), /Since your last visit/);
});

for (const value of ['', 'invalid', '%3Cscript%3E']) {
  test(`invalid token ${JSON.stringify(value)} is stripped without minting authority`, async (t) => {
    const { request } = await fixture(t);
    const exchange = await request('/edit/admin?retain=1&t=' + value);
    assert.equal(exchange.status, 302);
    assert.equal(exchange.headers.get('location'), '/edit/admin?retain=1');
    assert.equal(exchange.headers.get('set-cookie'), null);
    assert.equal((await request(exchange.headers.get('location'))).status, 404);
  });
}

test('preflight precedes token exchange and retains the configured origin boundary', async (t) => {
  const { env } = await fixture(t);
  for (const source of [origin, 'https://untrusted.example']) {
    const response = await worker.fetch(new Request(`${origin}/edit/v1/pending?t=${env.EDIT_TOKEN_JOHN}`, { method: 'OPTIONS', headers: { origin: source } }), env, {});
    assert.equal(response.status, 204);
    assert.equal(response.headers.get('location'), null);
    assert.equal(response.headers.get('set-cookie'), null);
    assert.equal(response.headers.get('access-control-allow-origin'), source === origin ? origin : null);
  }
});

for (const seen of ['', 'not-a-date', '%E0%A4%A', '2099-01-01T00%3A00%3A00.000Z']) {
  test(`admin seen marker ${JSON.stringify(seen)} safely renders an empty state and refreshes the marker`, async (t) => {
    const { request, login } = await fixture(t);
    const before = Date.now();
    const response = await request('/edit/admin', `${await login('admin')}; edit_seen=${seen}`);
    assert.equal(response.status, 200);
    assert.match(await response.text(), /Nothing new since your last visit/);
    const cookie = response.headers.get('set-cookie');
    assert.match(cookie, /; HttpOnly; Secure; SameSite=Lax; Path=\/edit\/admin; Max-Age=31536000$/);
    const stamp = Date.parse(decodeURIComponent(cookie.split(';')[0].split('=')[1]));
    assert.ok(stamp >= before && stamp <= Date.now());
  });
}

test('admin seen integration: real stored suggestions appear only after a valid earlier visit', async (t) => {
  const { request, login, store } = await fixture(t);
  const inserted = await store.object.suggest({ id: 'routing-seen', editor: 'slot:john', scope: 'edit', origin: 'human', kind: 'prose', page: 'index.html', block_anchor: 'routing:0', source_ref: 'data/routing.json#p0', json_path: null, original_text: 'Before', original_hash: 'routing-hash', new_text: 'After', comment: null, context: '', map_version: 'routing-map', group_id: null });
  assert.equal(inserted.ok, true);
  const cookie = await login('admin');
  const earlier = await request('/edit/admin', cookie + '; edit_seen=2000-01-01T00%3A00%3A00.000Z');
  const html = await earlier.text();
  assert.match(html, /class="ad-flag-list"/);
  assert.match(html, /href="\/edit\/history\/data__routing.json"/);
  assert.match(html, />JOS<\/span>/);
  for (const malformed of ['not-a-date', '%E0%A4%A']) {
    const fresh = await request('/edit/admin', cookie + '; edit_seen=' + malformed);
    assert.match(await fresh.text(), /Nothing new since your last visit/);
  }
});

test('seen marker grants no access and wrong-method admin requests do not issue bookmarks', async (t) => {
  const { request, login } = await fixture(t);
  for (const [cookie, method] of [['edit_seen=2000-01-01', 'GET'], [await login('john'), 'GET'], [await login('admin'), 'POST']]) {
    const response = await request('/edit/admin', cookie, method);
    assert.equal(response.status, 404);
    assert.equal(response.headers.get('set-cookie'), null);
  }
});

test('allowlisted edit page follows cookie authentication and real store reads into upstream configuration error', async (t) => {
  const { request, login, env } = await fixture(t);
  env.EDIT_UPSTREAM = 'invalid';
  const response = await request('/edit/index.html', await login('john'));
  assert.equal(response.status, 404);
  assert.match(await response.text(), /not available for editing/);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  const unknown = await request('/edit/not-allowlisted.html', await login('john'));
  assert.equal(unknown.status, 404);
  assert.doesNotMatch(await unknown.text(), /page just updated/);
});
