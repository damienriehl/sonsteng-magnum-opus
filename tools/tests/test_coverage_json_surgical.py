"""Parser refusal and real caller integration for formatting-preserving edits."""
import json

import pytest

from test_json_surgical import js, ap


@pytest.mark.parametrize('raw', ['', '"unterminated', 'truth', '[1', '[1 2]', '{a:1}', '{"a" 1}', '{"a":1', '{"a":1 "b":2}'])
def test_malformed_json_is_refused(raw):
    with pytest.raises(js.SurgicalError):
        js.parse(raw)


@pytest.mark.parametrize('value', [[], {}])
def test_container_replacements_refused(value):
    with pytest.raises(js.SurgicalError, match='scalars/strings only'):
        js.splice_scalar('{"value":1}', 'value', value)


def test_non_integer_array_navigation_refused():
    with pytest.raises(js.SurgicalError, match='non-int index'):
        js.locate('{"values":[1]}', 'values.first')


def test_conflicting_duplicate_edits_refused():
    with pytest.raises(js.SurgicalError, match='overlapping edit spans'):
        js.splice_scalars('{"value":0}', [('value', 1), ('value', 2)])


@pytest.mark.parametrize('raw', ['{}', '{\n}', '{"nested": {}}'])
def test_insert_into_empty_object_preserves_semantics_and_replays(raw):
    path = 'nested' if 'nested' in raw else ''
    edits = [(path, 'name', 'café')]
    result = js.insert_object_properties(raw, edits)
    decoded = json.loads(result)
    assert (decoded['nested'] if path else decoded) == {'name': 'café'}
    assert js.insert_object_properties(result, edits) == result


def test_insert_refuses_wrong_target_and_conflicting_existing_property():
    with pytest.raises(js.SurgicalError, match='not an object'):
        js.insert_object_properties('{"a":[]}', [('a', 'name', 1)])
    with pytest.raises(js.SurgicalError, match='different value'):
        js.insert_object_properties('{"a":1}', [('', 'a', 2)])


def test_real_file_caller_updates_array_and_nested_object_with_minimal_diff(tmp_path):
    source = tmp_path / 'document.json'
    original = '{\n  "names": ["first", "second"],\n  "details": {"enabled": true},\n  "untouched": 1.000\n}\n'
    source.write_text(original)
    result = ap.write_json_edits(source.read_text(), [('names.1', 'last'), ('details.enabled', False)])
    source.write_text(result)
    assert source.read_text() == original.replace('"second"', '"last"').replace('true', 'false')
    assert json.loads(source.read_text()) == {'names': ['first', 'last'], 'details': {'enabled': False}, 'untouched': 1}
    assert ap.write_json_edits(result, [('names.1', 'last'), ('details.enabled', False)]) == result
