#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_prod_promotion_daemon import coordinator  # noqa: E402


class ReconciliationTest(unittest.TestCase):
    def test_provider_timeout_is_discovered_not_repeated(self):
        c, ledger, provider, git, live = coordinator(); provider.timeout_once.add("preview")
        with self.assertRaises(TimeoutError): c.run_once()
        self.assertEqual(provider.calls.count("preview"), 1)
        ledger.released = False
        self.assertEqual(c.run_once(), "published")
        self.assertEqual(provider.calls.count("preview"), 1)

    def test_out_of_band_live_drift_pauses_before_claim(self):
        c, ledger, provider, git, live = coordinator(); provider.live_pair = ("alien", "old-worker")
        self.assertEqual(c.run_once(), "paused_drift")
        self.assertEqual(provider.calls, []); self.assertEqual(ledger.health, "stalled")

    def test_out_of_band_main_drift_pauses_before_claim(self):
        c, ledger, provider, git, live = coordinator(); git.main = "f" * 40
        self.assertEqual(c.run_once(), "paused_drift")
        self.assertEqual(provider.calls, [])


if __name__ == "__main__": unittest.main()
