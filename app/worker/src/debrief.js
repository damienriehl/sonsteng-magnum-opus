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

export async function completeBudgetedOneShot({
  budget,
  reservationId,
  pool,
  caps,
  inputTokens,
  maxTokens,
  complete,
}) {
  const reserved = await budget.reserveOneShot(reservationId, {
    pool,
    capPublicCents: caps.capPublicCents,
    capDemoCents: caps.capDemoCents,
    reserveCents: Math.max(1, worstCaseReserveCents(inputTokens, maxTokens)),
  });
  if (!reserved.ok) {
    return {
      ok: false,
      kind: reserved.reason === "cap_exceeded" ? "cap" : "upstream",
    };
  }

  let completion;
  try {
    completion = await complete();
  } catch {
    completion = { ok: false, kind: "upstream" };
  }

  try {
    await budget.settleOneShot(
      reservationId,
      completion.ok ? completion.usage : null,
    );
  } catch {
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

  let result = await run(DEBRIEF_INITIAL_MAX_TOKENS);
  if (!result.ok) return { ok: false, kind: "upstream", upstreamResult: result };

  if (result.stop_reason === "max_tokens") {
    result = await run(DEBRIEF_RETRY_MAX_TOKENS);
    if (!result.ok) return { ok: false, kind: "upstream", upstreamResult: result };
    if (result.stop_reason === "max_tokens") {
      return { ok: false, kind: "validation", subtype: "truncated" };
    }
  }

  const parsed = parseModelJson(result.text);
  if (!parsed) {
    return { ok: false, kind: "validation", subtype: "unparseable" };
  }

  const check = validateDebriefScorecard(parsed);
  if (!check.ok) {
    return {
      ok: false,
      kind: "validation",
      subtype: "wrong_shape",
      errors: check.errors,
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
    };
  }

  return { ok: true, scorecard: parsed };
}
