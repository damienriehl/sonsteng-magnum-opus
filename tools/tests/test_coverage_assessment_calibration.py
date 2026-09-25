"""Calibration boundary and real CLI-to-aggregate evidence tests."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assessment_calibration as calibration


def ratings(constant=False):
    return {'ratings': [
        {'work_id': f'synthetic-{i}', 'rater_role': role,
         'scores': {heading: 4 if constant else i % 7 + 1 for heading in calibration.HEADING_IDS}}
        for i in range(40) for role in calibration.RATER_ROLES
    ]}


@pytest.mark.parametrize('constant, status, label', [(False, 0, 'PASS'), (True, 1, 'FAIL')])
def test_human_cli_reports_aggregate_only_and_distinguishes_undefined_agreement(tmp_path, capsys, constant, status, label):
    source = tmp_path / 'ratings.json'
    source.write_text(json.dumps(ratings(constant)))
    before = source.read_bytes()
    assert calibration.main([str(source), '--min-kappa', '0.5',
                             '--max-abs-signed-difference', '0.5', '--human']) == status
    output = capsys.readouterr()
    assert output.err == ''
    assert output.out.startswith(f'Calibration: {label}\nComplete de-identified works: 40\n')
    assert 'synthetic-' not in output.out
    assert len(output.out.splitlines()) == 9
    assert ('baseline kappa=None' in output.out) == constant
    assert source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize('content', [b'{bad', b'\xff', b'null', b'{"ratings":null}'])
def test_cli_rejects_invalid_input_without_emitting_partial_metrics(tmp_path, capsys, content):
    source = tmp_path / 'invalid.json'
    source.write_bytes(content)
    assert calibration.main([str(source), '--min-kappa', '0.5', '--max-abs-signed-difference', '0.5']) == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert 'calibration input rejected:' in output.err
    assert source.read_bytes() == content


def test_cli_missing_file_fails_without_creating_output(tmp_path, capsys):
    assert calibration.main([str(tmp_path / 'absent.json'), '--min-kappa', '0.5',
                             '--max-abs-signed-difference', '0.5']) == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert 'calibration input rejected:' in output.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('payload', [None, {}, {'content': {}}, {'content': {'dimensions': None}}])
def test_heading_loader_normalizes_unavailable_instrument_errors(tmp_path, monkeypatch, payload):
    path = tmp_path / 'instrument.json'
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(calibration, 'INSTRUMENT_PATH', path)
    with pytest.raises(calibration.CalibrationInputError, match='unavailable'):
        calibration._load_heading_ids()


@pytest.mark.parametrize('ids', [['a'] * 7, ['a'] * 6, ['a', 'b', 'c', 'd', 'e', 'f', '!bad'],
                              ['a', 'b', 'c', 'd', 'e', 'f', 1]])
def test_heading_loader_rejects_duplicate_missing_and_invalid_ids(tmp_path, monkeypatch, ids):
    path = tmp_path / 'instrument.json'
    path.write_text(json.dumps({'content': {'dimensions': [{'id': item} for item in ids]}}))
    monkeypatch.setattr(calibration, 'INSTRUMENT_PATH', path)
    with pytest.raises(calibration.CalibrationInputError, match='invalid'):
        calibration._load_heading_ids()


@pytest.mark.parametrize('field, value, message', [
    ('work_id', '', 'opaque bounded'), ('work_id', 'x' * 129, 'opaque bounded'),
    ('work_id', 1, 'opaque bounded'), ('rater_role', 'faculty-3', 'rater_role'),
])
def test_rating_identity_and_role_boundaries(field, value, message):
    payload = ratings()
    payload['ratings'][0][field] = value
    with pytest.raises(calibration.CalibrationInputError, match=message):
        calibration.validate_ratings(payload)


@pytest.mark.parametrize('left,right', [([], []), ([1], []), ([1], [1, 2])])
def test_kappa_rejects_unpaired_or_empty_samples(left, right):
    with pytest.raises(ValueError, match='paired non-empty'):
        calibration.quadratic_weighted_kappa(left, right)


def test_kappa_known_disagreement_and_negative_zero_rounding():
    assert calibration.quadratic_weighted_kappa([1, 7], [7, 1]) == -1.0
    assert calibration.quadratic_weighted_kappa([4, 4], [4, 4]) is None
    assert str(calibration._rounded(-0.00000001)) == '0.0'
