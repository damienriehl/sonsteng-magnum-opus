// chat-stream.test.js — provider-neutral SSE streaming for POST /v1/chat.
//
// The live streaming path can't be smoke-tested here (BYOK-forever: no live
// provider key in the repo/CI), so these tests drive the TransformStream with
// fixture provider SSE and assert the three things the streaming path must
// guarantee vs. the non-streaming path:
//   1. chunk relay      — every text_delta reaches the client, in order.
//   2. usage capture     — token usage is merged from message_start (inputs +
//                          cache) AND the terminal message_delta (final output),
//                          into the SAME Anthropic-named object cost.js bills.
//   3. bookkeeping parity — the settled usage + assembled payload are identical
//                          to what the non-streaming parseResponse()/settle path
//                          produces for the SAME logical response.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  streamingEnabled,
  supportsStreaming,
  parseSSEFrame,
  sseFrame,
  makeChatTransform,
  pipeProviderStream,
  startProviderStream,
} from "../src/chat-stream.js";
import * as anthropic from "../src/providers/anthropic.js";
import { getProvider } from "../src/providers/registry.js";
import { centsForUsage } from "../src/cost.js";

// ---- shared fixture ---------------------------------------------------------
// One logical Anthropic reply, expressed BOTH as an SSE stream (streaming path)
// and as the equivalent single JSON body (non-streaming path), so we can assert
// the two paths agree.
const REPLY_CHUNKS = ["Well, ", "counselor, ", "here is ", "what happened."];
const REPLY_TEXT = REPLY_CHUNKS.join("");
const START_USAGE = { input_tokens: 12, cache_read_input_tokens: 4096, cache_creation_input_tokens: 0, output_tokens: 1 };
const FINAL_OUTPUT_TOKENS = 25;

// The merged usage the streaming path should settle with = message_start inputs
// + message_delta's final cumulative output_tokens.
const EXPECTED_USAGE = {
  input_tokens: 12,
  cache_read_input_tokens: 4096,
  cache_creation_input_tokens: 0,
  output_tokens: FINAL_OUTPUT_TOKENS,
};

// The equivalent non-streaming Anthropic JSON body.
const NONSTREAM_JSON = {
  content: [{ type: "text", text: REPLY_TEXT }],
  usage: EXPECTED_USAGE,
};

function anthropicSSE({ startUsage = START_USAGE, chunks = REPLY_CHUNKS, finalOutput = FINAL_OUTPUT_TOKENS, includeError = false, complete = true } = {}) {
  const frames = [];
  const raw = (event, obj) => frames.push(`event: ${event}\ndata: ${JSON.stringify(obj)}\n\n`);
  raw("message_start", { type: "message_start", message: { role: "assistant", usage: startUsage } });
  raw("content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } });
  frames.push(":ping keep-alive\n\n"); // comment frame — must be ignored
  for (const t of chunks) raw("content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: t } });
  raw("content_block_stop", { type: "content_block_stop", index: 0 });
  raw("message_delta", { type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { output_tokens: finalOutput } });
  if (includeError) raw("error", { type: "error", error: { type: "overloaded_error", message: "Overloaded" } });
  if (complete) raw("message_stop", { type: "message_stop" });
  return frames.join("");
}

function openaiSSE({ chunks = REPLY_CHUNKS, includeError = false, malformed = false, complete = true } = {}) {
  const frames = [];
  const raw = (obj) => frames.push(`data: ${JSON.stringify(obj)}\n\n`);
  raw({ id: "chatcmpl-test", choices: [{ index: 0, delta: { role: "assistant", content: "" } }] });
  for (const text of chunks) raw({ id: "chatcmpl-test", choices: [{ index: 0, delta: { content: text } }] });
  if (malformed) frames.push("data: {not-json}\n\n");
  if (includeError) raw({ error: { type: "server_error", message: "Overloaded" } });
  raw({ id: "chatcmpl-test", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
  raw({ id: "chatcmpl-test", choices: [], usage: {
    prompt_tokens: 1012,
    completion_tokens: FINAL_OUTPUT_TOKENS,
    prompt_tokens_details: { cached_tokens: 1000 },
  } });
  if (complete) frames.push("data: [DONE]\n\n");
  return frames.join("");
}

function googleSSE({ chunks = REPLY_CHUNKS, includeError = false, malformed = false, complete = true } = {}) {
  const frames = [];
  const raw = (obj) => frames.push(`data: ${JSON.stringify(obj)}\n\n`);
  for (const text of chunks) raw({ candidates: [{ content: { role: "model", parts: [{ text }] } }] });
  if (malformed) frames.push("data: {not-json}\n\n");
  if (includeError) raw({ error: { code: 503, status: "UNAVAILABLE", message: "Overloaded" } });
  if (complete) {
    raw({ candidates: [{ content: { role: "model", parts: [] }, finishReason: "STOP" }], usageMetadata: {
      promptTokenCount: 1012,
      candidatesTokenCount: FINAL_OUTPUT_TOKENS,
      cachedContentTokenCount: 1000,
    } });
  }
  return frames.join("");
}

const PROVIDER_CASES = {
  anthropic: {
    stream: anthropicSSE,
    nonStream: NONSTREAM_JSON,
  },
  openai: {
    stream: openaiSSE,
    nonStream: {
      choices: [{ message: { content: REPLY_TEXT } }],
      usage: {
        prompt_tokens: 1012,
        completion_tokens: FINAL_OUTPUT_TOKENS,
        prompt_tokens_details: { cached_tokens: 1000 },
      },
    },
  },
  google: {
    stream: googleSSE,
    nonStream: {
      candidates: [{ content: { parts: [{ text: REPLY_TEXT }] } }],
      usageMetadata: {
        promptTokenCount: 1012,
        candidatesTokenCount: FINAL_OUTPUT_TOKENS,
        cachedContentTokenCount: 1000,
      },
    },
  },
};

// Router-equivalent payload builder (mirrors index.js::buildPayload with fixed
// turn/remaining/state so we can assert the exact done payload).
function buildDonePayload(fullText, usage) {
  return {
    reply: fullText,
    turn: 3,
    remaining: 17,
    state: "active",
    usage: {
      input_tokens: usage.input_tokens || 0,
      output_tokens: usage.output_tokens || 0,
      cache_read_input_tokens: usage.cache_read_input_tokens || 0,
    },
  };
}

// Feed `sseText` through makeChatTransform in `chunkSize`-byte slices; collect
// the client-visible SSE and the onSettle arguments.
async function runTransform(sseText, { provider = "anthropic", chunkSize, onSettleExtra, onFailureExtra } = {}) {
  const enc = new TextEncoder();
  const bytes = enc.encode(sseText);
  const size = chunkSize || bytes.length;
  const slices = [];
  for (let i = 0; i < bytes.length; i += size) slices.push(bytes.slice(i, i + size));

  let settle = null;
  let failure = null;
  const transform = makeChatTransform({
    provider,
    buildDonePayload,
    onSettle: async (payload, usage, fullText) => {
      settle = { payload, usage: { ...usage }, fullText };
      if (onSettleExtra) await onSettleExtra();
    },
    onFailure: async (usage, fullText) => {
      failure = { usage: { ...usage }, fullText };
      if (onFailureExtra) await onFailureExtra();
    },
  });

  const readable = new ReadableStream({
    start(c) { for (const s of slices) c.enqueue(s); c.close(); },
  });
  const reader = readable.pipeThrough(transform).getReader();
  const dec = new TextDecoder();
  let clientText = "";
  for (;;) {
    const r = await reader.read();
    if (r.done) break;
    clientText += dec.decode(r.value, { stream: true });
  }
  clientText += dec.decode();

  // Parse the client-visible frames back into {event,data} objects.
  const events = [];
  for (const chunk of clientText.split("\n\n")) {
    if (!chunk.trim()) continue;
    const f = parseSSEFrame(chunk);
    if (f) events.push({ event: f.event, data: JSON.parse(f.data) });
  }
  return { clientText, events, settle, failure };
}

// ---- the flag ---------------------------------------------------------------
test("streamingEnabled: ONLY the exact string 'true' opts in (default OFF)", () => {
  assert.equal(streamingEnabled({ STREAMING: "true" }), true);
  assert.equal(streamingEnabled({ STREAMING: "false" }), false);
  assert.equal(streamingEnabled({ STREAMING: "1" }), false);
  assert.equal(streamingEnabled({ STREAMING: "TRUE" }), false);
  assert.equal(streamingEnabled({}), false);
  assert.equal(streamingEnabled(undefined), false);
});

test("supportsStreaming: every configured BYOK provider streams; unknown providers fall back", () => {
  assert.equal(supportsStreaming("anthropic"), true);
  assert.equal(supportsStreaming("openai"), true);
  assert.equal(supportsStreaming("google"), true);
  assert.equal(supportsStreaming("mistral"), false);
  assert.equal(supportsStreaming(undefined), false);
});

// ---- SSE frame parser -------------------------------------------------------
test("parseSSEFrame: event + single data", () => {
  const f = parseSSEFrame("event: delta\ndata: {\"text\":\"hi\"}");
  assert.deepEqual(f, { event: "delta", data: '{"text":"hi"}' });
});

test("parseSSEFrame: multi-line data joins with newline; comments/blank ignored; default event", () => {
  const f = parseSSEFrame(":comment\ndata: line1\ndata: line2");
  assert.equal(f.event, "message");
  assert.equal(f.data, "line1\nline2");
});

test("parseSSEFrame: a frame with no data field is null", () => {
  assert.equal(parseSSEFrame(":only a comment"), null);
  assert.equal(parseSSEFrame("event: ping"), null);
});

test("sseFrame encodes event + JSON data with a blank-line terminator", () => {
  assert.equal(sseFrame("delta", { text: "x" }), 'event: delta\ndata: {"text":"x"}\n\n');
});

// ---- relay + usage capture --------------------------------------------------
test("transform relays every text_delta to the client, in order", async () => {
  const { events } = await runTransform(anthropicSSE());
  const deltas = events.filter((e) => e.event === "delta").map((e) => e.data.text);
  assert.deepEqual(deltas, REPLY_CHUNKS);
});

test("transform emits a terminal done frame with the full payload", async () => {
  const { events } = await runTransform(anthropicSSE());
  const done = events.filter((e) => e.event === "done");
  assert.equal(done.length, 1);
  assert.deepEqual(done[0].data, {
    reply: REPLY_TEXT,
    turn: 3,
    remaining: 17,
    state: "active",
    usage: { input_tokens: 12, output_tokens: 25, cache_read_input_tokens: 4096 },
  });
});

test("transform captures usage merged from message_start + message_delta", async () => {
  const { settle } = await runTransform(anthropicSSE());
  assert.deepEqual(settle.usage, EXPECTED_USAGE);
  assert.equal(settle.fullText, REPLY_TEXT);
});

test("onSettle fires exactly once, in flush, with the built payload", async () => {
  let calls = 0;
  const { settle } = await runTransform(anthropicSSE(), { onSettleExtra: () => { calls++; } });
  assert.equal(calls, 1);
  assert.equal(settle.payload.reply, REPLY_TEXT);
});

// ---- chunk-boundary robustness ---------------------------------------------
for (const chunkSize of [1, 3, 7, 16, 64]) {
  test(`transform is byte-boundary agnostic (chunkSize=${chunkSize})`, async () => {
    const { events, settle } = await runTransform(anthropicSSE(), { chunkSize });
    const deltas = events.filter((e) => e.event === "delta").map((e) => e.data.text).join("");
    assert.equal(deltas, REPLY_TEXT);
    assert.deepEqual(settle.usage, EXPECTED_USAGE);
    assert.equal(settle.fullText, REPLY_TEXT);
  });
}

// ---- bookkeeping parity with the non-streaming path -------------------------
test("PARITY: streamed text + usage equal the non-streaming parseResponse output", async () => {
  const { settle } = await runTransform(anthropicSSE());
  const nonStream = anthropic.parseResponse(NONSTREAM_JSON);

  // Same assistant text.
  assert.equal(settle.fullText, nonStream.text);
  // Same billing-relevant usage fields (so cost.js bills identically).
  for (const k of ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]) {
    assert.equal((settle.usage[k] || 0), (nonStream.usage[k] || 0), `usage.${k} parity`);
  }
  // And therefore the SAME cents settle against the budget.
  assert.equal(centsForUsage(settle.usage), centsForUsage(nonStream.usage));
});

test("PARITY: the cache-read signal (worker-llm-facts §2) survives streaming", async () => {
  const { settle, events } = await runTransform(anthropicSSE());
  assert.ok(settle.usage.cache_read_input_tokens > 0, "cache read captured server-side");
  const done = events.find((e) => e.event === "done");
  assert.equal(done.data.usage.cache_read_input_tokens, 4096);
});

for (const [provider, fixture] of Object.entries(PROVIDER_CASES)) {
  test(`${provider}: happy path relays output and settles canonical usage with cost parity`, async () => {
    const { events, settle } = await runTransform(fixture.stream(), { provider });
    assert.equal(events.filter((e) => e.event === "delta").map((e) => e.data.text).join(""), REPLY_TEXT);
    assert.equal(events.filter((e) => e.event === "done").length, 1);

    const nonStream = getProvider(provider).parseResponse(fixture.nonStream);
    assert.equal(settle.fullText, nonStream.text);
    assert.deepEqual(settle.usage, nonStream.usage);
    assert.equal(centsForUsage(settle.usage), centsForUsage(nonStream.usage));
    assert.equal(settle.payload.reply, nonStream.text);
    assert.deepEqual(settle.payload.usage, {
      input_tokens: nonStream.usage.input_tokens || 0,
      output_tokens: nonStream.usage.output_tokens || 0,
      cache_read_input_tokens: nonStream.usage.cache_read_input_tokens || 0,
    });
  });

  test(`${provider}: provider error reaches the client without a successful terminal frame`, async () => {
    const { events, settle, failure } = await runTransform(fixture.stream({ includeError: true }), { provider });
    assert.equal(events.find((e) => e.event === "error").data.message, "Overloaded");
    assert.equal(events.filter((e) => e.event === "done").length, 0);
    assert.equal(settle, null, "failed partial output is never stored as a replay result");
    assert.equal(failure.fullText, REPLY_TEXT);
  });

  test(`${provider}: clean truncation fails without done, settle, or synthetic error`, async () => {
    const { events, settle, failure } = await runTransform(fixture.stream({ complete: false }), { provider });
    assert.equal(events.filter((e) => e.event === "done").length, 0);
    assert.equal(events.filter((e) => e.event === "error").length, 0);
    assert.equal(settle, null);
    assert.ok(failure, "failure finalizer was called");
    assert.equal(failure.fullText, REPLY_TEXT);
  });

  test(`${provider}: malformed frames are ignored and terminal usage still settles`, async () => {
    const stream = provider === "anthropic"
      ? fixture.stream() + "data: {not-json}\n\n"
      : fixture.stream({ malformed: true });
    const { events, settle } = await runTransform(stream, { provider });
    assert.equal(events.filter((e) => e.event === "done").length, 1);
    assert.equal(settle.fullText, REPLY_TEXT);
    assert.deepEqual(settle.usage, getProvider(provider).parseResponse(fixture.nonStream).usage);
  });
}

// ---- mid-stream error -------------------------------------------------------
test("transform forwards an upstream error and finalizes failure before closing", async () => {
  let finalized = false;
  const { events, settle, failure } = await runTransform(anthropicSSE({ includeError: true }), {
    onFailureExtra: async () => { finalized = true; },
  });
  const err = events.find((e) => e.event === "error");
  assert.ok(err, "client sees an error frame");
  assert.equal(err.data.message, "Overloaded");
  assert.equal(events.some((e) => e.event === "done"), false);
  assert.equal(settle, null);
  assert.equal(failure.fullText, REPLY_TEXT);
  assert.deepEqual(failure.usage, EXPECTED_USAGE);
  assert.equal(finalized, true, "failure state clears before the client stream closes");
});

test("transport abort clears the in-flight turn before the client read rejects", async () => {
  const enc = new TextEncoder();
  const transportError = new Error("socket reset");
  let inFlightTurn = true;
  let failureCalls = 0;
  let signalFailureStarted;
  const failureStarted = new Promise((resolve) => { signalFailureStarted = resolve; });
  let releaseFailure;
  const failureGate = new Promise((resolve) => { releaseFailure = resolve; });

  let upstreamPulls = 0;
  const upstreamBody = new ReadableStream({
    pull(controller) {
      if (upstreamPulls++ === 0) {
        controller.enqueue(enc.encode(
          'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"partial"}}\n\n'
        ));
      } else {
        controller.error(transportError);
      }
    },
  });
  const transform = makeChatTransform({
    provider: "anthropic",
    buildDonePayload,
    onSettle: async () => assert.fail("transport failures must not settle"),
  });
  const clientStream = pipeProviderStream({
    upstreamBody,
    transform,
    onFailure: async () => {
      failureCalls++;
      signalFailureStarted();
      await failureGate;
      inFlightTurn = false;
    },
  });
  const reader = clientStream.getReader();
  const first = await reader.read();
  assert.match(new TextDecoder().decode(first.value), /event: delta/);

  const terminalRead = reader.read();
  await failureStarted;
  assert.equal(inFlightTurn, true, "client is still waiting while failure state is in flight");
  releaseFailure();
  await assert.rejects(terminalRead, /socket reset/);
  assert.equal(failureCalls, 1);
  assert.equal(inFlightTurn, false, "same turn_id is retryable when the client observes failure");
});

// ---- startProviderStream (fetch injection) ---------------------------------
test("startProviderStream: Anthropic 200 with a body -> ok, hands back the upstream", async () => {
  const fake = { ok: true, status: 200, body: new ReadableStream({ start(c) { c.close(); } }) };
  const res = await startProviderStream(
    { up: { provider: "anthropic", apiKey: "sk-x", model: "claude-haiku-4-5" }, system: { prefix: "A", tail: "B" }, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
    async (url, init) => {
      // it must ask the provider to stream + carry the key + target the messages API
      assert.equal(url, "https://api.anthropic.com/v1/messages");
      assert.equal(init.headers["x-api-key"], "sk-x");
      assert.equal(JSON.parse(init.body).stream, true);
      return fake;
    }
  );
  assert.equal(res.ok, true);
  assert.equal(res.upstream, fake);
});

test("startProviderStream: Anthropic 4xx -> config error (BYOK key/model rejected, no retry)", async () => {
  const res = await startProviderStream(
    { up: { provider: "anthropic", apiKey: "sk-x", model: "m" }, system: null, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
    async () => ({ ok: false, status: 401, body: null })
  );
  assert.deepEqual(res, { ok: false, kind: "config", status: 401 });
});

test("startProviderStream: Anthropic 5xx -> upstream error", async () => {
  const res = await startProviderStream(
    { up: { provider: "anthropic", apiKey: "sk-x", model: "m" }, system: null, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
    async () => ({ ok: false, status: 529, body: null })
  );
  assert.deepEqual(res, { ok: false, kind: "upstream", status: 529 });
});

test("startProviderStream: Anthropic 429 -> upstream error (not treated as a config bug)", async () => {
  const res = await startProviderStream(
    { up: { provider: "anthropic", apiKey: "sk-x", model: "m" }, system: null, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
    async () => ({ ok: false, status: 429, body: null })
  );
  assert.deepEqual(res, { ok: false, kind: "upstream", status: 429 });
});

test("startProviderStream: Anthropic fetch throw -> upstream error", async () => {
  const res = await startProviderStream(
    { up: { provider: "anthropic", apiKey: "sk-x", model: "m" }, system: null, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
    async () => { throw new Error("network down"); }
  );
  assert.deepEqual(res, { ok: false, kind: "upstream" });
});

test("startProviderStream: Anthropic 200 but no body -> upstream error", async () => {
  const res = await startProviderStream(
    { up: { provider: "anthropic", apiKey: "sk-x", model: "m" }, system: null, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
    async () => ({ ok: true, status: 200, body: null })
  );
  assert.deepEqual(res, { ok: false, kind: "upstream", status: 200 });
});

for (const [provider, expected] of Object.entries({
  anthropic: { url: "https://api.anthropic.com/v1/messages", authHeader: "x-api-key" },
  openai: { url: "https://api.openai.com/v1/chat/completions", authHeader: "authorization" },
  google: { url: "https://generativelanguage.googleapis.com/v1beta/models/test-model:streamGenerateContent?alt=sse", authHeader: "x-goog-api-key" },
})) {
  test(`startProviderStream: ${provider} builds its streaming request without exposing the key`, async () => {
    const fake = { ok: true, status: 200, body: new ReadableStream({ start(c) { c.close(); } }) };
    const result = await startProviderStream(
      { up: { provider, apiKey: "secret-test-key", model: "test-model" }, system: { prefix: "A", tail: "B" }, messages: [{ role: "user", content: "hi" }], maxTokens: 300 },
      async (url, init) => {
        assert.equal(url, expected.url);
        assert.ok(init.headers[expected.authHeader]);
        assert.ok(!url.includes("secret-test-key"));
        const body = JSON.parse(init.body);
        if (provider === "anthropic") assert.equal(body.stream, true);
        if (provider === "openai") {
          assert.equal(body.stream, true);
          assert.deepEqual(body.stream_options, { include_usage: true });
        }
        if (provider === "google") assert.equal(body.stream, undefined);
        return fake;
      },
    );
    assert.equal(result.ok, true);
    assert.equal(result.upstream, fake);
  });
}

test("startProviderStream: unknown provider declines without calling fetch", async () => {
  let called = false;
  const result = await startProviderStream(
    { up: { provider: "mistral", apiKey: "secret-test-key", model: "m" }, system: null, messages: [], maxTokens: 300 },
    async () => { called = true; },
  );
  assert.equal(called, false);
  assert.deepEqual(result, { ok: false, kind: "config" });
});
