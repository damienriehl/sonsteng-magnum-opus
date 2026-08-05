#!/usr/bin/env python3
"""Crash-safe PROD publication coordinator.

All authority-bearing I/O is injected.  This module contains no Cloudflare or
GitHub credentials and no command that can mutate a provider.  Adapters must
implement the small interfaces exercised below; production adapter enablement is
deliberately deferred to the operational rollout unit.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prod_promotion import ReleaseManifest  # noqa: E402

PRODUCTION = "production"

HEALTH_STATES = frozenset({
    "idle", "queued", "awaiting_approval", "ai_degraded", "maintenance",
    "stalled", "restoring", "restore_failed", "unavailable",
})
PRE_BREACH_SECONDS = 240
BREACH_SECONDS = 300


class PromotionError(RuntimeError): pass
class StaleFence(PromotionError): pass
class DriftDetected(PromotionError): pass
class VerificationFailed(PromotionError): pass
class ReconciliationRequired(PromotionError): pass


@dataclasses.dataclass(frozen=True)
class OperationalConfig:
    """Non-secret contract consumed by the installed PROD service."""
    api_base: str
    checkout: str
    branch: str
    lock_path: str
    state_path: str
    credential_file: str
    wrangler_environment: str = PRODUCTION

    def validate(self):
        if self.wrangler_environment != PRODUCTION:
            raise ValueError("production_environment_required")
        if self.branch != "main":
            raise ValueError("prod_branch_must_be_main")
        if not self.api_base.startswith("https://"):
            raise ValueError("https_api_required")
        paths = (self.checkout, self.lock_path, self.state_path, self.credential_file)
        if any(not os.path.isabs(value) for value in paths):
            raise ValueError("absolute_paths_required")
        if len(set(paths[1:])) != 3:
            raise ValueError("isolated_paths_required")
        return self

    def public_dict(self):
        value = dataclasses.asdict(self)
        # The credential location is useful to operators; contents never are.
        value["credential_file"] = "configured (path redacted)"
        return value


@dataclasses.dataclass(frozen=True)
class KnownGoodAttestation:
    pages_id: str
    worker_id: str
    commit_sha: str
    manifest_hash: str
    editor_map_id: str
    build_id: str
    generated_contract_hashes: dict
    pages_restorable: bool
    worker_restorable: bool

    def validate(self):
        required = (self.pages_id, self.worker_id, self.commit_sha,
                    self.manifest_hash, self.editor_map_id, self.build_id)
        if any(not value for value in required):
            raise VerificationFailed("known_good_identity_incomplete")
        if len(self.commit_sha) != 40 or not self.generated_contract_hashes:
            raise VerificationFailed("known_good_hashes_incomplete")
        if not self.pages_restorable or not self.worker_restorable:
            raise VerificationFailed("known_good_not_restorable")
        return self


def inventory_exclusive_writers(items):
    """Return a content-light receipt; any competing enabled writer denies use."""
    normalized = []
    for item in items:
        normalized.append({
            "id": str(item.get("id", "unknown")),
            "kind": str(item.get("kind", "unknown")),
            "enabled": bool(item.get("enabled")),
            "intended": bool(item.get("intended")),
        })
    conflicts = [x["id"] for x in normalized if x["enabled"] and not x["intended"]]
    return {"ok": not conflicts and bool(normalized), "writers": normalized,
            "conflicts": conflicts, "default_deny": True}


def timing_health(started_at, now=None, *, awaiting_approval_since=None):
    now = time.time() if now is None else now
    elapsed = max(0, int(now - started_at))
    approval = (max(0, int(now - awaiting_approval_since))
                if awaiting_approval_since is not None else 0)
    active = max(0, elapsed - approval)
    state = "stalled" if active >= BREACH_SECONDS else (
        "queued" if active >= PRE_BREACH_SECONDS else "idle")
    return {"state": state, "active_seconds": active,
            "awaiting_approval_seconds": approval,
            "pre_breach": active >= PRE_BREACH_SECONDS,
            "breach": active >= BREACH_SECONDS}


def content_light_alert(candidate_id, state, *, manifest_hash=None):
    if state not in HEALTH_STATES:
        raise ValueError("unknown_health_state")
    result = {"candidate_id": candidate_id, "state": state,
              "acknowledgement_required": state in {
                  "stalled", "restore_failed", "unavailable"}}
    if manifest_hash:
        result["manifest_hash"] = manifest_hash
    return result


class LiveReleaseVerifier:
    """Pure verification seam; callers inject provider and HTTP observations."""
    def __init__(self, provider, fetch):
        self.provider = provider
        self.fetch = fetch

    def verify(self, manifest, writes_expected=True):
        observed = self.provider.observe()
        if (observed.pages_id, observed.worker_id) != (
                manifest.pages_production_id, manifest.worker_version_id):
            return False
        response = self.fetch(manifest)
        expected = {
            "manifest_hash": manifest.manifest_hash,
            "pages_id": manifest.pages_production_id,
            "worker_id": manifest.worker_version_id,
            "editor_map_id": manifest.editor_map_id,
            "build_id": manifest.build_id,
            "generated_contract_hashes": manifest.generated_contract_hashes,
            "writes_enabled": bool(writes_expected),
        }
        headers = {str(k).lower(): str(v) for k, v in response["headers"].items()}
        return (response.get("status") == 200 and response.get("markers") == expected
                and "no-store" in headers.get("cache-control", "").lower())

    def verify_known_good(self, known, writes_expected=True):
        known.validate()
        observed = self.provider.observe()
        if (observed.pages_id, observed.worker_id) != (known.pages_id, known.worker_id):
            return False
        return bool(self.provider.verify_known_good(known, writes_expected=writes_expected))


@dataclasses.dataclass(frozen=True)
class Artifact:
    id: str
    digest: str
    environment: str
    kind: str


@dataclasses.dataclass(frozen=True)
class ProviderObservation:
    pages_id: str
    worker_id: str


class PromotionCoordinator:
    """Serialized publication saga with reconcile-before-effect semantics."""
    def __init__(self, ledger, provider, git, live, *, owner,
                 stabilization_checks=3):
        if not owner or stabilization_checks < 2:
            raise ValueError("coordinator_configuration")
        self.ledger = ledger; self.provider = provider
        self.git = git; self.live = live; self.owner = owner
        self.stabilization_checks = stabilization_checks

    def _fence(self, token):
        if not self.ledger.assert_fence(self.owner, token):
            raise StaleFence("stale_fence")

    def _journal(self, token, kind, detail=None):
        self._fence(token)
        self.ledger.journal(token, kind, detail or {})

    def _effect(self, token, name, observe, invoke):
        """Observe, journal intent, invoke once, then independently observe."""
        self._fence(token)
        found = observe()
        if found is not None:
            self._journal(token, name + "_reconciled", {"id": found.id})
            return found
        self._journal(token, name + "_intent")
        invoke()
        self._fence(token)
        found = observe()
        if found is None:
            raise VerificationFailed(name + "_ambiguous")
        self._journal(token, name + "_observed", {"id": found.id})
        return found

    def _artifact(self, token, artifact, expected_digest, expected_kind):
        self._fence(token)
        pinned = self.provider.fetch_artifact(artifact)
        if (pinned.id != artifact.id or pinned.environment != PRODUCTION or
                pinned.kind != expected_kind or pinned.digest != expected_digest):
            raise VerificationFailed("artifact_identity_mismatch")
        self._journal(token, "artifact_verified", {"id": pinned.id, "kind": pinned.kind,
                                                     "digest": pinned.digest})
        return pinned

    def reconcile(self):
        state = self.ledger.reconcile_state()
        if state.get("health") != "healthy": return state.get("health")
        known = state.get("known_good")
        if known and not state.get("active"):
            observed = self.provider.observe()
            if ((observed.pages_id, observed.worker_id) != (known.pages_id, known.worker_id)
                    or self.git.observe_main() != known.commit_sha):
                # No claimant owns a token yet. The ledger owns the CAS/pause.
                self.ledger.pause(0, "stalled", "out_of_band_drift")
                return "paused_drift"
        return "ok"

    def run_once(self):
        status = self.reconcile()
        if status != "ok": return status
        claim = self.ledger.claim(self.owner)
        if not claim: return "idle"
        candidate, token = claim["candidate"], claim["fencing_token"]
        if candidate.get("environment") != PRODUCTION:
            self.ledger.pause(token, "stalled", "cross_environment_candidate")
            return "paused_drift"
        if not candidate.get("resource_compatible"):
            self._journal(token, "maintenance_gate", {"reason": "platform_resource_change"})
            self.ledger.release_lease(token)
            return "maintenance_required"
        if candidate.get("approval_required"):
            self._journal(token, "awaiting_approval", {"tuple_revalidation_required": True})
            self.ledger.release_lease(token)
            return "awaiting_approval"
        known = self.ledger.reconcile_state().get("known_good")
        if not known or candidate.get("base_sha") != known.commit_sha:
            self._journal(token, "revalidation_required", {
                "candidate_base": candidate.get("base_sha"),
                "verified_base": getattr(known, "commit_sha", None),
            })
            self.ledger.release_lease(token)
            return "revalidation_required"
        return self.publish(candidate, token)

    def _verify_no_drift(self, token, known):
        self._fence(token)
        pair = self.provider.observe()
        if (pair.pages_id, pair.worker_id) != (known.pages_id, known.worker_id):
            raise DriftDetected("provider_drift")
        if self.git.observe_main() != known.commit_sha:
            raise DriftDetected("main_drift")
        self._journal(token, "pre_effect_observation", {"pages_id": pair.pages_id,
                                                         "worker_id": pair.worker_id,
                                                         "main": known.commit_sha})

    def publish(self, candidate, token):
        known = self.ledger.reconcile_state()["known_good"]
        production_started = False
        try:
            reconciled = self._reconcile_already_published(candidate, token)
            if reconciled:
                return reconciled
            self._verify_no_drift(token, known)
            ref = candidate["candidate_ref"]; sha = candidate["commit_sha"]
            current_ref = self.git.observe_ref(ref)
            if current_ref not in (None, sha): raise DriftDetected("candidate_ref_drift")
            if current_ref is None:
                self._journal(token, "release_ref_intent", {"ref": ref, "sha": sha})
                self._fence(token); self.git.create_ref(ref, sha, expected_absent=True)
            if self.git.observe_ref(ref) != sha: raise VerificationFailed("release_ref_ambiguous")
            self._journal(token, "release_ref_observed", {"ref": ref, "sha": sha})

            key = candidate["attempt_id"]
            digest = candidate["artifact_digest"]
            preview = self._effect(token, "pages_preview",
                lambda: self.provider.find_pages_preview(key),
                lambda: self.provider.deploy_pages_preview(key, digest, PRODUCTION))
            preview = self._artifact(token, preview, digest, "pages-preview")
            worker = self._effect(token, "worker_upload",
                lambda: self.provider.find_worker_version(key),
                lambda: self.provider.upload_worker(key, digest, PRODUCTION))
            worker = self._artifact(token, worker, digest, "worker-version")

            # Maintenance begins before either production component is changed.
            self._journal(token, "fence_writes_intent")
            self._fence(token)
            if not self.ledger.fence_writes(token, candidate["commit_sha"]):
                raise VerificationFailed("write_fence_failed")
            self._fence(token)
            if not self.ledger.writes_fenced(token):
                raise VerificationFailed("write_fence_unproven")
            self._journal(token, "fence_writes_observed", {
                "intended_epoch": candidate["commit_sha"], "writes_fenced": True})
            # From this point an ambiguous provider response may hide a live
            # Pages mutation, so every handled failure performs paired restore.
            production_started = True
            production = self._effect(token, "pages_production",
                lambda: self.provider.find_pages_production(key),
                lambda: self.provider.upload_pages_production(key, digest, PRODUCTION))
            production = self._artifact(token, production, digest, "pages-production")
            if production.id == preview.id: raise VerificationFailed("pages_ids_not_distinct")
            manifest = self._manifest(candidate, preview, production, worker)
            self._journal(token, "manifest_verified", {"manifest_hash": manifest.manifest_hash})

            # A direct Pages upload makes the Pages half live. Writes remain
            # fenced while the pair is mixed and until repeated exact checks pass.
            self._fence(token)
            if self.provider.observe().pages_id != production.id:
                # The provider adapter may expose direct-upload activation as the
                # production artifact itself; reconcile it before Worker traffic.
                self.provider.restore_pages(production.id, PRODUCTION)
            self._journal(token, "pages_activation_observed", {"id": production.id})
            self._journal(token, "worker_activation_intent", {"id": worker.id})
            self._fence(token); self.provider.activate_worker(worker.id, PRODUCTION)
            if self.provider.observe().worker_id != worker.id:
                raise VerificationFailed("worker_activation_ambiguous")
            self._journal(token, "worker_activation_observed", {"id": worker.id})

            for index in range(self.stabilization_checks):
                self._fence(token)
                observation = self.provider.observe()
                exact = (observation.pages_id == production.id and observation.worker_id == worker.id)
                if not self.ledger.writes_fenced(token):
                    raise VerificationFailed("write_fence_lost")
                if not exact or not self.live.verify(manifest, writes_expected=False):
                    raise VerificationFailed("mixed_or_unverified_live_pair")
                self._journal(token, "live_check", {"ordinal": index + 1,
                                                     "manifest_hash": manifest.manifest_hash})

            self._journal(token, "main_cas_intent", {"old": known.commit_sha, "new": sha})
            self._fence(token)
            if not self.git.cas_main(known.commit_sha, sha, force=False):
                raise VerificationFailed("main_cas_conflict")
            if self.git.observe_main() != sha:
                self._journal(token, "main_cas_ambiguous", {"expected": sha})
                raise ReconciliationRequired("main_cas_ambiguous")
            self._journal(token, "main_cas_observed", {"sha": sha})
            self._fence(token)
            self.ledger.finish(token, "published", manifest=manifest)
            return "published"
        except ReconciliationRequired:
            return "reconciliation_required"
        except (StaleFence, TimeoutError):
            raise
        except Exception as exc:
            return self._restore(token, known, str(exc), mutate=production_started)

    @staticmethod
    def _manifest(candidate, preview, production, worker):
        return ReleaseManifest(
            candidate_id=candidate["id"], attempt_id=candidate["attempt_id"],
            environment=PRODUCTION, base_sha=candidate["base_sha"],
            commit_sha=candidate["commit_sha"], candidate_ref=candidate["candidate_ref"],
            pages_preview_id=preview.id, pages_production_id=production.id,
            worker_version_id=worker.id, editor_map_id=candidate["editor_map_id"],
            build_id=candidate["build_id"],
            generated_contract_hashes=candidate["generated_contract_hashes"])

    def _reconcile_already_published(self, candidate, token):
        """Finalize a CAS that succeeded but whose immediate read was stale.

        This path is observation-only until the terminal ledger CAS. It never
        repeats a provider mutation or the main CAS.
        """
        key = candidate["attempt_id"]
        preview = self.provider.find_pages_preview(key)
        production = self.provider.find_pages_production(key)
        worker = self.provider.find_worker_version(key)
        if not all((preview, production, worker)):
            return None
        observed = self.provider.observe()
        if ((observed.pages_id, observed.worker_id) != (production.id, worker.id)
                or self.git.observe_main() != candidate["commit_sha"]):
            return None
        try:
            self._fence(token)
            if not self.ledger.writes_fenced(token):
                raise VerificationFailed("write_fence_lost_during_reconcile")
            digest = candidate["artifact_digest"]
            preview = self._artifact(token, preview, digest, "pages-preview")
            production = self._artifact(token, production, digest, "pages-production")
            worker = self._artifact(token, worker, digest, "worker-version")
            manifest = self._manifest(candidate, preview, production, worker)
            for index in range(self.stabilization_checks):
                self._fence(token)
                if (not self.ledger.writes_fenced(token)
                        or not self.live.verify(manifest, writes_expected=False)):
                    raise VerificationFailed("post_cas_live_ambiguous")
                self._journal(token, "post_cas_reconcile_check", {
                    "ordinal": index + 1, "manifest_hash": manifest.manifest_hash})
            self._journal(token, "main_cas_reconciled", {"sha": candidate["commit_sha"]})
            self._fence(token)
            self.ledger.finish(token, "published", manifest=manifest)
            return "published"
        except StaleFence:
            raise
        except Exception as exc:
            self._journal(token, "post_cas_reconciliation_pending", {"reason": str(exc)})
            raise ReconciliationRequired("post_cas_reconciliation_pending") from exc

    def _restore(self, token, known, reason, *, mutate=True):
        try:
            # Restore BOTH even if one appears unchanged: observations may be stale.
            if mutate:
                self._journal(token, "restore_pages_intent", {"id": known.pages_id})
                self._fence(token); self.provider.restore_pages(known.pages_id, PRODUCTION)
                self._journal(token, "restore_worker_intent", {"id": known.worker_id})
                self._fence(token); self.provider.restore_worker(known.worker_id, PRODUCTION)
            self._fence(token)
            observed = self.provider.observe()
            if ((observed.pages_id, observed.worker_id) != (known.pages_id, known.worker_id)
                    or not self.live.verify_known_good(known, writes_expected=False)):
                raise VerificationFailed("restore_unverified")
            self._journal(token, "restore_verified", {"pages_id": known.pages_id,
                                                       "worker_id": known.worker_id})
            self._fence(token); self.ledger.finish(token, "failed", reason=reason)
            return "restored"
        except StaleFence:
            raise
        except Exception:
            self._fence(token)
            self.ledger.pause(token, "restore_failed", "restoration_unverified")
            return "restore_failed"


def main():
    if os.environ.get("PROD_PROMOTION_ENABLED") != "1":
        print(json.dumps({"state": "maintenance", "reason": "rollout_disabled",
                          "content_light": True}, sort_keys=True))
        return 0
    print(json.dumps({"state": "unavailable", "reason": "live_adapter_not_configured",
                      "acknowledgement_required": True,
                      "content_light": True}, sort_keys=True))
    return 78


if __name__ == "__main__": raise SystemExit(main())
