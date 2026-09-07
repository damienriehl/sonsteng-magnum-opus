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


def remote_refs(repo: Path) -> dict[str, str]:
    lines = git(repo, "ls-remote", "--refs", "origin").stdout.splitlines()
    return {ref: value for value, ref in (line.split() for line in lines)}


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
    git(seed, "add", "state.txt", "removed-by-candidate.txt")
    git(seed, "commit", "-q", "-m", "prior")
    prior = sha(seed)
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

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
        "mutations": [
            "remote-main-cas",
            "local-main-cas",
            "worktree-alignment",
        ],
        "readback": {
            "head": repositories.prior,
            "local": repositories.prior,
            "remote": repositories.prior,
        },
        "result": "success",
        "verb": "restore",
    }
    assert git(repositories.daemon, "status", "--porcelain", "--untracked-files=all").stdout == ""
    assert not (repositories.daemon / "added-by-candidate.txt").exists()
    assert (repositories.daemon / "removed-by-candidate.txt").read_text(
        encoding="utf-8"
    ) == "restore me\n"


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


def test_dry_run_refuses_remote_race_before_final_readback(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_readback = cas._readback
    readbacks = 0
    moved = None

    def racing_readback(operation, remote_url):
        nonlocal readbacks, moved
        readbacks += 1
        if readbacks == 2:
            moved = advance_remote(repositories)
        return original_readback(operation, remote_url)

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

    def racing_readback(operation, remote_url):
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
        return original_readback(operation, remote_url)

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

    assert captured.value.receipt["error"] == "exact canonical ref readback mismatch"
    assert captured.value.receipt["readback"]["local"] == competitor


def test_dry_run_rechecks_symbolic_head_before_success(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_readback = cas._readback
    readbacks = 0

    def racing_readback(operation, remote_url):
        nonlocal readbacks
        readbacks += 1
        observed = original_readback(operation, remote_url)
        if readbacks == 2:
            git(repositories.daemon, "checkout", "-q", "--detach", repositories.prior)
        return observed

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

    assert captured.value.receipt["error"] == (
        "daemon worktree must have main checked out"
    )


def test_dry_run_rechecks_cleanliness_before_success(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_readback = cas._readback
    readbacks = 0

    def racing_readback(operation, remote_url):
        nonlocal readbacks
        readbacks += 1
        observed = original_readback(operation, remote_url)
        if readbacks == 2:
            (repositories.daemon / "late-race.txt").write_text(
                "late untracked change\n", encoding="utf-8"
            )
        return observed

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
