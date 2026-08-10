#!/usr/bin/env python3
"""Privileged local bootstrap for an existing, exact production pair.

This command has no release-ledger client and no candidate builder.  It can
only verify, reactivate, restore, and atomically record artifacts that already
exist. Operator identity comes from the protected process environment, never
from browser or routine release-service credentials.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re

from prod_release_executor import (
    GitRefAdapter,
    RecoveryRegistry,
    ReleaseError,
    WranglerPagesAdapter,
    WranglerWorkerAdapter,
)


@dataclasses.dataclass(frozen=True)
class BootstrapRequest:
    repo: pathlib.Path
    source_sha: str
    candidate_sha: str
    pages_deployment_id: str
    worker_version_id: str
    expected_pages_provenance: str
    expected_worker_provenance: str
    recovery_registry: pathlib.Path
    receipt_log: pathlib.Path
    pages_artifact: pathlib.Path
    worker_config: pathlib.Path
    pages_project: str = "sonsteng"
    pages_provenance_url: str = ""
    worker_provenance_url: str = ""
    pages_branch: str = "main"


def _operator_authority():
    if os.environ.get("SONSTENG_PROD_BOOTSTRAP_AUTHORITY") != "local-operator":
        raise ReleaseError("legacy bootstrap requires local operator authority")
    if os.environ.get("SONSTENG_PROD_RELEASE_ENABLED") == "true":
        raise ReleaseError("legacy bootstrap requires normal publication config-off")
    if os.environ.get("SONSTENG_PROD_RELEASE_BEARER"):
        raise ReleaseError("routine release-service authority cannot bootstrap recovery")
    operator_id = os.environ.get("SONSTENG_PROD_BOOTSTRAP_OPERATOR_ID", "")
    channel = os.environ.get("SONSTENG_PROD_BOOTSTRAP_AUTHORITY_CHANNEL", "")
    safe_identity = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
    if not safe_identity.fullmatch(operator_id) or not safe_identity.fullmatch(channel):
        raise ReleaseError("legacy bootstrap requires operator identity and authority channel")
    return operator_id, channel


def _validate_request(request):
    sha_pattern = r"[0-9a-f]{40}"
    if request.source_sha != request.candidate_sha or not re.fullmatch(
            sha_pattern, request.source_sha or ""):
        raise ReleaseError("candidate/source SHA must be one exact commit")
    if request.expected_pages_provenance != request.source_sha or \
       request.expected_worker_provenance != request.source_sha:
        raise ReleaseError("provenance evidence must bind the exact source SHA")
    safe_provider_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    if not safe_provider_id.fullmatch(request.pages_deployment_id or "") or \
       not safe_provider_id.fullmatch(request.worker_version_id or ""):
        raise ReleaseError("both exact provider identifiers are required")
    repo = request.repo.resolve()
    if not (repo / ".git").exists():
        raise ReleaseError("bootstrap repository is not a git checkout")
    for configured in (request.pages_artifact, request.worker_config):
        path = pathlib.Path(configured)
        if path.is_symlink():
            raise ReleaseError("bootstrap release paths may not be symlinks")
        try:
            path.resolve().relative_to(repo)
        except ValueError as exc:
            raise ReleaseError("bootstrap release path is outside the trusted repository") from exc
        if not path.exists():
            raise ReleaseError("bootstrap release path is missing")
    for state_path in (request.recovery_registry, request.receipt_log):
        if pathlib.Path(state_path).is_symlink():
            raise ReleaseError("bootstrap state paths may not be symlinks")


def _checkout_path(root, repo, configured):
    relative = pathlib.Path(configured).resolve().relative_to(pathlib.Path(repo).resolve())
    target = pathlib.Path(root) / relative
    if target.is_symlink() or not target.exists() or \
       not target.resolve().is_relative_to(pathlib.Path(root).resolve()):
        raise ReleaseError("isolated bootstrap path is missing or untrusted")
    return target


def _default_targets(root, request):
    pages = _checkout_path(root, request.repo, request.pages_artifact)
    worker = _checkout_path(root, request.repo, request.worker_config)
    return (
        WranglerPagesAdapter(
            request.pages_project, pages, request.pages_provenance_url,
            candidate_root=root, production_branch=request.pages_branch),
        WranglerWorkerAdapter(
            worker, request.worker_provenance_url, candidate_root=root),
    )


def _safe_provider_call(action):
    try:
        return action()
    except Exception as exc:
        # Provider stdout/stderr can contain environment echoes or secrets.
        # Preserve no provider-controlled exception content.
        raise ReleaseError("legacy bootstrap provider operation failed") from None


def _require_provenance(pages, worker, sha):
    observed = {
        "pages": _safe_provider_call(pages.provenance),
        "worker": _safe_provider_call(worker.provenance),
    }
    if observed != {"pages": sha, "worker": sha}:
        raise ReleaseError("legacy pair live provenance mismatch")


def _append_receipt(path, receipt):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run_bootstrap_locked(request, *, target_factory, git_factory, now):
    operator_id, authority_channel = _operator_authority()
    _validate_request(request)
    registry = RecoveryRegistry(request.recovery_registry)
    existing = registry.pair(request.source_sha)
    requested_pair = {
        "pages_deployment_id": request.pages_deployment_id,
        "worker_version_id": request.worker_version_id,
    }
    if existing and existing != requested_pair:
        raise ReleaseError("complete pair conflict")
    if request.recovery_registry.exists() and request.source_sha in registry._read() and not existing:
        raise ReleaseError("partial recovery pair conflicts with audited bootstrap")

    git = git_factory(request.repo)
    with git.isolated_checkout(request.source_sha) as root:
        git_factory(root).require_clean_candidate(request.source_sha)
        # Resolve expected paths inside the exact checkout even for injected
        # provider adapters, so stale/outside path configuration always fails.
        _checkout_path(root, request.repo, request.pages_artifact)
        _checkout_path(root, request.repo, request.worker_config)
        pages, worker = target_factory(root, request)
        _require_provenance(pages, worker, request.source_sha)
        for target, provider_id in (
                (pages, request.pages_deployment_id),
                (worker, request.worker_version_id)):
            _safe_provider_call(lambda target=target, provider_id=provider_id:
                                target.restore(provider_id))
        _require_provenance(pages, worker, request.source_sha)
        # A second pass in reverse compatibility order is the restoration
        # drill; it must remain exact and must not upload a new artifact.
        for target, provider_id in (
                (worker, request.worker_version_id),
                (pages, request.pages_deployment_id)):
            _safe_provider_call(lambda target=target, provider_id=provider_id:
                                target.restore(provider_id))
        _require_provenance(pages, worker, request.source_sha)

    replay = existing == requested_pair
    if not replay:
        timestamp = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
        _append_receipt(request.receipt_log, {
            "schema_version": 1,
            "event": "legacy_pair_bootstrap_verified",
            "operator_id": operator_id,
            "authority_channel": authority_channel,
            "recorded_at": timestamp,
            "source_sha": request.source_sha,
            "candidate_sha": request.candidate_sha,
            "pages_id_digest": hashlib.sha256(
                request.pages_deployment_id.encode()).hexdigest()[:24],
            "worker_id_digest": hashlib.sha256(
                request.worker_version_id.encode()).hexdigest()[:24],
            "provenance_digest": hashlib.sha256(
                (request.source_sha + ":pages+worker").encode()).hexdigest(),
            "reactivation_verified": True,
            "restoration_verified": True,
        })
        registry.record_pair(
            request.source_sha, request.pages_deployment_id, request.worker_version_id)
    return {"ok": True, "replay": replay, "source_sha": request.source_sha}


def run_bootstrap(request, *, target_factory=_default_targets, git_factory=GitRefAdapter,
                  now=None):
    """Serialize the verification drill and complete-pair compare-and-set."""
    _operator_authority()
    _validate_request(request)
    lock_path = pathlib.Path(str(request.recovery_registry) + ".bootstrap.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise ReleaseError("bootstrap lock path may not be a symlink")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_bootstrap_locked(
            request, target_factory=target_factory, git_factory=git_factory, now=now)


def _parser():
    parser = argparse.ArgumentParser(
        description="Verify and atomically record an existing production pair")
    env = os.environ.get
    required = lambda option, key: parser.add_argument(  # noqa: E731
        option, default=env(key), required=not env(key))
    required("--repo", "SONSTENG_PROD_REPO")
    required("--source-sha", "SONSTENG_PROD_BOOTSTRAP_SOURCE_SHA")
    required("--candidate-sha", "SONSTENG_PROD_BOOTSTRAP_CANDIDATE_SHA")
    required("--pages-deployment-id", "SONSTENG_PROD_BOOTSTRAP_PAGES_DEPLOYMENT_ID")
    required("--worker-version-id", "SONSTENG_PROD_BOOTSTRAP_WORKER_VERSION_ID")
    required("--pages-provenance", "SONSTENG_PROD_BOOTSTRAP_PAGES_PROVENANCE")
    required("--worker-provenance", "SONSTENG_PROD_BOOTSTRAP_WORKER_PROVENANCE")
    required("--recovery-registry", "SONSTENG_PROD_RECOVERY_REGISTRY")
    required("--receipt-log", "SONSTENG_PROD_BOOTSTRAP_RECEIPT_LOG")
    required("--pages-artifact", "SONSTENG_PROD_PAGES_ARTIFACT")
    required("--worker-config", "SONSTENG_PROD_WORKER_CONFIG")
    required("--pages-project", "SONSTENG_PROD_PAGES_PROJECT")
    required("--pages-provenance-url", "SONSTENG_PROD_PAGES_PROVENANCE_URL")
    required("--worker-provenance-url", "SONSTENG_PROD_WORKER_PROVENANCE_URL")
    parser.add_argument("--pages-branch", default=env("SONSTENG_PROD_PAGES_BRANCH", "main"))
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    request = BootstrapRequest(
        repo=pathlib.Path(args.repo), source_sha=args.source_sha,
        candidate_sha=args.candidate_sha,
        pages_deployment_id=args.pages_deployment_id,
        worker_version_id=args.worker_version_id,
        expected_pages_provenance=args.pages_provenance,
        expected_worker_provenance=args.worker_provenance,
        recovery_registry=pathlib.Path(args.recovery_registry),
        receipt_log=pathlib.Path(args.receipt_log),
        pages_artifact=pathlib.Path(args.pages_artifact),
        worker_config=pathlib.Path(args.worker_config),
        pages_project=args.pages_project,
        pages_provenance_url=args.pages_provenance_url,
        worker_provenance_url=args.worker_provenance_url,
        pages_branch=args.pages_branch,
    )
    result = run_bootstrap(request)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
