"""Offline evidence-to-ledger backfill, including deterministic CLI audits."""
import copy
import json

import pytest

from test_build_prod_review_backfill import evidence
import build_prod_review_backfill as backfill


def test_cli_build_check_and_tamper_detection(tmp_path):
    source, output = tmp_path / 'evidence.json', tmp_path / 'payload.json'
    source.write_text(json.dumps(evidence()), encoding='utf-8')
    args = ['--input', str(source), '--output', str(output), '--migration-id', 'review-α', '--prod-base', 'a' * 40]
    assert backfill.main(args) == 0
    first = output.read_bytes()
    payload = json.loads(first)
    assert payload['migration_id'] == 'review-α'
    assert len(payload['revisions']) == 2
    assert all(row['operations'] for row in payload['revisions'])
    assert backfill.main(args + ['--check']) == 0
    assert output.read_bytes() == first
    output.write_text('{}\n')
    with pytest.raises(backfill.BackfillError, match='deterministic evidence'):
        backfill.main(args + ['--check'])
    assert output.read_text() == '{}\n'
    output.unlink()
    with pytest.raises(backfill.BackfillError, match='deterministic evidence'):
        backfill.main(args + ['--check'])
    assert not output.exists()


@pytest.mark.parametrize('mutation,message', [
    ('schema', 'unsupported'), ('suggestions', 'empty'), ('batches', 'empty'),
    ('disconnected', 'unambiguous'), ('cycle', 'unambiguous'),
    ('phase', 'incomplete'), ('commit', 'incomplete'), ('unknown-batch', 'completed batch'),
    ('source', 'durable'), ('no-op', 'source identity'),
])
def test_invalid_evidence_is_rejected_without_mutation(mutation, message):
    data = evidence()
    if mutation == 'schema':
        data['schema_version'] = 2
    elif mutation in ('suggestions', 'batches'):
        data[mutation] = []
    elif mutation == 'disconnected':
        data['batches'][1]['base_sha'] = 'd' * 40
    elif mutation == 'cycle':
        data['batches'][0]['commit_sha'] = 'a' * 40
    elif mutation == 'phase':
        data['batches'][0]['phase'] = 'applying'
    elif mutation == 'commit':
        data['batches'][0]['commit_sha'] = ''
    elif mutation == 'unknown-batch':
        data['suggestions'][0]['apply_batch_id'] = 'missing'
    elif mutation == 'source':
        data['suggestions'][0].pop('source_ref')
    else:
        data['suggestions'][0]['new_text'] = data['suggestions'][0]['original_text']
    before = copy.deepcopy(data)
    with pytest.raises(backfill.BackfillError, match=message):
        backfill.build_payload(data, 'migration', 'a' * 40)
    assert data == before


def test_shuffled_batches_and_suggestions_produce_cumulative_source_revision():
    data = evidence()
    first, last = data['suggestions']
    last.update(source_ref=first['source_ref'], original_text=first['new_text'], new_text='Final café copy.')
    expected = backfill.build_payload(data, 'migration', 'a' * 40)
    data['batches'].reverse()
    data['suggestions'].reverse()
    assert backfill.build_payload(data, 'migration', 'a' * 40) == expected
    revision, = expected['revisions']
    assert revision['source_original_text'] == 'Old copy.'
    assert revision['source_proposed_text'] == 'Final café copy.'
    assert revision['source_revision'] == 'c' * 40
    assert [row['suggestion_id'] for row in revision['suggestion_evidence']] == ['s1', 's2']
    assert len(revision['batch_chain']) == 2
