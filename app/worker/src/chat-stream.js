// chat-stream.js — SSE streaming for POST /v1/chat, behind the STREAMING flag.
//
// WHY THIS EXISTS (docs/research/worker-llm-facts.md §4): non-streaming ships by
// default — a single JSON response lets the spend counter read `usage` once and
// keeps the Worker simple. Streaming is the promoted fast-follow: proxy the
// provider's SSE to the browser so tokens render as they arrive, WITHOUT losing
// any of the non-streaming path's bookkeeping.
//
// THE CONTRACT THIS MODULE PRESERVES (identical to the non-streaming path in
// index.js::handleChat):
//   * usage-accounting — each provider's terminal usage is normalized to the
//       same canonical input/output/cache fields its non-streaming adapter
//       returns, so bookkeeping and client payloads stay identical.
//   * caching — cache_read_input_tokens is captured + surfaced (the load-bearing
//       "cache worked" signal, worker-llm-facts §2), same as non-streaming.
//   * turn/replay bookkeeping — the router calls stub.settle(sid, usage, turnId,
//       payload) in the transform's flush with a payload of the SAME shape it
//       builds non-streaming, so turn_id replay idempotency is preserved.
//   * redaction — the CHAT path performs NO redaction (only /debrief runs
//       redactDebriefOracle, and /debrief is a one-shot JSON call that is NEVER
//       streamed — it needs the full parsed JSON to validate + redact). So there
//       is nothing to redact here; but we DO buffer the full assistant text
//       server-side (fullText) so the settle payload's `reply` matches what a
//       non-streaming turn would store for replay.
//
import { getProvider } from "./providers/registry.js";

// The flag. Default OFF everywhere (wrangler.jsonc vars: "STREAMING":"false").
export function streamingEnabled(env) {
  return !!env && env.STREAMING === "true";
}

// Which providers this module can stream. Router consults this before streaming.
export function supportsStreaming(provider) {
  return typeof getProvider(provider)?.buildStreamingRequest === "function";
}

// ---- SSE frame parsing (pure, unit-tested) ----------------------------------
// One SSE "frame" is the text between blank-line delimiters. Fields: `event:`
// sets the event name (default "message"); each `data:` line is collected and
// joined with "\n". Comment lines (starting ":") and blanks are ignored.
export function parseSSEFrame(frame) {
  let event = "message";
  const dataLines = [];
  for (const raw of frame.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    const ci = line.indexOf(":");
    const field = ci === -1 ? line : line.slice(0, ci);
    let val = ci === -1 ? "" : line.slice(ci + 1);
    if (val.startsWith(" ")) val = val.slice(1);
    if (field === "event") event = val;
    else if (field === "data") dataLines.push(val);
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

function safeJson(s) {
  try { return JSON.parse(s); } catch { return null; }
}

// Encode one normalized server->client SSE frame.
export function sseFrame(event, dataObj) {
  return `event: ${event}\ndata: ${JSON.stringify(dataObj)}\n\n`;
}

// ---- The TransformStream ----------------------------------------------------
// Consumes a provider's native SSE bytes on the writable side; emits a
// NORMALIZED, provider-agnostic SSE on the readable side so the browser never
// needs to know which provider produced the tokens:
//   event: delta   data: {"text":"<incremental chunk>"}
//   event: done    data: <the same final payload the non-streaming path returns>
//   event: error   data: {"message":"..."}   (only if the upstream errors mid-stream)
//
// buildDonePayload(fullText, usage) -> the final payload object (reply + turn +
//   remaining + state + usage), assembled by the router from data it already has
//   (turn/remaining/state) plus the streamed-in fullText + usage.
// onSettle(payload, usage, fullText) -> async; the router commits the DO here.
//   Runs in flush() so it fires exactly once, after the terminal usage is known.
export function makeChatTransform({ provider = "anthropic", buildDonePayload, onSettle }) {
  const dec = new TextDecoder();
  const enc = new TextEncoder();

  let buffer = "";
  let fullText = "";
  const usage = {};
  let sawError = false;
  const providerAdapter = getProvider(provider);

  function emitDelta(text, controller) {
    if (!text) return;
    fullText += text;
    controller.enqueue(enc.encode(sseFrame("delta", { text })));
  }

  function captureError(data, controller) {
    if (!data || !data.error) return false;
    sawError = true;
    const msg = data.error.message || "upstream stream error";
    controller.enqueue(enc.encode(sseFrame("error", { message: msg })));
    return true;
  }

  function handleFrame(frame, controller) {
    const parsed = parseSSEFrame(frame);
    if (!parsed) return;
    const data = safeJson(parsed.data);
    if (!data) return;

    if (captureError(data, controller)) return;

    if (provider === "anthropic") {
      // Anthropic delivers usage across message_start (inputs) + message_delta
      // (final output_tokens). Merge into the canonical usage object.
      if (data.type === "message_start" && data.message && data.message.usage) {
        const u = data.message.usage;
        if (typeof u.input_tokens === "number") usage.input_tokens = u.input_tokens;
        if (typeof u.output_tokens === "number") usage.output_tokens = u.output_tokens;
        if (typeof u.cache_read_input_tokens === "number") usage.cache_read_input_tokens = u.cache_read_input_tokens;
        if (typeof u.cache_creation_input_tokens === "number") usage.cache_creation_input_tokens = u.cache_creation_input_tokens;
      } else if (data.type === "content_block_delta" && data.delta && data.delta.type === "text_delta") {
        emitDelta(data.delta.text || "", controller);
      } else if (data.type === "message_delta" && data.usage) {
        if (typeof data.usage.output_tokens === "number") usage.output_tokens = data.usage.output_tokens;
        if (typeof data.usage.input_tokens === "number") usage.input_tokens = data.usage.input_tokens;
      }
      return;
    }

    if (provider === "openai") {
      if (data.usage) Object.assign(usage, providerAdapter.parseResponse({ usage: data.usage }).usage);
      const choice = (data.choices || [])[0];
      if (choice && choice.delta) emitDelta(choice.delta.content || "", controller);
      return;
    }

    if (provider === "google") {
      const parsedResponse = providerAdapter.parseResponse(data);
      emitDelta(parsedResponse.text, controller);
      if (data.usageMetadata) Object.assign(usage, parsedResponse.usage);
    }
  }

  return new TransformStream({
    transform(chunk, controller) {
      buffer += dec.decode(chunk, { stream: true });
      let idx;
      // Frames are delimited by a blank line. Handle both \n\n and \r\n\r\n.
      while (true) {
        const nn = buffer.indexOf("\n\n");
        const rr = buffer.indexOf("\r\n\r\n");
        if (nn === -1 && rr === -1) break;
        let cut, adv;
        if (rr !== -1 && (nn === -1 || rr < nn)) { cut = rr; adv = 4; }
        else { cut = nn; adv = 2; }
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + adv);
        if (frame.trim()) handleFrame(frame, controller);
      }
    },
    async flush(controller) {
      // Drain any trailing partial frame (some providers omit the final blank).
      const tail = buffer + dec.decode();
      if (tail.trim()) handleFrame(tail, controller);

      const payload = buildDonePayload(fullText, usage);
      controller.enqueue(enc.encode(sseFrame("done", payload)));
      // Commit the turn EXACTLY as the non-streaming path does. Even on a
      // mid-stream error we settle with whatever usage was captured so partial
      // generations still reconcile the reserve (bounds overshoot to ~one turn,
      // per budget-core's accounting model). We never leave the reserve dangling
      // for a stream that produced output.
      if (onSettle) {
        try { await onSettle(payload, usage, fullText, sawError); } catch { /* never surface to client */ }
      }
    },
  });
}

// ---- Fire the upstream streaming request ------------------------------------
// Returns BEFORE any bytes reach the client, so the router can still fall back
// to a clean JSON error + rollback (no turn burned) when the upstream rejects
// the request outright. On success it hands back the raw upstream Response whose
// body is piped through makeChatTransform().
//   { ok:true, upstream }                         — 200, body is the SSE stream
//   { ok:false, kind:"config",   status }         — 4xx (BYOK key/model rejected)
//   { ok:false, kind:"upstream", status? }        — 5xx / network / no body
//
// No retry here: unlike the non-streaming completeWithRetry loop, a streamed
// response cannot be transparently retried once bytes flow. A pre-stream failure
// is surfaced to the router, which rolls back and returns the same in-character
// "bad connection" the non-streaming path returns.
function buildStreamingRequest({ up, system, messages, maxTokens }) {
  const opts = {
    system,
    messages,
    maxTokens,
    providerCfg: { apiKey: up.apiKey, model: up.model },
  };

  const provider = getProvider(up.provider);
  return provider?.buildStreamingRequest?.(opts) || null;
}

export async function startProviderStream({ up, system, messages, maxTokens }, fetchImpl) {
  const doFetch = fetchImpl || fetch;
  const request = buildStreamingRequest({ up, system, messages, maxTokens });
  if (!request) return { ok: false, kind: "config" };
  const { url, headers, body } = request;

  let res;
  try {
    res = await doFetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
  } catch {
    return { ok: false, kind: "upstream" };
  }
  if (!res.ok) {
    if (res.status >= 400 && res.status < 500 && res.status !== 429) {
      return { ok: false, kind: "config", status: res.status };
    }
    return { ok: false, kind: "upstream", status: res.status };
  }
  if (!res.body) return { ok: false, kind: "upstream", status: res.status };
  return { ok: true, upstream: res };
}

// Compatibility export for existing focused tests and any direct callers.
export function startAnthropicStream({ up, ...rest }, fetchImpl) {
  return startProviderStream({ up: { ...up, provider: "anthropic" }, ...rest }, fetchImpl);
}
