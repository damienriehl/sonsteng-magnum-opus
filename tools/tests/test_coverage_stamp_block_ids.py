"""Filesystem integration and migration safety for durable block stamping."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stamp_block_ids as sb


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, 'REPO_ROOT', str(tmp_path))
    for attr, filename in [('EDITOR_MAP', 'editor.json'),
                           ('INSTRUCTOR_BUNDLE', 'instructor.json'),
                           ('UNMARKED_REPORT', 'unmarked.json')]:
        monkeypatch.setattr(sb, attr, str(tmp_path / filename))
    return tmp_path


def put(root, filename, value):
    path = root / filename
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def block(ref, **kwargs):
    return {'kind': 'prose', 'source_ref': ref, 'index': 0,
            'original_text': 'Example', **kwargs}


def test_cli_real_maps_to_surgical_files_and_renderer_is_idempotent(corpus, capsys):
    md = corpus / 'lesson.md'
    md.write_text('Existing {#b:12345678}\n\nNew paragraph.\n')
    raw = '{ "untouched" : "caption", "body" : "First\\n\\nSecond", "extra":"Third" }\n'
    data = corpus / 'lesson.json'
    data.write_text(raw)
    extra = corpus / 'extra.md'
    extra.write_text('Unmapped prose.\n')
    put(corpus, 'editor.json', {'pages': {'p': [block('lesson.md#p0'),
        block('lesson.json#body.p0'), {'kind': 'scalar', 'source_ref': 'ignored.json#title'},
        block('missing.md#p0')]}})
    put(corpus, 'instructor.json', {'docs': [{'blocks': [block('lesson.json#body.b12345678')]}]})
    put(corpus, 'unmarked.json', {'unmarked': {'lesson.json#extra': 1, 'extra.md': 1}})
    originals = {p: p.read_bytes() for p in (md, data, extra)}
    assert sb.main(['--check']) == 0
    output = capsys.readouterr().out
    assert 'would stamp 5 block(s) across 3 file(s)' in output
    assert '1 pre-existing bid(s) preserved; 4 file(s) targeted' in output
    assert 'WARNING: mapped source missing on disk: missing.md' in output
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert sb.main([]) == 0
    assert 'stamped 5 block(s)' in capsys.readouterr().out
    ids = sb.existing_bids_in_corpus(['lesson.md', 'lesson.json', 'extra.md', 'missing.md'])
    assert len(ids) == 6 and '12345678' in ids
    assert data.read_text().startswith('{ "untouched" : "caption", "body" : ')
    assert data.read_text().endswith(' }\n')
    for path in (md, extra):
        spans = []
        rendered = sb.bs.markdown(path.read_text(), spans=spans)
        assert all(s['bid'] for s in spans)
        assert '{#b:' not in rendered
    value = json.loads(data.read_text())
    assert value['untouched'] == 'caption'
    assert len(sb.BID_RE.findall(value['body'])) == 2
    stamped = {p: p.read_bytes() for p in originals}
    assert sb.main([]) == 0
    assert 'stamped 0 block(s) across 0 file(s)' in capsys.readouterr().out
    assert all(p.read_bytes() == content for p, content in stamped.items())


def test_cli_missing_maps_reports_fatal(corpus, capsys):
    assert sb.main([]) == 2
    assert 'FATAL: no generated map found' in capsys.readouterr().err


@pytest.mark.parametrize('bundle', [{}, {'pages': None, 'docs': None}, {'pages': {'p': []}, 'docs': [{}]}])
def test_empty_maps_target_nothing(corpus, capsys, bundle):
    put(corpus, 'editor.json', bundle)
    assert sb.main([]) == 0
    assert '0 file(s) targeted' in capsys.readouterr().out
    assert sb.collect_unmarked_targets({}) == {}


def test_targets_merge_documents_and_page_fields():
    bundles = [{'pages': {'p': [block('a.json#section.body.p123'), block('a.md#p0')]}},
               {'docs': [{'blocks': [block('a.json#section.body.babcdef12'),
                                    block('a.json#another.body'), {'kind': 'scalar'}]}, {}]}]
    assert sb.collect_targets(bundles) == {'a.md': set(), 'a.json': {'section.body', 'another.body'}}


@pytest.mark.parametrize('field', ['missing', 'nested.absent', 'scalar.child', 'array.0', 'number', 'null', 'array'])
def test_non_string_and_missing_json_paths_leave_file_untouched(tmp_path, field):
    path = put(tmp_path, 'doc.json', {'nested': {}, 'scalar': 'text', 'array': ['text'], 'number': 1, 'null': None})
    original = path.read_bytes()
    assert sb.stamp_file(str(path), [field], set()) == 0
    assert path.read_bytes() == original


@pytest.mark.parametrize('raw', ['', '\n\n', '---\n', '| A | B |\n|---|---|\n| 1 | 2 |\n'])
def test_empty_or_structural_markdown_remains_untouched(tmp_path, raw):
    path = tmp_path / 'empty.md'
    path.write_text(raw)
    assert sb.stamp_file(str(path), [], set()) == 0
    assert path.read_text() == raw


def test_invalid_json_fails_before_writing(tmp_path):
    path = tmp_path / 'invalid.json'
    path.write_text('{invalid')
    with pytest.raises(json.JSONDecodeError):
        sb.stamp_file(str(path), ['body'], set())
    assert path.read_text() == '{invalid'


def test_mint_retries_collisions_without_losing_registry(monkeypatch):
    values = iter(['12345678', '12345678', 'abcdef12'])
    monkeypatch.setattr(sb.secrets, 'token_hex', lambda n: next(values))
    registry = {'12345678'}
    assert sb.mint_bid(registry) == 'abcdef12'
    assert registry == {'12345678', 'abcdef12'}


@pytest.mark.parametrize(('change', 'diagnostic'), [
    ({'kind': 'scalar'}, 'kind prose -> scalar'),
    ({'json_path': 'other.body'}, 'json_path None -> other.body'),
    ({'index': 1}, 'index 0 -> 1'),
    ({'source_ref': 'other.md#b12345678'}, 'source file a.md -> other.md'),
    ({'original_text': None}, 'text changed'),
])
def test_equivalence_cli_reports_structural_drift(corpus, capsys, change, diagnostic):
    before = put(corpus, 'before.json', {'pages': {'p': [block('a.md#p0')]}})
    migrated = {**block('a.md#b12345678'), **change}
    after = put(corpus, 'after.json', {'pages': {'p': [migrated]}})
    assert sb.main(['--equivalence', str(before), str(after)]) == 1
    out = capsys.readouterr().out
    assert diagnostic in out and '1 error(s)' in out


def test_equivalence_allows_locator_only_change_and_empty_json_path(corpus, capsys):
    before = put(corpus, 'before.json', {'pages': {'p': [block('a.json#body.p0', json_path='')]}})
    after = put(corpus, 'after.json', {'pages': {'p': [block('a.json#body.b12345678')]}})
    assert sb.main(['--equivalence', str(before), str(after)]) == 0
    assert 'equivalence proven' in capsys.readouterr().out
    assert sb.equivalence_check({'pages': None}, {}) == []


def test_equivalence_new_page_and_missing_page_are_both_reported():
    assert sb.equivalence_check({'pages': {'old': []}}, {'pages': {'new': []}}) == [
        'page missing after migration: old', 'page appeared after migration: new']
