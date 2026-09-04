// Provider adapter request-shaping + usage normalization (pure builders/parsers).
import { test } from "node:test";
import assert from "node:assert/strict";

import * as anthropic from "../src/providers/anthropic.js";
import * as openai from "../src/providers/openai.js";
import * as google from "../src/providers/google.js";
import { getProvider, PROVIDER_NAMES } from "../src/providers/registry.js";
import { completeWithRetry, PROVIDER_TIMEOUT_MS, systemToString } from "../src/providers/common.js";

const CHAT = {
  system: { prefix: "SEGMENT-A-TEXT", tail: "PERSONA-TAIL" },
  messages: [
    { role: "user", content: "Hello." },
    { role: "assistant", content: "Hi." },
    { role: "user", content: "Tell me what happened." },
  ],
  maxTokens: 300,
  providerCfg: { apiKey: "sk-test-abc", model: "test-model", jsonMode: false },
};

const EVAL = {
  system: null,
  messages: [{ role: "user", content: "Return ONLY valid JSON …" }],
  maxTokens: 1200,
  providerCfg: { apiKey: "sk-test-abc", model: "test-model", jsonMode: true },
};

test("registry exposes exactly the three providers", () => {
  assert.deepEqual(PROVIDER_NAMES.sort(), ["anthropic", "google", "openai"]);
  for (const n of PROVIDER_NAMES) assert.equal(typeof getProvider(n).complete, "function");
  assert.equal(getProvider("mistral"), null);
});

// ---- Anthropic ---------------------------------------------------------------

test("anthropic chat: cached two-block system + history breakpoint on last message", () => {
  const { url, headers, body } = anthropic.buildRequest(CHAT);
  assert.equal(url, "https://api.anthropic.com/v1/messages");
  assert.equal(headers["x-api-key"], "sk-test-abc");
  assert.equal(body.model, "test-model");
  assert.equal(body.max_tokens, 300);
  // System: Segment A block carries cache_control; persona tail does not.
  assert.equal(body.system.length, 2);
  assert.equal(body.system[0].text, "SEGMENT-A-TEXT\n\n");
  assert.deepEqual(body.system[0].cache_control, { type: "ephemeral" });
  assert.equal(body.system[1].text, "PERSONA-TAIL");
  assert.equal(body.system[1].cache_control, undefined);
  // History breakpoint: ONLY the last message gets block-form content + cache_control.
  assert.equal(typeof body.messages[0].content, "string");
  assert.equal(typeof body.messages[1].content, "string");
  const last = body.messages[2].content;
  assert.deepEqual(last[0].cache_control, { type: "ephemeral" });
  assert.equal(last[0].text, "Tell me what happened.");
});

test("anthropic evaluator: no system, no cache_control anywhere (one-shot varies)", () => {
  const { body } = anthropic.buildRequest(EVAL);
  assert.equal(body.system, undefined);
  assert.equal(typeof body.messages[0].content, "string");
  assert.ok(!JSON.stringify(body).includes("cache_control"));
});

test("anthropic parseResponse: canonical usage and stop reason passthrough", () => {
  const { text, usage, stop_reason } = anthropic.parseResponse({
    content: [{ type: "text", text: "hi " }, { type: "text", text: "there" }],
    usage: { input_tokens: 10, output_tokens: 20, cache_read_input_tokens: 5000, cache_creation_input_tokens: 7 },
    stop_reason: "end_turn",
  });
  assert.equal(text, "hi there");
  assert.equal(usage.cache_read_input_tokens, 5000);
  assert.equal(usage.cache_creation_input_tokens, 7);
  assert.equal(stop_reason, "stop");
});

// ---- OpenAI ------------------------------------------------------------------

test("openai chat: system message first, bearer auth, max_tokens mapped", () => {
  const { url, headers, body } = openai.buildRequest(CHAT);
  assert.equal(url, "https://api.openai.com/v1/chat/completions");
  assert.equal(headers.authorization, "Bearer sk-test-abc");
  assert.equal(body.model, "test-model");
  assert.equal(body.max_tokens, 300);
  assert.equal(body.messages[0].role, "system");
  assert.equal(body.messages[0].content, "SEGMENT-A-TEXT\n\nPERSONA-TAIL");
  assert.equal(body.messages.length, 4); // system + 3 chat messages
  assert.equal(body.messages[3].content, "Tell me what happened.");
  assert.equal(body.response_format, undefined); // jsonMode off for chat
});

test("openai jsonMode: response_format json_object", () => {
  const { body } = openai.buildRequest(EVAL);
  assert.deepEqual(body.response_format, { type: "json_object" });
  assert.equal(body.messages.length, 1); // no system for evaluator
});

test("openai parseResponse: usage and stop reason normalized", () => {
  const { text, usage, stop_reason } = openai.parseResponse({
    choices: [{ message: { content: "reply" }, finish_reason: "length" }],
    usage: { prompt_tokens: 1000, completion_tokens: 50, prompt_tokens_details: { cached_tokens: 800 } },
  });
  assert.equal(text, "reply");
  assert.deepEqual(usage, { input_tokens: 200, output_tokens: 50, cache_read_input_tokens: 800 });
  assert.equal(stop_reason, "max_tokens");
});

// ---- Google ------------------------------------------------------------------

test("google chat: systemInstruction, role mapping, maxOutputTokens, header auth", () => {
  const { url, headers, body } = google.buildRequest(CHAT);
  assert.equal(url, "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent");
  // Key travels in the header, NEVER in the URL (keys in URLs leak into logs).
  assert.equal(headers["x-goog-api-key"], "sk-test-abc");
  assert.ok(!url.includes("sk-test-abc"));
  assert.equal(body.systemInstruction.parts[0].text, "SEGMENT-A-TEXT\n\nPERSONA-TAIL");
  assert.deepEqual(body.contents.map((c) => c.role), ["user", "model", "user"]);
  assert.equal(body.contents[2].parts[0].text, "Tell me what happened.");
  assert.equal(body.generationConfig.maxOutputTokens, 300);
  assert.equal(body.generationConfig.responseMimeType, undefined);
});

test("google jsonMode: responseMimeType application/json", () => {
  const { body } = google.buildRequest(EVAL);
  assert.equal(body.generationConfig.responseMimeType, "application/json");
  assert.equal(body.systemInstruction, undefined);
});

test("google parseResponse: usage, thought tokens, and stop reason normalized", () => {
  const { text, usage, stop_reason } = google.parseResponse({
    candidates: [{
      finishReason: "MAX_TOKENS",
      content: { parts: [{ text: "re" }, { text: "ply" }] },
    }],
    usageMetadata: {
      promptTokenCount: 900,
      candidatesTokenCount: 40,
      cachedContentTokenCount: 100,
      thoughtsTokenCount: 25,
    },
  });
  assert.equal(text, "reply");
  assert.deepEqual(usage, {
    input_tokens: 800,
    output_tokens: 40,
    cache_read_input_tokens: 100,
    thought_tokens: 25,
  });
  assert.equal(stop_reason, "max_tokens");
});

// ---- shared ------------------------------------------------------------------

test("systemToString joins prefix+tail exactly like the anthropic block layout", () => {
  assert.equal(systemToString(CHAT.system), "SEGMENT-A-TEXT\n\nPERSONA-TAIL");
  assert.equal(systemToString("plain"), "plain");
  assert.equal(systemToString(null), null);
});

test("provider completions carry a bounded abort signal", async () => {
  const originalFetch = globalThis.fetch;
  let capturedSignal;
  globalThis.fetch = async (_url, init) => {
    capturedSignal = init.signal;
    return new Response(JSON.stringify({ answer: "ok" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    const result = await completeWithRetry(
      () => ({ url: "https://provider.example.test", headers: {}, body: {} }),
      (data) => ({ text: data.answer, usage: {}, stop_reason: "max_tokens" })
    );
    assert.equal(result.ok, true);
    assert.equal(result.stop_reason, "max_tokens");
    assert.ok(capturedSignal instanceof AbortSignal);
    assert.equal(PROVIDER_TIMEOUT_MS, 60_000);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
