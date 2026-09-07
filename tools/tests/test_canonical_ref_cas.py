"""Integration tests for the bounded Day Zero canonical-ref CAS."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools" / "canonical_ref_cas.py"
sys.path.insert(0, str(CLI.parent))
import canonical_ref_cas as cas


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def sha(repo: Path, ref: str = "HEAD") -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def remote_sha(repo: Path) -> str:
    line = git(repo, "ls-remote", "--refs", "origin", "refs/heads/main").stdout.strip()
    return line.split()[0]


@dataclass(frozen=True)
class Repositories:
    remote: Path
    seed: Path
    daemon: Path
    prior: str
    candidate: str


@pytest.fixture
def repositories(tmp_path: Path) -> Repositories:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    daemon = tmp_path / "daemon"

    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    git(seed, "config", "user.name", "CAS Test")
    git(seed, "config", "user.email", "cas@example.invalid")
    (seed / "base.txt").write_text("ancestor\n", encoding="utf-8")
    git(seed, "add", "base.txt")
    git(seed, "commit", "-q", "-m", "ancestor")
    (seed / "state.txt").write_text("prior\n", encoding="utf-8")
    git(seed, "add", "state.txt")
    git(seed, "commit", "-q", "-m", "prior")
    prior = sha(seed)
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

    git(tmp_path, "clone", "-q", "--branch", "main", str(remote), str(daemon))
    (seed / "state.txt").write_text("candidate\n", encoding="utf-8")
    git(seed, "commit", "-q", "-am", "candidate")
    candidate = sha(seed)
    git(daemon, "fetch", "-q", str(seed), candidate)

    return Repositories(remote, seed, daemon, prior, candidate)


def invoke(
    repositories: Repositories,
    verb: str,
    *,
    dry_run: bool = False,
    from_sha: str | None = None,
    to_sha: str | None = None,
):
    command = [
        sys.executable,
        str(CLI),
        verb,
        "--repo",
        str(repositories.daemon),
        "--remote",
        "origin",
        "--branch",
        "main",
        "--from",
        from_sha
        or (repositories.prior if verb == "forward" else repositories.candidate),
        "--to",
        to_sha
        or (repositories.candidate if verb == "forward" else repositories.prior),
    ]
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def receipt(completed: subprocess.CompletedProcess[str]) -> dict:
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    return json.loads(stream)


def advance_remote(repositories: Repositories) -> str:
    writer = repositories.remote.parent / "writer"
    git(writer.parent, "clone", "-q", "--branch", "main", str(repositories.remote), str(writer))
    git(writer, "config", "user.name", "CAS Interloper")
    git(writer, "config", "user.email", "interloper@example.invalid")
    (writer / "other.txt").write_text("other\n", encoding="utf-8")
    git(writer, "add", "other.txt")
    git(writer, "commit", "-q", "-m", "remote moved")
    moved = sha(writer)
    git(writer, "push", "-q", "origin", "main")
    return moved


def test_happy_forward(repositories: Repositories):
    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    assert receipt(completed) == {
        "dry_run": False,
        "expected": {"from": repositories.prior, "to": repositories.candidate},
        "mutations": ["local-main-fast-forward", "remote-main-cas"],
        "readback": {
            "head": repositories.candidate,
            "local": repositories.candidate,
            "remote": repositories.candidate,
        },
        "result": "success",
        "verb": "forward",
    }
    assert sha(repositories.daemon, "refs/heads/main") == repositories.candidate
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_forward_refused_when_remote_moved(repositories: Repositories):
    moved = advance_remote(repositories)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == "remote main does not equal --from"
    assert failure["mutations"] == []
    assert failure["readback"] == {
        "head": repositories.prior,
        "local": repositories.prior,
        "remote": moved,
    }
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == moved


def test_forward_refuses_distinct_push_url_without_mutation(
    repositories: Repositories,
):
    push_remote = repositories.remote.parent / "push.git"
    git(push_remote.parent, "init", "-q", "--bare", str(push_remote))
    git(
        repositories.seed,
        "push",
        "-q",
        str(push_remote),
        f"{repositories.prior}:refs/heads/main",
    )
    git(
        repositories.daemon,
        "remote",
        "set-url",
        "--push",
        "origin",
        str(push_remote),
    )

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "remote must have one identical fetch and push URL"
    assert failure["mutations"] == []
    assert failure["readback"] == {"head": None, "local": None, "remote": None}
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior
    assert sha(push_remote, "refs/heads/main") == repositories.prior


def test_forward_refuses_multi_parent_candidate(repositories: Repositories):
    tree = sha(repositories.seed, f"{repositories.candidate}^{{tree}}")
    ancestor = sha(repositories.seed, f"{repositories.prior}^")
    merge_candidate = git(
        repositories.seed,
        "commit-tree",
        tree,
        "-p",
        repositories.prior,
        "-p",
        ancestor,
        "-m",
        "multi-parent candidate",
    ).stdout.strip()
    git(repositories.daemon, "fetch", "-q", str(repositories.seed), merge_candidate)

    completed = invoke(
        repositories,
        "forward",
        dry_run=True,
        to_sha=merge_candidate,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == (
        "candidate must have exactly one parent equal to the prior SHA"
    )
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_restore_happy(repositories: Repositories):
    assert invoke(repositories, "forward").returncode == 0

    completed = invoke(repositories, "restore")

    assert completed.returncode == 0, completed.stderr
    assert receipt(completed) == {
        "dry_run": False,
        "expected": {"from": repositories.candidate, "to": repositories.prior},
        "mutations": ["remote-main-cas", "local-main-cas", "worktree-reset"],
        "readback": {
            "head": repositories.prior,
            "local": repositories.prior,
            "remote": repositories.prior,
        },
        "result": "success",
        "verb": "restore",
    }
    assert git(repositories.daemon, "status", "--porcelain", "--untracked-files=all").stdout == ""


def test_restore_adapter_satisfies_migration_contract(repositories: Repositories):
    assert invoke(repositories, "forward").returncode == 0
    adapter = cas.CanonicalRefCasAdapter(repositories.daemon)

    observed = adapter.restore_canonical_ref_exact(
        repositories.candidate, repositories.prior
    )

    assert observed == repositories.prior
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_restore_refused_when_remote_is_not_candidate(repositories: Repositories):
    assert invoke(repositories, "forward").returncode == 0
    moved = advance_remote(repositories)

    completed = invoke(repositories, "restore")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == "remote main does not equal --from"
    assert failure["mutations"] == []
    assert failure["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": moved,
    }
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == moved


def test_dry_run_mutates_nothing(repositories: Repositories):
    forward = invoke(repositories, "forward", dry_run=True)

    assert forward.returncode == 0, forward.stderr
    assert receipt(forward)["readback"] == {
        "head": repositories.prior,
        "local": repositories.prior,
        "remote": repositories.prior,
    }
    assert receipt(forward)["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior

    assert invoke(repositories, "forward").returncode == 0
    restore = invoke(repositories, "restore", dry_run=True)

    assert restore.returncode == 0, restore.stderr
    assert receipt(restore)["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }
    assert receipt(restore)["mutations"] == []
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_readback_mismatch_reported(repositories: Repositories):
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        f"git update-ref refs/heads/main {repositories.prior}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == "exact canonical ref readback mismatch"
    assert failure["mutations"] == ["local-main-fast-forward", "remote-main-cas"]
    assert failure["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.prior,
    }
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.prior


def test_rejected_forward_push_reports_local_partial_state(
    repositories: Repositories,
):
    hook = repositories.remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == "Git operation failed during remote main compare-and-swap"
    assert failure["mutations"] == ["local-main-fast-forward"]
    assert failure["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.prior,
    }
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.prior


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_non_utf8_hook_output_is_json_safe(
    repositories: Repositories, verb: str
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_bytes(b"#!/bin/sh\nprintf '\\377\\n' >&2\n")
    hook.chmod(0o755)

    completed = invoke(repositories, verb)

    assert completed.returncode == 0, completed.stderr
    assert receipt(completed)["result"] == "success"


def test_forward_pushes_immutable_candidate_during_local_ref_race(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    (repositories.seed / "state.txt").write_text("competitor\n", encoding="utf-8")
    git(repositories.seed, "commit", "-q", "-am", "competing local update")
    competitor = sha(repositories.seed)
    git(repositories.daemon, "fetch", "-q", str(repositories.seed), competitor)
    original_run_git = cas._run_git
    raced = False

    def racing_run_git(operation, args, *, stage, check=True, cwd=None):
        nonlocal raced
        if stage == "remote main compare-and-swap" and not raced:
            raced = True
            git(
                repositories.daemon,
                "update-ref",
                "refs/heads/main",
                competitor,
                repositories.candidate,
            )
        return original_run_git(
            operation, args, stage=stage, check=check, cwd=cwd
        )

    monkeypatch.setattr(cas, "_run_git", racing_run_git)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
        )

    failure = captured.value.receipt
    assert raced
    assert failure["error"] == "exact canonical ref readback mismatch"
    assert failure["mutations"] == ["local-main-fast-forward", "remote-main-cas"]
    assert failure["readback"] == {
        "head": competitor,
        "local": competitor,
        "remote": repositories.candidate,
    }
    assert remote_sha(repositories.daemon) == repositories.candidate
    assert git(
        repositories.remote,
        "cat-file",
        "-e",
        f"{competitor}^{{commit}}",
        check=False,
    ).returncode != 0


def test_invalid_sha_fails_before_readback(repositories: Repositories):
    completed = invoke(
        repositories,
        "forward",
        from_sha="A" * 40,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "--from must be an exact lowercase 40-character SHA"
    assert failure["mutations"] == []
    assert failure["readback"] == {"head": None, "local": None, "remote": None}
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior
