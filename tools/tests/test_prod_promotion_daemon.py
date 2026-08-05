#!/usr/bin/env python3
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import prod_promotion_daemon as pd  # noqa: E402


def candidate():
    return {
        "id": "cand-1", "attempt_id": "cand-1:1", "environment": "production",
        "base_sha": "a" * 40, "commit_sha": "b" * 40,
        "candidate_ref": "refs/heads/releases/cand-1", "artifact_digest": "d" * 64,
        "editor_map_id": "map-1", "build_id": "build-1",
        "generated_contract_hashes": {"spine": "c" * 64},
        "resource_compatible": True, "approval_required": False,
    }


class FakeLedger:
    def __init__(self):
        self.token = 1; self.events = []; self.final = None; self.health = "healthy"
        self.known = pd.KnownGoodAttestation(
            pages_id="old-pages", worker_id="old-worker", commit_sha="a" * 40,
            manifest_hash="old-manifest", editor_map_id="old-map",
            build_id="old-build", generated_contract_hashes={"spine": "c" * 64},
            pages_restorable=True, worker_restorable=True,
        )
        self.item = candidate(); self.released = False; self.fenced = False; self.active = None
        self.fence_ok = True; self.fence_calls = []
    def reconcile_state(self): return {"health": self.health, "known_good": self.known, "active": self.active}
    def claim(self, owner):
        if self.released: return None
        self.active = copy.deepcopy(self.item)
        return {"candidate": copy.deepcopy(self.item), "fencing_token": self.token}
    def assert_fence(self, owner, token): return token == self.token
    def journal(self, token, kind, detail):
        if token != self.token: raise pd.StaleFence("stale_fence")
        self.events.append((kind, detail))
    def fence_writes(self, token, intended_epoch):
        self.fence_calls.append(intended_epoch)
        if token != self.token or not self.fence_ok: return False
        self.fenced = True
        return True
    def writes_fenced(self, token): return token == self.token and self.fenced
    def finish(self, token, outcome, manifest=None, reason=None):
        if token != self.token: raise pd.StaleFence("stale_fence")
        self.final = (outcome, manifest, reason); self.released = True; self.fenced = False; self.active = None
    def release_lease(self, token): self.released = True
    def pause(self, token, health, reason): self.health = health; self.events.append(("paused", reason))


class FakeProvider:
    def __init__(self):
        self.preview = None; self.production = None; self.worker = None
        self.live_pair = ("old-pages", "old-worker"); self.calls = []
        self.timeout_once = set(); self.tamper = False
        self.ledger = None; self.mutation_fences = []
    def _record_fence(self, pair):
        self.mutation_fences.append((pair, bool(self.ledger and self.ledger.fenced)))
    def observe(self): return pd.ProviderObservation(*self.live_pair)
    def find_pages_preview(self, key): return self.preview
    def deploy_pages_preview(self, key, digest, environment):
        self.calls.append("preview"); self.preview = pd.Artifact("preview-new", digest, environment, "pages-preview")
        if "preview" in self.timeout_once: self.timeout_once.remove("preview"); raise TimeoutError()
        return self.preview
    def find_worker_version(self, key): return self.worker
    def upload_worker(self, key, digest, environment):
        self.calls.append("worker-upload"); self.worker = pd.Artifact("worker-new", digest, environment, "worker-version"); return self.worker
    def find_pages_production(self, key): return self.production
    def upload_pages_production(self, key, digest, environment):
        self.calls.append("pages-production"); self.production = pd.Artifact("pages-new", digest, environment, "pages-production"); return self.production
    def fetch_artifact(self, artifact):
        return pd.Artifact(artifact.id, "bad" if self.tamper else artifact.digest, artifact.environment, artifact.kind)
    def activate_worker(self, version_id, environment):
        self.calls.append("activate-worker"); self.live_pair = (self.live_pair[0], version_id); self._record_fence(self.live_pair)
    def restore_pages(self, pages_id, environment):
        self.calls.append("restore-pages"); self.live_pair = (pages_id, self.live_pair[1]); self._record_fence(self.live_pair)
    def restore_worker(self, worker_id, environment):
        self.calls.append("restore-worker"); self.live_pair = (self.live_pair[0], worker_id); self._record_fence(self.live_pair)


class FakeGit:
    def __init__(self):
        self.main = "a" * 40; self.refs = {}; self.conflict = False
        self.stale_after_cas = False; self.cas_calls = 0
    def observe_main(self):
        if self.stale_after_cas and self.cas_calls:
            self.stale_after_cas = False
            return "a" * 40
        return self.main
    def observe_ref(self, ref): return self.refs.get(ref)
    def create_ref(self, ref, sha, expected_absent=True): self.refs[ref] = sha
    def cas_main(self, expected, new, force=False):
        self.cas_calls += 1
        if force: raise AssertionError("force forbidden")
        if self.conflict or self.main != expected: return False
        self.main = new; return True


class FakeLive:
    def __init__(self, provider): self.provider = provider; self.reject = False; self.checks = 0
    def verify(self, manifest, writes_expected):
        self.checks += 1
        pair = self.provider.live_pair
        exact = pair == (manifest.pages_production_id, manifest.worker_version_id)
        return (exact and not self.reject and writes_expected is False
                and self.provider.ledger.fenced)
    def verify_known_good(self, known, writes_expected):
        return self.provider.live_pair == (known.pages_id, known.worker_id) and writes_expected is False


def coordinator():
    ledger, provider = FakeLedger(), FakeProvider()
    provider.ledger = ledger
    git, live = FakeGit(), FakeLive(provider)
    return pd.PromotionCoordinator(ledger, provider, git, live, owner="test", stabilization_checks=3), ledger, provider, git, live


class CoordinatorTest(unittest.TestCase):
    def test_success_verifies_exact_pair_then_nonforced_cas(self):
        c, ledger, provider, git, live = coordinator()
        self.assertEqual(c.run_once(), "published")
        self.assertEqual(git.main, "b" * 40)
        self.assertEqual(live.checks, 3)
        self.assertEqual(ledger.final[0], "published")
        self.assertIn(("fence_writes_intent", {}), ledger.events)
        self.assertEqual(ledger.fence_calls, ["b" * 40])
        self.assertFalse(ledger.fenced)
        self.assertLess(provider.calls.index("pages-production"), provider.calls.index("activate-worker"))

    def test_main_cas_conflict_restores_both_and_leaves_main(self):
        c, ledger, provider, git, _ = coordinator(); git.conflict = True
        self.assertEqual(c.run_once(), "restored")
        self.assertEqual(provider.live_pair, ("old-pages", "old-worker"))
        self.assertIn("restore-pages", provider.calls); self.assertIn("restore-worker", provider.calls)
        self.assertEqual(git.main, "a" * 40)
        # Both mixed combinations occurred only while the durable write fence held.
        mixed = [row for row in provider.mutation_fences
                 if row[0] in (("pages-new", "old-worker"), ("old-pages", "worker-new"))]
        self.assertTrue(mixed); self.assertTrue(all(fenced for _, fenced in mixed))

    def test_mixed_pair_never_accepts_writes(self):
        c, ledger, provider, git, live = coordinator(); live.reject = True
        self.assertEqual(c.run_once(), "restored")
        self.assertEqual(provider.live_pair, ("old-pages", "old-worker"))
        self.assertNotEqual(ledger.final[0], "published")

    def test_unproven_write_fence_causes_safe_failure_before_prod_mutation(self):
        c, ledger, provider, git, live = coordinator(); ledger.fence_ok = False
        self.assertEqual(c.run_once(), "restored")
        self.assertNotIn("pages-production", provider.calls)
        self.assertNotIn("activate-worker", provider.calls)
        self.assertEqual(provider.live_pair, ("old-pages", "old-worker"))

    def test_stale_token_blocks_every_external_effect_and_finalize(self):
        c, ledger, provider, git, live = coordinator(); ledger.token = 2
        with self.assertRaises(pd.StaleFence): c.publish(candidate(), 1)
        self.assertEqual(provider.calls, []); self.assertIsNone(ledger.final)

    def test_approval_wait_releases_lease(self):
        c, ledger, provider, git, live = coordinator(); ledger.item["approval_required"] = True
        self.assertEqual(c.run_once(), "awaiting_approval")
        self.assertTrue(ledger.released); self.assertEqual(provider.calls, [])

    def test_incompatible_resource_is_maintenance_gated(self):
        c, ledger, provider, git, live = coordinator(); ledger.item["resource_compatible"] = False
        self.assertEqual(c.run_once(), "maintenance_required")
        self.assertEqual(provider.calls, [])

    def test_waiting_candidate_reanchors_before_any_publication_effect(self):
        c, ledger, provider, git, live = coordinator()
        ledger.item["base_sha"] = "9" * 40
        self.assertEqual(c.run_once(), "revalidation_required")
        self.assertTrue(ledger.released); self.assertEqual(provider.calls, [])

    def test_tampered_pinned_artifact_is_restored(self):
        c, ledger, provider, git, live = coordinator(); provider.tamper = True
        self.assertEqual(c.run_once(), "restored")
        self.assertNotEqual(ledger.final[0], "published")

    def test_stale_read_after_successful_main_cas_waits_then_reconciles(self):
        c, ledger, provider, git, live = coordinator(); git.stale_after_cas = True
        self.assertEqual(c.run_once(), "reconciliation_required")
        self.assertEqual(git.main, "b" * 40)
        self.assertTrue(ledger.fenced); self.assertIsNone(ledger.final)
        self.assertNotIn("restore-worker", provider.calls)
        calls = list(provider.calls)
        self.assertEqual(c.run_once(), "published")
        self.assertEqual(provider.calls, calls)
        self.assertEqual(git.cas_calls, 1)
        self.assertEqual(ledger.final[0], "published")


if __name__ == "__main__": unittest.main()
