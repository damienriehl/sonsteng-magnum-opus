"""Student archive boundary and transformed-payload integration tests."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import student_archives as sa


def matter_tree(tmp_path, files=None):
    root = tmp_path / "matter"
    (root / "exercise").mkdir(parents=True)
    (root / "matter.json").write_text('{"id":"example"}\n')
    (root / "rubric.json").write_text('{}\n')
    (root / "exercise/exercise.json").write_text(json.dumps(
        {"sections": {"case_file": {"files": files}}}))
    return root


def test_transformed_archive_records_actual_hashes_and_keeps_manifest_private(tmp_path):
    root = matter_tree(tmp_path, ["case-file/exhibit.txt", "case-file/exhibit.txt",
                                  "facts.md", "exercise/answer-key.md"])
    (root / "case-file").mkdir()
    (root / "case-file/exhibit.txt").write_bytes(b"original exhibit\n")
    (root / "business").mkdir()
    for name in sa.OPTIONAL:
        (root / name).write_text("{}")
    manifest = sa.student_material_manifest(root, "example")
    before = json.loads(json.dumps(manifest))
    destination = tmp_path / "nested/student.zip"
    result = sa.write_student_archive(manifest, destination,
                                     lambda name, data: data.replace(b"original", b"edited"))
    assert result == destination
    assert manifest == before
    assert [r["path"] for r in sa.public_data_members(manifest)] == sorted(sa.REQUIRED)
    assert [r["path"] for r in sa.learner_exhibit_members(manifest)] == ["case-file/exhibit.txt"]
    with zipfile.ZipFile(destination) as archive:
        public = json.loads(archive.read("manifest.json"))
        assert "root" not in public
        assert public["missing_optional"] == []
        assert public["excluded_authored"] == ["exercise/answer-key.md", "facts.md"]
        assert len(public["members"]) == 6
        for record in public["members"]:
            assert record["sha256"] == hashlib.sha256(archive.read(record["path"])).hexdigest()
            assert record["required"] == (record["path"] not in sa.OPTIONAL)
        assert archive.read("case-file/exhibit.txt") == b"edited exhibit\n"
        assert all(info.external_attr >> 16 == 0o100644 for info in archive.infolist())


@pytest.mark.parametrize("sections", [None, {}, {"case_file": None}, {"case_file": {"files": []}}])
def test_empty_exhibit_states_still_make_complete_required_archive(tmp_path, sections):
    root = matter_tree(tmp_path)
    (root / "exercise/exercise.json").write_text(json.dumps({"sections": sections}))
    manifest = sa.student_material_manifest(root, "example")
    assert sa.learner_exhibit_members(manifest) == []
    assert len(sa.public_data_members(manifest)) == 3
    with zipfile.ZipFile(sa.write_student_archive(manifest, tmp_path / "empty.zip")) as archive:
        assert set(archive.namelist()) == set(sa.REQUIRED) | {"manifest.json"}


@pytest.mark.parametrize("member,error", [
    ("/case-file/exhibit.md", "unsafe"), ("", "unsafe"),
    ("private.md", "allowlist"), ("case-file/missing.md", "missing required learner"),
    ("case-file/directory", "missing required learner"),
])
def test_authored_exhibits_fail_closed(tmp_path, member, error):
    root = matter_tree(tmp_path, [member])
    (root / "case-file/directory").mkdir(parents=True)
    with pytest.raises(sa.StudentArchiveError, match=error):
        sa.student_material_manifest(root, "example")


def test_malformed_exercise_is_not_silently_packaged(tmp_path):
    root = matter_tree(tmp_path)
    (root / "exercise/exercise.json").write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        sa.student_material_manifest(root, "example")


def test_archive_rechecks_members_after_manifest_creation(tmp_path):
    root = matter_tree(tmp_path, ["case-file/exhibit.txt"])
    (root / "case-file").mkdir()
    exhibit = root / "case-file/exhibit.txt"
    exhibit.write_text("safe")
    manifest = sa.student_material_manifest(root, "example")
    exhibit.unlink()
    exhibit.symlink_to(root / "matter.json")
    destination = tmp_path / "student.zip"
    with pytest.raises(sa.StudentArchiveError, match="symlinks"):
        sa.write_student_archive(manifest, destination)
    assert not destination.exists()


def test_transform_failure_does_not_replace_existing_archive(tmp_path):
    root = matter_tree(tmp_path)
    manifest = sa.student_material_manifest(root, "example")
    destination = sa.write_student_archive(manifest, tmp_path / "student.zip")
    original = destination.read_bytes()

    def reject(name, data):
        raise ValueError("transformation failed")

    with pytest.raises(ValueError, match="transformation failed"):
        sa.write_student_archive(manifest, destination, reject)
    assert destination.read_bytes() == original


@pytest.mark.parametrize("member", ["business/business.json", "business/engagement-letter.md"])
def test_optional_material_symlinks_are_rejected_even_without_authored_reference(tmp_path, member):
    root = matter_tree(tmp_path)
    (root / "business").mkdir()
    (root / member).symlink_to(root / "matter.json")
    with pytest.raises(sa.StudentArchiveError, match="symlinks"):
        sa.student_material_manifest(root, "example")


def test_removed_member_prevents_archive_creation(tmp_path):
    root = matter_tree(tmp_path)
    manifest = sa.student_material_manifest(root, "example")
    (root / "rubric.json").unlink()
    destination = tmp_path / "student.zip"
    with pytest.raises(FileNotFoundError):
        sa.write_student_archive(manifest, destination)
    assert not destination.exists()
