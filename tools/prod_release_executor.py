#!/usr/bin/env python3
"""Execute a frozen, human-authorized production release.

This module deliberately contains no approval, risk-scoring, timer, or AI path.
Provider clients and credentials are injected by the operator entry point; the
release manifest and ledger, never ambient HEAD, are the recovery authority.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import urllib.request
from collections.abc import Callable


class ReleaseError(RuntimeError):
    pass


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclasses.dataclass(frozen=True)
class FrozenRelease:
    id: str
    state: str
    base_sha: str
    candidate_sha: str
    manifest_hash: str
    membership_hash: str
    suggestion_ids: tuple[str, ...]
    batch_commits: tuple[str, ...]
    fencing_token: str
    completed_phases: tuple[str, ...] = ()

    def __post_init__(self):
        if self.state not in {"authorized", "executing", "pages_deployed",
                              "worker_deployed", "complete"}:
            raise ValueError("release is not executable")
        if len(set(self.suggestion_ids)) != len(self.suggestion_ids) or not self.suggestion_ids:
            raise ValueError("membership must contain unique suggestion IDs")
        if not self.batch_commits or self.batch_commits[-1] != self.candidate_sha:
            raise ValueError("candidate frontier must equal the last frozen batch commit")
        if not self.fencing_token:
            raise ValueError("fencing token required")

    @classmethod
    def from_ledger(cls, value):
        batches = value.get("batches") or []
        return cls(id=value["id"], state=value["state"], base_sha=value["base_sha"],
                   candidate_sha=value["candidate_sha"], manifest_hash=value["manifest_hash"],
                   membership_hash=value["membership_hash"],
                   suggestion_ids=tuple(value.get("suggestion_ids") or ()),
                   batch_commits=tuple(item["commit_sha"] for item in batches),
                   fencing_token=value["fencing_token"],
                   completed_phases=tuple(event["type"] for event in value.get("events") or ()))

    @property
    def digest(self):
        return hashlib.sha256(_canonical(dataclasses.asdict(self))).hexdigest()


@dataclasses.dataclass(frozen=True)
class CompatibilityGate:
    old_worker_accepts_new_pages: bool
    new_worker_accepts_old_pages: bool

    def deployment_order(self):
        if self.old_worker_accepts_new_pages:
            return ("pages", "worker")
        if self.new_worker_accepts_old_pages:
            return ("worker", "pages")
        raise ReleaseError("no compatible transient deployment order")


class LedgerHTTP:
    """Concrete release-service adapter; authorization is intentionally absent."""

    def __init__(self, base_url, bearer, opener=urllib.request.urlopen):
        self.base_url, self._bearer, self._opener = base_url.rstrip("/"), bearer, opener

    def _request(self, path, body=None):
        data = None if body is None else _canonical(body)
        request = urllib.request.Request(self.base_url + path, data=data,
            method="GET" if body is None else "POST",
            headers={"Authorization": "Bearer " + self._bearer,
                     "Content-Type": "application/json", "X-Edit-Request": "1"})
        with self._opener(request, timeout=30) as response:
            return json.load(response)

    def prepare(self, binding):
        return self._request("/edit/v1/prod/releases/prepare", binding)

    def claim_authorized(self):
        result = self._request("/edit/v1/prod/releases/claim", {})
        return FrozenRelease.from_ledger(result["release"]) if result.get("release") else None

    def transition(self, release_id, state, detail, fencing_token=None):
        return self._request("/edit/v1/prod/releases/transition",
            {"id": release_id, "state": state, "detail": detail,
             "fencing_token": fencing_token})


class GitRefAdapter:
    def __init__(self, repo, run=subprocess.run):
        self.repo, self._run = repo, run

    def is_ancestor(self, base, candidate):
        result = self._run(["git", "merge-base", "--is-ancestor", base, candidate],
                           cwd=self.repo, check=False, capture_output=True)
        return result.returncode == 0

    def tree(self, sha):
        result = self._run(["git", "rev-parse", f"{sha}^{{tree}}"], cwd=self.repo,
                           check=True, capture_output=True, text=True)
        return result.stdout.strip()


class CandidateValidator:
    """Prove that the frozen manifest reproduces the authorized git frontier."""

    def __init__(self, git, manifest):
        self.git, self.manifest = git, dict(manifest)

    def __call__(self, release):
        if self.manifest.get("base_sha") != release.base_sha or \
           self.manifest.get("candidate_sha") != release.candidate_sha or \
           tuple(self.manifest.get("suggestion_ids") or ()) != release.suggestion_ids or \
           tuple(self.manifest.get("batch_commits") or ()) != release.batch_commits:
            raise ReleaseError("manifest binding mismatch")
        manifest_hash = hashlib.sha256(_canonical(self.manifest)).hexdigest()
        if manifest_hash != release.manifest_hash:
            raise ReleaseError("manifest hash mismatch")
        if not self.git.is_ancestor(release.base_sha, release.candidate_sha):
            raise ReleaseError("candidate is not descended from production base")
        if self.git.tree(release.candidate_sha) != self.manifest.get("candidate_tree"):
            raise ReleaseError("candidate tree mismatch")


class WranglerPagesAdapter:
    """Concrete Pages CLI contract. Wrangler reads injected process credentials."""

    name = "pages"

    def __init__(self, project, artifact_dir, provenance_url, run=subprocess.run,
                 opener=urllib.request.urlopen):
        self.project, self.artifact_dir = project, artifact_dir
        self.provenance_url, self._run, self._opener = provenance_url, run, opener

    def deploy(self, manifest):
        result = self._run(["npx", "wrangler", "pages", "deploy", self.artifact_dir,
            "--project-name", self.project, "--commit-hash", manifest.candidate_sha],
            check=True, capture_output=True, text=True)
        # Store an opaque digest, not raw CLI output (which may include URLs or account data).
        return {"provider_id": hashlib.sha256(result.stdout.encode()).hexdigest()[:24]}

    def provenance(self):
        with self._opener(self.provenance_url, timeout=30) as response:
            return response.headers.get("X-Release-SHA", "")


class WranglerWorkerAdapter:
    """Concrete Worker version/activation CLI contract; no authorization path."""

    name = "worker"

    def __init__(self, config, provenance_url, run=subprocess.run,
                 opener=urllib.request.urlopen):
        self.config, self.provenance_url, self._run, self._opener = config, provenance_url, run, opener

    def deploy(self, manifest):
        uploaded = self._run(["npx", "wrangler", "versions", "upload", "--config", self.config,
            "--message", "release:" + manifest.candidate_sha], check=True,
            capture_output=True, text=True)
        version = uploaded.stdout.strip().splitlines()[-1].strip()
        if not version or len(version) > 256:
            raise ReleaseError("Worker upload did not return a bounded version identifier")
        self._run(["npx", "wrangler", "versions", "deploy", version, "--config", self.config,
                   "--yes"], check=True, capture_output=True, text=True)
        return {"provider_id": hashlib.sha256(version.encode()).hexdigest()[:24]}

    def provenance(self):
        with self._opener(self.provenance_url, timeout=30) as response:
            return response.headers.get("X-Release-SHA", "")


class ProductionExecutor:
    def __init__(self, ledger, pages, worker, compatibility=None,
                 candidate_validator: Callable[[FrozenRelease], None] | None = None,
                 restorer=None):
        self.ledger, self.pages, self.worker = ledger, pages, worker
        self.compatibility = compatibility or CompatibilityGate(True, False)
        self.candidate_validator = candidate_validator
        self.restorer = restorer

    def _event(self, release, state, **detail):
        # Evidence is deliberately identifiers/hashes only; edited text and
        # credentials never enter journals or provider receipts.
        safe = {key: value for key, value in detail.items()
                if key in {"manifest_hash", "candidate_sha", "pages_id", "worker_id", "reason"}}
        self.ledger.transition(release.id, state, safe, release.fencing_token)

    def run_once(self):
        release = self.ledger.claim_authorized()
        if release is None or release.state == "complete":
            return None
        if release.state not in {"authorized", "executing", "pages_deployed", "worker_deployed"}:
            return None
        try:
            if self.candidate_validator:
                self.candidate_validator(release)
            if "executing" not in release.completed_phases:
                self._event(release, "executing", manifest_hash=release.manifest_hash,
                            candidate_sha=release.candidate_sha)
            targets = {"pages": self.pages, "worker": self.worker}
            receipts = {}
            for name in self.compatibility.deployment_order():
                if name + "_deployed" in release.completed_phases:
                    continue
                receipts[name] = targets[name].deploy(release)
                self._event(release, name + "_deployed",
                            **{name + "_id": receipts[name].get("provider_id", "")})
            observed = {name: target.provenance() for name, target in targets.items()}
            if any(value != release.candidate_sha for value in observed.values()):
                raise ReleaseError("live provenance does not match recorded candidate")
            self._event(release, "verified", candidate_sha=release.candidate_sha)
            self._event(release, "complete", manifest_hash=release.manifest_hash,
                        candidate_sha=release.candidate_sha)
            return {"id": release.id, "state": "complete", "receipts": receipts}
        except Exception as exc:
            self._event(release, "failed_fenced", reason=type(exc).__name__)
            reason = str(exc) if isinstance(exc, ReleaseError) else "provider operation failed"
            raise ReleaseError(f"{reason}; release is fenced") from exc

    def restore_recorded_base(self, release):
        """Operator recovery toward the recorded known-good pair, never HEAD."""
        if self.restorer is None:
            raise ReleaseError("recorded-pair restore adapter is required; release remains fenced")
        self._event(release, "restoring", candidate_sha=release.base_sha)
        try:
            self.restorer.restore(release.base_sha)
            if any(target.provenance() != release.base_sha for target in (self.pages, self.worker)):
                raise ReleaseError("restored provenance mismatch")
            self._event(release, "restored", candidate_sha=release.base_sha)
            return {"id":release.id,"state":"restored","sha":release.base_sha}
        except Exception as exc:
            self._event(release, "failed_fenced", reason=type(exc).__name__)
            raise ReleaseError("restoration failed; release remains fenced") from exc


class RecordedPairRestorer:
    """Restore only provider IDs frozen for a known-good release SHA.

    Callbacks receive recorded artifact/version identifiers. There is no lookup
    of latest deployment, ambient artifact directory, branch, or HEAD.
    """

    def __init__(self, known_good, restore_pages, restore_worker):
        self.known_good = dict(known_good)
        self.restore_pages, self.restore_worker = restore_pages, restore_worker

    def restore(self, sha):
        pair = self.known_good.get(sha)
        if not pair or not pair.get("pages_deployment_id") or not pair.get("worker_version_id"):
            raise ReleaseError("exact recorded known-good pair is unavailable")
        self.restore_pages(pair["pages_deployment_id"])
        self.restore_worker(pair["worker_version_id"])
