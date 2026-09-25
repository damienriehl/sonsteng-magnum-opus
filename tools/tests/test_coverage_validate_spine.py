"""Disk-backed integrity regressions through discovery, schemas, and validation."""
import json
import shutil

import pytest

from test_validate_spine import build_base_spine, run_validator, vs

MATTER = 'matters/m99-noncompete-meridian/'
PERSONA = MATTER + 'personas/m99.per.okwuosa.json'


@pytest.fixture
def spine(tmp_path):
    build_base_spine(tmp_path)
    return tmp_path


def change(root, filename, path, value):
    file = root / filename
    obj = json.loads(file.read_text())
    target = obj
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    file.write_text(json.dumps(obj))


def findings(report, check, text=''):
    return [f for f in report.findings if f.check == check and text in f.message]


def test_complete_disk_spine_has_no_errors_or_broken_relationships(spine):
    report = run_validator(spine)
    assert not report.has_errors(), [f.message for f in report.findings]
    assert report.matters_seen == {'m99'}
    assert report.checked_dates > 0
    for check in ('A1', 'A2', 'A3', 'A4', 'C13', 'D19', 'D20'):
        assert not findings(report, check)


@pytest.mark.parametrize('strict,severity', [(False, vs.WARN), (True, vs.ERROR)])
@pytest.mark.parametrize('filename,path,value,check,message', [
    (MATTER+'matter.json', ['skill_refs'], ['SK-LP-99'], 'A2', 'skill_ref SK-LP-99'),
    (MATTER+'matter.json', ['task_refs'], ['TSK-999'], 'A2', 'task_ref TSK-999'),
    (MATTER+'matter.json', ['client_id'], 'FIRM-C-98', 'A2', 'client_id FIRM-C-98'),
    ('tasks/TSK-001.json', ['skill_id'], 'SK-LP-99', 'E27', 'unknown skill_id'),
    (MATTER+'rubric.json', ['criteria',0,'skill_id'], 'SK-LP-99', 'D20', 'skill_id'),
    (MATTER+'rubric.json', ['criteria',0,'task_id'], 'TSK-999', 'D20', 'task_id'),
    (PERSONA, ['disclosure','volunteered',0,'fact_ref'], 'm99.fact.999', 'C13', 'volunteered'),
    (PERSONA, ['disclosure','revealed_if_asked',0,'fact_ref'], 'm99.fact.999', 'C13', 'revealed_if_asked'),
    (PERSONA, ['disclosure','unknown',0,'fact_ref'], 'm99.fact.999', 'C13', 'unknown'),
])
def test_unresolved_reference_severity_tracks_ship_gate(spine, strict, severity, filename, path, value, check, message):
    change(spine, filename, path, value)
    report = run_validator(spine, strict=strict)
    matches = findings(report, check, message)
    assert len(matches) == 1
    assert matches[0].severity == severity
    assert not findings(report, 'F29')


@pytest.mark.parametrize('tier', ['rapport_gated', 'concealed'])
@pytest.mark.parametrize('strict', [False, True])
def test_material_disclosure_dangling_fact_always_blocks(spine, tier, strict):
    change(spine, PERSONA, ['disclosure',tier,0,'fact_ref'], 'm99.fact.999')
    matches = findings(run_validator(spine, strict=strict), 'C13', tier)
    assert len(matches) == 1 and matches[0].severity == vs.ERROR


@pytest.mark.parametrize('filename,path,value,check,message', [
    (MATTER+'matter.json', ['personas'], ['m99.per.absent','m99.per.salvato'], 'A4', 'lists persona m99.per.absent'),
    (MATTER+'matter.json', ['personas'], ['m99.per.salvato'], 'A4', 'not listed'),
    (MATTER+'matter.json', ['open_date'], '2026-07-01', 'B12', 'after as_of_date'),
    (MATTER+'matter.json', ['slug'], 'm99-different-meridian', 'A5', 'slug='),
    (PERSONA, ['interviewable_by'], ['m99.role.absent'], 'C16', 'not a side'),
    (PERSONA, ['knowledge_boundary','color_topics'], ['I kept a spreadsheet of pricing notes.'], 'C14', 'overlaps'),
    (PERSONA, ['background'], 'I read 42 U.S.C. section 1983.', 'C18', 'citation'),
    (MATTER+'rubric.json', ['declared_total'], 188, 'D19', 'master-outline pin'),
    (MATTER+'rubric.json', ['criteria',0,'weight_points'], 99, 'D19', 'subcriteria'),
    (MATTER+'rubric.json', ['letter_grade_map',1,'points'], 190, 'D21', 'not monotonic'),
    ('firm/firm.json', ['book_of_business'], [], 'B11', 'no entry for m99'),
    ('firm/firm.json', ['book_of_business',0,'client_id'], 'FIRM-C-98', 'B11', 'client_id firm='),
    ('firm/firm.json', ['book_of_business',0,'fee_type'], 'flat', 'B11', 'fee_type firm='),
    ('firm/firm.json', ['ar_aging','current'], 100, 'B11', 'matter invoice balances'),
    ('skills/SK-LP-01.json', ['name'], 'An unrecognized skill', 'E24', 'not an exact survey phrasing'),
    ('skills/SK-LP-01.json', ['extension'], True, 'E25', 'non-extension skill count'),
    (MATTER+'matter.json', ['schema_version'], '9.0.0', 'F29', '!= manifest'),
])
def test_integrity_mutations_are_reported_from_real_files(spine, filename, path, value, check, message):
    change(spine, filename, path, value)
    report = run_validator(spine)
    assert findings(report, check, message), [f.message for f in report.findings]


@pytest.mark.parametrize('filename,check,message', [
    ('spine-manifest.json','F29','missing'),
    ('matters/manifest.json','A5','not present'),
    ('firm/firm.json','FIRM','not present'),
    (MATTER+'exercise.json','A3','exercise'),
    (MATTER+'rubric.json','A3','rubric'),
    (MATTER+'personas','A3','persona'),
    (MATTER+'matter.json','DEPTH','no matter.json'),
    (MATTER+'facts.md','DEPTH','facts.md missing'),
    (MATTER+'business.json','DEPTH','business layer absent'),
])
def test_missing_layers_do_not_crash_other_checks(spine, filename, check, message):
    target = spine / filename
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    report = run_validator(spine)
    assert findings(report, check, message)
    assert report.matters_seen == {'m99'}


@pytest.mark.parametrize('filename', [
    'spine-manifest.json', 'matters/manifest.json', 'firm/firm.json',
    MATTER+'matter.json', MATTER+'personas/m99.per.okwuosa.json',
    'curriculum/assessment-instrument.json', 'day-zero-anchor-audit.json',
])
def test_malformed_json_is_a_load_error_and_surviving_data_is_checked(spine, filename):
    (spine / filename).write_text('{broken')
    report = run_validator(spine)
    assert findings(report, 'LOAD', filename.split('/')[-1])
    assert report.has_errors()
    assert report.checked_dates > 0


def test_empty_spine_reports_missing_contracts_and_partial_modules(tmp_path, capsys):
    world = vs.discover(tmp_path, None)
    report = vs.Report()
    vs.Validator(world, vs.SchemaSet(tmp_path/'schemas'), report, strict=False, online=False).run()
    assert findings(report, 'F29', 'missing')
    assert findings(report, 'FIRM', 'not present')
    assert findings(report, 'E24', 'partial-spine')
    vs.emit_human(world, report, False)
    output = capsys.readouterr().out
    assert '(no matter directories' in output
    assert 'RESULT: FAIL' in output


def test_cli_writes_machine_report_and_returns_success_then_failure(spine, tmp_path, capsys):
    output = tmp_path/'report.json'
    assert vs.main(['--data-dir',str(spine),'--strict','--json',str(output)]) == 0
    assert 'RESULT: PASS' in capsys.readouterr().out
    change(spine, PERSONA, ['disclosure','concealed',0,'fact_ref'], 'm99.fact.999')
    assert vs.main(['--data-dir',str(spine),'--strict','--json',str(output)]) == 1
    assert 'RESULT: FAIL' in capsys.readouterr().out
    payload = json.loads(output.read_text())
    assert payload['matters']['m99']['status'] == 'FAIL'
    assert any(f['check']=='C13' for f in payload['matters']['m99']['findings'])


@pytest.mark.parametrize('args,message', [(['--data-dir','ABSENT'], 'data dir not found'), (['--matter','bad'], '--matter must look')])
def test_cli_invalid_inputs_return_usage_error(spine, args, message, capsys):
    actual = ['--data-dir',str(spine / 'absent')] if args[0]=='--data-dir' else ['--data-dir',str(spine),*args]
    assert vs.main(actual) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize('path,value,check,message', [
    (['time_entries',0,'hours'], 2.05, 'B8h', '0.1 increment'),
    (['time_entries',0,'rate'], 999, 'B7', 'matches neither'),
    (['invoices',0,'balance_due'], 999, 'B9', 'fees+expenses-payments'),
    (['invoices',0,'fees'], 999, 'B8', 'line_refs'),
    (['invoices',0,'line_refs'], ['m99.te.9999'], 'B8', 'line_refs'),
    (['invoices',0,'date'], '2026-01-15', 'B12', 'precedes latest'),
    (['time_entries',0,'date'], '2026-07-01', 'B12', 'after as_of_date'),
])
def test_financial_invariants_reconcile_real_business_records(spine, path, value, check, message):
    change(spine, MATTER+'business.json', path, value)
    report = run_validator(spine)
    assert not findings(report, 'F29')
    assert findings(report, check, message)


@pytest.mark.parametrize('delta,errors', [(0,False), (0.01,False), (0.02,True)])
def test_invoice_arithmetic_cent_tolerance(spine, delta, errors):
    change(spine, MATTER+'business.json', ['invoices',0,'balance_due'], 1050+delta)
    assert bool(findings(run_validator(spine), 'B9')) is errors


def ledger(amount=1050, invoice='m99.inv.001'):
    # Intentionally reverse chronological file order: the gate must sort dates.
    return [
        {'id':'m99.tr.002','date':'2026-02-02','type':'disbursement','amount':amount,
         'related_invoice_id':invoice,'running_balance':2000-amount},
        {'id':'m99.tr.001','date':'2026-01-12','type':'deposit','amount':2000,'running_balance':2000},
    ]


def test_trust_reconciles_sorted_ledger_to_real_invoice(spine):
    change(spine, MATTER+'business.json', ['trust_entries'], ledger())
    report = run_validator(spine)
    assert not report.has_errors(), [f.message for f in report.findings]


@pytest.mark.parametrize('strict,severity', [(False,vs.WARN),(True,vs.ERROR)])
def test_trust_unissued_invoice_severity(spine, strict, severity):
    change(spine, MATTER+'business.json', ['trust_entries'], ledger(invoice='m99.inv.999'))
    matches = findings(run_validator(spine, strict=strict), 'B10', 'unknown invoice')
    assert len(matches)==1 and matches[0].severity == severity


def test_trust_cannot_disburse_unearned_funds(spine):
    change(spine, MATTER+'business.json', ['trust_entries'], ledger(amount=1050.02))
    report = run_validator(spine)
    assert not findings(report, 'F29')
    assert findings(report, 'B10', 'exceeds invoice')


@pytest.mark.parametrize('field,value,message', [('date','2026-01-01','goes negative'),('running_balance',1000,'recomputed')])
def test_trust_chronology_and_balance_failures(spine, field, value, message):
    entries = ledger()
    entries[0][field] = value
    change(spine, MATTER+'business.json', ['trust_entries'], entries)
    assert findings(run_validator(spine), 'B10', message)


@pytest.mark.parametrize('fee,amount,match', [('retainer',2000,True),('retainer',1500,False),('flat',1050,True),('flat',1500,False)])
def test_fee_structures_require_actual_deposit_or_invoice(spine, fee, amount, match):
    change(spine, MATTER+'business.json', ['engagement'], {'fee_type':fee, 'engagement_date':'2026-01-12', 'letter_md':'business/engagement-letter.md', 'retainer_amount' if fee=='retainer' else 'flat_fee':amount})
    change(spine, MATTER+'business.json', ['trust_entries'], ledger())
    change(spine, MATTER+'matter.json', ['fee_type'], fee)
    change(spine, 'matters/manifest.json', ['matters',0,'fee_type'], fee)
    change(spine, 'firm/firm.json', ['book_of_business',0,'fee_type'], fee)
    report = run_validator(spine)
    assert not findings(report, 'F29')
    failures = findings(report, 'B8', 'matching trust deposit' if fee=='retainer' else 'fixed fee line')
    assert bool(failures) is (not match)
    if failures:
        assert failures[0].severity == (vs.ERROR if fee=='retainer' else vs.WARN)


@pytest.mark.parametrize('filename,path,value,message', [
    (MATTER+'matter.json',['witnesses'],[],'witnesses <'),
    (MATTER+'matter.json',['exhibits'],[],'exhibits <'),
    (PERSONA,['identity','role'],'observer',"no persona has role 'client'"),
    (PERSONA,['disclosure','rapport_gated'],[],'rapport-gated facts'),
    (MATTER+'exercise.json',['sections','intro','body_md'],'Too short.','section \'intro\' only'),
    (MATTER+'business.json',['time_entries'],[],'no time entries'),
    (MATTER+'business.json',['invoices'],[],'no invoices'),
    (MATTER+'business.json',['engagement'],{'fee_type':'hourly'},'missing rate'),
    (MATTER+'business.json',['engagement'],{'fee_type':'flat'},'missing flat_fee'),
    (MATTER+'business.json',['engagement'],{'fee_type':'contingency'},'missing contingency_pct'),
    (MATTER+'business.json',['engagement'],{'fee_type':'retainer'},'missing retainer_amount'),
])
def test_content_depth_does_not_accept_empty_required_work(spine, filename, path, value, message):
    change(spine, filename, path, value)
    assert findings(run_validator(spine), 'DEPTH', message)


@pytest.mark.parametrize('text,message', [('short','words outside'), ('word '*1300,'has no [mNN.fact.NNN] anchors')])
def test_facts_depth_and_anchor_contract(spine, text, message):
    (spine/MATTER/'facts.md').write_text(text)
    assert findings(run_validator(spine), 'DEPTH', message)


@pytest.mark.parametrize('snapshot,expected', [
    (None,'snapshot absent'), ('{bad','unreadable'), ('{}','unreadable'),
    ('{"iris":["Rother"]}','not found'),
    ('{"iris":["Rtest123"]}',None),
    ('{"iris":["https://folio.openlegalstandard.org/Rtest123"]}',None),
])
def test_folio_online_mode_uses_only_local_snapshot(spine, snapshot, expected):
    file = spine/'skills/SK-LP-01.json'
    obj = json.loads(file.read_text())
    del obj['no_folio_equivalent']
    obj['folio']={'iri':'https://folio.openlegalstandard.org/Rtest123','mapping_confidence':'exact'}
    file.write_text(json.dumps(obj))
    if snapshot is not None:
        (spine/'taxonomy/folio-crosswalk.json').write_text(snapshot)
    world = vs.discover(spine, None)
    report = vs.Report()
    vs.Validator(world, vs.SchemaSet(spine/'schemas'),report,strict=True,online=True).run()
    assert not report.has_errors(), [f.message for f in report.findings]
    matches = findings(report,'E23')
    if expected:
        assert len(matches)==1 and expected in matches[0].message
    else:
        assert not matches


def add_second_matter(spine, mid='m98'):
    original = spine / MATTER
    copied = spine / 'matters' / f'{mid}-noncompete-meridian'
    shutil.copytree(original, copied)
    for file in copied.rglob('*'):
        if file.is_file():
            file.write_text(file.read_text().replace('m99', mid))
    manifest = spine/'matters/manifest.json'
    obj = json.loads(manifest.read_text())
    obj['matters'].append(json.loads(json.dumps(obj['matters'][0]).replace('m99',mid)))
    manifest.write_text(json.dumps(obj))
    return copied


def test_broken_matter_does_not_block_second_matter_and_filter_isolates(spine):
    add_second_matter(spine)
    change(spine, PERSONA, ['disclosure','concealed',0,'fact_ref'], 'm99.fact.999')
    report = run_validator(spine)
    assert report.matters_seen == {'m98','m99'}
    assert report.counts('m98')[0] == 0
    assert report.counts('m99')[0] > 0
    assert findings(report, 'A6', 'appears in multiple matters')
    assert findings(report, 'A6', 'owned by m99')
    filtered = run_validator(spine, matter='m98')
    assert filtered.matters_seen == {'m98'}
    assert not findings(filtered, 'C13')


def test_name_sweep_checks_reserved_surnames_and_high_scrutiny_shapes(spine):
    add_second_matter(spine, 'm02')
    (spine/'jurisdictions/meridian.json').write_text(json.dumps({'judge_pool':{'judges':[{'name':'Hon. Adaeze Okwuosa Jr.'}]}}))
    report = run_validator(spine)
    assert findings(report, 'A6', "surname 'okwuosa' collides")
    assert findings(report, 'A6', 'm02 is a discipline/DWI shape')


def test_cross_file_subtask_id_collision_is_reported_with_sources(spine):
    original = spine/'tasks/TSK-001.json'
    obj = json.loads(original.read_text())
    obj['id']='TSK-002'
    obj['@id']=obj['@id'].replace('TSK-001','TSK-002')
    (spine/'tasks/TSK-002.json').write_text(json.dumps(obj))
    matches = findings(run_validator(spine), 'A1', "duplicate id 'TSK-001.01'")
    assert len(matches)==1
    assert len(matches[0].detail['sources'])==2


def test_repeated_foreign_prefix_in_one_file_is_reported_once(spine):
    change(spine, MATTER+'matter.json', ['skill_refs'], ['m98.per.other','m98.per.other'])
    matches = findings(run_validator(spine), 'A1', 'foreign matter-prefix')
    assert len(matches)==1 and 'm98.per.other' in matches[0].message



def test_invalid_persona_schema_prevents_semantic_cascades(spine):
    change(spine, PERSONA, ['identity','age'], 'invalid')
    change(spine, PERSONA, ['disclosure','concealed',0,'fact_ref'], 'm99.fact.999')
    report = run_validator(spine)
    assert findings(report, 'F29', 'persona schema fail')
    assert not findings(report, 'C13')


def test_missing_firm_rate_card_warns_instead_of_asserting_wrong_rate(spine):
    (spine/'firm/firm.json').unlink()
    change(spine, MATTER+'business.json', ['time_entries',0,'rate'], 999)
    matches = findings(run_validator(spine), 'B7', 'unverifiable')
    assert len(matches)==1 and matches[0].severity==vs.WARN


@pytest.mark.parametrize('layer,link', [('skills','skill'),('tasks','task')])
def test_missing_taxonomy_layer_breaks_correct_d1_link(spine, layer, link):
    shutil.rmtree(spine/layer)
    for strict, severity in [(False,vs.WARN),(True,vs.ERROR)]:
        report=run_validator(spine,strict=strict)
        matches=findings(report,'A3',f'broken at {link}')
        assert len(matches)==1 and matches[0].severity==severity


def test_degraded_schema_still_checks_required_fields_and_id_patterns(spine, monkeypatch):
    monkeypatch.setattr(vs, 'HAVE_JSONSCHEMA', False)
    schemas=vs.SchemaSet(spine/'schemas')
    errors=schemas.validate('matter', {'id':'not-a-matter'})
    assert ('schema_version', "missing required property 'schema_version'") in errors
    assert any(field=='id' and 'does not match' in message for field,message in errors)
    assert schemas.validate('not-a-schema',{}) == []
    # The actual validator still accepts the complete spine in fallback mode.
    report=run_validator(spine)
    assert not report.has_errors()


@pytest.mark.parametrize('folio,none,expected', [
    ({'iri':'Rtest','mapping_confidence':'exact'},True,'exactly one'),
    (None,False,'exactly one'),
    ({'iri':'bad','mapping_confidence':'exact'},False,'malformed'),
    ({'iri':'Rtest','mapping_confidence':'approximate'},False,'confidence invalid'),
])
def test_degraded_mode_retains_folio_semantic_gate(spine, monkeypatch, folio, none, expected):
    monkeypatch.setattr(vs,'HAVE_JSONSCHEMA',False)
    path=spine/'skills/SK-LP-01.json'
    obj=json.loads(path.read_text())
    obj.pop('no_folio_equivalent')
    if folio is not None:
        obj['folio']=folio
    if none:
        obj['no_folio_equivalent']=True
    path.write_text(json.dumps(obj))
    matches=findings(run_validator(spine),'E23',expected)
    assert len(matches)==1 and matches[0].severity==vs.ERROR


def test_degraded_mode_still_rejects_unknown_rapport_triggers(spine, monkeypatch):
    monkeypatch.setattr(vs,'HAVE_JSONSCHEMA',False)
    change(spine, PERSONA, ['disclosure','rapport_gated',0,'requires'], ['magic_spell'])
    assert findings(run_validator(spine),'C15','outside closed vocabulary')


def test_degraded_mode_still_requires_all_eight_exercise_sections(spine, monkeypatch):
    monkeypatch.setattr(vs,'HAVE_JSONSCHEMA',False)
    change(spine, MATTER+'exercise.json', ['sections'], {})
    missing=findings(run_validator(spine),'DEPTH','exercise section')
    assert len(missing)==len(vs.SECTION_KEYS)


def test_business_fee_type_must_agree_with_frozen_manifest(spine):
    change(spine, MATTER+'business.json', ['engagement'], {'fee_type':'flat','flat_fee':1050,'engagement_date':'2026-01-12','letter_md':'business/engagement-letter.md'})
    assert findings(run_validator(spine),'B8','business fee_type flat != manifest hourly')


def test_machine_report_preserves_global_schema_evidence(spine, tmp_path, capsys):
    change(spine, 'firm/firm.json', ['schema_version'], '9.0.0')
    destination=tmp_path/'machine.json'
    assert vs.main(['--data-dir',str(spine),'--strict','--quiet','--json',str(destination)])==1
    payload=json.loads(destination.read_text())
    encoded=json.dumps(payload)
    assert '!= manifest' in encoded
    assert str(spine/'firm/firm.json') in encoded
    assert capsys.readouterr().out==''
