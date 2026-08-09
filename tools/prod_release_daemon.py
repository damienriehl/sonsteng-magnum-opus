#!/usr/bin/env python3
"""Single-shot PROD release executor entry point (separate from DEV apply)."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib

from prod_release_executor import (CandidateValidator, CompatibilityGate, GitRefAdapter,
    LedgerHTTP, ProductionExecutor, WranglerPagesAdapter, WranglerWorkerAdapter)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger-url", required=True)
    parser.add_argument("--pages-project", required=True)
    parser.add_argument("--pages-artifact", required=True)
    parser.add_argument("--pages-provenance-url", required=True)
    parser.add_argument("--worker-config", required=True)
    parser.add_argument("--worker-provenance-url", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--lock", default="/tmp/sonsteng-prod-release.lock")
    parser.add_argument("--new-worker-accepts-old-pages", action="store_true")
    parser.add_argument("--old-worker-accepts-new-pages", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("SONSTENG_PROD_RELEASE_BEARER")
    if not token:
        parser.error("SONSTENG_PROD_RELEASE_BEARER must be injected")
    pathlib.Path(args.lock).parent.mkdir(parents=True, exist_ok=True)
    with open(args.lock, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = LedgerHTTP(args.ledger_url, token)
        pages = WranglerPagesAdapter(args.pages_project,args.pages_artifact,args.pages_provenance_url)
        worker = WranglerWorkerAdapter(args.worker_config,args.worker_provenance_url)
        gate = CompatibilityGate(args.old_worker_accepts_new_pages,
                                 args.new_worker_accepts_old_pages)
        with open(args.manifest, encoding="utf-8") as source:
            manifest = json.load(source)
        validator = CandidateValidator(GitRefAdapter(args.repo), manifest)
        ProductionExecutor(ledger,pages,worker,gate,validator).run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
