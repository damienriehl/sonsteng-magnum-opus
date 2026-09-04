// providers/google.js — Google Gemini generateContent adapter (non-streaming).
//
// System prompt -> systemInstruction; messages -> contents with role mapping
// user->user, assistant->model; maxTokens -> generationConfig.maxOutputTokens;
// jsonMode -> generationConfig.responseMimeType "application/json".
//
// AUTH NOTE: the API key is sent in the `x-goog-api-key` HEADER (officially
// supported), NOT as a `?key=` query parameter. Keys in URLs leak into request
// logs, proxies, and error traces; the header form keeps the key out of every
// URL-shaped surface — load-bearing for the "key never stored or logged" BYOK
// guarantee.

import { completeWithRetry, normalizeStopReason, systemToString } from "./common.js";

const BASE = "https://generativelanguage.googleapis.com/v1beta/models/";

// Pure request builder (unit-tested): returns { url, headers, body }.
export function buildRequest({ system, messages, maxTokens, providerCfg }) {
  const sys = systemToString(system);
  const body = {
    contents: messages.map((m) => ({
      role: m.role === "assistant" ? "model" : "user",
      parts: [{ text: m.content }],
    })),
    generationConfig: { maxOutputTokens: maxTokens },
  };
  if (sys) body.systemInstruction = { parts: [{ text: sys }] };
  if (providerCfg.jsonMode) body.generationConfig.responseMimeType = "application/json";
  return {
    url: BASE + encodeURIComponent(providerCfg.model) + ":generateContent",
    headers: { "x-goog-api-key": providerCfg.apiKey },
    body,
  };
}

export function buildStreamingRequest(opts) {
  const request = buildRequest(opts);
  request.url = request.url.replace(/:generateContent$/, ":streamGenerateContent?alt=sse");
  return request;
}

// Pure response parser: normalize usageMetadata to the canonical field names.
export function parseResponse(data) {
  const cand = (data.candidates || [])[0] || {};
  const parts = (cand.content && cand.content.parts) || [];
  const text = parts.map((p) => p.text || "").join("");
  const u = data.usageMetadata || {};
  const cached = u.cachedContentTokenCount || 0;
  const thoughtTokens = Number.isFinite(u.thoughtsTokenCount)
    ? Math.max(0, u.thoughtsTokenCount)
    : null;
  return {
    text,
    stop_reason: normalizeStopReason(cand.finishReason),
    usage: {
      input_tokens: Math.max(0, (u.promptTokenCount || 0) - cached),
      output_tokens: u.candidatesTokenCount || 0,
      cache_read_input_tokens: cached,
      ...(thoughtTokens != null ? { thought_tokens: thoughtTokens } : {}),
    },
  };
}

export function complete(opts) {
  return completeWithRetry(() => buildRequest(opts), parseResponse);
}
