"""Real commit evidence, legacy restoration, and reconciliation CLI boundaries."""
import copy
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_legacy_review_reconciliation as reconciliation
from build_prod_review_backfill import BackfillError
from test_build_legacy_review_reconciliation import legacy_repo, git, write_json


def commit(repo, message):
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-qm', message], cwd=repo, check=True)
    return git(repo, 'rev-parse', 'HEAD')


def test_cli_writes_canonical_payload_checks_and_detects_drift(legacy_repo, tmp_path):
    repo, base, evidence, classification = legacy_repo
    ep, cp, out = [tmp_path / n for n in ['evidence.json', 'classification.json', 'out.json']]
    write_json(ep, evidence)
    write_json(cp, classification)
    argv = ['--repo', str(repo), '--prod-base', base, '--migration-id', 'test',
            '--evidence', str(ep), '--classification', str(cp), '--output', str(out)]
    assert reconciliation.main(argv) == 0
    payload = json.loads(out.read_text())
    assert payload['revisions'][0]['suggestion_ids'] == ['effective']
    assert payload['exclusions'][0]['suggestion_id'] == 'reverted'
    assert reconciliation.main(argv + ['--check']) == 0
    out.write_text(out.read_text() + '\n')
    with pytest.raises(BackfillError, match='payload drift'):
        reconciliation.main(argv + ['--check'])


@pytest.mark.parametrize('legacy_locator', [False, True])
def test_exclusion_can_prove_restoration_without_explicit_base(legacy_repo, legacy_locator):
    repo, base, evidence, classification = legacy_repo
    classification['exclusions'][0].pop('proof_base_sha')
    if legacy_locator:
        evidence['suggestions'][1]['source_ref'] = 'data/copy/reverted.json#p1'
    payload = reconciliation.build_reconciliation(evidence, classification, repo, 'x', base)
    assert payload['exclusions'][0]['reason'] == 'reverted_legacy_uat'
    write_json(repo / 'data/copy/reverted.json', {'lead': 'Still changed'})
    commit(repo, 'unrestored')
    with pytest.raises(BackfillError, match='excluded prose is not restored'):
        reconciliation.build_reconciliation(evidence, classification, repo, 'x', base)


@pytest.mark.parametrize('section', ['batches', 'effective_ids', 'exclusions'])
def test_duplicate_classification_and_batch_ids_fail_closed(legacy_repo, section):
    repo, base, evidence, classification = legacy_repo
    container = evidence if section == 'batches' else classification
    container[section].append(copy.deepcopy(container[section][0]))
    with pytest.raises(BackfillError, match='identities must be unique'):
        reconciliation.build_reconciliation(evidence, classification, repo, 'x', base)


@pytest.mark.parametrize('commit_value', [None, '', 123, 'not-a-real-revision'])
def test_invalid_commit_evidence_is_not_accepted(legacy_repo, commit_value):
    repo, _, evidence, _ = legacy_repo
    assert not reconciliation._commit_matches_batch(repo, commit_value, evidence['batches'][0])


def test_json_embedded_block_resolution_and_ambiguity(legacy_repo):
    repo, _, _, _ = legacy_repo
    path = repo / 'embedded.json'
    write_json(path, {'body': 'First {#b:11111111}\n\nSecond {#b:22222222}'})
    sha = commit(repo, 'embedded block source')
    assert reconciliation._source_value(repo, sha, 'embedded.json', 'body.b22222222') == 'Second'
    with pytest.raises(BackfillError, match='not uniquely resolvable'):
        reconciliation._source_value(repo, sha, 'embedded.json', 'body.b99999999')


@pytest.mark.parametrize('kind,bid,new,before,after,expected', [
    ('insert_after', '11111111', 'New', 'Anchor {#b:11111111}', 'Other {#b:22222222}', False),
    ('move', '11111111', None, 'Anchor {#b:11111111}', 'Other {#b:22222222}', False),
    ('delete', '99999999', None, 'Anchor {#b:11111111}', 'Other {#b:22222222}', False),
    ('unexpected', '11111111', None, 'Anchor {#b:11111111}', 'Other {#b:22222222}', False),
    ('prose', 'p1', 'New', 'Old unique', 'New', True),
    ('prose', 'p1', 'New', 'Old unique\nOld unique', 'New', False),
])
def test_transition_proofs_reject_missing_anchors_and_ambiguous_legacy_text(
        legacy_repo, kind, bid, new, before, after, expected):
    repo, _, _, _ = legacy_repo
    path = repo / 'source.md'
    path.write_text(before)
    base = commit(repo, 'source base')
    path.write_text(after)
    sha = commit(repo, 'apply: batch test transition')
    row = {'source_ref': 'source.md#' + bid, 'kind': kind,
           'original_text': 'Old unique', 'new_text': new}
    assert reconciliation._commit_applies_suggestion(
        repo, sha, {'batch_id': 'test', 'base_sha': base}, row) is expected


def test_missing_source_at_named_commit_fails_proof(legacy_repo):
    repo, _, evidence, _ = legacy_repo
    batch = evidence['batches'][0]
    assert not reconciliation._commit_applies_suggestion(repo, batch['commit_sha'], batch,
        {'source_ref': 'absent.md#b11111111', 'kind': 'prose', 'original_text': 'a', 'new_text': 'b'})


@pytest.mark.parametrize('kind', ['insert_after', 'move'])
def test_structural_exclusion_requires_exact_proof_base(legacy_repo, kind):
    repo, base, evidence, classification = legacy_repo
    # The actual scalar transition proves the commit, then the structural policy
    # requires byte-for-byte restoration evidence rather than a scalar match.
    evidence['suggestions'][1]['kind'] = kind
    classification['exclusions'][0].pop('proof_base_sha')
    with pytest.raises(BackfillError, match='structural exclusion requires an exact proof base'):
        reconciliation.build_reconciliation(evidence, classification, repo, 'x', base)


@pytest.mark.parametrize('change', ['empty', 'overlap', 'unknown', 'missing_batch'])
def test_classification_and_effective_evidence_reject_inconsistent_sets(legacy_repo, change):
    repo, base, evidence, classification = legacy_repo
    if change == 'empty':
        classification['effective_ids'] = []
    elif change == 'overlap':
        classification['effective_ids'].append('reverted')
    elif change == 'unknown':
        classification['effective_ids'].append('absent')
    else:
        evidence['batches'] = evidence['batches'][1:]
    expected = 'lacks exact apply commit' if change == 'missing_batch' else 'cover every legacy suggestion'
    with pytest.raises(BackfillError, match=expected):
        reconciliation.build_reconciliation(evidence, classification, repo, 'x', base)
