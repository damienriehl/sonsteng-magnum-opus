"""Staleness gate tested against content hashes and real build artifacts."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_build_parity as parity
import spine_stamp


@pytest.fixture
def tree(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "spine-manifest.json").write_text('{"spine_version":"1"}')
    artifacts = [(label, str(tmp_path / (str(i) + ".json")))
                 for i, (label, _) in enumerate(parity.ARTIFACTS)]
    monkeypatch.setattr(parity, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(parity, "DATA_DIR", str(data))
    monkeypatch.setattr(parity, "ARTIFACTS", artifacts)
    truth = spine_stamp.compute(data)
    for _, path in artifacts:
        Path(path).write_text(json.dumps({"spine_build_id": truth, "git_base_sha": "trace-only"}))
    return data, artifacts


def test_real_chain_passes_then_rejects_changed_spine_and_accepts_rebuild(tree, capsys):
    data, artifacts = tree
    assert parity.main() == 0
    assert capsys.readouterr().out.count("OK      ") == 4
    (data / "new.md").write_text("authored addition")
    assert parity.main() == 1
    failure = capsys.readouterr().out
    assert failure.count("MISMATCH") == 4
    assert "PARITY: FAIL" in failure
    for _, path in artifacts:
        Path(path).write_text(json.dumps({"spine_build_id": spine_stamp.compute(data)}))
    assert parity.main() == 0
    assert "PARITY: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("failure", ["missing", "invalid", "absent-id", "stale", "null", "empty", "directory"])
def test_each_artifact_is_required_and_other_artifacts_still_checked(tree, capsys, index, failure):
    _, artifacts = tree
    path = Path(artifacts[index][1])
    if failure in {"missing", "directory"}:
        path.unlink()
        if failure == "directory":
            path.mkdir()
    else:
        path.write_text({"invalid": "{bad", "absent-id": "{}", "stale": '{"spine_build_id":"old"}',
                         "null": '{"spine_build_id":null}', "empty": '{"spine_build_id":""}'}[failure])
    assert parity.main() == 1
    output = capsys.readouterr().out
    assert output.count("MISMATCH") == 1
    assert output.count("OK      ") == 3
    assert artifacts[index][0] in output
    assert "PARITY: FAIL" in output
    if failure in {"invalid", "directory"}:
        assert "unreadable:" in output
    if failure == "missing":
        assert "missing (run its generator)" in output
    if failure in {"absent-id", "null", "empty"}:
        assert "(none)" in output
