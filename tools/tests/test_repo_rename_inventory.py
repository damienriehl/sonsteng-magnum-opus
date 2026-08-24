from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import repo_rename_inventory as inventory  # noqa: E402


OWNER = "example-owner"
CURRENT = "current-repo"
TARGET = "target-repo"
EMPTY_RUNTIME = {
    "remote_names_requiring_review": [],
    "worktree_path_digests_requiring_review": [],
}


def write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build(repo: Path, paths: list[str], runtime=EMPTY_RUNTIME):
    return inventory.build_inventory(
        repo,
        owner=OWNER,
        current=CURRENT,
        target=TARGET,
        tracked_paths=paths,
        runtime_state=runtime,
    )


def test_known_active_references_patch_and_history_stays_preserved(tmp_path):
    files = {
        "README.md": "git clone https://github.com/example-owner/current-repo\n",
        "tools/build_site.py": 'URL = "https://github.com/example-owner/current-repo"\n',
        "tools/install-daemon.sh": "Documentation=https://github.com/example-owner/current-repo\n",
        "tools/tests/test_contract.py": 'assert "current-repo"\n',
        "docs/plans/old-plan.md": "Target repo: current-repo\n",
        "docs/decisions/old.md": "The repository was current-repo.\n",
    }
    for path, text in files.items():
        write(tmp_path, path, text)
    report = build(tmp_path, list(files))
    categories = {row["path"]: row["classification"] for row in report["references"]}
    assert categories == {
        "README.md": "clone_or_web_url_patch",
        "docs/decisions/old.md": "historical_evidence_preserve",
        "docs/plans/old-plan.md": "historical_evidence_preserve",
        "tools/build_site.py": "generated_url_patch",
        "tools/install-daemon.sh": "local_installer_template_patch",
        "tools/tests/test_contract.py": "active_contract_test_patch",
    }
    assert report["activation_status"] == "prepared_not_activated"


def test_hosted_actions_consumers_are_not_treated_as_redirectable_urls(tmp_path):
    path = ".github/workflows/use.yml"
    write(tmp_path, path, "steps:\n  - uses: example-owner/current-repo/action@v1\n")
    report = build(tmp_path, [path])
    assert report["references"][0]["classification"] == "hosted_actions_consumer_patch"


def test_unclassified_current_name_reference_fails(tmp_path):
    path = "misc/config.toml"
    write(tmp_path, path, 'repo = "current-repo"\n')
    with pytest.raises(inventory.InventoryError, match="misc/config.toml:1"):
        build(tmp_path, [path])


def test_manifest_contains_every_controlled_transition_step(tmp_path):
    report = build(tmp_path, [])
    assert report["transition_steps"] == [
        "confirm-quiet-window",
        "confirm-no-active-release",
        "rename-external-repository",
        "patch-active-references",
        "repair-remotes",
        "repair-worktrees",
        "repair-systemd-units",
        "verify-daemons",
        "verify-clone-and-web-redirects",
        "verify-hosted-actions-consumers",
    ]


def test_runtime_inventory_exposes_remote_names_but_digests_worktree_paths(tmp_path):
    runtime = {
        "remote_names_requiring_review": ["upstream", "origin", "origin"],
        "worktree_path_digests_requiring_review": ["sha256:bbb", "sha256:aaa", "sha256:aaa"],
    }
    report = build(tmp_path, [], runtime)
    assert report["runtime"] == {
        "remote_names_requiring_review": ["origin", "upstream"],
        "worktree_path_digests_requiring_review": ["sha256:aaa", "sha256:bbb"],
    }


def test_manifest_is_deterministic_and_contains_no_source_text(tmp_path):
    files = ["README.md", "docs/handoffs/old.md"]
    write(tmp_path, files[0], "https://github.com/example-owner/current-repo\n")
    write(tmp_path, files[1], "private prose about current-repo\n")
    first = build(tmp_path, list(reversed(files)))
    second = build(tmp_path, files)
    assert first == second
    serialized = json.dumps(first)
    assert "private prose" not in serialized
    assert "https://" not in serialized


def test_invalid_parameters_fail_before_scanning(tmp_path):
    with pytest.raises(inventory.InventoryError):
        inventory.build_inventory(
            tmp_path, owner="bad/owner", current=CURRENT, target=TARGET,
            tracked_paths=[], runtime_state=EMPTY_RUNTIME,
        )
    with pytest.raises(inventory.InventoryError, match="must differ"):
        inventory.build_inventory(
            tmp_path, owner=OWNER, current=CURRENT, target=CURRENT,
            tracked_paths=[], runtime_state=EMPTY_RUNTIME,
        )


def test_cli_is_read_only_and_does_not_call_github(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, "README.md", "https://github.com/example-owner/current-repo\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    before = (tmp_path / "README.md").read_bytes()
    result = subprocess.run(
        [sys.executable, str(TOOLS / "repo_rename_inventory.py"), "--repo", str(tmp_path),
         "--owner", OWNER, "--current", CURRENT, "--target", TARGET],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["repository"]["target"] == TARGET
    assert (tmp_path / "README.md").read_bytes() == before
    assert "target-repo" not in (tmp_path / "README.md").read_text()


def test_live_repository_inventory_classifies_every_tracked_reference():
    repo = TOOLS.parent
    report = inventory.build_inventory(
        repo,
        owner="damienriehl",
        current="sonsteng-magnum-opus",
        target="legal-practicum",
    )
    assert report["references"]
    assert all(row["classification"] for row in report["references"])
