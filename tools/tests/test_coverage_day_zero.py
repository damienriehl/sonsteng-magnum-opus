"""Offline corpus conversion, report generation, and atomic file boundaries."""
from datetime import date
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero as dz
from test_day_zero import _approve_fixture_review


def corpus(root, prose='Hearing January 3, 2026. {#b:12345678}\n'):
    matter = root / 'data/matters/m01-fixture'
    matter.mkdir(parents=True)
    (matter / 'matter.json').write_text(json.dumps({'id': 'm01', 'open_date': '2026-01-01'}))
    (matter / 'facts.md').write_text(prose)
    return matter


@pytest.mark.parametrize('write', [False, True])
def test_cli_real_conversion_proofs_and_reports(tmp_path, capsys, write):
    matter = corpus(tmp_path)
    original = (matter / 'facts.md').read_bytes()
    args = ['--repo', str(tmp_path), '--audit-output', str(tmp_path / 'audit.json'), '--holdouts-output', str(tmp_path / 'holdouts.json')]
    if write:
        args.append('--write')
    assert dz.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    audit = json.loads((tmp_path / 'audit.json').read_text())
    assert summary['mode'] == ('write' if write else 'dry-run')
    assert summary['converted_dates'] == summary['proof_records'] == summary['inventory_total'] == 2
    assert audit['summary']['attention_required'] == 0
    assert json.loads((tmp_path / 'holdouts.json').read_text())['entries'] == []
    assert (matter / 'facts.md').read_bytes() == original
    assert (matter / 'date-offsets.json').exists() is write
    if write:
        entries = json.loads((matter / 'date-offsets.json').read_text())['entries']
        assert entries[0]['block_id'] == 'b:12345678'
        assert entries[0]['day_zero_offset'] == 2


def test_cli_reports_invalid_prose_date_without_materializing_it(tmp_path, capsys):
    matter = corpus(tmp_path, 'Hearing 2026-02-30. {#b:12345678}\n')
    assert dz.main(['--repo', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    summary = json.loads(output.splitlines()[0])
    attention = json.loads('\n'.join(output.splitlines()[1:]))
    assert summary['unclassified'] == 1
    assert attention['unclassified_dates'][0]['literal'] == '2026-02-30'
    assert not (matter / 'date-offsets.json').exists()


def test_approved_review_reports_are_returned_after_validation(tmp_path):
    corpus(tmp_path, 'Statute effective January 3, 2026. {#b:12345678}\n')
    initial = dz.convert_corpus(tmp_path)
    _approve_fixture_review(tmp_path, initial)
    converted = dz.convert_corpus(tmp_path, write=True)
    holdouts, audit = dz.governed_reports(tmp_path, converted)
    assert holdouts['summary']['count'] == 0
    assert audit['summary']['converted_dates'] == 2
    assert dz._approved_conversion_target(tmp_path) == 2
    assert any(row.get('review_status') == 'human_confirmed_convertible' for row in audit['entries'])


def test_nearby_case_citation_holds_out_full_date(tmp_path):
    corpus(tmp_path, '410 U.S. 113 (1973), discussed January 3, 2026. {#b:12345678}\n')
    result = dz.convert_corpus(tmp_path)
    assert result.full_date_holdouts == 1
    full = next(row for row in result.holdouts if row['literal'] == 'January 3, 2026')
    assert full['reason'] == 'case-citation year is a fixed fact'
    assert not result.unclassified


def test_raw_inventory_skips_binary_files_and_generated_sidecars(tmp_path):
    directory = tmp_path / 'data/curriculum'
    directory.mkdir(parents=True)
    (directory / 'binary.dat').write_bytes(b'\xff2026-01-01')
    (directory / 'date-offsets.json').write_text('2026-01-01')
    (directory / 'notes.md').write_text('Date 2026-01-02, identifier 12026-01-023')
    rows, excluded = dz._raw_full_date_inventory(tmp_path)
    assert [row['literal'] for row in rows] == ['2026-01-02']
    assert len(excluded) == 1
    assert excluded[0]['literal'] == '2026-01-02'


def test_conflicting_existing_json_sibling_is_rejected():
    before = '{"date_day_zero_offset": 3}'
    with pytest.raises(RuntimeError, match='conflicting existing'):
        dz._validate_intended_json_additions(before, before, [('', 'date_day_zero_offset', 4)])


def test_record_invalid_calendar_date_has_no_conversion_proof():
    result = dz.Result()
    dz._record(result, 'facts.md', 'b:12345678:0', '2026-02-30', date(2026, 1, 1), 'anchor', 'prose_sidecar', 'dates.json', 0)
    assert result.converted_dates == 0
    assert result.audit == []
    assert result.unclassified[0]['literal'] == '2026-02-30'


def test_unresolved_approved_block_cannot_be_silently_materialized(tmp_path):
    row = {'source': 'facts.md', 'locator': 'b:12345678:0', 'literal': '2026-01-02', 'durable_locator': 'b:12345678:0', 'key': 'fixture'}
    with pytest.raises(RuntimeError, match='identity no longer resolves: fixture'):
        dz._materialize_approved_rows(tmp_path, [row], [], 'offsets.json', dz.Result(), date(2026, 1, 1), 'anchor')


@pytest.mark.parametrize('failure', ['fdopen', 'fchmod', 'fsync'])
def test_staging_failure_cleans_temporary_file(tmp_path, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise OSError('disk boundary unavailable')
    monkeypatch.setattr(dz.os, failure, fail)
    with pytest.raises(OSError, match='disk boundary unavailable'):
        dz._stage_payload(tmp_path / 'new.json', b'payload', 0o644)
    assert list(tmp_path.iterdir()) == []


def test_failure_staging_later_file_cleans_earlier_temporary(tmp_path, monkeypatch):
    first = tmp_path / 'first.json'
    second = tmp_path / 'second.json'
    first.write_bytes(b'original')
    real = dz.os.fsync
    calls = 0
    def fail_second(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('second staging failed')
        return real(fd)
    monkeypatch.setattr(dz.os, 'fsync', fail_second)
    with pytest.raises(OSError, match='second staging failed'):
        dz._write_staged_files({first: b'changed', second: b'new'})
    assert first.read_bytes() == b'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['first.json']


def test_failed_commit_removes_new_file_and_preserves_existing(tmp_path, monkeypatch):
    created = tmp_path / 'a-new.json'
    existing = tmp_path / 'z-existing.json'
    existing.write_bytes(b'original')
    real = dz.os.replace
    def fail_existing(source, target):
        if target == existing:
            raise OSError('replace denied')
        return real(source, target)
    monkeypatch.setattr(dz.os, 'replace', fail_existing)
    with pytest.raises(OSError, match='replace denied'):
        dz._write_staged_files({created: b'new', existing: b'changed'})
    assert not created.exists()
    assert existing.read_bytes() == b'original'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['z-existing.json']


def test_empty_corpus_is_valid_and_cli_reports_zero(tmp_path, capsys):
    assert dz.main(['--repo', str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['inventory_total'] == summary['converted_dates'] == summary['touched_files'] == 0


def test_approved_audit_tampering_fails_before_corpus_write(tmp_path):
    import apply_day_zero_review
    matter = corpus(tmp_path, 'Statute effective January 3, 2026. {#b:12345678}\n')
    _approve_fixture_review(tmp_path, dz.convert_corpus(tmp_path))
    path = tmp_path / apply_day_zero_review.AUDIT_REL
    audit = json.loads(path.read_text())
    audit['summary']['converted_dates'] += 1
    path.write_text(json.dumps(audit))
    original = {p.name: p.read_bytes() for p in matter.iterdir()}
    with pytest.raises(RuntimeError, match='approved Day Zero identity no longer resolves'):
        dz.convert_corpus(tmp_path, write=True)
    assert {p.name: p.read_bytes() for p in matter.iterdir()} == original


def test_reconciliation_detects_double_counted_proofs(tmp_path):
    corpus(tmp_path, '')
    result = dz.Result()
    for _ in range(2):
        dz._record(result, 'data/matters/m01-fixture/matter.json', 'open_date', '2026-01-01', date(2026, 1, 1), 'anchor', 'json_sibling', 'data/matters/m01-fixture/matter.json', 'open_date_day_zero_offset')
    with pytest.raises(RuntimeError, match='2 categorized != 1 inventoried'):
        dz._reconcile_full_date_inventory(tmp_path, result)
