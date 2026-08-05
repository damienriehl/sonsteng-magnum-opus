import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";
import { PROMOTION_STAGE } from "../src/editor-status.js";

function candidate(over = {}) {
  return {
    id: "cand-1", principal: "slot:john", environment: "production",
    idempotency_key: "idem-1", request_digest: "digest-1", content_bytes: 120,
    source_ref: "data/copy/home.json#hero", ...over,
  };
}

test("accepted PROD save atomically creates candidate, attempt, and saved event", () => {
  const core = makeCore(() => 1000);
  const first = core.createPromotionCandidate(candidate());
  assert.equal(first.ok, true);
  assert.equal(first.candidate.stage, PROMOTION_STAGE.SAVED);
  assert.equal(first.attempt.number, 1);
  assert.deepEqual(core.listPromotionEvents("cand-1").map((e) => e.type), ["saved"]);
  const replay = core.createPromotionCandidate(candidate());
  assert.equal(replay.replay, true);
  assert.equal(core.listPromotionCandidates().length, 1);
});

test("idempotency is principal, environment, operation, resource and body bound", () => {
  const core = makeCore();
  assert.equal(core.createPromotionCandidate(candidate()).ok, true);
  for (const patch of [
    { principal: "slot:roger" }, { environment: "dev" }, { request_digest: "changed" },
    { source_ref: "data/copy/home.json#other" },
  ]) assert.equal(core.createPromotionCandidate(candidate(patch)).reason, "idempotency_conflict");
});

test("admission bounds reject before acceptance without disturbing accepted work", () => {
  const core = makeCore();
  assert.equal(core.createPromotionCandidate(candidate(), { maxBytes: 200, maxQueued: 1, maxStoredBytes: 200, perPrincipalPerMinute: 2 }).ok, true);
  const over = core.createPromotionCandidate(candidate({ id: "cand-2", idempotency_key: "idem-2", request_digest: "digest-2" }),
    { maxBytes: 200, maxQueued: 1, maxStoredBytes: 200, perPrincipalPerMinute: 2 });
  assert.equal(over.reason, "queue_full");
  assert.equal(core.listPromotionCandidates().length, 1);
  assert.equal(core.createPromotionCandidate(candidate({ id: "huge", idempotency_key: "huge", request_digest: "huge", content_bytes: 201 }), { maxBytes: 200 }).reason, "too_large");
});

test("legal transitions project public stages; stale and terminal writes do nothing", () => {
  const core = makeCore();
  const made = core.createPromotionCandidate(candidate());
  const a = made.attempt.id;
  assert.equal(core.transitionPromotion({ candidate_id: "cand-1", attempt_id: a, expected_stage: "saved", to: "validating", actor: "service:prod", fencing_token: 0 }).ok, true);
  assert.equal(core.transitionPromotion({ candidate_id: "cand-1", attempt_id: a, expected_stage: "saved", to: "failed", actor: "service:prod", fencing_token: 0 }).reason, "stale_state");
  assert.equal(core.transitionPromotion({ candidate_id: "cand-1", attempt_id: a, expected_stage: "validating", to: "published", actor: "service:prod", fencing_token: 0 }).reason, "illegal_transition");
  assert.equal(core.transitionPromotion({ candidate_id: "cand-1", attempt_id: a, expected_stage: "validating", to: "failed", actor: "service:prod", fencing_token: 0 }).ok, true);
  assert.equal(core.transitionPromotion({ candidate_id: "cand-1", attempt_id: a, expected_stage: "failed", to: "saved", actor: "service:prod", fencing_token: 0 }).reason, "terminal_state");
});

test("oldest claim is exclusive and expired lease is reclaimed with higher fencing", () => {
  const clock = { value: 1000 };
  const core = makeCore(() => clock.value);
  core.createPromotionCandidate(candidate({ id: "a", idempotency_key: "a", request_digest: "a" }));
  clock.value = 1001;
  core.createPromotionCandidate(candidate({ id: "b", idempotency_key: "b", request_digest: "b" }));
  const first = core.claimPromotion("worker-a", 100);
  assert.equal(first.candidate.id, "a");
  assert.equal(core.claimPromotion("worker-b", 100).reason, "lease_held");
  clock.value = 1200;
  const reclaimed = core.claimPromotion("worker-b", 100);
  assert.ok(reclaimed.fencing_token > first.fencing_token);
  assert.equal(core.transitionPromotion({ candidate_id: "a", attempt_id: first.attempt.id, expected_stage: "validating", to: "failed", actor: "worker-a", fencing_token: first.fencing_token }).reason, "stale_fence");
});

test("decision is exact-attempt/evidence/base/manifest bound and attributed", () => {
  const core = makeCore(() => 1000);
  const made = core.createPromotionCandidate(candidate());
  const ids = { candidate_id: "cand-1", attempt_id: made.attempt.id };
  core.bindPromotionEvidence({ ...ids, base_sha: "base", evidence_hash: "ev", manifest_hash: "man", actor: "service:prod" });
  core.transitionPromotion({ ...ids, expected_stage: "saved", to: "validating", actor: "service:prod", fencing_token: 0 });
  core.transitionPromotion({ ...ids, expected_stage: "validating", to: "preview_ready", actor: "service:prod", fencing_token: 0 });
  core.transitionPromotion({ ...ids, expected_stage: "preview_ready", to: "awaiting_approval", actor: "service:prod", fencing_token: 0 });
  assert.equal(core.decidePromotion({ ...ids, decision: "approve", principal: "slot:admin", base_sha: "base", evidence_hash: "stale", manifest_hash: "man", idempotency_key: "d1", request_digest: "d" }).reason, "stale_evidence");
  const ok = core.decidePromotion({ ...ids, decision: "approve", principal: "slot:admin", base_sha: "base", evidence_hash: "ev", manifest_hash: "man", idempotency_key: "d2", request_digest: "d" });
  assert.equal(ok.ok, true);
  assert.equal(ok.decision.principal, "slot:admin");
});

test("prepared preview projection is immutable, bounded, redacted, and returned for the active tuple", () => {
  const core = makeCore(() => 1000);
  const made = core.createPromotionCandidate(candidate());
  const tuple = { candidate_id:"cand-1", attempt_id:made.attempt.id,
    base_sha:"base", evidence_hash:"ev", manifest_hash:"man" };
  core.bindPromotionEvidence({ ...tuple, actor:"service:prod" });
  const projection = { ...tuple, preview_html:"<p>candidate</p>",
    evidence:{ gates:[{ name:"tests", status:"pass", summary:"all green", internal:"hidden" }] },
    score:{ confidence:0.93, deterministic_score:0.9, internal:"hidden" },
    ai:{ disposition:"hold", reasons:["borderline risk"], provider_error:"Bearer secret" } };
  assert.equal(core.bindPromotionProjection(projection).ok, true);
  assert.equal(core.bindPromotionProjection(projection).replay, true);
  const current = core.getPromotionCandidate("cand-1");
  assert.equal(current.preview_html, "<p>candidate</p>");
  assert.deepEqual(current.evidence.gates[0], { name:"tests", status:"pass", summary:"all green" });
  assert.equal(current.score.internal, undefined);
  assert.equal(current.ai.provider_error, undefined);
  assert.doesNotMatch(JSON.stringify(current), /Bearer secret|hidden/);
  assert.equal(core.bindPromotionProjection({ ...projection, preview_html:"<p>changed</p>" }).reason, "immutable_projection");
  assert.equal(core.bindPromotionProjection({ ...projection, evidence_hash:"other" }).reason, "stale_evidence");
  assert.equal(core.bindPromotionProjection({ ...projection, preview_html:"x".repeat(600_000) }).reason, "projection_too_large");
});

test("retry creates a linked immutable attempt", () => {
  const core = makeCore();
  const made = core.createPromotionCandidate(candidate());
  core.transitionPromotion({ candidate_id: "cand-1", attempt_id: made.attempt.id, expected_stage: "saved", to: "failed", actor: "service:prod", fencing_token: 0 });
  const retry = core.retryPromotion({ candidate_id: "cand-1", prior_attempt_id: made.attempt.id, principal: "slot:admin", idempotency_key: "retry-1", request_digest: "retry" });
  assert.equal(retry.ok, true);
  assert.equal(retry.attempt.number, 2);
  assert.equal(retry.attempt.prior_attempt_id, made.attempt.id);
  assert.notEqual(retry.attempt.id, made.attempt.id);
});

test("lane pause/health and reconciliation observations are PROD-only", () => {
  const core = makeCore();
  core.setPromotionLane({ expected_version: 0, paused: true, health: "restore_failed", actor: "slot:admin", reason_code: "restore_unproven" });
  const lane = core.getPromotionLane();
  assert.equal(lane.paused, 1);
  assert.equal(lane.health, "restore_failed");
  core.recordPromotionObservation({ actor: "service:prod", kind: "provider", resource: "worker", observed_id: "v1", digest: "sha" });
  assert.equal(core.listPromotionObservations().length, 1);
  assert.equal(core.listAll().length, 0);
});

test("release manifests are immutable and replayable by manifest hash", () => {
  const core = makeCore();
  const release = { manifest_hash: "manifest", base_sha: "base", commit_sha: "commit",
    contract_hashes: JSON.stringify({ editor: "e1" }), state: "verified",
    pages_preview_id: "preview", pages_production_id: "prod", worker_version_id: "worker" };
  assert.equal(core.recordPromotionRelease(release).ok, true);
  assert.equal(core.recordPromotionRelease(release).replay, true);
  assert.equal(core.recordPromotionRelease({ ...release, commit_sha: "different" }).reason, "immutable_release");
  assert.deepEqual(core.getPromotionRelease("manifest").contract_hashes, { editor: "e1" });
});
