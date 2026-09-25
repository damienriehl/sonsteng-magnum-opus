import { test } from 'node:test';
import assert from 'node:assert/strict';
import { publisherViewModel, renderPublisherPage } from '../src/editor-publisher.js';
import { EditorStore, durableObject } from './coverage-runtime-helper.mjs';

function review(extra = {}) {
  return { revisions: [{ revision: { id: 'revision', source_revision: 'source', prod_base: 'base',
    source_ref: 'site/platform/fixture.html#body', operations: [{ id: 'op', kind: 'replace',
      old_text: 'Before', new_text: 'After' }] }, ...extra }] };
}

test('publisher empty SQLite ledger renders a truthful non-authorizing empty state', async t => {
  const fixture = await durableObject(EditorStore);
  t.after(fixture.close);
  const context = fixture.object.publisherContext();
  const html = await renderPublisherPage(context).text();
  assert.match(html, /No complete DEV apply batch is eligible/);
  assert.match(html, /No enclosed change detail/);
  assert.doesNotMatch(html, /id="pub-authorize"/);
  assert.equal(publisherViewModel(context).eligibleChanges, 0);
});

test('publisher malformed optional batches and unknown state remain inert and non-authorizing', async () => {
  const context = { batches: 'bad', release: { state: 'future_state<script>' } };
  assert.equal(publisherViewModel(context).productionStatus, 'future state<script>');
  const html = await renderPublisherPage(context, '<viewer>').text();
  assert.doesNotMatch(html, /future_state<script>|<viewer>|id="pub-authorize"/);
  assert.match(html, /future_state&lt;script&gt;/);
});

test('schema-v2 preview uses frozen operation IDs when expanded members are absent', async () => {
  const html = await renderPublisherPage({ release: { state: 'verified', schema_version: 2,
    operation_ids: ['op-one', 'op-two'] } }).text();
  assert.match(html, /Frozen atomic operation 1/);
  assert.match(html, /Frozen atomic operation 2/);
  assert.match(html, /op-one/);
  assert.doesNotMatch(html, /id="pub-authorize"/);
});

test('schema-v2 preview clearly reports an empty frozen membership', async () => {
  const html = await renderPublisherPage({ release: { state: 'verified', schema_version: 2 } }).text();
  assert.match(html, /No frozen atomic operation membership/);
});

test('stale review disables its controls while retaining the prior question as escaped text', async () => {
  const html = await renderPublisherPage({ review: review({ stale: true,
    draft: { decisions: [{ operation_id: 'op', decision: 'questioned', note: '</textarea><script>bad</script>' }] } }) }).text();
  assert.match(html, /data-review-status="stale"/);
  assert.match(html, /<fieldset class="pub-decision" disabled>/);
  assert.match(html, /id="question-op"[^>]*required[^>]*disabled/);
  assert.doesNotMatch(html, /<script>bad/);
  assert.match(html, /&lt;\/textarea&gt;/);
});

test('submitted review takes precedence over a private draft and freezes decision controls', async () => {
  const html = await renderPublisherPage({ review: review({
    submitted_review: { id: 'submitted', decisions: [{ operation_id: 'op', decision: 'rejected', note: 'Final reason' }] },
    draft: { decisions: [{ operation_id: 'op', decision: 'accepted' }] },
  }) }).text();
  assert.match(html, /data-review-status="rejected"/);
  assert.match(html, /value="rejected" checked disabled/);
  assert.match(html, /Final reason/);
  assert.match(html, /Submitted review: submitted/);
});

test('unknown structural dependencies explain the hold without exposing decision controls', async () => {
  const r = review();
  r.revisions[0].revision.operations[0].production_hold_reason = 'dependent_prose';
  const html = await renderPublisherPage({ review: r }).text();
  assert.match(html, /depends on a structurally held block/);
  assert.match(html, /data-review-status="held"/);
  assert.doesNotMatch(html, /name="decision-op"/);
});

test('long before and after context is bounded on opposite sides of an atomic redline', async () => {
  const r = review();
  r.revisions[0].revision.operations[0].context_before = ['BEGIN', 'b'.repeat(180)];
  r.revisions[0].revision.operations[0].context_after = ['a'.repeat(180), 'END'];
  const html = await renderPublisherPage({ review: r }).text();
  // The serialized review island deliberately keeps full source evidence; the
  // rendered excerpt alone must use bounded nearest-neighbor context.
  const excerpt = html.match(/<article class="pub-operation"[\s\S]*?<\/article>/)[0];
  assert.doesNotMatch(excerpt, /BEGIN|END/);
  assert.ok(excerpt.includes('b'.repeat(180)));
  assert.ok(excerpt.includes('a'.repeat(180)));
});
