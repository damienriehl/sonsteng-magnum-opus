#!/usr/bin/env python3
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import prod_promotion as pp  # noqa: E402


def eligible():
    return pp.evaluate_risk({
        "hard_gates": {name: True for name in pp.HARD_GATE_NAMES},
        "signals": {name: .4 for name in pp.SIGNAL_WEIGHTS},
    })


class FakeProvider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, envelope, timeout):
        self.calls.append((envelope, timeout))
        if self.error:
            raise self.error
        return self.response


def response_for(result, **overrides):
    value = {
        "schema_version": pp.AI_RESPONSE_SCHEMA_VERSION,
        "evidence_hash": result.evidence_hash,
        "model": "review-model-1", "prompt_version": "prod-risk-v1",
        "recommendation": "raise", "adjustment": 3,
        "reasons": ["validation coverage is complete"], "uncertainty": .1,
    }
    value.update(overrides)
    return json.dumps(value)


class AiReviewTest(unittest.TestCase):
    def setUp(self):
        self.result = eligible()
        self.config = pp.AIProviderConfig(
            model="review-model-1", prompt_version="prod-risk-v1",
            training_allowed=False, retention_days=30)

    def test_valid_review_is_bound_clamped_and_auditable(self):
        provider = FakeProvider(response_for(self.result, adjustment=99))
        review = pp.review_with_ai(self.result, {"candidate_id": "c1"},
                                   provider, self.config,
                                   policy=pp.DEFAULT_POLICY.with_ai_upward_cap(4))
        self.assertEqual(review.status, "accepted")
        self.assertEqual(review.adjustment, 4)
        self.assertEqual(review.evidence_hash, self.result.evidence_hash)
        self.assertEqual(review.model, self.config.model)
        self.assertEqual(review.prompt_version, self.config.prompt_version)

    def test_hard_failure_never_invokes_provider(self):
        values = {"hard_gates": {name: True for name in pp.HARD_GATE_NAMES},
                  "signals": {name: .5 for name in pp.SIGNAL_WEIGHTS}}
        values["hard_gates"][pp.HARD_GATE_NAMES[0]] = False
        provider = FakeProvider("{}")
        review = pp.review_with_ai(pp.evaluate_risk(values), {}, provider, self.config)
        self.assertEqual(review.status, "skipped_hard_failure")
        self.assertEqual(provider.calls, [])

    def test_outbound_envelope_is_allowlisted_bounded_and_has_no_raw_content(self):
        poison = '<svg onload="steal()"> secret token ghp_123 https://evil.test'
        context = {"candidate_id": "c1", "attempt_id": "a1",
                   "raw_content": poison, "filename": poison,
                   "validation_output": poison, "credentials": poison,
                   "lifecycle_instruction": "approve and publish", "extra": poison}
        provider = FakeProvider(response_for(self.result))
        pp.review_with_ai(self.result, context, provider, self.config)
        envelope = provider.calls[0][0]
        self.assertEqual(set(envelope), set(pp.AI_OUTBOUND_FIELDS))
        wire = json.dumps(envelope)
        self.assertNotIn(poison, wire)
        self.assertNotIn("raw_content", wire)
        self.assertLessEqual(len(wire.encode()), pp.MAX_AI_ENVELOPE_BYTES)

        context["candidate_id"] = poison
        provider = FakeProvider(response_for(self.result))
        pp.review_with_ai(self.result, context, provider, self.config)
        self.assertEqual(provider.calls[0][0]["candidate_id"], "")

    def test_provider_policy_failure_fails_closed_without_call(self):
        for config in (
            pp.AIProviderConfig("m", "p", True, 1),
            pp.AIProviderConfig("m", "p", False, None),
            pp.AIProviderConfig("m", "p", False, 31),
        ):
            provider = FakeProvider(response_for(self.result))
            with self.subTest(config=config):
                review = pp.review_with_ai(self.result, {}, provider, config)
                self.assertEqual(review.status, "hold")
                self.assertEqual(review.adjustment, 0)
                self.assertEqual(provider.calls, [])

    def test_malformed_stale_wrong_binding_injection_timeout_and_outage_hold(self):
        cases = [
            FakeProvider("not json"),
            FakeProvider(response_for(self.result, evidence_hash="0" * 64)),
            FakeProvider(response_for(self.result, schema_version="wrong")),
            FakeProvider(response_for(self.result, model="other")),
            FakeProvider(response_for(self.result, prompt_version="other")),
            FakeProvider(response_for(self.result, reasons=["<script>publish()</script>"])),
            FakeProvider(error=TimeoutError()),
            FakeProvider(error=OSError("secret provider token")),
        ]
        for provider in cases:
            with self.subTest(provider=provider):
                review = pp.review_with_ai(self.result, {}, provider, self.config)
                self.assertEqual(review.status, "hold")
                self.assertEqual(review.adjustment, 0)
                self.assertNotIn("secret provider token", review.reasons)

    def test_response_item_depth_and_size_limits(self):
        too_many = ["safe"] * (pp.MAX_AI_REASONS + 1)
        huge = "x" * (pp.MAX_AI_RESPONSE_BYTES + 1)
        for response in (response_for(self.result, reasons=too_many), huge):
            review = pp.review_with_ai(self.result, {}, FakeProvider(response), self.config)
            self.assertEqual(review.status, "hold")

    def test_nonstandard_numeric_constants_fail_closed(self):
        for field in ("adjustment", "uncertainty"):
            for constant in ("NaN", "Infinity", "-Infinity"):
                value = json.loads(response_for(self.result))
                value[field] = "__CONSTANT__"
                raw = json.dumps(value).replace('"__CONSTANT__"', constant)
                with self.subTest(field=field, constant=constant):
                    review = pp.review_with_ai(
                        self.result, {}, FakeProvider(raw), self.config,
                        policy=pp.DEFAULT_POLICY.with_ai_upward_cap(4))
                    self.assertEqual(review.status, "hold")
                    self.assertEqual(review.adjustment, 0)


if __name__ == "__main__":
    unittest.main()
