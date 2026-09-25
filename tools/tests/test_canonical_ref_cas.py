"""Integration tests for the bounded Day Zero canonical-ref CAS."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools" / "canonical_ref_cas.py"
AUTO_REMOTE_EXPECTATION = object()
WINDOW_OWNER = "packet-d-window-test"
sys.path.insert(0, str(CLI.parent))
import canonical_ref_cas as cas

EXPECTED_PROCESS_INJECTION_ENV_NAMES = frozenset(
    {
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
    }
)
EXPECTED_PROCESS_INJECTION_ENV_PREFIXES = ("LD_",)
EXPECTED_GIT_CONFIG_ENV_NAMES = frozenset(
    {
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
    }
)
EXPECTED_GIT_CONFIG_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
URL_USERINFO_REDACTION_CASES = (
    (
        "https://operator@example.invalid/repository.git",
        "https://example.invalid/repository.git",
    ),
    (
        "operator@example.invalid:repository.git",
        "example.invalid:repository.git",
    ),
    (
        "operator@example.invalid:repository@archive",
        "[redacted]",
    ),
)
SCHEMELESS_USERINFO_CASES = (
    "user-info:masked-value@example.invalid",
    "user-info:masked-value@example.invalid/org/repository.git",
)


def _test_subprocess_environment(**updates: str) -> dict[str, str]:
    environment = {
        "LC_ALL": "C",
        "PATH": os.defpath,
    }
    environment.update(updates)
    return environment


def _test_git_environment(**updates: str) -> dict[str, str]:
    environment = _test_subprocess_environment(**updates)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )
    return environment


@pytest.fixture(autouse=True)
def isolate_test_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep in-process calls independent of the pytest runner's environment."""
    git_config_names = EXPECTED_GIT_CONFIG_ENV_NAMES | cas.GIT_CONFIG_ENV_NAMES
    git_config_prefixes = (
        *EXPECTED_GIT_CONFIG_ENV_PREFIXES,
        *cas.GIT_CONFIG_ENV_PREFIXES,
    )
    for name in tuple(os.environ):
        if (
            name in EXPECTED_PROCESS_INJECTION_ENV_NAMES
            or name.startswith(EXPECTED_PROCESS_INJECTION_ENV_PREFIXES)
            or name in git_config_names
            or name.startswith(git_config_prefixes)
        ):
            monkeypatch.delenv(name, raising=False)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [cas.GIT_PATH, *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        env=_test_git_environment(),
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
    expected_remote_url_sha256: str | None | object = AUTO_REMOTE_EXPECTATION,
    receipt_path: Path | None = None,
    close_stdout: bool = False,
    window_owner: str | None = WINDOW_OWNER,
):
    if env is None:
        env = _test_subprocess_environment()
    configured_remote_url = git(
        repositories.daemon, "remote", "get-url", "origin"
    ).stdout.strip()
    if expected_remote_url_sha256 is AUTO_REMOTE_EXPECTATION:
        expected_remote_url_sha256 = hashlib.sha256(
            configured_remote_url.encode("utf-8")
        ).hexdigest()
    if receipt_path is None:
        sequence = len(list(repositories.remote.parent.glob("cas-receipt-*.json")))
        receipt_path = repositories.remote.parent / f"cas-receipt-{sequence}.json"
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
        "--receipt-path",
        str(receipt_path),
    ]
    if window_owner is not None:
        command.extend(["--window-owner", window_owner])
    if expected_remote_url_sha256 is not None:
        command.extend(
            ["--expect-remote-url-sha256", str(expected_remote_url_sha256)]
        )
    if dry_run:
        command.append("--dry-run")
    if close_stdout:
        return subprocess.run(
            command,
            cwd=ROOT,
            stdout=None,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            preexec_fn=lambda: os.close(1),
        )
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env)


def receipt(completed: subprocess.CompletedProcess[str]) -> dict:
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    return json.loads(stream)


def _receipt_string_values(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _receipt_string_values(key)
            yield from _receipt_string_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _receipt_string_values(nested)
    elif isinstance(value, str):
        yield value


def assert_receipt_contains_no_url_userinfo(result: dict) -> None:
    for value in _receipt_string_values(result):
        assert (
            re.search(
                r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/?#]*@[^\s/?#]+",
                value,
            )
            is None
        )
        assert (
            re.search(
                r"(?<![A-Za-z0-9._+-])[A-Za-z0-9._+-]+@[^/:\s]+:[^\s]+",
                value,
            )
            is None
        )
        assert (
            re.search(
                r"(?<![A-Za-z0-9._+-])[A-Za-z0-9._+-]+:[^\s/@]+@[^\s/@]+",
                value,
            )
            is None
        )


def remote_url_sha256(repositories: Repositories) -> str:
    remote_url = git(
        repositories.daemon, "remote", "get-url", "origin"
    ).stdout.strip()
    return hashlib.sha256(remote_url.encode("utf-8")).hexdigest()


def assert_tool_run_identity(result: dict) -> None:
    assert result["tool"] == {
        "path": str(CLI),
        "sha256": hashlib.sha256(CLI.read_bytes()).hexdigest(),
    }
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
        result["timestamp_utc"],
    )


def assert_receipt_identity(result: dict, repositories: Repositories) -> None:
    assert_tool_run_identity(result)
    assert result["repo"] == str(repositories.daemon.resolve())
    assert result["remote"] == "origin"
    assert result["branch"] == "main"
    assert result["window_owner"] == WINDOW_OWNER
    assert result["host_identity"] == os.uname().nodename


def receipt_without_identity(result: dict, repositories: Repositories) -> dict:
    assert_receipt_identity(result, repositories)
    stripped = dict(result)
    for name in (
        "tool",
        "repo",
        "remote",
        "branch",
        "timestamp_utc",
        "window_owner",
        "host_identity",
    ):
        stripped.pop(name)
    return stripped


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
    assert receipt_without_identity(receipt(completed), repositories) == {
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
        "expected_remote_url_sha256": remote_url_sha256(repositories),
        "result": "success",
        "transition_outcome": "succeeded",
        "verb": "forward",
    }
    assert sha(repositories.daemon, "refs/heads/main") == repositories.candidate
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_success_receipt_carries_verifier_and_operation_identity(
    repositories: Repositories,
):
    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    assert_receipt_identity(receipt(completed), repositories)


def test_missing_remote_url_expectation_is_refused_before_mutation(
    repositories: Repositories,
):
    completed = invoke(
        repositories,
        "forward",
        expected_remote_url_sha256=None,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "--expect-remote-url-sha256 is required"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_missing_window_owner_is_refused_before_mutation(
    repositories: Repositories,
):
    completed = invoke(repositories, "forward", window_owner=None)

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error"] == "--window-owner is required"
    assert failure["error_code"] == "invalid-window-owner"
    assert failure["window_owner"] is None
    assert failure["host_identity"] == os.uname().nodename
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


@pytest.mark.parametrize(
    "invalid_owner",
    [
        pytest.param(" leading-space", id="leading-space"),
        pytest.param("x" * 257, id="too-long"),
        pytest.param("control\x07character", id="control-character"),
        pytest.param("ops@window-host:packet-d-1", id="userinfo-separator"),
    ],
)
def test_invalid_window_owner_format_is_refused_before_mutation(
    repositories: Repositories,
    invalid_owner: str,
):
    completed = invoke(
        repositories,
        "forward",
        window_owner=invalid_owner,
    )

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error"] == (
        "--window-owner must be a non-empty printable value "
        "of at most 256 characters without '@'"
    )
    assert failure["error_code"] == "invalid-window-owner"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


@pytest.mark.parametrize(
    "invalid_digest",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64],
)
def test_malformed_remote_url_expectation_is_refused_before_mutation(
    repositories: Repositories,
    invalid_digest: str,
):
    completed = invoke(
        repositories,
        "forward",
        expected_remote_url_sha256=invalid_digest,
    )

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error_code"] == "invalid-remote-expectation"
    assert failure["mutations"] == []
    assert failure["readback"] == {"head": None, "local": None, "remote": None}
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


@pytest.mark.parametrize(
    "environment_name",
    sorted(cas.PROCESS_INJECTION_ENV_NAMES)
    + sorted(
        {
            "LD_PRELOAD",
            "LD_AUDIT",
            "LD_LIBRARY_PATH",
            *(
                f"{prefix}ARBITRARY_FAMILY_MEMBER"
                for prefix in cas.PROCESS_INJECTION_ENV_PREFIXES
            ),
        }
    ),
)
def test_forward_refuses_process_injection_environment_before_mutation(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
):
    monkeypatch.setenv(environment_name, "")

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    failure = captured.value.receipt
    assert failure["result"] == "error"
    assert failure["error"] == "process injection environment is not permitted"
    assert failure["error_code"] == "unsafe-process-environment"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_process_injection_environment_contract_matches_behavior_matrix():
    assert cas.PROCESS_INJECTION_ENV_NAMES == EXPECTED_PROCESS_INJECTION_ENV_NAMES
    assert (
        cas.PROCESS_INJECTION_ENV_PREFIXES
        == EXPECTED_PROCESS_INJECTION_ENV_PREFIXES
    )


@pytest.mark.parametrize("field", ["repo", "remote", "branch"])
def test_invalid_operation_identity_is_not_echoed_in_failure_receipt(
    repositories: Repositories,
    field: str,
):
    secret = "do-not-echo-secret-shaped-value"
    arguments = {
        "repo": repositories.daemon,
        "remote": "origin",
        "branch": "main",
    }
    arguments[field] = secret

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            arguments["repo"],
            arguments["remote"],
            arguments["branch"],
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    failure = captured.value.receipt
    assert_tool_run_identity(failure)
    assert failure["repo"] is None
    assert failure["remote"] is None
    assert failure["branch"] is None
    assert secret not in json.dumps(failure, sort_keys=True)


def test_mismatched_remote_url_expectation_is_refused_before_mutation(
    repositories: Repositories,
):
    canonical_remote_url_sha256 = remote_url_sha256(repositories)
    decoy = repositories.remote.parent / "decoy.git"
    git(decoy.parent, "init", "-q", "--bare", str(decoy))
    git(
        repositories.seed,
        "push",
        "-q",
        str(decoy),
        f"{repositories.prior}:refs/heads/main",
    )
    git(decoy, "symbolic-ref", "HEAD", "refs/heads/main")
    git(repositories.daemon, "remote", "set-url", "origin", str(decoy))

    completed = invoke(
        repositories,
        "forward",
        expected_remote_url_sha256=canonical_remote_url_sha256,
    )

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "configured remote URL does not match operator expectation"
    assert failure["mutations"] == []
    assert failure["remote_url_sha256"] == hashlib.sha256(
        str(decoy).encode("utf-8")
    ).hexdigest()
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(decoy, "refs/heads/main") == repositories.prior


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_relative_remote_url_is_refused_before_mutation(
    repositories: Repositories,
    verb: str,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    relative_remote = os.path.relpath(repositories.remote, repositories.daemon)
    git(repositories.daemon, "remote", "set-url", "origin", relative_remote)

    completed = invoke(repositories, verb)

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error"] == "local remote URL must be absolute"
    expected_sha = (
        repositories.prior if verb == "forward" else repositories.candidate
    )
    assert sha(repositories.daemon) == expected_sha
    assert sha(repositories.remote, "refs/heads/main") == expected_sha


def test_daemon_local_remote_url_rewrite_is_refused_before_mutation(
    repositories: Repositories,
):
    decoy = repositories.remote.parent / "local-rewrite-decoy.git"
    git(decoy.parent, "init", "-q", "--bare", str(decoy))
    git(
        repositories.seed,
        "push",
        "-q",
        str(decoy),
        f"{repositories.prior}:refs/heads/main",
    )
    git(
        repositories.daemon,
        "config",
        f"url.{decoy}.insteadOf",
        str(repositories.remote),
    )

    completed = invoke(repositories, "forward")

    assert completed.returncode == 1
    assert receipt(completed)["error"] == (
        "daemon repository must not configure remote URL rewrites"
    )
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(decoy, "refs/heads/main") == repositories.prior


def test_daemon_worktree_remote_url_rewrite_is_refused_before_mutation(
    repositories: Repositories,
):
    decoy = repositories.remote.parent / "worktree-rewrite-decoy.git"
    git(decoy.parent, "init", "-q", "--bare", str(decoy))
    git(
        repositories.seed,
        "push",
        "-q",
        str(decoy),
        f"{repositories.prior}:refs/heads/main",
    )
    git(repositories.daemon, "config", "extensions.worktreeConfig", "true")
    git(
        repositories.daemon,
        "config",
        "--worktree",
        f"url.{decoy}.insteadOf",
        str(repositories.remote),
    )

    completed = invoke(repositories, "forward")

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error"] == (
        "daemon repository must not configure remote URL rewrites"
    )
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(decoy, "refs/heads/main") == repositories.prior


def test_closed_stdout_preserves_receipt_at_required_path(
    repositories: Repositories,
):
    receipt_path = repositories.remote.parent / "closed-stdout-receipt.json"

    completed = invoke(
        repositories,
        "forward",
        receipt_path=receipt_path,
        close_stdout=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["result"] == "success"
    assert result["readback"]["remote"] == repositories.candidate
    assert_receipt_identity(result, repositories)
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_existing_receipt_path_is_refused_before_mutation(
    repositories: Repositories,
):
    receipt_path = repositories.remote.parent / "existing-receipt.json"
    receipt_path.write_text("existing evidence\n", encoding="utf-8")

    completed = invoke(repositories, "forward", receipt_path=receipt_path)

    assert completed.returncode == 1
    assert receipt_path.read_text(encoding="utf-8") == "existing evidence\n"
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_retry_after_consumed_receipt_path_records_target_already_present(
    repositories: Repositories,
):
    first = invoke(repositories, "forward")
    assert first.returncode == 0, first.stderr
    consumed_path = repositories.remote.parent / "consumed-receipt.json"
    consumed_path.touch(mode=0o600)
    retry_path = repositories.remote.parent / "retry-receipt.json"

    refused = invoke(
        repositories,
        "forward",
        receipt_path=consumed_path,
    )
    retry = invoke(
        repositories,
        "forward",
        receipt_path=retry_path,
    )

    assert refused.returncode == 1
    assert consumed_path.read_bytes() == b""
    assert retry.returncode == 1
    result = json.loads(retry_path.read_text(encoding="utf-8"))
    assert result["result"] == "error"
    assert result["transition_outcome"] == "target-already-present"
    assert result["mutations"] == []
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }


def test_write_all_retries_partial_writes(monkeypatch: pytest.MonkeyPatch):
    writes: list[bytes] = []

    def partial_write(_descriptor: int, payload: bytes) -> int:
        amount = min(3, len(payload))
        writes.append(payload[:amount])
        return amount

    monkeypatch.setattr(cas.os, "write", partial_write)

    cas._write_all(123, b"complete receipt")

    assert b"".join(writes) == b"complete receipt"


@pytest.mark.parametrize("written", [0, -1])
def test_write_all_refuses_nonprogressing_write(
    monkeypatch: pytest.MonkeyPatch,
    written: int,
):
    monkeypatch.setattr(cas.os, "write", lambda _descriptor, _payload: written)

    with pytest.raises(OSError):
        cas._write_all(123, b"receipt")


def test_receipt_publish_refuses_replaced_private_temp_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "receipt.json"
    (
        receipt_descriptor,
        directory_descriptor,
        temporary_filename,
        filename,
    ) = cas._open_receipt(str(target))
    original_fsync = cas.os.fsync
    swapped = False

    def replace_temp_after_file_sync(file_descriptor: int) -> None:
        nonlocal swapped
        original_fsync(file_descriptor)
        if file_descriptor != receipt_descriptor or swapped:
            return
        cas.os.unlink(temporary_filename, dir_fd=directory_descriptor)
        replacement_descriptor = cas.os.open(
            temporary_filename,
            cas.os.O_WRONLY | cas.os.O_CREAT | cas.os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        try:
            cas.os.write(replacement_descriptor, b'{"forged":true}\n')
            original_fsync(replacement_descriptor)
        finally:
            cas.os.close(replacement_descriptor)
        swapped = True

    monkeypatch.setattr(cas.os, "fsync", replace_temp_after_file_sync)

    try:
        with pytest.raises(OSError):
            cas._write_receipt(
                receipt_descriptor,
                directory_descriptor,
                temporary_filename,
                filename,
                b'{"authentic":true}\n',
            )
    finally:
        try:
            cas.os.unlink(temporary_filename, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        cas.os.close(receipt_descriptor)
        cas.os.close(directory_descriptor)

    assert swapped
    assert not target.exists()


def test_unwritable_receipt_path_fails_before_mutation(
    repositories: Repositories,
):
    receipt_path = repositories.remote.parent / "missing" / "receipt.json"

    completed = invoke(
        repositories,
        "forward",
        receipt_path=receipt_path,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "canonical-ref CAS receipt file could not be opened\n"
    assert not receipt_path.exists()
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_outermost_fallback_does_not_assert_state_after_landed_operation(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    receipt_path = repositories.remote.parent / "unexpected-error-receipt.json"
    expected_remote_url_sha256 = remote_url_sha256(repositories)

    original_forward = cas.forward

    def raising_forward(*args, **kwargs):
        landed = original_forward(*args, **kwargs)
        assert landed["transition_outcome"] == "succeeded"
        raise RuntimeError("unexpected test failure")

    monkeypatch.setattr(cas, "forward", raising_forward)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            expected_remote_url_sha256,
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    assert result_code == 1
    failure = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert failure["result"] == "error"
    assert failure["error"] == "unexpected internal failure"
    assert failure["error_code"] == "unexpected-exception"
    assert failure["transition_outcome"] == "undetermined"
    assert failure["transition_outcome_source"] == "outermost-fallback"
    assert failure["mutations"] is None
    assert failure["readback"] == {"head": None, "local": None, "remote": None}
    assert failure["expected"] == {
        "from": repositories.prior,
        "to": repositories.candidate,
    }
    assert failure["expected_remote_url_sha256"] == expected_remote_url_sha256
    assert failure["window_owner"] == WINDOW_OWNER
    assert failure["repo"] == str(repositories.daemon)
    assert failure["remote"] == "origin"
    assert failure["branch"] == "main"
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_execute_converts_interruption_to_landed_receipt(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    interruption: type[BaseException],
):
    def interrupt_remote_tracking(*_args, **_kwargs):
        raise interruption

    monkeypatch.setattr(cas, "_cas_remote_tracking_main", interrupt_remote_tracking)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    result = captured.value.receipt
    assert result["result"] == "warning"
    assert result["error_code"] == "interrupted"
    assert result["warning"] == "operation interrupted"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert sha(repositories.remote, "refs/heads/main") == repositories.candidate


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_main_writes_receipt_for_interruption_without_traceback(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    interruption: type[BaseException],
):
    receipt_path = repositories.remote.parent / "interrupt-receipt.json"

    def raising_forward(*_args, **_kwargs):
        raise interruption

    monkeypatch.setattr(cas, "forward", raising_forward)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            remote_url_sha256(repositories),
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    captured = capfd.readouterr()
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result_code == 130
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert json.loads(captured.err) == result
    assert result["result"] == "error"
    assert result["error"] == "operation interrupted"
    assert result["error_code"] == "interrupted"
    assert result["transition_outcome"] == "undetermined"
    assert result["transition_outcome_source"] == "outermost-fallback"
    assert result["mutations"] is None


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_main_bounds_interruption_during_receipt_publication(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    interruption: type[BaseException],
):
    receipt_path = repositories.remote.parent / "publication-interrupt.json"

    def interrupt_publication(*_args, **_kwargs):
        raise interruption

    monkeypatch.setattr(cas, "_write_receipt", interrupt_publication)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            remote_url_sha256(repositories),
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    captured = capfd.readouterr()
    mirrored_payload, diagnostic = captured.err.splitlines()
    result = json.loads(mirrored_payload)
    assert result_code == 130
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert diagnostic == "canonical-ref CAS receipt publication interrupted"
    assert result["result"] == "success"
    assert result["transition_outcome"] == "succeeded"
    assert not receipt_path.exists()
    assert not list(receipt_path.parent.glob(f".{receipt_path.name}.*.tmp"))
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_unexpected_post_mutation_exception_writes_complete_warning_receipt(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    receipt_path = repositories.remote.parent / "post-mutation-exception.json"
    expected_remote_url_sha256 = remote_url_sha256(repositories)
    original_final_state = cas._require_final_state

    def raise_after_final_readback(*args, **kwargs):
        original_final_state(*args, **kwargs)
        raise RuntimeError("unbounded internal detail must not escape")

    monkeypatch.setattr(cas, "_require_final_state", raise_after_final_readback)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            expected_remote_url_sha256,
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    assert result_code == 1
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["result"] == "warning"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["warning"] == "unexpected internal failure"
    assert "unbounded internal detail" not in json.dumps(result, sort_keys=True)
    assert result["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_failure_handler_fallback_preserves_landed_mutation_evidence(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    receipt_path = repositories.remote.parent / "failure-handler-interrupt.json"
    configured_url = f"file://operator@localhost{repositories.remote}"
    git(repositories.daemon, "remote", "set-url", "origin", configured_url)
    original_push_main = cas._push_main
    original_transition_outcome = cas._transition_outcome

    def committed_then_client_failure(*args, **kwargs):
        original_push_main(*args, **kwargs)
        raise cas.CasError("Git operation failed during remote main compare-and-swap")

    def interrupt_failure_classification(*args, **kwargs):
        if kwargs.get("unexpected_observation"):
            raise KeyboardInterrupt
        return original_transition_outcome(*args, **kwargs)

    monkeypatch.setattr(cas, "_push_main", committed_then_client_failure)
    monkeypatch.setattr(cas, "_transition_outcome", interrupt_failure_classification)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            remote_url_sha256(repositories),
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    assert result_code == 130
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["result"] == "error"
    assert result["error"] == "operation interrupted"
    assert result["error_code"] == "interrupted"
    assert result["transition_outcome"] == "undetermined"
    assert result["transition_outcome_source"] == "failure-handler-fallback"
    assert result["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
    ]
    assert result["mutation_reconciliation"] == {
        "remote-main-cas": "confirmed-by-post-failure-readback"
    }
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }
    assert_receipt_contains_no_url_userinfo(result)
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_landed_forward_with_remote_tracking_failure_is_not_demoted(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_remote_tracking(*_args, **_kwargs):
        raise cas.CasError("remote-tracking main compare-and-swap failed")

    monkeypatch.setattr(cas, "_cas_remote_tracking_main", fail_remote_tracking)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    result = captured.value.receipt
    assert result["result"] == "warning"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }
    assert result["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
    ]


def test_complete_landing_with_unavailable_remote_readback_is_not_demoted(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_final_state(*_args, **_kwargs):
        raise cas.CasError("final verification unavailable")

    def fail_remote_readback(*_args, **_kwargs):
        raise cas.CasError("remote readback unavailable")

    monkeypatch.setattr(cas, "_require_final_state", fail_final_state)
    monkeypatch.setattr(cas, "_remote_sha", fail_remote_readback)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    result = captured.value.receipt
    assert result["result"] == "warning"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": None,
    }
    assert result["readback_errors"] == {
        "remote": "remote readback unavailable",
    }
    assert result["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]


def test_receipt_directory_sync_failure_returns_error_after_completed_cas(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
):
    receipt_path = repositories.remote.parent / "directory-sync-failure.json"
    expected_remote_url_sha256 = remote_url_sha256(repositories)
    original_fsync = cas.os.fsync
    fsync_calls = 0

    def fail_directory_sync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("injected directory sync failure")
        original_fsync(file_descriptor)

    monkeypatch.setattr(cas.os, "fsync", fail_directory_sync)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            expected_remote_url_sha256,
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    captured = capfd.readouterr()
    mirrored_payload, diagnostic = captured.err.splitlines()
    assert result_code == 1
    assert captured.out == ""
    assert diagnostic == "canonical-ref CAS receipt file could not be written"
    assert json.loads(mirrored_payload) == json.loads(
        receipt_path.read_text(encoding="utf-8")
    )
    assert fsync_calls == 2
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_partial_receipt_write_leaves_no_target_and_mirrors_complete_payload(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
):
    receipt_path = repositories.remote.parent / "partial-write-receipt.json"
    original_write = cas.os.write
    receipt_write_calls = 0

    def partial_then_full_disk(file_descriptor: int, payload: bytes) -> int:
        nonlocal receipt_write_calls
        if file_descriptor not in {1, 2}:
            receipt_write_calls += 1
            if receipt_write_calls == 1:
                return min(40, len(payload))
            raise OSError(28, "injected full disk")
        return original_write(file_descriptor, payload)

    monkeypatch.setattr(cas.os, "write", partial_then_full_disk)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            remote_url_sha256(repositories),
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    captured = capfd.readouterr()
    mirrored_payload, diagnostic = captured.err.splitlines()
    result = json.loads(mirrored_payload)
    assert result_code == 1
    assert captured.out == ""
    assert diagnostic == "canonical-ref CAS receipt file could not be written"
    assert result["result"] == "success"
    assert result["transition_outcome"] == "succeeded"
    assert receipt_write_calls == 2
    assert not receipt_path.exists()
    assert not list(receipt_path.parent.glob(f".{receipt_path.name}.*.tmp"))
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_receipt_publication_collision_preserves_competing_target(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
):
    receipt_path = repositories.remote.parent / "publication-collision.json"
    original_link = cas.os.link

    def competing_link(*args, **kwargs):
        receipt_path.write_text("competing receipt\n", encoding="utf-8")
        return original_link(*args, **kwargs)

    monkeypatch.setattr(cas.os, "link", competing_link)

    result_code = cas.main(
        [
            "forward",
            "--repo",
            str(repositories.daemon),
            "--remote",
            "origin",
            "--branch",
            "main",
            "--from",
            repositories.prior,
            "--to",
            repositories.candidate,
            "--expect-remote-url-sha256",
            remote_url_sha256(repositories),
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    captured = capfd.readouterr()
    mirrored_payload, diagnostic = captured.err.splitlines()
    result = json.loads(mirrored_payload)
    assert result_code == 1
    assert captured.out == ""
    assert diagnostic == "canonical-ref CAS receipt file could not be written"
    assert result["result"] == "success"
    assert result["transition_outcome"] == "succeeded"
    assert receipt_path.read_text(encoding="utf-8") == "competing receipt\n"
    assert not list(receipt_path.parent.glob(f".{receipt_path.name}.*.tmp"))
    assert sha(repositories.daemon) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_closed_standard_streams_cannot_alias_receipt_diagnostic(
    repositories: Repositories,
):
    receipt_path = repositories.remote.parent / "closed-streams-write-failure.json"
    command_arguments = [
        "forward",
        "--repo",
        str(repositories.daemon),
        "--remote",
        "origin",
        "--branch",
        "main",
        "--from",
        repositories.prior,
        "--to",
        repositories.candidate,
        "--expect-remote-url-sha256",
        remote_url_sha256(repositories),
        "--window-owner",
        WINDOW_OWNER,
        "--receipt-path",
        str(receipt_path),
    ]
    child = (
        "import os,sys;"
        f"sys.path.insert(0,{str(CLI.parent)!r});"
        "import canonical_ref_cas as cas;"
        "cas._write_receipt=lambda *_args,**_kwargs:(_ for _ in ()).throw(OSError());"
        "cas._best_effort_unlink=lambda *_args,**_kwargs:None;"
        "os.close(1);os.close(2);"
        f"os._exit(cas.main({command_arguments!r}))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", child],
        cwd=ROOT,
        env=_test_subprocess_environment(),
    )

    assert completed.returncode == 1
    assert not receipt_path.exists()
    temporary_receipts = list(
        receipt_path.parent.glob(f".{receipt_path.name}.*.tmp")
    )
    assert len(temporary_receipts) == 1
    assert temporary_receipts[0].read_bytes() == b""
    temporary_receipts[0].unlink()
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


def test_checked_out_clean_main_requires_remote_to_equal_from(
    repositories: Repositories,
):
    moved = advance_remote(repositories)
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    with pytest.raises(cas.CasError, match="remote main does not equal --from"):
        cas._require_checked_out_clean_main(
            operation,
            repositories.prior,
            str(repositories.remote),
        )

    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == moved


def test_baseline_snapshot_requires_remote_to_equal_from_without_earlier_guard(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    moved = advance_remote(repositories)
    monkeypatch.setattr(cas, "_require_checked_out_clean_main", lambda *_args: None)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    failure = captured.value.receipt
    assert failure["error"] == "remote main does not equal --from"
    assert failure["mutations"] == []
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
    assert receipt_without_identity(receipt(completed), repositories) == {
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
        "expected_remote_url_sha256": remote_url_sha256(repositories),
        "result": "success",
        "transition_outcome": "succeeded",
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
    receipt_path = repositories.remote.parent / "adapter-restore-success.json"
    adapter = cas.CanonicalRefCasAdapter(
        repositories.daemon,
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
        receipt_path=receipt_path,
    )

    observed = adapter.restore_canonical_ref_exact(
        repositories.candidate, repositories.prior
    )

    assert observed == repositories.prior
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["result"] == "success"
    assert result["transition_outcome"] == "succeeded"
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_restore_adapter_writes_failure_receipt_before_reraising(
    repositories: Repositories,
):
    receipt_path = repositories.remote.parent / "adapter-restore-failure.json"
    adapter = cas.CanonicalRefCasAdapter(
        repositories.daemon,
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
        receipt_path=receipt_path,
    )

    with pytest.raises(cas.CasFailure) as captured:
        adapter.restore_canonical_ref_exact(
            repositories.candidate, repositories.prior
        )

    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result == captured.value.receipt
    assert result["result"] == "error"
    assert result["mutations"] == []
    assert receipt_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_restore_adapter_writes_post_mutation_interruption_receipt(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    interruption: type[BaseException],
):
    assert invoke(repositories, "forward").returncode == 0
    receipt_path = repositories.remote.parent / "adapter-interruption.json"

    def interrupt_remote_tracking(*_args, **_kwargs):
        raise interruption

    monkeypatch.setattr(cas, "_cas_remote_tracking_main", interrupt_remote_tracking)
    adapter = cas.CanonicalRefCasAdapter(
        repositories.daemon,
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
        receipt_path=receipt_path,
    )

    with pytest.raises(cas.CasFailure) as captured:
        adapter.restore_canonical_ref_exact(
            repositories.candidate, repositories.prior
        )

    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result == captured.value.receipt
    assert result["result"] == "warning"
    assert result["error_code"] == "interrupted"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["mutations"] == [
        "remote-main-cas",
        "local-main-cas",
        "worktree-alignment",
    ]
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


@pytest.mark.parametrize(
    ("publication_exception", "expected_message", "expected_error_code"),
    [
        (OSError, "canonical-ref CAS receipt file could not be written", None),
        (
            KeyboardInterrupt,
            "canonical-ref CAS receipt publication interrupted",
            "interrupted",
        ),
        (
            SystemExit,
            "canonical-ref CAS receipt publication interrupted",
            "interrupted",
        ),
    ],
)
def test_restore_adapter_mirrors_receipt_publication_failure(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    publication_exception: type[BaseException],
    expected_message: str,
    expected_error_code: str | None,
):
    assert invoke(repositories, "forward").returncode == 0
    receipt_path = repositories.remote.parent / "adapter-publication-failure.json"

    def fail_publication(*_args, **_kwargs):
        raise publication_exception

    monkeypatch.setattr(cas, "_write_receipt", fail_publication)
    adapter = cas.CanonicalRefCasAdapter(
        repositories.daemon,
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
        receipt_path=receipt_path,
    )

    with pytest.raises(cas.CasError) as captured:
        adapter.restore_canonical_ref_exact(
            repositories.candidate, repositories.prior
        )

    streams = capfd.readouterr()
    mirrored_payload, diagnostic = streams.err.splitlines()
    result = json.loads(mirrored_payload)
    assert str(captured.value) == expected_message
    assert captured.value.error_code == expected_error_code
    assert streams.out == ""
    assert diagnostic == expected_message
    assert result["result"] == "success"
    assert result["transition_outcome"] == "succeeded"
    assert not receipt_path.exists()
    assert not list(receipt_path.parent.glob(f".{receipt_path.name}.*.tmp"))
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_restore_adapter_preserves_ledger_when_reconciliation_is_interrupted(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    assert invoke(repositories, "forward").returncode == 0
    receipt_path = repositories.remote.parent / "adapter-reconciliation-interrupt.json"
    original_push_main = cas._push_main

    def committed_then_client_failure(*args, **kwargs):
        original_push_main(*args, **kwargs)
        raise cas.CasError("Git operation failed during remote main compare-and-swap")

    def interrupt_remote_readback(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cas, "_push_main", committed_then_client_failure)
    monkeypatch.setattr(cas, "_remote_sha", interrupt_remote_readback)
    adapter = cas.CanonicalRefCasAdapter(
        repositories.daemon,
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
        receipt_path=receipt_path,
    )

    with pytest.raises(cas.CasFailure) as captured:
        adapter.restore_canonical_ref_exact(
            repositories.candidate, repositories.prior
        )

    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result == captured.value.receipt
    assert result["transition_outcome_source"] == "post-failure-readback"
    assert result["transition_outcome"] == "incomplete"
    assert result["mutations"] == []
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": None,
    }
    assert result["readback_errors"] == {
        "remote": "remote readback unavailable"
    }
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior


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
    forward_result = receipt(forward)
    assert forward_result["transition_outcome"] == "not-attempted"
    assert forward_result["readback"] == {
        "head": repositories.prior,
        "local": repositories.prior,
        "remote": repositories.prior,
    }
    assert forward_result["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior

    assert invoke(repositories, "forward").returncode == 0
    restore = invoke(repositories, "restore", dry_run=True)

    assert restore.returncode == 0, restore.stderr
    restore_result = receipt(restore)
    assert restore_result["transition_outcome"] == "not-attempted"
    assert restore_result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }
    assert restore_result["mutations"] == []
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


def test_disposable_index_detects_content_hidden_by_daemon_stat_cache(
    repositories: Repositories,
):
    tracked = repositories.daemon / "state.txt"
    original = tracked.stat()
    old_mtime_ns = original.st_mtime_ns - 2_000_000_000
    os.utime(tracked, ns=(original.st_atime_ns, old_mtime_ns))
    git(repositories.daemon, "config", "core.trustctime", "false")
    git(repositories.daemon, "update-index", "--refresh")
    cached = tracked.stat()
    tracked.write_text("alter\n", encoding="utf-8")
    os.utime(tracked, ns=(cached.st_atime_ns, cached.st_mtime_ns))
    control = git(
        repositories.daemon,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.fileMode=true",
        "-c",
        "core.symlinks=true",
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    assert control.stdout == ""

    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )
    with pytest.raises(
        cas.CasError,
        match="daemon tracked content does not equal HEAD",
    ):
        cas._require_exact_cleanliness(operation)


def test_cleanliness_enumerates_files_inside_untracked_directories(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    nested = repositories.daemon / "untracked" / "nested"
    nested.mkdir(parents=True)
    (nested / "evidence.txt").write_text("untracked\n", encoding="utf-8")
    original_run_git = cas._run_git
    status_args: list[str] | None = None

    def recording_run_git(operation, args, **kwargs):
        nonlocal status_args
        if kwargs["stage"] == "daemon worktree cleanliness":
            status_args = list(args)
        return original_run_git(operation, args, **kwargs)

    monkeypatch.setattr(cas, "_run_git", recording_run_git)
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    with pytest.raises(cas.CasError, match="daemon worktree must be clean"):
        cas._require_exact_cleanliness(operation)

    assert status_args is not None
    assert "--untracked-files=all" in status_args


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


def test_runbook_pins_cas_release_identity_invocation_and_receipt_acceptance():
    documentation = (ROOT / "docs" / "day-zero-migration-operations.md").read_text(
        encoding="utf-8"
    )
    normalized_prose = re.sub(r"\s+", " ", documentation)

    for required_text in (
        "OPS_REPO=/absolute/path/to/the/reviewed/operations-checkout",
        'CAS="$OPS_REPO/tools/canonical_ref_cas.py"',
        "REVIEWED_OPS_COMMIT=",
        "REVIEWED_CAS_SHA256=",
        "EXPECTED_REMOTE_URL_SHA256=",
        "WINDOW_OWNER=",
        'HOST_IDENTITY=$(/usr/bin/env -i /usr/bin/uname -n)',
        "set -eu",
        'for INJECTION_NAME in ${!LD_@}; do unset "$INJECTION_NAME"; done',
        "unset OPENSSL_CONF OPENSSL_MODULES",
        "unset PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP",
        "PYTHONUSERBASE",
        'trusted_git -C "$OPS_REPO" rev-parse --verify HEAD',
        '"$REVIEWED_OPS_COMMIT:tools/canonical_ref_cas.py"',
        'trusted_git -C "$OPS_REPO" hash-object -- "$CAS"',
        '/usr/bin/env -i /usr/bin/readlink -f -- "$CAS"',
        'ACTUAL_CAS_SHA256=$(/usr/bin/env -i /usr/bin/sha256sum -- "$CAS")',
        "/usr/bin/env -i /usr/bin/test -x /usr/bin/python3",
        '/usr/bin/env -i /usr/bin/mkdir "$RECEIPT_DIR"',
        'os.confstr("CS_PATH")',
        '/usr/bin/env -i /usr/bin/python3 -I "$CAS" forward',
        '/usr/bin/env -i /usr/bin/python3 -I "$CAS" restore',
        '--expect-remote-url-sha256 "$EXPECTED_REMOTE_URL_SHA256"',
        '--window-owner "$WINDOW_OWNER"',
        "--receipt-path",
        'tool.sha256` is `$REVIEWED_CAS_SHA256',
        '`window_owner` equals `$WINDOW_OWNER`',
        '`host_identity` equals `$HOST_IDENTITY`',
        "`verb` equals the command verb",
        "`dry_run` is `true` exactly for a rehearsal",
        '`transition_outcome_source` is `"post-failure-readback"`',
        '`"failure-handler-fallback"`',
        '`"outermost-fallback"`',
        "unvalidated echoes of the command input",
        '`mutation_reconciliation.remote-main-cas`',
        '`"confirmed-by-post-failure-readback"`',
        "`readback_errors`",
        "`landed-verification-incomplete`",
        "`target-already-present`",
        'Exit `0` alone never accepts any of the four CAS commands',
    ):
        assert required_text in documentation or required_text in normalized_prose
    assert "the production window must not open" in normalized_prose
    assert documentation.count('--window-owner "$WINDOW_OWNER"') == 4
    assert "python3 tools/canonical_ref_cas.py" not in documentation
    outcome_rules = documentation.split(
        "Every possible `transition_outcome` has an operator rule:", 1
    )[1].split(
        "\nOn any normally handled failure after operation validation", 1
    )[0]
    documented_outcomes = set(
        re.findall(r"^- `([^`]+)`:", outcome_rules, flags=re.MULTILINE)
    )
    assert documented_outcomes == cas.TRANSITION_OUTCOMES
    assert (
        "If the remote readback is neither `--from` nor `--to`, a third party "
        "moved production after this command's CAS"
    ) in normalized_prose


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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
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
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
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
    result = receipt(completed)
    assert result["result"] == "warning"
    assert result["warning"] == "remote ref map changed outside canonical main"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
    assert result["readback"] == {
        "head": repositories.candidate,
        "local": repositories.candidate,
        "remote": repositories.candidate,
    }
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
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert retargeted
    result = captured.value.receipt
    assert result["result"] == "warning"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["warning"] == (
        "validated remote URL changed during operation"
    )
    assert sha(repositories.remote, "refs/heads/main") == repositories.candidate
    assert sha(decoy, "refs/heads/main") == repositories.prior


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_remote_compare_and_swap_refuses_mid_operation_competing_commit(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    original_push_main = cas._push_main
    raced = False
    competitor: str | None = None

    def racing_push_main(*args, **kwargs):
        nonlocal raced, competitor
        if not raced:
            raced = True
            competitor = advance_remote(repositories)
        return original_push_main(*args, **kwargs)

    monkeypatch.setattr(cas, "_push_main", racing_push_main)
    operation = cas.forward if verb == "forward" else cas.restore
    current = repositories.prior if verb == "forward" else repositories.candidate
    target = repositories.candidate if verb == "forward" else repositories.prior

    with pytest.raises(cas.CasFailure) as captured:
        operation(
            repositories.daemon,
            "origin",
            "main",
            current,
            target,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert raced
    assert competitor is not None
    assert captured.value.receipt["error"] == (
        "Git operation failed during remote main compare-and-swap"
    )
    assert sha(repositories.remote, "refs/heads/main") == competitor
    assert competitor != target


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_post_failure_remote_reread_reconciles_committed_push(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    original_push_main = cas._push_main

    def committed_then_client_failure(*args, **kwargs):
        original_push_main(*args, **kwargs)
        raise cas.CasError("Git operation failed during remote main compare-and-swap")

    monkeypatch.setattr(cas, "_push_main", committed_then_client_failure)
    operation = cas.forward if verb == "forward" else cas.restore
    current = repositories.prior if verb == "forward" else repositories.candidate
    target = repositories.candidate if verb == "forward" else repositories.prior

    with pytest.raises(cas.CasFailure) as captured:
        operation(
            repositories.daemon,
            "origin",
            "main",
            current,
            target,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    result = captured.value.receipt
    assert result["transition_outcome_source"] == "post-failure-readback"
    assert result["mutation_reconciliation"] == {
        "remote-main-cas": "confirmed-by-post-failure-readback",
    }
    assert result["readback"]["remote"] == target
    assert result["mutations"] == (
        ["local-main-cas", "worktree-alignment", "remote-main-cas"]
        if verb == "forward"
        else ["remote-main-cas"]
    )
    assert result["transition_outcome"] == (
        "landed-verification-incomplete" if verb == "forward" else "incomplete"
    )
    assert sha(repositories.remote, "refs/heads/main") == target


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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert raced
    failure = captured.value.receipt
    assert failure["error"] == "local main changed during worktree alignment"
    assert failure["result"] == "error"
    assert failure["transition_outcome"] == "incomplete"
    assert failure["mutations"] == [
        "remote-main-cas",
        "local-main-cas",
        "worktree-alignment",
    ]
    assert failure["readback"] == {
        "head": competitor,
        "local": competitor,
        "remote": repositories.prior,
    }
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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
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


def test_run_git_environment_allowlist_ignores_inherited_git_dir(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    attacker = repositories.remote.parent / "attacker.git"
    git(attacker.parent, "init", "-q", "--bare", str(attacker))
    monkeypatch.setenv("GIT_DIR", str(attacker))
    control = subprocess.run(
        [cas.GIT_PATH, "rev-parse", "--absolute-git-dir"],
        cwd=repositories.daemon,
        check=False,
        capture_output=True,
        text=True,
        env=_test_subprocess_environment(GIT_DIR=str(attacker)),
    )
    assert control.returncode == 0
    assert control.stdout.strip() == str(attacker)

    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    isolated = cas._run_git(
        operation,
        ["rev-parse", "--absolute-git-dir"],
        stage="environment allowlist test",
    )

    assert isolated.returncode == 0
    assert isolated.stdout.strip() == str(repositories.daemon / ".git")
    assert isolated.stderr == ""


def test_run_git_disables_injected_global_config(
    repositories: Repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    hostile_global_config = tmp_path / "hostile-global.gitconfig"
    hostile_global_config.write_text(
        "[cas]\n\tsentinel = inherited-global-config\n",
        encoding="utf-8",
    )
    control = subprocess.run(
        [cas.GIT_PATH, "config", "--get", "cas.sentinel"],
        cwd=repositories.daemon,
        check=False,
        capture_output=True,
        text=True,
        env=_test_subprocess_environment(
            GIT_CONFIG_GLOBAL=str(hostile_global_config)
        ),
    )
    assert control.returncode == 0
    assert control.stdout.strip() == "inherited-global-config"

    original_run = subprocess.run

    def run_with_platform_global_default(*args, env=None, **kwargs):
        effective_environment = dict(env or {})
        effective_environment.setdefault(
            "GIT_CONFIG_GLOBAL", str(hostile_global_config)
        )
        return original_run(*args, env=effective_environment, **kwargs)

    monkeypatch.setattr(cas.subprocess, "run", run_with_platform_global_default)

    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    isolated = cas._run_git(
        operation,
        ["config", "--get", "cas.sentinel"],
        stage="global config isolation test",
        check=False,
    )

    assert isolated.returncode == 1
    assert isolated.stdout == ""
    assert isolated.stderr == ""


def test_run_git_disables_injected_system_config(
    repositories: Repositories,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    hostile_system_config = tmp_path / "hostile-system.gitconfig"
    hostile_system_config.write_text(
        "[cas]\n\tsentinel = inherited-system-config\n",
        encoding="utf-8",
    )
    control = subprocess.run(
        [cas.GIT_PATH, "config", "--get", "cas.sentinel"],
        cwd=repositories.daemon,
        check=False,
        capture_output=True,
        text=True,
        env=_test_subprocess_environment(
            GIT_CONFIG_SYSTEM=str(hostile_system_config)
        ),
    )
    assert control.returncode == 0
    assert control.stdout.strip() == "inherited-system-config"

    original_run = subprocess.run

    def run_with_platform_system_default(*args, env=None, **kwargs):
        effective_environment = dict(env or {})
        effective_environment.setdefault(
            "GIT_CONFIG_SYSTEM", str(hostile_system_config)
        )
        return original_run(*args, env=effective_environment, **kwargs)

    monkeypatch.setattr(cas.subprocess, "run", run_with_platform_system_default)

    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    isolated = cas._run_git(
        operation,
        ["config", "--get", "cas.sentinel"],
        stage="system config isolation test",
        check=False,
    )

    assert isolated.returncode == 1
    assert isolated.stdout == ""
    assert isolated.stderr == ""


def test_representative_subset_is_identical_under_polluted_environment():
    selection = (
        "test_happy_forward or "
        "test_missing_resolved_git_fails_closed_before_subprocess or "
        "test_serving_repository_hidden_main_fails_closed_before_mutation"
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(Path(__file__).resolve()),
        "-q",
        "--tb=no",
        "-k",
        selection,
    ]
    polluted_environment = _test_subprocess_environment(
        LD_LIBRARY_PATH="/tmp/canonical-ref-cas-hostile-library-path",
        PYTHONPATH="/tmp/canonical-ref-cas-hostile-python-path",
    )
    pollution_control = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['LD_LIBRARY_PATH'])",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=polluted_environment,
    )
    assert pollution_control.stdout.strip() == (
        "/tmp/canonical-ref-cas-hostile-library-path"
    )
    clean_process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_test_subprocess_environment(),
    )
    polluted_process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=polluted_environment,
    )
    clean_stdout, clean_stderr = clean_process.communicate()
    polluted_stdout, polluted_stderr = polluted_process.communicate()

    outcome_pattern = re.compile(
        r"(\d+) (failed|passed|skipped|xfailed|xpassed|errors?)"
    )

    def outcome_counts(output: str) -> dict[str, int]:
        return {
            outcome: int(count)
            for count, outcome in outcome_pattern.findall(output)
        }

    def progress_signature(output: str) -> str | None:
        return next(
            (
                line.split(" ", 1)[0]
                for line in output.splitlines()
                if re.fullmatch(r"[.FEsxX]+\s+\[100%\]", line)
            ),
            None,
        )

    clean_outcomes = outcome_counts(clean_stdout)
    polluted_outcomes = outcome_counts(polluted_stdout)
    clean_progress = progress_signature(clean_stdout)
    polluted_progress = progress_signature(polluted_stdout)
    evidence = clean_stdout + clean_stderr + polluted_stdout + polluted_stderr
    assert sum(clean_outcomes.values()) == sum(polluted_outcomes.values()) == 3, evidence
    assert clean_process.returncode == polluted_process.returncode, evidence
    assert clean_outcomes == polluted_outcomes, evidence
    assert clean_progress is not None and polluted_progress is not None, evidence
    assert clean_progress == polluted_progress, evidence


def test_run_git_without_resolved_git_carries_stage_and_starts_no_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    subprocess_calls: list[tuple[tuple, dict]] = []

    def record_call(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

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
    monkeypatch.setattr(subprocess, "run", record_call)

    with pytest.raises(cas.CasError) as captured:
        cas._run_git(operation, ["version"], stage="supplied fail-closed stage")

    assert str(captured.value)
    assert str(captured.value) == "Git operation failed during supplied fail-closed stage"
    assert subprocess_calls == []


def test_run_git_bounds_real_hanging_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    operation = cas.Operation(
        "forward",
        tmp_path,
        "origin",
        "main",
        "1" * 40,
        "2" * 40,
        False,
    )

    monkeypatch.setattr(cas, "GIT_PATH", sys.executable)
    monkeypatch.setattr(cas, "TIMEOUT_SECONDS", 0.05)

    started = time.monotonic()
    with pytest.raises(cas.CasError) as captured:
        cas._run_git(
            operation,
            ["-c", "import time; time.sleep(5)"],
            stage="bounded timeout test",
        )
    elapsed = time.monotonic() - started

    assert str(captured.value) == "Git operation failed during bounded timeout test"
    assert elapsed < 2


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
    which_calls: list[tuple[tuple, dict]] = []

    def record_call(*args, **kwargs):
        which_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(cas.os, "confstr", lambda _name: system_path)
    monkeypatch.setattr(cas.shutil, "which", record_call)

    assert cas._resolve_git_path() is None
    assert which_calls == []


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
    expected_remote_url_sha256 = remote_url_sha256(repositories)
    subprocess_calls: list[tuple[tuple, dict]] = []

    def record_call(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(cas, "GIT_PATH", None)
    monkeypatch.setattr(subprocess, "run", record_call)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=expected_remote_url_sha256,
            window_owner=WINDOW_OWNER,
        )

    failure = captured.value.receipt
    assert_tool_run_identity(failure)
    stripped = dict(failure)
    stripped.pop("tool")
    stripped.pop("timestamp_utc")
    assert stripped == {
        "branch": None,
        "dry_run": False,
        "error": "Git operation failed during repository validation",
        "expected": {
            "from": repositories.prior,
            "to": repositories.candidate,
        },
        "git_executable": None,
        "host_identity": os.uname().nodename,
        "mutations": [],
        "remote": None,
        "readback": {"head": None, "local": None, "remote": None},
        "remote_url": None,
        "remote_url_sha256": None,
        "repo": None,
        "expected_remote_url_sha256": expected_remote_url_sha256,
        "result": "error",
        "transition_outcome": "not-landed",
        "transition_outcome_source": "pre-operation-evidence",
        "verb": "forward",
        "window_owner": WINDOW_OWNER,
    }
    assert subprocess_calls == []


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
    hostile_environment = _test_subprocess_environment()
    hostile_environment["PATH"] = str(shim_directory)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode == 0, completed.stderr
    result = receipt(completed)
    assert result["result"] == "success"
    assert result["git_executable"] == cas.GIT_PATH
    assert Path(result["git_executable"]).is_absolute()
    assert not shim_marker.exists()


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
            backend_environment = _test_git_environment()
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
        for name in ("git-remote-http",):
            shim = shim_directory / name
            shim.write_text(
                "#!/bin/sh\n"
                f": > {shim_marker}\n"
                "exit 97\n",
                encoding="utf-8",
            )
            shim.chmod(0o700)
        hostile_environment = _test_subprocess_environment()
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


def test_forward_refuses_ssh_remote_before_network_or_mutation(
    repositories: Repositories,
):
    remote_url = f"ssh://localhost{repositories.remote}"
    git(repositories.daemon, "remote", "set-url", "origin", remote_url)

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "SSH remote transport is not permitted"
    assert failure["mutations"] == []
    assert failure["remote_url"] == remote_url
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior


def test_forward_refuses_git_config_environment_with_pack_objects_hook(
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
    hostile_environment = _test_subprocess_environment()
    hostile_environment["GIT_CONFIG_GLOBAL"] = str(global_config)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == (
        "external Git configuration environment is not permitted"
    )
    assert failure["mutations"] == []
    assert "refs/heads/side" not in local_refs(repositories.daemon)
    assert not hook_log.exists()
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_remote_queries_ignore_daemon_repository_transport_config(
    repositories: Repositories,
):
    git(repositories.daemon, "config", "protocol.file.allow", "never")

    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    assert receipt(completed)["result"] == "success"


def test_post_failure_remote_reread_ignores_daemon_transport_config(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    git(repositories.daemon, "config", "protocol.file.allow", "never")
    original_push_main = cas._push_main

    def committed_then_client_failure(*args, **kwargs):
        original_push_main(*args, **kwargs)
        raise cas.CasError("Git operation failed during remote main compare-and-swap")

    monkeypatch.setattr(cas, "_push_main", committed_then_client_failure)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    result = captured.value.receipt
    assert result["transition_outcome_source"] == "post-failure-readback"
    assert result["mutation_reconciliation"] == {
        "remote-main-cas": "confirmed-by-post-failure-readback"
    }
    assert result["readback"]["remote"] == repositories.candidate
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert sha(repositories.remote, "refs/heads/main") == repositories.candidate
    assert sha(repositories.remote, "refs/heads/main") == repositories.candidate


def test_forward_refuses_git_config_environment_with_url_rewrite(
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
    configured_url = str(repositories.remote)
    git(repositories.daemon, "remote", "set-url", "origin", configured_url)
    global_config = repositories.remote.parent / "hostile-url.config"
    global_config.write_text(
        f'[url "{decoy}"]\n\tinsteadOf = {configured_url}\n',
        encoding="utf-8",
    )
    hostile_environment = _test_subprocess_environment()
    hostile_environment["GIT_CONFIG_GLOBAL"] = str(global_config)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == (
        "external Git configuration environment is not permitted"
    )
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(decoy, "refs/heads/main") == repositories.prior


def test_forward_refuses_git_config_environment_with_hidden_refs(
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
    hostile_environment = _test_subprocess_environment()
    hostile_environment["GIT_CONFIG_GLOBAL"] = str(global_config)

    completed = invoke(repositories, "forward", env=hostile_environment)

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["result"] == "error"
    assert failure["error"] == (
        "external Git configuration environment is not permitted"
    )
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(repositories.remote, "refs/heads/side") == repositories.prior


def test_serving_repository_hidden_main_fails_closed_before_mutation(
    repositories: Repositories,
):
    git(
        repositories.remote,
        "update-ref",
        "refs/heads/side",
        repositories.prior,
    )
    git(
        repositories.remote,
        "config",
        "uploadpack.hideRefs",
        "refs/heads/main",
    )

    completed = invoke(repositories, "forward")

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error"] == "remote ref snapshot could not be read exactly"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(repositories.remote, "refs/heads/side") == repositories.prior


def test_serving_repository_requires_symbolic_head_in_combined_snapshot(
    repositories: Repositories,
):
    git(
        repositories.remote,
        "update-ref",
        "refs/heads/side",
        repositories.prior,
    )
    git(
        repositories.remote,
        "update-ref",
        "--no-deref",
        "HEAD",
        repositories.prior,
    )
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    with pytest.raises(
        cas.CasError,
        match="remote ref snapshot could not be read exactly",
    ):
        cas._remote_snapshot(operation, str(repositories.remote))

    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.remote, "refs/heads/main") == repositories.prior
    assert sha(repositories.remote, "refs/heads/side") == repositories.prior


def test_fresh_candidate_proof_refuses_dirty_exact_clone(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )
    original_run_git = cas._run_git
    dirtied_checkout: Path | None = None

    def dirty_after_head_readback(operation, args, **kwargs):
        nonlocal dirtied_checkout
        completed = original_run_git(operation, args, **kwargs)
        if kwargs["stage"] == "fresh candidate HEAD readback":
            dirtied_checkout = kwargs["cwd"]
            (dirtied_checkout / "state.txt").write_text(
                "dirty after checkout\n",
                encoding="utf-8",
            )
        return completed

    monkeypatch.setattr(cas, "_run_git", dirty_after_head_readback)

    with pytest.raises(
        cas.CasError,
        match="candidate tree could not be proved clean in a fresh exact clone",
    ):
        cas._require_clean_fresh_candidate(operation, repositories.candidate)

    assert dirtied_checkout is not None


def test_fresh_candidate_proof_refuses_mismatched_checked_out_head(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )
    original_run_git = cas._run_git
    falsified_head = False

    def report_wrong_head(operation, args, **kwargs):
        nonlocal falsified_head
        completed = original_run_git(operation, args, **kwargs)
        if kwargs["stage"] == "fresh candidate HEAD readback":
            falsified_head = True
            return subprocess.CompletedProcess(
                completed.args,
                completed.returncode,
                repositories.prior + "\n",
                completed.stderr,
            )
        return completed

    monkeypatch.setattr(cas, "_run_git", report_wrong_head)

    with pytest.raises(
        cas.CasError,
        match="candidate tree is not clean in a fresh exact clone",
    ):
        cas._require_clean_fresh_candidate(operation, repositories.candidate)

    assert falsified_head


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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
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
    result = receipt(completed)
    assert result["warning"] == "local ref map changed outside allowed transitions"
    assert result["result"] == "warning"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert sha(repositories.daemon, "refs/heads/side") == repositories.candidate


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_unchanged_non_main_local_symref_succeeds_for_forward_and_restore(
    repositories: Repositories,
    verb: str,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    current = repositories.prior if verb == "forward" else repositories.candidate
    target = repositories.candidate if verb == "forward" else repositories.prior
    git(
        repositories.daemon,
        "update-ref",
        "refs/remotes/origin/side",
        current,
    )
    git(
        repositories.daemon,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/side",
    )

    completed = invoke(repositories, verb)

    assert completed.returncode == 0, completed.stderr
    result = receipt(completed)
    assert result["result"] == "success"
    assert result["readback"] == {"head": target, "local": target, "remote": target}
    assert git(
        repositories.daemon,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
    ).stdout.strip() == "refs/remotes/origin/side"


@pytest.mark.parametrize("verb", ["forward", "restore"])
def test_changed_non_main_local_symref_fails_for_forward_and_restore(
    repositories: Repositories,
    verb: str,
):
    if verb == "restore":
        assert invoke(repositories, "forward").returncode == 0
    current = repositories.prior if verb == "forward" else repositories.candidate
    target = repositories.candidate if verb == "forward" else repositories.prior
    git(
        repositories.daemon,
        "update-ref",
        "refs/remotes/origin/side",
        current,
    )
    git(
        repositories.daemon,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/side",
    )
    hook = repositories.remote / "hooks" / "post-receive"
    hook.write_text(
        "#!/bin/sh\n"
        f"git --git-dir={repositories.daemon / '.git'} symbolic-ref "
        "refs/remotes/origin/HEAD refs/remotes/origin/main\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    completed = invoke(repositories, verb)

    assert completed.returncode != 0
    result = receipt(completed)
    assert result["result"] == "warning"
    assert result["warning"] == "local ref map changed outside allowed transitions"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert result["mutations"] == (
        [
            "local-main-cas",
            "worktree-alignment",
            "remote-main-cas",
            "remote-tracking-main-cas",
        ]
        if verb == "forward"
        else [
            "remote-main-cas",
            "local-main-cas",
            "worktree-alignment",
            "remote-tracking-main-cas",
        ]
    )
    assert result["readback"] == {"head": target, "local": target, "remote": target}


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
    result = receipt(completed)
    assert result["result"] == "warning"
    assert result["warning"] == "remote HEAD changed during operation"
    assert result["transition_outcome"] == "landed-verification-incomplete"
    assert git(repositories.remote, "symbolic-ref", "HEAD").stdout.strip() == (
        "refs/heads/side"
    )


def test_remote_head_must_resolve_to_main_before_mutation(
    repositories: Repositories,
):
    git(
        repositories.remote,
        "update-ref",
        "refs/heads/side",
        repositories.prior,
    )
    git(repositories.remote, "symbolic-ref", "HEAD", "refs/heads/side")

    completed = invoke(repositories, "forward")

    assert completed.returncode != 0
    failure = receipt(completed)
    assert failure["error"] == "remote HEAD must resolve to refs/heads/main"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


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


def test_detached_daemon_is_diagnosed_as_not_having_main_checked_out(
    repositories: Repositories,
):
    git(repositories.daemon, "checkout", "-q", "--detach")

    completed = invoke(repositories, "forward")

    assert completed.returncode == 1
    failure = receipt(completed)
    assert failure["error"] == "daemon worktree must have main checked out"
    assert failure["mutations"] == []
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.daemon, "refs/heads/main") == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_symbolic_main_proof_rejects_detached_daemon_directly(
    repositories: Repositories,
):
    git(repositories.daemon, "checkout", "-q", "--detach")
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )

    with pytest.raises(cas.CasError) as captured:
        cas._require_symbolic_main(operation)

    assert str(captured.value) == "daemon worktree must have main checked out"


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


def test_remote_tracking_cas_refuses_stale_old_value_without_later_delta_guard(
    repositories: Repositories,
):
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )
    third_sha = sha(repositories.daemon, f"{repositories.prior}^")
    git(
        repositories.daemon,
        "update-ref",
        operation.remote_tracking_ref,
        third_sha,
        repositories.prior,
    )
    mutations: list[str] = []

    with pytest.raises(
        cas.CasError,
        match="Git operation failed during remote-tracking main compare-and-swap",
    ):
        cas._cas_remote_tracking_main(operation, mutations)

    assert mutations == []
    assert sha(repositories.daemon, operation.remote_tracking_ref) == third_sha


def test_remote_tracking_cas_readback_detects_race_without_later_delta_guard(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    operation = cas.Operation(
        "forward",
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        False,
    )
    third_sha = sha(repositories.daemon, f"{repositories.prior}^")
    original_remote_tracking_sha = cas._remote_tracking_sha
    raced = False

    def race_before_readback(current_operation):
        nonlocal raced
        git(
            repositories.daemon,
            "update-ref",
            current_operation.remote_tracking_ref,
            third_sha,
            repositories.candidate,
        )
        raced = True
        return original_remote_tracking_sha(current_operation)

    monkeypatch.setattr(cas, "_remote_tracking_sha", race_before_readback)
    mutations: list[str] = []

    with pytest.raises(
        cas.CasError,
        match="remote-tracking main compare-and-swap mismatch",
    ):
        cas._cas_remote_tracking_main(operation, mutations)

    assert raced
    assert mutations == ["remote-tracking-main-cas"]
    assert sha(repositories.daemon, operation.remote_tracking_ref) == third_sha


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
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
    )

    assert result["result"] == "success"
    assert checked_paths[0] == repositories.daemon
    assert checked_paths[1].name == "source.git"


def test_all_temporary_workspaces_are_pinned_below_daemon_parent(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    original_temporary_directory = cas.tempfile.TemporaryDirectory
    chosen_roots: list[Path] = []
    cleanup_policies: list[bool] = []

    def recording_temporary_directory(*args, **kwargs):
        chosen_roots.append(Path(kwargs["dir"]).resolve())
        cleanup_policies.append(kwargs["ignore_cleanup_errors"])
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        cas.tempfile,
        "TemporaryDirectory",
        recording_temporary_directory,
    )

    result = cas.forward(
        repositories.daemon,
        "origin",
        "main",
        repositories.prior,
        repositories.candidate,
        expected_remote_url_sha256=remote_url_sha256(repositories),
        window_owner=WINDOW_OWNER,
    )

    assert result["result"] == "success"
    assert chosen_roots
    assert cleanup_policies and all(cleanup_policies)
    assert all(
        root == repositories.remote.parent
        or root.is_relative_to(repositories.remote.parent)
        for root in chosen_roots
    )


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
    result = receipt(completed)
    assert result["warning"] == "local refs could not be read exactly"
    assert result["result"] == "warning"
    assert result["transition_outcome"] == "landed-verification-incomplete"
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


def test_forward_local_cas_refuses_second_main_worktree_before_owned_mutation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    second = repositories.remote.parent / "racing-main-worktree"
    original_local_cas = cas._cas_local_main_and_align
    raced = False

    def racing_local_cas(operation, mutations):
        nonlocal raced
        git(
            repositories.daemon,
            "worktree",
            "add",
            "-q",
            "-f",
            str(second),
            "main",
        )
        raced = True
        return original_local_cas(operation, mutations)

    monkeypatch.setattr(cas, "_cas_local_main_and_align", racing_local_cas)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert raced
    failure = captured.value.receipt
    assert failure["mutations"] == []
    assert failure["error"] == "main must not be checked out in another worktree"
    assert sha(repositories.daemon) == repositories.prior
    assert sha(second) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_forward_local_cas_refuses_detach_before_owned_mutation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    original_local_cas = cas._cas_local_main_and_align
    original_symbolic_main = cas._require_symbolic_main
    raced = False

    def racing_local_cas(operation, mutations):
        nonlocal raced
        git(repositories.daemon, "checkout", "-q", "--detach")
        raced = True
        return original_local_cas(operation, mutations)

    def symbolic_main_outside_race(operation):
        if raced:
            return None
        return original_symbolic_main(operation)

    monkeypatch.setattr(cas, "_cas_local_main_and_align", racing_local_cas)
    monkeypatch.setattr(cas, "_require_symbolic_main", symbolic_main_outside_race)

    with pytest.raises(cas.CasFailure) as captured:
        cas.forward(
            repositories.daemon,
            "origin",
            "main",
            repositories.prior,
            repositories.candidate,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert raced
    failure = captured.value.receipt
    assert failure["mutations"] == []
    assert failure["error"] == "daemon worktree must have main checked out"
    assert sha(repositories.daemon, "refs/heads/main") == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.prior


def test_restore_reproves_no_other_main_worktree_before_remote_mutation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    assert invoke(repositories, "forward").returncode == 0
    second = repositories.remote.parent / "restore-racing-main-worktree"
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
        cas.restore(
            repositories.daemon,
            "origin",
            "main",
            repositories.candidate,
            repositories.prior,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert snapshots == 2
    failure = captured.value.receipt
    assert failure["error"] == "main must not be checked out in another worktree"
    assert failure["mutations"] == []
    assert sha(repositories.daemon, "refs/heads/main") == repositories.candidate
    assert sha(second) == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


@pytest.mark.parametrize(
    ("race_kind", "expected_error"),
    [
        (
            "second-worktree",
            "main must not be checked out in another worktree",
        ),
        (
            "detach",
            "daemon worktree must have main checked out",
        ),
    ],
)
def test_restore_local_cas_rechecks_main_worktree_before_local_mutation(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
    race_kind: str,
    expected_error: str,
):
    assert invoke(repositories, "forward").returncode == 0
    second = repositories.remote.parent / "restore-local-cas-main-worktree"
    original_local_cas = cas._cas_local_main_and_align
    raced = False

    def racing_local_cas(operation, mutations):
        nonlocal raced
        if race_kind == "second-worktree":
            git(
                repositories.daemon,
                "worktree",
                "add",
                "-q",
                "-f",
                str(second),
                "main",
            )
        else:
            git(repositories.daemon, "checkout", "-q", "--detach")
        raced = True
        return original_local_cas(operation, mutations)

    monkeypatch.setattr(cas, "_cas_local_main_and_align", racing_local_cas)

    with pytest.raises(cas.CasFailure) as captured:
        cas.restore(
            repositories.daemon,
            "origin",
            "main",
            repositories.candidate,
            repositories.prior,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert raced
    failure = captured.value.receipt
    assert failure["mutations"] == ["remote-main-cas"]
    assert failure["error"] == expected_error
    assert failure["transition_outcome"] == "incomplete"
    assert sha(repositories.daemon, "refs/heads/main") == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.prior
    if race_kind == "second-worktree":
        assert sha(second) == repositories.candidate


def test_restore_reproves_local_refs_before_remote_mutation(
    repositories: Repositories, monkeypatch: pytest.MonkeyPatch
):
    assert invoke(repositories, "forward").returncode == 0
    original_snapshot = cas._remote_snapshot
    snapshots = 0

    def racing_snapshot(operation, remote_url):
        nonlocal snapshots
        snapshots += 1
        observed = original_snapshot(operation, remote_url)
        if snapshots == 2:
            git(
                repositories.daemon,
                "update-ref",
                "refs/heads/side",
                repositories.candidate,
            )
        return observed

    monkeypatch.setattr(cas, "_remote_snapshot", racing_snapshot)

    with pytest.raises(cas.CasFailure) as captured:
        cas.restore(
            repositories.daemon,
            "origin",
            "main",
            repositories.candidate,
            repositories.prior,
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    failure = captured.value.receipt
    assert failure["mutations"] == []
    assert failure["error"] == "local ref map changed outside allowed transitions"
    assert snapshots == 2
    assert sha(repositories.daemon, "refs/heads/main") == repositories.candidate
    assert sha(repositories.daemon, "refs/heads/side") == repositories.candidate
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_forward_reproves_local_refs_after_remote_inspection_before_mutation(
    repositories: Repositories,
    monkeypatch: pytest.MonkeyPatch,
):
    original_snapshot = cas._remote_snapshot
    snapshots = 0

    def racing_snapshot(operation, remote_url):
        nonlocal snapshots
        snapshots += 1
        observed = original_snapshot(operation, remote_url)
        if snapshots == 2:
            git(
                repositories.daemon,
                "update-ref",
                "refs/heads/side",
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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    failure = captured.value.receipt
    assert failure["mutations"] == []
    assert failure["error"] == "local ref map changed outside allowed transitions"
    assert snapshots == 2
    assert sha(repositories.daemon) == repositories.prior
    assert sha(repositories.daemon, "refs/heads/side") == repositories.candidate
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
            expected_remote_url_sha256=remote_url_sha256(repositories),
            window_owner=WINDOW_OWNER,
        )

    assert snapshots == 3
    failure = captured.value.receipt
    assert failure["result"] == "error"
    assert failure["transition_outcome"] == "incomplete"
    assert failure["mutations"] == [
        "local-main-cas",
        "worktree-alignment",
        "remote-main-cas",
        "remote-tracking-main-cas",
    ]
    assert sha(repositories.daemon) == repositories.prior
    assert remote_sha(repositories.daemon) == repositories.candidate


def test_receipt_preserves_local_remote_path_containing_at_sign(
    repositories: Repositories,
):
    local_remote = repositories.remote.parent / "remote@local-repository"
    local_remote.symlink_to(repositories.remote, target_is_directory=True)
    git(repositories.daemon, "remote", "set-url", "origin", str(local_remote))

    completed = invoke(repositories, "forward")

    assert completed.returncode == 0, completed.stderr
    result = receipt(completed)
    assert result["result"] == "success"
    assert result["remote_url"] == str(local_remote)
    assert result["remote_url_sha256"] == hashlib.sha256(
        str(local_remote).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("baseline_remote_moved", [False, True])
def test_receipt_redacts_remote_url_credentials_on_success_and_baseline_refusal(
    repositories: Repositories, baseline_remote_moved: bool
):
    exact_url = f"file://operator@localhost{repositories.remote}"
    redacted_url = f"file://localhost{repositories.remote}"
    git(repositories.daemon, "remote", "set-url", "origin", exact_url)
    if baseline_remote_moved:
        advance_remote(repositories)

    completed = invoke(repositories, "forward")

    assert (completed.returncode != 0) is baseline_remote_moved
    result = receipt(completed)
    assert_receipt_contains_no_url_userinfo(result)
    assert result["remote_url"] == redacted_url
    assert result["remote_url_sha256"] == hashlib.sha256(
        exact_url.encode("utf-8")
    ).hexdigest()
    assert result["result"] == (
        "error" if baseline_remote_moved else "success"
    )


@pytest.mark.parametrize(
    ("configured_url", "redacted_url"),
    URL_USERINFO_REDACTION_CASES
    + (
        (
            "operator@example.invalid",
            "[redacted]",
        ),
        (
            "operator@example.invalid/not-scp",
            "[redacted]",
        ),
    )
    + tuple((url, "[redacted]") for url in SCHEMELESS_USERINFO_CASES),
)
def test_failure_receipt_redacts_userinfo_from_every_remote_url_form(
    repositories: Repositories,
    configured_url: str,
    redacted_url: str,
):
    userinfo = configured_url.split("@", 1)[0]
    git(repositories.daemon, "remote", "set-url", "origin", configured_url)

    completed = invoke(repositories, "forward")

    assert completed.returncode == 1
    result = receipt(completed)
    serialized = json.dumps(result, sort_keys=True)
    assert result["remote_url"] == redacted_url
    assert userinfo not in serialized
    assert_receipt_contains_no_url_userinfo(result)
    assert result["mutations"] == []


@pytest.mark.parametrize(
    ("url_with_userinfo", "redacted_url"),
    URL_USERINFO_REDACTION_CASES,
)
def test_receipt_payload_structurally_redacts_userinfo_from_future_fields(
    url_with_userinfo: str,
    redacted_url: str,
):
    result = cas._Receipt()
    result["future_receipt_field"] = {"nested_urls": [url_with_userinfo]}

    assert_receipt_contains_no_url_userinfo(result)
    assert result["future_receipt_field"]["nested_urls"] == [redacted_url]

    serialized_result = json.loads(
        cas._receipt_payload(
            {"future_receipt_field": {"nested_urls": [url_with_userinfo]}}
        )
    )

    assert_receipt_contains_no_url_userinfo(serialized_result)
    assert serialized_result["future_receipt_field"]["nested_urls"] == [
        redacted_url
    ]


@pytest.mark.parametrize(
    "embedded_url",
    (
        "diagnostic: https://operator@example.invalid/repository.git",
        "diagnostic: operator@example.invalid:repository.git",
        "diagnostic:(operator@example.invalid:repository.git)",
    ),
)
def test_receipt_payload_redacts_userinfo_embedded_in_future_fields(
    embedded_url: str,
):
    result = cas._Receipt(future_receipt_field=embedded_url)

    assert result["future_receipt_field"] == "[redacted]"
    assert_receipt_contains_no_url_userinfo(result)


def test_receipt_payload_redacts_userinfo_from_future_mapping_keys():
    key_with_userinfo = "https://operator@example.invalid/repository.git"
    redacted_key = "https://example.invalid/repository.git"
    result = cas._Receipt()

    assert result.setdefault(key_with_userinfo, True) is True
    assert result == {redacted_key: True}

    serialized_result = json.loads(
        cas._receipt_payload({"future_receipt_field": {key_with_userinfo: True}})
    )

    assert serialized_result["future_receipt_field"] == {
        redacted_key: True
    }
    assert_receipt_contains_no_url_userinfo(serialized_result)


@pytest.mark.parametrize(
    ("url_with_userinfo", "redacted_url"),
    URL_USERINFO_REDACTION_CASES,
)
def test_outermost_fallback_redacts_userinfo_from_every_receipt_field(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    url_with_userinfo: str,
    redacted_url: str,
):
    receipt_path = tmp_path / "outermost-fallback.json"

    result_code = cas.main(
        [
            "forward",
            "--repo",
            "~canonical-ref-cas-user-that-must-not-exist/repository",
            "--remote",
            url_with_userinfo,
            "--branch",
            "main",
            "--from",
            "1" * 40,
            "--to",
            "2" * 40,
            "--expect-remote-url-sha256",
            "3" * 64,
            "--window-owner",
            WINDOW_OWNER,
            "--receipt-path",
            str(receipt_path),
        ]
    )

    captured = capfd.readouterr()
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result_code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == result
    assert result["transition_outcome"] == "undetermined"
    assert result["transition_outcome_source"] == "outermost-fallback"
    assert result["remote"] == "[redacted]"
    assert_receipt_contains_no_url_userinfo(result)


@pytest.mark.parametrize("url_with_userinfo", SCHEMELESS_USERINFO_CASES)
@pytest.mark.parametrize(
    "echoed_argument",
    ["--remote", "--repo", "--branch", "--window-owner", "--from"],
)
def test_outermost_fallback_redacts_schemeless_userinfo_from_echoed_arguments(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    url_with_userinfo: str,
    echoed_argument: str,
):
    receipt_path = tmp_path / "outermost-fallback.json"
    arguments = {
        "--repo": "~canonical-ref-cas-user-that-must-not-exist/repository",
        "--remote": "origin",
        "--branch": "main",
        "--from": "1" * 40,
        "--to": "2" * 40,
        "--expect-remote-url-sha256": "3" * 64,
        "--window-owner": WINDOW_OWNER,
        "--receipt-path": str(receipt_path),
    }
    arguments[echoed_argument] = url_with_userinfo
    argv = ["forward"]
    for option, value in arguments.items():
        argv.extend([option, value])

    result_code = cas.main(argv)

    captured = capfd.readouterr()
    result = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result_code == 1
    assert json.loads(captured.err) == result
    for secret in ("user-info", "masked-value"):
        assert secret not in captured.out
        assert secret not in captured.err
        assert secret not in json.dumps(result, sort_keys=True)
    assert_receipt_contains_no_url_userinfo(result)
    if echoed_argument == "--remote":
        assert result["transition_outcome_source"] == "outermost-fallback"
        assert result["remote"] == "[redacted]"


def test_url_named_future_fields_redact_bare_userinfo_host():
    bare_userinfo_host = "operator@example.invalid/not-scp"

    result = cas._Receipt(
        future_mirror_url=bare_userinfo_host,
        future_diagnostics={"fetch_url": bare_userinfo_host},
    )
    serialized_result = json.loads(
        cas._receipt_payload({"future_mirror_url": bare_userinfo_host})
    )

    assert result["future_mirror_url"] == "[redacted]"
    assert result["future_diagnostics"] == {"fetch_url": "[redacted]"}
    assert serialized_result["future_mirror_url"] == "[redacted]"


def test_malformed_remote_url_produces_bounded_receipt_without_traceback(
    repositories: Repositories,
):
    configured_url = "http://[::1"
    git(repositories.daemon, "remote", "set-url", "origin", configured_url)

    completed = invoke(repositories, "forward")

    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    result = receipt(completed)
    assert result["result"] == "error"
    assert result["remote_url"] == "[redacted]"
    assert result["error"] == "remote URL could not be parsed safely"
    assert result["mutations"] == []
