#!/usr/bin/env python3
"""Bounded compare-and-swap operations for the Day Zero canonical ref.

This tool intentionally owns only the one-commit ``main`` transition and its
exact candidate-to-prior compensation.  It is not a general-purpose Git ref
writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


SHA_RE = re.compile(r"[0-9a-f]{40}")
REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT_SECONDS = 120
GIT_ENV_ALLOWLIST = (
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SSH_AUTH_SOCK",
    "TZ",
)
GIT_INTERNAL_ENV = frozenset({"GIT_INDEX_FILE", "GIT_OPTIONAL_LOCKS"})
GIT_CONFIG_ENV_NAMES = frozenset(
    {
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
    }
)
GIT_CONFIG_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
GIT_CONFIG_PINS = (
    "-c",
    "uploadpack.packObjectsHook=",
    "-c",
    "uploadpack.hideRefs=",
    "-c",
    "transfer.hideRefs=",
)


class CasError(RuntimeError):
    """A bounded canonical-ref safety failure."""

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class CasFailure(CasError):
    """A failed operation with a structured JSON receipt."""

    def __init__(self, receipt: dict):
        super().__init__(receipt["error"])
        self.receipt = receipt


@dataclass(frozen=True)
class Operation:
    verb: str
    repo: pathlib.Path
    remote: str
    branch: str
    from_sha: str
    to_sha: str
    dry_run: bool

    @property
    def local_ref(self) -> str:
        return f"refs/heads/{self.branch}"

    @property
    def remote_tracking_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"


@dataclass(frozen=True)
class RemoteSnapshot:
    refs: Mapping[str, str]
    head: str


@dataclass(frozen=True)
class RefBaseline:
    local: Mapping[str, tuple[str, str | None]]
    remote: RemoteSnapshot


def _require_no_external_git_config_environment() -> None:
    if any(
        name in GIT_CONFIG_ENV_NAMES
        or name.startswith(GIT_CONFIG_ENV_PREFIXES)
        for name in os.environ
    ):
        raise CasError("external Git configuration environment is not permitted")


def _run_git(
    operation: Operation,
    args: Sequence[str],
    *,
    stage: str,
    check: bool = True,
    cwd: pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        name: os.environ[name]
        for name in GIT_ENV_ALLOWLIST
        if name in os.environ
    }
    if env:
        if any(name not in GIT_INTERNAL_ENV for name in env):
            raise CasError(f"Git operation failed during {stage}")
        environment.update(env)
    environment.update(
        {
            "GIT_CONFIG_COUNT": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        completed = subprocess.run(
            ["git", *GIT_CONFIG_PINS, *args],
            cwd=cwd or operation.repo,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        raise CasError(f"Git operation failed during {stage}") from None
    if check and completed.returncode != 0:
        raise CasError(f"Git operation failed during {stage}")
    return completed


def _exact_ref_sha(
    operation: Operation, ref: str, *, stage: str, error: str
) -> str:
    value = _run_git(
        operation,
        ["rev-parse", "--verify", ref],
        stage=stage,
    ).stdout.strip()
    if not SHA_RE.fullmatch(value):
        raise CasError(error)
    return value


def _local_sha(operation: Operation) -> str:
    return _exact_ref_sha(
        operation,
        operation.local_ref,
        stage="local main readback",
        error="local main readback was not an exact SHA",
    )


def _remote_tracking_sha(operation: Operation) -> str:
    return _exact_ref_sha(
        operation,
        operation.remote_tracking_ref,
        stage="remote-tracking main readback",
        error="remote-tracking main readback was not an exact SHA",
    )


def _head_sha(operation: Operation) -> str:
    value = _run_git(
        operation, ["rev-parse", "--verify", "HEAD"], stage="worktree HEAD readback"
    ).stdout.strip()
    if not SHA_RE.fullmatch(value):
        raise CasError("worktree HEAD readback was not an exact SHA")
    return value


def _local_ref_map(operation: Operation) -> dict[str, tuple[str, str | None]]:
    completed = _run_git(
        operation,
        ["for-each-ref", "--format=%(refname) %(objectname) %(symref)"],
        stage="local ref map readback",
        check=False,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise CasError("local refs could not be read exactly")
    refs: dict[str, tuple[str, str | None]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) not in {2, 3}:
            raise CasError("local refs could not be read exactly")
        ref, value = fields[:2]
        symref = fields[2] if len(fields) == 3 else None
        if (
            not ref
            or not ref.startswith("refs/")
            or ref in refs
            or not value
            or not SHA_RE.fullmatch(value)
            or (symref is not None and (not symref or not symref.startswith("refs/")))
        ):
            raise CasError("local refs could not be read exactly")
        refs[ref] = (value, symref)
    integrity = _run_git(
        operation,
        ["fsck", "--connectivity-only", "--no-reflogs", "--no-dangling"],
        stage="local ref integrity validation",
        check=False,
    )
    if integrity.returncode != 0:
        raise CasError("local refs could not be read exactly")
    return refs


def _remote_snapshot(operation: Operation, remote_url: str) -> RemoteSnapshot:
    completed = _run_git(
        operation,
        ["ls-remote", "--symref", "--exit-code", remote_url],
        stage="remote ref snapshot readback",
        check=False,
    )
    if completed.returncode != 0:
        raise CasError("remote ref snapshot could not be read exactly")

    refs: dict[str, str] = {}
    peeled: set[str] = set()
    head: str | None = None
    head_sha: str | None = None
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "ref:":
            target, name = fields[1:]
            if (
                head is not None
                or not target
                or not target.startswith("refs/")
                or name != "HEAD"
            ):
                raise CasError("remote ref snapshot could not be read exactly")
            head = target
            continue
        if len(fields) != 2:
            raise CasError("remote ref snapshot could not be read exactly")
        value, ref = fields
        if not value or not SHA_RE.fullmatch(value) or not ref:
            raise CasError("remote ref snapshot could not be read exactly")
        if ref == "HEAD":
            if head_sha is not None:
                raise CasError("remote ref snapshot could not be read exactly")
            head_sha = value
        elif ref.startswith("refs/") and ref.endswith("^{}"):
            base_ref = ref.removesuffix("^{}")
            if not base_ref or base_ref in peeled:
                raise CasError("remote ref snapshot could not be read exactly")
            peeled.add(base_ref)
        elif ref.startswith("refs/") and ref not in refs:
            refs[ref] = value
        else:
            raise CasError("remote ref snapshot could not be read exactly")
    if (
        not refs
        or not head
        or not head_sha
        or refs.get(head) != head_sha
        or any(base_ref not in refs for base_ref in peeled)
    ):
        raise CasError("remote ref snapshot could not be read exactly")
    return RemoteSnapshot(refs=refs, head=head)


def _remote_sha_from_map(operation: Operation, refs: Mapping[str, str]) -> str:
    value = refs.get(operation.local_ref)
    if value is None or not SHA_RE.fullmatch(value):
        raise CasError("remote main readback was not an exact SHA")
    return value


def _remote_tracking_sha_from_map(
    operation: Operation, refs: Mapping[str, tuple[str, str | None]]
) -> str:
    state = refs.get(operation.remote_tracking_ref)
    if state is None:
        raise CasError("remote-tracking main readback was not an exact SHA")
    value, symref = state
    if symref is not None or not value or not SHA_RE.fullmatch(value):
        raise CasError("remote-tracking main readback was not an exact SHA")
    return value


def _remote_sha(operation: Operation, remote_url: str) -> str:
    completed = _run_git(
        operation,
        ["ls-remote", "--refs", "--exit-code", remote_url, operation.local_ref],
        stage="remote main readback",
        check=False,
    )
    lines = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(lines) != 1 or len(lines[0]) != 2:
        raise CasError("remote main could not be read exactly")
    value, ref = lines[0]
    if ref != operation.local_ref or not SHA_RE.fullmatch(value):
        raise CasError("remote main readback was not an exact SHA")
    return value


def _require_remote_ref_delta(
    operation: Operation,
    before: Mapping[str, str],
    after: Mapping[str, str],
) -> None:
    if (
        not before
        or not after
        or any(not ref or not sha for ref, sha in before.items())
        or any(not ref or not sha for ref, sha in after.items())
    ):
        raise CasError("remote refs could not be read exactly")
    changed_or_removed = any(
        ref != operation.local_ref and after.get(ref) != sha
        for ref, sha in before.items()
    )
    added = any(
        ref != operation.local_ref and ref not in before for ref in after
    )
    if changed_or_removed or added:
        raise CasError("remote ref map changed outside canonical main")


def _has_exact_local_ref_identifiers(
    refs: Mapping[str, tuple[str, str | None]],
) -> bool:
    return bool(refs) and all(
        ref
        and ref.startswith("refs/")
        and value
        and SHA_RE.fullmatch(value)
        and (symref is None or (symref and symref.startswith("refs/")))
        for ref, (value, symref) in refs.items()
    )


def _require_local_refs_unchanged(
    before: Mapping[str, tuple[str, str | None]],
    after: Mapping[str, tuple[str, str | None]],
) -> None:
    if (
        not _has_exact_local_ref_identifiers(before)
        or not _has_exact_local_ref_identifiers(after)
        or before != after
    ):
        raise CasError("local ref map changed outside allowed transitions")


def _require_local_ref_delta(
    operation: Operation,
    before: Mapping[str, tuple[str, str | None]],
    after: Mapping[str, tuple[str, str | None]],
    expected_sha: str,
    *,
    transitioned: bool,
) -> None:
    if (
        not _has_exact_local_ref_identifiers(before)
        or not _has_exact_local_ref_identifiers(after)
        or not expected_sha
    ):
        raise CasError("local ref map changed outside allowed transitions")
    if not transitioned:
        _require_local_refs_unchanged(before, after)
        return

    allowed = {operation.local_ref, operation.remote_tracking_ref}
    for ref in before.keys() | after.keys():
        before_state = before.get(ref)
        after_state = after.get(ref)
        if not ref or before_state is None or after_state is None:
            raise CasError("local ref map changed outside allowed transitions")
        before_sha, before_symref = before_state
        after_sha, after_symref = after_state
        if ref in allowed:
            if (
                not before_sha
                or not after_sha
                or before_symref is not None
                or after_symref is not None
                or before_sha != operation.from_sha
                or after_sha != expected_sha
            ):
                raise CasError("local ref map changed outside allowed transitions")
        elif before_symref is not None or after_symref is not None:
            if (
                not before_sha
                or not after_sha
                or not before_symref
                or not after_symref
                or before_symref != after_symref
                or before_symref not in allowed
                or before_sha != operation.from_sha
                or after_sha != expected_sha
            ):
                raise CasError("local ref map changed outside allowed transitions")
        elif not before_sha or not after_sha or before_sha != after_sha:
            raise CasError("local ref map changed outside allowed transitions")


def _readback(
    operation: Operation,
    remote_url: str,
    *,
    baseline_remote: RemoteSnapshot | None = None,
) -> dict[str, str]:
    head = _head_sha(operation)
    local = _local_sha(operation)
    remote = _remote_snapshot(operation, remote_url)
    if baseline_remote is not None:
        if (
            not baseline_remote.head
            or not remote.head
            or remote.head != baseline_remote.head
        ):
            raise CasError("remote HEAD changed during operation")
        _require_remote_ref_delta(operation, baseline_remote.refs, remote.refs)
    return {
        "head": head,
        "local": local,
        "remote": _remote_sha_from_map(operation, remote.refs),
    }


def _best_effort_readback(
    operation: Operation, remote_url: str
) -> dict[str, str | None]:
    observed: dict[str, str | None] = {}
    for name, reader in (
        ("head", _head_sha),
        ("local", _local_sha),
        ("remote", lambda current: _remote_sha(current, remote_url)),
    ):
        try:
            observed[name] = reader(operation)
        except CasError:
            observed[name] = None
    return observed


def _validated_coordinates(operation: Operation) -> dict[str, str]:
    if not SHA_RE.fullmatch(operation.from_sha):
        raise CasError(
            "--from must be an exact lowercase 40-character SHA",
            error_code="invalid-coordinate",
        )
    if not SHA_RE.fullmatch(operation.to_sha):
        raise CasError(
            "--to must be an exact lowercase 40-character SHA",
            error_code="invalid-coordinate",
        )
    if operation.from_sha == operation.to_sha:
        raise CasError(
            "--from and --to must differ", error_code="invalid-coordinate"
        )
    return {"from": operation.from_sha, "to": operation.to_sha}


def _require_non_shallow(
    operation: Operation,
    *,
    cwd: pathlib.Path | None = None,
    error: str,
) -> None:
    value = _run_git(
        operation,
        ["rev-parse", "--is-shallow-repository"],
        stage="shallow repository validation",
        cwd=cwd,
    ).stdout.strip()
    if not value or value != "false":
        raise CasError(error)


def _require_no_other_main_worktree(operation: Operation) -> None:
    output = _run_git(
        operation,
        ["worktree", "list", "--porcelain", "-z"],
        stage="linked worktree validation",
    ).stdout
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for field in output.split("\0"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        name, separator, value = field.partition(" ")
        if not name or name in current:
            raise CasError("linked worktrees could not be read exactly")
        if not separator:
            if name not in {"bare", "detached", "locked", "prunable"}:
                raise CasError("linked worktrees could not be read exactly")
            current[name] = "true"
        else:
            if not value:
                raise CasError("linked worktrees could not be read exactly")
            current[name] = value
    if current:
        records.append(current)
    if not records:
        raise CasError("linked worktrees could not be read exactly")

    holders: list[pathlib.Path] = []
    for record in records:
        worktree = record.get("worktree")
        head = record.get("HEAD")
        if not worktree or not head or not SHA_RE.fullmatch(head):
            raise CasError("linked worktrees could not be read exactly")
        branch = record.get("branch")
        if branch is not None:
            if not branch or not branch.startswith("refs/"):
                raise CasError("linked worktrees could not be read exactly")
            if branch == operation.local_ref:
                holders.append(pathlib.Path(worktree).resolve())
    if (
        len(holders) != 1
        or not str(holders[0])
        or holders[0] != operation.repo
    ):
        raise CasError("main must not be checked out in another worktree")


def _require_operation(operation: Operation) -> str:
    if operation.verb not in {"forward", "restore"}:
        raise CasError("unsupported operation")
    if operation.branch != "main":
        raise CasError("--branch must be exactly main")
    if not REMOTE_RE.fullmatch(operation.remote):
        raise CasError("--remote must name one configured Git remote")

    root = _run_git(
        operation, ["rev-parse", "--show-toplevel"], stage="repository validation"
    ).stdout.strip()
    if pathlib.Path(root).resolve() != operation.repo:
        raise CasError("--repo must name the Git worktree root")
    inside = _run_git(
        operation, ["rev-parse", "--is-inside-work-tree"], stage="repository validation"
    ).stdout.strip()
    if inside != "true":
        raise CasError("--repo is not a Git worktree")
    _require_non_shallow(
        operation,
        error="daemon repository must not be shallow",
    )
    _require_no_other_main_worktree(operation)
    fetch_urls = _run_git(
        operation,
        ["remote", "get-url", "--all", operation.remote],
        stage="remote validation",
    ).stdout.splitlines()
    push_urls = _run_git(
        operation,
        ["remote", "get-url", "--push", "--all", operation.remote],
        stage="remote validation",
    ).stdout.splitlines()
    if (
        len(fetch_urls) != 1
        or len(push_urls) != 1
        or not fetch_urls[0]
        or not push_urls[0]
        or fetch_urls != push_urls
    ):
        raise CasError("remote must have one identical fetch and push URL")
    return fetch_urls[0]


def _require_remote_url_unchanged(operation: Operation, remote_url: str) -> None:
    fetch_urls = _run_git(
        operation,
        ["remote", "get-url", "--all", operation.remote],
        stage="final remote validation",
    ).stdout.splitlines()
    push_urls = _run_git(
        operation,
        ["remote", "get-url", "--push", "--all", operation.remote],
        stage="final remote validation",
    ).stdout.splitlines()
    if (
        not remote_url
        or fetch_urls != [remote_url]
        or push_urls != [remote_url]
    ):
        raise CasError("validated remote URL changed during operation")


def _require_symbolic_main(operation: Operation) -> None:
    symbolic = _run_git(
        operation,
        ["symbolic-ref", "--quiet", "HEAD"],
        stage="checked-out branch validation",
        check=False,
    )
    if symbolic.returncode != 0 or symbolic.stdout.strip() != operation.local_ref:
        raise CasError("daemon worktree must have main checked out")


def _require_exact_cleanliness(
    operation: Operation, *, cwd: pathlib.Path | None = None
) -> None:
    repository = cwd or operation.repo
    listed = _run_git(
        operation,
        ["ls-files", "-v", "-z"],
        stage="daemon index flag validation",
        cwd=repository,
    ).stdout
    entries = (entry for entry in listed.split("\0") if entry)
    if any(entry[0] == "S" or entry[0].islower() for entry in entries):
        raise CasError("daemon index contains hidden tracked entries")

    status = _run_git(
        operation,
        [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.fileMode=true",
            "-c",
            "core.symlinks=true",
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        stage="daemon worktree cleanliness",
        cwd=repository,
        env={"GIT_OPTIONAL_LOCKS": "0"},
    ).stdout
    if status.strip():
        raise CasError("daemon worktree must be clean")

    with tempfile.TemporaryDirectory(
        prefix="sonsteng-canonical-ref-cas-index-"
    ) as directory:
        index_path = pathlib.Path(directory) / "index"
        index_environment = {"GIT_INDEX_FILE": str(index_path)}
        _run_git(
            operation,
            [
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fileMode=true",
                "-c",
                "core.symlinks=true",
                "read-tree",
                "HEAD",
            ],
            stage="temporary HEAD index construction",
            cwd=repository,
            env=index_environment,
        )
        cached = _run_git(
            operation,
            [
                "-c",
                "core.fileMode=true",
                "-c",
                "core.symlinks=true",
                "diff-index",
                "--cached",
                "--quiet",
                "HEAD",
                "--",
            ],
            stage="temporary index HEAD comparison",
            check=False,
            cwd=repository,
            env=index_environment,
        )
        refreshed = _run_git(
            operation,
            [
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fileMode=true",
                "-c",
                "core.symlinks=true",
                "update-index",
                "--really-refresh",
            ],
            stage="temporary index worktree refresh",
            check=False,
            cwd=repository,
            env=index_environment,
        )
        files = _run_git(
            operation,
            [
                "-c",
                "core.fileMode=true",
                "-c",
                "core.symlinks=true",
                "diff-files",
                "--quiet",
                "--",
            ],
            stage="tracked worktree content comparison",
            check=False,
            cwd=repository,
            env=index_environment,
        )
        if (
            cached.returncode != 0
            or refreshed.returncode != 0
            or files.returncode != 0
        ):
            raise CasError("daemon tracked content does not equal HEAD")


def _require_checked_out_clean_main(
    operation: Operation, expected_sha: str, remote_url: str
) -> None:
    _require_symbolic_main(operation)
    _require_exact_cleanliness(operation)
    observed = _readback(operation, remote_url)
    if observed["local"] != expected_sha:
        raise CasError("local main does not equal --from")
    if observed["head"] != expected_sha:
        raise CasError("daemon worktree HEAD does not equal --from")
    if observed["remote"] != expected_sha:
        raise CasError("remote main does not equal --from")


def _require_final_state(
    operation: Operation,
    expected_sha: str,
    remote_url: str,
    baseline: RefBaseline,
) -> dict[str, str]:
    _require_remote_url_unchanged(operation, remote_url)
    observed = _readback(
        operation,
        remote_url,
        baseline_remote=baseline.remote,
    )

    # Read all local proof inputs after the final remote interaction so a local
    # ref race during that interaction cannot be reported as success.
    _require_no_other_main_worktree(operation)
    _require_symbolic_main(operation)
    _require_exact_cleanliness(operation)
    current_local_refs = _local_ref_map(operation)
    _require_local_ref_delta(
        operation,
        baseline.local,
        current_local_refs,
        expected_sha,
        transitioned=not operation.dry_run,
    )
    observed.update(head=_head_sha(operation), local=_local_sha(operation))
    if (
        set(observed) != {"head", "local", "remote"}
        or any(not value or value != expected_sha for value in observed.values())
    ):
        raise CasError("exact canonical ref readback mismatch")
    return observed


def _require_one_commit_transition(operation: Operation, prior: str, candidate: str) -> None:
    object_type = _run_git(
        operation,
        ["cat-file", "-t", candidate],
        stage="candidate commit validation",
    ).stdout.strip()
    if not object_type or object_type != "commit":
        raise CasError("candidate is not the exact commit object")

    raw_commit = _run_git(
        operation,
        ["cat-file", "commit", candidate],
        stage="raw candidate parent validation",
    ).stdout
    headers, separator, _message = raw_commit.partition("\n\n")
    if not raw_commit or not separator:
        raise CasError("candidate must have exactly one parent equal to the prior SHA")
    parents: list[str] = []
    for header in headers.splitlines():
        if not header.startswith("parent "):
            continue
        parent = header.removeprefix("parent ")
        if not parent or not SHA_RE.fullmatch(parent):
            raise CasError("candidate must have exactly one parent equal to the prior SHA")
        parents.append(parent)
    if not parents or parents != [prior]:
        raise CasError("candidate must have exactly one parent equal to the prior SHA")


def _require_clean_fresh_candidate(operation: Operation, candidate: str) -> None:
    try:
        with tempfile.TemporaryDirectory(prefix="sonsteng-canonical-ref-cas-") as directory:
            checkout = pathlib.Path(directory) / "checkout"
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--quiet",
                    "--no-local",
                    "--no-checkout",
                    str(operation.repo),
                    str(checkout),
                ],
                stage="fresh candidate clone",
                cwd=operation.repo.parent,
            )
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    str(operation.repo),
                    candidate,
                ],
                stage="exact candidate fetch",
                cwd=checkout,
            )
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.symlinks=true",
                    "checkout",
                    "--quiet",
                    "--detach",
                    candidate,
                ],
                stage="exact candidate checkout",
                cwd=checkout,
            )
            head = _run_git(
                operation,
                ["rev-parse", "--verify", "HEAD"],
                stage="fresh candidate HEAD readback",
                cwd=checkout,
            ).stdout.strip()
            _require_exact_cleanliness(operation, cwd=checkout)
            if head != candidate:
                raise CasError("candidate tree is not clean in a fresh exact clone")
    except CasError as exc:
        if str(exc) == "candidate tree is not clean in a fresh exact clone":
            raise
        raise CasError("candidate tree could not be proved clean in a fresh exact clone") from None


def _push_main(
    operation: Operation, remote_url: str, expected_remote: str, target: str
) -> None:
    try:
        with tempfile.TemporaryDirectory(
            prefix="sonsteng-canonical-ref-cas-push-"
        ) as directory:
            push_source = pathlib.Path(directory) / "source.git"
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "init",
                    "--quiet",
                    "--bare",
                    str(push_source),
                ],
                stage="immutable push source initialization",
                cwd=operation.repo.parent,
            )
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    str(operation.repo),
                    target,
                ],
                stage="immutable push source fetch",
                cwd=push_source,
            )
            _require_non_shallow(
                operation,
                cwd=push_source,
                error="immutable push source must not be shallow",
            )
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "update-ref",
                    operation.local_ref,
                    target,
                ],
                stage="immutable push source preparation",
                cwd=push_source,
            )
            source = _run_git(
                operation,
                ["rev-parse", "--verify", operation.local_ref],
                stage="immutable push source readback",
                cwd=push_source,
            ).stdout.strip()
            if source != target:
                raise CasError("immutable push source readback mismatch")
            _run_git(
                operation,
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "push.followTags=false",
                    "-c",
                    "push.default=nothing",
                    "push",
                    "--no-verify",
                    "--no-follow-tags",
                    f"--force-with-lease={operation.branch}:{expected_remote}",
                    remote_url,
                    f"{operation.local_ref}:{operation.local_ref}",
                ],
                stage="remote main compare-and-swap",
                cwd=push_source,
            )
    except CasError as exc:
        if str(exc) == "Git operation failed during remote main compare-and-swap":
            raise
        raise CasError("remote main compare-and-swap preparation failed") from None


def _cas_local_main_and_align(operation: Operation, mutations: list[str]) -> None:
    _require_no_other_main_worktree(operation)
    before_error = (
        "local main changed before compensation CAS"
        if operation.verb == "restore"
        else "local main changed before forward CAS"
    )
    if (
        _local_sha(operation) != operation.from_sha
        or _head_sha(operation) != operation.from_sha
    ):
        raise CasError(before_error)
    _run_git(
        operation,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "update-ref",
            operation.local_ref,
            operation.to_sha,
            operation.from_sha,
        ],
        stage="local main compare-and-swap",
    )
    mutations.append("local-main-cas")
    if (
        _local_sha(operation) != operation.to_sha
        or _head_sha(operation) != operation.to_sha
    ):
        raise CasError("local main changed before worktree alignment")
    _run_git(
        operation,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.symlinks=true",
            "read-tree",
            "-m",
            "-u",
            operation.to_sha,
        ],
        stage="daemon worktree alignment",
    )
    mutations.append("worktree-alignment")
    if (
        _local_sha(operation) != operation.to_sha
        or _head_sha(operation) != operation.to_sha
    ):
        raise CasError("local main changed during worktree alignment")


def _cas_remote_tracking_main(
    operation: Operation, mutations: list[str]
) -> None:
    _run_git(
        operation,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "update-ref",
            operation.remote_tracking_ref,
            operation.to_sha,
            operation.from_sha,
        ],
        stage="remote-tracking main compare-and-swap",
    )
    mutations.append("remote-tracking-main-cas")
    observed = _remote_tracking_sha(operation)
    if not observed or observed != operation.to_sha:
        raise CasError("remote-tracking main compare-and-swap mismatch")


def _base_receipt(operation: Operation, mutations: list[str]) -> dict:
    return {
        "dry_run": operation.dry_run,
        "expected": {"from": None, "to": None},
        "mutations": mutations,
        "remote_url": None,
        "remote_url_sha256": None,
        "verb": operation.verb,
    }


def _receipt_remote_identity(remote_url: str) -> dict[str, str]:
    if not remote_url:
        raise CasError("validated remote URL was empty")
    fingerprint = hashlib.sha256(remote_url.encode("utf-8")).hexdigest()
    display = remote_url
    if "://" in remote_url:
        parsed = urlsplit(remote_url)
        if parsed.scheme:
            netloc = parsed.netloc.rsplit("@", 1)[-1]
            display = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        else:
            display = "[redacted]"
    if not display or not fingerprint:
        raise CasError("validated remote URL could not be recorded safely")
    return {"remote_url": display, "remote_url_sha256": fingerprint}


def _execute(operation: Operation) -> dict:
    mutations: list[str] = []
    receipt = _base_receipt(operation, mutations)
    operation_validated = False
    remote_url: str | None = None
    try:
        receipt["expected"] = _validated_coordinates(operation)
        _require_no_external_git_config_environment()
        remote_url = _require_operation(operation)
        receipt.update(_receipt_remote_identity(remote_url))
        operation_validated = True
        _require_checked_out_clean_main(operation, operation.from_sha, remote_url)
        baseline_local_refs = _local_ref_map(operation)
        tracking_sha = _remote_tracking_sha_from_map(operation, baseline_local_refs)
        if not tracking_sha or tracking_sha != operation.from_sha:
            raise CasError("remote-tracking main does not equal --from")
        prior, candidate = (
            (operation.from_sha, operation.to_sha)
            if operation.verb == "forward"
            else (operation.to_sha, operation.from_sha)
        )
        _require_one_commit_transition(operation, prior, candidate)

        if operation.verb == "forward":
            _require_clean_fresh_candidate(operation, candidate)
        _require_local_refs_unchanged(
            baseline_local_refs,
            _local_ref_map(operation),
        )

        baseline_remote = _remote_snapshot(operation, remote_url)
        remote_sha = _remote_sha_from_map(operation, baseline_remote.refs)
        if not remote_sha or remote_sha != operation.from_sha:
            raise CasError("remote main does not equal --from")
        baseline = RefBaseline(local=baseline_local_refs, remote=baseline_remote)

        if operation.dry_run:
            observed = _require_final_state(
                operation,
                operation.from_sha,
                remote_url,
                baseline,
            )
            receipt.update(result="success", readback=observed)
            return receipt

        # Repeat the local proof immediately before the first owned mutation.
        # This closes the validation window used by worktree/ref races during
        # candidate and remote inspection.
        _require_no_other_main_worktree(operation)
        _require_local_refs_unchanged(
            baseline.local,
            _local_ref_map(operation),
        )

        if operation.verb == "forward":
            _cas_local_main_and_align(operation, mutations)

        _push_main(
            operation,
            remote_url,
            operation.from_sha,
            operation.to_sha,
        )
        mutations.append("remote-main-cas")

        if operation.verb == "restore":
            # The remote CAS happens first.  The local ref then gets its own CAS;
            # update-ref cannot move main unless it still equals the candidate.
            _cas_local_main_and_align(operation, mutations)

        _cas_remote_tracking_main(operation, mutations)

        observed = _require_final_state(
            operation,
            operation.to_sha,
            remote_url,
            baseline,
        )
        receipt.update(result="success", readback=observed)
        return receipt
    except CasError as exc:
        if exc.error_code is not None:
            receipt["error_code"] = exc.error_code
        receipt.update(
            result="error",
            error=str(exc),
            readback=(
                _best_effort_readback(operation, remote_url)
                if operation_validated and remote_url is not None
                else {"head": None, "local": None, "remote": None}
            ),
        )
        raise CasFailure(receipt) from None


def forward(
    repo: pathlib.Path | str,
    remote: str,
    branch: str,
    from_sha: str,
    to_sha: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Fast-forward canonical main by one exact commit and CAS-push it."""
    return _execute(
        Operation(
            "forward",
            pathlib.Path(repo).expanduser().resolve(),
            remote,
            branch,
            from_sha,
            to_sha,
            dry_run,
        )
    )


def restore(
    repo: pathlib.Path | str,
    remote: str,
    branch: str,
    from_sha: str,
    to_sha: str,
    *,
    dry_run: bool = False,
) -> dict:
    """CAS canonical main from one exact candidate back to its exact prior."""
    return _execute(
        Operation(
            "restore",
            pathlib.Path(repo).expanduser().resolve(),
            remote,
            branch,
            from_sha,
            to_sha,
            dry_run,
        )
    )


class CanonicalRefCasAdapter:
    """The narrow method seam required by ``day_zero_migration.execute``."""

    def __init__(
        self,
        repo: pathlib.Path | str,
        remote: str = "origin",
        branch: str = "main",
    ):
        self.repo = pathlib.Path(repo)
        self.remote = remote
        self.branch = branch

    def restore_canonical_ref_exact(self, candidate_sha: str, prior_sha: str) -> str:
        result = restore(
            self.repo,
            self.remote,
            self.branch,
            candidate_sha,
            prior_sha,
        )
        return result["readback"]["local"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded Day Zero canonical-main compare-and-swap",
    )
    subparsers = parser.add_subparsers(dest="verb", required=True)
    for verb in ("forward", "restore"):
        command = subparsers.add_parser(verb)
        command.add_argument("--repo", required=True)
        command.add_argument("--remote", required=True)
        command.add_argument("--branch", required=True)
        command.add_argument("--from", dest="from_sha", required=True)
        command.add_argument("--to", dest="to_sha", required=True)
        command.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    operation = forward if args.verb == "forward" else restore
    try:
        result = operation(
            args.repo,
            args.remote,
            args.branch,
            args.from_sha,
            args.to_sha,
            dry_run=args.dry_run,
        )
    except CasFailure as exc:
        print(json.dumps(exc.receipt, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
