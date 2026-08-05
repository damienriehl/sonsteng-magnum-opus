#!/usr/bin/env python3
import dataclasses
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import prod_promotion as pp  # noqa: E402


def good_inputs():
    return {
        "hard_gates": {name: True for name in pp.HARD_GATE_NAMES},
        "signals": {name: 0.05 for name in pp.SIGNAL_WEIGHTS},
    }


def passing_metrics():
    return {
        "reviewed_candidates": 50,
        "observation_days": 14,
        "admin_agreement": .90,
        "hard_gate_escapes": 0,
        "false_automatic_promotions": 0,
        "restart_drill_passed": True,
        "restoration_drill_passed": True,
        "automatic_within_five_minutes": .95,
        "ai_unavailable_samples_handled": True,
    }


def all_prerequisites():
    return pp.RolloutPrerequisites(**{
        field.name: True for field in dataclasses.fields(pp.RolloutPrerequisites)
    })


class ProdPromotionRolloutTest(unittest.TestCase):
    def test_shadow_replay_is_deterministic_and_has_zero_mutation_authority(self):
        config = pp.AIProviderConfig("reviewer-v1", "prompt-v1", False, 0)

        def provider(envelope, _timeout):
            return json.dumps({
                "schema_version": pp.AI_RESPONSE_SCHEMA_VERSION,
                "evidence_hash": envelope["evidence_hash"],
                "model": config.model,
                "prompt_version": config.prompt_version,
                "recommendation": "raise",
                "adjustment": 8,
                "reasons": ["bounded_signal_review"],
                "uncertainty": .1,
            })

        samples = [{"candidate_id": "history-1", "attempt_id": "attempt-1",
                    "risk_inputs": good_inputs(), "admin_agreed": True}]
        one = pp.replay_shadow_candidates(samples, provider, config)
        two = pp.replay_shadow_candidates(samples, provider, config)
        self.assertEqual(one, two)
        self.assertFalse(one[0].mutation_authority)
        self.assertEqual(one[0].ai_adjustment, 0)  # upward authority is off
        self.assertNotIn("risk_inputs", dataclasses.asdict(one[0]))

    def test_launch_threshold_table_is_exact_and_default_deny(self):
        thresholds = pp.DEFAULT_ROLLOUT_THRESHOLDS
        self.assertEqual(thresholds.reviewed_candidates, 50)
        self.assertEqual(thresholds.observation_days, 14)
        self.assertEqual(thresholds.admin_agreement, .90)
        self.assertEqual(thresholds.hard_gate_escapes, 0)
        self.assertEqual(thresholds.false_automatic_promotions, 0)
        self.assertEqual(thresholds.automatic_within_five_minutes, .95)
        self.assertFalse(pp.ai_upward_launch_ready({}).ready)
        self.assertTrue(pp.ai_upward_launch_ready(passing_metrics()).ready)
        for key in passing_metrics():
            values = passing_metrics()
            values.pop(key)
            with self.subTest(key=key):
                self.assertFalse(pp.ai_upward_launch_ready(values).ready)

    def test_deterministic_phase_requires_supervised_canary_and_rollback(self):
        prereqs = dataclasses.replace(
            all_prerequisites(), supervised_canary_passed=False,
            rollback_drill_passed=False)
        receipt = pp.evaluate_rollout_transition(
            "supervised_canary", "deterministic_only", prereqs, {}, "operator-1",
            "proof incomplete", "2026-08-01", "2026-08-05")
        self.assertEqual(receipt.decision, "no_go")
        self.assertIn("supervised_canary_passed", receipt.failed_criteria)
        self.assertIn("rollback_drill_passed", receipt.failed_criteria)

    def test_every_enabling_transition_requires_operational_proof(self):
        receipt = pp.evaluate_rollout_transition(
            "disabled", "shadow", pp.RolloutPrerequisites(), {}, "operator-1",
            "pending evidence", "2026-08-01", "2026-08-05")
        self.assertEqual(receipt.decision, "no_go")
        for name in ("restorable_baseline", "queue_accounted", "prod_healthy",
                     "dev_healthy", "no_drift", "pause_switch_tested",
                     "kill_switch_tested", "operator_assigned"):
            self.assertIn(name, receipt.failed_criteria)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            receipt.decision = "go"

    def test_deterministic_only_has_no_ai_upward_authority(self):
        receipt = pp.evaluate_rollout_transition(
            "supervised_canary", "deterministic_only", all_prerequisites(), {},
            "operator-1", "canary and rollback proven", "2026-08-01", "2026-08-05")
        self.assertEqual(receipt.decision, "go")
        authority = pp.authority_for_next_evaluation(
            "deterministic_only", receipt=receipt,
            window_start="2026-08-01", window_end="2026-08-05")
        self.assertTrue(authority["deterministic_promotion"])
        self.assertFalse(authority["ai_upward"])
        self.assertEqual(authority["risk_policy"].ai_upward_cap, 0)

    def test_ai_phase_requires_all_metrics_and_current_policy_binding(self):
        thresholds = pp.RolloutThresholds(version="prod-rollout-v2")
        policy = pp.RolloutPolicy(
            version="prod-rollout-v2", thresholds=thresholds,
            ai_upward_cap=4, ai_model="reviewer-v2", ai_prompt_version="prompt-v2")
        receipt = pp.evaluate_rollout_transition(
            "deterministic_only", "ai_upward", all_prerequisites(), passing_metrics(),
            "operator-1", "measured contract passed", "2026-07-20", "2026-08-05",
            policy=policy)
        self.assertEqual(receipt.decision, "go")
        self.assertTrue(pp.receipt_matches_evaluation(
            receipt, policy, "2026-07-20", "2026-08-05"))
        changed = dataclasses.replace(policy, ai_upward_cap=5)
        self.assertNotEqual(policy.policy_id, changed.policy_id)
        self.assertFalse(pp.receipt_matches_evaluation(
            receipt, changed, "2026-07-20", "2026-08-05"))
        self.assertFalse(pp.receipt_matches_evaluation(
            receipt, policy, "2026-07-21", "2026-08-05"))

        enabled_risk = pp.DEFAULT_POLICY.with_ai_upward_cap(4)
        authority = pp.authority_for_next_evaluation(
            "ai_upward", enabled_risk, receipt=receipt, rollout_policy=policy,
            window_start="2026-07-20", window_end="2026-08-05")
        self.assertTrue(authority["ai_upward"])

    def test_ai_kill_switch_is_independent_and_applies_next_evaluation(self):
        enabled_policy = pp.DEFAULT_POLICY.with_ai_upward_cap(4)
        rollout_thresholds = pp.RolloutThresholds(version="prod-rollout-v2")
        rollout_policy = pp.RolloutPolicy(
            version="prod-rollout-v2", thresholds=rollout_thresholds,
            ai_upward_cap=4, ai_model="reviewer-v2", ai_prompt_version="prompt-v2")
        receipt = pp.evaluate_rollout_transition(
            "deterministic_only", "ai_upward", all_prerequisites(), passing_metrics(),
            "operator-1", "measured contract passed", "2026-07-20", "2026-08-05",
            policy=rollout_policy)
        before = pp.authority_for_next_evaluation(
            "ai_upward", enabled_policy, ai_kill_switch=False, receipt=receipt,
            rollout_policy=rollout_policy, window_start="2026-07-20",
            window_end="2026-08-05")
        after = pp.authority_for_next_evaluation(
            "ai_upward", enabled_policy, ai_kill_switch=True, receipt=receipt,
            rollout_policy=rollout_policy, window_start="2026-07-20",
            window_end="2026-08-05")
        self.assertTrue(before["ai_upward"])
        self.assertFalse(after["ai_upward"])
        self.assertEqual(after["risk_policy"].ai_upward_cap, 0)
        self.assertTrue(after["deterministic_promotion"])

    def test_no_receipt_means_no_authority_even_if_phase_string_is_enabled(self):
        enabled_policy = pp.DEFAULT_POLICY.with_ai_upward_cap(4)
        authority = pp.authority_for_next_evaluation("ai_upward", enabled_policy)
        self.assertFalse(authority["deterministic_promotion"])
        self.assertFalse(authority["ai_upward"])

    def test_emergency_pause_is_always_available(self):
        receipt = pp.evaluate_rollout_transition(
            "ai_upward", "disabled", pp.RolloutPrerequisites(), {}, "operator-1",
            "emergency pause", "2026-08-05", "2026-08-05")
        self.assertEqual(receipt.decision, "go")


if __name__ == "__main__":
    unittest.main()
