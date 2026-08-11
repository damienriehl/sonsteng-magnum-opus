import copy
import json
import pathlib
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from build_legacy_review_reconciliation import build_reconciliation
from build_prod_review_backfill import BackfillError


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def legacy_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    effective_path = repo / "data/copy/effective.json"
    reverted_path = repo / "data/copy/reverted.json"
    write_json(effective_path, {"lead": "Old effective"})
    write_json(reverted_path, {"lead": "Old reverted"})
    subprocess.run(["git", "add", "data"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    prod_base = git(repo, "rev-parse", "HEAD")

    write_json(effective_path, {"lead": "New effective"})
    subprocess.run(["git", "add", "data"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "apply: batch b-effective one edit"], cwd=repo, check=True)
    effective_commit = git(repo, "rev-parse", "HEAD")

    write_json(reverted_path, {"lead": "New reverted"})
    subprocess.run(["git", "add", "data"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "apply: batch b-reverted one edit"], cwd=repo, check=True)
    reverted_commit = git(repo, "rev-parse", "HEAD")

    write_json(reverted_path, {"lead": "Old reverted"})
    subprocess.run(["git", "add", "data"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "revert test edit"], cwd=repo, check=True)

    evidence = {
        "suggestions": [
            {"id": "effective", "editor": "JOS", "kind": "json_scalar",
             "source_ref": "data/copy/effective.json#lead", "original_text": "Old effective",
             "new_text": "New effective", "apply_batch_id": "b-effective", "created_at": 1},
            {"id": "reverted", "editor": "JOS", "kind": "json_scalar",
             "source_ref": "data/copy/reverted.json#lead", "original_text": "Old reverted",
             "new_text": "New reverted", "apply_batch_id": "b-reverted", "created_at": 2},
        ],
        "batches": [
            {"batch_id": "b-effective", "base_sha": prod_base,
             "commit_sha": effective_commit, "phase": "done"},
            {"batch_id": "b-reverted", "base_sha": effective_commit,
             "commit_sha": reverted_commit, "phase": "done"},
        ],
    }
    classification = {
        "effective_ids": ["effective"],
        "exclusions": [{"suggestion_id": "reverted", "apply_commit": reverted_commit,
                        "proof_base_sha": effective_commit}],
    }
    return repo, prod_base, evidence, classification


def test_builds_deterministic_effective_revision_and_reverted_exclusion(legacy_repo):
    repo, prod_base, evidence, classification = legacy_repo
    first = build_reconciliation(evidence, classification, repo, "legacy-1", prod_base)
    second = build_reconciliation(evidence, classification, repo, "legacy-1", prod_base)
    assert first == second
    assert first["migration_id"] == "legacy-1"
    assert first["exclusions"][0]["suggestion_id"] == "reverted"
    assert first["exclusions"][0]["reason"] == "reverted_legacy_uat"
    assert first["exclusions"][0]["apply_base"] == evidence["batches"][1]["base_sha"]
    assert len(first["exclusions"][0]["evidence_hash"]) == 64
    assert first["revisions"][0]["suggestion_ids"] == ["effective"]
    assert first["revisions"][0]["source_revision"] == evidence["batches"][0]["commit_sha"]
    assert first["revisions"][0]["source_original_text"] == "Old effective"
    assert first["revisions"][0]["source_proposed_text"] == "New effective"
    assert first["revisions"][0]["suggestion_evidence"] == [{
        "suggestion_id": "effective", "batch_id": "b-effective",
        "base_sha": evidence["batches"][0]["base_sha"],
        "commit_sha": evidence["batches"][0]["commit_sha"],
    }]


def test_rejects_incomplete_classification_and_wrong_apply_commit(legacy_repo):
    repo, prod_base, evidence, classification = legacy_repo
    incomplete = copy.deepcopy(classification)
    incomplete["exclusions"] = []
    with pytest.raises(BackfillError, match="cover every legacy suggestion"):
        build_reconciliation(evidence, incomplete, repo, "legacy-1", prod_base)
    wrong = copy.deepcopy(classification)
    wrong["exclusions"][0]["apply_commit"] = evidence["batches"][0]["commit_sha"]
    with pytest.raises(BackfillError, match="apply commit"):
        build_reconciliation(evidence, wrong, repo, "legacy-1", prod_base)
    duplicate = copy.deepcopy(evidence)
    duplicate["suggestions"].append(copy.deepcopy(duplicate["suggestions"][0]))
    with pytest.raises(BackfillError, match="identities must be unique"):
        build_reconciliation(duplicate, classification, repo, "legacy-1", prod_base)


def test_rejects_reverted_or_effective_source_drift(legacy_repo):
    repo, prod_base, evidence, classification = legacy_repo
    reverted_path = repo / "data/copy/reverted.json"
    write_json(reverted_path, {"lead": "Drifted"})
    subprocess.run(["git", "add", "data"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "drift reverted source"], cwd=repo, check=True)
    with pytest.raises(BackfillError, match="not restored"):
        build_reconciliation(evidence, classification, repo, "legacy-1", prod_base)

    subprocess.run(["git", "reset", "--hard", "HEAD^"], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL)
    effective_path = repo / "data/copy/effective.json"
    write_json(effective_path, {"lead": "Different effective text"})
    subprocess.run(["git", "add", "data"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "drift effective source"], cwd=repo, check=True)
    with pytest.raises(BackfillError, match="does not match PROD and current source"):
        build_reconciliation(evidence, classification, repo, "legacy-1", prod_base)


def test_rejects_correctly_named_commit_that_did_not_apply_target_edit(legacy_repo):
    repo, prod_base, evidence, classification = legacy_repo
    original_head = git(repo, "rev-parse", "HEAD")

    subprocess.run(["git", "checkout", "-q", "--detach", prod_base], cwd=repo, check=True)
    write_json(repo / "data/copy/unrelated.json", {"lead": "Unrelated"})
    subprocess.run(["git", "add", "data/copy/unrelated.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "apply: batch b-effective unrelated"], cwd=repo, check=True)
    fake_effective = git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "checkout", "-q", "--detach", original_head], cwd=repo, check=True)
    forged = copy.deepcopy(evidence)
    forged["batches"][0]["commit_sha"] = fake_effective
    with pytest.raises(BackfillError, match="effective suggestion lacks exact apply commit"):
        build_reconciliation(forged, classification, repo, "legacy-1", prod_base)

    effective_commit = evidence["batches"][0]["commit_sha"]
    subprocess.run(["git", "checkout", "-q", "--detach", effective_commit], cwd=repo, check=True)
    write_json(repo / "data/copy/unrelated-2.json", {"lead": "Still unrelated"})
    subprocess.run(["git", "add", "data/copy/unrelated-2.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "apply: batch b-reverted unrelated"], cwd=repo, check=True)
    fake_reverted = git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "checkout", "-q", "--detach", original_head], cwd=repo, check=True)
    forged = copy.deepcopy(classification)
    forged["exclusions"][0]["apply_commit"] = fake_reverted
    with pytest.raises(BackfillError, match="exclusion apply commit"):
        build_reconciliation(evidence, forged, repo, "legacy-1", prod_base)

def test_ignores_uncommitted_worktree_content(legacy_repo):
    repo, prod_base, evidence, classification = legacy_repo
    write_json(repo / "data/copy/reverted.json", {"lead": "Uncommitted drift"})
    payload = build_reconciliation(evidence, classification, repo, "legacy-1", prod_base)
    expected_blob = subprocess.check_output(
        ["git", "show", "HEAD:data/copy/reverted.json"], cwd=repo)
    proof = {"suggestion_id": "reverted", "apply_batch_id": "b-reverted",
             "apply_commit": classification["exclusions"][0]["apply_commit"],
             "proof_base_sha": classification["exclusions"][0]["proof_base_sha"],
             "current_blob": __import__("hashlib").sha256(expected_blob).hexdigest()}
    from build_prod_review_backfill import _canonical
    expected_hash = __import__("hashlib").sha256(_canonical(proof).encode()).hexdigest()
    assert payload["exclusions"][0]["evidence_hash"] == expected_hash
