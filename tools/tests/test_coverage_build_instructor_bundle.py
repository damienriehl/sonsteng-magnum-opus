"""Instructor-only build safety and real markdown/editor-map integration."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_instructor_bundle as bundle
import build_site as bs
import spine_stamp
import text_norm


@pytest.fixture
def repo(tmp_path, monkeypatch):
    data = tmp_path / "data"
    matters = data / "matters"
    matters.mkdir(parents=True)
    (data / "spine-manifest.json").write_text('{"spine_version":"test"}')
    monkeypatch.setattr(bundle, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(bundle, "DATA_DIR", str(data))
    monkeypatch.setattr(bundle, "MATTERS_DIR", str(matters))
    monkeypatch.setattr(bundle, "SITE_PLATFORM", str(tmp_path / "site" / "platform"))
    monkeypatch.setattr(bundle, "OUT_PATH", str(tmp_path / "build" / "instructor-bundle.generated.json"))
    monkeypatch.setattr(bs, "ROOT", str(tmp_path))
    monkeypatch.setattr(bs, "EDMAP", bs._EditorMap())
    # Traceability is unrelated to the renderer and hash chain under test.
    monkeypatch.setattr(spine_stamp, "git_base_sha", lambda: None)
    return tmp_path


def matter(repo, slug="m02-example", identifier="m02", docs=None):
    path = repo / "data" / "matters" / slug
    path.mkdir()
    (path / "matter.json").write_text(json.dumps({"id": identifier}))
    for name, content in (docs or {}).items():
        target = path / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(content)
    return path


def output():
    return json.loads(Path(bundle.OUT_PATH).read_text())


def test_build_real_instructor_documents_have_local_blocks_and_matching_stamp(repo, capsys):
    matter(repo, "m02-example", "canonical", {
        "facts.md": "# Facts {#b:aaaabbbb}\n\nSecret **fact**. {#b:ccccdddd}\n",
        "exercise/instructor-notes.md": "Notes <script>danger</script>. {#b:11112222}\n",
        "exercise/answer-key.md": "Answer café. {#b:33334444}\n"})
    matter(repo, "m01-earlier", "m01", {"facts.md": "Earlier fact. {#b:55556666}\n"})
    (repo / "data" / "matters" / "m00-not-a-directory").write_text("ignored")
    (repo / "data" / "matters" / "other").mkdir()
    assert bundle.main() == 0
    result = output()
    assert result["spine_build_id"] == spine_stamp.compute(repo / "data")
    assert result["git_base_sha"] is None
    docs = result["docs"]
    assert [(d["matter_id"], d["doc_type"]) for d in docs] == [
        ("m01", "facts"), ("canonical", "facts"), ("canonical", "instructor_notes"), ("canonical", "answer_key")]
    assert "<strong>fact</strong>" in docs[1]["html"]
    assert "&lt;script&gt;" in docs[2]["html"]
    assert [len(d["blocks"]) for d in docs] == [1, 2, 1, 1]
    for doc in docs:
        assert doc["source_ref"].startswith("data/matters/")
        assert all(block["source_ref"].startswith(doc["source_ref"] + "#") for block in doc["blocks"])
        assert [b["index"] for b in doc["blocks"]] == list(range(len(doc["blocks"])))
    assert docs[3]["blocks"][0]["original_hash"] == text_norm.norm_hash("Answer café.")
    log = capsys.readouterr().out
    assert "missing    : 2" in log and "m01/answer_key" in log
    first = Path(bundle.OUT_PATH).read_bytes()
    assert bundle.main() == 0
    assert Path(bundle.OUT_PATH).read_bytes() == first
    assert not (repo / "site").exists()


def test_empty_tree_builds_valid_empty_bundle(repo, capsys):
    assert bundle.main() == 0
    assert output()["docs"] == []
    assert "docs       : 0" in capsys.readouterr().out


def test_empty_file_is_present_while_missing_files_are_reported(repo, capsys):
    matter(repo, docs={"facts.md": ""})
    assert bundle.main() == 0
    assert len(output()["docs"]) == 1
    assert output()["docs"][0]["html"] == ""
    assert output()["docs"][0]["blocks"] == []
    assert "missing    : 2" in capsys.readouterr().out


@pytest.mark.parametrize("metadata", [None, "broken JSON", "{}", '{"id":""}', '{"id":null}'])
def test_matter_identifier_falls_back_to_slug_prefix(repo, metadata):
    path = matter(repo, slug="m09-multi-word-name")
    if metadata is None:
        (path / "matter.json").unlink()
    else:
        (path / "matter.json").write_text(metadata)
    assert bundle._matter_id(str(path)) == "m09"


@pytest.mark.parametrize("suffix", ["bundle.json", "nested/bundle.json", "../platform/bundle.json"])
def test_public_output_guard_refuses_before_reading_or_writing(repo, monkeypatch, capsys, suffix):
    target = repo / "site" / "platform" / suffix
    monkeypatch.setattr(bundle, "OUT_PATH", str(target))
    assert bundle.main() == 2
    assert not target.exists()
    assert "refusing to write answer-key content" in capsys.readouterr().err
    assert not (repo / "build").exists()


@pytest.mark.parametrize("leaked", [True, False])
def test_persona_leak_check_compares_real_rendered_content(repo, capsys, leaked):
    path = matter(repo, docs={"exercise/answer-key.md": "Instructor-only solution.\n"})
    html, _ = bundle._render_doc(str(path), "exercise/answer-key.md")
    persona = repo / "app" / "worker" / "personas" / "personas.generated.json"
    persona.parent.mkdir(parents=True)
    persona.write_text(json.dumps({"text": text_norm.normalize(html) if leaked else "Student-safe description"}))
    assert bundle.main() == (2 if leaked else 0)
    log = capsys.readouterr()
    if leaked:
        assert "FATAL: instructor content leaked" in log.err
        assert "m02/answer_key" in log.err
    else:
        assert "self-check" in log.out


def test_empty_html_does_not_trigger_false_positive_persona_leak(repo):
    matter(repo, docs={"facts.md": ""})
    persona = repo / "app" / "worker" / "personas" / "personas.generated.json"
    persona.parent.mkdir(parents=True)
    persona.write_text("{}")
    assert bundle.main() == 0


def test_missing_render_input_is_distinct_from_empty_markdown(repo):
    assert bundle._render_doc(str(repo), "absent.md") == (None, [])


@pytest.mark.parametrize("content", ["Secret solution. {#b:aaaabbbb}\n", "Café naïve solution. {#b:aaaabbbb}\n"])
def test_persona_leak_detects_json_escaped_marked_html(repo, content, capsys):
    path = matter(repo, docs={"exercise/answer-key.md": content})
    html, _ = bundle._render_doc(str(path), "exercise/answer-key.md")
    persona = repo / "app/worker/personas/personas.generated.json"
    persona.parent.mkdir(parents=True)
    persona.write_text(json.dumps({"nested": [{"text": html}]}))
    assert bundle.main() == 2
    assert "instructor content leaked" in capsys.readouterr().err
    assert not Path(bundle.OUT_PATH).exists()


@pytest.mark.parametrize("location", ["build-public", "elsewhere", "symlink-directory", "symlink-file"])
def test_output_must_resolve_inside_private_build_before_write(repo, monkeypatch, location):
    public = repo / "site/platform"
    public.mkdir(parents=True)
    build = repo / "build"
    build.mkdir()
    if location == "symlink-directory":
        (build / "redirect").symlink_to(public, target_is_directory=True)
        target = build / "redirect/bundle.json"
    elif location == "symlink-file":
        target = build / "bundle.json"
        target.symlink_to(public / "bundle.json")
    else:
        target = repo / location / "bundle.json"
    monkeypatch.setattr(bundle, "OUT_PATH", str(target))
    assert bundle.main() == 2
    assert not target.exists()
    assert not (public / "bundle.json").exists()


@pytest.mark.parametrize("contents", [b"{broken", b"\xff"])
def test_unreadable_persona_bundle_fails_closed_before_output(repo, contents, capsys):
    matter(repo, docs={"facts.md": "Confidential fact."})
    persona = repo / "app/worker/personas/personas.generated.json"
    persona.parent.mkdir(parents=True)
    persona.write_bytes(contents)
    assert bundle.main() == 2
    assert "cannot check personas bundle" in capsys.readouterr().err
    assert not Path(bundle.OUT_PATH).exists()


def test_private_nested_output_and_unrelated_nested_persona_values_are_allowed(repo, monkeypatch):
    matter(repo, docs={"facts.md": "Confidential fact. {#b:aaaabbbb}"})
    persona = repo / "app/worker/personas/personas.generated.json"
    persona.parent.mkdir(parents=True)
    persona.write_text(json.dumps({"nested": [None, 7, True, {"text": "Public text"}]}))
    target = repo / "build/nested/instructor.json"
    monkeypatch.setattr(bundle, "OUT_PATH", str(target))
    assert bundle.main() == 0
    assert json.loads(target.read_text())["docs"][0]["matter_id"] == "m02"
