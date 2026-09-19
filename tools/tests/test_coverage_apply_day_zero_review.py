"""Approved review integrity, CLI replay, and real atomic file updates."""
import copy
import json
import sys

import pytest

from test_coverage_review_proposal import pending, publish, approve, apply, review


def test_apply_cli_writes_replays_and_verifies_tmp_governed_pair(pending, monkeypatch, capsys):
    repo, proposal, holdouts, audit = pending
    publish(repo, proposal)
    digest = approve(repo)
    before = copy.deepcopy((proposal, holdouts, audit))
    monkeypatch.setattr(sys, 'argv', ['apply', '--repo', str(repo), '--write'])
    assert apply.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['state'] == 'applied' and result['approval_sha256'] == digest
    governed = [repo / review.HOLDOUTS_REL, repo / review.AUDIT_REL]
    first = [path.read_bytes() for path in governed]
    actual_holdouts, actual_audit = [json.loads(data) for data in first]
    assert actual_holdouts['summary']['count'] == 2
    assert actual_audit['summary']['converted_dates'] == 2
    assert actual_audit['summary']['reconciled_category_total'] == 4
    assert all(r['locator'].startswith('raw:') for r in actual_audit['entries'])
    assert apply.main() == 0
    assert [path.read_bytes() for path in governed] == first
    capsys.readouterr()
    monkeypatch.setattr(sys, 'argv', ['apply', '--repo', str(repo)])
    assert apply.main() == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'verified'
    assert (proposal, holdouts, audit) == before
    assert not list((repo / 'data').glob('.*.tmp'))


@pytest.mark.parametrize('kind,message', [('key', 'key does not match'), ('duplicate', 'duplicate proposal'), ('count', 'attention count is stale'), ('inventory', 'does not reconcile')])
def test_apply_rejects_inconsistent_inputs(pending, kind, message):
    repo, proposal, holdouts, audit = pending
    if kind == 'key':
        proposal['proposals'][0]['key'] = 'not-the-key'
    elif kind == 'duplicate':
        proposal['proposals'].append(copy.deepcopy(proposal['proposals'][0]))
    elif kind == 'count':
        audit['summary']['attention_required'] = 20
    else:
        audit['summary']['full_date_inventory_total'] = 20
    before = copy.deepcopy((proposal, holdouts, audit))
    with pytest.raises(ValueError, match=message):
        apply.apply_review(repo, proposal, holdouts, audit)
    assert (proposal, holdouts, audit) == before


@pytest.mark.parametrize('category', ['holdout', 'anchor_attention'])
@pytest.mark.parametrize('kind', ['anchor', 'disposition'])
def test_apply_rejects_unapproved_disposition_and_wrong_anchor(pending, category, kind):
    repo, proposal, holdouts, audit = pending
    row = next(r for r in proposal['proposals'] if r['category'] == category and r['proposed_disposition'].startswith('convertible'))
    if kind == 'anchor':
        row['matter_anchor'] = '2024-01-01'
        message = 'no matching matter anchor'
    else:
        row['proposed_disposition'] = 'needs_subject_matter_judgment'
        message = 'unsupported approved'
    with pytest.raises(ValueError, match=message):
        apply.apply_review(repo, proposal, holdouts, audit)


def test_existing_conversion_cannot_duplicate_new_conversion(pending):
    repo, proposal, holdouts, audit = pending
    row = next(r for r in proposal['proposals'] if r['proposed_disposition'] == 'convertible')
    audit['entries'].append(apply._approved_conversion(repo, row, 'start'))
    audit['summary']['full_date_inventory_total'] += 1
    with pytest.raises(ValueError, match='identities are not unique'):
        apply.apply_review(repo, proposal, holdouts, audit)


@pytest.mark.parametrize('which', ['holdout', 'audit'])
def test_validate_applied_detects_tampering(pending, which):
    repo, proposal, holdouts, audit = pending
    resolved_holdouts, resolved_audit = apply.apply_review(repo, proposal, holdouts, audit)
    if which == 'holdout':
        resolved_holdouts['entries'][0]['reason'] = 'changed reason'
    else:
        resolved_audit['entries'][0]['review_reason'] = 'changed reason'
    with pytest.raises(ValueError, match='do not match the approved proposal'):
        apply.validate_applied_review(repo, proposal, resolved_holdouts, resolved_audit)


@pytest.mark.parametrize('approval', [None, 'different proposal digest'])
def test_missing_or_unrelated_approval_does_not_mutate_artifacts(pending, monkeypatch, approval):
    repo, proposal, _, _ = pending
    publish(repo, proposal)
    if approval is not None:
        (repo / review.APPROVAL_REL).write_text(approval)
    paths = [repo / review.HOLDOUTS_REL, repo / review.AUDIT_REL]
    before = [p.read_bytes() for p in paths]
    monkeypatch.setattr(sys, 'argv', ['apply', '--repo', str(repo), '--write'])
    with pytest.raises(ValueError, match='missing or names another proposal digest'):
        apply.main()
    assert [p.read_bytes() for p in paths] == before


def test_empty_review_resolves_and_validates(tmp_path):
    proposal = {'proposals': []}
    holdouts = {'entries': []}
    audit = {'entries': [], 'attention_required': [], 'matter_anchors': [], 'summary': {'attention_required': 0, 'full_date_inventory_total': 0}}
    resolved_holdouts, resolved_audit = apply.apply_review(tmp_path, proposal, holdouts, audit)
    assert resolved_holdouts['summary'] == {'count': 0, 'by_reason': {}}
    assert resolved_audit['summary']['converted_dates'] == 0
    apply.validate_applied_review(tmp_path, proposal, resolved_holdouts, resolved_audit)


@pytest.mark.parametrize('phase', ['chmod', 'fsync'])
def test_staging_failures_preserve_original_and_remove_temporary_file(tmp_path, monkeypatch, phase):
    destination = tmp_path / 'report.json'
    destination.write_text('original')
    def fail(*args):
        raise OSError('injected staging failure')
    monkeypatch.setattr(apply.os, phase, fail)
    with pytest.raises(OSError, match='injected staging failure'):
        apply._stage_bytes(destination, b'new report')
    assert destination.read_text() == 'original'
    assert list(tmp_path.iterdir()) == [destination]


def test_pair_replacement_preserves_modes_across_directories(tmp_path):
    first = tmp_path / 'a' / 'one.json'
    second = tmp_path / 'b' / 'two.json'
    for path, mode in [(first, 0o640), (second, 0o600)]:
        path.parent.mkdir()
        path.write_text('old')
        path.chmod(mode)
    apply._write_governed_pair(first, 'new one', second, 'new two')
    assert first.read_text() == 'new one'
    assert second.read_text() == 'new two'
    assert first.stat().st_mode & 0o777 == 0o640
    assert second.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.rglob('*.tmp'))
