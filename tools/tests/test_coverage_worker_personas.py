"""Isolated build integration for partial fleets and confidential-label fallbacks."""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_worker_personas as builder


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    paths = {
        'REPO_ROOT': tmp_path, 'DATA_DIR': tmp_path / 'data',
        'MATTERS_DIR': tmp_path / 'data/matters',
        'FIXTURE_PERSONA': tmp_path / 'fixtures/persona.json',
        'FIXTURE_SIDECAR': tmp_path / 'fixtures/labels.json',
        'ASSESSMENT_INSTRUMENT': tmp_path / 'data/instrument.json',
        'OUT_PATH': tmp_path / 'output/personas.json',
    }
    for name, path in paths.items():
        monkeypatch.setattr(builder, name, str(path))
    for name, label in [('SYSTEM_TEMPLATE', 'SEGMENT A (verbatim)'),
                        ('DEBRIEF_TEMPLATE', 'DEBRIEF PROMPT'),
                        ('CRITIQUE_TEMPLATE', 'CRITIQUE PROMPT'),
                        ('MEMO_SCORECARD_TEMPLATE', 'MEMO SCORECARD PROMPT')]:
        path = tmp_path / (name + '.md')
        end = 'SEGMENT A' if name == 'SYSTEM_TEMPLATE' else label
        path.write_text(f'<!-- ===== BEGIN {label} ===== -->\n{label} café\n<!-- ===== END {end} ===== -->')
        monkeypatch.setattr(builder, name, str(path))
    write(paths['ASSESSMENT_INSTRUMENT'], {'id': 'assessment-fixture'})
    return paths


def test_partial_fleet_build_filters_secrets_and_reports_missing_curations(fleet, capsys):
    write(fleet['FIXTURE_PERSONA'], {'id': 'm00.client', 'identity': {'name': 'Test'},
          'instructor_notes': 'NEVER BUNDLE', '@context': 'private',
          'disclosure': {'concealed': [{'fact_ref': 'm00.fact.001', 'text': 'Secret fact'},
                                       {'text': 'no reference'}]}})
    write(fleet['FIXTURE_SIDECAR'], {'m00.fact.001': '  SECRET   FACT  '})
    pdir = fleet['MATTERS_DIR'] / 'sample/personas'
    write(pdir / 'a.json', {'id': 'm00.client'})
    write(pdir / 'b.json', {'background': 'missing id'})
    (pdir / 'c.json').write_text('{bad')
    write(pdir / 'd.json', {'id': 'm01.client', 'disclosure': {}})
    write(pdir / 'topic-labels.json', {'m01.fact.001': 'A safe topic'})
    (pdir / 'notes.txt').write_text('not a persona')
    write(pdir.parent / 'rubric.json', {'id': 'm01.rubric', 'criteria': ['criterion']})
    write(fleet['MATTERS_DIR'] / 'empty/rubric.json', {})
    bad = fleet['MATTERS_DIR'] / 'bad/rubric.json'
    bad.parent.mkdir()
    bad.write_text('{')
    assert builder.main() == 0
    raw = fleet['OUT_PATH'].read_text()
    bundle = json.loads(raw)
    assert set(bundle['personas']) == {'m00.client', 'm01.client'}
    assert 'NEVER BUNDLE' not in raw and '@context' not in raw
    assert bundle['fact_map']['m00.client']['m00.fact.001'] == {
        'topic_label': builder.NEUTRAL_TIER_LABEL['concealed'], 'tier': 'concealed'}
    assert bundle['rubrics']['m01']['criteria'] == ['criterion']
    assert bundle['spine_build_id'] == builder.spine_stamp.compute(str(fleet['DATA_DIR']))
    assert bundle['assessment_instrument'] == {'id': 'assessment-fixture'}
    output = capsys.readouterr().out
    assert 'duplicate id m00.client' in output and 'no id field' in output
    assert 'skipped    : 3' in output and 'WARNING: 1 fact(s)' in output
    assert builder.main() == 0
    assert fleet['OUT_PATH'].read_text() == raw


def test_missing_fixture_and_missing_matter_tree_build_empty_bundle(fleet, capsys):
    assert builder.collect_rubrics() == {}
    assert builder.main() == 0
    bundle = json.loads(fleet['OUT_PATH'].read_text())
    assert bundle['personas'] == bundle['fact_map'] == bundle['rubrics'] == {}
    assert '(missing)' in capsys.readouterr().out


@pytest.mark.parametrize('value', [[], None, 'label', 42])
def test_sidecar_non_objects_are_ignored(tmp_path, value):
    path = tmp_path / 'labels.json'
    write(path, value)
    assert builder.load_sidecar(str(path)) == {}


def test_sidecar_filters_bad_keys_empty_and_nonstring_values(tmp_path):
    path = tmp_path / 'labels.json'
    write(path, {'_comment': 'ignore', 'm01.fact.001': '  useful topic  ',
                 'm01.fact.002': ' ', 'm01.fact.003': 9, 'm01.fact.004x': 'bad'})
    assert builder.load_sidecar(str(path)) == {'m01.fact.001': 'useful topic'}
    path.write_text('{')
    assert builder.load_sidecar(str(path)) == {}
    assert builder.load_sidecar(None) == {}


@pytest.mark.parametrize('tier', builder.TIERS + ['future'])
def test_fallback_never_contains_fact_content(tier):
    label, fallback = builder.derive_topic_label('missing', tier, {}, 'confidential value')
    assert fallback and 'confidential value' not in label
    assert label == builder.NEUTRAL_TIER_LABEL.get(tier, 'an unexplored topic')


def test_template_last_markers_preserve_one_extra_newline(tmp_path):
    path = tmp_path / 'prompt.md'
    path.write_text('BEGINignoredEND\nBEGIN\n\nactual\n\nEND')
    assert builder.read_template(path, 'BEGIN', 'END') == '\nactual\n'
    with pytest.raises(ValueError):
        builder.extract_between('no markers', 'BEGIN', 'END')


@pytest.mark.parametrize('invalid_asset', ['SYSTEM_TEMPLATE', 'ASSESSMENT_INSTRUMENT'])
def test_invalid_required_assets_leave_existing_bundle_intact(fleet, invalid_asset):
    fleet['OUT_PATH'].parent.mkdir(parents=True)
    fleet['OUT_PATH'].write_text('previous successful bundle')
    pathlib.Path(getattr(builder, invalid_asset)).write_text('invalid required input')
    with pytest.raises(ValueError):
        builder.main()
    assert fleet['OUT_PATH'].read_text() == 'previous successful bundle'


def test_safe_curated_label_and_content_free_missing_reference_coexist(fleet):
    write(fleet['FIXTURE_PERSONA'], {'id': 'm00.client', 'disclosure': {
        'volunteered': [{'fact_ref': 'm00.fact.001', 'text': 'Specific confidential value'}],
        'unknown': [{'fact_ref': 'm00.fact.002', 'text': 'Other confidential value'}]}})
    write(fleet['FIXTURE_SIDECAR'], {'m00.fact.001': 'Timing of the meeting'})
    assert builder.main() == 0
    bundle = json.loads(fleet['OUT_PATH'].read_text())
    assert bundle['fact_map']['m00.client'] == {
        'm00.fact.001': {'topic_label': 'Timing of the meeting', 'tier': 'volunteered'},
        'm00.fact.002': {'topic_label': builder.NEUTRAL_TIER_LABEL['unknown'], 'tier': 'unknown'}}
    assert 'confidential value' not in json.dumps(bundle['fact_map'])
