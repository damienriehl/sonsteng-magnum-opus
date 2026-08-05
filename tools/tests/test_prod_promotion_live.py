#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import prod_promotion as pp  # noqa: E402
import prod_promotion_daemon as daemon  # noqa: E402


class Provider:
    def __init__(self, pages="pages-2", worker="worker-2"):
        self.pair = daemon.ProviderObservation(pages, worker)
    def observe(self): return self.pair
    def verify_known_good(self, known, writes_expected=True): return writes_expected


def manifest():
    return pp.ReleaseManifest(
        candidate_id="c", attempt_id="c:1", environment="production",
        base_sha="a" * 40, commit_sha="b" * 40,
        candidate_ref="refs/heads/releases/c", pages_preview_id="preview-1",
        pages_production_id="pages-2", worker_version_id="worker-2",
        editor_map_id="map-2", build_id="build-2",
        generated_contract_hashes={"spine": "c" * 64})


class ProdPromotionLiveTest(unittest.TestCase):
    def test_exact_pair_markers_hashes_and_no_store_are_required(self):
        expected = manifest()
        markers = {"manifest_hash": expected.manifest_hash,
                   "pages_id": expected.pages_production_id,
                   "worker_id": expected.worker_version_id,
                   "editor_map_id": expected.editor_map_id,
                   "build_id": expected.build_id,
                   "generated_contract_hashes": expected.generated_contract_hashes,
                   "writes_enabled": True}
        response = {"status": 200, "headers": {"Cache-Control": "private, no-store"},
                    "markers": markers}
        verifier = daemon.LiveReleaseVerifier(Provider(), lambda _manifest: response)
        self.assertTrue(verifier.verify(expected))
        for field in ("pages_id", "worker_id", "editor_map_id", "build_id",
                      "generated_contract_hashes"):
            broken = {**response, "markers": {**markers, field: "wrong"}}
            self.assertFalse(daemon.LiveReleaseVerifier(
                Provider(), lambda _manifest, value=broken: value).verify(expected))
        self.assertFalse(daemon.LiveReleaseVerifier(
            Provider("pages-wrong"), lambda _manifest: response).verify(expected))
        self.assertFalse(daemon.LiveReleaseVerifier(
            Provider(), lambda _manifest: {**response, "headers": {}}).verify(expected))

    def test_known_good_requires_complete_restorable_exact_main_identity(self):
        good = daemon.KnownGoodAttestation(
            pages_id="p", worker_id="w", commit_sha="a" * 40,
            manifest_hash="m", editor_map_id="e", build_id="b",
            generated_contract_hashes={"x": "c" * 64},
            pages_restorable=True, worker_restorable=True)
        self.assertIs(good, good.validate())
        with self.assertRaisesRegex(daemon.VerificationFailed, "not_restorable"):
            daemon.dataclasses.replace(good, worker_restorable=False).validate()

    def test_exclusive_writer_inventory_is_default_deny(self):
        self.assertFalse(daemon.inventory_exclusive_writers([])["ok"])
        receipt = daemon.inventory_exclusive_writers([
            {"id": "promotion", "kind": "coordinator", "enabled": True, "intended": True},
            {"id": "pages-auto", "kind": "pages-branch-build", "enabled": True, "intended": False},
        ])
        self.assertFalse(receipt["ok"])
        self.assertEqual(receipt["conflicts"], ["pages-auto"])

    def test_four_and_five_minute_health_excludes_human_wait(self):
        self.assertTrue(daemon.timing_health(0, 241)["pre_breach"])
        self.assertTrue(daemon.timing_health(0, 301)["breach"])
        held = daemon.timing_health(0, 500, awaiting_approval_since=100)
        self.assertEqual(held["active_seconds"], 100)
        self.assertEqual(held["awaiting_approval_seconds"], 400)
        self.assertFalse(held["pre_breach"])

    def test_alerts_are_content_light_and_acknowledged(self):
        alert = daemon.content_light_alert("candidate-id", "restore_failed",
                                           manifest_hash="manifest-id")
        self.assertEqual(set(alert), {"candidate_id", "state", "manifest_hash",
                                      "acknowledgement_required"})
        self.assertTrue(alert["acknowledgement_required"])


if __name__ == "__main__":
    unittest.main()
