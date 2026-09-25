"""UAT record error, empty, and rendering paths through isolated run files."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_persona_uat_record as renderer
from test_render_persona_uat_record import attempt, run_file, write_json


def arguments(tmp_path):
    return ['--runs', str(tmp_path / 'runs'), '--journeys', str(tmp_path / 'journeys.json'),
            '--stories', str(tmp_path / 'stories.md'), '--out', str(tmp_path / 'output/record.md')]


def test_cli_loads_real_runs_renders_escaping_and_preserves_history(tmp_path, capsys):
    row = attempt(verdict='ERROR', shot_path='shots/a|b.png', review_count=2)
    row['persona'] = 'A|1'
    row['first_failure'] = 'first\nsecond|third'
    write_json(tmp_path / 'runs/one.json', run_file('run-1', '2026-01-01', [None, row]))
    write_json(tmp_path / 'journeys.json', {'journeys': [{'story': 'US-1-01'}]})
    (tmp_path / 'stories.md').write_text('## US-1-01 Story\n')
    assert renderer.main(arguments(tmp_path)) == 0
    assert 'Rendered 1 run' in capsys.readouterr().out
    output = (tmp_path / 'output/record.md').read_text()
    assert 'A\\|1' in output and 'first second\\|third' in output
    assert 'shots/a\\|b.png' in output and '2 — HUMAN READ REQUIRED' in output
    assert '- Attempts: 1' in output


def test_empty_runs_and_missing_stories_render_explicit_empty_state(tmp_path, capsys):
    write_json(tmp_path / 'journeys.json', [])
    assert renderer.main(arguments(tmp_path)) == 0
    assert 'coverage check skipped' in capsys.readouterr().out
    output = (tmp_path / 'output/record.md').read_text()
    assert 'No run files rendered' in output and 'No attempts recorded' in output
    assert '| — | 0 | 0 | 0 | 0 | 0 | 0 | 0 |' in output


@pytest.mark.parametrize('value', [[], {}, {'attempts': {}}, {'attempts': None}])
def test_invalid_run_envelopes_are_diagnosed_without_output(tmp_path, capsys, value):
    write_json(tmp_path / 'runs/one.json', value)
    write_json(tmp_path / 'journeys.json', [])
    assert renderer.main(arguments(tmp_path)) == 2
    assert 'expected an object with an attempts list' in capsys.readouterr().err
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('failure', ['run-json', 'journey-json', 'verdict', 'review-count', 'journey-shape', 'missing-journeys'])
def test_cli_read_and_validation_errors(tmp_path, capsys, failure):
    write_json(tmp_path / 'journeys.json', [])
    if failure == 'run-json':
        (tmp_path / 'runs').mkdir()
        (tmp_path / 'runs/one.json').write_text('{invalid')
    elif failure == 'journey-json':
        (tmp_path / 'journeys.json').write_text('{invalid')
    elif failure == 'journey-shape':
        write_json(tmp_path / 'journeys.json', {'journeys': {}})
    elif failure == 'missing-journeys':
        (tmp_path / 'journeys.json').unlink()
    else:
        row = attempt(verdict='unknown' if failure == 'verdict' else 'PASS')
        if failure == 'review-count':
            row['review_count'] = 'nonnumeric'
        write_json(tmp_path / 'runs/one.json', run_file('run', '2026', [row]))
    assert renderer.main(arguments(tmp_path)) == 2
    assert 'UAT record render error' in capsys.readouterr().err
    assert not (tmp_path / 'output').exists()


def test_catalog_filters_nonobjects_and_deduplicates_unrun_stories(tmp_path):
    catalog = [{'story': 'US-1-01', 'id': 'one', 'binding': 'command', 'command': {'command': 'local check'}},
               None, {'story': 'US-1-01', 'id': 'duplicate'}, {'story': ''}]
    write_json(tmp_path / 'catalog.json', catalog)
    journeys = renderer.load_journeys(tmp_path / 'catalog.json')
    assert len(journeys) == 3
    placeholders = renderer.unrun_attempts([], journeys)
    assert len(placeholders) == 1
    assert placeholders[0]['artifact'] == 'local check' and placeholders[0]['viewport'] == 'n/a'


@pytest.mark.parametrize('record,expected', [({}, '—'), ({'digest': 'abc'}, '`abc`'), ({'artifact': 'a|b'}, 'a\\|b')])
def test_artifact_variants_have_unambiguous_cells(record, expected):
    assert renderer.artifact_cell(record) == expected
