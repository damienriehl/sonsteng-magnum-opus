"""Content-fingerprint invariants exercised on real temporary data trees."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spine_stamp as stamp


def test_manifest_and_binary_tree_follow_published_fingerprint(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "spine-manifest.json").write_text(json.dumps({"spine_version": "2.α"}))
    (data / "nested").mkdir()
    payload = bytes(range(256)) * 600
    (data / "nested" / "binary.dat").write_bytes(payload)
    (data / "empty").write_bytes(b"")
    names = ["empty", "nested/binary.dat", "spine-manifest.json"]
    lines = [name + ":" + hashlib.sha256((data / name).read_bytes()).hexdigest() for name in names]
    expected = hashlib.sha256(("2.α\n" + "\n".join(lines)).encode()).hexdigest()
    assert stamp.compute(data) == stamp.spine_build_id(data) == expected
    clone = tmp_path / "clone"
    shutil.copytree(data, clone)
    os.utime(clone / "empty", (1, 1))
    assert stamp.compute(clone) == expected
    (clone / "nested" / "binary.dat").write_bytes(payload + b"changed")
    assert stamp.compute(clone) != expected


@pytest.mark.parametrize("content", [None, "{broken", "{}", '{"other": "value"}'])
def test_missing_invalid_or_unversioned_manifest_uses_empty_version(tmp_path, content):
    if content is not None:
        (tmp_path / "spine-manifest.json").write_text(content)
    assert stamp._spine_version(tmp_path) == ""
    expected_lines = [] if content is None else ["spine-manifest.json:" + hashlib.sha256(content.encode()).hexdigest()]
    assert stamp.compute(tmp_path) == hashlib.sha256(("\n" + "\n".join(expected_lines)).encode()).hexdigest()


def test_cruft_ignored_but_hidden_authored_files_and_renames_change_stamp(tmp_path):
    (tmp_path / "keep.txt").write_text("same")
    original = stamp.compute(tmp_path)
    for directory in [".git", "__pycache__", ".ipynb_checkpoints"]:
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "ignored").write_text("ignored")
    for name in [".DS_Store", "module.pyc"]:
        (tmp_path / name).write_text("ignored")
    (tmp_path / "broken-link").symlink_to(tmp_path / "absent")
    assert list(name for name, _ in stamp._iter_data_files(tmp_path)) == ["keep.txt"]
    assert stamp.compute(tmp_path) == original
    (tmp_path / "keep.txt").rename(tmp_path / "renamed.txt")
    renamed = stamp.compute(tmp_path)
    assert renamed != original
    (tmp_path / ".authored").write_text("hidden content")
    assert stamp.compute(tmp_path) != renamed


def test_nonexistent_data_directory_has_empty_fingerprint(tmp_path):
    assert stamp.compute(tmp_path / "absent") == hashlib.sha256(b"\n").hexdigest()


@pytest.mark.parametrize("output,expected", [("abc123\n", "abc123"), (" \n", None)])
def test_traceability_normalizes_command_output_without_entering_hash(monkeypatch, tmp_path, output, expected):
    import subprocess
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=output)
    monkeypatch.setattr(stamp.subprocess, "run", run)
    before = stamp.compute(tmp_path)
    assert stamp.git_base_sha(str(tmp_path)) == expected
    assert calls[0][0] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]
    assert calls[0][1]["timeout"] == 10
    assert stamp.compute(tmp_path) == before


@pytest.mark.parametrize("kind", ["missing", "failed", "timeout"])
def test_traceability_command_failures_are_optional(monkeypatch, kind):
    import subprocess
    error = {"missing": FileNotFoundError("git"), "failed": subprocess.CalledProcessError(128, "git"),
             "timeout": subprocess.TimeoutExpired("git", 10)}[kind]
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(stamp.subprocess, "run", fail)
    assert stamp.git_base_sha("unused") is None
