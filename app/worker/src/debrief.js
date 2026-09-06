// Pure debrief completion/validation orchestration. The router supplies the
// provider call and server-owned oracle inputs; this module decides whether a
// completion is retryable without importing Cloudflare runtime bindings.

import { worstCaseReserveCents } from "./cost.js";
import {
  detectDebriefOracleLeak,
  parseModelJson,
  redactDebriefOracle,
  validateDebriefScorecard,
} from "./validate.js";

export const DEBRIEF_INITIAL_MAX_TOKENS = 1200;
// The prompt documents a 1.5-2k-token scorecard. 2400 covers the high end with
// 20% headroom while applying only to the single truncation retry.
export const DEBRIEF_RETRY_MAX_TOKENS = 2400;

const GENERIC_VALIDATION_MESSAGE = "The debrief could not be generated. Please try again.";

export function debriefValidationMessage(subtype) {
  return subtype === "truncated"
    ? "The debrief exceeded the provider output limit. Please try again."
    : GENERIC_VALIDATION_MESSAGE;
}

// validMessages accepts at most 24,000 UTF-16 code units. One scalar code unit
// can encode to 3 UTF-8 bytes (for example U+0800), so accepted transcript text
// alone can occupy 72,000 bytes. A byte-level tokenizer cannot produce more
// input tokens than bytes. Measuring the final serialized message also covers
// server-owned prompt/framing bytes and conservatively counts escaped surrogates.
export function debriefInputTokenUpperBound(prompt) {
  const framedInput = JSON.stringify([{ role: "user", content: prompt }]);
  return new TextEncoder().encode(framedInput).length;
}

function isBudgetTransitionResult(result) {
  if (result === null || typeof result !== "object" || Array.isArray(result)) {
    return false;
  }
  if (result.ok === true) return true;
  return result.ok === false && typeof result.reason === "string" && result.reason.length > 0;
}

async function reconcileBudgetTransition(transition) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const result = await transition();
      if (isBudgetTransitionResult(result)) return result;
    } catch {
      // A transition can commit before its RPC acknowledgment is lost. Replay
      // the same idempotent operation once to confirm its durable result.
    }
  }
  return null;
}

export async function completeBudgetedOneShot({
  budget,
  reservationId,
  pool,
  caps,
  inputTokens,
  maxTokens,
  providerMaxAttempts = 2,
  complete,
}) {
  const perCallReserveCents = Math.max(
    1,
    worstCaseReserveCents(inputTokens, maxTokens),
  );
  const reservation = {
    pool,
    capPublicCents: caps.capPublicCents,
    capDemoCents: caps.capDemoCents,
    // A fetch rejection can occur after the provider accepted and billed the
    // POST. Reserve every allowed provider request, then retain one per-call
    // worst case for each response whose usage was lost.
    reserveCents: perCallReserveCents * providerMaxAttempts,
  };
  const reserved = await reconcileBudgetTransition(
    () => budget.reserveOneShot(reservationId, reservation),
  );
  if (!reserved?.ok) {
    return {
      ok: false,
      kind: reserved?.reason === "cap_exceeded" ? "cap" : "upstream",
    };
  }

  let completion;
  try {
    completion = await complete();
  } catch {
    completion = { ok: false, kind: "upstream" };
  }

  const settled = await reconcileBudgetTransition(
    () => budget.settleOneShot(
      reservationId,
      completion.ok ? completion.usage : null,
      Math.min(completion.ambiguous_attempts || 0, providerMaxAttempts) * perCallReserveCents,
    ),
  );
  if (!settled) {
    return { ok: false, kind: "upstream", subtype: "settle_unconfirmed" };
  }
  if (!settled.ok) {
    return { ok: false, kind: "upstream" };
  }
  return completion;
}

export async function generateDebriefScorecard({ complete, persona, factMap }) {
  const run = async (maxTokens) => {
    try {
      return await complete(maxTokens);
    } catch {
      return { ok: false, kind: "upstream" };
    }
  };

  let attemptCount = 1;
  let result = await run(DEBRIEF_INITIAL_MAX_TOKENS);
  const initialStopReason = result.stop_reason;
  const attempt = (outcome) => ({
    count: attemptCount,
    ...(initialStopReason ? { initial_stop_reason: initialStopReason } : {}),
    outcome,
  });
  if (!result.ok) {
    return {
      ok: false,
      kind: "upstream",
      upstreamResult: result,
      attempt: attempt("initial_upstream_failed"),
    };
  }

  if (result.stop_reason === "max_tokens") {
    attemptCount = 2;
    result = await run(DEBRIEF_RETRY_MAX_TOKENS);
    if (!result.ok) {
      return {
        ok: false,
        kind: "upstream",
        upstreamResult: result,
        attempt: attempt("retry_upstream_failed"),
      };
    }
    if (result.stop_reason === "max_tokens") {
      return {
        ok: false,
        kind: "validation",
        subtype: "truncated",
        attempt: attempt("retry_truncated"),
      };
    }
  }

  const parsed = parseModelJson(result.text);
  if (!parsed) {
    return {
      ok: false,
      kind: "validation",
      subtype: "unparseable",
      attempt: attempt(attemptCount === 2 ? "retry_invalid" : "invalid"),
    };
  }

  const check = validateDebriefScorecard(parsed);
  if (!check.ok) {
    return {
      ok: false,
      kind: "validation",
      subtype: "wrong_shape",
      errors: check.errors,
      attempt: attempt(attemptCount === 2 ? "retry_invalid" : "invalid"),
    };
  }

  redactDebriefOracle(parsed, persona, factMap);
  const leakField = detectDebriefOracleLeak(parsed, persona, factMap);
  if (leakField) {
    return {
      ok: false,
      kind: "validation",
      subtype: "oracle_leak",
      leakField,
      attempt: attempt(attemptCount === 2 ? "retry_invalid" : "invalid"),
    };
  }

  return {
    ok: true,
    scorecard: parsed,
    attempt: attempt(attemptCount === 2 ? "recovered" : "completed"),
  };
}
