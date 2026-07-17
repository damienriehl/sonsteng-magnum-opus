// anthropic.js — the ONLY place that talks to Anthropic. Non-streaming.
//
// SECURITY: the Worker constructs the ENTIRE request server-side — model is
// hard-coded from env (never the client), system prompt is server-built, and
// max_tokens is server-set. Client input is untrusted `messages` content only.
// This function NEVER accepts a client-supplied model/system/max_tokens/tools —
// if it did, the API key would become a free general-purpose proxy.
//
// Caching (docs/research/worker-llm-facts.md §2): the system is sent as TWO
// blocks — Segment A (the ≥4096-token shared prefix) with cache_control ephemeral
// so it caches ONCE and is reused across every persona/session, then the persona
// tail. A second cache_control breakpoint rides the last message so cumulative
// history is cheap. Segment A must be byte-stable (no timestamps/UUIDs) or the
// cache silently misses.
//
// Errors (§6): raw fetch does NOT auto-retry. 429 -> read retry-after, one retry.
// 529/500 -> one retry with backoff+jitter. Exhausted -> {ok:false, kind:"upstream"}
// (caller returns the in-character "bad phone connection", burns no turn). 4xx
// config errors -> {ok:false, kind:"config"} (never retry, never in-character).

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION = "2023-06-01";
const EPHEMERAL = { type: "ephemeral" };

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// Build the chat system blocks: [ SegmentA (cached), persona tail ].
export function chatSystemBlocks(segmentA, personaTail) {
  return [
    { type: "text", text: segmentA + "\n\n", cache_control: EPHEMERAL },
    { type: "text", text: personaTail },
  ];
}

// Convert client messages to API messages, marking the LAST message's content
// with a cache_control breakpoint (the conversation-history breakpoint).
function withHistoryBreakpoint(messages) {
  return messages.map((m, i) => {
    if (i !== messages.length - 1) return { role: m.role, content: m.content };
    return {
      role: m.role,
      content: [{ type: "text", text: m.content, cache_control: EPHEMERAL }],
    };
  });
}

function extractText(data) {
  if (!data || !Array.isArray(data.content)) return "";
  return data.content
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("");
}

// opts: { model, apiKey, maxTokens, system?, messages, cacheLastMessage? }
async function callOnce(opts) {
  const body = {
    model: opts.model,
    max_tokens: opts.maxTokens,
    messages: opts.cacheLastMessage ? withHistoryBreakpoint(opts.messages) : opts.messages,
  };
  if (opts.system) body.system = opts.system;

  const res = await fetch(ANTHROPIC_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": opts.apiKey,
      "anthropic-version": ANTHROPIC_VERSION,
    },
    body: JSON.stringify(body),
  });
  return res;
}

async function callWithRetry(opts) {
  let res;
  try {
    res = await callOnce(opts);
  } catch {
    // Network error — treat as one transient failure, retry once.
    await sleep(600);
    try {
      res = await callOnce(opts);
    } catch {
      return { ok: false, kind: "upstream" };
    }
  }

  if (res.ok) {
    const data = await res.json();
    return { ok: true, text: extractText(data), usage: data.usage || {}, stopReason: data.stop_reason };
  }

  // 4xx config errors (except 429): never retry, never in-character.
  if (res.status >= 400 && res.status < 500 && res.status !== 429) {
    return { ok: false, kind: "config", status: res.status };
  }

  // 429 / 5xx / 529: one retry with appropriate backoff.
  let waitMs = 700;
  if (res.status === 429) {
    const ra = parseInt(res.headers.get("retry-after") || "", 10);
    waitMs = Number.isFinite(ra) ? Math.min(2000, Math.max(0, ra * 1000)) : 1000;
  } else {
    waitMs = 500 + Math.floor(Math.random() * 500);
  }
  await sleep(waitMs);

  try {
    res = await callOnce(opts);
  } catch {
    return { ok: false, kind: "upstream" };
  }
  if (res.ok) {
    const data = await res.json();
    return { ok: true, text: extractText(data), usage: data.usage || {}, stopReason: data.stop_reason };
  }
  if (res.status >= 400 && res.status < 500 && res.status !== 429) {
    return { ok: false, kind: "config", status: res.status };
  }
  return { ok: false, kind: "upstream", status: res.status };
}

// Chat turn: cached two-block system + history breakpoint.
export function callChat(env, { segmentA, personaTail, messages, maxTokens }) {
  return callWithRetry({
    model: env.ANTHROPIC_MODEL,
    apiKey: env.ANTHROPIC_API_KEY,
    maxTokens,
    system: chatSystemBlocks(segmentA, personaTail),
    messages,
    cacheLastMessage: true,
  });
}

// One-shot evaluator call (debrief/critique): the whole server-built prompt is the
// single user message; no system, no caching (transcript/deliverable vary).
export function callEvaluator(env, { prompt, maxTokens }) {
  return callWithRetry({
    model: env.ANTHROPIC_MODEL,
    apiKey: env.ANTHROPIC_API_KEY,
    maxTokens,
    messages: [{ role: "user", content: prompt }],
    cacheLastMessage: false,
  });
}
