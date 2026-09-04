// providers/openai.js — OpenAI Chat Completions adapter (non-streaming).
//
// System prompt -> the leading `system` message; max_tokens maps directly;
// jsonMode -> response_format {type:"json_object"} (supported; our evaluator
// prompts already contain the word "JSON", which json_object mode requires).
// No prompt-caching request control (OpenAI caches automatically; any
// cached_tokens show up in usage and are normalized to cache_read_input_tokens).

import { completeWithRetry, normalizeStopReason, systemToString } from "./common.js";

const URL_ = "https://api.openai.com/v1/chat/completions";

// Pure request builder (unit-tested): returns { url, headers, body }.
export function buildRequest({ system, messages, maxTokens, providerCfg }) {
  const sys = systemToString(system);
  const msgs = [];
  if (sys) msgs.push({ role: "system", content: sys });
  for (const m of messages) msgs.push({ role: m.role, content: m.content });
  const body = { model: providerCfg.model, max_tokens: maxTokens, messages: msgs };
  if (providerCfg.jsonMode) body.response_format = { type: "json_object" };
  return {
    url: URL_,
    headers: { authorization: "Bearer " + providerCfg.apiKey },
    body,
  };
}

export function buildStreamingRequest(opts) {
  const request = buildRequest(opts);
  request.body.stream = true;
  request.body.stream_options = { include_usage: true };
  return request;
}

// Pure response parser: normalize usage to the canonical field names.
export function parseResponse(data) {
  const choice = (data.choices || [])[0] || {};
  const text = (choice.message && choice.message.content) || "";
  const u = data.usage || {};
  const cached = (u.prompt_tokens_details && u.prompt_tokens_details.cached_tokens) || 0;
  return {
    text,
    stop_reason: normalizeStopReason(choice.finish_reason),
    usage: {
      input_tokens: Math.max(0, (u.prompt_tokens || 0) - cached),
      output_tokens: u.completion_tokens || 0,
      cache_read_input_tokens: cached,
    },
  };
}

export function complete(opts) {
  return completeWithRetry(() => buildRequest(opts), parseResponse);
}
