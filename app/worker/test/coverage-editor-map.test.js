import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolvePagePath, buildUpstreamUrl, lookupBlocks, validateJsonScalar,
  projectReviewAnnotations, projectPendingItems, pageBlockDescriptors,
  escapeJsonIsland, EDITOR_MAP } from '../src/editor-map.js';
import { EditorStore, durableObject } from './coverage-runtime-helper.mjs';

test('malformed percent escapes and control characters cannot enter the page allowlist', () => {
  for (const path of ['%', '%zz', '%E0%A4%A', '/%00index.html', '%7findex.html', 'x\\index.html']) {
    assert.equal(resolvePagePath(path), null, path);
  }
});

test('upstream URL rejects invalid deployment URLs and query or fragment smuggling', () => {
  assert.equal(buildUpstreamUrl('index.html', 'not a URL'), null);
  for (const path of ['index.html?q=secret', 'index.html#fragment', '../outside.html']) {
    assert.equal(buildUpstreamUrl(path, 'https://public.example.test/platform/'), null);
  }
});

test('shared scalar validation fails closed when an occurrence disagrees with the authoritative descriptor', () => {
  const block = Object.values(EDITOR_MAP.pages).flat().find(b => b.kind === 'json_scalar');
  assert.ok(block);
  const occurrences = lookupBlocks(block.source_ref, 'edit');
  const originalLength = occurrences.length;
  try {
    occurrences.push({ ...occurrences[0], json_path: 'forged.path' });
    assert.equal(validateJsonScalar(block.source_ref, null, 'edit'), null);
    occurrences[occurrences.length - 1] = { ...occurrences[0], kind: 'prose' };
    assert.equal(validateJsonScalar(block.source_ref, block.json_path, 'edit'), null);
  } finally {
    occurrences.length = originalLength;
  }
  assert.ok(validateJsonScalar(block.source_ref, null, 'edit'));
});

test('annotation projection handles unanswered moves and stale decisions without inventing a reviewer', () => {
  assert.deepEqual(projectReviewAnnotations(undefined), []);
  assert.deepEqual(projectReviewAnnotations([{}]), []);
  const review = { source_ref: 'fixture#p0', operations: [{ id: 'move-from', decision_id: 'move', kind: 'move',
    proposed_range: 'invalid', move_pair_id: 'pair', move_role: 'from' }] };
  const unanswered = projectReviewAnnotations([review])[0];
  assert.equal(unanswered.status, 'unanswered');
  assert.equal(unanswered.reviewer, 'Publisher');
  assert.equal(unanswered.proposed_range, null);
  assert.equal(unanswered.old_text, '');
  assert.equal(unanswered.note, '');
  const stale = projectReviewAnnotations([{ ...review, stale: true,
    decisions: [{ operation_id: 'move', decision: 'accept', note: 'old decision' }] }])[0];
  assert.equal(stale.status, 'stale');
  assert.equal(stale.note, 'old decision');
  assert.equal(stale.decision_id, 'move');
});

test('stored comment projection and JSON island preserve inert data without exposing an edit overlay', async t => {
  const fixture = await durableObject(EditorStore);
  t.after(fixture.close);
  const [page, blocks] = Object.entries(EDITOR_MAP.pages).find(([, blocks]) => blocks.length);
  const b = blocks[0];
  const comment = '</script><img src=x>\u2028';
  const inserted = fixture.object.suggest({ id: 'map-projection', editor: 'slot:john', scope: 'edit', origin: 'human',
    kind: 'comment', page, block_anchor: `${page}:${b.index}`, source_ref: b.source_ref,
    original_text: b.original_text, original_hash: b.original_hash, comment, new_text: null,
    map_version: 'fixture', context: '', json_path: b.json_path || null, group_id: null });
  assert.equal(inserted.ok, true);
  const rows = projectPendingItems(fixture.object.listForPage(page), 4);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].preview, '</sc');
  assert.equal(rows[0].attribution, 'JOS');
  assert.equal(Object.hasOwn(rows[0], 'new_text'), false);
  const descriptor = pageBlockDescriptors(resolvePagePath(page).blocks).find(d => d.source_ref === b.source_ref);
  assert.equal(rows[0].base_hash, descriptor.original_hash);
  const island = escapeJsonIsland({ items: rows });
  assert.doesNotMatch(island, /[<>]/);
  assert.deepEqual(JSON.parse(island), { items: rows });
});
