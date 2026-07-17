// providers/anthropic.js — Anthropic Messages API adapter (non-streaming).
//
// The ONLY provider with prompt caching: when `system` is a {prefix, tail} pair
// (a chat turn), the prefix (Segment A, ≥4096 tokens, byte-stable) is sent as the
// first system block with cache_control ephemeral so it caches once and reuses
// across every persona/session, and a second cache_control breakpoint rides the
// last message (the conversation-history breakpoint). Evaluator calls pass
// system=null + the whole prompt as one user message — no caching (they vary).
//
// jsonMode: no request flag exists; the prompt's own OUTPUT CONTRACT already
// pins JSON-only output. Unchanged by design.

import { completeWithRetry } from "./common.js";

const URL_ = "https://api.anthropic.com/v1/messages";
const VERSION = "2023-06-01";
const EPHEMERAL = { type: "ephemeral" };

function systemBlocks(system) {
  if (system == null) return undefined;
  if (typeof system === "string") return [{ type: "text", text: system }];
  return [
    { type: "text", text: system.prefix + "\n\n", cache_control: EPHEMERAL },
    { type: "text", text: system.tail },
  ];
}

function messagesWithBreakpoint(messages, cacheLast) {
  return messages.map((m, i) => {
    if (!cacheLast || i !== messages.length - 1) return { role: m.role, content: m.content };
    return { role: m.role, content: [{ type: "text", text: m.content, cache_control: EPHEMERAL }] };
  });
}

// Pure request builder (unit-tested): returns { url, headers, body }.
export function buildRequest({ system, messages, maxTokens, providerCfg }) {
  const chatMode = system != null && typeof system === "object";
  const body = {
    model: providerCfg.model,
    max_tokens: maxTokens,
    messages: messagesWithBreakpoint(messages, chatMode),
  };
  const sys = systemBlocks(system);
  if (sys) body.system = sys;
  return {
    url: URL_,
    headers: { "x-api-key": providerCfg.apiKey, "anthropic-version": VERSION },
    body,
  };
}

// Pure response parser: normalize to { text, usage } (Anthropic field names are
// already the canonical shape).
export function parseResponse(data) {
  const text = (data.content || [])
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("");
  return { text, usage: data.usage || {} };
}

export function complete(opts) {
  return completeWithRetry(() => buildRequest(opts), parseResponse);
}
