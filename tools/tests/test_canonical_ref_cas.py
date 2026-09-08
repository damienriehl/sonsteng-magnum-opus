"""Integration tests for the bounded Day Zero canonical-ref CAS."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

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


def remote_refs(repo: Path) -> dict[str, str]:
    lines = git(repo, "ls-remote", "--refs", "origin").stdout.splitlines()
    return {ref: value for value, ref in (line.split() for line in lines)}


def local_refs(repo: Path) -> dict[str, str]:
    lines = git(
        repo,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
    ).stdout.splitlines()
    return {ref: value for ref, value in (line.split() for line in lines)}


def sockets_available() -> bool:
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


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
    (seed / "removed-by-candidate.txt").write_text("restore me\n", encoding="utf-8")
    executable = seed / "executable.sh"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    git(seed, "add", "state.txt", "removed-by-candidate.txt", "executable.sh")
    git(seed, "commit", "-q", "-m", "prior")
    prior = sha(seed)
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    git(tmp_path, "clone", "-q", "--branch", "main", str(remote), str(daemon))
    (seed / "state.txt").write_text("candidate\n", encoding="utf-8")
    (seed / "removed-by-candidate.txt").unlink()
    (seed / "added-by-candidate.txt").write_text("remove me\n", encoding="utf-8")
    git(seed, "add", "--all")
    git(seed, "commit", "-q", "-m", "candidate")
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
    env: dict[str, str] | None = None,
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
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


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
        "git_executable": cas.GIT_PATH,
        "mutations": [
            "local-main-cas",
            "worktree-alignment",
            "remote-main-cas",
            "remote-tracking-main-cas",
        ],
        "readback": {
            "head": repositories.candidate,
            "local": repositories.candidate,
            "remote": repositories.candidate,
        },
        "remote_url": str(repositories.remote),
        "remote_url_sha256": hashlib.sha256(
            str(repositories.remote).encode("utf-8")
        ).hexdigest(),
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
    assert failure["remote_url"] == str(repositories.remote)
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


def test_forward_refuses_grafted_unrelated_candidate_before_mutation(
    repositories: Repositories,
):
    tree = sha(repositories.seed, f"{repositories.candidate}^{{tree}}")
    unrelated_candidate = git(
        repositories.seed,
        "commit-tree",
        tree,
        "-m",
        "unrelated candidate",
    ).stdout.strip()
    assert unrelated_candidate
    raw_headers = git(
        repositories.seed,
        "cat-file",
        "commit",
        unrelated_candidate,
    ).stdout.partition("\n\n")[0]
    assert raw_headers and "parent " not in raw_headers.splitlines()
    git(
        repositories.daemon,
        "fetch",
        "-q",
        str(repositories.seed),
        unrelated_candidate,
    )
    grafts = repositories.daemon / ".git" / "info" / "grafts"
    grafts.write_text(
        f"{unrelated_candidate} {repositories.prior}\n",
        encoding="utf-8",
    )
    reported_parents = git(
        repositories.daemon,
        "rev-list",
        "--parents",
        "-n",
        "1",
        unrelated_candidate,
    ).stdout.split()
    assert reported_parents and reported_parents == [
        unrelated_candidate,
        repositories.prior,
    ]

    completed = invoke(
        repositories,
        "forward",
        to_sha=unrelated_candidate,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == (
        "candidate must have exactly one parent equal to the prior SHA"
    )
    assert failure["mutations"] == []
    observed_head = sha(repositories.daemon)
    observed_remote = remote_sha(repositories.daemon)
    assert observed_head and observed_head == repositories.prior
    assert observed_remote and observed_remote == repositories.prior


def test_forward_refuses_replacement_object_spoof_before_mutation(
    repositories: Repositories,
):
    tree = sha(repositories.seed, f"{repositories.candidate}^{{tree}}")
    assert tree
    unrelated_candidate = git(
        repositories.seed,
        "commit-tree",
        tree,
        "-m",
        "unrelated candidate",
    ).stdout.strip()
    replacement_candidate = git(
        repositories.seed,
        "commit-tree",
        tree,
        "-p",
        repositories.prior,
        "-m",
        "replacement candidate",
    ).stdout.strip()
    assert unrelated_candidate
    assert replacement_candidate
    git(
        repositories.daemon,
        "fetch",
        "-q",
        str(repositories.seed),
        unrelated_candidate,
        replacement_candidate,
    )
    git(
        repositories.daemon,
        "replace",
        unrelated_candidate,
        replacement_candidate,
    )
    reported_parents = git(
        repositories.daemon,
        "rev-list",
        "--parents",
        "-n",
        "1",
        unrelated_candidate,
    ).stdout.split()
    assert reported_parents and reported_parents == [
        unrelated_candidate,
        repositories.prior,
    ]

    completed = invoke(
        repositories,
        "forward",
        to_sha=unrelated_candidate,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == (
        "candidate must have exactly one parent equal to the prior SHA"
    )
    assert failure["mutations"] == []
    observed_head = sha(repositories.daemon)
    observed_remote = remote_sha(repositories.daemon)
    assert observed_head and observed_head == repositories.prior
    assert observed_remote and observed_remote == repositories.prior


def test_restore_happy(repositories: Repositories):
    assert invoke(repositories, "forward").returncode == 0

    completed = invoke(repositories, "restore")

    assert completed.returncode == 0, completed.stderr
    assert receipt(completed) == {
        "dry_run": False,
        "expected": {"from": repositories.candidate, "to": repositories.prior},
        "git_executable": cas.GIT_PATH,
        "mutations": [
            "remote-main-cas",
            "local-main-cas",
            "worktree-alignment",
            "remote-tracking-main-cas",
        ],
        "readback": {
            "head": repositories.prior,
            "local": repositories.prior,
            "remote": repositories.prior,
        },
        "remote_url": str(repositories.remote),
        "remote_url_sha256": hashlib.sha256(
            str(repositories.remote).encode("utf-8")
        ).hexdigest(),
        "result": "success",
        "verb": "restore",
    }
    assert git(repositories.daemon, "status", "--porcelain", "--untracked-files=all").stdout == ""
    assert not (repositories.daemon / "added-by-candidate.txt").exists()
    assert (repositories.daemon / "removed-by-candidate.txt").read_text(
        encoding="utf-8"
    ) == "restore me\n"
    assert sha(repositories.daemon, "refs/remotes/origin/main") == repositories.prior


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


@pytest.mark.parametrize("verb", ["forward", "restore"])
@pytest.mark.parametrize(
    "index_option",
    [
        pytest.param("--assume-unchanged", id="assume-unchanged"),
        pytest.param("--skip-worktree", id="skip-worktree"),
    ],
)
def test_hidden_index_entry_is_refused(
    repositories: Repositories,
    verb: str,
    index_option: str,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    git(repositories.daemon, "update-index", index_option, "state.txt")
    (repositories.daemon / "state.txt").write_text("hidden edit\n", encoding="utf-8")

    completed = invoke(repositories, verb, dry_run=True)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "daemon index contains hidden tracked entries"
    assert failure["mutations"] == []


def test_core_filemode_false_cannot_hide_executable_bit_drift(
    repositories: Repositories,
):
    git(repositories.daemon, "config", "core.fileMode", "false")
    (repositories.daemon / "executable.sh").chmod(0o644)

    completed = invoke(repositories, "forward", dry_run=True)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "daemon worktree must be clean"
    assert failure["mutations"] == []


def test_core_symlinks_false_cannot_hide_tracked_file_type_drift(
    repositories: Repositories,
):
    link = repositories.daemon / "tracked-link"
    git(repositories.daemon, "config", "user.name", "CAS Test")
    git(repositories.daemon, "config", "user.email", "cas@example.invalid")
    link.symlink_to("base.txt")
    git(repositories.daemon, "add", "tracked-link")
    git(repositories.daemon, "commit", "-q", "-m", "track symlink")
    committed = sha(repositories.daemon)
    assert committed
    git(
        repositories.daemon,
        "push",
        "-q",
        "origin",
        f"{committed}:refs/heads/main",
    )
    tree = sha(repositories.daemon, f"{committed}^{{tree}}")
    assert tree
    next_candidate = git(
        repositories.daemon,
        "commit-tree",
        tree,
        "-p",
        committed,
        "-m",
        "candidate after symlink",
    ).stdout.strip()
    assert next_candidate
    git(repositories.daemon, "config", "core.symlinks", "false")
    link.unlink()
    link.write_text("base.txt", encoding="utf-8")

    completed = invoke(
        repositories,
        "forward",
        from_sha=committed,
        to_sha=next_candidate,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "daemon worktree must be clean"
    assert failure["mutations"] == []
    observed_head = sha(repositories.daemon)
    observed_remote = remote_sha(repositories.daemon)
    assert observed_head and observed_head == committed
    assert observed_remote and observed_remote == committed


def test_core_symlinks_false_allows_exact_symlink_forward_and_restore(
    repositories: Repositories,
):
    path = repositories.daemon / "mode-switch"
    git(repositories.daemon, "config", "user.name", "CAS Test")
    git(repositories.daemon, "config", "user.email", "cas@example.invalid")
    path.write_text("regular\n", encoding="utf-8")
    git(repositories.daemon, "add", "mode-switch")
    git(repositories.daemon, "commit", "-q", "-m", "regular-file prior")
    prior = sha(repositories.daemon)
    assert prior
    git(
        repositories.daemon,
        "push",
        "-q",
        "origin",
        f"{prior}:refs/heads/main",
    )
    path.unlink()
    path.symlink_to("base.txt")
    git(repositories.daemon, "add", "mode-switch")
    git(repositories.daemon, "commit", "-q", "-m", "symlink candidate")
    candidate = sha(repositories.daemon)
    assert candidate
    git(repositories.daemon, "reset", "--hard", "-q", prior)
    git(repositories.daemon, "config", "core.symlinks", "false")

    forward = invoke(
        repositories,
        "forward",
        from_sha=prior,
        to_sha=candidate,
    )

    assert forward.returncode == 0, forward.stderr
    assert path.is_symlink()
    observed_head = sha(repositories.daemon)
    observed_remote = remote_sha(repositories.daemon)
    assert observed_head and observed_head == candidate
    assert observed_remote and observed_remote == candidate

    restore = invoke(
        repositories,
        "restore",
        from_sha=candidate,
        to_sha=prior,
    )

    assert restore.returncode == 0, restore.stderr
    assert not path.is_symlink()
    assert path.read_text(encoding="utf-8") == "regular\n"
    observed_head = sha(repositories.daemon)
    observed_remote = remote_sha(repositories.daemon)
    assert observed_head and observed_head == prior
    assert observed_remote and observed_remote == prior


def test_runbook_describes_forward_cas_without_destructive_ref_moves():
    documentation = (ROOT / "docs" / "day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    assert "`forward` requires" in documentation
    forward_description = documentation.split("`forward` requires", 1)[1]
    assert forward_description

    for required_text in (
        "raw commit object",
        "`update-ref refs/heads/main <candidate> <prior>`",
        "before and after aligning",
        "`read-tree -m -u <candidate>`",
        "Before mutation it snapshots the full local ref map",
        "advertised non-hidden",
        "one `ls-remote --symref`",
        "remote symbolic `HEAD`",
        "after-state `ls-remote --symref`",
        "only change among the remote's advertised",
        "`refs/remotes/origin/main`",
    ):
        assert required_text in forward_description
    assert "merge --ff-only" not in forward_description
    assert "reset --hard" not in forward_description


def test_dry_run_refuses_remote_race_before_final_readback(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_readback = cas._readback
    readbacks = 0
    moved = None

    def racing_readback(operation, remote_url, **kwargs):
        nonlocal readbacks, moved
        readbacks += 1
        if readbacks == 2:
            moved = advance_remote(repositories)
        return original_readback(operation, remote_url, **kwargs)

    monkeypatch.setattr(cas, "_readback", racing_readback)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            dry_run=True,
        )

    assert moved is not None
    assert captured.value.receipt["error"] == "exact canonical ref readback mismatch"
    assert captured.value.receipt["readback"]["remote"] == moved


def test_dry_run_refuses_local_race_before_final_readback(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    (repositories.seed / "state.txt").write_text("competitor\n", encoding="utf-8")
    git(repositories.seed, "commit", "-q", "-am", "competing local update")
    competitor = sha(repositories.seed)
    git(repositories.daemon, "fetch", "-q", str(repositories.seed), competitor)
    original_readback = cas._readback
    readbacks = 0

    def racing_readback(operation, remote_url, **kwargs):
        nonlocal readbacks
        readbacks += 1
        if readbacks == 2:
            git(
                repositories.daemon,
                "update-ref",
                "refs/heads/main",
                competitor,
                repositories.prior,
            )
        return original_readback(operation, remote_url, **kwargs)

    monkeypatch.setattr(cas, "_readback", racing_readback)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            dry_run=True,
        )

    assert captured.value.receipt["error"] == "daemon worktree must be clean"
    assert captured.value.receipt["readback"]["local"] == competitor


@pytest.mark.parametrize(
    ("verb", "dry_run"),
    [
        pytest.param("forward", False, id="forward"),
        pytest.param("restore", False, id="restore"),
        pytest.param("forward", True, id="dry-run"),
    ],
)
def test_validated_readback_is_last_git_interaction_before_success(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    dry_run: bool,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    original_run_git = cas._run_git
    stages: list[str] = []
    remote_snapshot_args: list[list[str]] = []

    def recording_run_git(operation, args, *, stage, check=True, cwd=None, env=None):
        stages.append(stage)
        if stage == "remote ref snapshot readback":
            remote_snapshot_args.append(list(args))
        return original_run_git(
            operation, args, stage=stage, check=check, cwd=cwd, env=env
        )

    monkeypatch.setattr(cas, "_run_git", recording_run_git)
    function = cas.forward if verb == "forward" else cas.restore
    result = function(
        repositories.daemon,
        "origin",
        "main",
        repositories.prior if verb == "forward" else repositories.candidate,
        repositories.candidate if verb == "forward" else repositories.prior,
        dry_run=dry_run,
    )

    assert result["result"] == "success"
    assert stages[-1] == "local main readback"
    assert remote_snapshot_args
    assert stages.index("remote ref snapshot readback") < len(stages) - 1
    assert all(
        args == ["ls-remote", "--symref", "--exit-code", str(repositories.remote)]
        for args in remote_snapshot_args
    )


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_push_changes_only_remote_main_when_follow_tags_is_enabled(
    repositories: Repositories, verb: str
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
        tag_target = repositories.prior
    else:
        tag_target = repositories.candidate
    git(repositories.daemon, "config", "user.name", "CAS Test")
    git(repositories.daemon, "config", "user.email", "cas@example.invalid")
    git(
        repositories.daemon,
        "tag",
        "-a",
        "candidate-tag",
        tag_target,
        "-m",
        "reachable annotated tag",
    )
    git(repositories.daemon, "config", "push.followTags", "true")
    before = remote_refs(repositories.daemon)

    completed = invoke(repositories, verb)

    assert completed.returncode == 0, completed.stderr
    after = remote_refs(repositories.daemon)
    expected = dict(before)
    expected["refs/heads/main"] = (
        repositories.candidate if verb == "forward" else repositories.prior
    )
    assert after == expected


@pytest.mark.parametrize(
    "delta",
    [
        pytest.param("added", id="added"),
        pytest.param("changed", id="changed"),
        pytest.param("removed", id="removed"),
    ],
)
def test_post_receive_hook_non_main_remote_ref_delta_is_reported(
    repositories: Repositories,
    delta: str,
):
    tag_ref = "refs/tags/unexpected" if delta == "added" else "refs/tags/existing"
    if delta != "added":
        git(repositories.remote, "update-ref", tag_ref, repositories.prior)
    hook_command = (
        f"git update-ref -d {tag_ref}"
        if delta == "removed"
        else f"git update-ref {tag_ref} {repositories.candidate}"
    )
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        f"#!/bin/sh\n{hook_command}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "remote ref map changed outside canonical main"
    assert failure["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
    after = remote_refs(repositories.daemon)
    if delta == "removed":
        assert tag_ref not in after
    else:
        assert after[tag_ref] == repositories.candidate


def test_post_merge_hook_cannot_retarget_forward_push(repositories: Repositories):
    decoy = repositories.remote.parent / "decoy.git"
    git(decoy.parent, "init", "-q", "--bare", str(decoy))
    git(
        repositories.seed,
        "push",
        "-q",
        str(decoy),
        f"{repositories.prior}:refs/heads/main",
    )
    hook = repositories.daemon / ".git" / "hooks" / "post-merge"
    hook.write_text(
        "#!/bin/sh\n"
        f"git remote set-url origin {decoy}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    assert sha(repositories.remote, "refs/heads/main") == repositories.candidate
    assert sha(decoy, "refs/heads/main") == repositories.prior
    assert (
        git(repositories.daemon, "remote", "get-url", "origin").stdout.strip()
        == str(repositories.remote)
    )


def test_push_uses_pinned_url_and_fully_qualified_main_refspec(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_run_git = cas._run_git
    push_args = None

    def recording_run_git(operation, args, *, stage, check=True, cwd=None, env=None):
        nonlocal push_args
        if stage == "remote main compare-and-swap":
            push_args = list(args)
        return original_run_git(
            operation, args, stage=stage, check=check, cwd=cwd, env=env
        )

    monkeypatch.setattr(cas, "_run_git", recording_run_git)

    result = cas.forward(
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
    )

    assert result["result"] == "success"
    assert push_args is not None
    assert "--no-follow-tags" in push_args
    assert "push.followTags=false" in push_args
    assert "push.default=nothing" in push_args
    assert str(repositories.remote) in push_args
    assert "origin" not in push_args
    assert "refs/heads/main:refs/heads/main" in push_args


def test_remote_url_change_is_detected_without_retargeting_push(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    decoy = repositories.remote.parent / "decoy.git"
    git(decoy.parent, "init", "-q", "--bare", str(decoy))
    git(
        repositories.seed,
        "push",
        "-q",
        str(decoy),
        f"{repositories.prior}:refs/heads/main",
    )
    original_run_git = cas._run_git
    retargeted = False

    def racing_run_git(operation, args, *, stage, check=True, cwd=None, env=None):
        nonlocal retargeted
        if stage == "remote main compare-and-swap" and not retargeted:
            retargeted = True
            git(repositories.daemon, "remote", "set-url", "origin", str(decoy))
        return original_run_git(
            operation, args, stage=stage, check=check, cwd=cwd, env=env
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

    assert retargeted
    assert captured.value.receipt["error"] == (
        "validated remote URL changed during operation"
    )
    assert sha(repositories.remote, "refs/heads/main") == repositories.candidate
    assert sha(decoy, "refs/heads/main") == repositories.prior


def test_restore_alignment_does_not_overwrite_raced_local_main(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    assert invoke(repositories, "forward").returncode == 0
    (repositories.seed / "state.txt").write_text("competitor\n", encoding="utf-8")
    git(repositories.seed, "commit", "-q", "-am", "competing local update")
    competitor = sha(repositories.seed)
    git(repositories.daemon, "fetch", "-q", str(repositories.seed), competitor)
    original_run_git = cas._run_git
    raced = False

    def racing_run_git(operation, args, *, stage, check=True, cwd=None, env=None):
        nonlocal raced
        if stage == "daemon worktree alignment" and not raced:
            raced = True
            git(
                repositories.daemon,
                "update-ref",
                "refs/heads/main",
                competitor,
                repositories.prior,
            )
        return original_run_git(
            operation, args, stage=stage, check=check, cwd=cwd, env=env
        )

    monkeypatch.setattr(cas, "_run_git", racing_run_git)

    with pytest.raises(cas.CasFailure) as captured:
        cas.restore(
            repositories.daemon,
            "origin",
            "main",
            repositories.candidate,
            repositories.prior,
        )

    assert raced
    assert captured.value.receipt["error"] == "local main changed during worktree alignment"
    assert sha(repositories.daemon, "refs/heads/main") == competitor
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_cas_does_not_overwrite_coherent_ancestor_race(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    competitor = sha(repositories.daemon, f"{repositories.prior}^")
    original_run_git = cas._run_git
    raced = False

    def racing_run_git(operation, args, *, stage, check=True, cwd=None, env=None):
        nonlocal raced
        if stage == "local main compare-and-swap" and not raced:
            raced = True
            git(repositories.daemon, "reset", "--hard", "-q", competitor)
        return original_run_git(
            operation, args, stage=stage, check=check, cwd=cwd, env=env
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

    assert raced
    assert captured.value.receipt["error"] == (
        "Git operation failed during local main compare-and-swap"
    )
    assert captured.value.receipt["mutations"] == []
    assert sha(repositories.daemon, "refs/heads/main") == competitor
    assert sha(repositories.daemon) == competitor
    assert remote_sha(repositories.daemon) == repositories.prior


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
    assert failure["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
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
    assert failure["mutations"] == ["local-main-cas", "worktree-alignment"]
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

    def racing_run_git(operation, args, *, stage, check=True, cwd=None, env=None):
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
            operation, args, stage=stage, check=check, cwd=cwd, env=env
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
    assert failure["error"] == "daemon worktree must be clean"
    assert failure["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
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
    assert failure["error_code"] == "invalid-coordinate"
    assert failure["expected"] == {"from": None, "to": None}
    assert failure["mutations"] == []
    assert failure["readback"] == {"head": None, "local": None, "remote": None}
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


@pytest.mark.parametrize(
    ("coordinate", "value"),
    [
        ("from_sha", "/tmp/not-a-sha"),
        ("to_sha", "../../private/not-a-sha"),
        ("from_sha", "super-secret-token-value"),
        ("to_sha", "credential=super-secret-value"),
    ],
)
def test_invalid_coordinate_is_redacted_from_receipt(
    repositories: Repositories, coordinate: str, value: str
):
    completed = invoke(repositories, "forward", **{coordinate: value})

    assert completed.returncode != 0
    assert value not in completed.stdout
    assert value not in completed.stderr
    failure = receipt(completed)
    assert failure["error_code"] == "invalid-coordinate"
    assert failure["expected"] == {"from": None, "to": None}


def test_run_git_uses_allowlisted_environment_and_pins_config_isolation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    captured_argv = None
    captured_environment = None

    def fake_run(*args, **kwargs):
        nonlocal captured_argv
        nonlocal captured_environment
        captured_argv = args[0]
        captured_environment = kwargs["env"]
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setenv("CAS_UNRELATED_SENTINEL", "must-not-reach-git")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/tmp/hostile-global-config")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/tmp/hostile-system-config")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "uploadpack.hideRefs")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "refs/heads/side")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'uploadpack.hideRefs=refs/heads/side'")
    monkeypatch.setenv("PATH", "/tmp/hostile-path")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/hostile-agent")
    monkeypatch.setattr(subprocess, "run", fake_run)
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    cas._run_git(operation, ["version"], stage="environment test")

    assert captured_argv is not None
    assert captured_argv[0] == cas.GIT_PATH
    assert Path(captured_argv[0]).is_absolute()
    assert captured_environment is not None
    assert "CAS_UNRELATED_SENTINEL" not in captured_environment
    assert "GIT_CONFIG_KEY_0" not in captured_environment
    assert "GIT_CONFIG_VALUE_0" not in captured_environment
    assert "GIT_CONFIG_PARAMETERS" not in captured_environment
    assert "PATH" not in captured_environment
    assert "SSH_AUTH_SOCK" not in captured_environment
    assert captured_environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert captured_environment["GIT_CONFIG_SYSTEM"] == os.devnull
    assert captured_environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert captured_environment["GIT_CONFIG_COUNT"] == "0"
    assert captured_environment["GIT_SSH_COMMAND"]
    assert captured_environment["GIT_SSH_COMMAND"] == os.devnull


def test_run_git_without_resolved_git_carries_stage_and_starts_no_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fail_if_called(*_args, **_kwargs):
        pytest.fail("subprocess.run must not be called without resolved Git")

    operation = cas.Operation(
        "forward",
        tmp_path,
        "origin",
        "main",
        "1" * 40,
        "2" * 40,
        False,
    )
    monkeypatch.setattr(cas, "GIT_PATH", None)
    monkeypatch.setattr(subprocess, "run", fail_if_called)

    with pytest.raises(cas.CasError) as captured:
        cas._run_git(operation, ["version"], stage="supplied fail-closed stage")

    assert str(captured.value)
    assert str(captured.value) == "Git operation failed during supplied fail-closed stage"


def test_git_resolution_fails_closed_when_confstr_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    def raising_confstr(_name: str):
        raise OSError("CS_PATH unavailable")

    monkeypatch.setattr(cas.os, "confstr", raising_confstr)

    assert cas._resolve_git_path() is None


@pytest.mark.parametrize("system_path", ["relative/bin", "/bin::/usr/bin"])
def test_git_resolution_fails_closed_for_malformed_or_relative_cs_path(
    system_path: str, monkeypatch: pytest.MonkeyPatch
):
    def fail_if_called(*_args, **_kwargs):
        pytest.fail("shutil.which must not inspect an invalid CS_PATH")

    monkeypatch.setattr(cas.os, "confstr", lambda _name: system_path)
    monkeypatch.setattr(cas.shutil, "which", fail_if_called)

    assert cas._resolve_git_path() is None


def test_git_resolution_fails_closed_when_git_is_not_on_cs_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(cas.os, "confstr", lambda _name: str(tmp_path))
    monkeypatch.setattr(cas.shutil, "which", lambda _name, *, path: None)

    assert cas._resolve_git_path() is None


def test_git_resolution_fails_closed_when_strict_resolve_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidate = tmp_path / "git"

    def raising_resolve(_path: Path, *, strict: bool = False):
        assert strict is True
        raise OSError("resolved Git disappeared")

    monkeypatch.setattr(cas.os, "confstr", lambda _name: str(tmp_path))
    monkeypatch.setattr(cas.shutil, "which", lambda _name, *, path: str(candidate))
    monkeypatch.setattr(cas.pathlib.Path, "resolve", raising_resolve)

    assert cas._resolve_git_path() is None


def test_git_resolution_requires_a_regular_executable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    candidate = tmp_path / "git"
    candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(cas.os, "confstr", lambda _name: str(tmp_path))

    assert cas._resolve_git_path() is None

    candidate.chmod(0o700)

    assert cas._resolve_git_path() == str(candidate.resolve())

    candidate.unlink()
    candidate.mkdir()

    assert cas._resolve_git_path() is None


def test_missing_resolved_git_fails_closed_before_subprocess(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    def fail_if_called(*_args, **_kwargs):
        pytest.fail("subprocess.run must not be called without resolved Git")

    monkeypatch.setattr(cas, "GIT_PATH", None)
    monkeypatch.setattr(subprocess, "run", fail_if_called)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
        )

    assert captured.value.receipt == {
        "dry_run": False,
        "error": "Git operation failed during repository validation",
        "expected": {
            "from": repositories.prior,
            "to": repositories.candidate,
        },
        "git_executable": None,
        "mutations": [],
        "readback": {"head": None, "local": None, "remote": None},
        "remote_url": None,
        "remote_url_sha256": None,
        "result": "error",
        "verb": "forward",
    }


def test_forward_ignores_inherited_path_git_shim_and_records_real_executable(
    repositories: Repositories,
):
    shim_directory = repositories.remote.parent / "shim"
    shim_directory.mkdir()
    shim_marker = repositories.remote.parent / "path-git-shim-invoked"
    path_shim = shim_directory / "git"
    path_shim.write_text(
        "#!/bin/sh\n"
        f": > {shim_marker}\n"
        "exit 97\n",
        encoding="utf-8",
    )
    path_shim.chmod(0o700)
    hostile_environment = dict(os.environ)
    hostile_environment["PATH"] = str(shim_directory)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode == 0, completed.stderr
    result = receipt(completed)
    assert result["result"] == "success"
    assert result["git_executable"] == cas.GIT_PATH
    assert Path(result["git_executable"]).is_absolute()
    assert not shim_marker.exists()


@pytest.mark.skipif(not sockets_available(), reason="localhost sockets are unavailable")
def test_forward_uses_smart_http_remote_without_inherited_helper_path(
    repositories: Repositories,
):
    git(repositories.remote, "config", "http.receivepack", "true")
    requests: list[tuple[str, str]] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(
                *args,
                directory=str(repositories.remote.parent),
                **kwargs,
            )

        def _serve_git(self) -> None:
            parsed = urlsplit(self.path)
            content_length = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(content_length)
            backend_environment = dict(os.environ)
            backend_environment.update(
                {
                    "CONTENT_LENGTH": str(content_length),
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "GIT_PROJECT_ROOT": str(repositories.remote.parent),
                    "PATH_INFO": parsed.path,
                    "QUERY_STRING": parsed.query,
                    "REMOTE_ADDR": self.client_address[0],
                    "REQUEST_METHOD": self.command,
                }
            )
            completed = subprocess.run(
                [cas.GIT_PATH, "http-backend"],
                input=request_body,
                capture_output=True,
                env=backend_environment,
                check=False,
            )
            header_block, separator, response_body = completed.stdout.partition(
                b"\r\n\r\n"
            )
            if not separator:
                header_block, separator, response_body = completed.stdout.partition(
                    b"\n\n"
                )
            assert separator
            status = 200
            response_headers: list[tuple[str, str]] = []
            for line in header_block.decode("latin-1").splitlines():
                name, value = line.split(":", 1)
                assert name
                if name.lower() == "status":
                    status = int(value.strip().split()[0])
                else:
                    response_headers.append((name, value.strip()))
            self.send_response(status)
            for name, value in response_headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            requests.append((self.command, self.path))

        def do_GET(self) -> None:  # noqa: N802
            self._serve_git()

        def do_POST(self) -> None:  # noqa: N802
            self._serve_git()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        remote_url = f"http://127.0.0.1:{server.server_port}/{repositories.remote.name}"
        assert remote_url
        git(repositories.daemon, "remote", "set-url", "origin", remote_url)

        shim_directory = repositories.remote.parent / "helper-shims"
        shim_directory.mkdir()
        shim_marker = repositories.remote.parent / "ambient-helper-invoked"
        for name in ("git-remote-http", "ssh"):
            shim = shim_directory / name
            shim.write_text(
                "#!/bin/sh\n"
                f": > {shim_marker}\n"
                "exit 97\n",
                encoding="utf-8",
            )
            shim.chmod(0o700)
        hostile_environment = dict(os.environ)
        hostile_environment["PATH"] = str(shim_directory)
        hostile_environment["SSH_AUTH_SOCK"] = str(
            repositories.remote.parent / "hostile-agent"
        )

        completed = invoke(
            repositories,
            "forward",
            env=hostile_environment,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    result = receipt(completed)
    assert result["result"]
    assert result["result"] == "success"
    assert result["remote_url"]
    assert result["remote_url"] == remote_url
    assert result["mutations"]
    assert result["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
    assert requests
    assert any(
        method
        and path
        and method == "POST"
        and path.endswith("/git-receive-pack")
        for method, path in requests
    )
    observed_remote = sha(repositories.remote, "refs/heads/main")
    assert observed_remote
    assert observed_remote == repositories.candidate
    assert not shim_marker.exists()


def test_forward_refuses_global_pack_objects_hook_before_mutation(
    repositories: Repositories,
):
    hook_log = repositories.remote.parent / "pack-hook.log"
    hook = repositories.remote.parent / "pack-hook.sh"
    hook.write_text(
        "#!/bin/sh\n"
        f"git --git-dir={repositories.daemon / '.git'} update-ref "
        f"refs/heads/side {repositories.candidate}\n"
        f"printf ran >> {hook_log}\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    global_config = repositories.remote.parent / "hostile-pack.config"
    global_config.write_text(
        f"[uploadpack]\n\tpackObjectsHook = {hook}\n",
        encoding="utf-8",
    )
    hostile_environment = dict(os.environ)
    hostile_environment["GIT_CONFIG_GLOBAL"] = str(global_config)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["mutations"] == []
    assert "refs/heads/side" not in local_refs(repositories.daemon)
    assert not hook_log.exists()
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_refuses_global_url_instead_of_before_mutation(
    repositories: Repositories,
):
    decoy = repositories.remote.parent / "decoy-instead-of.git"
    git(decoy.parent, "init", "-q", "--bare", str(decoy))
    git(
        repositories.seed,
        "push",
        "-q",
        str(decoy),
        f"{repositories.prior}:refs/heads/main",
    )
    configured_url = "https://prod.invalid/repo.git"
    git(repositories.daemon, "remote", "set-url", "origin", configured_url)
    global_config = repositories.remote.parent / "hostile-url.config"
    global_config.write_text(
        f'[url "{decoy}"]\n\tinsteadOf = {configured_url}\n',
        encoding="utf-8",
    )
    hostile_environment = dict(os.environ)
    hostile_environment["GIT_CONFIG_GLOBAL"] = str(global_config)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(decoy, "refs/heads/main") == repositories.prior


def test_forward_refuses_global_uploadpack_hide_refs_before_mutation(
    repositories: Repositories,
):
    git(
        repositories.remote,
        "update-ref",
        "refs/heads/side",
        repositories.prior,
    )
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        f"git update-ref refs/heads/side {repositories.candidate}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    global_config = repositories.remote.parent / "hostile-hide-refs.config"
    global_config.write_text(
        "[uploadpack]\n\thideRefs = refs/heads/side\n",
        encoding="utf-8",
    )
    hostile_environment = dict(os.environ)
    hostile_environment["GIT_CONFIG_GLOBAL"] = str(global_config)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(repositories.remote, "refs/heads/side") == repositories.prior


def test_forward_refuses_local_non_main_ref_delta_before_owned_mutation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_proof = cas._require_clean_fresh_candidate

    def mutating_candidate_proof(operation, candidate):
        original_proof(operation, candidate)
        git(
            repositories.daemon,
            "update-ref",
            "refs/heads/side",
            repositories.candidate,
        )

    monkeypatch.setattr(cas, "_require_clean_fresh_candidate", mutating_candidate_proof)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
        )

    assert captured.value.receipt["error"] == (
        "local ref map changed outside allowed transitions"
    )
    assert captured.value.receipt["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_reports_local_non_main_ref_delta_before_success(
    repositories: Repositories,
):
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        f"git --git-dir={repositories.daemon / '.git'} update-ref "
        f"refs/heads/side {repositories.candidate}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "local ref map changed outside allowed transitions"
    assert failure["result"] == "error"
    assert sha(repositories.daemon, "refs/heads/side") == repositories.candidate


def test_forward_reports_remote_head_symref_change(
    repositories: Repositories,
):
    git(
        repositories.remote,
        "update-ref",
        "refs/heads/side",
        repositories.prior,
    )
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "git symbolic-ref HEAD refs/heads/side\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == "remote HEAD changed during operation"
    assert git(repositories.remote, "symbolic-ref", "HEAD").stdout.strip() == (
        "refs/heads/side"
    )


def test_forward_preserves_annotated_remote_tag_in_combined_snapshot(
    repositories: Repositories,
):
    git(
        repositories.seed,
        "tag",
        "-a",
        "preserved",
        repositories.prior,
        "-m",
        "preserved annotated tag",
    )
    git(repositories.seed, "push", "-q", "origin", "refs/tags/preserved")
    tag_before = sha(repositories.remote, "refs/tags/preserved")

    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    tag_after = sha(repositories.remote, "refs/tags/preserved")
    assert tag_before and tag_after and tag_after == tag_before


def test_forward_refuses_shallow_daemon_before_mutation(
    repositories: Repositories,
):
    shallow_path = Path(
        git(repositories.daemon, "rev-parse", "--git-path", "shallow").stdout.strip()
    )
    if not shallow_path.is_absolute():
        shallow_path = repositories.daemon / shallow_path
    shallow_path.write_text(f"{repositories.prior}\n", encoding="utf-8")

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "daemon repository must not be shallow"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_refuses_second_worktree_holding_main_before_mutation(
    repositories: Repositories,
):
    second = repositories.remote.parent / "second-main-worktree"
    git(repositories.daemon, "worktree", "add", "-q", "-f", str(second), "main")

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "main must not be checked out in another worktree"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(second) == repositories.prior
    assert git(second, "status", "--porcelain").stdout == ""
    assert remote_sha(repositories.daemon) == repositories.prior


def test_success_updates_remote_tracking_main_and_records_validated_url(
    repositories: Repositories,
):
    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    result = receipt(completed)
    assert result["remote_url"] == str(repositories.remote)
    assert result["remote_url_sha256"] == hashlib.sha256(
        str(repositories.remote).encode("utf-8")
    ).hexdigest()
    tracking = sha(repositories.daemon, "refs/remotes/origin/main")
    assert tracking and tracking == repositories.candidate
    assert git(repositories.daemon, "status", "--short", "--branch").stdout == (
        "## main...origin/main\n"
    )


def test_push_source_is_verified_non_shallow_after_fetch(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_check = cas._require_non_shallow
    checked_paths: list[Path] = []

    def recording_check(operation, *, cwd=None, error):
        checked_paths.append(cwd or operation.repo)
        return original_check(operation, cwd=cwd, error=error)

    monkeypatch.setattr(cas, "_require_non_shallow", recording_check)

    result = cas.forward(
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
    )

    assert result["result"] == "success"
    assert checked_paths[0] == repositories.daemon
    assert checked_paths[1].name == "source.git"


def test_forward_refuses_stale_remote_tracking_main_before_mutation(
    repositories: Repositories,
):
    stale = sha(repositories.daemon, f"{repositories.prior}^")
    assert stale and stale != repositories.prior
    git(
        repositories.daemon,
        "update-ref",
        "refs/remotes/origin/main",
        stale,
        repositories.prior,
    )

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "remote-tracking main does not equal --from"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_refuses_malformed_local_ref_before_mutation(
    repositories: Repositories,
):
    broken = repositories.daemon / ".git" / "refs" / "heads" / "broken"
    broken.write_text("f" * 40 + "\n", encoding="utf-8")

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "local refs could not be read exactly"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_refuses_dangling_symbolic_local_ref_before_mutation(
    repositories: Repositories,
):
    git(
        repositories.daemon,
        "symbolic-ref",
        "refs/heads/dangling",
        "refs/heads/missing",
    )

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "local refs could not be read exactly"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_reports_malformed_local_ref_before_success(
    repositories: Repositories,
):
    broken = repositories.daemon / ".git" / "refs" / "heads" / "broken"
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' {'f' * 40} > {broken}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "local refs could not be read exactly"
    assert failure["result"] == "error"
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_restore_refuses_stale_remote_tracking_main_before_mutation(
    repositories: Repositories,
):
    assert invoke(repositories, "forward").returncode == 0
    stale = sha(repositories.daemon, f"{repositories.prior}^")
    assert stale and stale != repositories.candidate
    git(
        repositories.daemon,
        "update-ref",
        "refs/remotes/origin/main",
        stale,
        repositories.candidate,
    )

    completed = invoke(repositories, "restore")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "remote-tracking main does not equal --from"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_forward_refuses_second_main_worktree_race_before_owned_mutation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    second = repositories.remote.parent / "racing-main-worktree"
    original_snapshot = cas._remote_snapshot
    snapshots = 0

    def racing_snapshot(operation, remote_url):
        nonlocal snapshots
        snapshots += 1
        observed = original_snapshot(operation, remote_url)
        if snapshots == 2:
            git(
                repositories.daemon,
                "worktree",
                "add",
                "-q",
                "-f",
                str(second),
                "main",
            )
        return observed

    monkeypatch.setattr(cas, "_remote_snapshot", racing_snapshot)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
        )

    assert snapshots == 2
    failure = captured.value.receipt
    assert failure["error"] == "main must not be checked out in another worktree"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(second) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_reports_local_main_race_during_final_remote_snapshot(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_snapshot = cas._remote_snapshot
    snapshots = 0

    def racing_snapshot(operation, remote_url):
        nonlocal snapshots
        snapshots += 1
        observed = original_snapshot(operation, remote_url)
        if snapshots == 3:
            git(
                repositories.daemon,
                "update-ref",
                "refs/heads/main",
                repositories.prior,
                repositories.candidate,
            )
        return observed

    monkeypatch.setattr(cas, "_remote_snapshot", racing_snapshot)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
        )

    assert snapshots == 3
    failure = captured.value.receipt
    assert failure["result"] == "error"
    assert failure["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.candidate


@pytest.mark.parametrize("remote_moved", [False, True])
def test_receipt_redacts_remote_url_credentials(
    repositories: Repositories, remote_moved: bool
):
    exact_url = f"file://operator:super-secret@localhost{repositories.remote}"
    redacted_url = f"file://localhost{repositories.remote}"
    git(repositories.daemon, "remote", "set-url", "origin", exact_url)
    if remote_moved:
        advance_remote(repositories)

    completed = invoke(repositories, "forward")

    assert (completed.returncode != 0) is remote_moved
    assert "super-secret" not in completed.stdout
    assert "super-secret" not in completed.stderr
    result = receipt(completed)
    assert result["remote_url"] == redacted_url
    assert result["remote_url_sha256"] == hashlib.sha256(
        exact_url.encode("utf-8")
    ).hexdigest()
    assert result["result"] == ("error" if remote_moved else "success")
