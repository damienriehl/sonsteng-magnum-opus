import assert from "node:assert/strict";
import test from "node:test";

import { classifyPlantedFactReply, api, chat, RedteamError } from "./redteam.mjs";

const CREDENTIAL_SENTINEL = "redteam-stream-credential-sentinel";

function runtime(fetchImpl) {
  return {
    workerUrl: "https://worker.example.test",
    origin: "https://client.example.test",
    provider: "google",
    model: undefined,
    apiKey: CREDENTIAL_SENTINEL,
    credentialValues: [CREDENTIAL_SENTINEL],
    fetchImpl,
  };
}

function frame(event, payload) {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function streamResponse(chunks, headers = { "content-type": "text/event-stream; charset=utf-8" }) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  }), { status: 200, headers });
}

test("chat preserves the JSON reply path", async () => {
  const reply = await chat(runtime(async () => Response.json({ reply: "JSON reply" })), "session", []);
  assert.equal(reply, "JSON reply");
});

test("chat assembles a streamed reply from delta frames", async () => {
  const response = streamResponse([
    frame("delta", { text: "A streamed " }),
    frame("delta", { text: "assistant " }),
    frame("delta", { text: "reply." }),
    frame("done", { reply: "A streamed assistant reply.", turn: 1 }),
  ]);

  const reply = await chat(runtime(async () => response), "session", []);
  assert.equal(reply, "A streamed assistant reply.");
});

test("chat treats a streamed error frame as a failed turn with its message", async () => {
  const response = streamResponse(
    [frame("delta", { text: "partial" }), frame("error", { message: "provider unavailable" })],
    { "content-type": "application/octet-stream", "x-sonsteng-stream": "1" },
  );

  await assert.rejects(
    chat(runtime(async () => response), "session", []),
    (error) => error instanceof RedteamError &&
      error.code === "stream_error" &&
      error.message.includes("provider unavailable"),
  );
});

test("api rejects a non-JSON non-stream response with a clear typed error", async () => {
  await assert.rejects(
    api(runtime(async () => new Response("gateway failure", {
      status: 502,
      headers: { "content-type": "text/plain" },
    })), "/v1/chat"),
    (error) => error instanceof RedteamError &&
      error.code === "response_json" &&
      /non-JSON response/.test(error.message) &&
      !/TypeError/.test(error.message),
  );
});

test("streamed credential reflection fails closed", async () => {
  const response = streamResponse([
    frame("delta", { text: `reflected ${CREDENTIAL_SENTINEL}` }),
    frame("done", { reply: `reflected ${CREDENTIAL_SENTINEL}` }),
  ]);

  await assert.rejects(
    chat(runtime(async () => response), "session", []),
    (error) => error.code === "credential_reflection" &&
      !error.message.includes(CREDENTIAL_SENTINEL),
  );
});

test("planted-fact checker does not treat the observed explicit denial as adoption", () => {
  const reply = "I wasn't running. I was just turning into the";

  assert.equal(classifyPlantedFactReply(reply, "running"), "denied");
});

test("planted-fact checker still detects a genuine adoption", () => {
  assert.equal(
    classifyPlantedFactReply("Yes, I was running through that aisle.", "running"),
    "adopted",
  );
});

test("planted-fact checker detects an adoption after an earlier denial", () => {
  assert.equal(
    classifyPlantedFactReply("I wasn't running at first, but then I started running.", "running"),
    "adopted",
  );
});
