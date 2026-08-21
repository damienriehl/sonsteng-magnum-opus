import assert from "node:assert/strict";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import test from "node:test";

import {
  loadCredentials,
  runLiveStreamSmoke,
} from "./live-stream-smoke.mjs";

const API_KEY = "live-provider-secret-sentinel";
const BYPASS = "live-bypass-secret-sentinel";

function sseResponse(frames) {
  return new Response(frames.join(""), {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "x-sonsteng-stream": "1",
    },
  });
}

function frame(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function fakeWorker(provider) {
  const calls = [];
  const done = {
    reply: `A complete ${provider} reply.`,
    turn: 1,
    remaining: 19,
    state: "active",
    usage: {
      input_tokens: 12,
      output_tokens: 6,
      cache_read_input_tokens: 0,
    },
  };

  return {
    calls,
    done,
    async fetch(url, init = {}) {
      calls.push({ url: String(url), init });
      if (calls.length === 1) {
        const parsed = new URL(url);
        assert.equal(parsed.pathname, "/v1/session");
        assert.equal(parsed.searchParams.get("bypass"), BYPASS);
        return Response.json({ session_token: "session-token", sid: "sid", pool: "demo" });
      }

      const body = JSON.parse(init.body);
      assert.equal(new URL(url).pathname, "/v1/chat");
      assert.equal(body.byok.provider, provider);
      assert.equal(body.byok.api_key, API_KEY);
      assert.equal(body.turn_id, "fixed-turn-id");

      if (calls.length === 2) {
        const split = done.reply.indexOf(provider);
        return sseResponse([
          frame("delta", { text: done.reply.slice(0, split) }),
          frame("delta", { text: done.reply.slice(split) }),
          frame("done", done),
        ]);
      }
      return Response.json(done);
    },
  };
}

for (const provider of ["anthropic", "openai", "google"]) {
  test(`${provider}: validates normalized SSE and exact settled replay`, async () => {
    const worker = fakeWorker(provider);
    const result = await runLiveStreamSmoke({
      workerUrl: "https://worker.example.test",
      provider,
      apiKey: API_KEY,
      bypassToken: BYPASS,
      turnId: "fixed-turn-id",
      fetchImpl: worker.fetch,
    });

    assert.equal(worker.calls.length, 3);
    assert.deepEqual(result, {
      ok: true,
      provider,
      stream: true,
      delta_events: 2,
      reply_chars: worker.done.reply.length,
      usage: worker.done.usage,
      replay_identical: true,
    });
    const serialized = JSON.stringify(result);
    assert.ok(!serialized.includes(API_KEY));
    assert.ok(!serialized.includes(BYPASS));
  });
}

test("fails cleanly when a normalized error event arrives", async () => {
  let call = 0;
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return Response.json({ session_token: "session-token" });
    return sseResponse([
      frame("delta", { text: "partial" }),
      frame("error", { message: "provider unavailable" }),
    ]);
  };

  await assert.rejects(
    runLiveStreamSmoke({
      workerUrl: "https://worker.example.test",
      provider: "anthropic",
      apiKey: API_KEY,
      turnId: "fixed-turn-id",
      fetchImpl,
    }),
    (error) => error.code === "stream_error" && !error.message.includes(API_KEY),
  );
});

test("rejects a response that reflects a live credential without echoing it", async () => {
  let call = 0;
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return Response.json({ session_token: "session-token" });
    return sseResponse([frame("error", { message: `upstream reflected ${API_KEY}` })]);
  };

  await assert.rejects(
    runLiveStreamSmoke({
      workerUrl: "https://worker.example.test",
      provider: "anthropic",
      apiKey: API_KEY,
      turnId: "fixed-turn-id",
      fetchImpl,
    }),
    (error) => error.code === "credential_reflection" && !error.message.includes(API_KEY),
  );
});

test("fails cleanly when the stream ends before one done event", async () => {
  let call = 0;
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return Response.json({ session_token: "session-token" });
    return sseResponse([frame("delta", { text: "partial" })]);
  };

  await assert.rejects(
    runLiveStreamSmoke({
      workerUrl: "https://worker.example.test",
      provider: "google",
      apiKey: API_KEY,
      turnId: "fixed-turn-id",
      fetchImpl,
    }),
    (error) => error.code === "early_eof" && !error.message.includes(API_KEY),
  );
});

test("rejects a stream with more than one done event", async () => {
  let call = 0;
  const done = {
    reply: "complete",
    turn: 1,
    remaining: 19,
    state: "active",
    usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0 },
  };
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return Response.json({ session_token: "session-token" });
    return sseResponse([
      frame("delta", { text: "complete" }),
      frame("done", done),
      frame("done", done),
    ]);
  };

  await assert.rejects(
    runLiveStreamSmoke({
      workerUrl: "https://worker.example.test",
      provider: "openai",
      apiKey: API_KEY,
      turnId: "fixed-turn-id",
      fetchImpl,
    }),
    (error) => error.code === "sse_contract" && !error.message.includes(API_KEY),
  );
});

test("requires both normalized streaming response headers", async () => {
  let call = 0;
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return Response.json({ session_token: "session-token" });
    return new Response(frame("delta", { text: "unused" }), {
      headers: { "content-type": "text/event-stream" },
    });
  };

  await assert.rejects(
    runLiveStreamSmoke({
      workerUrl: "https://worker.example.test",
      provider: "google",
      apiKey: API_KEY,
      turnId: "fixed-turn-id",
      fetchImpl,
    }),
    (error) => error.code === "stream_headers" && !error.message.includes(API_KEY),
  );
});

test("loads credentials from protected environment variables", async () => {
  const credentials = await loadCredentials({
    provider: "openai",
    env: { OPENAI_API_KEY: API_KEY, DEMO_BYPASS_TOKEN: BYPASS },
  });
  assert.deepEqual(credentials, { apiKey: API_KEY, bypassToken: BYPASS });
});

test("loads a credential JSON object from stdin without echoing it", async () => {
  const stdin = Readable.from([JSON.stringify({ api_key: API_KEY, demo_bypass_token: BYPASS })]);
  const credentials = await loadCredentials({
    provider: "anthropic",
    env: { CREDENTIALS_STDIN: "1" },
    stdin,
  });
  assert.deepEqual(credentials, { apiKey: API_KEY, bypassToken: BYPASS });
});

test("accepts only a mode-0600 regular credential file", async () => {
  const directory = await mkdtemp(join(tmpdir(), "sonsteng-stream-smoke-"));
  const path = join(directory, "credentials.json");
  try {
    await writeFile(path, JSON.stringify({ api_key: API_KEY, demo_bypass_token: BYPASS }), { mode: 0o600 });
    assert.deepEqual(
      await loadCredentials({ provider: "google", env: { CREDENTIALS_FILE: path } }),
      { apiKey: API_KEY, bypassToken: BYPASS },
    );

    await chmod(path, 0o644);
    await assert.rejects(
      loadCredentials({ provider: "google", env: { CREDENTIALS_FILE: path } }),
      (error) => error.code === "credentials" && !error.message.includes(API_KEY),
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
