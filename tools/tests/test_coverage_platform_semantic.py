"""Real temporary-page capture and legacy/full snapshot comparison contracts."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform_semantic_contract as contract


@pytest.fixture
def site(tmp_path):
    root = tmp_path / 'site'
    root.mkdir()
    (root / 'index.html').write_text('<h1>Heading &amp; label</h1><p>Visible text.</p><a href="next.html">Continue</a>')
    return root


def test_capture_compare_cli_chain_detects_real_content_drift(site, tmp_path, capsys):
    mapping = {'pages': {}}
    snapshot = contract.capture_site(site, mapping)
    assert snapshot['pages']['index.html']['headings'] == [{'level': 1, 'text': 'Heading & label'}]
    assert snapshot['pages']['index.html']['links'] == [{'text': 'Continue', 'href': 'next.html'}]
    baseline = tmp_path / 'baseline.json'
    map_path = tmp_path / 'map.json'
    baseline.write_text(json.dumps(snapshot))
    map_path.write_text(json.dumps(mapping))
    assert contract.main([str(baseline), str(site), str(map_path)]) is False
    assert capsys.readouterr().out == ''
    (site / 'index.html').write_text('<h1>Changed heading</h1>')
    assert contract.main([str(baseline), str(site), str(map_path)]) is True
    output = capsys.readouterr().out
    assert 'SEMANTIC DRIFT: index.html: headings changed' in output
    assert 'links changed' in output


def test_capture_reports_placement_and_reference_integrity_errors(site):
    mapping = {'pages': {'index.html': [
        {'index': 'not-an-integer'}, {'index': 1, 'source_ref': 'missing-fixture.json#p0'},
        {'index': 1, 'source_ref': 'missing-fixture.json#p0'},
    ]}}
    snapshot = contract.capture_site(site, mapping)
    errors = contract.validate_snapshot(snapshot)
    assert any('without integer placement index' in item for item in errors)
    assert any('duplicate editor placement index' in item for item in errors)
    assert sum('unresolvable editor source_ref' in item for item in errors) == 3
    assert any('without source_ref' in item for item in errors)
    assert any('duplicate durable source_ref' in item for item in errors)


def test_legacy_snapshot_reports_missing_and_unexpected_pages(site):
    original = contract.capture_site(site, {})
    changed = copy.deepcopy(original)
    changed['pages']['new.html'] = changed['pages'].pop('index.html')
    assert contract.compare_snapshots(original, changed) == [
        'index.html: page missing', 'new.html: unexpected page']


@pytest.mark.parametrize('field', ['text', 'headings', 'links', 'editor_blocks', 'reading_order'])
def test_legacy_per_field_digest_accepts_exact_data_and_rejects_changed_digest(site, field):
    actual = contract.capture_site(site, {})
    expected = copy.deepcopy(actual)
    value = actual['pages']['index.html'][field]
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    expected['pages']['index.html'][field] = {'count': len(value), 'sha256': hashlib.sha256(serialized.encode()).hexdigest()}
    assert contract.compare_snapshots(expected, actual) == []
    expected['pages']['index.html'][field]['sha256'] = 'different'
    errors = contract.compare_snapshots(expected, actual)
    assert len(errors) == 1
    assert errors[0].startswith(f'index.html: {field} changed')


def test_frozen_snapshot_reports_page_identity_changes_independently_of_field_digests(site):
    actual = contract.capture_site(site, {})
    frozen = contract.freeze_snapshot(actual)
    frozen['page_ids'] = ['renamed.html']
    assert contract.compare_snapshots(frozen, actual) == [
        "page identity changed: ['renamed.html'] -> ['index.html']"]


def test_empty_site_is_stable_and_static_assets_are_excluded(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    for directory in ['assets', 'chat']:
        (root / directory).mkdir()
        (root / directory / 'index.html').write_text('<h1>Excluded</h1>')
    (root / 'data.json').write_text('{}')
    actual = contract.capture_site(root, {})
    assert actual['pages'] == {}
    assert contract.compare_snapshots({}, actual) == []
    assert contract.compare_snapshots(contract.freeze_snapshot(actual), actual) == []
