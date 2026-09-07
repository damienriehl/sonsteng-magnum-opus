#!/usr/bin/env python3
"""Bounded compare-and-swap operations for the Day Zero canonical ref.

This tool intentionally owns only the one-commit ``main`` transition and its
exact candidate-to-prior compensation.  It is not a general-purpose Git ref
writer.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Mapping, Sequence


SHA_RE = re.compile(r"[0-9a-f]{40}")
REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT_SECONDS = 120
GIT_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


class CasError(RuntimeError):
    """A bounded canonical-ref safety failure."""

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class CasFailure(CasError):
    """A failed operation with a path-free JSON receipt."""

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


def _run_git(
    operation: Operation,
    args: Sequence[str],
    *,
    stage: str,
    check: bool = True,
    cwd: pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in GIT_REPOSITORY_ENV:
        environment.pop(name, None)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    if env:
        environment.update(env)
    try:
        completed = subprocess.run(
            ["git", *args],
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


def _local_sha(operation: Operation) -> str:
    value = _run_git(
        operation,
        ["rev-parse", "--verify", operation.local_ref],
        stage="local main readback",
    ).stdout.strip()
    if not SHA_RE.fullmatch(value):
        raise CasError("local main readback was not an exact SHA")
    return value


def _head_sha(operation: Operation) -> str:
    value = _run_git(
        operation, ["rev-parse", "--verify", "HEAD"], stage="worktree HEAD readback"
    ).stdout.strip()
    if not SHA_RE.fullmatch(value):
        raise CasError("worktree HEAD readback was not an exact SHA")
    return value


def _remote_ref_map(operation: Operation, remote_url: str) -> dict[str, str]:
    completed = _run_git(
        operation,
        ["ls-remote", "--refs", remote_url],
        stage="remote visible ref map readback",
        check=False,
    )
    if completed.returncode != 0:
        raise CasError("remote refs could not be read exactly")

    refs: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise CasError("remote refs could not be read exactly")
        value, ref = fields
        if (
            not SHA_RE.fullmatch(value)
            or not ref.startswith("refs/")
            or ref in refs
        ):
            raise CasError("remote refs could not be read exactly")
        refs[ref] = value
    return refs


def _remote_sha_from_map(operation: Operation, refs: Mapping[str, str]) -> str:
    value = refs.get(operation.local_ref)
    if value is None or not SHA_RE.fullmatch(value):
        raise CasError("remote main readback was not an exact SHA")
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
    changed_or_removed = any(
        ref != operation.local_ref and after.get(ref) != sha
        for ref, sha in before.items()
    )
    added = any(
        ref != operation.local_ref and ref not in before for ref in after
    )
    if changed_or_removed or added:
        raise CasError("remote ref map changed outside canonical main")


def _readback(
    operation: Operation,
    remote_url: str,
    *,
    baseline_remote_refs: Mapping[str, str] | None = None,
) -> dict[str, str]:
    head = _head_sha(operation)
    local = _local_sha(operation)
    remote_refs = _remote_ref_map(operation, remote_url)
    if baseline_remote_refs is not None:
        _require_remote_ref_delta(operation, baseline_remote_refs, remote_refs)
    return {
        "head": head,
        "local": local,
        "remote": _remote_sha_from_map(operation, remote_refs),
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
    if len(fetch_urls) != 1 or len(push_urls) != 1 or fetch_urls != push_urls:
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
    if fetch_urls != [remote_url] or push_urls != [remote_url]:
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
    baseline_remote_refs: Mapping[str, str],
) -> dict[str, str]:
    _require_symbolic_main(operation)
    _require_exact_cleanliness(operation)
    _require_remote_url_unchanged(operation, remote_url)
    observed = _readback(
        operation,
        remote_url,
        baseline_remote_refs=baseline_remote_refs,
    )
    if any(value != expected_sha for value in observed.values()):
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


def _base_receipt(operation: Operation, mutations: list[str]) -> dict:
    return {
        "dry_run": operation.dry_run,
        "expected": {"from": None, "to": None},
        "mutations": mutations,
        "verb": operation.verb,
    }


def _execute(operation: Operation) -> dict:
    mutations: list[str] = []
    receipt = _base_receipt(operation, mutations)
    operation_validated = False
    remote_url: str | None = None
    try:
        receipt["expected"] = _validated_coordinates(operation)
        remote_url = _require_operation(operation)
        operation_validated = True
        _require_checked_out_clean_main(operation, operation.from_sha, remote_url)
        prior, candidate = (
            (operation.from_sha, operation.to_sha)
            if operation.verb == "forward"
            else (operation.to_sha, operation.from_sha)
        )
        _require_one_commit_transition(operation, prior, candidate)

        if operation.verb == "forward":
            _require_clean_fresh_candidate(operation, candidate)

        baseline_remote_refs = _remote_ref_map(operation, remote_url)
        if (
            _remote_sha_from_map(operation, baseline_remote_refs)
            != operation.from_sha
        ):
            raise CasError("remote main does not equal --from")

        if operation.dry_run:
            observed = _require_final_state(
                operation,
                operation.from_sha,
                remote_url,
                baseline_remote_refs,
            )
            receipt.update(result="success", readback=observed)
            return receipt

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

        observed = _require_final_state(
            operation,
            operation.to_sha,
            remote_url,
            baseline_remote_refs,
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
