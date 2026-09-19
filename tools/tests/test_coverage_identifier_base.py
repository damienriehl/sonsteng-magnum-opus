"""Identifier rewriting preserves unrelated bytes and scans authoritative scope."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import identifier_base as ib


@pytest.mark.parametrize("prefix", [r"\ud83d\ude00", r"\ud800", r"\ud800\u0041", r"\n\t\"\\"])
def test_json_escape_spans_before_and_after_multiple_identifiers(prefix):
    old = ib.OLD_JSONLD_BASE_TEXT
    payload = ('{ "' + old + 'key": ["' + prefix + old + 'one ' + old +
               'two' + prefix + '", null, true, 42], "plain":"\\u263a" }\n').encode()
    rewritten, count = ib.replace_identifier_base(payload)
    assert count == 3
    assert rewritten == payload.replace(ib.OLD_JSONLD_BASE, ib.NEW_JSONLD_BASE)
    assert ib.identifier_base_counts(Path("data.JSON"), rewritten) == (0, 3)
    assert ib.replace_identifier_base(rewritten) == (rewritten, 0)
    assert json.loads(rewritten) != json.loads(payload)


@pytest.mark.parametrize("payload", [b"", b"null", b"[false, 0, {}]", b'"unrelated \\u263a"', b"\xffunchanged"])
def test_no_identifier_preserves_original_bytes(payload):
    assert ib.replace_identifier_base(payload) == (payload, 0)
    assert ib.identifier_base_counts(Path("data.json"), payload) == (0, 0)


@pytest.mark.parametrize("prefix", [b"not json: ", b"\xff"])
def test_invalid_json_falls_back_to_literal_and_escaped_bytes(prefix):
    payload = prefix + ib.OLD_JSONLD_BASE + b"one " + ib.OLD_JSONLD_ESCAPED_BASE + b"two"
    rewritten, count = ib.replace_identifier_base(payload)
    assert count == 2
    assert rewritten == prefix + ib.NEW_JSONLD_BASE + b"one " + ib.NEW_JSONLD_ESCAPED_BASE + b"two"
    assert ib.identifier_base_counts(Path("broken.json"), payload) == (2, 0)
    assert ib.identifier_base_counts(Path("broken.json"), rewritten) == (0, 2)
    # Non-JSON inventories count literal URLs, even if slash escapes appear in prose.
    assert ib.identifier_base_counts(Path("notes.md"), payload) == (1, 0)


def test_authoritative_scan_rewrite_and_rescan_real_files(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "spine-manifest.json").write_bytes(b'{"id":"' + ib.OLD_JSONLD_BASE + b'manifest"}')
    for tree in ib.IDENTIFIER_TREE_NAMES:
        (data / tree / "nested").mkdir(parents=True)
        (data / tree / "nested/item.json").write_bytes(b'{"id":"' + ib.OLD_JSONLD_ESCAPED_BASE + b'item"}')
    excluded = data / "private.json"
    excluded.write_bytes(ib.OLD_JSONLD_BASE)
    (data / "matters/linked.json").symlink_to(excluded)
    paths = ib.authoritative_paths(data)
    assert len(paths) == 7
    assert paths == sorted(paths, key=lambda path: path.relative_to(data).as_posix())
    assert ib.authoritative_scope_gaps(data, paths) == []
    for path in paths:
        assert ib.identifier_base_counts(path, path.read_bytes()) == (1, 0)
        rewritten, count = ib.replace_identifier_base(path.read_bytes())
        assert count == 1
        path.write_bytes(rewritten)
    assert all(ib.identifier_base_counts(path, path.read_bytes()) == (0, 1) for path in paths)
    assert excluded.read_bytes() == ib.OLD_JSONLD_BASE
    assert (data / "matters/linked.json").is_symlink()


def test_scope_reports_missing_invalid_symlink_and_empty_inputs(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "entry.json").write_text('{}')
    data = tmp_path / "data"
    data.mkdir()
    (data / "spine-manifest.json").symlink_to(outside / "entry.json")
    (data / "matters").symlink_to(outside, target_is_directory=True)
    (data / "curriculum").write_text("not a directory")
    (data / "jurisdictions").mkdir()
    paths = ib.authoritative_paths(data)
    assert paths == []
    gaps = ib.authoritative_scope_gaps(data, paths)
    assert len(gaps) == 7
    assert "spine-manifest.json is missing, invalid, or a symlink" in gaps
    assert "matters/ is missing, invalid, or a symlink" in gaps
    assert "curriculum/ is missing, invalid, or a symlink" in gaps
    assert "jurisdictions/ contains no authoritative files" in gaps


def test_missing_data_directory_produces_explicit_scope_gaps(tmp_path):
    data = tmp_path / "missing"
    assert ib.authoritative_paths(data) == []
    assert len(ib.authoritative_scope_gaps(data, [])) == 7
