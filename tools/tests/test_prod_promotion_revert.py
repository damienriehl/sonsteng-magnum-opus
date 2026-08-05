#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import prod_promotion_daemon as pd  # noqa: E402
from test_prod_promotion_daemon import coordinator  # noqa: E402


class RestoreTest(unittest.TestCase):
    def test_ambiguous_restore_is_durable_restore_failed_and_fenced(self):
        c, ledger, provider, git, live = coordinator(); git.conflict = True
        live.verify_known_good = lambda known, writes_expected: False
        self.assertEqual(c.run_once(), "restore_failed")
        self.assertEqual(ledger.health, "restore_failed")
        self.assertEqual(ledger.final, None)

    def test_cross_environment_artifact_denied_before_activation(self):
        c, ledger, provider, git, live = coordinator()
        original = provider.upload_worker
        def wrong(*args, **kwargs):
            artifact = original(*args, **kwargs)
            provider.worker = pd.Artifact(artifact.id, artifact.digest, "dev", artifact.kind)
            return provider.worker
        provider.upload_worker = wrong
        self.assertEqual(c.run_once(), "restored")
        self.assertNotIn("activate-worker", provider.calls)


if __name__ == "__main__": unittest.main()
