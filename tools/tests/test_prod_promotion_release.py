#!/usr/bin/env python3
import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import prod_promotion as pp  # noqa: E402


class ReleaseManifestTest(unittest.TestCase):
    def test_manifest_is_canonical_complete_and_immutable(self):
        value = pp.ReleaseManifest(
            candidate_id="cand-1", attempt_id="cand-1:1", environment="production",
            base_sha="a" * 40, commit_sha="b" * 40,
            candidate_ref="refs/heads/releases/cand-1",
            pages_preview_id="preview-1", pages_production_id="prod-2",
            worker_version_id="worker-1", editor_map_id="map-1",
            build_id="build-1", generated_contract_hashes={"spine": "c" * 64},
        )
        self.assertNotEqual(value.pages_preview_id, value.pages_production_id)
        self.assertEqual(value.manifest_hash, pp.ReleaseManifest.from_dict(value.to_dict()).manifest_hash)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.commit_sha = "d" * 40

    def test_cross_environment_or_missing_identity_fails_closed(self):
        fields = dict(candidate_id="c", attempt_id="a", environment="dev",
                      base_sha="a" * 40, commit_sha="b" * 40,
                      candidate_ref="refs/heads/releases/c", pages_preview_id="p",
                      pages_production_id="q", worker_version_id="w",
                      editor_map_id="m", build_id="b",
                      generated_contract_hashes={"x": "c" * 64})
        with self.assertRaisesRegex(ValueError, "environment"):
            pp.ReleaseManifest(**fields)
        fields["environment"] = "production"
        fields["pages_production_id"] = "p"
        with self.assertRaisesRegex(ValueError, "distinct"):
            pp.ReleaseManifest(**fields)


if __name__ == "__main__":
    unittest.main()
