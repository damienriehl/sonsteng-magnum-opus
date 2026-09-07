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
from typing import Sequence


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
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in GIT_REPOSITORY_ENV:
        environment.pop(name, None)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
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


def _remote_sha(operation: Operation) -> str:
    completed = _run_git(
        operation,
        ["ls-remote", "--refs", "--exit-code", operation.remote, operation.local_ref],
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


def _readback(operation: Operation) -> dict[str, str]:
    return {
        "head": _head_sha(operation),
        "local": _local_sha(operation),
        "remote": _remote_sha(operation),
    }


def _best_effort_readback(operation: Operation) -> dict[str, str | None]:
    observed: dict[str, str | None] = {}
    for name, reader in (
        ("head", _head_sha),
        ("local", _local_sha),
        ("remote", _remote_sha),
    ):
        try:
            observed[name] = reader(operation)
        except CasError:
            observed[name] = None
    return observed


def _require_operation(operation: Operation) -> None:
    if operation.verb not in {"forward", "restore"}:
        raise CasError("unsupported operation")
    if not SHA_RE.fullmatch(operation.from_sha):
        raise CasError("--from must be an exact lowercase 40-character SHA")
    if not SHA_RE.fullmatch(operation.to_sha):
        raise CasError("--to must be an exact lowercase 40-character SHA")
    if operation.from_sha == operation.to_sha:
        raise CasError("--from and --to must differ")
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


def _require_checked_out_clean_main(operation: Operation, expected_sha: str) -> None:
    symbolic = _run_git(
        operation,
        ["symbolic-ref", "--quiet", "HEAD"],
        stage="checked-out branch validation",
        check=False,
    )
    if symbolic.returncode != 0 or symbolic.stdout.strip() != operation.local_ref:
        raise CasError("daemon worktree must have main checked out")
    status = _run_git(
        operation,
        ["status", "--porcelain", "--untracked-files=all"],
        stage="daemon worktree cleanliness",
    ).stdout
    if status.strip():
        raise CasError("daemon worktree must be clean")

    observed = _readback(operation)
    if observed["local"] != expected_sha:
        raise CasError("local main does not equal --from")
    if observed["head"] != expected_sha:
        raise CasError("daemon worktree HEAD does not equal --from")
    if observed["remote"] != expected_sha:
        raise CasError("remote main does not equal --from")


def _require_one_commit_transition(operation: Operation, prior: str, candidate: str) -> None:
    resolved = _run_git(
        operation,
        ["rev-parse", "--verify", f"{candidate}^{{commit}}"],
        stage="candidate commit validation",
    ).stdout.strip()
    if resolved != candidate:
        raise CasError("candidate is not the exact commit object")

    parent_line = _run_git(
        operation,
        ["rev-list", "--parents", "-n", "1", candidate],
        stage="candidate parent validation",
    ).stdout.strip()
    if parent_line.split() != [candidate, prior]:
        raise CasError("candidate must have exactly one parent equal to the prior SHA")

    count = _run_git(
        operation,
        ["rev-list", "--count", f"{prior}..{candidate}"],
        stage="candidate commit-count validation",
    ).stdout.strip()
    if count != "1":
        raise CasError("prior-to-candidate range is not exactly one commit")


def _require_clean_fresh_candidate(operation: Operation, candidate: str) -> None:
    try:
        with tempfile.TemporaryDirectory(prefix="sonsteng-canonical-ref-cas-") as directory:
            checkout = pathlib.Path(directory) / "checkout"
            _run_git(
                operation,
                [
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
                ["fetch", "--quiet", "--no-tags", str(operation.repo), candidate],
                stage="exact candidate fetch",
                cwd=checkout,
            )
            _run_git(
                operation,
                ["checkout", "--quiet", "--detach", candidate],
                stage="exact candidate checkout",
                cwd=checkout,
            )
            head = _run_git(
                operation,
                ["rev-parse", "--verify", "HEAD"],
                stage="fresh candidate HEAD readback",
                cwd=checkout,
            ).stdout.strip()
            status = _run_git(
                operation,
                ["status", "--porcelain", "--untracked-files=all"],
                stage="fresh candidate cleanliness",
                cwd=checkout,
            ).stdout
            if head != candidate or status.strip():
                raise CasError("candidate tree is not clean in a fresh exact clone")
    except CasError as exc:
        if str(exc) == "candidate tree is not clean in a fresh exact clone":
            raise
        raise CasError("candidate tree could not be proved clean in a fresh exact clone") from None


def _base_receipt(operation: Operation, mutations: list[str]) -> dict:
    return {
        "dry_run": operation.dry_run,
        "expected": {"from": operation.from_sha, "to": operation.to_sha},
        "mutations": mutations,
        "verb": operation.verb,
    }


def _execute(operation: Operation) -> dict:
    mutations: list[str] = []
    receipt = _base_receipt(operation, mutations)
    operation_validated = False
    try:
        _require_operation(operation)
        operation_validated = True
        _require_checked_out_clean_main(operation, operation.from_sha)
        prior, candidate = (
            (operation.from_sha, operation.to_sha)
            if operation.verb == "forward"
            else (operation.to_sha, operation.from_sha)
        )
        _require_one_commit_transition(operation, prior, candidate)

        if operation.verb == "forward":
            _require_clean_fresh_candidate(operation, candidate)

        if operation.dry_run:
            receipt.update(result="success", readback=_readback(operation))
            return receipt

        if operation.verb == "forward":
            _run_git(
                operation,
                ["merge", "--ff-only", "--no-edit", operation.to_sha],
                stage="local main fast-forward",
            )
            mutations.append("local-main-fast-forward")
            local = _local_sha(operation)
            head = _head_sha(operation)
            if local != operation.to_sha or head != operation.to_sha:
                raise CasError("local main fast-forward readback mismatch")
            _run_git(
                operation,
                [
                    "push",
                    f"--force-with-lease={operation.branch}:{operation.from_sha}",
                    operation.remote,
                    f"{operation.to_sha}:{operation.local_ref}",
                ],
                stage="remote main compare-and-swap",
            )
            mutations.append("remote-main-cas")
        else:
            _run_git(
                operation,
                [
                    "push",
                    f"--force-with-lease={operation.branch}:{operation.from_sha}",
                    operation.remote,
                    f"{operation.to_sha}:{operation.local_ref}",
                ],
                stage="remote main compare-and-swap",
            )
            mutations.append("remote-main-cas")

            # The remote CAS happens first.  The local ref then gets its own CAS;
            # update-ref cannot move main unless it still equals the candidate.
            if (
                _local_sha(operation) != operation.from_sha
                or _head_sha(operation) != operation.from_sha
            ):
                raise CasError("local main changed before compensation CAS")
            _run_git(
                operation,
                [
                    "update-ref",
                    operation.local_ref,
                    operation.to_sha,
                    operation.from_sha,
                ],
                stage="local main compare-and-swap",
            )
            mutations.append("local-main-cas")
            _run_git(
                operation,
                ["reset", "--hard", operation.to_sha],
                stage="daemon worktree alignment",
            )
            mutations.append("worktree-reset")

        observed = _readback(operation)
        if any(value != operation.to_sha for value in observed.values()):
            raise CasError("exact canonical ref readback mismatch")
        receipt.update(result="success", readback=observed)
        return receipt
    except CasError as exc:
        receipt.update(
            result="error",
            error=str(exc),
            readback=(
                _best_effort_readback(operation)
                if operation_validated
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
