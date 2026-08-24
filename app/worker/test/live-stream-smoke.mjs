#!/usr/bin/env node
// Credential-safe live verification for DEV chat streaming. This is a live
// smoke harness, not part of the Worker's request path and not a provider mock.

import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { open } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";

const PROVIDERS = new Set(["anthropic", "openai", "google"]);
export const DEFAULT_ORIGIN = "https://sonsteng-dev.damienriehl.com";
export const DEV_WORKER_ORIGIN = "https://sonsteng-chat.damienriehl.workers.dev";
const MATTER_ID = "m00";
const PERSONA_ID = "m00.per.tester";
const PROMPT = "In one or two sentences, please tell me what brought you in today.";
const MAX_STDIN_BYTES = 64 * 1024;
export const REQUEST_TIMEOUT_MS = 90_000;

export class SmokeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "SmokeError";
    this.code = code;
  }
}

function fail(code, message) {
  throw new SmokeError(code, message);
}

export function nonempty(value) {
  return typeof value === "string" && value.length > 0;
}

export function validateProvider(provider) {
  if (!PROVIDERS.has(provider)) {
    fail("config", "PROVIDER must be one of: anthropic, openai, google.");
  }
}

function parseCredentialJson(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    fail("credentials", "Credential input must be a JSON object.");
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object" || !nonempty(parsed.api_key)) {
    fail("credentials", "Credential input requires a non-empty api_key string.");
  }
  if (parsed.demo_bypass_token != null && typeof parsed.demo_bypass_token !== "string") {
    fail("credentials", "demo_bypass_token must be a string when supplied.");
  }
  return {
    apiKey: parsed.api_key,
    bypassToken: parsed.demo_bypass_token || "",
  };
}

async function readStdin(stdin) {
  let text = "";
  for await (const chunk of stdin) {
    text += chunk.toString();
    if (Buffer.byteLength(text) > MAX_STDIN_BYTES) {
      fail("credentials", "Credential input exceeds the 64 KiB limit.");
    }
  }
  return text;
}

/**
 * Resolve one credential source without ever including its contents in an
 * exception. Direct environment variables are intended to be loaded from a
 * protected environment file by the caller. File input must itself be 0600.
 */
export async function loadCredentials({
  provider,
  env = process.env,
  stdin = process.stdin,
  openImpl = open,
  allowDirectEnvironment = true,
} = {}) {
  validateProvider(provider);
  const providerVar = `${provider.toUpperCase()}_API_KEY`;
  const directCandidates = [env.API_KEY, env[providerVar]].filter(nonempty);
  if (directCandidates.length > 1) {
    fail("credentials", `Set only one of API_KEY or ${providerVar}.`);
  }

  const hasDirect = directCandidates.length === 1;
  const hasFile = nonempty(env.CREDENTIALS_FILE);
  const hasStdin = env.CREDENTIALS_STDIN === "1";
  if (hasDirect && !allowDirectEnvironment) {
    fail("credentials", "This command accepts credentials only from a mode-0600 file or protected standard input.");
  }
  if (Number(hasDirect) + Number(hasFile) + Number(hasStdin) !== 1) {
    fail("credentials", "Select exactly one credential source: environment, CREDENTIALS_FILE, or CREDENTIALS_STDIN=1.");
  }

  if (hasDirect) {
    return {
      apiKey: directCandidates[0],
      bypassToken: nonempty(env.DEMO_BYPASS_TOKEN) ? env.DEMO_BYPASS_TOKEN : "",
    };
  }

  if (hasFile) {
    let handle;
    try {
      handle = await openImpl(
        env.CREDENTIALS_FILE,
        constants.O_RDONLY | constants.O_NOFOLLOW,
      );
    } catch {
      fail("credentials", "Unable to inspect CREDENTIALS_FILE.");
    }
    try {
      const metadata = await handle.stat();
      const owned = typeof process.getuid !== "function" || metadata.uid === process.getuid();
      if (!metadata.isFile() || !owned || (metadata.mode & 0o777) !== 0o600) {
        fail("credentials", "CREDENTIALS_FILE must be an owned regular mode-0600 file.");
      }
      if (metadata.size > MAX_STDIN_BYTES) {
        fail("credentials", "Credential input exceeds the 64 KiB limit.");
      }
      const buffer = Buffer.alloc(MAX_STDIN_BYTES + 1);
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
      if (bytesRead > MAX_STDIN_BYTES) {
        fail("credentials", "Credential input exceeds the 64 KiB limit.");
      }
      return parseCredentialJson(buffer.subarray(0, bytesRead).toString("utf8"));
    } catch (error) {
      if (error instanceof SmokeError) throw error;
      fail("credentials", "Unable to read CREDENTIALS_FILE.");
    } finally {
      await handle.close().catch(() => {});
    }
  }

  return parseCredentialJson(await readStdin(stdin));
}

export function credentialValues(apiKey, bypassToken) {
  return [apiKey, bypassToken].filter(nonempty);
}

export function assertCredentialAbsent(value, secrets) {
  const serialized = typeof value === "string" ? value : JSON.stringify(value);
  for (const secret of secrets) {
    if (serialized.includes(secret)) {
      fail("credential_reflection", "A live credential appeared in a response or report surface.");
    }
  }
}

export function parseJsonResponse(text, code) {
  try {
    return JSON.parse(text);
  } catch {
    fail(code, "The Worker returned malformed JSON.");
  }
}

function parseFrame(frameText) {
  let event = "message";
  const data = [];
  for (const line of frameText.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    if (field === "data") data.push(value);
  }
  if (data.length === 0) fail("sse_contract", "An SSE frame had no data field.");
  let payload;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    fail("sse_contract", "An SSE frame contained malformed JSON.");
  }
  return { event, payload };
}

function normalizedUsage(usage) {
  if (!usage || Array.isArray(usage) || typeof usage !== "object") {
    fail("usage_contract", "The done event omitted normalized usage.");
  }
  const result = {};
  for (const key of ["input_tokens", "output_tokens", "cache_read_input_tokens"]) {
    if (!Number.isInteger(usage[key]) || usage[key] < 0) {
      fail("usage_contract", `Normalized usage.${key} must be a non-negative integer.`);
    }
    result[key] = usage[key];
  }
  return result;
}

function validateNormalizedFrames(frames) {
  let done = null;
  let doneCount = 0;
  let deltaCount = 0;
  let output = "";

  let firstDeltaRead = null;
  let doneRead = null;
  for (const { event, payload, readIndex } of frames) {
    if (event === "error") {
      fail("stream_error", "The normalized stream reported an upstream error.");
    }
    if (event === "delta") {
      if (doneCount > 0 || !payload || !nonempty(payload.text)) {
        fail("sse_contract", "A delta was empty or arrived after the terminal event.");
      }
      output += payload.text;
      deltaCount += 1;
      if (firstDeltaRead === null) firstDeltaRead = readIndex;
      continue;
    }
    if (event === "done") {
      done = payload;
      doneCount += 1;
      doneRead = readIndex;
      continue;
    }
    fail("sse_contract", "The stream contained a non-normalized event type.");
  }

  if (doneCount === 0) fail("early_eof", "The stream ended before its done event.");
  if (doneCount !== 1) fail("sse_contract", "The stream must contain exactly one done event.");
  if (deltaCount < 1) fail("sse_contract", "The stream must contain at least one delta event.");
  if (doneRead <= firstDeltaRead) {
    fail("stream_buffered", "The response buffered all deltas and the terminal event into one body read.");
  }
  if (!done || !nonempty(done.reply) || !nonempty(output)) {
    fail("sse_contract", "The normalized stream returned empty output.");
  }
  if (done.reply !== output) {
    fail("sse_contract", "The done reply did not match the concatenated deltas.");
  }
  if (!Number.isInteger(done.turn) || done.turn < 1 ||
      !Number.isInteger(done.remaining) || done.remaining < 0 ||
      !["active", "warning", "ended"].includes(done.state)) {
    fail("sse_contract", "The done event omitted canonical turn state.");
  }
  return { done, deltaCount, usage: normalizedUsage(done.usage) };
}

async function readNormalizedStream(response, secrets) {
  if (!response.body || typeof response.body.getReader !== "function") {
    fail("stream_transport", "The Worker response did not expose a readable stream body.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const rawParts = [];
  const frames = [];
  let pending = "";
  let readIndex = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      readIndex += 1;
      const text = decoder.decode(value, { stream: true });
      rawParts.push(text);
      assertCredentialAbsent(rawParts.join(""), secrets);
      pending = (pending + text).replace(/\r\n/g, "\n");
      let boundary;
      while ((boundary = pending.indexOf("\n\n")) !== -1) {
        const candidate = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        if (candidate.trim()) frames.push({ ...parseFrame(candidate), readIndex });
      }
    }
    const tail = decoder.decode();
    if (tail) {
      rawParts.push(tail);
      pending += tail;
      assertCredentialAbsent(rawParts.join(""), secrets);
    }
  } catch (error) {
    if (error instanceof SmokeError) throw error;
    fail("stream_transport", "The Worker response body ended unexpectedly.");
  }
  if (pending.trim()) fail("sse_contract", "The stream ended with an incomplete SSE frame.");
  return validateNormalizedFrames(frames);
}

export async function request(fetchImpl, url, init, code, timeoutMs = REQUEST_TIMEOUT_MS) {
  try {
    const response = await fetchImpl(url, {
      ...init,
      redirect: "manual",
      signal: init.signal || AbortSignal.timeout(timeoutMs),
    });
    if (response.status >= 300 && response.status < 400) {
      fail(code, "The Worker attempted to redirect a credential-bearing request.");
    }
    return response;
  } catch (error) {
    if (error instanceof SmokeError) throw error;
    fail(code, "The Worker request failed before a response arrived.");
  }
}

export async function responseText(response, code) {
  try {
    return await response.text();
  } catch {
    fail(code, "The Worker response body ended unexpectedly.");
  }
}

export function workerBaseUrl(workerUrl, { allowTestOrigin = false } = {}) {
  let parsed;
  try {
    parsed = new URL(workerUrl);
  } catch {
    fail("config", "WORKER_URL must be an absolute HTTPS URL.");
  }
  const local = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  if (parsed.protocol !== "https:" && !(allowTestOrigin && local)) {
    fail("config", "WORKER_URL must use HTTPS (except explicit localhost tests).");
  }
  if (parsed.origin !== DEV_WORKER_ORIGIN && !allowTestOrigin) {
    fail("config", `WORKER_URL must be the approved DEV Worker origin (${DEV_WORKER_ORIGIN}).`);
  }
  if (parsed.username || parsed.password) fail("config", "WORKER_URL must not contain credentials.");
  if (parsed.search || parsed.hash) fail("config", "WORKER_URL must not contain a query or fragment.");
  return parsed;
}

export function workerEndpoint(base, pathname) {
  const endpoint = new URL(base.href);
  const prefix = endpoint.pathname.replace(/\/+$/, "");
  endpoint.pathname = `${prefix}/${pathname.replace(/^\/+/, "")}`;
  return endpoint;
}

export async function runLiveStreamSmoke({
  workerUrl,
  provider,
  apiKey,
  bypassToken = "",
  model,
  origin = DEFAULT_ORIGIN,
  turnId = `stream-smoke-${randomUUID()}`,
  fetchImpl = fetch,
  allowTestOrigin = false,
}) {
  validateProvider(provider);
  if (!nonempty(apiKey)) fail("credentials", "A provider API key is required.");
  if (!nonempty(workerUrl)) fail("config", "WORKER_URL is required.");
  const base = workerBaseUrl(workerUrl, { allowTestOrigin });
  const secrets = credentialValues(apiKey, bypassToken);
  const headers = { Origin: origin };

  const sessionUrl = workerEndpoint(base, "/v1/session");
  if (bypassToken) sessionUrl.searchParams.set("bypass", bypassToken);
  const sessionResponse = await request(fetchImpl, sessionUrl, { method: "GET", headers }, "session_network");
  const sessionText = await responseText(sessionResponse, "session_transport");
  assertCredentialAbsent(sessionText, secrets);
  if (sessionResponse.status !== 200) {
    fail("session_http", `Session mint failed with HTTP ${sessionResponse.status}.`);
  }
  const session = parseJsonResponse(sessionText, "session_json");
  if (!nonempty(session.session_token)) fail("session_contract", "Session mint returned no session_token.");

  const transcript = [{ role: "user", content: PROMPT }];
  assertCredentialAbsent(transcript, secrets);
  const byok = { provider, api_key: apiKey };
  if (nonempty(model)) byok.model = model;
  const body = {
    session_token: session.session_token,
    matter_id: MATTER_ID,
    persona_id: PERSONA_ID,
    turn_id: turnId,
    messages: transcript,
    byok,
  };
  const chatUrl = workerEndpoint(base, "/v1/chat");
  const chatInit = {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify(body),
  };

  const streamResponse = await request(fetchImpl, chatUrl, chatInit, "chat_network");
  if (streamResponse.status !== 200) fail("chat_http", `Chat failed with HTTP ${streamResponse.status}.`);
  if (!/^text\/event-stream(?:;|$)/i.test(streamResponse.headers.get("content-type") || "")) {
    fail("stream_headers", "Chat did not return text/event-stream.");
  }
  if (streamResponse.headers.get("x-sonsteng-stream") !== "1") {
    fail("stream_headers", "Chat did not return x-sonsteng-stream: 1.");
  }
  const stream = await readNormalizedStream(streamResponse, secrets);
  assertCredentialAbsent(stream.done, secrets);

  const replayResponse = await request(fetchImpl, chatUrl, chatInit, "replay_network");
  const replayText = await responseText(replayResponse, "replay_transport");
  assertCredentialAbsent(replayText, secrets);
  if (replayResponse.status !== 200) fail("replay_http", `Replay failed with HTTP ${replayResponse.status}.`);
  if (!/^application\/json(?:;|$)/i.test(replayResponse.headers.get("content-type") || "")) {
    fail("replay_contract", "Settled same-turn replay did not return canonical JSON.");
  }
  const replay = parseJsonResponse(replayText, "replay_json");
  if (!isDeepStrictEqual(replay, stream.done)) {
    fail("replay_identity", "Settled same-turn replay did not match the streamed done payload.");
  }

  const report = {
    ok: true,
    provider,
    stream: true,
    delta_events: stream.deltaCount,
    reply_chars: stream.done.reply.length,
    usage: stream.usage,
    replay_identical: true,
  };
  assertCredentialAbsent(report, secrets);
  return report;
}

async function main() {
  const provider = process.env.PROVIDER || "";
  const credentials = await loadCredentials({ provider });
  return runLiveStreamSmoke({
    workerUrl: process.env.WORKER_URL || "",
    provider,
    apiKey: credentials.apiKey,
    bypassToken: credentials.bypassToken,
    model: process.env.MODEL || undefined,
    origin: process.env.ORIGIN || DEFAULT_ORIGIN,
  });
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === invokedPath) {
  main()
    .then((report) => console.log(JSON.stringify(report)))
    .catch((error) => {
      const code = error instanceof SmokeError ? error.code : "unexpected";
      const message = error instanceof SmokeError ? error.message : "Unexpected smoke-harness failure.";
      console.error(`live stream smoke failed [${code}]: ${message}`);
      process.exitCode = code === "config" || code === "credentials" ? 2 : 1;
    });
}
