#!/usr/bin/env python3
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import prod_promotion as pp  # noqa: E402


def good_inputs():
    return {
        "hard_gates": {name: True for name in pp.HARD_GATE_NAMES},
        "signals": {
            "scope": 0.1,
            "affected_surfaces": 0.1,
            "change_size": 0.1,
            "drift_history": 0.0,
            "conflict_history": 0.0,
            "validation_coverage": 1.0,
        },
    }


class RiskPolicyTest(unittest.TestCase):
    def test_each_hard_gate_has_catch_power(self):
        baseline = pp.evaluate_risk(good_inputs())
        self.assertTrue(baseline.eligible)
        for name in pp.HARD_GATE_NAMES:
            inputs = good_inputs()
            inputs["hard_gates"][name] = False
            with self.subTest(name=name):
                result = pp.evaluate_risk(inputs)
                self.assertFalse(result.eligible)
                self.assertEqual(result.disposition, "blocked")
                self.assertIn("hard_gate.%s" % name, result.failure_codes)

    def test_signals_are_bounded_and_total_score_is_bounded(self):
        inputs = good_inputs()
        inputs["signals"] = {name: -100 for name in pp.SIGNAL_WEIGHTS}
        safest = pp.evaluate_risk(inputs)
        inputs["signals"] = {name: 100 for name in pp.SIGNAL_WEIGHTS}
        riskiest = pp.evaluate_risk(inputs)
        self.assertEqual(safest.score, 100.0)
        self.assertEqual(riskiest.score, 0.0)
        self.assertTrue(all(value == 0 for value in safest.signals.values()))
        self.assertTrue(all(value == 1 for value in riskiest.signals.values()))

    def test_replay_is_deterministic_and_versioned(self):
        one = pp.evaluate_risk(good_inputs())
        two = pp.evaluate_risk(copy.deepcopy(good_inputs()))
        self.assertEqual(one, two)
        self.assertEqual(one.policy_version, pp.DEFAULT_POLICY.version)
        self.assertEqual(len(one.evidence_hash), 64)
        self.assertEqual(one.signal_contributions, two.signal_contributions)
        with self.assertRaises(TypeError):
            pp.DEFAULT_POLICY.signal_weights["scope"] = 1

    def test_missing_or_nonfinite_signal_fails_closed(self):
        inputs = good_inputs()
        del inputs["signals"]["scope"]
        self.assertEqual(pp.evaluate_risk(inputs).disposition, "blocked")

    def test_preparation_projection_uses_named_gates_and_no_content(self):
        evidence = {"hard_gates": [
            {"name": name, "status": "pass", "detail": "sensitive content"}
            for name in pp.HARD_GATE_NAMES if name != "resource_compatibility"
        ], "changed_paths": ["private/client-name.md"]}
        metrics = {name: .2 for name in pp.SIGNAL_WEIGHTS}
        metrics["resource_compatibility"] = True
        projected = pp.risk_inputs_from_preparation(evidence, metrics)
        self.assertTrue(all(projected["hard_gates"].values()))
        self.assertNotIn("sensitive content", repr(projected))
        self.assertNotIn("client-name", repr(projected))
        inputs = good_inputs()
        inputs["signals"]["scope"] = float("nan")
        self.assertEqual(pp.evaluate_risk(inputs).disposition, "blocked")

    def test_upward_authority_defaults_off_and_is_independent(self):
        self.assertEqual(pp.DEFAULT_POLICY.ai_upward_cap, 0)
        base = pp.evaluate_risk(good_inputs())
        held = pp.apply_ai_adjustment(base, 50)
        self.assertEqual(held.adjustment, 0)
        enabled = pp.DEFAULT_POLICY.with_ai_upward_cap(4)
        raised = pp.apply_ai_adjustment(base, 50, policy=enabled)
        self.assertEqual(raised.adjustment, 4)
        self.assertLessEqual(raised.score, 100)
        disabled_again = pp.apply_ai_adjustment(base, 50)
        self.assertEqual(disabled_again.disposition, base.disposition)

    def test_ai_cannot_rescue_hard_failure(self):
        inputs = good_inputs()
        inputs["hard_gates"][pp.HARD_GATE_NAMES[0]] = False
        blocked = pp.evaluate_risk(inputs)
        reviewed = pp.apply_ai_adjustment(
            blocked, 100, policy=pp.DEFAULT_POLICY.with_ai_upward_cap(10))
        self.assertEqual(reviewed.disposition, "blocked")
        self.assertEqual(reviewed.adjustment, 0)

    def test_measured_unlock_contract_is_explicit_and_all_conditions_required(self):
        passing = {
            "reviewed_candidates": 50, "observation_days": 14,
            "admin_agreement": .90, "hard_gate_escapes": 0,
            "false_automatic_promotions": 0,
            "restart_drill_passed": True, "restoration_drill_passed": True,
            "automatic_within_five_minutes": .95,
            "ai_unavailable_samples_handled": True,
        }
        self.assertTrue(pp.ai_upward_launch_ready(passing).ready)
        for key in passing:
            failed = dict(passing)
            failed[key] = False if isinstance(passing[key], bool) else -1
            with self.subTest(key=key):
                self.assertFalse(pp.ai_upward_launch_ready(failed).ready)


if __name__ == "__main__":
    unittest.main()
