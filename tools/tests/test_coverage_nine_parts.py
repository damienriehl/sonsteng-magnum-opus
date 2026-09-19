"""Auditor CLI and incomplete corpus behavior with real exercise packets."""
import json

import pytest

from test_audit_nine_parts import audit, _write_fixture, _edit_packet


@pytest.mark.parametrize('complete', [True, False])
def test_cli_reports_machine_json_and_human_status(tmp_path, capsys, complete):
    root = _write_fixture(tmp_path, complete=complete)
    assert audit.main(['--root', str(root)]) == (0 if complete else 1)
    result = capsys.readouterr()
    report = json.loads(result.out)
    assert report['summary']['conforming_matters'] == int(complete)
    assert audit.human_summary(report) in result.err


@pytest.mark.parametrize('sections', [None, [], 'invalid'])
def test_invalid_sections_are_reported_as_missing(tmp_path, sections):
    root = _write_fixture(tmp_path)
    _edit_packet(root, lambda packet: packet.update(sections=sections))
    report = audit.audit_repository(root)
    matter, = report['matters']
    assert not matter['conforms']
    assert any(issue['class'] == 'missing_section' for issue in matter['issues'])
    assert report['summary']['mechanical_gaps'] > 0


@pytest.mark.parametrize('adjacent', [[], ['rubric.json'], ['rubric.json', 'exercise/answer-key.md']])
def test_assessment_gap_retains_adjacent_evidence_without_calling_it_conforming(tmp_path, adjacent):
    root = _write_fixture(tmp_path)
    matter = root / 'data/matters/m01-example'
    (matter / 'exercise/assessment-feedback-form.md').unlink()
    for name in adjacent:
        (matter / name).write_text('Existing assessment evidence')
    report = audit.audit_repository(root)
    part = next(p for p in report['matters'][0]['parts'] if p['id'] == 'assessment_feedback_form')
    assert part['status'] == 'nonconforming'
    assert sorted(part['evidence']) == sorted('data/matters/m01-example/' + name for name in adjacent)


def test_empty_corpus_cli_has_zero_counts(tmp_path, capsys):
    assert audit.main(['--root', str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['matters'] == []
    assert all(value == 0 for value in report['summary'].values())
