import { test } from 'node:test';
import assert from 'node:assert/strict';
import { editorFetch } from '../src/editor.js';
import { renderHistoryPage, renderHistoryIndex, findDocBySlug } from '../src/editor-history.js';
import { renderInstructorDoc } from '../src/editor-instructor.js';
import { renderReviewPage } from '../src/editor-review.js';
import { resolveInstructorDoc } from '../src/editor-map.js';
import HISTORY from '../editor-data/history-bundle.generated.json' with { type: 'json' };
import { makeCore } from './editor-sql-helper.mjs';

const origin = 'https://editor.example.test';
const attack = '</script><img src=x onerror=alert(1)>&\u2028\u2029';
function island(html, id) {
  const match = html.match(new RegExp(`<script type="application/json" id="${id}">([\\s\\S]*?)</script>`));
  assert.ok(match, `${id} island exists`);
  assert.doesNotMatch(match[1], /[<>&\u2028\u2029]/);
  return JSON.parse(match[1]);
}
function fixture() {
  const core = makeCore();
  const env = {
    SESSION_SIGNING_KEY: 'coverage-views-signing-key', EDIT_ORIGIN: origin,
    EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1, instructor: 1 }, roger: { edit: 1 }, admin: { admin: 1 } }),
    EDIT_TOKEN_JOHN: 'coverage-john-opaque-token', EDIT_TOKEN_ROGER: 'coverage-roger-opaque-token',
    EDIT_TOKEN_ADMIN: 'coverage-admin-opaque-token',
    EDITOR: { getByName() { return core; } },
  };
  const fetch = (path, cookie = '', method = 'GET') => editorFetch(new Request(origin + path, { method, headers: { cookie } }), env, {});
  const login = async (slot) => {
    const response = await fetch(`/edit/history/?t=${env[`EDIT_TOKEN_${slot.toUpperCase()}`]}`);
    assert.equal(response.status, 302);
    assert.equal(response.headers.get('location'), '/edit/history/');
    return response.headers.get('set-cookie').split(';')[0];
  };
  return { core, fetch, login };
}
function seed(core, overrides = {}) {
  const result = core.suggest({ id: 'view-suggestion', editor: 'slot:john', scope: 'instructor', origin: 'human',
    kind: 'prose', page: null, block_anchor: 'instructor:0', source_ref: 'doc#p0', json_path: null,
    original_text: 'Original paragraph', original_hash: 'view-hash', new_text: attack, comment: null,
    context: '', map_version: 'view-map', group_id: null, ...overrides });
  assert.equal(result.ok, true);
  return result.suggestion;
}

test('history integration: cookie login, index links, canonical lookup and per-doc island agree', async () => {
  const { fetch, login } = fixture();
  const cookie = await login('john');
  const index = await fetch('/edit/history/', cookie);
  assert.equal(index.status, 200);
  assert.equal(index.headers.get('cache-control'), 'private, no-store');
  const html = await index.text();
  const docs = Object.values(HISTORY.docs);
  assert.ok(docs.length > 0);
  const doc = docs[0];
  assert.ok(html.includes(`/edit/history/${doc.slug}`));
  const page = await fetch(`/edit/history/${encodeURIComponent(doc.slug)}/`, cookie);
  assert.equal(page.status, 200);
  assert.deepEqual(island(await page.text(), 'history-data'), doc);
  assert.deepEqual(findDocBySlug(doc.slug), [doc.doc, doc]);
});

test('history lookup rejects missing, empty and prototype names', () => {
  for (const slug of [undefined, '', 'not-a-document', '__proto__', 'constructor']) assert.equal(findDocBySlug(slug), null);
});

test('history document escapes title and preserves hostile data only in JSON', async () => {
  const doc = { doc: attack, diffs: [{ html: attack }], revisions: [], slug: 'safe' };
  const response = renderHistoryPage(doc);
  assert.equal(response.headers.get('content-type'), 'text/html; charset=utf-8');
  const html = await response.text();
  assert.doesNotMatch(html, /<img src=x/);
  assert.deepEqual(island(html, 'history-data'), doc);
  assert.match(html, /window\.__HX_REVERT__="\/edit\/v1\/revert-request"/);
});

test('history index lists every bundled document alphabetically with accurate revision counts', async () => {
  const html = await renderHistoryIndex().text();
  let previous = -1;
  for (const doc of Object.values(HISTORY.docs).sort((a,b) => a.doc.localeCompare(b.doc, 'en', { sensitivity: 'variant' }))) {
    // Canonical paths in this bundle are ASCII and share the same sort order.
    const position = html.indexOf(`>${doc.doc}</a>`);
    assert.ok(position > previous, doc.doc);
    previous = position;
    const count = doc.revisions?.length || 0;
    assert.ok(html.slice(position, html.indexOf('</li>', position)).includes(`${count} revision${count === 1 ? '' : 's'}`));
  }
});

for (const path of ['/edit/history/', '/edit/history/missing', '/edit/instructor/m01/facts', '/edit/review']) {
  test(`protected view ${path} uses uniform denial for unauthenticated and wrong-method requests`, async () => {
    const { fetch, login } = fixture();
    const absent = await fetch(path);
    const cookie = await login(path === '/edit/review' ? 'admin' : 'john');
    const wrongMethod = await fetch(path, cookie, 'POST');
    assert.equal(absent.status, 404);
    assert.equal(wrongMethod.status, 404);
    assert.equal(await absent.text(), await wrongMethod.text());
  });
}

test('instructor integration: SQLite pending edits are filtered by editor and document then projected into real bundle', async () => {
  const { core, fetch, login } = fixture();
  const doc = resolveInstructorDoc('m01', 'facts');
  seed(core, { source_ref: doc.blocks[0].source_ref });
  seed(core, { id: 'other-editor', editor: 'slot:roger', source_ref: doc.blocks[1].source_ref, new_text: 'Private other editor' });
  seed(core, { id: 'other-doc', source_ref: 'unrelated/doc#b0', new_text: 'Unrelated text' });
  const response = await fetch('/edit/instructor/m01/facts/', await login('john'));
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-security-policy'), /script-src 'self'/);
  const html = await response.text();
  assert.ok(html.includes(doc.html));
  const map = island(html, 'editor-map-data');
  assert.equal(map.scope, 'instructor');
  assert.equal(map.source_ref, doc.source_ref);
  assert.equal(map.blocks.length, doc.blocks.length);
  const edits = island(html, 'edits-data').items;
  assert.equal(edits.length, 1);
  assert.equal(edits[0].new_text, attack);
  assert.equal(edits[0].attribution, 'JOS');
  assert.equal(edits[0].block_index, 0);
  assert.doesNotMatch(html, /Private other editor|Unrelated text|<img src=x/);
});

test('instructor empty store renders aliases as the same canonical document', async () => {
  const { fetch, login } = fixture();
  const cookie = await login('john');
  for (const type of ['notes', 'instructor-notes', 'instructor_notes', 'key', 'answer-key', 'answer_key']) {
    const doc = resolveInstructorDoc('m01', type);
    const response = await fetch(`/edit/instructor/m01/${type}`, cookie);
    assert.equal(response.status, 200);
    const html = await response.text();
    assert.equal(island(html, 'editor-map-data').source_ref, doc.source_ref);
    assert.deepEqual(island(html, 'edits-data'), { items: [] });
  }
});

test('instructor missing and insufficient-scope routes have identical responses', async () => {
  const { fetch, login } = fixture();
  const denied = await fetch('/edit/instructor/m01/facts', await login('roger'));
  const missing = await fetch('/edit/instructor/m99/unknown', await login('john'));
  const nested = await fetch('/edit/instructor/m01/facts/extra', await login('john'));
  assert.equal(denied.status, 404);
  assert.equal(missing.status, 404);
  assert.equal(nested.status, 404);
  const body = await denied.text();
  assert.equal(await missing.text(), body);
  assert.equal(await nested.text(), body);
});

test('instructor renderer handles absent optional block properties and escapes metadata', async () => {
  const doc = { matter_id: 'm01"<&', doc_type: 'facts"<&', source_ref: attack, html: '<p>Trusted bundle</p>',
    blocks: [{ index: 4, kind: 'prose', source_ref: attack, original_text: attack, original_hash: 'hash' },
      { index: 5, kind: 'json_scalar', source_ref: 'safe', original_text: 'text', original_hash: 'hash2', json_path: ['title'], has_inline_formatting: true, context: 'Heading' }] };
  const html = await renderInstructorDoc(doc, null).text();
  assert.doesNotMatch(html, /<img src=x|m01"<&/);
  const blocks = island(html, 'editor-map-data').blocks;
  assert.equal(blocks[0].context, '');
  assert.equal(blocks[0].json_path, null);
  assert.equal(blocks[0].has_inline_formatting, false);
  assert.deepEqual(blocks[1].json_path, ['title']);
  assert.equal(blocks[1].context, 'Heading');
  assert.equal(blocks[1].has_inline_formatting, true);
  assert.deepEqual(island(html, 'edits-data'), { items: [] });
});

test('instructor renderer supports a document without editable blocks', async () => {
  const html = await renderInstructorDoc({ matter_id: 'm01', doc_type: 'facts', source_ref: 'empty', html: '' }).text();
  assert.deepEqual(island(html, 'editor-map-data').blocks, []);
  assert.deepEqual(island(html, 'edits-data').items, []);
});

test('review integration: admin cookie loads real SQLite suggestions and revert requests with attribution', async () => {
  const { core, fetch, login } = fixture();
  seed(core);
  seed(core, { id: 'review-roger', editor: 'slot:roger', source_ref: 'other#p0', new_text: 'Second suggestion' });
  assert.equal(core.fileRevertRequest({ id: 'view-revert', editor: 'slot:john', doc: 'data/example.json', run_first: 'a'.repeat(40), run_last: 'b'.repeat(40) }).ok, true);
  const response = await fetch('/edit/review', await login('admin'));
  assert.equal(response.status, 200);
  const html = await response.text();
  const data = island(html, 'review-data');
  assert.equal(data.items.length, 2);
  assert.deepEqual(data.items.map(it => it.attribution).sort(), ['JOS', 'RSH']);
  assert.equal(data.items.find(it => it.editor === 'slot:john').new_text, attack);
  assert.match(html, /data-status="requested"/);
  assert.match(html, /aaaaaaaa…bbbbbbbb/);
  assert.match(html, /data\/example.json/);
  assert.doesNotMatch(html, /<img src=x/);
});

test('review empty SQLite store yields an empty digest and zero publisher count', async () => {
  const { fetch, login } = fixture();
  const response = await fetch('/edit/review', await login('admin'));
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.deepEqual(island(html, 'review-data').items, []);
  assert.match(html, /Publisher \(0 eligible\)/);
  assert.doesNotMatch(html, /rv-revert-row/);
});

test('review page denies edit-only viewers even when suggestions exist', async () => {
  const { core, fetch, login } = fixture();
  seed(core);
  const response = await fetch('/edit/review', await login('john'));
  assert.equal(response.status, 404);
  assert.doesNotMatch(await response.text(), /review-data|Original paragraph/);
});

test('review renderer escapes revert metadata and accepts missing optional fields', async () => {
  const html = await renderReviewPage(null, [{ status: attack, doc: attack, editor: 'unknown', run_first: attack, run_last: null }], 3).text();
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;\/script&gt;/);
  assert.match(html, /Publisher \(3 eligible\)/);
  assert.deepEqual(island(html, 'review-data').items, []);
  const empty = await renderReviewPage().text();
  assert.deepEqual(island(empty, 'review-data').items, []);
  assert.doesNotMatch(empty, /rv-revert-row/);
});

// The JSON import is the renderer's actual bundle. Restore it synchronously
// before awaiting Response.text(), so no other test observes fixture data.
function withHistoryDocs(docs, operation) {
  const saved = HISTORY.docs;
  try { HISTORY.docs = docs; return operation(); }
  finally { HISTORY.docs = saved; }
}

test('history empty bundle renders an explicit empty state without a list', async () => {
  const response = withHistoryDocs({}, () => renderHistoryIndex());
  const html = await response.text();
  assert.match(html, /No document history available yet/);
  assert.doesNotMatch(html, /<ul/);
});

test('history index escapes fixture paths and labels singular and missing revisions correctly', async () => {
  const response = withHistoryDocs({
    one: { doc: 'a<&', slug: 'a"<&', revisions: [{}], baselines: [{}] },
    two: { doc: 'b', slug: 'b', revisions: null, baselines: null },
  }, () => renderHistoryIndex());
  const html = await response.text();
  assert.match(html, /href="\/edit\/history\/a&quot;&lt;&amp;"/);
  assert.match(html, />a&lt;&amp;<\/a>/);
  assert.match(html, /1 revision · 1 baseline\(s\)/);
  assert.match(html, /0 revisions/);
});

test('history slug fallback resolves bundled explicit aliases despite canonical encoding drift', () => {
  const doc = { doc: 'data/example.json', slug: 'legacy-alias', revisions: [] };
  withHistoryDocs({ 'data/example.json': doc }, () => {
    assert.deepEqual(findDocBySlug('legacy-alias'), ['data/example.json', doc]);
    assert.deepEqual(findDocBySlug('data__example.json'), ['data/example.json', doc]);
    assert.equal(findDocBySlug('unknown'), null);
  });
});

test('history router rejects a missing document after successful authentication', async () => {
  const { fetch, login } = fixture();
  const response = await fetch('/edit/history/not-bundled', await login('john'));
  assert.equal(response.status, 404);
  assert.doesNotMatch(await response.text(), /history-data/);
});

test('history admin-only scope does not grant editor history access', async () => {
  const { fetch, login } = fixture();
  const cookie = await login('admin');
  for (const path of ['/edit/history', `/edit/history/${Object.values(HISTORY.docs)[0].slug}`]) {
    assert.equal((await fetch(path, cookie)).status, 404);
  }
});

test('history absent docs and generation timestamp degrade to a usable empty index', async () => {
  const generated = HISTORY.generated_at;
  let response;
  try {
    HISTORY.generated_at = null;
    response = withHistoryDocs(undefined, () => {
      assert.equal(findDocBySlug('missing'), null);
      return renderHistoryIndex();
    });
  } finally { HISTORY.generated_at = generated; }
  const html = await response.text();
  assert.match(html, /Generated \./);
  assert.match(html, /No document history available yet/);
});

test('history alias lookup tolerates an empty bundle entry while finding a later document', () => {
  const doc = { doc: 'real', slug: 'alias', revisions: [] };
  withHistoryDocs({ empty: null, real: doc }, () => {
    assert.deepEqual(findDocBySlug('alias'), ['real', doc]);
  });
});

test('history index preserves distinct links when documents have equal display names', async () => {
  const response = withHistoryDocs({
    first: { doc: 'duplicate', slug: 'first', revisions: [] },
    second: { doc: 'duplicate', slug: 'second', revisions: [] },
  }, () => renderHistoryIndex());
  const html = await response.text();
  assert.match(html, /href="\/edit\/history\/first"/);
  assert.match(html, /href="\/edit\/history\/second"/);
});
