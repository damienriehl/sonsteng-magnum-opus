#!/usr/bin/env python3
"""Bounded compare-and-swap operations for the Day Zero canonical ref.

This tool intentionally owns only the one-commit ``main`` transition and its
exact candidate-to-prior compensation.  It is not a general-purpose Git ref
writer.
"""

from __future__ import annotations

import argparse
import datetime
import enum
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
TIMEOUT_SECONDS = 120
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
PROCESS_INJECTION_ENV_NAMES = frozenset(
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
PROCESS_INJECTION_ENV_PREFIXES = ("LD_",)
TOOL_PATH = str(pathlib.Path(__file__).resolve(strict=True))
TOOL_SHA256 = hashlib.sha256(pathlib.Path(TOOL_PATH).read_bytes()).hexdigest()
REMOTE_QUERY_ROOT = pathlib.Path(os.devnull).parent
HOST_IDENTITY = os.uname().nodename
MUTATION_LOCAL_MAIN_CAS = "local-main-cas"
MUTATION_WORKTREE_ALIGNMENT = "worktree-alignment"
MUTATION_REMOTE_MAIN_CAS = "remote-main-cas"
MUTATION_REMOTE_TRACKING_MAIN_CAS = "remote-tracking-main-cas"
FORWARD_MUTATIONS = (
    MUTATION_LOCAL_MAIN_CAS,
    MUTATION_WORKTREE_ALIGNMENT,
    MUTATION_REMOTE_MAIN_CAS,
    MUTATION_REMOTE_TRACKING_MAIN_CAS,
)
RESTORE_MUTATIONS = (
    MUTATION_REMOTE_MAIN_CAS,
    MUTATION_LOCAL_MAIN_CAS,
    MUTATION_WORKTREE_ALIGNMENT,
    MUTATION_REMOTE_TRACKING_MAIN_CAS,
)


class TransitionOutcome(enum.StrEnum):
    INCOMPLETE = "incomplete"
    LANDED_VERIFICATION_INCOMPLETE = "landed-verification-incomplete"
    NOT_ATTEMPTED = "not-attempted"
    NOT_LANDED = "not-landed"
    SUCCEEDED = "succeeded"
    TARGET_ALREADY_PRESENT = "target-already-present"
    UNDETERMINED = "undetermined"


class TransitionOutcomeSource(enum.StrEnum):
    FAILURE_HANDLER_FALLBACK = "failure-handler-fallback"
    OUTERMOST_FALLBACK = "outermost-fallback"
    POST_FAILURE_READBACK = "post-failure-readback"
    PRE_OPERATION_EVIDENCE = "pre-operation-evidence"


TRANSITION_OUTCOMES = frozenset(outcome.value for outcome in TransitionOutcome)


def _undetermined_failure_fields(
    exc: BaseException, source: TransitionOutcomeSource
) -> dict[str, str]:
    interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
    return {
        "result": "error",
        "error": (
            "operation interrupted" if interrupted else "unexpected internal failure"
        ),
        "error_code": "interrupted" if interrupted else "unexpected-exception",
        "transition_outcome": TransitionOutcome.UNDETERMINED.value,
        "transition_outcome_source": source.value,
    }


def _resolve_git_path() -> str | None:
    """Resolve Git once from the OS-defined trusted utility path."""
    try:
        system_path = os.confstr("CS_PATH")
    except (AttributeError, OSError, ValueError):
        return None
    if not system_path or any(
        not entry or not os.path.isabs(entry)
        for entry in system_path.split(os.pathsep)
    ):
        return None
    candidate = shutil.which("git", path=system_path)
    if candidate is None:
        return None
    try:
        resolved = pathlib.Path(candidate).resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return str(resolved)


GIT_PATH = _resolve_git_path()


class CasError(RuntimeError):
    """A bounded canonical-ref safety failure."""

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class CasFailure(CasError):
    """A failed operation with a structured JSON receipt."""

    def __init__(self, receipt: dict):
        super().__init__(receipt.get("error", receipt.get("warning", "CAS failed")))
        self.receipt = receipt


def _redact_url_userinfo(value: str, *, url_field: bool = False) -> str:
    embedded_scheme_userinfo = re.search(
        r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/?#]*@[^\s/?#]+", value
    )
    if embedded_scheme_userinfo and embedded_scheme_userinfo.start() != 0:
        return "[redacted]"
    embedded_scp_userinfo = re.search(
        r"(?<![A-Za-z0-9._+-])[A-Za-z0-9._+-]+@[^/:\s]+:[^\s]+", value
    )
    if embedded_scp_userinfo and embedded_scp_userinfo.start() != 0:
        return "[redacted]"
    if "://" in value:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return "[redacted]"
        if not parsed.scheme:
            return "[redacted]" if url_field else value
        if "@" not in parsed.netloc:
            return value
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    if "@" not in value or os.path.isabs(value):
        return value
    userinfo, scp_target = value.split("@", 1)
    if ":" in userinfo:
        # A scheme-less ``user:password@host[/path]`` carries a credential.
        return "[redacted]"
    scp_host, separator, scp_path = scp_target.partition(":")
    if (
        separator
        and re.fullmatch(r"[^/:\s@]+", userinfo)
        and scp_host
        and "/" not in scp_host
        and scp_path
    ):
        # A later ``@`` in the scp path would make the stripped value look like
        # ``user:password@host`` on the next pass; collapse it so redaction is
        # idempotent. ``remote_url_sha256`` still identifies the exact URL.
        return "[redacted]" if "@" in scp_path else scp_target
    return "[redacted]" if url_field else value


def _receipt_safe_value(value, *, field_name: str | None = None):
    if isinstance(value, str):
        return _redact_url_userinfo(
            value,
            url_field=field_name is not None and "url" in field_name.lower(),
        )
    if isinstance(value, Mapping):
        return {
            _receipt_safe_key(key): _receipt_safe_value(
                nested, field_name=str(key)
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            _receipt_safe_value(nested, field_name=field_name)
            for nested in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _receipt_safe_value(nested, field_name=field_name) for nested in value
        )
    return value


def _receipt_safe_key(key):
    if isinstance(key, str):
        return _redact_url_userinfo(key)
    return key


class _Receipt(dict):
    """A receipt whose every inserted field passes through URL-userinfo redaction."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.update(*args, **kwargs)

    def __setitem__(self, key, value) -> None:
        super().__setitem__(
            _receipt_safe_key(key),
            _receipt_safe_value(value, field_name=str(key)),
        )

    def update(self, *args, **kwargs) -> None:
        fields = dict(*args, **kwargs)
        for key, value in fields.items():
            self[key] = value

    def setdefault(self, key, default=None):
        safe_key = _receipt_safe_key(key)
        if safe_key not in self:
            self[key] = default
        return self[safe_key]

    def __ior__(self, other):
        self.update(other)
        return self


@dataclass(frozen=True)
class Operation:
    verb: str
    repo: pathlib.Path
    remote: str
    branch: str
    from_sha: str
    to_sha: str
    dry_run: bool
    expected_remote_url_sha256: str | None = None
    window_owner: str | None = None

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


def _require_safe_process_environment() -> None:
    _require_no_external_git_config_environment()
    if any(
        name.startswith(PROCESS_INJECTION_ENV_PREFIXES)
        or name in PROCESS_INJECTION_ENV_NAMES
        for name in os.environ
    ):
        raise CasError(
            "process injection environment is not permitted",
            error_code="unsafe-process-environment",
        )


def _run_git(
    operation: Operation,
    args: Sequence[str],
    *,
    stage: str,
    check: bool = True,
    cwd: pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if GIT_PATH is None:
        raise CasError(f"Git operation failed during {stage}")
    environment: dict[str, str] = {}
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
            "GIT_SSH_COMMAND": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    try:
        completed = subprocess.run(
            [GIT_PATH, *args],
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
        cwd=REMOTE_QUERY_ROOT,
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
        or any(base_ref not in refs for base_ref in peeled)
    ):
        raise CasError("remote ref snapshot could not be read exactly")
    # Apply identity only to an advertised symbolic target so presence and
    # advertised-object identity remain independently testable protections.
    if head is not None and refs.get(head) != head_sha:
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
        cwd=REMOTE_QUERY_ROOT,
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
                not before_symref
                or not after_symref
                or before_symref != after_symref
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
) -> tuple[dict[str, str | None], dict[str, str]]:
    observed: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for name, reader in (
        ("head", _head_sha),
        ("local", _local_sha),
        ("remote", lambda current: _remote_sha(current, remote_url)),
    ):
        try:
            observed[name] = reader(operation)
        except BaseException:
            observed[name] = None
            errors[name] = f"{name} readback unavailable"
    return observed, errors


def _intended_mutations(operation: Operation) -> tuple[str, ...]:
    return FORWARD_MUTATIONS if operation.verb == "forward" else RESTORE_MUTATIONS


def _transition_outcome(
    operation: Operation,
    mutations: Sequence[str],
    readback: Mapping[str, str | None],
    *,
    unexpected_observation: bool,
    remote_main_attempted: bool = False,
) -> str:
    if operation.dry_run:
        return TransitionOutcome.NOT_ATTEMPTED.value
    target_observed = readback == dict.fromkeys(
        ("head", "local", "remote"), operation.to_sha
    )
    complete_ledger = tuple(mutations) == _intended_mutations(operation)
    no_contradicting_readback = all(
        value is None or value == operation.to_sha for value in readback.values()
    )
    if complete_ledger and target_observed and not unexpected_observation:
        return TransitionOutcome.SUCCEEDED.value
    target_already_present = not mutations and target_observed
    if target_already_present:
        return TransitionOutcome.TARGET_ALREADY_PRESENT.value
    if target_observed or (complete_ledger and no_contradicting_readback):
        return TransitionOutcome.LANDED_VERIFICATION_INCOMPLETE.value
    if not mutations and remote_main_attempted and readback.get("remote") is None:
        return TransitionOutcome.INCOMPLETE.value
    if not mutations:
        return TransitionOutcome.NOT_LANDED.value
    return TransitionOutcome.INCOMPLETE.value


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


def _validated_remote_expectation(operation: Operation) -> str:
    expected = operation.expected_remote_url_sha256
    if expected is None:
        raise CasError(
            "--expect-remote-url-sha256 is required",
            error_code="invalid-remote-expectation",
        )
    if not SHA256_RE.fullmatch(expected):
        raise CasError(
            "--expect-remote-url-sha256 must be an exact lowercase SHA-256 digest",
            error_code="invalid-remote-expectation",
        )
    return expected


def _validated_window_owner(operation: Operation) -> str:
    owner = operation.window_owner
    if owner is None or not owner:
        raise CasError(
            "--window-owner is required",
            error_code="invalid-window-owner",
        )
    if (
        len(owner) > 256
        or owner.strip() != owner
        or "@" in owner
        or any(not character.isprintable() for character in owner)
    ):
        raise CasError(
            "--window-owner must be a non-empty printable value "
            "of at most 256 characters without '@'",
            error_code="invalid-window-owner",
        )
    if not HOST_IDENTITY or len(HOST_IDENTITY) > 255:
        raise CasError("host identity could not be recorded safely")
    return owner


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
    if not holders:
        raise CasError("daemon worktree must have main checked out")
    if holders != [operation.repo]:
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
    url_rewrites = _run_git(
        operation,
        [
            "config",
            "--includes",
            "--get-regexp",
            r"^url\..*\.(insteadOf|pushInsteadOf)$",
        ],
        stage="remote URL rewrite validation",
        check=False,
    )
    if url_rewrites.returncode not in {0, 1} or url_rewrites.stdout.strip():
        raise CasError("daemon repository must not configure remote URL rewrites")
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
        prefix="sonsteng-canonical-ref-cas-index-",
        dir=repository.parent,
        ignore_cleanup_errors=True,
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
        with tempfile.TemporaryDirectory(
            prefix="sonsteng-canonical-ref-cas-",
            dir=operation.repo.parent,
            ignore_cleanup_errors=True,
        ) as directory:
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
            prefix="sonsteng-canonical-ref-cas-push-",
            dir=operation.repo.parent,
            ignore_cleanup_errors=True,
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
    mutations.append(MUTATION_LOCAL_MAIN_CAS)
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
    mutations.append(MUTATION_WORKTREE_ALIGNMENT)
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
    mutations.append(MUTATION_REMOTE_TRACKING_MAIN_CAS)
    observed = _remote_tracking_sha(operation)
    if not observed or observed != operation.to_sha:
        raise CasError("remote-tracking main compare-and-swap mismatch")


def _base_receipt(operation: Operation, mutations: list[str] | None) -> _Receipt:
    return _Receipt(
        {
            "branch": None,
            "dry_run": operation.dry_run,
            "expected": {"from": None, "to": None},
            "expected_remote_url_sha256": None,
            "git_executable": GIT_PATH,
            "host_identity": HOST_IDENTITY,
            "mutations": mutations,
            "remote": None,
            "remote_url": None,
            "remote_url_sha256": None,
            "repo": None,
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "tool": {"path": TOOL_PATH, "sha256": TOOL_SHA256},
            "verb": operation.verb,
            "window_owner": None,
        }
    )


def _receipt_remote_identity(remote_url: str) -> dict[str, str]:
    if not remote_url:
        raise CasError("validated remote URL was empty")
    fingerprint = hashlib.sha256(remote_url.encode("utf-8")).hexdigest()
    display = _redact_url_userinfo(remote_url, url_field=True)
    if not display or not fingerprint:
        raise CasError("validated remote URL could not be recorded safely")
    return {"remote_url": display, "remote_url_sha256": fingerprint}


def _require_expected_remote_url(
    operation: Operation,
    remote_url: str,
    remote_url_sha256: str,
) -> None:
    if remote_url_sha256 != operation.expected_remote_url_sha256:
        raise CasError("configured remote URL does not match operator expectation")
    try:
        parsed = urlsplit(remote_url)
    except ValueError:
        raise CasError("remote URL could not be parsed safely") from None
    scheme = parsed.scheme.lower()
    if scheme == "ssh" or (not scheme and ":" in remote_url):
        raise CasError("SSH remote transport is not permitted")
    if scheme:
        if scheme not in {"file", "http", "https"}:
            raise CasError("remote URL transport is not permitted")
        if scheme == "file" and not pathlib.PurePosixPath(parsed.path).is_absolute():
            raise CasError("local remote URL must be absolute")
    elif not pathlib.Path(remote_url).is_absolute():
        raise CasError("local remote URL must be absolute")


def _require_remote_head_main(operation: Operation, snapshot: RemoteSnapshot) -> None:
    if snapshot.head != operation.local_ref:
        raise CasError("remote HEAD must resolve to refs/heads/main")


def _execute(operation: Operation) -> dict:
    receipt = _base_receipt(operation, [])
    mutations: list[str] = receipt["mutations"]
    operation_validated = False
    remote_main_attempted = False
    remote_url: str | None = None
    try:
        receipt["expected"] = _validated_coordinates(operation)
        receipt["expected_remote_url_sha256"] = _validated_remote_expectation(operation)
        receipt["window_owner"] = _validated_window_owner(operation)
        _require_safe_process_environment()
        remote_url = _require_operation(operation)
        receipt.update(
            branch=operation.branch,
            remote=operation.remote,
            repo=str(operation.repo),
        )
        remote_identity = _receipt_remote_identity(remote_url)
        receipt.update(remote_identity)
        _require_expected_remote_url(
            operation,
            remote_url,
            remote_identity["remote_url_sha256"],
        )
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
        _require_remote_head_main(operation, baseline_remote)
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
            receipt.update(
                result="success",
                readback=observed,
                transition_outcome=_transition_outcome(
                    operation,
                    mutations,
                    observed,
                    unexpected_observation=False,
                ),
            )
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

        remote_main_attempted = True
        _push_main(
            operation,
            remote_url,
            operation.from_sha,
            operation.to_sha,
        )
        mutations.append(MUTATION_REMOTE_MAIN_CAS)

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
        receipt.update(
            result="success",
            readback=observed,
            transition_outcome=_transition_outcome(
                operation,
                mutations,
                observed,
                unexpected_observation=False,
            ),
        )
        return receipt
    except CasError as exc:
        message = str(exc)
        error_code = exc.error_code
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            message = "operation interrupted"
            error_code = "interrupted"
        else:
            message = "unexpected internal failure"
            error_code = "unexpected-exception"

    readback: dict[str, str | None] = {
        "head": None,
        "local": None,
        "remote": None,
    }
    readback_errors: dict[str, str] = {}
    try:
        if operation_validated and remote_url is not None:
            readback, readback_errors = _best_effort_readback(operation, remote_url)
            outcome_source = TransitionOutcomeSource.POST_FAILURE_READBACK.value
        else:
            outcome_source = TransitionOutcomeSource.PRE_OPERATION_EVIDENCE.value
        if (
            remote_main_attempted
            and MUTATION_REMOTE_MAIN_CAS not in mutations
            and readback["remote"] == operation.to_sha
        ):
            mutations.append(MUTATION_REMOTE_MAIN_CAS)
            receipt["mutation_reconciliation"] = {
                MUTATION_REMOTE_MAIN_CAS: "confirmed-by-post-failure-readback"
            }
        transition_outcome = _transition_outcome(
            operation,
            mutations,
            readback,
            unexpected_observation=True,
            remote_main_attempted=remote_main_attempted,
        )
        if transition_outcome == TransitionOutcome.LANDED_VERIFICATION_INCOMPLETE:
            receipt.update(
                result="warning",
                warning=message,
                readback=readback,
                transition_outcome=transition_outcome,
            )
        else:
            receipt.update(
                result="error",
                error=message,
                readback=readback,
                transition_outcome=transition_outcome,
            )
        receipt["transition_outcome_source"] = outcome_source
        if readback_errors:
            receipt["readback_errors"] = readback_errors
        if error_code is not None:
            receipt["error_code"] = error_code
    except BaseException as handler_exc:
        receipt.pop("warning", None)
        receipt.update(
            readback=readback,
            **_undetermined_failure_fields(
                handler_exc,
                TransitionOutcomeSource.FAILURE_HANDLER_FALLBACK,
            ),
        )
        if readback_errors:
            receipt["readback_errors"] = readback_errors
    raise CasFailure(receipt) from None


def forward(
    repo: pathlib.Path | str,
    remote: str,
    branch: str,
    from_sha: str,
    to_sha: str,
    *,
    dry_run: bool = False,
    expected_remote_url_sha256: str | None = None,
    window_owner: str | None = None,
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
            expected_remote_url_sha256,
            window_owner,
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
    expected_remote_url_sha256: str | None = None,
    window_owner: str | None = None,
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
            expected_remote_url_sha256,
            window_owner,
        )
    )


class CanonicalRefCasAdapter:
    """The narrow method seam required by ``day_zero_migration.execute``."""

    def __init__(
        self,
        repo: pathlib.Path | str,
        remote: str = "origin",
        branch: str = "main",
        *,
        expected_remote_url_sha256: str,
        window_owner: str,
        receipt_path: pathlib.Path | str,
    ):
        self.repo = pathlib.Path(repo)
        self.remote = remote
        self.branch = branch
        self.expected_remote_url_sha256 = expected_remote_url_sha256
        self.window_owner = window_owner
        self.receipt_path = pathlib.Path(receipt_path)

    def restore_canonical_ref_exact(self, candidate_sha: str, prior_sha: str) -> str:
        result = _execute_with_receipt_path(
            self.receipt_path,
            lambda: restore(
                self.repo,
                self.remote,
                self.branch,
                candidate_sha,
                prior_sha,
                expected_remote_url_sha256=self.expected_remote_url_sha256,
                window_owner=self.window_owner,
            ),
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
        command.add_argument("--expect-remote-url-sha256")
        command.add_argument("--window-owner")
        command.add_argument("--receipt-path", required=True)
        command.add_argument("--dry-run", action="store_true")
    return parser


def _open_receipt(path: str) -> tuple[int, int, str, str]:
    target = pathlib.Path(path)
    if not target.is_absolute():
        raise OSError
    directory_descriptor = os.open(
        target.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        try:
            os.stat(
                target.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError
        for _attempt in range(128):
            temporary_filename = (
                f".{target.name}.{secrets.token_hex(16)}.tmp"
            )
            try:
                receipt_descriptor = os.open(
                    temporary_filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                continue
            break
        else:
            raise OSError
    except BaseException:
        os.close(directory_descriptor)
        raise
    return (
        receipt_descriptor,
        directory_descriptor,
        temporary_filename,
        target.name,
    )


def _receipt_payload(receipt: dict) -> bytes:
    return (
        json.dumps(
            _Receipt(receipt),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_receipt(
    file_descriptor: int,
    directory_descriptor: int,
    temporary_filename: str,
    filename: str,
    payload: bytes,
) -> None:
    _write_all(file_descriptor, payload)
    os.fsync(file_descriptor)
    opened = os.fstat(file_descriptor)
    named = os.stat(
        temporary_filename,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OSError
    os.link(
        temporary_filename,
        filename,
        src_dir_fd=directory_descriptor,
        dst_dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    os.fsync(directory_descriptor)
    os.unlink(temporary_filename, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)


def _write_all(file_descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(file_descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _best_effort_mirror(file_descriptor: int, payload: bytes) -> None:
    try:
        _write_all(file_descriptor, payload)
    except OSError:
        pass


def _best_effort_diagnostic(message: str) -> None:
    _best_effort_mirror(2, (message + "\n").encode("utf-8"))


def _best_effort_unlink(directory_descriptor: int, filename: str) -> None:
    try:
        os.unlink(filename, dir_fd=directory_descriptor)
    except OSError:
        pass


def _ensure_standard_streams() -> None:
    for target_descriptor in (1, 2):
        try:
            os.fstat(target_descriptor)
        except OSError:
            null_descriptor = os.open(os.devnull, os.O_WRONLY)
            if null_descriptor != target_descriptor:
                try:
                    os.dup2(null_descriptor, target_descriptor)
                finally:
                    os.close(null_descriptor)


def _execute_with_receipt_path(
    path: pathlib.Path | str, operation: Callable[[], dict]
) -> dict:
    try:
        (
            receipt_descriptor,
            directory_descriptor,
            temporary_filename,
            filename,
        ) = _open_receipt(str(path))
    except OSError:
        raise CasError("canonical-ref CAS receipt file could not be opened") from None
    failure: CasFailure | None = None
    receipt_written = False
    try:
        try:
            result = operation()
        except CasFailure as exc:
            result = exc.receipt
            failure = exc
        payload: bytes | None = None
        try:
            payload = _receipt_payload(result)
            _write_receipt(
                receipt_descriptor,
                directory_descriptor,
                temporary_filename,
                filename,
                payload,
            )
            receipt_written = True
        except OSError:
            if payload is not None:
                _best_effort_mirror(2, payload)
            _best_effort_diagnostic(
                "canonical-ref CAS receipt file could not be written"
            )
            raise CasError("canonical-ref CAS receipt file could not be written") from None
        except BaseException as exc:
            if payload is not None:
                _best_effort_mirror(2, payload)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                _best_effort_diagnostic(
                    "canonical-ref CAS receipt publication interrupted"
                )
                raise CasError(
                    "canonical-ref CAS receipt publication interrupted",
                    error_code="interrupted",
                ) from None
            _best_effort_diagnostic(
                "unexpected internal failure during canonical-ref CAS receipt publication"
            )
            raise CasError(
                "unexpected internal failure during canonical-ref CAS receipt publication",
                error_code="unexpected-exception",
            ) from None
    finally:
        if not receipt_written:
            _best_effort_unlink(directory_descriptor, temporary_filename)
        try:
            os.close(receipt_descriptor)
        except OSError:
            pass
        try:
            os.close(directory_descriptor)
        except OSError:
            pass
    if failure is not None:
        raise failure
    return result


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_standard_streams()
    args = _parser().parse_args(argv)
    try:
        (
            receipt_descriptor,
            receipt_directory_descriptor,
            receipt_temporary_filename,
            receipt_filename,
        ) = _open_receipt(args.receipt_path)
    except OSError:
        _best_effort_diagnostic("canonical-ref CAS receipt file could not be opened")
        return 1
    receipt_written = False
    try:
        operation = forward if args.verb == "forward" else restore
        try:
            result = operation(
                args.repo,
                args.remote,
                args.branch,
                args.from_sha,
                args.to_sha,
                dry_run=args.dry_run,
                expected_remote_url_sha256=args.expect_remote_url_sha256,
                window_owner=args.window_owner,
            )
        except CasFailure as exc:
            result = exc.receipt
            result_code = 130 if result.get("error_code") == "interrupted" else 1
            mirror_descriptor = 2
        except BaseException as exc:
            fallback_operation = Operation(
                args.verb,
                pathlib.Path(args.repo),
                args.remote,
                args.branch,
                args.from_sha,
                args.to_sha,
                args.dry_run,
                args.expect_remote_url_sha256,
                args.window_owner,
            )
            result = _base_receipt(fallback_operation, None)
            readback = {"head": None, "local": None, "remote": None}
            result.update(
                branch=args.branch,
                expected={"from": args.from_sha, "to": args.to_sha},
                expected_remote_url_sha256=args.expect_remote_url_sha256,
                readback=readback,
                remote=(
                    args.remote
                    if isinstance(args.remote, str)
                    and REMOTE_RE.fullmatch(args.remote)
                    else "[redacted]"
                ),
                repo=args.repo,
                window_owner=args.window_owner,
                **_undetermined_failure_fields(
                    exc,
                    TransitionOutcomeSource.OUTERMOST_FALLBACK,
                ),
            )
            interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
            result_code = 130 if interrupted else 1
            mirror_descriptor = 2
        else:
            result_code = 0
            mirror_descriptor = 1
        payload: bytes | None = None
        try:
            payload = _receipt_payload(result)
            _write_receipt(
                receipt_descriptor,
                receipt_directory_descriptor,
                receipt_temporary_filename,
                receipt_filename,
                payload,
            )
            receipt_written = True
        except OSError:
            if payload is not None:
                _best_effort_mirror(2, payload)
            _best_effort_diagnostic(
                "canonical-ref CAS receipt file could not be written"
            )
            return 1
        except BaseException as exc:
            if payload is not None:
                _best_effort_mirror(2, payload)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                _best_effort_diagnostic(
                    "canonical-ref CAS receipt publication interrupted"
                )
                return 130
            _best_effort_diagnostic(
                "unexpected internal failure during canonical-ref CAS receipt publication"
            )
            return 1
    finally:
        if receipt_directory_descriptor >= 0 and not receipt_written:
            _best_effort_unlink(
                receipt_directory_descriptor,
                receipt_temporary_filename,
            )
        if receipt_descriptor >= 0:
            try:
                os.close(receipt_descriptor)
            except OSError:
                pass
        if receipt_directory_descriptor >= 0:
            try:
                os.close(receipt_directory_descriptor)
            except OSError:
                pass
    _best_effort_mirror(mirror_descriptor, payload)
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
