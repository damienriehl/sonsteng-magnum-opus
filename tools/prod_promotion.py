#!/usr/bin/env python3
"""Pure PROD promotion risk policy and evidence-bound advisory AI boundary.

This module deliberately has no lifecycle, network, branch, approval, or deploy
authority. Callers inject an AI provider and persist the returned records.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import sys
from types import MappingProxyType

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from editorial_pass import parse_strict_json_object  # noqa: E402

POLICY_VERSION = "prod-risk-v1"
AI_RESPONSE_SCHEMA_VERSION = "prod-ai-review-v1"
MAX_AI_ENVELOPE_BYTES = 8192
MAX_AI_RESPONSE_BYTES = 16384
MAX_AI_REASONS = 5
MAX_REASON_CHARS = 240
MAX_PROVIDER_RETENTION_DAYS = 30
MAX_POLICY_INPUT_BYTES = 32768

HARD_GATE_NAMES = (
    "base", "candidate_nonempty", "editor_map", "drift", "path_and_format",
    "group_atomicity", "group_atomic_patch", "validate_spine", "build",
    "parity", "immutable_ref", "resource_compatibility",
)

# Values are risks: zero is safest, one is riskiest. Weights sum to one.
SIGNAL_WEIGHTS = {
    "scope": .20,
    "affected_surfaces": .15,
    "change_size": .10,
    "drift_history": .20,
    "conflict_history": .15,
    "validation_coverage": .20,
}

AI_OUTBOUND_FIELDS = (
    "schema_version", "policy_version", "evidence_hash", "deterministic_score",
    "deterministic_disposition", "signals", "signal_contributions",
    "failure_codes", "candidate_id", "attempt_id",
)


@dataclasses.dataclass(frozen=True)
class RiskPolicy:
    version: str = POLICY_VERSION
    signal_weights: dict = dataclasses.field(default_factory=lambda: dict(SIGNAL_WEIGHTS))
    automatic_threshold: float = 85.0
    approval_threshold: float = 60.0
    ai_downward_cap: float = 10.0
    ai_upward_cap: float = 0.0

    def __post_init__(self):
        weights = dict(self.signal_weights)
        if set(weights) != set(SIGNAL_WEIGHTS):
            raise ValueError("policy_signal_set")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) or value < 0 for value in weights.values()):
            raise ValueError("policy_weight")
        if not math.isclose(sum(weights.values()), 1.0):
            raise ValueError("policy_weight_total")
        if not 0 <= self.approval_threshold <= self.automatic_threshold <= 100:
            raise ValueError("policy_threshold")
        if not 0 <= self.ai_downward_cap <= 10 or not 0 <= self.ai_upward_cap <= 10:
            raise ValueError("policy_ai_cap")
        object.__setattr__(self, "signal_weights", MappingProxyType(weights))

    def with_ai_upward_cap(self, cap):
        return dataclasses.replace(self, ai_upward_cap=max(0.0, min(10.0, float(cap))))


DEFAULT_POLICY = RiskPolicy()


@dataclasses.dataclass(frozen=True)
class RiskResult:
    eligible: bool
    policy_version: str
    hard_gates: dict
    failure_codes: tuple
    signals: dict
    signal_contributions: dict
    score: float
    disposition: str
    evidence_hash: str


@dataclasses.dataclass(frozen=True)
class AdjustedRisk:
    eligible: bool
    policy_version: str
    evidence_hash: str
    deterministic_score: float
    adjustment: float
    score: float
    disposition: str


@dataclasses.dataclass(frozen=True)
class AIProviderConfig:
    model: str
    prompt_version: str
    training_allowed: bool
    retention_days: int | None
    timeout_seconds: int = 30


@dataclasses.dataclass(frozen=True)
class AIReview:
    status: str
    adjustment: float
    recommendation: str
    reasons: tuple
    uncertainty: float | None
    evidence_hash: str
    model: str
    prompt_version: str
    failure_code: str = ""


@dataclasses.dataclass(frozen=True)
class LaunchReadiness:
    ready: bool
    failed_criteria: tuple


def _canonical_hash(value):
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


def _disposition(score, policy):
    if score >= policy.automatic_threshold:
        return "automatic"
    if score >= policy.approval_threshold:
        return "awaiting_approval"
    return "low_confidence"


def evaluate_risk(inputs, policy=DEFAULT_POLICY):
    """Evaluate table-driven hard gates, then deterministic bounded confidence."""
    try:
        encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_POLICY_INPUT_BYTES:
            raise ValueError("risk_input_size")
    except (TypeError, ValueError):
        inputs = {}
    gates_in = inputs.get("hard_gates", {}) if isinstance(inputs, dict) else {}
    signals_in = inputs.get("signals", {}) if isinstance(inputs, dict) else {}
    gates = {name: gates_in.get(name) is True for name in HARD_GATE_NAMES}
    failures = ["hard_gate.%s" % name for name, passed in gates.items() if not passed]
    signals = {}
    invalid = []
    for name in policy.signal_weights:
        value = signals_in.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            invalid.append(name)
            signals[name] = 1.0
        else:
            signals[name] = max(0.0, min(1.0, float(value)))
    failures.extend("risk_input.%s" % name for name in invalid)
    contributions = {
        name: round(signals[name] * float(weight) * 100.0, 6)
        for name, weight in policy.signal_weights.items()
    }
    score = round(max(0.0, min(100.0, 100.0 - sum(contributions.values()))), 6)
    eligible = not failures
    disposition = _disposition(score, policy) if eligible else "blocked"
    evidence = {
        "policy_version": policy.version, "hard_gates": gates,
        "failure_codes": failures, "signals": signals,
        "signal_contributions": contributions, "score": score,
        "disposition": disposition,
    }
    return RiskResult(eligible, policy.version, gates, tuple(failures), signals,
                      contributions, score, disposition, _canonical_hash(evidence))


def risk_inputs_from_preparation(evidence, metrics):
    """Project immutable builder evidence plus bounded history into policy inputs.

    ``metrics`` is computed by the coordinator from its ledger; no content or
    filenames enter this projection. Values are already normalized risk values.
    """
    gate_records = evidence.get("hard_gates", []) if isinstance(evidence, dict) else []
    observed = {
        row.get("name"): row.get("status") == "pass"
        for row in gate_records if isinstance(row, dict)
    }
    gates = {name: observed.get(name, False) for name in HARD_GATE_NAMES}
    # Resource compatibility is a coordinator preflight and defaults fail-closed.
    if "resource_compatibility" in metrics:
        gates["resource_compatibility"] = metrics["resource_compatibility"] is True
    return {"hard_gates": gates,
            "signals": {name: metrics.get(name) for name in SIGNAL_WEIGHTS}}


def apply_ai_adjustment(result, requested_adjustment, policy=DEFAULT_POLICY):
    """Clamp advisory adjustment; a hard failure always remains blocked."""
    adjustment = 0.0
    if result.eligible:
        try:
            value = float(requested_adjustment)
            if math.isfinite(value):
                adjustment = max(-policy.ai_downward_cap,
                                 min(policy.ai_upward_cap, value))
        except (TypeError, ValueError, OverflowError):
            pass
    score = round(max(0.0, min(100.0, result.score + adjustment)), 6)
    disposition = _disposition(score, policy) if result.eligible else "blocked"
    return AdjustedRisk(result.eligible, policy.version, result.evidence_hash,
                        result.score, adjustment, score, disposition)


def _hold(result, config, code, status="hold"):
    return AIReview(status, 0.0, "hold", (code,), None, result.evidence_hash,
                    config.model, config.prompt_version, code)


def _provider_policy_valid(config):
    safe_id = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
    return (config.training_allowed is False
            and isinstance(config.retention_days, int)
            and not isinstance(config.retention_days, bool)
            and 0 <= config.retention_days <= MAX_PROVIDER_RETENTION_DAYS
            and bool(safe_id.fullmatch(config.model))
            and bool(safe_id.fullmatch(config.prompt_version))
            and isinstance(config.timeout_seconds, int)
            and 1 <= config.timeout_seconds <= 60)


def _safe_reason(value):
    if not isinstance(value, str) or not value or len(value) > MAX_REASON_CHARS:
        return False
    # Reasons are evidence labels, never renderable content or instructions.
    return (value.isprintable()
            and not re.search(r"[<>]|https?://|javascript:|on\w+\s*=|\b(?:publish|approve|deploy|secret|token)\b",
                              value, re.IGNORECASE))


def _opaque_id(value):
    value = str(value)
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else ""


def build_ai_envelope(result, context):
    candidate_id = context.get("candidate_id", "") if isinstance(context, dict) else ""
    attempt_id = context.get("attempt_id", "") if isinstance(context, dict) else ""
    envelope = {
        "schema_version": AI_RESPONSE_SCHEMA_VERSION,
        "policy_version": result.policy_version,
        "evidence_hash": result.evidence_hash,
        "deterministic_score": result.score,
        "deterministic_disposition": result.disposition,
        "signals": result.signals,
        "signal_contributions": result.signal_contributions,
        "failure_codes": list(result.failure_codes),
        "candidate_id": _opaque_id(candidate_id),
        "attempt_id": _opaque_id(attempt_id),
    }
    wire = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    if len(wire) > MAX_AI_ENVELOPE_BYTES:
        raise ValueError("ai_envelope_size")
    return envelope


def review_with_ai(result, context, provider, config, policy=DEFAULT_POLICY):
    """Call a read-only provider and validate a strictly evidence-bound response."""
    if not result.eligible:
        return _hold(result, config, "hard_failure", "skipped_hard_failure")
    if not _provider_policy_valid(config):
        return _hold(result, config, "provider_policy")
    try:
        envelope = build_ai_envelope(result, context)
        raw = provider(envelope, config.timeout_seconds)
        value = parse_strict_json_object(raw, max_bytes=MAX_AI_RESPONSE_BYTES, max_depth=4)
        required = {"schema_version", "evidence_hash", "model", "prompt_version",
                    "recommendation", "adjustment", "reasons", "uncertainty"}
        if set(value) != required:
            raise ValueError("response_fields")
        if value["schema_version"] != AI_RESPONSE_SCHEMA_VERSION:
            raise ValueError("schema_binding")
        if value["evidence_hash"] != result.evidence_hash:
            raise ValueError("evidence_binding")
        if value["model"] != config.model or value["prompt_version"] != config.prompt_version:
            raise ValueError("reviewer_binding")
        if value["recommendation"] not in ("raise", "hold", "lower"):
            raise ValueError("recommendation")
        reasons = value["reasons"]
        if (not isinstance(reasons, list) or not reasons
                or len(reasons) > MAX_AI_REASONS or not all(_safe_reason(x) for x in reasons)):
            raise ValueError("reasons")
        uncertainty = value["uncertainty"]
        if (not isinstance(uncertainty, (int, float)) or isinstance(uncertainty, bool)
                or not math.isfinite(uncertainty) or not 0 <= uncertainty <= 1):
            raise ValueError("uncertainty")
        adjustment = value["adjustment"]
        if not isinstance(adjustment, (int, float)) or isinstance(adjustment, bool):
            raise ValueError("adjustment")
        if ((value["recommendation"] == "raise" and adjustment < 0)
                or (value["recommendation"] == "lower" and adjustment > 0)
                or (value["recommendation"] == "hold" and adjustment != 0)):
            raise ValueError("adjustment_direction")
        bounded = apply_ai_adjustment(result, adjustment, policy)
        return AIReview("accepted", bounded.adjustment, value["recommendation"],
                        tuple(reasons), float(uncertainty), result.evidence_hash,
                        config.model, config.prompt_version)
    except Exception:  # noqa: BLE001 - provider is an untrusted availability boundary
        # Provider errors are intentionally not persisted: they may contain secrets.
        return _hold(result, config, "review_unavailable")


def ai_upward_launch_ready(metrics):
    """KTD13's measured, non-adaptive authority unlock contract."""
    checks = {
        "reviewed_candidates": metrics.get("reviewed_candidates", 0) >= 50,
        "observation_days": metrics.get("observation_days", 0) >= 14,
        "admin_agreement": metrics.get("admin_agreement", 0) >= .90,
        "hard_gate_escapes": metrics.get("hard_gate_escapes") == 0,
        "false_automatic_promotions": metrics.get("false_automatic_promotions") == 0,
        "restart_drill_passed": metrics.get("restart_drill_passed") is True,
        "restoration_drill_passed": metrics.get("restoration_drill_passed") is True,
        "automatic_within_five_minutes": metrics.get("automatic_within_five_minutes", 0) >= .95,
        "ai_unavailable_samples_handled": metrics.get("ai_unavailable_samples_handled") is True,
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return LaunchReadiness(not failed, failed)
