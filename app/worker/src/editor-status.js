// editor-status.js — the suggestion status machine (docs/research/editor-apply-spec.md
// §Status machine). Centralized so both the core and its tests share one truth.
//
// Owners:
//   suggest()  -> pending; and pending -> superseded (same editor re-edits source_ref)
//   decide()   -> the SOLE writer of `accepted`; also -> declined
//   markDrift()/reanchor() -> drift <-> pending (re-anchor forces RE-REVIEW)
//   claimBatch() -> the SOLE writer of `in_flight`
//   finalize()/reconcile() -> applied / accepted_blocked / needs_human / (rollback to accepted)

export const STATUS = {
  PENDING: "pending",
  SUPERSEDED: "superseded",
  ACCEPTED: "accepted",
  ACCEPTED_BLOCKED: "accepted_blocked",
  DECLINED: "declined",
  DRIFT: "drift",
  NEEDS_HUMAN: "needs_human",
  IN_FLIGHT: "in_flight",
  APPLIED: "applied",
};

// Terminal states — no transition may leave them (⛔ in the spec).
export const TERMINAL = [STATUS.SUPERSEDED, STATUS.DECLINED, STATUS.APPLIED];

// from -> Set(allowed to). Anything not listed is rejected by canTransition.
export const ALLOWED_TRANSITIONS = {
  [STATUS.PENDING]: new Set([
    STATUS.SUPERSEDED, STATUS.DECLINED, STATUS.ACCEPTED, STATUS.DRIFT,
  ]),
  [STATUS.ACCEPTED]: new Set([
    // superseded: DIRECT_APPLY only — the SAME editor re-edits the SAME source_ref
    // before the daemon claims the accepted-but-unapplied row (last-edit-wins,
    // exactly like pending). Never for an in_flight (claimed + leased) row.
    STATUS.IN_FLIGHT, STATUS.DRIFT, STATUS.SUPERSEDED,
  ]),
  [STATUS.IN_FLIGHT]: new Set([
    STATUS.APPLIED, STATUS.ACCEPTED_BLOCKED, STATUS.DRIFT,
    STATUS.NEEDS_HUMAN, STATUS.ACCEPTED,
  ]),
  [STATUS.ACCEPTED_BLOCKED]: new Set([
    STATUS.ACCEPTED, STATUS.DECLINED,
  ]),
  [STATUS.DRIFT]: new Set([
    STATUS.PENDING, STATUS.DECLINED,
  ]),
  [STATUS.NEEDS_HUMAN]: new Set([
    STATUS.APPLIED, STATUS.ACCEPTED, STATUS.DECLINED,
  ]),
  // terminal states:
  [STATUS.SUPERSEDED]: new Set(),
  [STATUS.DECLINED]: new Set(),
  [STATUS.APPLIED]: new Set(),
};

export function isTerminal(status) {
  return TERMINAL.includes(status);
}

export function canTransition(from, to) {
  if (from === to) return false;
  const set = ALLOWED_TRANSITIONS[from];
  return !!set && set.has(to);
}

// Public PROD promotion vocabulary. Internal coordinator substates are recorded
// as events; callers only project these deliberately small, stable stages.
export const PROMOTION_STAGE = Object.freeze({
  SAVED: "saved",
  VALIDATING: "validating",
  PREVIEW_READY: "preview_ready",
  AWAITING_APPROVAL: "awaiting_approval",
  PUBLISHING: "publishing",
  PUBLISHED: "published",
  FAILED: "failed",
});

export const PROMOTION_TRANSITIONS = Object.freeze({
  [PROMOTION_STAGE.SAVED]: new Set([PROMOTION_STAGE.VALIDATING, PROMOTION_STAGE.FAILED]),
  [PROMOTION_STAGE.VALIDATING]: new Set([PROMOTION_STAGE.PREVIEW_READY, PROMOTION_STAGE.FAILED]),
  [PROMOTION_STAGE.PREVIEW_READY]: new Set([
    PROMOTION_STAGE.AWAITING_APPROVAL, PROMOTION_STAGE.PUBLISHING, PROMOTION_STAGE.FAILED,
  ]),
  [PROMOTION_STAGE.AWAITING_APPROVAL]: new Set([PROMOTION_STAGE.PUBLISHING, PROMOTION_STAGE.FAILED]),
  [PROMOTION_STAGE.PUBLISHING]: new Set([PROMOTION_STAGE.PUBLISHED, PROMOTION_STAGE.FAILED]),
  [PROMOTION_STAGE.PUBLISHED]: new Set(),
  [PROMOTION_STAGE.FAILED]: new Set(),
});
