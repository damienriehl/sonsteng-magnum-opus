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
RELEASE_MANIFEST_SCHEMA_VERSION = "prod-release-v1"
ROLLOUT_POLICY_VERSION = "prod-rollout-v1"

ROLLOUT_PHASES = (
    "disabled", "shadow", "supervised_canary", "deterministic_only", "ai_upward",
)

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


@dataclasses.dataclass(frozen=True)
class RolloutThresholds:
    """Immutable, versioned launch contract. Changes create a new evidence epoch."""
    version: str = ROLLOUT_POLICY_VERSION
    reviewed_candidates: int = 50
    observation_days: int = 14
    admin_agreement: float = .90
    hard_gate_escapes: int = 0
    false_automatic_promotions: int = 0
    restart_drill_required: bool = True
    restoration_drill_required: bool = True
    automatic_within_five_minutes: float = .95
    all_ai_unavailable_handled: bool = True

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.version or ""):
            raise ValueError("rollout_policy_version")
        if (not isinstance(self.reviewed_candidates, int)
                or isinstance(self.reviewed_candidates, bool)
                or not isinstance(self.observation_days, int)
                or isinstance(self.observation_days, bool)):
            raise ValueError("rollout_sample_type")
        for value in (self.admin_agreement, self.automatic_within_five_minutes):
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value)):
                raise ValueError("rollout_ratio_type")
        for value in (self.hard_gate_escapes, self.false_automatic_promotions):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("rollout_incident_type")
        if self.reviewed_candidates < 50 or self.observation_days < 14:
            raise ValueError("rollout_minimum_sample")
        if not .90 <= self.admin_agreement <= 1:
            raise ValueError("rollout_admin_agreement")
        if self.hard_gate_escapes != 0 or self.false_automatic_promotions != 0:
            raise ValueError("rollout_zero_incidents")
        if not .95 <= self.automatic_within_five_minutes <= 1:
            raise ValueError("rollout_timing")
        if not all((self.restart_drill_required, self.restoration_drill_required,
                    self.all_ai_unavailable_handled)):
            raise ValueError("rollout_required_proof")

    @property
    def configuration_hash(self):
        return _canonical_hash(dataclasses.asdict(self))


DEFAULT_ROLLOUT_THRESHOLDS = RolloutThresholds()


@dataclasses.dataclass(frozen=True)
class RolloutPrerequisites:
    restorable_baseline: bool = False
    queue_accounted: bool = False
    prod_healthy: bool = False
    dev_healthy: bool = False
    no_drift: bool = False
    pause_switch_tested: bool = False
    kill_switch_tested: bool = False
    operator_assigned: bool = False
    supervised_canary_passed: bool = False
    rollback_drill_passed: bool = False


@dataclasses.dataclass(frozen=True)
class RolloutPolicy:
    """Binds thresholds and reviewer authority; no version is reusable after a change."""
    version: str = ROLLOUT_POLICY_VERSION
    thresholds: RolloutThresholds = dataclasses.field(default_factory=RolloutThresholds)
    risk_policy_version: str = POLICY_VERSION
    ai_upward_cap: float = 0.0
    ai_model: str = "disabled"
    ai_prompt_version: str = "disabled"

    def __post_init__(self):
        if self.version != self.thresholds.version:
            raise ValueError("rollout_version_binding")
        if (not isinstance(self.ai_upward_cap, (int, float))
                or isinstance(self.ai_upward_cap, bool)
                or not math.isfinite(self.ai_upward_cap)
                or not 0 <= self.ai_upward_cap <= 10):
            raise ValueError("rollout_ai_cap")
        for value in (self.risk_policy_version, self.ai_model, self.ai_prompt_version):
            if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", value or ""):
                raise ValueError("rollout_binding")

    @property
    def configuration_hash(self):
        value = dataclasses.asdict(self)
        return _canonical_hash(value)

    @property
    def policy_id(self):
        """Effective version changes for every threshold/cap/model/prompt change."""
        return "%s:%s" % (self.version, self.configuration_hash[:16])


DEFAULT_ROLLOUT_POLICY = RolloutPolicy()


@dataclasses.dataclass(frozen=True)
class RolloutReceipt:
    decision: str
    requested_phase: str
    current_phase: str
    actor: str
    rationale: str
    evidence_window_start: str
    evidence_window_end: str
    policy_version: str
    policy_hash: str
    risk_policy_version: str
    ai_model: str
    ai_prompt_version: str
    failed_criteria: tuple
    evidence_hash: str


@dataclasses.dataclass(frozen=True)
class ShadowReplayRecord:
    candidate_id: str
    policy_version: str
    evidence_hash: str
    deterministic_score: float
    deterministic_disposition: str
    ai_status: str
    ai_adjustment: float
    admin_agreed: bool | None
    mutation_authority: bool = False


@dataclasses.dataclass(frozen=True)
class ReleaseManifest:
    """Canonical, immutable identity of one coherent PROD release."""
    candidate_id: str
    attempt_id: str
    environment: str
    base_sha: str
    commit_sha: str
    candidate_ref: str
    pages_preview_id: str
    pages_production_id: str
    worker_version_id: str
    editor_map_id: str
    build_id: str
    generated_contract_hashes: dict
    schema_version: str = RELEASE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self):
        if self.environment != "production":
            raise ValueError("manifest_environment")
        text_fields = (self.candidate_id, self.attempt_id, self.candidate_ref,
                       self.pages_preview_id, self.pages_production_id,
                       self.worker_version_id, self.editor_map_id, self.build_id)
        if not all(isinstance(value, str) and value for value in text_fields):
            raise ValueError("manifest_identity")
        if self.pages_preview_id == self.pages_production_id:
            raise ValueError("pages_deployments_not_distinct")
        if not re.fullmatch(r"[0-9a-f]{40,64}", self.base_sha or "") or not re.fullmatch(r"[0-9a-f]{40,64}", self.commit_sha or ""):
            raise ValueError("manifest_git_sha")
        if not self.candidate_ref.startswith("refs/heads/releases/"):
            raise ValueError("manifest_candidate_ref")
        hashes = dict(self.generated_contract_hashes or {})
        if not hashes or any(not isinstance(k, str) or not k or
                             not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                             for k, v in hashes.items()):
            raise ValueError("manifest_contract_hash")
        object.__setattr__(self, "generated_contract_hashes", MappingProxyType(hashes))

    def to_dict(self):
        return {field.name: (dict(value) if field.name == "generated_contract_hashes" else value)
                for field in dataclasses.fields(self)
                for value in (getattr(self, field.name),)}

    @classmethod
    def from_dict(cls, value):
        return cls(**{field.name: value[field.name] for field in dataclasses.fields(cls)})

    @property
    def manifest_hash(self):
        return _canonical_hash(self.to_dict())


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


def _at_least(value, minimum):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= minimum)


def _exact_count(value, expected):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value == expected)


def ai_upward_launch_ready(metrics, thresholds=DEFAULT_ROLLOUT_THRESHOLDS):
    """KTD13's measured, non-adaptive authority unlock contract."""
    metrics = metrics if isinstance(metrics, dict) else {}
    checks = {
        "reviewed_candidates": _at_least(metrics.get("reviewed_candidates"), thresholds.reviewed_candidates),
        "observation_days": _at_least(metrics.get("observation_days"), thresholds.observation_days),
        "admin_agreement": _at_least(metrics.get("admin_agreement"), thresholds.admin_agreement),
        "hard_gate_escapes": _exact_count(metrics.get("hard_gate_escapes"), thresholds.hard_gate_escapes),
        "false_automatic_promotions": _exact_count(
            metrics.get("false_automatic_promotions"),
            thresholds.false_automatic_promotions),
        "restart_drill_passed": metrics.get("restart_drill_passed") is True,
        "restoration_drill_passed": metrics.get("restoration_drill_passed") is True,
        "automatic_within_five_minutes": _at_least(
            metrics.get("automatic_within_five_minutes"),
            thresholds.automatic_within_five_minutes),
        "ai_unavailable_samples_handled": metrics.get("ai_unavailable_samples_handled") is True,
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return LaunchReadiness(not failed, failed)


def replay_shadow_candidates(candidates, provider=None, config=None,
                             risk_policy=DEFAULT_POLICY):
    """Replay content-free historical evidence with permanently zero authority.

    This pure projection has no coordinator or ledger handle. It cannot approve,
    deploy, move a ref, or change candidate state.
    """
    records = []
    for sample in candidates:
        if not isinstance(sample, dict):
            raise ValueError("shadow_sample")
        candidate_id = _opaque_id(sample.get("candidate_id", ""))
        if not candidate_id:
            raise ValueError("shadow_candidate_id")
        result = evaluate_risk(sample.get("risk_inputs", {}), risk_policy)
        review = None
        if provider is not None and config is not None:
            review = review_with_ai(
                result,
                {"candidate_id": candidate_id,
                 "attempt_id": _opaque_id(sample.get("attempt_id", ""))},
                provider, config, risk_policy)
        records.append(ShadowReplayRecord(
            candidate_id=candidate_id,
            policy_version=result.policy_version,
            evidence_hash=result.evidence_hash,
            deterministic_score=result.score,
            deterministic_disposition=result.disposition,
            ai_status=review.status if review else "not_configured",
            ai_adjustment=review.adjustment if review else 0.0,
            admin_agreed=(sample.get("admin_agreed")
                          if sample.get("admin_agreed") in (True, False) else None),
        ))
    return tuple(records)


def _rollout_prerequisite_failures(prerequisites, requested_phase):
    common = (
        "restorable_baseline", "queue_accounted", "prod_healthy", "dev_healthy",
        "no_drift", "pause_switch_tested", "kill_switch_tested", "operator_assigned",
    )
    failed = [name for name in common if getattr(prerequisites, name) is not True]
    if requested_phase in ("deterministic_only", "ai_upward"):
        for name in ("supervised_canary_passed", "rollback_drill_passed"):
            if getattr(prerequisites, name) is not True:
                failed.append(name)
    return failed


def evaluate_rollout_transition(current_phase, requested_phase, prerequisites,
                                metrics, actor, rationale,
                                evidence_window_start, evidence_window_end,
                                policy=DEFAULT_ROLLOUT_POLICY,
                                ai_kill_switch=False):
    """Return an immutable go/no-go receipt; never performs the transition."""
    if current_phase not in ROLLOUT_PHASES or requested_phase not in ROLLOUT_PHASES:
        raise ValueError("rollout_phase")
    if not isinstance(prerequisites, RolloutPrerequisites):
        raise ValueError("rollout_prerequisites")
    if not _opaque_id(actor):
        raise ValueError("rollout_actor")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500:
        raise ValueError("rollout_rationale")
    for value in (evidence_window_start, evidence_window_end):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]{1,40})?", value):
            raise ValueError("rollout_evidence_window")
    failures = []
    if requested_phase == "disabled":
        # An emergency pause is always available and cannot depend on AI.
        pass
    else:
        expected_index = ROLLOUT_PHASES.index(current_phase) + 1
        if expected_index >= len(ROLLOUT_PHASES) or ROLLOUT_PHASES[expected_index] != requested_phase:
            failures.append("phase_sequence")
        failures.extend(_rollout_prerequisite_failures(prerequisites, requested_phase))
        if requested_phase == "deterministic_only" and policy.ai_upward_cap != 0:
            failures.append("ai_upward_must_be_zero")
        if requested_phase == "ai_upward":
            failures.extend(ai_upward_launch_ready(metrics, policy.thresholds).failed_criteria)
            if policy.ai_upward_cap <= 0:
                failures.append("ai_upward_cap_disabled")
            if ai_kill_switch:
                failures.append("ai_kill_switch_active")
    failures = tuple(dict.fromkeys(failures))
    evidence = {
        "current_phase": current_phase,
        "requested_phase": requested_phase,
        "actor": actor,
        "rationale": rationale.strip(),
        "evidence_window_start": evidence_window_start,
        "evidence_window_end": evidence_window_end,
        "policy_version": policy.policy_id,
        "policy_hash": policy.configuration_hash,
        "risk_policy_version": policy.risk_policy_version,
        "ai_model": policy.ai_model,
        "ai_prompt_version": policy.ai_prompt_version,
        "prerequisites": dataclasses.asdict(prerequisites),
        "metrics": metrics if isinstance(metrics, dict) else {},
        "failed_criteria": failures,
    }
    return RolloutReceipt(
        decision="go" if not failures else "no_go",
        requested_phase=requested_phase,
        current_phase=current_phase,
        actor=actor,
        rationale=rationale.strip(),
        evidence_window_start=evidence_window_start,
        evidence_window_end=evidence_window_end,
        policy_version=policy.policy_id,
        policy_hash=policy.configuration_hash,
        risk_policy_version=policy.risk_policy_version,
        ai_model=policy.ai_model,
        ai_prompt_version=policy.ai_prompt_version,
        failed_criteria=failures,
        evidence_hash=_canonical_hash(evidence),
    )


def authority_for_next_evaluation(phase, risk_policy=DEFAULT_POLICY,
                                  ai_kill_switch=False, receipt=None,
                                  rollout_policy=DEFAULT_ROLLOUT_POLICY,
                                  window_start="", window_end=""):
    """Resolve authority at evaluation time so the AI kill switch is immediate."""
    if phase not in ROLLOUT_PHASES:
        raise ValueError("rollout_phase")
    receipt_valid = (receipt_matches_evaluation(
        receipt, rollout_policy, window_start, window_end)
        and receipt.requested_phase == phase
        and rollout_policy.risk_policy_version == risk_policy.version)
    deterministic = receipt_valid and phase in ("deterministic_only", "ai_upward")
    ai_upward = (deterministic and phase == "ai_upward" and not ai_kill_switch
                 and rollout_policy.ai_upward_cap > 0
                 and risk_policy.ai_upward_cap == rollout_policy.ai_upward_cap)
    effective = risk_policy if ai_upward else risk_policy.with_ai_upward_cap(0)
    return MappingProxyType({
        "deterministic_promotion": deterministic,
        "ai_upward": ai_upward,
        "risk_policy": effective,
    })


def receipt_matches_evaluation(receipt, policy, window_start, window_end):
    """Prevent evidence from authorizing a changed policy or another window."""
    return (isinstance(receipt, RolloutReceipt)
            and receipt.decision == "go"
            and receipt.policy_version == policy.policy_id
            and receipt.policy_hash == policy.configuration_hash
            and receipt.evidence_window_start == window_start
            and receipt.evidence_window_end == window_end)
