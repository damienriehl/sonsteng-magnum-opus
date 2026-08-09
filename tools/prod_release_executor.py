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
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager


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
                              "worker_deployed", "verified", "failed_fenced", "restoring",
                              "complete"}:
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
                     "Content-Type": "application/json", "X-Edit-Request": "1",
                     "User-Agent": "sonsteng-prod-release/1.0"})
        with self._opener(request, timeout=30) as response:
            return json.load(response)

    def prepare(self, binding):
        return self._request("/edit/v1/prod/releases/prepare", binding)

    def preparation_context(self):
        return self._request("/edit/v1/prod/releases/frontier")["context"]

    def claim_authorized(self):
        result = self._request("/edit/v1/prod/releases/claim", {})
        return FrozenRelease.from_ledger(result["release"]) if result.get("release") else None

    def get_release(self, release_id):
        result = self._request("/edit/v1/prod/releases/status?id=" +
                               urllib.parse.quote(release_id, safe=""))
        return FrozenRelease.from_ledger(result["release"])

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

    def require_clean_candidate(self, sha):
        head = self._run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                         capture_output=True, text=True).stdout.strip()
        status = self._run(["git", "status", "--porcelain", "--untracked-files=all"],
                           cwd=self.repo, check=True, capture_output=True, text=True).stdout
        if head != sha or status.strip():
            raise ReleaseError("candidate checkout is not the clean frozen commit")

    @contextmanager
    def isolated_checkout(self, sha):
        """Materialize an immutable candidate without pinning the DEV checkout."""
        root = pathlib.Path(tempfile.mkdtemp(prefix="sonsteng-prod-candidate-"))
        added = False
        try:
            self._run(["git", "worktree", "add", "--detach", str(root), sha],
                      cwd=self.repo, check=True, capture_output=True, text=True)
            added = True
            yield root
        finally:
            if added:
                self._run(["git", "worktree", "remove", "--force", str(root)],
                          cwd=self.repo, check=True, capture_output=True, text=True)
            shutil.rmtree(root, ignore_errors=True)


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
        self.git.require_clean_candidate(release.candidate_sha)


class ProductionCandidateBuilder:
    """Freeze the latest contiguous DEV frontier for human Publisher review."""

    def __init__(self, ledger, git, manifest_path, bootstrap_base_sha=None):
        self.ledger, self.git = ledger, git
        self.manifest_path = pathlib.Path(manifest_path)
        self.bootstrap_base_sha = bootstrap_base_sha

    @staticmethod
    def _manifest(base_sha, candidate_sha, candidate_tree, generator_id,
                  batch_ids, batch_commits, suggestion_ids):
        return {"schema_version": 1, "target_environment": "production",
                "base_sha": base_sha, "candidate_sha": candidate_sha,
                "candidate_tree": candidate_tree, "generator_id": generator_id,
                "batch_ids": list(batch_ids), "batch_commits": list(batch_commits),
                "suggestion_ids": list(suggestion_ids)}

    def _write(self, manifest):
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _canonical(manifest) + b"\n"
        with tempfile.NamedTemporaryFile(dir=self.manifest_path.parent, delete=False) as target:
            target.write(payload)
            temporary = pathlib.Path(target.name)
        temporary.replace(self.manifest_path)

    def prepare_latest(self):
        context = self.ledger.preparation_context()
        active = context.get("active_release")
        if active:
            batches = active.get("batches") or []
            manifest = self._manifest(active["base_sha"], active["candidate_sha"],
                self.git.tree(active["candidate_sha"]), active["generator_id"],
                [item["batch_id"] for item in batches],
                [item["commit_sha"] for item in batches], active.get("suggestion_ids") or [])
            if hashlib.sha256(_canonical(manifest)).hexdigest() != active["manifest_hash"]:
                raise ReleaseError("active release manifest cannot be reproduced")
            self._write(manifest)
            return {"ok": True, "replay": True, "release": active}

        batches = context.get("batches") or []
        if not batches:
            return None
        if any(not batch.get("suggestion_ids") for batch in batches):
            raise ReleaseError("eligible batch has no applied membership")
        generators = {batch.get("generator_id") for batch in batches}
        if None in generators or len(generators) != 1:
            raise ReleaseError("eligible batches do not share generator evidence")
        base_sha = context.get("base_sha") or self.bootstrap_base_sha
        if not base_sha:
            raise ReleaseError("first release requires a recorded production base SHA")
        candidate_sha = batches[-1]["commit_sha"]
        self.git.require_clean_candidate(candidate_sha)
        if not self.git.is_ancestor(base_sha, candidate_sha):
            raise ReleaseError("candidate is not descended from production base")
        batch_ids = [batch["batch_id"] for batch in batches]
        batch_commits = [batch["commit_sha"] for batch in batches]
        suggestion_ids = sorted(item for batch in batches for item in batch["suggestion_ids"])
        generator_id = generators.pop()
        manifest = self._manifest(base_sha, candidate_sha, self.git.tree(candidate_sha),
            generator_id, batch_ids, batch_commits, suggestion_ids)
        manifest_hash = hashlib.sha256(_canonical(manifest)).hexdigest()
        evidence_hash = hashlib.sha256(_canonical({"batch_ids":batch_ids,
            "batch_commits":batch_commits,"generator_id":generator_id,
            "suggestion_ids":suggestion_ids})).hexdigest()
        release_id = "release-" + manifest_hash[:24]
        binding = {"id":release_id, "idempotency_key":release_id,
            "target_batch_id":batch_ids[-1], "base_sha":base_sha,
            "candidate_sha":candidate_sha, "generator_id":generator_id,
            "evidence_hash":evidence_hash, "manifest_hash":manifest_hash,
            "ancestry_verified":True, "expected_batch_ids":batch_ids,
            "expected_suggestion_ids":suggestion_ids}
        result = self.ledger.prepare(binding)
        if not result or result.get("ok") is not True:
            raise ReleaseError("ledger rejected preparation: " +
                               str((result or {}).get("reason", "unknown")))
        self._write(manifest)
        return result


class WranglerPagesAdapter:
    """Concrete Pages CLI contract. Wrangler reads injected process credentials."""

    name = "pages"

    def __init__(self, project, artifact_dir, provenance_url, candidate_root=None,
                 production_branch="main",
                 run=subprocess.run, opener=urllib.request.urlopen, timeout=240):
        self.project, self.artifact_dir = project, artifact_dir
        self.candidate_root = pathlib.Path(candidate_root or pathlib.Path(artifact_dir).parent).resolve()
        self.production_branch = production_branch
        self.provenance_url, self._run, self._opener, self.timeout = provenance_url, run, opener, timeout

    def deploy(self, manifest):
        artifact = pathlib.Path(self.artifact_dir).resolve()
        if not artifact.is_relative_to(self.candidate_root):
            raise ReleaseError("Pages artifact is outside the frozen candidate checkout")
        # Pages has no runtime environment for static responses. Stage the exact
        # frozen artifact and add a deployment-only response header so the live
        # origin can prove which authorized candidate it is serving.
        with tempfile.TemporaryDirectory(prefix="sonsteng-pages-release-") as staging:
            staged = pathlib.Path(staging) / "site"
            shutil.copytree(artifact, staged)
            headers = staged / "_headers"
            existing = headers.read_text(encoding="utf-8") if headers.exists() else ""
            if existing and not existing.endswith("\n"):
                existing += "\n"
            headers.write_text(existing + "/*\n  X-Release-SHA: " +
                               manifest.candidate_sha + "\n", encoding="utf-8")
            result = self._run(["npx", "wrangler", "pages", "deploy", str(staged),
                "--project-name", self.project, "--branch", self.production_branch,
                "--commit-hash", manifest.candidate_sha],
                cwd=self.candidate_root, check=True, capture_output=True, text=True,
                timeout=self.timeout)
        match = re.search(r"https://([a-z0-9-]{8,})\.[a-z0-9-]+\.pages\.dev(?:/|\s|$)",
                          result.stdout, re.IGNORECASE)
        deployment = match.group(1) if match else ""
        if not deployment:
            raise ReleaseError("Pages deploy did not return a bounded deployment identifier")
        return {"provider_id": hashlib.sha256(deployment.encode()).hexdigest()[:24],
                "deployable_id": deployment}

    def provenance(self):
        with self._opener(self.provenance_url, timeout=30) as response:
            return response.headers.get("X-Release-SHA", "")

    def restore(self, deployment_id):
        self._run(["npx", "wrangler", "pages", "deployment", "rollback", deployment_id,
                   "--project-name", self.project, "--yes"], cwd=self.candidate_root,
                  check=True, capture_output=True, text=True, timeout=self.timeout)


class WranglerWorkerAdapter:
    """Concrete Worker version/activation CLI contract; no authorization path."""

    name = "worker"

    def __init__(self, config, provenance_url, candidate_root=None, run=subprocess.run,
                 opener=urllib.request.urlopen, timeout=240):
        self.config, self.provenance_url = config, provenance_url
        self.candidate_root = pathlib.Path(candidate_root or pathlib.Path(config).parents[2]).resolve()
        self._run, self._opener, self.timeout = run, opener, timeout

    def deploy(self, manifest):
        config = pathlib.Path(self.config).resolve()
        if not config.is_relative_to(self.candidate_root):
            raise ReleaseError("Worker config is outside the frozen candidate checkout")
        uploaded = self._run(["npx", "wrangler", "versions", "upload", "--config", self.config,
            "--env", "production",
            "--message", "release:" + manifest.candidate_sha,
            "--var", "RELEASE_SHA:" + manifest.candidate_sha], check=True,
            cwd=self.candidate_root, capture_output=True, text=True, timeout=self.timeout)
        match = re.search(
            r"(?:^|\n)\s*Worker Version ID:\s*([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\s*(?:\n|$)",
            uploaded.stdout, re.IGNORECASE)
        version = match.group(1) if match else ""
        if not version:
            raise ReleaseError("Worker upload did not return a bounded version identifier")
        self._run(["npx", "wrangler", "versions", "deploy", version, "--config", self.config,
                   "--env", "production", "--yes"], cwd=self.candidate_root, check=True, capture_output=True,
                  text=True, timeout=self.timeout)
        return {"provider_id": hashlib.sha256(version.encode()).hexdigest()[:24],
                "deployable_id": version}

    def provenance(self):
        with self._opener(self.provenance_url, timeout=30) as response:
            return response.headers.get("X-Release-SHA", "")

    def restore(self, version_id):
        self._run(["npx", "wrangler", "versions", "deploy", version_id, "--config", self.config,
                   "--env", "production", "--yes"], cwd=self.candidate_root, check=True,
                  capture_output=True, text=True, timeout=self.timeout)


class ProductionExecutor:
    def __init__(self, ledger, pages, worker, compatibility=None,
                 candidate_validator: Callable[[FrozenRelease], None] | None = None,
                 restorer=None, recovery_registry=None):
        self.ledger, self.pages, self.worker = ledger, pages, worker
        self.compatibility = compatibility or CompatibilityGate(True, False)
        self.candidate_validator = candidate_validator
        self.restorer = restorer
        self.recovery_registry = recovery_registry

    def _event(self, release, state, **detail):
        # Evidence is deliberately identifiers/hashes only; edited text and
        # credentials never enter journals or provider receipts.
        safe = {key: value for key, value in detail.items()
                if key in {"manifest_hash", "candidate_sha", "pages_id", "worker_id", "reason"}}
        result = self.ledger.transition(release.id, state, safe, release.fencing_token)
        if not result or result.get("ok") is not True:
            raise ReleaseError("ledger rejected transition: " + str((result or {}).get("reason", "unknown")))

    def run_once(self, release=None):
        release = release or self.ledger.claim_authorized()
        if release is None or release.state == "complete":
            return None
        if release.state not in {"authorized", "executing", "pages_deployed", "worker_deployed", "verified"}:
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
                recorded = self.recovery_registry.target(release.candidate_sha, name) \
                    if self.recovery_registry else None
                if recorded:
                    if targets[name].provenance() != release.candidate_sha:
                        raise ReleaseError("recorded target does not match live provenance")
                    self._event(release, name + "_deployed",
                        **{name + "_id": hashlib.sha256(recorded.encode()).hexdigest()[:24]})
                    continue
                receipts[name] = targets[name].deploy(release)
                if self.recovery_registry:
                    self.recovery_registry.record_target(
                        release.candidate_sha, name, receipts[name].get("deployable_id", ""))
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
            try:
                self._event(release, "failed_fenced", reason=type(exc).__name__)
            except ReleaseError:
                pass
            reason = str(exc) if isinstance(exc, ReleaseError) else "provider operation failed"
            raise ReleaseError(f"{reason}; release is fenced") from exc

    def restore_recorded_base(self, release):
        """Operator recovery toward the recorded known-good pair, never HEAD."""
        if self.restorer is None:
            raise ReleaseError("recorded-pair restore adapter is required; release remains fenced")
        self._event(release, "restoring", candidate_sha=release.base_sha)
        try:
            self.restorer.restore(release.base_sha,
                                  tuple(reversed(self.compatibility.deployment_order())))
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

    def restore(self, sha, order=("pages", "worker")):
        pair = self.known_good.get(sha)
        if not pair or not pair.get("pages_deployment_id") or not pair.get("worker_version_id"):
            raise ReleaseError("exact recorded known-good pair is unavailable")
        callbacks = {"pages": lambda: self.restore_pages(pair["pages_deployment_id"]),
                     "worker": lambda: self.restore_worker(pair["worker_version_id"])}
        for target in order:
            callbacks[target]()


class RecoveryRegistry:
    """0600 atomic registry of opaque deployable IDs keyed by candidate SHA."""

    def __init__(self, path):
        self.path = pathlib.Path(path)

    def _read(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record_target(self, sha, target, deployable_id):
        if target not in {"pages", "worker"} or not deployable_id:
            raise ReleaseError("recovery registry requires an exact deployable identifier")
        data = self._read()
        pair = data.setdefault(sha, {})
        key = "pages_deployment_id" if target == "pages" else "worker_version_id"
        prior = pair.get(key)
        if prior and prior != deployable_id:
            raise ReleaseError("recovery registry identifier conflict")
        pair[key] = deployable_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False,
                                         encoding="utf-8") as target_file:
            json.dump(data, target_file, sort_keys=True, separators=(",", ":"))
            target_file.write("\n")
            temporary = pathlib.Path(target_file.name)
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def target(self, sha, target):
        key = "pages_deployment_id" if target == "pages" else "worker_version_id"
        return self._read().get(sha, {}).get(key)

    def pairs(self):
        return self._read()
