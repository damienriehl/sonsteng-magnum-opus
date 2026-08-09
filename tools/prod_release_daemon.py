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

def main(argv=None):
    if os.environ.get("SONSTENG_PROD_RELEASE_ENABLED") != "true":
        print("[prod-release] disabled (SONSTENG_PROD_RELEASE_ENABLED is not true)")
        return 0
    from prod_release_executor import (CandidateValidator, CompatibilityGate, GitRefAdapter,
        LedgerHTTP, ProductionCandidateBuilder, ProductionExecutor,
        WranglerPagesAdapter, WranglerWorkerAdapter)
    parser = argparse.ArgumentParser()
    env = os.environ.get
    argument = lambda name, key: parser.add_argument(  # noqa: E731
        name, default=env(key), required=not env(key))
    argument("--ledger-url", "SONSTENG_PROD_LEDGER_URL")
    argument("--pages-project", "SONSTENG_PROD_PAGES_PROJECT")
    argument("--pages-artifact", "SONSTENG_PROD_PAGES_ARTIFACT")
    argument("--pages-provenance-url", "SONSTENG_PROD_PAGES_PROVENANCE_URL")
    argument("--worker-config", "SONSTENG_PROD_WORKER_CONFIG")
    argument("--worker-provenance-url", "SONSTENG_PROD_WORKER_PROVENANCE_URL")
    argument("--repo", "SONSTENG_PROD_REPO")
    argument("--manifest", "SONSTENG_PROD_MANIFEST")
    parser.add_argument("--bootstrap-base", default=env("SONSTENG_PROD_BOOTSTRAP_BASE_SHA"))
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
            pages_artifact = candidate_root / pathlib.Path(args.pages_artifact).resolve().relative_to(
                pathlib.Path(args.repo).resolve())
            worker_config = candidate_root / pathlib.Path(args.worker_config).resolve().relative_to(
                pathlib.Path(args.repo).resolve())
            pages = WranglerPagesAdapter(args.pages_project,pages_artifact,args.pages_provenance_url,
                                         candidate_root=candidate_root)
            worker = WranglerWorkerAdapter(worker_config,args.worker_provenance_url,
                                           candidate_root=candidate_root)
            validator = CandidateValidator(isolated_git, manifest)
            ProductionExecutor(ledger,pages,worker,gate,validator).run_once(release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
