#!/usr/bin/env python3
"""Single-shot PROD release executor entry point (separate from DEV apply).

The installed service is deliberately safe to schedule while config-off.  It
does no argument validation, credential lookup, ledger call, git operation, or
provider operation until SONSTENG_PROD_RELEASE_ENABLED is exactly ``true``.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib


def _path_in_checkout(checkout_root, configured_repo, configured_path):
    repo = pathlib.Path(configured_repo).resolve()
    configured = pathlib.Path(configured_path).resolve()
    try:
        relative = configured.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError("configured release path is outside the trusted repository") from exc
    resolved = pathlib.Path(checkout_root).resolve() / relative
    if not resolved.exists() or not resolved.resolve().is_relative_to(pathlib.Path(checkout_root).resolve()):
        raise RuntimeError("recorded release path is missing or untrusted")
    return resolved


def _restore_recorded_release(args, ledger, gate, git, *, registry_factory,
                              pages_factory, worker_factory, restorer_factory,
                              executor_factory, git_factory):
    release = ledger.claim_restore(args.restore_release_id)
    if not release or not release.base_sha:
        raise RuntimeError("restore release lacks a recorded base SHA")
    registry = registry_factory(args.recovery_registry)
    with git.isolated_checkout(release.base_sha) as base_root:
        git_factory(base_root).require_clean_candidate(release.base_sha)
        pages_artifact = _path_in_checkout(base_root,args.repo,args.pages_artifact)
        worker_config = _path_in_checkout(base_root,args.repo,args.worker_config)
        pages = pages_factory(args.pages_project,pages_artifact,
            args.pages_provenance_url,candidate_root=base_root,
            production_branch=args.pages_branch)
        worker = worker_factory(worker_config,args.worker_provenance_url,
                                candidate_root=base_root)
        restorer = restorer_factory(registry.pairs(),pages.restore,worker.restore)
        executor_factory(ledger,pages,worker,gate,restorer=restorer,
                         recovery_registry=registry).restore_recorded_base(release)

def main(argv=None):
    if os.environ.get("SONSTENG_PROD_RELEASE_ENABLED") != "true":
        print("[prod-release] disabled (SONSTENG_PROD_RELEASE_ENABLED is not true)")
        return 0
    from prod_release_executor import (CandidateValidator, CompatibilityGate, GitRefAdapter,
        LedgerHTTP, ProductionCandidateBuilder, ProductionExecutor,
        RecordedPairRestorer, RecoveryRegistry, WranglerPagesAdapter, WranglerWorkerAdapter)
    parser = argparse.ArgumentParser()
    env = os.environ.get
    argument = lambda name, key: parser.add_argument(  # noqa: E731
        name, default=env(key), required=not env(key))
    argument("--ledger-url", "SONSTENG_PROD_LEDGER_URL")
    argument("--pages-project", "SONSTENG_PROD_PAGES_PROJECT")
    argument("--pages-artifact", "SONSTENG_PROD_PAGES_ARTIFACT")
    argument("--pages-provenance-url", "SONSTENG_PROD_PAGES_PROVENANCE_URL")
    parser.add_argument("--pages-branch", default=env("SONSTENG_PROD_PAGES_BRANCH", "main"))
    argument("--worker-config", "SONSTENG_PROD_WORKER_CONFIG")
    argument("--worker-provenance-url", "SONSTENG_PROD_WORKER_PROVENANCE_URL")
    argument("--repo", "SONSTENG_PROD_REPO")
    argument("--manifest", "SONSTENG_PROD_MANIFEST")
    argument("--recovery-registry", "SONSTENG_PROD_RECOVERY_REGISTRY")
    parser.add_argument("--bootstrap-base", default=env("SONSTENG_PROD_BOOTSTRAP_BASE_SHA"))
    parser.add_argument("--restore-release-id", default=None)
    parser.add_argument("--lock", default=env("SONSTENG_PROD_LOCK", "/tmp/sonsteng-prod-release.lock"))
    parser.add_argument("--new-worker-accepts-old-pages", action="store_true",
        default=env("SONSTENG_NEW_WORKER_ACCEPTS_OLD_PAGES") == "true")
    parser.add_argument("--old-worker-accepts-new-pages", action="store_true",
        default=env("SONSTENG_OLD_WORKER_ACCEPTS_NEW_PAGES") == "true")
    args = parser.parse_args(argv)
    token = os.environ.get("SONSTENG_PROD_RELEASE_BEARER")
    if not token:
        parser.error("SONSTENG_PROD_RELEASE_BEARER must be injected")
    pathlib.Path(args.lock).parent.mkdir(parents=True, exist_ok=True)
    with open(args.lock, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = LedgerHTTP(args.ledger_url, token)
        gate = CompatibilityGate(args.old_worker_accepts_new_pages,
                                 args.new_worker_accepts_old_pages)
        git = GitRefAdapter(args.repo)
        if args.restore_release_id:
            _restore_recorded_release(args,ledger,gate,git,
                registry_factory=RecoveryRegistry,pages_factory=WranglerPagesAdapter,
                worker_factory=WranglerWorkerAdapter,restorer_factory=RecordedPairRestorer,
                executor_factory=ProductionExecutor,git_factory=GitRefAdapter)
            return 0
        ProductionCandidateBuilder(ledger, git, args.manifest,
                                   args.bootstrap_base).prepare_latest()
        if not pathlib.Path(args.manifest).is_file():
            return 0
        with open(args.manifest, encoding="utf-8") as source:
            manifest = json.load(source)
        release = ledger.claim_authorized()
        if release is None:
            return 0
        with git.isolated_checkout(release.candidate_sha) as candidate_root:
            isolated_git = GitRefAdapter(candidate_root)
            pages_artifact = _path_in_checkout(candidate_root,args.repo,args.pages_artifact)
            worker_config = _path_in_checkout(candidate_root,args.repo,args.worker_config)
            pages = WranglerPagesAdapter(args.pages_project,pages_artifact,args.pages_provenance_url,
                                         candidate_root=candidate_root,
                                         production_branch=args.pages_branch)
            worker = WranglerWorkerAdapter(worker_config,args.worker_provenance_url,
                                           candidate_root=candidate_root)
            validator = CandidateValidator(isolated_git, manifest)
            registry = RecoveryRegistry(args.recovery_registry)
            ProductionExecutor(ledger,pages,worker,gate,validator,
                               recovery_registry=registry).run_once(release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
