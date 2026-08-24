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
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager


class ReleaseError(RuntimeError):
    pass


class ObserverError(RuntimeError):
    """Bounded failure from the read-only production-ledger observer."""


BOUNDED_PROVIDER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


MAX_PRODUCTION_LEASE_MS = 15 * 60 * 1000
SERVICE_USER_AGENT = "sonsteng-prod-release/1.0"
WRANGLER_COMMAND = ("npx", "wrangler@4")
STRUCTURAL_OPERATIONS = frozenset({"insert_after", "delete", "split", "merge", "move"})


def _wrangler(*args):
    return [*WRANGLER_COMMAND,*args]


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclasses.dataclass(frozen=True)
class ProjectionPatch:
    """One synthetic whole-value patch for an existing durable source."""

    source_ref: str
    original_text: str
    new_text: str
    operation_ids: tuple[str, ...]
    review_revision_id: str


@dataclasses.dataclass(frozen=True)
class ProjectionExclusion:
    operation_id: str
    source_ref: str
    reason: str


@dataclasses.dataclass(frozen=True)
class MaterializedProjection:
    patches: tuple[ProjectionPatch, ...]
    structural_operations: tuple[dict, ...]
    exclusions: tuple[ProjectionExclusion, ...]
    accepted_operation_ids: tuple[str, ...]


class AcceptedOnlyMaterializer:
    """Pure accepted-operation projection; performs no filesystem writes.

    Submitted review evidence is already immutable and server-derived.  This
    class deliberately resolves every prose operation before a caller may open
    or modify an isolated checkout, so ambiguity cannot leave a partial tree.
    """

    @staticmethod
    def _operation_id(operation):
        return operation.get("decision_id") or operation.get("id")

    @staticmethod
    def _source_evidence(operation):
        evidence = operation.get("source_patch") or {}
        return {
            "old_text": evidence.get("old_text", operation.get("old_text", "")),
            "new_text": evidence.get("new_text", operation.get("new_text", "")),
            "context_before": evidence.get("context_before", operation.get("context_before", [])),
            "context_after": evidence.get("context_after", operation.get("context_after", [])),
        }

    @staticmethod
    def _unique_span(value, evidence, operation_id):
        old = evidence["old_text"]
        before = "".join(evidence["context_before"] or [])
        after = "".join(evidence["context_after"] or [])
        if old:
            starts = [match.start() for match in re.finditer(re.escape(old), value)]
            starts = [start for start in starts
                      if (not before or value[:start].endswith(before)) and
                      (not after or value[start + len(old):].startswith(after))]
            if len(starts) != 1:
                raise ReleaseError(f"operation {operation_id} lacks a unique anchor")
            return starts[0], starts[0] + len(old)
        # Insertions anchor the boundary between unchanged before/after text.
        starts = []
        for position in range(len(value) + 1):
            if (not before or value[:position].endswith(before)) and \
               (not after or value[position:].startswith(after)):
                starts.append(position)
        if len(starts) != 1:
            raise ReleaseError(f"operation {operation_id} lacks a unique anchor")
        return starts[0], starts[0]

    def _held_group_ids(self, sources):
        group_states = {}
        for source in sources:
            decisions = {item.get("operation_id"): item.get("decision")
                         for item in source.get("decisions") or []}
            for operation in source.get("operations") or []:
                group_id = operation.get("group_id") or operation.get("move_pair_id")
                if group_id:
                    group_states.setdefault(group_id, []).append({
                        "stale": bool(source.get("stale")),
                        "decision": decisions.get(self._operation_id(operation)),
                    })
        return {group_id for group_id,states in group_states.items()
                if any(state["decision"] == "accepted" for state in states) and
                (any(state["stale"] for state in states) or
                 any(state["decision"] != "accepted" for state in states))}

    @staticmethod
    def _production_holds(sources):
        structural_groups, structural_refs = set(), set()
        for source in sources:
            for operation in source.get("operations") or []:
                if operation.get("op") not in STRUCTURAL_OPERATIONS:
                    continue
                if operation.get("group_id"):
                    structural_groups.add(operation["group_id"])
                structural_refs.update(ref for ref in (
                    source.get("source_ref"), operation.get("source_ref"),
                    operation.get("op_arg")) if isinstance(ref, str) and ref)
        holds = {}
        for source in sources:
            for operation in source.get("operations") or []:
                if operation.get("op") in STRUCTURAL_OPERATIONS:
                    holds[operation.get("id")] = "structural_prod_deferred"
                elif ((operation.get("group_id") and
                       operation.get("group_id") in structural_groups) or
                      (operation.get("source_ref") or source.get("source_ref")) in structural_refs):
                    holds[operation.get("id")] = "depends_on_structural_prod_deferred"
        return holds

    def materialize(self, sources):
        patches, structural, exclusions, accepted_ids = [], [], [], []
        seen_operation_ids = set()
        ordered_sources = sorted(sources, key=lambda item: (item.get("source_ref", ""),
                                                             item.get("review_revision_id", "")))
        held_groups = self._held_group_ids(ordered_sources)
        production_holds = self._production_holds(ordered_sources)

        for source in ordered_sources:
            source_ref = source.get("source_ref")
            original = source.get("source_original_text", source.get("original_text"))
            if not source_ref or not isinstance(original, str):
                raise ReleaseError("projection source lacks durable source evidence")
            decisions = {item.get("operation_id"): item.get("decision")
                         for item in source.get("decisions") or []}
            operations = list(source.get("operations") or [])
            groups = {}
            for operation in operations:
                operation_id = self._operation_id(operation)
                payload_id = operation.get("id")
                if not payload_id or not operation_id or operation.get("source_ref") != source_ref:
                    raise ReleaseError("operation does not bind its durable source")
                if payload_id in seen_operation_ids:
                    raise ReleaseError("duplicate operation identity")
                seen_operation_ids.add(payload_id)
                group_id = operation.get("group_id") or operation.get("move_pair_id")
                if group_id:
                    groups.setdefault(group_id, set()).add(operation_id)

            for group_id, members in groups.items():
                group_decisions = {decisions.get(member) for member in members}
                if not any(production_holds.get(operation.get("id"))
                           for operation in operations
                           if (operation.get("group_id") or operation.get("move_pair_id")) == group_id) and \
                   not source.get("stale") and "accepted" in group_decisions and \
                   group_decisions != {"accepted"}:
                    raise ReleaseError(f"partial structural group {group_id}")

            projected = original
            accepted_ranges = []
            source_accepted = []
            resolved_edits = []
            for operation in operations:
                operation_id = self._operation_id(operation)
                decision = decisions.get(operation_id)
                group_id = operation.get("group_id") or operation.get("move_pair_id")
                reason = production_holds.get(operation.get("id")) or ("stale" if source.get("stale") else (
                    "group_held" if group_id in held_groups else
                    decision if decision in {"rejected", "questioned"} else
                    "unanswered" if decision is None else None))
                if reason:
                    exclusions.append(ProjectionExclusion(operation["id"], source_ref, reason))
                    continue
                if decision != "accepted":
                    raise ReleaseError(f"unknown submitted decision for {operation_id}")
                base_range = operation.get("base_range")
                if not (isinstance(base_range, list) and len(base_range) == 2 and
                        all(isinstance(value, int) for value in base_range)):
                    raise ReleaseError(f"operation {operation_id} lacks a base range")
                start, end = base_range
                if end < start:
                    raise ReleaseError(f"operation {operation_id} has an invalid base range")
                if any(max(start, prior_start) < min(end, prior_end)
                       for prior_start, prior_end in accepted_ranges
                       if end > start and prior_end > prior_start):
                    raise ReleaseError(f"overlapping accepted operations at {operation_id}")
                evidence = self._source_evidence(operation)
                anchor_start, anchor_end = self._unique_span(original, evidence, operation_id)
                accepted_ranges.append((start, end))
                resolved_edits.append((anchor_start,anchor_end,evidence["new_text"],operation_id))
                source_accepted.append(operation["id"])
                accepted_ids.append(operation["id"])
            for anchor_start,anchor_end,new_text,operation_id in sorted(
                    resolved_edits,key=lambda item:(item[0],item[1],item[3]),reverse=True):
                projected = projected[:anchor_start] + new_text + projected[anchor_end:]
            if source_accepted:
                patches.append(ProjectionPatch(source_ref, original, projected,
                    tuple(source_accepted), source.get("review_revision_id", "")))
        return MaterializedProjection(tuple(patches), tuple(structural), tuple(exclusions),
                                      tuple(accepted_ids))


class ProjectionTreeWriter:
    """Apply a materialized projection atomically, then rebuild all consumers."""

    def __init__(self, pipeline=None):
        if pipeline is None:
            from apply_suggestions import SubprocessPipeline
            pipeline = SubprocessPipeline()
        self.pipeline = pipeline

    def verify_rebased_sources(self, root, sources):
        """Prove old-base reviews still name the identical current PROD leaf."""
        source_index = self.pipeline.regenerate_map(str(root))
        for source in sources:
            block = source_index.get(source.get("source_ref"))
            expected = source.get("source_original_text", source.get("original_text"))
            if not block or not isinstance(expected, str) or block.get("original_text") != expected:
                raise ReleaseError("reviewed source changed after its production base")

    @staticmethod
    def _patch_for(block, operation_id, source_ref, old_text, new_text, operation=None):
        from apply_suggestions import Patch, classify
        operation = operation or {}
        relpath = source_ref.split("#", 1)[0]
        op_name = operation.get("op")
        kind, json_path = classify(source_ref, block, op_name)
        return Patch(suggestion_id=operation_id,
            group_id=operation.get("group_id") or operation.get("move_pair_id"),
            source_ref=source_ref,relpath=relpath,kind=kind,json_path=json_path,
            original_text=old_text,new_text=new_text,op=op_name,
            op_arg=operation.get("op_arg"),created_at=operation.get("created_at",0))

    def write(self, root, projection):
        from apply_suggestions import apply_file_patches
        root = pathlib.Path(root)
        source_index = self.pipeline.regenerate_map(str(root))
        patches = []
        for item in projection.patches:
            block = source_index.get(item.source_ref)
            if not block:
                raise ReleaseError(f"durable source is absent from production base: {item.source_ref}")
            patches.append(self._patch_for(block,"projection:" + "+".join(item.operation_ids),
                item.source_ref,item.original_text,item.new_text))
        for operation in projection.structural_operations:
            source_ref = operation["source_ref"]
            block = source_index.get(source_ref)
            if not block:
                raise ReleaseError(f"structural source is absent from production base: {source_ref}")
            evidence = AcceptedOnlyMaterializer._source_evidence(operation)
            patches.append(self._patch_for(block,operation.get("id"),source_ref,
                evidence["old_text"],evidence["new_text"],operation))

        by_file = {}
        for patch in patches:
            by_file.setdefault(patch.relpath, []).append(patch)
        # Resolve every file against a private data copy.  Only after all files
        # succeed are their bytes installed in the disposable candidate tree.
        with tempfile.TemporaryDirectory(prefix="sonsteng-projection-stage-") as stage:
            stage_root = pathlib.Path(stage)
            shutil.copytree(root / "data", stage_root / "data")
            for relpath, file_patches in sorted(by_file.items()):
                results = apply_file_patches(str(stage_root),relpath,file_patches)
                if not results or not all(value is True for value in results.values()):
                    failed = sorted(key for key,value in results.items()
                                    if value is not True)
                    raise ReleaseError("projection patch is ambiguous or invalid: " + ",".join(failed))
            for relpath in sorted(by_file):
                shutil.copy2(stage_root / relpath, root / relpath)

        # The real pipeline owns the complete generator dependency closure.
        self.pipeline.regenerate_map(str(root))
        valid, detail = self.pipeline.validate(str(root))
        if not valid:
            raise ReleaseError("projected candidate validation failed: " + str(detail.get("step", "validate")))
        built, detail = self.pipeline.build(str(root))
        if not built:
            raise ReleaseError("projected candidate build failed: " + str(detail.get("step", "build")))
        parity, detail = self.pipeline.parity(str(root))
        if not parity:
            raise ReleaseError("projected candidate parity failed: " + str(detail.get("step", "parity")))
        return {"generator_id":self.pipeline.generator_identity(str(root)),"parity_verified":True}


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
    schema_version: int = 1
    operation_ids: tuple[str, ...] = ()
    review_receipt_hash: str = ""
    projection_identity: str = ""

    def __post_init__(self):
        if self.state not in {"authorized", "executing", "pages_deployed",
                              "worker_deployed", "verified", "failed_fenced", "restoring",
                              "complete"}:
            raise ValueError("release is not executable")
        membership = self.operation_ids if self.schema_version >= 2 else self.suggestion_ids
        if len(set(membership)) != len(membership) or not membership:
            raise ValueError("membership must contain unique IDs")
        if self.schema_version >= 2:
            if not self.review_receipt_hash or not self.projection_identity:
                raise ValueError("operation release lacks review/projection identity")
        elif not self.batch_commits or self.batch_commits[-1] != self.candidate_sha:
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
                   completed_phases=tuple(event["type"] for event in value.get("events") or ()),
                   schema_version=int(value.get("schema_version") or 1),
                   operation_ids=tuple(value.get("operation_ids") or ()),
                   review_receipt_hash=value.get("review_receipt_hash") or "",
                   projection_identity=value.get("projection_identity") or "")

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


def _require_https_url(value, purpose):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ReleaseError(f"{purpose} requires HTTPS")


class LedgerHTTP:
    """Concrete release-service adapter; authorization is intentionally absent."""

    def __init__(self, base_url, bearer, opener=urllib.request.urlopen):
        _require_https_url(base_url, "release ledger")
        self.base_url, self._bearer, self._opener = base_url.rstrip("/"), bearer, opener

    def _request(self, path, body=None):
        data = None if body is None else _canonical(body)
        request = urllib.request.Request(self.base_url + path, data=data,
            method="GET" if body is None else "POST",
            headers={"Authorization": "Bearer " + self._bearer,
                     "Content-Type": "application/json", "X-Edit-Request": "1",
                     "User-Agent": SERVICE_USER_AGENT})
        with self._opener(request, timeout=30) as response:
            return json.load(response)

    def prepare(self, binding):
        return self._request("/edit/v1/prod/releases/prepare", binding)

    def preparation_context(self):
        return self._request("/edit/v1/prod/releases/frontier")["context"]

    def claim_authorized(self, release_id=None):
        result = self._request("/edit/v1/prod/releases/claim",
                               {"id": release_id} if release_id else {})
        return FrozenRelease.from_ledger(result["release"]) if result.get("release") else None

    def claim_restore(self, release_id):
        result = self._request("/edit/v1/prod/releases/restore-claim",{"id":release_id})
        return FrozenRelease.from_ledger(result["release"])

    def get_release(self, release_id):
        result = self._request("/edit/v1/prod/releases/status?id=" +
                               urllib.parse.quote(release_id, safe=""))
        return FrozenRelease.from_ledger(result["release"])

    def transition(self, release_id, state, detail, fencing_token=None):
        return self._request("/edit/v1/prod/releases/transition",
            {"id": release_id, "state": state, "detail": detail,
             "fencing_token": fencing_token})

    def renew(self, release_id, fencing_token, lease_ms=None):
        body = {"id": release_id, "fencing_token": fencing_token}
        if lease_ms is not None:
            body["lease_ms"] = lease_ms
        return self._request("/edit/v1/prod/releases/renew", body)


class ReleaseObserverHTTP:
    """GET-only, allowlisted production-ledger client.

    This intentionally does not inherit from ``LedgerHTTP``: a caller holding
    the observer bearer has no method that can construct a mutation request.
    Provider operations and authored release operations are outside this
    adapter's contract.
    """

    MAX_RESPONSE_BYTES = 1024 * 1024
    _FIXED_GETS = frozenset({
        "/edit/v1/prod/releases/frontier",
        "/edit/v1/prod/releases/audit",
    })

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            return None

    def __init__(self, base_url, bearer, opener=None, timeout=15):
        _require_https_url(base_url, "release observer")
        if not isinstance(bearer, str) or not bearer or len(bearer) > 4096:
            raise ObserverError("observer credential unavailable")
        self.base_url = base_url.rstrip("/")
        self.__bearer = bearer
        self.__opener = opener or urllib.request.build_opener(self._NoRedirect).open
        self.__timeout = timeout

    def __get(self, path):
        if path not in self._FIXED_GETS and not path.startswith(
                "/edit/v1/prod/releases/status?id="):
            raise ObserverError("observer endpoint is not allowlisted")
        request = urllib.request.Request(self.base_url + path, method="GET", headers={
            "Authorization":"Bearer " + self.__bearer,
            "User-Agent":SERVICE_USER_AGENT,
        })
        try:
            with self.__opener(request, timeout=self.__timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, OSError):
            raise ObserverError("observer request unavailable") from None
        if len(raw) > self.MAX_RESPONSE_BYTES:
            raise ObserverError("observer response exceeded bound")
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise ObserverError("observer response malformed") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ObserverError("observer response rejected")
        return result

    def preparation_context(self):
        result = self.__get("/edit/v1/prod/releases/frontier")
        context = result.get("context")
        if not isinstance(context, dict):
            raise ObserverError("observer frontier malformed")
        return context

    def audit(self):
        result = self.__get("/edit/v1/prod/releases/audit")
        audit = result.get("audit")
        if not isinstance(audit, dict):
            raise ObserverError("observer audit malformed")
        return audit

    def get_release(self, release_id):
        if not isinstance(release_id, str) or not BOUNDED_PROVIDER_ID_RE.fullmatch(release_id):
            raise ObserverError("observer release id malformed")
        result = self.__get("/edit/v1/prod/releases/status?id=" +
                            urllib.parse.quote(release_id, safe=""))
        release = result.get("release")
        if not isinstance(release, dict):
            raise ObserverError("observer release malformed")
        return release


class GitRefAdapter:
    def __init__(self, repo, run=subprocess.run, timeout=120):
        self.repo, self._run, self.timeout = repo, run, timeout

    def _git(self, argv, **kwargs):
        try:
            return self._run(argv, timeout=self.timeout, **kwargs)
        except subprocess.TimeoutExpired:
            raise ReleaseError("git operation exceeded its bounded timeout") from None

    def is_ancestor(self, base, candidate):
        result = self._git(["git", "merge-base", "--is-ancestor", base, candidate],
                           cwd=self.repo, check=False, capture_output=True)
        return result.returncode == 0

    def tree(self, sha):
        result = self._git(["git", "rev-parse", f"{sha}^{{tree}}"], cwd=self.repo,
                           check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def require_clean_candidate(self, sha):
        head = self._git(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                         capture_output=True, text=True).stdout.strip()
        status = self._git(["git", "status", "--porcelain", "--untracked-files=all"],
                           cwd=self.repo, check=True, capture_output=True, text=True).stdout
        if head != sha or status.strip():
            raise ReleaseError("candidate checkout is not the clean frozen commit")

    def commit_projected_tree(self, base_sha, timestamp):
        """Create a deterministic synthetic commit without moving ambient HEAD."""
        self._git(["git", "add", "-A"], cwd=self.repo, check=True,
                  capture_output=True, text=True)
        tree = self._git(["git", "write-tree"], cwd=self.repo, check=True,
                         capture_output=True, text=True).stdout.strip()
        env = dict(os.environ, GIT_AUTHOR_NAME="Sonsteng Release Service",
                   GIT_AUTHOR_EMAIL="release@localhost",
                   GIT_COMMITTER_NAME="Sonsteng Release Service",
                   GIT_COMMITTER_EMAIL="release@localhost",
                   GIT_AUTHOR_DATE=f"@{int(timestamp)} +0000",
                   GIT_COMMITTER_DATE=f"@{int(timestamp)} +0000")
        commit = self._git(["git", "commit-tree", tree, "-p", base_sha,
                            "-m", "release: accepted-only projection"],
                           cwd=self.repo, check=True, capture_output=True,
                           text=True, env=env).stdout.strip()
        return commit, tree

    def retain_release_candidate(self, candidate_sha, identity):
        """Pin a synthetic candidate for the lifetime of its release evidence."""
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ReleaseError("candidate retention identity is invalid")
        ref = f"refs/sonsteng/releases/{identity}"
        existing = self._git(["git", "rev-parse", "--verify", "--quiet", ref],
                             cwd=self.repo, check=False, capture_output=True,
                             text=True).stdout.strip()
        if existing and existing != candidate_sha:
            raise ReleaseError("candidate retention ref conflicts with immutable evidence")
        if not existing:
            result = self._git(["git", "update-ref", ref, candidate_sha,
                                "0" * 40], cwd=self.repo, check=False,
                               capture_output=True, text=True)
            if result.returncode != 0:
                # A concurrent deterministic preparation may have won the
                # create-only race. Accept only the identical pinned object.
                existing = self._git(["git", "rev-parse", "--verify", "--quiet", ref],
                                     cwd=self.repo, check=False, capture_output=True,
                                     text=True).stdout.strip()
                if existing != candidate_sha:
                    raise ReleaseError("candidate retention ref could not be created")
        return ref

    @contextmanager
    def isolated_checkout(self, sha):
        """Materialize an immutable candidate without pinning the DEV checkout."""
        root = pathlib.Path(tempfile.mkdtemp(prefix="sonsteng-prod-candidate-"))
        added = False
        try:
            self._git(["git", "worktree", "add", "--detach", str(root), sha],
                      cwd=self.repo, check=True, capture_output=True, text=True)
            added = True
            yield root
        finally:
            if added:
                self._git(["git", "worktree", "remove", "--force", str(root)],
                          cwd=self.repo, check=True, capture_output=True, text=True)
            shutil.rmtree(root, ignore_errors=True)


class CandidateValidator:
    """Prove that the frozen manifest reproduces the authorized git frontier."""

    def __init__(self, git, manifest):
        self.git, self.manifest = git, dict(manifest)

    def __call__(self, release):
        common_mismatch = self.manifest.get("base_sha") != release.base_sha or \
           self.manifest.get("candidate_sha") != release.candidate_sha
        if release.schema_version >= 2:
            held = self.manifest.get("held_exclusions") or []
            held_ids = {item.get("operation_id") for item in held if isinstance(item, dict)}
            binding_mismatch = (tuple(self.manifest.get("accepted_operation_ids") or ()) !=
                                release.operation_ids or
                                self.manifest.get("review_receipt_hash") != release.review_receipt_hash or
                                self.manifest.get("production_scope") != "prose_only_v1" or
                                len(held_ids) != len(held) or
                                bool(set(self.manifest.get("accepted_operation_ids") or ()) & held_ids))
        else:
            binding_mismatch = (tuple(self.manifest.get("suggestion_ids") or ()) != release.suggestion_ids or
                                tuple(self.manifest.get("batch_commits") or ()) != release.batch_commits)
        if common_mismatch or binding_mismatch:
            raise ReleaseError("manifest binding mismatch")
        manifest_hash = hashlib.sha256(_canonical(self.manifest)).hexdigest()
        if manifest_hash != release.manifest_hash:
            raise ReleaseError("manifest hash mismatch")
        if not self.git.is_ancestor(release.base_sha, release.candidate_sha):
            raise ReleaseError("candidate is not descended from production base")
        if self.git.tree(release.candidate_sha) != self.manifest.get("candidate_tree"):
            raise ReleaseError("candidate tree mismatch")
        self.git.require_clean_candidate(release.candidate_sha)


class AcceptedProjectionCandidateBuilder:
    """Create a deterministic accepted-only commit from a verified PROD base."""

    def __init__(self, git, manifest_path, writer=None, materializer=None):
        self.git = git
        self.manifest_path = pathlib.Path(manifest_path)
        self.writer = writer or ProjectionTreeWriter()
        self.materializer = materializer or AcceptedOnlyMaterializer()

    def build(self, context):
        projection_context = context.get("projection") or {}
        if projection_context.get("blocked_reason"):
            raise ReleaseError("production projection blocked: " +
                               projection_context["blocked_reason"])
        sources = projection_context.get("sources") or []
        receipts = projection_context.get("review_receipts") or []
        if not sources or not receipts:
            return None
        base_sha = context.get("base_sha") or sources[0].get("prod_base")
        if not base_sha or any(not source.get("prod_base") for source in sources):
            raise ReleaseError("submitted review does not match the verified production base")
        # An unrelated release may advance the global PROD frontier after this
        # source was reviewed. Permit that ancestry-only rebase here; the
        # ProjectionTreeWriter still applies the immutable original value in an
        # isolated checkout of `base_sha`, so any same-source drift fails closed
        # before a candidate commit is created.
        if any(source["prod_base"] != base_sha and
               not self.git.is_ancestor(source["prod_base"], base_sha)
               for source in sources):
            raise ReleaseError("submitted review does not descend from the verified production base")
        receipt_hashes = tuple(sorted(item["receipt_hash"] for item in receipts))
        receipt_binding = hashlib.sha256(_canonical(receipt_hashes)).hexdigest()
        timestamp = max(int(item.get("created_at", 0)) for item in receipts) // 1000
        with self.git.isolated_checkout(base_sha) as root:
            rebased = [source for source in sources if source["prod_base"] != base_sha]
            if rebased:
                verifier = getattr(self.writer,"verify_rebased_sources",None)
                if verifier is None:
                    raise ReleaseError("projection writer cannot verify rebased sources")
                verifier(root,rebased)
            materialized = self.materializer.materialize(sources)
            if materialized.structural_operations:
                raise ReleaseError("structural operations are deferred from production")
            if not materialized.accepted_operation_ids:
                return None
            evidence = self.writer.write(root, materialized)
            candidate_git = GitRefAdapter(root)
            candidate_sha, candidate_tree = candidate_git.commit_projected_tree(base_sha,timestamp)
            exclusions = [dataclasses.asdict(item) for item in materialized.exclusions]
            manifest = {"schema_version":2,"target_environment":"production",
                "production_scope":"prose_only_v1",
                "base_sha":base_sha,"candidate_sha":candidate_sha,
                "candidate_tree":candidate_tree,"generator_id":evidence["generator_id"],
                "review_receipt_hash":receipt_binding,"review_receipts":list(receipt_hashes),
                "accepted_operation_ids":list(materialized.accepted_operation_ids),
                "held_exclusions":exclusions,"generated_parity":evidence["parity_verified"]}
            manifest_hash = hashlib.sha256(_canonical(manifest)).hexdigest()
            # commit-tree does not move a branch. Pin the object before the
            # temporary worktree is removed so approval may safely be delayed
            # and Git GC cannot erase the immutable candidate meanwhile.
            self.git.retain_release_candidate(candidate_sha,manifest_hash)
        self.manifest_path.parent.mkdir(parents=True,exist_ok=True)
        payload = _canonical(manifest) + b"\n"
        with tempfile.NamedTemporaryFile(dir=self.manifest_path.parent,delete=False) as target:
            target.write(payload)
            temporary = pathlib.Path(target.name)
        temporary.replace(self.manifest_path)
        return {"manifest":manifest,"manifest_hash":manifest_hash,
            "candidate_sha":candidate_sha,"candidate_tree":candidate_tree,
            "review_receipt_hash":receipt_binding,
            "review_receipts":receipt_hashes,
            "projection_identity":manifest_hash,
            "accepted_operation_ids":materialized.accepted_operation_ids,
            "held_exclusions":materialized.exclusions}


class ProductionCandidateBuilder:
    """Freeze the latest contiguous DEV frontier for human Publisher review."""

    def __init__(self, ledger, git, manifest_path, bootstrap_base_sha=None,
                 attempt_id_factory=None, projection_builder=None):
        self.ledger, self.git = ledger, git
        self.manifest_path = pathlib.Path(manifest_path)
        self.bootstrap_base_sha = bootstrap_base_sha
        self.attempt_id_factory = attempt_id_factory or (lambda: secrets.token_hex(12))
        self.projection_builder = projection_builder or AcceptedProjectionCandidateBuilder(
            git,manifest_path)

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

    def _frozen_tree(self, candidate_sha):
        if hasattr(self.git, "isolated_checkout"):
            with self.git.isolated_checkout(candidate_sha) as root:
                frozen = GitRefAdapter(root)
                frozen.require_clean_candidate(candidate_sha)
                return frozen.tree(candidate_sha)
        self.git.require_clean_candidate(candidate_sha)
        return self.git.tree(candidate_sha)

    def prepare_latest(self):
        context = self.ledger.preparation_context()
        active = context.get("active_release")
        if active:
            if int(active.get("schema_version") or 1) >= 2:
                if not self.manifest_path.is_file():
                    raise ReleaseError("active operation release manifest is unavailable")
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if hashlib.sha256(_canonical(manifest)).hexdigest() != active["manifest_hash"]:
                    raise ReleaseError("active release manifest cannot be reproduced")
                return {"ok":True,"replay":True,"release":active}
            batches = active.get("batches") or []
            manifest = self._manifest(active["base_sha"], active["candidate_sha"],
                self.git.tree(active["candidate_sha"]), active["generator_id"],
                [item["batch_id"] for item in batches],
                [item["commit_sha"] for item in batches], active.get("suggestion_ids") or [])
            if hashlib.sha256(_canonical(manifest)).hexdigest() != active["manifest_hash"]:
                raise ReleaseError("active release manifest cannot be reproduced")
            self._write(manifest)
            return {"ok": True, "replay": True, "release": active}

        if (context.get("projection") or {}).get("sources"):
            built = self.projection_builder.build(context)
            if built is None:
                return None
            manifest = built["manifest"]
            release_id = "release-" + built["manifest_hash"][:16] + "-" + self.attempt_id_factory()
            held = [{"operation_id":item.operation_id,"decision":item.reason,
                     "reason":item.reason,"source_ref":item.source_ref}
                    for item in built["held_exclusions"]]
            evidence_hash = hashlib.sha256(_canonical({
                "review_receipts":list(built["review_receipts"]),
                "accepted_operation_ids":list(built["accepted_operation_ids"]),
                "held_exclusions":held,
                "generator_id":manifest["generator_id"]})).hexdigest()
            binding = {"schema_version":2,"id":release_id,"idempotency_key":release_id,
                "target_batch_id":"operation-frontier","base_sha":manifest["base_sha"],
                "candidate_sha":built["candidate_sha"],"generator_id":manifest["generator_id"],
                "evidence_hash":evidence_hash,"manifest_hash":built["manifest_hash"],
                "ancestry_verified":True,"review_receipt_hash":built["review_receipt_hash"],
                "review_receipts":list(built["review_receipts"]),
                "projection_identity":built["projection_identity"],
                "accepted_operation_ids":list(built["accepted_operation_ids"]),
                "held_exclusions":held}
            result = self.ledger.prepare(binding)
            if not result or result.get("ok") is not True:
                raise ReleaseError("ledger rejected preparation: " +
                                   str((result or {}).get("reason","unknown")))
            return result

        batches = context.get("batches") or []
        if context.get("blocked_reason"):
            raise ReleaseError("production frontier blocked: " + context["blocked_reason"])
        if not batches:
            return None
        if any(not batch.get("suggestion_ids") for batch in batches):
            raise ReleaseError("eligible batch has no applied membership")
        generator_id = batches[0].get("generator_id")
        if not generator_id:
            raise ReleaseError("eligible batch lacks generator evidence")
        boundary = next((index for index, batch in enumerate(batches)
                         if batch.get("generator_id") != generator_id), len(batches))
        batches = batches[:boundary]
        base_sha = context.get("base_sha") or self.bootstrap_base_sha
        if not base_sha:
            raise ReleaseError("first release requires a recorded production base SHA")
        candidate_sha = batches[-1]["commit_sha"]
        if not self.git.is_ancestor(base_sha, candidate_sha):
            raise ReleaseError("candidate is not descended from production base")
        batch_ids = [batch["batch_id"] for batch in batches]
        batch_commits = [batch["commit_sha"] for batch in batches]
        suggestion_ids = sorted(item for batch in batches for item in batch["suggestion_ids"])
        manifest = self._manifest(base_sha, candidate_sha, self._frozen_tree(candidate_sha),
            generator_id, batch_ids, batch_commits, suggestion_ids)
        manifest_hash = hashlib.sha256(_canonical(manifest)).hexdigest()
        evidence_hash = hashlib.sha256(_canonical({"batch_ids":batch_ids,
            "batch_commits":batch_commits,"generator_id":generator_id,
            "suggestion_ids":suggestion_ids})).hexdigest()
        # A manifest identifies immutable release content, not an execution
        # attempt. A restored attempt must receive fresh human authorization
        # before the exact same content can run again, so the trusted service
        # (never the Publisher client) adds an opaque attempt identity.
        release_id = "release-" + manifest_hash[:16] + "-" + self.attempt_id_factory()
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
                 run=subprocess.run, opener=urllib.request.urlopen, timeout=240,
                 account_id="", api_token=""):
        _require_https_url(provenance_url, "Pages provenance")
        self.project, self.artifact_dir = project, artifact_dir
        self.candidate_root = pathlib.Path(candidate_root or pathlib.Path(artifact_dir).parent).resolve()
        self.production_branch = production_branch
        self.account_id, self._api_token = account_id, api_token
        self.provenance_url, self._run, self._opener, self.timeout = provenance_url, run, opener, timeout

    @property
    def max_operation_seconds(self):
        return self.timeout

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
            result = self._run(_wrangler("pages", "deploy", str(staged),
                "--project-name", self.project, "--branch", self.production_branch,
                "--commit-hash", manifest.candidate_sha),
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
        request = urllib.request.Request(self.provenance_url,
            headers={"Accept": "*/*", "User-Agent": SERVICE_USER_AGENT})
        with self._opener(request, timeout=30) as response:
            return response.headers.get("X-Release-SHA", "")

    def restore(self, deployment_id):
        if not BOUNDED_PROVIDER_ID_RE.fullmatch(self.account_id or "") or \
           not BOUNDED_PROVIDER_ID_RE.fullmatch(self.project or "") or \
           not BOUNDED_PROVIDER_ID_RE.fullmatch(deployment_id or "") or not self._api_token:
            raise ReleaseError("Pages rollback requires bounded API authority and identifiers")
        url = ("https://api.cloudflare.com/client/v4/accounts/" + self.account_id +
               "/pages/projects/" + self.project + "/deployments/" + deployment_id + "/rollback")
        request = urllib.request.Request(url, data=b"{}", method="POST", headers={
            "Authorization":"Bearer " + self._api_token,
            "Content-Type":"application/json", "User-Agent":SERVICE_USER_AGENT})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            try: payload = json.load(exc)
            except Exception: payload = {}
            # Cloudflare rejects rollback to the deployment already active.
            # That response proves the requested exact ID is current and is an
            # idempotent recovery success, not a reason to mutate another target.
            if exc.code == 400 and any(item.get("code") == 8000039
                                       for item in payload.get("errors", [])):
                return
            raise ReleaseError("Pages rollback API rejected the exact deployment") from None
        if payload.get("success") is not True or (payload.get("result") or {}).get("id") != deployment_id:
            raise ReleaseError("Pages rollback API did not bind the exact deployment")


class WranglerWorkerAdapter:
    """Concrete Worker version/activation CLI contract; no authorization path."""

    name = "worker"

    def __init__(self, config, provenance_url, candidate_root=None, run=subprocess.run,
                 opener=urllib.request.urlopen, timeout=240):
        _require_https_url(provenance_url, "Worker provenance")
        self.config, self.provenance_url = config, provenance_url
        self.candidate_root = pathlib.Path(candidate_root or pathlib.Path(config).parents[2]).resolve()
        self._run, self._opener, self.timeout = run, opener, timeout

    @property
    def max_operation_seconds(self):
        # Upload and activation are separate, sequential bounded commands.
        return self.timeout * 2

    def deploy(self, manifest):
        config = pathlib.Path(self.config).resolve()
        if not config.is_relative_to(self.candidate_root):
            raise ReleaseError("Worker config is outside the frozen candidate checkout")
        uploaded = self._run(_wrangler("versions", "upload", "--config", self.config,
            "--env", "production",
            "--message", "release:" + manifest.candidate_sha,
            "--var", "RELEASE_SHA:" + manifest.candidate_sha), check=True,
            cwd=self.candidate_root, capture_output=True, text=True, timeout=self.timeout)
        match = re.search(
            r"(?:^|\n)\s*Worker Version ID:\s*([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\s*(?:\n|$)",
            uploaded.stdout, re.IGNORECASE)
        version = match.group(1) if match else ""
        if not version:
            raise ReleaseError("Worker upload did not return a bounded version identifier")
        self._run(_wrangler("versions", "deploy", version, "--config", self.config,
                   "--env", "production", "--yes"), cwd=self.candidate_root, check=True, capture_output=True,
                  text=True, timeout=self.timeout)
        return {"provider_id": hashlib.sha256(version.encode()).hexdigest()[:24],
                "deployable_id": version}

    def provenance(self):
        request = urllib.request.Request(self.provenance_url,
            headers={"Accept": "*/*", "User-Agent": SERVICE_USER_AGENT})
        with self._opener(request, timeout=30) as response:
            return response.headers.get("X-Release-SHA", "")

    def restore(self, version_id):
        self._run(_wrangler("versions", "deploy", version_id, "--config", self.config,
                   "--env", "production", "--yes"), cwd=self.candidate_root, check=True,
                  capture_output=True, text=True, timeout=self.timeout)


class ProductionExecutor:
    def __init__(self, ledger, pages, worker, compatibility=None,
                 candidate_validator: Callable[[FrozenRelease], None] | None = None,
                 restorer=None, recovery_registry=None, heartbeat_interval=60,
                 lease_ms=5 * 60 * 1000, operation_margin_seconds=60):
        self.ledger, self.pages, self.worker = ledger, pages, worker
        self.compatibility = compatibility or CompatibilityGate(True, False)
        self.candidate_validator = candidate_validator
        self.restorer = restorer
        self.recovery_registry = recovery_registry
        self.heartbeat_interval = heartbeat_interval
        self.lease_ms = lease_ms
        self.operation_margin_seconds = operation_margin_seconds

    def _event(self, release, state, **detail):
        # Evidence is deliberately identifiers/hashes only; edited text and
        # credentials never enter journals or provider receipts.
        safe = {key: value for key, value in detail.items()
                if key in {"manifest_hash", "candidate_sha", "pages_id", "worker_id", "reason"}}
        result = self.ledger.transition(release.id, state, safe, release.fencing_token)
        if not result or result.get("ok") is not True:
            raise ReleaseError("ledger rejected transition: " + str((result or {}).get("reason", "unknown")))

    def _renew(self, release, lease_ms=None):
        result = self.ledger.renew(
            release.id, release.fencing_token, lease_ms or self.lease_ms)
        if not result or result.get("ok") is not True:
            raise ReleaseError("release lease renewal rejected: " +
                               str((result or {}).get("reason", "unknown")))

    def _provider_operation(self, release, target, operation):
        """Fence the full provider bound, then heartbeat while it blocks."""
        operation_seconds = getattr(target, "max_operation_seconds", 0)
        operation_lease_ms = max(
            self.lease_ms,
            int((operation_seconds + self.operation_margin_seconds) * 1000),
        )
        if operation_lease_ms > MAX_PRODUCTION_LEASE_MS:
            raise ReleaseError("provider operation bound exceeds maximum production lease")
        self._renew(release, operation_lease_ms)
        stopped = threading.Event()
        failures = []

        def heartbeat():
            while not stopped.wait(self.heartbeat_interval):
                try:
                    self._renew(release, operation_lease_ms)
                except Exception as exc:
                    failures.append(exc)
                    return

        thread = threading.Thread(target=heartbeat, name="prod-release-lease", daemon=True)
        thread.start()
        try:
            result = operation()
        finally:
            stopped.set()
            thread.join()
        if failures:
            raise ReleaseError("release lease renewal lost during provider operation") from failures[0]
        self._renew(release, operation_lease_ms)
        return result

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
                    live = targets[name].provenance()
                    if live == release.base_sha:
                        # The artifact belongs to the candidate, while activation
                        # belongs to this release attempt. After a recorded-pair
                        # restore, a fresh human-authorized attempt may reactivate
                        # that exact artifact; it must never upload ambient state.
                        self._provider_operation(release, targets[name],
                            lambda target=targets[name], artifact=recorded:
                                target.restore(artifact))
                        if targets[name].provenance() != release.candidate_sha:
                            raise ReleaseError("recorded target reactivation provenance mismatch")
                        receipts[name] = {"provider_id":hashlib.sha256(recorded.encode()).hexdigest()[:24],
                                          "reactivated":True}
                    elif live != release.candidate_sha:
                        raise ReleaseError("recorded target does not match live provenance or base")
                    self._event(release, name + "_deployed",
                        **{name + "_id": hashlib.sha256(recorded.encode()).hexdigest()[:24]})
                    continue
                receipts[name] = self._provider_operation(
                    release, targets[name],
                    lambda target=targets[name]: target.deploy(release))
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
        if release.state != "restoring" or not release.fencing_token:
            raise ReleaseError("restore requires an exclusive claimed restore lease")
        try:
            targets = {"pages":self.pages,"worker":self.worker}
            for name in reversed(self.compatibility.deployment_order()):
                target = targets[name]
                live = target.provenance()
                if live == release.base_sha:
                    continue
                if live != release.candidate_sha:
                    raise ReleaseError("restore target provenance is neither candidate nor base")
                artifact = self.restorer.artifact(release.base_sha,name)
                self._provider_operation(release,target,
                    lambda target=target,artifact=artifact:target.restore(artifact))
                if target.provenance() != release.base_sha:
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
        callbacks = {"pages":lambda:self.restore_pages(self.artifact(sha,"pages")),
                     "worker":lambda:self.restore_worker(self.artifact(sha,"worker"))}
        for target in order:
            callbacks[target]()

    def artifact(self,sha,target):
        pair = self.known_good.get(sha)
        key = "pages_deployment_id" if target == "pages" else "worker_version_id"
        if target not in {"pages","worker"} or not pair or not pair.get(key):
            raise ReleaseError("exact recorded known-good pair is unavailable")
        return pair[key]


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

    def record_pair(self, sha, pages_deployment_id, worker_version_id):
        """Compare-and-set one complete recovery pair without exposing halves."""
        if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            raise ReleaseError("recovery registry requires an exact candidate SHA")
        if not pages_deployment_id or not worker_version_id:
            raise ReleaseError("recovery registry requires a complete provider pair")
        data = self._read()
        expected = {
            "pages_deployment_id": pages_deployment_id,
            "worker_version_id": worker_version_id,
        }
        prior = data.get(sha)
        if prior is not None:
            if set(prior) != set(expected):
                raise ReleaseError("partial recovery pair conflicts with audited bootstrap")
            if prior != expected:
                raise ReleaseError("complete pair conflict")
            return True
        data[sha] = expected
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False,
                                         encoding="utf-8") as target_file:
            json.dump(data, target_file, sort_keys=True, separators=(",", ":"))
            target_file.write("\n")
            temporary = pathlib.Path(target_file.name)
        temporary.chmod(0o600)
        temporary.replace(self.path)
        return False

    @staticmethod
    def _complete_pair(pair):
        required = {"pages_deployment_id", "worker_version_id"}
        return dict(pair) if isinstance(pair, dict) and set(pair) == required and \
            all(pair.get(key) for key in required) else None

    def pair_state(self, sha):
        data = self._read()
        return sha in data, self._complete_pair(data.get(sha))

    def pair(self, sha):
        return self.pair_state(sha)[1]

    def target(self, sha, target):
        key = "pages_deployment_id" if target == "pages" else "worker_version_id"
        return self._read().get(sha, {}).get(key)

    def pairs(self):
        data = self._read()
        return {sha: complete for sha, pair in data.items()
                if (complete := self._complete_pair(pair))}
