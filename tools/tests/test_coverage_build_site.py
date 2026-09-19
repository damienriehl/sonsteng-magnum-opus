"""Public builder boundaries using disposable data and generated output trees."""
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_site as bs


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    matter = data / 'matters' / 'm01-fixture'
    write_json(data / 'matters' / 'manifest.json', {'matters': [{'id': 'm01'}]})
    write_json(data / 'jurisdictions' / 'meridian.json', {'name': 'Fixture jurisdiction'})
    write_json(data / 'taxonomy' / 'skills.json', {'skills': []})
    write_json(data / 'taxonomy' / 'tasks.json', {'tasks': []})
    write_json(data / 'taxonomy' / 'folio-crosswalk.json', {})
    write_json(data / 'firm' / 'firm.json', {})
    for page in ('home', 'matters', 'firm'):
        write_json(data / 'copy' / (page + '.json'), {'shape_labels': {}})
    write_json(matter / 'matter.json', {'id': 'm01', 'caption': 'Fixture & matter', 'tier': 'meridian', 'jurisdiction': 'meridian'})
    write_json(matter / 'rubric.json', {})
    write_json(matter / 'exercise' / 'exercise.json', {'sections': {
        'history': {'title': 'History', 'body_md': 'A public history.'},
        'case_file': {'files': ['case-file/exhibit.md']}}})
    (matter / 'case-file').mkdir()
    (matter / 'case-file' / 'exhibit.md').write_text('A public exhibit.', encoding='utf-8')
    for name, value in {'ROOT': str(tmp_path), 'DATA': str(data),
                        'MATTERS_DIR': str(data / 'matters'),
                        'CURRICULUM_DIR': str(data / 'curriculum'),
                        'OUT': str(tmp_path / 'output'), 'PAGE_OVERRIDES': {},
                        'EDMAP': type(bs.EDMAP)()}.items():
        monkeypatch.setattr(bs, name, value)
    return data, matter


def test_dataset_load_to_packet_preserves_public_content(dataset):
    _, matter = dataset
    corpus = bs.load_corpus()
    sizes = bs.build_packet_pages(corpus)
    assert len(sizes) == 1
    page = Path(bs.OUT, sizes[0][0])
    assert page.stat().st_size == sizes[0][1]
    rendered = page.read_text()
    assert 'Fixture &amp; matter' in rendered
    assert 'A public exhibit.' in rendered
    assert 'A public history.' in rendered
    assert bs.check_no_instructor_leaks(corpus) == []
    assert corpus['matters'][0]['_dir'] == str(matter)


def test_oversize_case_file_is_split_from_packet(dataset):
    _, matter = dataset
    text = ('Distinct exhibit content ' * 14000).strip()
    (matter / 'case-file' / 'exhibit.md').write_text(text)
    corpus = bs.load_corpus()
    sizes = bs.build_packet_pages(corpus)
    assert [name for name, _ in sizes] == ['matters/m01-fixture/case-file.html', 'matters/m01-fixture/index.html']
    case = Path(bs.OUT, sizes[0][0]).read_text()
    packet = Path(bs.OUT, sizes[1][0]).read_text()
    assert text in case
    assert text not in packet
    assert 'href="case-file.html"' in packet
    assert 'A public history.' in packet
    assert sizes[1][1] < 250000


@pytest.mark.parametrize('history', [None, {}, {'body_md': '   '}])
def test_loader_rejects_missing_public_history(dataset, history):
    _, matter = dataset
    write_json(matter / 'exercise' / 'exercise.json', {'sections': {'history': history}})
    with pytest.raises(ValueError, match='m01 requires exercise.sections.history.body_md'):
        bs.load_corpus()


def test_loader_skips_nonmatter_entries_and_bad_persona_files(dataset):
    data, matter = dataset
    (data / 'matters' / 'm02-file').write_text('not a directory')
    (data / 'matters' / 'm03-empty').mkdir()
    personas = matter / 'personas'
    write_json(personas / 'valid.json', {'id': 'person', 'identity': {'name': 'Someone'}})
    write_json(personas / 'array.json', [])
    write_json(personas / 'partial.json', {'id': 'partial'})
    write_json(personas / 'topic-labels.json', {'id': 'labels', 'identity': {}})
    (personas / 'broken.json').write_text('{')
    corpus = bs.load_corpus()
    assert [m['id'] for m in corpus['matters']] == ['m01']
    assert list(corpus['matters'][0]['_personas']) == ['person']
    assert all(not text for text in corpus['curriculum']['volumes'].values())


def test_loader_registers_only_deliberate_page_overrides(dataset):
    data, _ = dataset
    record = {'intent': 'deliberate_page_override', 'page': 'index.html',
              'shared_source_ref': 'shared#title', 'value': 'Local title'}
    write_json(data / 'copy' / 'home.json', {'overrides': {'local': record, 'ignored': {'intent': 'other'}}})
    bs.load_corpus()
    assert list(bs.PAGE_OVERRIDES) == [('index.html', 'shared#title')]
    assert bs.PAGE_OVERRIDES[('index.html', 'shared#title')]['source_ref'] == 'data/copy/home.json#overrides.local.value'


def test_link_checker_reports_each_kind_of_invalid_link(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'OUT', str(tmp_path))
    bs.write_file('index.html', '<p id="here"><a href="#missing">x</a><a href="lost.html">x</a>'
                  '<a href="sub/?q=1#wrong">x</a><img src="https://invalid.example/image.png"></p>')
    bs.write_file('sub/index.html', '<p id="present">ok</p>')
    count, errors = bs.check_links()
    assert count == 2
    assert errors == ['index.html: missing anchor #missing', 'index.html: broken link lost.html',
                      'index.html: missing anchor sub/#wrong',
                      'index.html: EXTERNAL request https://invalid.example/image.png']


def test_link_checker_accepts_queries_fragments_and_permitted_links(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'OUT', str(tmp_path))
    bs.write_file('index.html', '<p id="here"><a href="#here">self</a><a href="sub/?q=1#ok">sub</a>'
                  '<a href="https://creativecommons.org/licenses/by/4.0/">license</a>'
                  '<a href="mailto:fixture@example.invalid">email</a><img src="data:image/png;base64,AA=="></p>')
    bs.write_file('sub/index.html', '<p id="ok">ok</p><a href="../index.html#here">home</a>')
    bs.write_file('assets/ignored.html', '<a href="missing.html">design input</a>')
    assert bs.check_links() == (2, [])


def test_instructor_guard_scans_archives_and_loose_text(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'OUT', str(tmp_path))
    secret = 'A synthetic concealed fact long enough to activate the safety guard.'
    corpus = {'matters': [{'_personas': {'p': {'id': 'p', 'disclosure': {'concealed': [{'text': secret}]}}}}]}
    bs.write_file('data/facts.md', secret)
    with zipfile.ZipFile(tmp_path / 'student.zip', 'w') as archive:
        archive.writestr('nested/answer-key.md', 'INSTRUCTOR-ONLY ' + secret)
    bs.write_file('broken.zip', b'not a zip file')
    bs.write_file('image.bin', b'INSTRUCTOR-ONLY')
    errors = bs.check_no_instructor_leaks(corpus)
    assert any('instructor file present in archive: student.zip!nested/answer-key.md' == error for error in errors)
    assert any('unreadable student archive broken.zip:' in error for error in errors)
    assert 'p [concealed] leaked into data/facts.md' in errors
    assert 'p [concealed] leaked into student.zip!nested/answer-key.md' in errors
    assert 'INSTRUCTOR-ONLY sentinel present in output file: student.zip!nested/answer-key.md' in errors
    assert 'instructor file copied into output: data/facts.md' in errors
    assert not any('image.bin' in error for error in errors)


def test_clean_output_preserves_input_assets_and_removes_stale_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'OUT', str(tmp_path / 'output'))
    bs.clean_output()
    bs.write_file('assets/design.css', 'body {}')
    bs.write_file('old/nested.html', 'stale')
    bs.write_file('old.json', '{}')
    bs.clean_output()
    assert list(Path(bs.OUT).iterdir()) == [Path(bs.OUT, 'assets')]
    assert Path(bs.OUT, 'assets/design.css').read_text() == 'body {}'


@pytest.mark.parametrize('personas,expected', [([], None),
    ([{'id': 'w', 'identity': {'role': 'witness'}}], 'w'),
    ([{'id': 'w', 'identity': {'role': 'witness'}}, {'id': 'c', 'identity': {'role': 'guardian of client'}}], 'c')])
def test_client_fallbacks(personas, expected):
    matter = {'_personas': {p['id']: p for p in personas}}
    selected = bs.pick_client_persona(matter, {})
    assert (selected['id'] if selected else None) == expected
    assert bs.pick_represented_persona(matter, selected) is None


@pytest.mark.parametrize('function,value,expected', [(bs.money, None, '—'), (bs.money_compact, 'invalid', '—'),
    (bs.money_compact, -2500000, '$-2.50M'), (bs.pct, 0.125, '12.5%'),
    (bs.pct, 75, '75.0%'), (bs.pct, None, '—')])
def test_financial_display_boundaries(function, value, expected):
    assert function(value) == expected


def test_catalog_empty_invalid_size_and_short_history():
    assert bs.paginate_catalog_records([]) == [[]]
    with pytest.raises(ValueError, match='positive'):
        bs.paginate_catalog_records([], 0)
    assert bs.catalog_history_summary({'_history': {'body_md': '**Brief** history. {#b:12345678}'}}) == 'Brief history.'


def test_optional_renderers_and_instructor_case_file_exclusions(tmp_path):
    matter = {'_dir': str(tmp_path)}
    assert bs.render_doc_card(str(tmp_path), 'absent.md', 'missing') == ''
    assert bs.render_case_file_cards(matter, ['facts.md', 'case-file/facts.md', 'exercise/student.md']) == []
    assert bs.build_business_section({}) == ''
    assert bs.build_rubric_section({}, 'index.html') == ''
    assert bs.matter_fees({}) == 0


def test_editor_walker_nested_candidates_keep_indexes_and_ignore_unknown_refs(monkeypatch):
    monkeypatch.setattr(bs, 'EDMAP', type(bs.EDMAP)())
    bs.EDMAP.register('known#title', 'json_scalar', 'Outer inner tail', False,
                      'fixture title', json_path='title')
    entries, refs, annotated = bs._extract_page_blocks(
        '<p>outside</p><main><p data-ebsrc="unknown">unregistered</p>'
        '<p data-ebsrc="known#title">Outer <p>inner</p> tail</p>'
        '<span data-ebrender="known#title">passive</span></main>')
    assert annotated is True
    assert refs == ['known#title']
    assert len(entries) == 1
    assert entries[0]['index'] == 1
    assert entries[0]['original_hash'] == bs.text_norm.norm_hash('Outer inner tail')
    assert entries[0]['json_path'] == 'title'
    bs.validate_editor_contract({'index.html': entries}, {}, {})


@pytest.mark.parametrize('reason', ['', '  ', None, 17])
def test_editor_transition_exception_requires_explanation(reason):
    with pytest.raises(bs.EditorContractError, match='transition allowlist entries require a reason: ref'):
        bs.validate_editor_contract({}, {}, {'ref': reason})


def test_firm_dashboard_exports_unreconciled_status_and_met_targets(tmp_path, monkeypatch):
    # Read authored curriculum data; every generated byte stays under tmp_path.
    monkeypatch.setattr(bs, 'OUT', str(tmp_path))
    monkeypatch.setattr(bs, 'PAGE_OVERRIDES', {})
    monkeypatch.setattr(bs, 'EDMAP', type(bs.EDMAP)())
    corpus = bs.load_corpus()
    corpus['firm']['realization_target'] = 0.01
    corpus['firm']['collection_target'] = 0.01
    corpus['firm']['trust']['three_way_reconciled'] = False
    bs.build_firm_dashboard(corpus)
    page = (tmp_path / 'firm' / 'index.html').read_text()
    assert 'TRUST DISCREPANCY' in page
    assert 'Trust balances by matter, each reconciled' not in page
    assert 'Trust balances by matter, discrepancy' in page
    import re
    trust_svg = re.search(r'<svg[^>]*aria-label="Trust balances by matter[^>]*>.*?</svg>', page, re.S).group()
    assert '  ✓' not in trust_svg
    assert '  ⚠' in trust_svg
    assert 'TARGET 1%' in page
    assert 'TARGET 1% · BELOW' not in page
    import csv
    with (tmp_path / 'firm' / 'csv' / 'trust-balances.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows
    assert {row['reconciled'] for row in rows} == {'no'}
