"""Temporary governed reports exercise proposal validation before and after approval."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_day_zero_review_proposal as review
import apply_day_zero_review as apply


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


@pytest.fixture
def pending(tmp_path):
    rows = []
    for category, name, disposition in [
        ('holdout', 'fixed', 'declared_holdout'),
        ('holdout', 'relative', 'convertible'),
        ('anchor_attention', 'raw', 'convertible_after_durable_locator_added'),
        ('anchor_attention', 'outside', 'declared_out_of_anchor_holdout'),
    ]:
        source = f'data/{name}.md'
        (tmp_path / 'data').mkdir(exist_ok=True)
        (tmp_path / source).write_text(f'{name} event 2025-01-02\n')
        row = dict(category=category, source=source, locator='line:1:raw-occurrence:1',
                   literal='2025-01-02', matter='m01',
                   current_state='candidate_pending_human_review' if category == 'holdout' else 'attention_required',
                   proposed_disposition=disposition, reason_code='historical', rationale='Keep the recorded fact.',
                   confidence='high', needs_john=False, context_excerpt=f'{name} event 2025-01-02',
                   matter_anchor='2025-01-01', proposed_day_zero_offset=1)
        row['key'] = review._key(category, row)
        rows.append(row)
    holdouts = {'entries': [dict(source=r['source'], locator=r['locator'], literal=r['literal'], review_status='candidate_pending_human_review') for r in rows if r['category'] == 'holdout']}
    audit = {'entries': [], 'attention_required': [dict(source=r['source'], locator=r['locator'], literal=r['literal']) for r in rows if r['category'] == 'anchor_attention'], 'matter_anchors': [{'matter_slug': 'm01', 'anchor': '2025-01-01', 'reason': 'start'}], 'summary': {'attention_required': 2, 'full_date_inventory_total': 4}}
    write_json(tmp_path / review.HOLDOUTS_REL, holdouts)
    write_json(tmp_path / review.AUDIT_REL, audit)
    proposal = {'proposals': sorted(rows, key=lambda r: r['key']), 'governed_inputs': {
        str(review.HOLDOUTS_REL): {'sha256': review._sha256(tmp_path / review.HOLDOUTS_REL), 'pending_count': 2},
        str(review.AUDIT_REL): {'sha256': review._sha256(tmp_path / review.AUDIT_REL), 'attention_required_count': 2}}}
    return tmp_path, proposal, holdouts, audit


def publish(repo, proposal):
    path = repo / review.PROPOSAL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(review.render_proposal(proposal))
    sheet = repo / review.SHEET_REL
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text(review.render_sheet(proposal))


def approve(repo):
    digest = hashlib.sha256((repo / review.PROPOSAL_REL).read_bytes()).hexdigest()
    (repo / review.APPROVAL_REL).write_text(f'Approved proposal SHA256 {digest}\n')
    return digest


def test_pending_report_to_approved_artifacts_cli_chain(pending, monkeypatch, capsys):
    repo, proposal, holdouts, audit = pending
    publish(repo, proposal)
    monkeypatch.setattr(sys, 'argv', ['validate', '--repo', str(repo)])
    assert review.main() == 0
    assert json.loads(capsys.readouterr().out)['total'] == 4
    approve(repo)
    resolved_holdouts, resolved_audit = apply.apply_review(repo, proposal, holdouts, audit)
    write_json(repo / review.HOLDOUTS_REL, resolved_holdouts)
    write_json(repo / review.AUDIT_REL, resolved_audit)
    assert review.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'valid'


@pytest.mark.parametrize('field,value,message', [
    ('category', 'unknown', 'invalid category'),
    ('current_state', 'approved', 'misstates'),
    ('confidence', 'certain', 'invalid confidence'),
    ('needs_john', 1, 'invalid confidence'),
    ('reason_code', None, 'empty reason'),
    ('rationale', '  ', 'empty reason'),
    ('context_excerpt', 'no date here', 'does not contain literal'),
    ('context_excerpt', 'changed 2025-01-02', 'stored context is stale'),
    ('matter_anchor', '2024-01-01', 'wrong matter anchor'),
    ('proposed_day_zero_offset', 2, 'wrong offset'),
])
def test_invalid_row_fields(pending, field, value, message):
    repo, proposal, _, _ = pending
    row = next(r for r in proposal['proposals'] if r['proposed_disposition'] == 'convertible')
    row[field] = value
    with pytest.raises(ValueError, match=message):
        review.validate_proposal(repo, proposal)


def test_missing_required_field(pending):
    repo, proposal, _, _ = pending
    del proposal['proposals'][0]['rationale']
    with pytest.raises(ValueError, match='missing.*rationale'):
        review.validate_proposal(repo, proposal)


@pytest.mark.parametrize('kind', ['state', 'count', 'missing', 'digest', 'approved-digest'])
def test_governed_integrity_rejections(pending, kind):
    repo, proposal, holdouts, audit = pending
    if kind == 'state':
        holdouts['entries'][0]['review_status'] = 'approved'
        write_json(repo / review.HOLDOUTS_REL, holdouts)
        message = 'pending review states'
    elif kind == 'count':
        audit['summary']['attention_required'] = 99
        write_json(repo / review.AUDIT_REL, audit)
        message = 'attention-required set'
    elif kind == 'missing':
        proposal['proposals'].pop()
        message = 'coverage mismatch'
    else:
        proposal['governed_inputs'][str(review.HOLDOUTS_REL)]['sha256'] = 'broken'
        message = 'hashes or counts are stale'
        if kind == 'approved-digest':
            publish(repo, proposal)
            approve(repo)
            message = 'digest is invalid'
    with pytest.raises(ValueError, match=message):
        review.validate_proposal(repo, proposal)


@pytest.mark.parametrize('locator,literal,message', [
    ('b:aaaaaaaa:0', '2025-01-02', 'expected one source block'),
    ('b:12345678:year:1', '2025', 'year offset'),
    ('b:12345678:8', '2025-01-02', 'date ordinal'),
    ('b:12345678:0', '2026-01-02', 'date ordinal'),
])
def test_block_context_rejects_unresolvable_identity(tmp_path, locator, literal, message):
    (tmp_path / 'source.md').write_text('Event 2025-01-02 {#b:12345678}\n')
    with pytest.raises(ValueError, match=message):
        review._context_for(tmp_path, dict(source='source.md', locator=locator, literal=literal))


def test_json_scalar_resolution_and_missing_scalar(tmp_path):
    write_json(tmp_path / 'source.json', {'items': [{'text': 'Event 2025-01-02'}, 1, None]})
    row = dict(source='source.json', locator='items.0.text:date:0', literal='2025-01-02')
    assert review._context_for(tmp_path, row) == 'Event 2025-01-02'
    row['locator'] = 'items.1.text:date:0'
    with pytest.raises(ValueError, match='JSON scalar no longer resolves'):
        review._context_for(tmp_path, row)


def test_fallback_context_requires_unique_literal(tmp_path):
    path = tmp_path / 'plain.md'
    path.write_text('Unique 2025\n')
    row = dict(source='plain.md', locator='legacy', literal='2025')
    assert review._context_for(tmp_path, row) == 'Unique 2025'
    path.write_text('2025 and 2025')
    with pytest.raises(ValueError, match='unsupported ambiguous locator'):
        review._context_for(tmp_path, row)
    with pytest.raises(ValueError, match='was not resolved'):
        review._compact('nothing here', '2025')


def test_sheet_empty_and_judgment_batches(pending):
    empty = review.render_sheet({'proposals': []})
    assert 'Total one-to-one proposals: **0**' in empty
    assert 'None.' in empty
    _, proposal, _, _ = pending
    row = proposal['proposals'][0]
    rows = [dict(row, key=f'judgment-{i}', needs_john=True) for i in range(11)]
    sheet = review.render_sheet({'proposals': rows})
    assert '### Batch 1' in sheet and '### Batch 2' in sheet
    assert '11-item' in sheet
    assert sheet.count('— Keep the recorded fact.') == 11
    assert 'shared by another date' in sheet


@pytest.mark.parametrize('kind', ['missing', 'json', 'sheet'])
def test_cli_artifact_failures(pending, monkeypatch, kind):
    repo, proposal, _, _ = pending
    if kind != 'missing':
        publish(repo, proposal)
        path = repo / (review.PROPOSAL_REL if kind == 'json' else review.SHEET_REL)
        path.write_text(path.read_text() + '\n')
    monkeypatch.setattr(sys, 'argv', ['validate', '--repo', str(repo)])
    with pytest.raises(SystemExit, match={'missing': 'artifacts are missing', 'json': 'normalized', 'sheet': 'sheet is stale'}[kind]):
        review.main()


def test_json_string_array_and_year_block_context(tmp_path):
    write_json(tmp_path / 'source.json', {'items': ['Event 2025 {#b:12345678}', 'Other 2026']})
    assert list(review._json_scalars(json.loads((tmp_path / 'source.json').read_text()))) == [
        ('items.0', 'Event 2025 {#b:12345678}'), ('items.1', 'Other 2026')]
    row = dict(source='source.json', locator='b:12345678:year:6', literal='2025')
    assert review._context_for(tmp_path, row) == 'Event 2025'
