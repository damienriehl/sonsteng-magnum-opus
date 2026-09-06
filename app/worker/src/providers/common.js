// providers/common.js — shared bounded fetch retry flow for all adapters.
//
// Semantics (docs/research/worker-llm-facts.md §6, applied per-provider):
//   429            -> read retry-after (cap ~2s), retry once.
//   500/529/5xx    -> one retry, 500ms-1s backoff + jitter.
//   network error  -> one retry.
//   other 4xx      -> {ok:false, kind:"config", status} — never retried. For the
//                     hosted key that is our config bug; for BYOK it usually
//                     means the USER'S key/model was rejected (surfaced as a
//                     validation_error by the router, never in-character).
//   exhausted      -> {ok:false, kind:"upstream", status?} — the router returns
//                     the in-character "bad phone connection" and burns no turn.
//
// SECURITY: this module NEVER logs. Request headers/urls carry API keys (hosted
// or the user's BYOK key) and must not reach any logging path.

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export const PROVIDER_TIMEOUT_MS = 60_000;
export const PROVIDER_DEFAULT_MAX_ATTEMPTS = 3;
// Each debrief logical completion gets one initial request plus one retry. The
// debrief layer itself may run twice, so the end-to-end ceiling is four.
export const DEBRIEF_PROVIDER_MAX_ATTEMPTS = 2;

// buildReq: () => { url, headers, body }   (body = plain object, JSON-encoded here)
// parseResp: (data) => { text, usage, stop_reason? } (usage normalized to the
//                                           Anthropic field names: input_tokens,
//                                           output_tokens, cache_read_input_tokens,
//                                           cache_creation_input_tokens; provider
//                                           stop reasons use the canonical values
//                                           returned by normalizeStopReason)
export async function completeWithRetry(
  buildReq,
  parseResp,
  maxAttempts = PROVIDER_DEFAULT_MAX_ATTEMPTS,
) {
  const attemptLimit = Number.isInteger(maxAttempts) && maxAttempts > 0
    ? maxAttempts
    : PROVIDER_DEFAULT_MAX_ATTEMPTS;
  let attemptCount = 0;
  let ambiguousAttempts = 0;
  const attempt = async () => {
    attemptCount += 1;
    const { url, headers, body } = buildReq();
    return fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
    });
  };

  const withAmbiguousAttempts = (result) => ambiguousAttempts > 0
    ? { ...result, ambiguous_attempts: ambiguousAttempts }
    : result;

  const finish = async (res) => {
    let data;
    try {
      data = await res.json();
    } catch {
      return { ok: false, kind: "upstream", status: res.status };
    }
    try {
      const { text, usage, stop_reason } = parseResp(data);
      return {
        ok: true,
        text,
        usage: usage || {},
        ...(stop_reason ? { stop_reason } : {}),
      };
    } catch {
      return { ok: false, kind: "upstream", status: res.status };
    }
  };

  let res;
  try {
    res = await attempt();
  } catch {
    ambiguousAttempts += 1;
    if (attemptCount >= attemptLimit) {
      return withAmbiguousAttempts({ ok: false, kind: "upstream" });
    }
    await sleep(600);
    try {
      res = await attempt();
    } catch {
      ambiguousAttempts += 1;
      return withAmbiguousAttempts({ ok: false, kind: "upstream" });
    }
  }

  if (res.ok) {
    const result = await finish(res);
    if (!result.ok) ambiguousAttempts += 1;
    return withAmbiguousAttempts(result);
  }
  if (res.status >= 400 && res.status < 500 && res.status !== 429) {
    return withAmbiguousAttempts({ ok: false, kind: "config", status: res.status });
  }
  if (attemptCount >= attemptLimit) {
    return withAmbiguousAttempts({ ok: false, kind: "upstream", status: res.status });
  }

  if (res.status === 429) {
    const ra = parseInt(res.headers.get("retry-after") || "", 10);
    await sleep(Number.isFinite(ra) ? Math.min(2000, Math.max(0, ra * 1000)) : 1000);
  } else {
    await sleep(500 + Math.floor(Math.random() * 500));
  }

  try {
    res = await attempt();
  } catch {
    ambiguousAttempts += 1;
    return withAmbiguousAttempts({ ok: false, kind: "upstream" });
  }
  if (res.ok) {
    const result = await finish(res);
    if (!result.ok) ambiguousAttempts += 1;
    return withAmbiguousAttempts(result);
  }
  if (res.status >= 400 && res.status < 500 && res.status !== 429) {
    return withAmbiguousAttempts({ ok: false, kind: "config", status: res.status });
  }
  return withAmbiguousAttempts({ ok: false, kind: "upstream", status: res.status });
}

// Collapse provider-specific output-cap and ordinary-stop spellings without
// losing less common safety/tool/refusal reasons (which remain lowercase).
export function normalizeStopReason(reason) {
  if (typeof reason !== "string" || !reason) return null;
  const normalized = reason.trim().toLowerCase();
  if (normalized === "length" || normalized === "max_tokens") return "max_tokens";
  if (normalized === "end_turn" || normalized === "stop") return "stop";
  return normalized;
}

// Join a {prefix, tail} chat system into one string (for providers without
// prompt caching). Matches the byte layout the Anthropic adapter sends as blocks.
export function systemToString(system) {
  if (system == null) return null;
  if (typeof system === "string") return system;
  return system.prefix + "\n\n" + system.tail;
}
