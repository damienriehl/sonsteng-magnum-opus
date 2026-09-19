"""Exercise offline apply safety through real schema, patch, lock and git paths."""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apply_suggestions as ap


def write(root, rel, content):
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def patch(rel="data/matter.json", path="value", **changes):
    values = dict(suggestion_id="s1", group_id="g1", source_ref=rel + "#" + path,
                  relpath=rel, kind="json_scalar", json_path=path,
                  original_text="old", new_text="new")
    values.update(changes)
    return ap.Patch(**values)


@pytest.mark.parametrize("page,expected", [("matters/m1.html", "matters"),
    ("firm/index.html", "firm"), ("modules/m1.html", "home"), (None, "home")])
def test_override_routes_non_copy_sources_to_surface_owned_copy(page, expected):
    rel, path, ref = ap.page_override_address(page, "data/matters/m1/overview.md#b12345678")
    assert rel == "data/copy/" + expected + ".json"
    assert path.startswith("overrides.") and path.endswith(".value")
    assert ref == rel + "#" + path


@pytest.mark.parametrize("incoming,current,expected", [
    ("true", False, True), ("false", True, False), ("1.25", 0.0, 1.25),
    ("-0.25e2", 0.0, -25.0), ("new", "old", "new")])
def test_override_typed_values(incoming, current, expected):
    actual = ap.coerce_page_override_value(incoming, current)
    assert actual == expected and type(actual) is type(expected)


def test_override_rejects_float_overflow():
    with pytest.raises(ValueError, match="finite"):
        ap.coerce_page_override_value("1e9999", 0.0)


@pytest.mark.parametrize("target", ["", "#value", "data/../outside", "data\\..\\outside", "/outside", "~/outside"])
def test_safe_data_path_rejects_unsafe_addresses(tmp_path, target):
    (tmp_path / "data").mkdir()
    with pytest.raises(ap.ApplyError):
        ap.safe_data_path(tmp_path, target)


@pytest.mark.parametrize("directory", [False, True])
def test_safe_data_path_rejects_real_symlink_escape(tmp_path, directory):
    (tmp_path / "data").mkdir()
    outside = write(tmp_path, "outside/value.json", "{}")
    link = tmp_path / "data/link"
    link.symlink_to(outside.parent if directory else outside, target_is_directory=directory)
    with pytest.raises(ap.ApplyError, match="escapes data"):
        ap.safe_data_path(tmp_path, "data/link/value.json" if directory else "data/link")
    assert outside.read_text() == "{}"


def test_lock_contends_and_releases_even_after_exception(tmp_path):
    lock = str(tmp_path / "locks/apply.lock")
    with pytest.raises(RuntimeError, match="interrupted"):
        with ap.apply_lock(lock):
            assert Path(lock).read_text() == "pid=%d\n" % os.getpid()
            with pytest.raises(ap.ApplyError, match="another apply run"):
                with ap.apply_lock(lock):
                    pytest.fail("contending lock acquired")
            raise RuntimeError("interrupted")
    with ap.apply_lock(lock):
        assert Path(lock).read_text().startswith("pid=")


@pytest.mark.parametrize("syntax", [False, True])
def test_generator_dependency_errors_explain_missing_or_unparseable_file(tmp_path, syntax):
    if syntax:
        for rel in ap.GENERATOR_ENTRYPOINTS:
            write(tmp_path, rel, "this is not valid python !!!\n")
    with pytest.raises(ap.ApplyError, match="cannot be parsed" if syntax else "is missing"):
        ap.generator_dependency_paths(tmp_path)


def test_generator_identity_follows_package_import_and_ignores_unrelated_file(tmp_path):
    for rel in ap.GENERATOR_ENTRYPOINTS:
        write(tmp_path, rel, "import shared\n")
    helper = write(tmp_path, "tools/shared/__init__.py", "VALUE = 1\n")
    before = ap.SubprocessPipeline().generator_identity(tmp_path)
    write(tmp_path, "tools/unrelated.py", "VALUE = 9\n")
    assert ap.generator_identity(tmp_path) == before
    helper.write_text("VALUE = 2\n")
    assert ap.generator_identity(tmp_path) != before
    assert "tools/shared/__init__.py" in ap.generator_dependency_paths(tmp_path)


@pytest.mark.parametrize("incoming,current,expected", [
    ("true", False, True), ("false", True, False), ("-12", 0, -12),
    ("2.0", 0.5, 2), ("0.125", 0.5, 0.125)])
def test_schema_less_scalar_preserves_supported_types(tmp_path, incoming, current, expected):
    actual = ap.coerce_json_scalar(tmp_path, "data/custom.json", "value", incoming, current)
    assert actual == expected and type(actual) is type(expected)


@pytest.mark.parametrize("incoming,current,message", [
    ("TRUE", False, "boolean"), ("1.2", 0, "integer"), ("01", 0, "integer"),
    ("１２", 0, "integer"), ("+2", 0, "integer"), ("[]", [], "unsupported"),
    ("1e101", 0.0, "range"), ("1e13", 0.0, "range"),
    ("0.1234567890123456789", 0.0, "range")])
def test_schema_less_scalar_rejects_invalid_and_unbounded_input(tmp_path, incoming, current, message):
    with pytest.raises(ValueError, match=message):
        ap.coerce_json_scalar(tmp_path, "data/custom.json", "value", incoming, current)


def test_schema_refs_arrays_nullable_type_feed_real_file_patch(tmp_path):
    write(tmp_path, "data/schemas/matter.schema.json", json.dumps({
        "$defs": {"a/b~c": {"type": ["null", "integer"]},
                  "record": {"properties": {"value": {"$ref": "#/$defs/a~1b~0c"}}}},
        "properties": {"records": {"type": "array", "items": {"$ref": "#/$defs/record"}}}}))
    source = write(tmp_path, "data/matter.json", '{\n    "records": [{"value": null}],\n    "untouched": "é"\n}\n')
    old = source.read_text()
    p = patch(path="records.0.value", new_text="42")
    row = {"id": "s1", "source_ref": p.source_ref, "kind": "json_scalar",
           "json_path": p.json_path, "new_text": "42", "original_hash": "same"}
    block = {"kind": "json_scalar", "json_path": p.json_path, "original_hash": "same"}
    status, patches = ap._gate_group([row], {p.source_ref: block}, tmp_path)
    assert status == ""
    assert ap.apply_file_patches(tmp_path, p.relpath, patches) == {"s1": True}
    assert source.read_text() == old.replace("null", "42")
    assert ap.json_get(json.loads(source.read_text()), "records.0.value") == 42


@pytest.mark.parametrize("schema", [{"properties": {}}, {"$ref": "https://invalid.test/schema"},
    {"properties": {"value": {"$ref": "https://invalid.test/schema"}}}])
def test_unavailable_schema_leaf_falls_back_to_current_type_without_fetch(tmp_path, schema):
    write(tmp_path, "data/schemas/matter.schema.json", json.dumps(schema))
    assert ap.coerce_json_scalar(tmp_path, "data/matter.json", "value", "3", 2) == 3


def test_json_helpers_support_list_intermediates_and_list_leaf():
    obj = {"items": [{"name": "old"}, 1]}
    ap.json_set(obj, "items.0.name", "new")
    ap.json_set(obj, "items.1", 2)
    assert obj == {"items": [{"name": "new"}, 2]}
    with pytest.raises(KeyError):
        ap.json_get(obj, "items.1.missing")


@pytest.mark.parametrize("raw,escaped", [('{\n    "value": "\\u00e9"\n}\n', True),
    ('{\n    "value": "é\\u0061"\n}\n', False)])
def test_json_add_preserves_indent_newline_and_unicode_policy(raw, escaped):
    result = ap.write_json_edits(raw, [("new.leaf", "ñ")], create_paths=["new.leaf"])
    assert result.endswith("\n") and '\n    "new"' in result
    assert json.loads(result)["new"]["leaf"] == "ñ"
    assert ("\\u00f1" in result) is escaped


@pytest.mark.parametrize("body,old", [(None, "old"), ("old old", "old"), ("different", "old")])
def test_ambiguous_json_prose_abandons_other_valid_changes(tmp_path, body, old):
    source = write(tmp_path, "data/custom.json", json.dumps({"body": body, "title": "old"}))
    before = source.read_bytes()
    patches = [patch(rel="data/custom.json", path="title", new_text="changed"),
               patch(rel="data/custom.json", path="body", suggestion_id="s2", kind="prose_json_body", original_text=old)]
    assert ap.apply_file_patches(tmp_path, "data/custom.json", patches) == {"s1": True, "s2": ap.OUT_NEEDS_HUMAN}
    assert source.read_bytes() == before


@pytest.mark.parametrize("op,arg,new_text", [("split", None, "not JSON"),
    ("merge", "data/a.md#bad", ""), ("move", "data/a.md#bad", ""), ("unknown", None, "")])
def test_invalid_structural_edit_rolls_back_valid_text_edit(tmp_path, op, arg, new_text):
    source = write(tmp_path, "data/a.md", "<!-- bid:baaaaaaaa -->\n\nOld paragraph.\n")
    before = source.read_bytes()
    good = patch(rel="data/a.md", path="baaaaaaaa", kind="prose_md", original_text="Old paragraph.", new_text="New paragraph.")
    bad = patch(rel="data/a.md", path="baaaaaaaa", kind="structural_md", suggestion_id="s2", op=op, op_arg=arg, new_text=new_text)
    result = ap.apply_file_patches(tmp_path, "data/a.md", [good, bad])
    assert result["s2"] == ap.OUT_NEEDS_HUMAN
    assert source.read_bytes() == before


def test_real_git_clean_tree_and_offline_cli(tmp_path, monkeypatch, capsys):
    ap.git(["init", "-q"], tmp_path)
    write(tmp_path, "data/readme.md", "fixture\n")
    ap.git(["add", "data/readme.md"], tmp_path)
    ap.git(["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], tmp_path)
    monkeypatch.setattr(ap, "REPO_ROOT", str(tmp_path))
    for name in (ap.ENV_API_BASE, ap.ENV_SERVICE_TOKEN, ap.ENV_DEPLOY):
        monkeypatch.delenv(name, raising=False)
    assert ap.main(["--dry-run", "--no-lock"]) == 0
    output = capsys.readouterr().out
    assert ap.head_sha(tmp_path) in output and "skipped RPC" in output
    assert ap.main(["--no-lock"]) == 2
    assert "live apply needs" in capsys.readouterr().err
    write(tmp_path, "data/readme.md", "changed\n")
    assert ap.main(["--dry-run", "--no-lock"]) == 2
    assert "canonical tree is dirty" in capsys.readouterr().err


def test_real_git_failure_includes_command_exit_and_diagnostic(tmp_path):
    with pytest.raises(ap.ApplyError, match="git rev-parse HEAD failed"):
        ap.git(["rev-parse", "HEAD"], tmp_path)


def test_pipeline_plan_only_never_executes_commands(tmp_path):
    ok, result = ap.SubprocessPipeline().deploy(tmp_path, "test-fixture", plan_only=True)
    assert ok and result["executed"] is False
    assert result["planned"][0][-1] == "test-fixture"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind,row_changes,block_changes,expected", [
    ("json_scalar", {"original_hash": "stale"}, {}, ap.OUT_DRIFT),
    ("json_scalar", {"json_path": "forged"}, {}, ap.OUT_NEEDS_HUMAN),
    ("page_override_revert", {}, {}, ap.OUT_NEEDS_HUMAN),
    ("json_add", {"json_path": "custom_facts.Valid"}, {}, ap.OUT_NEEDS_HUMAN),
    ("json_add", {"json_path": "custom_facts.valid"}, {}, ap.OUT_NEEDS_HUMAN),
    ("json_add", {"json_path": "custom_facts.valid", "source_ref": "data/matter.json#custom_facts.valid", "new_text": "  "}, {}, ap.OUT_NEEDS_HUMAN),
    ("move", {"op_arg": "data/matter.json#missing"}, {}, ap.OUT_DRIFT),
    ("merge", {}, {}, ap.OUT_DRIFT),
    ("prose", {}, {"has_inline_formatting": True, "original_text": "**bold**", "original_hash": "wrong"}, ap.OUT_NEEDS_HUMAN),
])
def test_group_gate_rejects_stale_forged_and_incomplete_edits(tmp_path, kind, row_changes, block_changes, expected):
    ref = "data/matter.json#value"
    row = {"id": "s1", "kind": kind, "source_ref": ref, "json_path": "value", "new_text": "new"}
    row.update(row_changes)
    block = {"kind": "json_scalar", "json_path": "value", "original_hash": "current"}
    block.update(block_changes)
    assert ap._gate_group([row], {ref: block}, tmp_path) == (expected, [])
    assert not list(tmp_path.iterdir())


def test_json_add_gate_to_real_file_preserves_previous_facts(tmp_path):
    source = write(tmp_path, "data/custom.json", '{"custom_facts":{"existing":"keep"}}\n')
    row = {"id": "s1", "kind": "json_add", "source_ref": "data/custom.json#custom_facts.new",
           "json_path": "custom_facts.new", "new_text": "New fact"}
    status, patches = ap._gate_group([row], {}, tmp_path)
    assert status == ""
    assert ap.apply_file_patches(tmp_path, "data/custom.json", patches) == {"s1": True}
    assert json.loads(source.read_text()) == {"custom_facts": {"existing": "keep", "new": "New fact"}}
    before = source.read_bytes()
    assert ap.apply_file_patches(tmp_path, "data/custom.json", patches) == {"s1": ap.OUT_NEEDS_HUMAN}
    assert source.read_bytes() == before


def test_missing_structural_bid_abandons_whole_markdown_write(tmp_path):
    source = write(tmp_path, "data/a.md", "Original paragraph.\n")
    p = patch(rel="data/a.md", path="missing", kind="structural_md", op="delete")
    assert ap.apply_file_patches(tmp_path, p.relpath, [p]) == {"s1": ap.OUT_NEEDS_HUMAN}
    assert source.read_text() == "Original paragraph.\n"


def test_structural_json_non_text_body_is_held_for_human(tmp_path):
    source = write(tmp_path, "data/custom.json", '{"body": null}\n')
    p = patch(rel="data/custom.json", path="body", kind="structural_json_body", op="delete")
    assert ap.apply_file_patches(tmp_path, p.relpath, [p]) == {"s1": ap.OUT_NEEDS_HUMAN}
    assert source.read_text() == '{"body": null}\n'


def test_value_sync_scope_is_bounded_and_includes_firm_book(tmp_path):
    allowed = [write(tmp_path, name, "content") for name in (
        "data/matters/m03/facts.md", "data/matters/m03-extra/nested/file.md", "data/firm/firm.json")]
    for name in ("m30", "m3", "m030", "m04"):
        write(tmp_path, "data/matters/" + name + "/facts.md", "excluded")
    assert set(ap.matter_scope_files(tmp_path, "m03")) == {str(p) for p in allowed}
    assert ap._money_equal("$1,234.00", "$1234.01")
    assert not ap._money_equal("invalid", "$1234")


def test_generated_map_flattening_retains_shared_occurrences_without_mutating_bundle():
    ref = "data/a.md#baaaaaaaa"
    block = {"source_ref": ref, "original_text": "Shared paragraph"}
    occurrences = [{"page": "first.html"}, {"page": "second.html"}]
    bundle = {"pages": {"first.html": [block], "second.html": [block]},
              "occurrences": {ref: occurrences}}
    indexed = ap.index_map(bundle)
    assert indexed[ref] == {**block, "page": "second.html", "occurrences": occurrences}
    assert "page" not in block
    assert ap.index_map({}) == {}
    assert ap.index_map({"pages": {"first.html": [block]}})[ref]["occurrences"] == []


def test_real_subprocess_pipeline_reports_missing_tools_and_map(tmp_path):
    pipeline = ap.SubprocessPipeline()
    with pytest.raises(ap.ApplyError, match="map regeneration produced no editor map") as error:
        pipeline.regenerate_map(tmp_path)
    assert "build_site.py" in str(error.value)
    ok, info = pipeline.validate(tmp_path)
    assert not ok and info["report"] is None and "validate_spine.py" in info["stdout"]
    ok, info = pipeline.build(tmp_path)
    assert not ok and info["step"] == "build_site --check" and "build_site.py" in info["stdout"]
    ok, info = pipeline.parity(tmp_path)
    assert not ok and "check_build_parity.py" in info["stdout"]
