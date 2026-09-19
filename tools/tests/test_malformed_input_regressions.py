"""Malformed external data produces diagnostics through real validation chains."""
import json

import pytest

from test_validate_spine import build_base_spine, run_validator, vs
from test_coverage_validate_spine import MATTER, PERSONA, change, findings
from test_coverage_validate_spine_dates import converted, edit_sidecar, validate
from test_coverage_day_zero_equivalence import eq, sidecar, snapshots
from test_coverage_editor_scoped_drafts import sd, configure, request


@pytest.mark.parametrize('field,value', [('disclosure', 'invalid'), ('identity', None)])
def test_invalid_persona_schema_reports_without_depth_crash(tmp_path, field, value):
    build_base_spine(tmp_path)
    change(tmp_path, PERSONA, [field], value)
    report = run_validator(tmp_path)
    assert report.has_errors()
    assert any('schema' in f.message.lower() for f in report.findings)


def test_unreadable_prose_reports_validation_error(converted):
    data, facts, _ = converted
    facts.write_bytes(b'\xff')
    report = validate(data)
    assert report.has_errors()
    assert findings(report, 'DEPTH', 'facts.md missing')
    assert findings(report, 'F30', 'Cannot read prose date source')


@pytest.mark.parametrize('offset', [10**12, -10**12, 4000000])
def test_sidecar_offset_out_of_date_range_is_diagnostic(converted, offset):
    data, _, sidecar_path = converted
    edit_sidecar(sidecar_path, 'day_zero_offset', offset)
    report = validate(data)
    assert findings(report, 'F30', 'cannot resolve date-offsets entries.0')


@pytest.mark.parametrize('stored', [None, [], 'bad', 7])
def test_nonobject_emitted_sidecar_entry_is_equivalence_error(stored):
    document, proof = sidecar()
    document['entries'][0] = stored
    with pytest.raises(eq.EquivalenceError, match='cannot read emitted proof record'):
        eq.file_round_trip(*snapshots(document, proof))


@pytest.mark.parametrize('bad', [None, [], 'bad', 7, {'source_ref': [], 'new_text': 'bad'}])
def test_malformed_draft_does_not_strand_claim_or_discard_valid_drafts(monkeypatch, tmp_path, bad):
    row = request()
    rpc = configure(monkeypatch, tmp_path, [row])
    def invoke(prompt):
        blocks = json.loads(prompt.split('BLOCKS_JSON:', 1)[1])['blocks']
        return json.dumps({'drafts': [bad] + [
            {'source_ref': b['source_ref'], 'new_text': b['original_text'] + ' Revised.'}
            for b in blocks]})
    monkeypatch.setattr(sd, 'run_cli', invoke)
    assert sd.main([]) == 0
    assert row['status'] == 'drafted'
    assert rpc.proposals


@pytest.mark.parametrize('snapshot,expected', [(['Rtest123'], ''), (['Rother'], 'not found'), ({'iris': [None, {}]}, 'unreadable'), (42, 'unreadable')])
def test_local_folio_crosswalk_shapes(tmp_path, snapshot, expected):
    build_base_spine(tmp_path)
    path = tmp_path / 'skills/SK-LP-01.json'
    obj = json.loads(path.read_text())
    del obj['no_folio_equivalent']
    obj['folio'] = {'iri': 'https://folio.openlegalstandard.org/Rtest123', 'mapping_confidence': 'exact'}
    path.write_text(json.dumps(obj))
    (tmp_path / 'taxonomy/folio-crosswalk.json').write_text(json.dumps(snapshot))
    report = vs.Report()
    vs.Validator(vs.discover(tmp_path, None), vs.SchemaSet(tmp_path / 'schemas'), report,
                 strict=True, online=True).run()
    assert not report.has_errors()
    matches = findings(report, 'E23')
    assert (len(matches) == 1 and expected in matches[0].message) if expected else not matches


@pytest.mark.parametrize('drafts', [42, 'bad', {'source_ref': 'bad'}])
def test_nonlist_draft_response_resolves_claim_as_failed(monkeypatch, tmp_path, drafts):
    row = request()
    configure(monkeypatch, tmp_path, [row])
    monkeypatch.setattr(sd, 'run_cli', lambda _: json.dumps({'drafts': drafts}))
    assert sd.main([]) == 0
    assert row['status'] == 'failed'
