// providers/common.js — shared retry-once fetch loop for all provider adapters.
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

// buildReq: () => { url, headers, body }   (body = plain object, JSON-encoded here)
// parseResp: (data) => { text, usage }     (usage normalized to the Anthropic
//                                           field names: input_tokens,
//                                           output_tokens, cache_read_input_tokens,
//                                           cache_creation_input_tokens)
export async function completeWithRetry(buildReq, parseResp) {
  const attempt = async () => {
    const { url, headers, body } = buildReq();
    return fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
  };

  const finish = async (res) => {
    let data;
    try {
      data = await res.json();
    } catch {
      return { ok: false, kind: "upstream", status: res.status };
    }
    try {
      const { text, usage } = parseResp(data);
      return { ok: true, text, usage: usage || {} };
    } catch {
      return { ok: false, kind: "upstream", status: res.status };
    }
  };

  let res;
  try {
    res = await attempt();
  } catch {
    await sleep(600);
    try {
      res = await attempt();
    } catch {
      return { ok: false, kind: "upstream" };
    }
  }

  if (res.ok) return finish(res);
  if (res.status >= 400 && res.status < 500 && res.status !== 429) {
    return { ok: false, kind: "config", status: res.status };
  }

  let waitMs;
  if (res.status === 429) {
    const ra = parseInt(res.headers.get("retry-after") || "", 10);
    waitMs = Number.isFinite(ra) ? Math.min(2000, Math.max(0, ra * 1000)) : 1000;
  } else {
    waitMs = 500 + Math.floor(Math.random() * 500);
  }
  await sleep(waitMs);

  try {
    res = await attempt();
  } catch {
    return { ok: false, kind: "upstream" };
  }
  if (res.ok) return finish(res);
  if (res.status >= 400 && res.status < 500 && res.status !== 429) {
    return { ok: false, kind: "config", status: res.status };
  }
  return { ok: false, kind: "upstream", status: res.status };
}

// Join a {prefix, tail} chat system into one string (for providers without
// prompt caching). Matches the byte layout the Anthropic adapter sends as blocks.
export function systemToString(system) {
  if (system == null) return null;
  if (typeof system === "string") return system;
  return system.prefix + "\n\n" + system.tail;
}
