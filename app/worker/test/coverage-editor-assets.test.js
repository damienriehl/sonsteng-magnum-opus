import { test } from 'node:test';
import assert from 'node:assert/strict';
import { serveAsset } from '../src/editor-assets.js';
import { serveSiteAsset, handleEditPage } from '../src/editor-inject.js';
import { worker } from './coverage-runtime-helper.mjs';

const origin = 'https://editor.example.test';
for (const name of ['editor.css', 'editor.js', 'review.css', 'review.js', 'publisher.css', 'publisher.js', 'history.css', 'history.js', 'assessment-review.css', 'assessment-review.js']) {
  test(`asset integration: worker routes ${name} with exact content and security headers`, async () => {
    const direct = serveAsset(name);
    const response = await worker.fetch(new Request(`${origin}/edit/assets/${name}`), {}, {});
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('content-type'), name.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8');
    assert.equal(await response.text(), await direct.text());
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
    assert.match(response.headers.get('content-security-policy'), /default-src 'none'/);
  });
}

test('asset router uniformly rejects missing assets and non-read methods', async () => {
  for (const [path, method] of [['assets/missing.js', 'GET'], ['assets/editor.js', 'HEAD'], ['assets/editor.css', 'POST'], ['site-assets/theme.css', 'POST'], ['site-assets/missing.css', 'GET']]) {
    const response = await worker.fetch(new Request(`${origin}/edit/${path}`, { method }), {}, {});
    assert.equal(response.status, 404);
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
  }
  assert.equal(serveAsset('missing.js'), null);
});

test('injector integration: worker rejects invalid upstream configuration without fetching', async () => {
  const response = await worker.fetch(new Request(`${origin}/edit/site-assets/theme.css`), { EDIT_UPSTREAM: 'invalid' }, {});
  assert.equal(response.status, 404);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  for (const upstream of [undefined, '', 'invalid', 'https://public.example/platform']) {
    assert.equal(await serveSiteAsset({ EDIT_UPSTREAM: upstream }, 'theme.css'), null);
  }
  const page = await handleEditPage({ EDIT_UPSTREAM: 'invalid' }, { pageKey: 'index.html', blocks: [], pending: [] });
  assert.equal(page.status, 404);
  assert.match(await page.text(), /not available for editing/);
});

// Fetch is only the external transport boundary; actual Request, Response,
// URL validation, body streaming and the worker/editor routing stay real.
test('site stylesheet proxy constructs clean requests and discards upstream headers', async (t) => {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async request => {
    requests.push(request);
    return new Response('body{color:navy}', { headers: { 'content-type': 'text/html', 'set-cookie': 'upstream=secret', 'cache-control': 'public, max-age=999', 'content-security-policy': 'unsafe' } });
  });
  for (const [name, relative] of [['theme.css', 'assets/theme.css'], ['fonts.css', 'assets/fonts.css'], ['platform.css', 'platform.css']]) {
    const response = await worker.fetch(new Request(`${origin}/edit/site-assets/${name}?toss=1`, { headers: { cookie: 'private=value', authorization: 'Bearer private' } }), { EDIT_UPSTREAM: 'https://public.example/platform/' }, {});
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'body{color:navy}');
    assert.equal(response.headers.get('content-type'), 'text/css; charset=utf-8');
    assert.equal(response.headers.get('set-cookie'), null);
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
    const request = requests.at(-1);
    assert.equal(request.url, `https://public.example/platform/${relative}`);
    assert.equal(request.method, 'GET');
    assert.equal(request.redirect, 'manual');
    assert.deepEqual([...request.headers], [['accept', 'text/css,*/*'], ['user-agent', 'sonsteng-editor-proxy']]);
  }
});

for (const status of [301, 302, 307, 404, 500]) {
  test(`upstream ${status} produces a closed asset response and an honest page error`, async (t) => {
    t.mock.method(globalThis, 'fetch', async () => new Response('not serveable', { status, headers: { location: 'https://elsewhere.example/', 'content-type': 'text/html' } }));
    const env = { EDIT_UPSTREAM: 'https://public.example/platform/' };
    assert.equal(await serveSiteAsset(env, 'theme.css'), null);
    const page = await handleEditPage(env, { pageKey: 'index.html', blocks: [], pending: [] });
    assert.equal(page.status, status < 400 ? 409 : status === 404 ? 404 : 502);
    assert.match(await page.text(), /This page just updated/);
    assert.equal(page.headers.get('location'), null);
  });
}

test('upstream transport errors and non-HTML pages degrade without exposing details', async (t) => {
  const env = { EDIT_UPSTREAM: 'https://public.example/platform/' };
  const args = { pageKey: 'index.html', blocks: [], pending: [] };
  const transport = t.mock.method(globalThis, 'fetch', async () => { throw new Error('private transport details'); });
  assert.equal(await serveSiteAsset(env, 'fonts.css'), null);
  const failed = await handleEditPage(env, args);
  assert.equal(failed.status, 502);
  assert.doesNotMatch(await failed.text(), /private transport details/);
  for (const headers of [{}, { 'content-type': 'application/json' }]) {
    transport.mock.mockImplementation(async () => new Response('{}', { headers }));
    const response = await handleEditPage(env, args);
    assert.equal(response.status, 415);
    assert.match(await response.text(), /cannot be edited/);
  }
});
