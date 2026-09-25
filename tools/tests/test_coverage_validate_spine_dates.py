"""Day Zero failures exercised through real converted on-disk spines."""
import json

import pytest

from test_validate_spine import _converted_prose_fixture, run_validator, vs
from test_coverage_validate_spine import MATTER, change, findings


@pytest.fixture
def converted(tmp_path):
    return _converted_prose_fixture(tmp_path)


def validate(data):
    return run_validator(data, matter='m99', strict=True, enforce_day_zero_offsets=True)


def edit_sidecar(sidecar, key, value):
    payload = json.loads(sidecar.read_text())
    payload['entries'][0][key] = value
    sidecar.write_text(json.dumps(payload))


def test_sidecar_anchor_mismatch_stops_resolution(converted):
    data, _, sidecar = converted
    payload = json.loads(sidecar.read_text())
    payload['anchor'] = '2026-01-01'
    sidecar.write_text(json.dumps(payload))
    report = validate(data)
    assert findings(report, 'F30', 'does not match matter open_date')
    assert not findings(report, 'F30', 'disagrees with day_zero_offset')


@pytest.mark.parametrize('source', ['missing.json', MATTER, MATTER+'unreadable.bin'])
def test_unreadable_sidecar_source_is_reported(converted, source):
    data, _, sidecar = converted
    if source.endswith('.bin'):
        (data/source).write_bytes(b'\xff')
    edit_sidecar(sidecar, 'source', source)
    report = validate(data)
    assert findings(report, 'F30', 'source is unreadable')
    assert findings(report, 'F30', 'has no date-offsets entry')


@pytest.mark.parametrize('key,value', [('literal', 'not a date'), ('day_zero_offset', True), ('day_zero_offset', 1.5), ('day_zero_offset', None)])
def test_degraded_schema_still_checks_sidecar_date_types(converted, monkeypatch, key, value):
    data, _, sidecar = converted
    edit_sidecar(sidecar, key, value)
    monkeypatch.setattr(vs, 'HAVE_JSONSCHEMA', False)
    report = validate(data)
    assert findings(report, 'F30', 'cannot resolve date-offsets entries.0')
    assert findings(report, 'F30', 'has no date-offsets entry')


@pytest.mark.parametrize('locator', [True, '0', 99])
def test_degraded_schema_rejects_noninteger_or_out_of_range_occurrence(converted, monkeypatch, locator):
    data, _, sidecar = converted
    edit_sidecar(sidecar, 'locator', locator)
    monkeypatch.setattr(vs, 'HAVE_JSONSCHEMA', False)
    assert findings(validate(data), 'F30', 'stale literal/block identity')


@pytest.mark.parametrize('contents', ['{invalid json', 'null', '{"note":"No hearing date here"}'])
def test_legacy_json_source_without_resolvable_block(converted, contents):
    data, _, sidecar = converted
    source = data / 'legacy-source.json'
    source.write_text(contents)
    edit_sidecar(sidecar, 'source', source.name)
    assert findings(validate(data), 'F30', 'stale literal/block identity')


@pytest.mark.parametrize('prefix', ['', 'data/', 'absolute', 'windows'])
def test_legacy_json_source_resolves_and_normalizes_paths(converted, prefix):
    data, facts, sidecar = converted
    target = facts.parent / 'exercise.json'
    payload = json.loads(target.read_text())
    payload['sections']['instructions']['body_md'] = 'Hearing January 13, 2026. {#b:deadbeef}'
    target.write_text(json.dumps(payload))
    facts.write_text(facts.read_text().replace('Hearing January 13, 2026. {#b:deadbeef}', ''))
    relative = str(target.relative_to(data))
    source = str(target) if prefix == 'absolute' else relative.replace('/', '\\') if prefix == 'windows' else prefix + relative
    edit_sidecar(sidecar, 'source', source)
    assert not findings(validate(data), 'F30')


@pytest.mark.parametrize('mutation', ['unchanged', 'text_changed', 'invalid_json', 'missing_pointer'])
def test_durable_json_locator_tracks_real_json_path(converted, mutation):
    data, facts, sidecar_path = converted
    target = data / MATTER / 'exercise.json'
    change(data, MATTER+'exercise.json', ['sections','instructions','body_md'], 'Hearing January 13, 2026.')
    facts.write_text(facts.read_text().replace('Hearing January 13, 2026. {#b:deadbeef}', ''))
    sidecar = json.loads(sidecar_path.read_text())
    entry = sidecar['entries'][0]
    entry.pop('block_id')
    entry['source'] = str(target.relative_to(data))
    entry['durable_locator'] = 'json:sections.instructions.body_md:date:0'
    sidecar_path.write_text(json.dumps(sidecar))
    if mutation == 'text_changed':
        change(data, MATTER+'exercise.json', ['sections','instructions','body_md'], 'Hearing January 14, 2026.')
    elif mutation == 'invalid_json':
        target.write_text('{bad')
    elif mutation == 'missing_pointer':
        payload = json.loads(target.read_text())
        del payload['sections']['instructions']
        target.write_text(json.dumps(payload))
    report = validate(data)
    if mutation == 'unchanged':
        assert not findings(report, 'F30')
    else:
        assert findings(report, 'F30', 'stale literal/block identity')


@pytest.mark.parametrize('offset,message', [(True, 'cannot resolve'), ('1', 'cannot resolve'), (10**12, 'outside the supported calendar range')])
def test_structured_offset_types_and_calendar_limits(converted, offset, message):
    data, _, _ = converted
    change(data, MATTER+'matter.json', ['as_of_date_day_zero_offset'], offset)
    assert findings(validate(data), 'F30', message)


def test_nested_reserved_offset_containers_are_walked(converted):
    data, _, _ = converted
    change(data, MATTER+'matter.json', ['custom_facts'], {
        'nested_day_zero_offset': [{'hearing': '2026-01-13', 'hearing_day_zero_offset': 1}],
        'empty': [],
    })
    report = validate(data)
    assert not findings(report, 'F30')
    assert report.offset_dates_checked > 0


@pytest.mark.parametrize('status,occurrences,missing', [
    ('declared_absolute_holdout', 1, 0),
    ('candidate_pending_human_review', 1, 1),
    ('declared_absolute_holdout', 2, 1),
])
def test_prose_holdout_status_and_occurrence_accounting(converted, status, occurrences, missing):
    data, facts, sidecar = converted
    payload = json.loads(sidecar.read_text())
    payload['entries'] = []
    sidecar.write_text(json.dumps(payload))
    if occurrences == 2:
        facts.write_text(facts.read_text()+'\nAnother hearing January 13, 2026.\n')
    (data/'day-zero-holdouts.json').write_text(json.dumps({
        'schema_version': '1.0.0', 'description': 'Fixed date test fixture',
        'method': 'Reviewed fact', 'summary': {'count': 1, 'by_reason': {'fixed fact': 1}},
        'entries': [{'source': 'data/'+str(facts.relative_to(data)), 'locator': 'b:deadbeef',
                     'literal': 'January 13, 2026', 'reason': 'fixed fact', 'review_status': status}],
    }))
    report = validate(data)
    matches = findings(report, 'F30', 'has no date-offsets entry')
    assert len(matches) == bool(missing)
    if missing:
        assert f'({missing} occurrence(s))' in matches[0].message
    else:
        assert not findings(report, 'F30')


def test_empty_prose_and_empty_sidecar_are_valid(converted):
    data, facts, sidecar = converted
    facts.write_text(facts.read_text().replace('Hearing January 13, 2026. {#b:deadbeef}', ''))
    payload = json.loads(sidecar.read_text())
    payload['entries'] = []
    sidecar.write_text(json.dumps(payload))
    assert not findings(validate(data), 'F30')
    sidecar.unlink()
    assert not findings(validate(data), 'F30')


def test_schema_invalid_sidecar_is_treated_as_missing(converted):
    data, _, sidecar = converted
    sidecar.write_text(json.dumps({'schema_version': '1.0.0'}))
    report = validate(data)
    assert findings(report, 'F29', 'date_offsets')
    assert findings(report, 'F30', 'missing date-offsets.json')


def test_date_count_and_resolution_cache_are_idempotent(converted):
    data, _, _ = converted
    world = vs.discover(data, 'm99')
    report = vs.Report()
    validator = vs.Validator(world, vs.SchemaSet(data/'schemas'), report, strict=True, online=False,
                             enforce_day_zero_offsets=True)
    validator.run()
    before = report.checked_dates, report.offset_dates_checked
    assert before[0] == before[1] > 0
    bundle = world.matters['m99']
    validator._check_day_zero_representation('m99', bundle)
    assert (report.checked_dates, report.offset_dates_checked) == before
    assert not findings(report, 'F30')
