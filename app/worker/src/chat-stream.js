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
//   * turn/replay bookkeeping — successful streams call settle() in flush with
//       the SAME payload shape as non-streaming; failed streams call fail()
//       instead, so partial output never becomes a successful replay record.
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
// onFailure(usage, fullText) -> async; the router clears the in-flight turn
//   without storing a replay result. Both terminal callbacks run from flush(),
//   after the last complete provider frame has been parsed.
export function makeChatTransform({ provider = "anthropic", buildDonePayload, onSettle, onFailure }) {
  const dec = new TextDecoder();
  const enc = new TextEncoder();

  let buffer = "";
  let fullText = "";
  const usage = {};
  let sawError = false;
  let sawTerminalUsage = false;
  let sawTerminal = false;
  const providerAdapter = getProvider(provider);

  function emitDelta(text, controller) {
    if (!text) return;
    fullText += text;
    controller.enqueue(enc.encode(sseFrame("delta", { text })));
  }

  function captureError(data, controller) {
    if (!data || !data.error) return false;
    if (sawError) return true;
    sawError = true;
    const msg = data.error.message || "upstream stream error";
    controller.enqueue(enc.encode(sseFrame("error", { message: msg })));
    return true;
  }

  function handleFrame(frame, controller) {
    if (sawError) return;
    const parsed = parseSSEFrame(frame);
    if (!parsed) return;
    if (provider === "openai" && parsed.data.trim() === "[DONE]") {
      sawTerminal = sawTerminalUsage;
      return;
    }
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
        sawTerminalUsage = typeof data.usage.output_tokens === "number" || typeof data.usage.input_tokens === "number";
      } else if (data.type === "message_stop") {
        sawTerminal = sawTerminalUsage;
      }
      return;
    }

    if (provider === "openai") {
      if (data.usage) {
        Object.assign(usage, providerAdapter.parseResponse({ usage: data.usage }).usage);
        sawTerminalUsage = typeof data.usage.prompt_tokens === "number" || typeof data.usage.completion_tokens === "number";
      }
      const choice = (data.choices || [])[0];
      if (choice && choice.delta) emitDelta(choice.delta.content || "", controller);
      return;
    }

    if (provider === "google") {
      const parsedResponse = providerAdapter.parseResponse(data);
      emitDelta(parsedResponse.text, controller);
      if (data.usageMetadata) {
        Object.assign(usage, parsedResponse.usage);
        sawTerminalUsage = typeof data.usageMetadata.promptTokenCount === "number" ||
          typeof data.usageMetadata.candidatesTokenCount === "number";
      }
      const hasFinishReason = (data.candidates || []).some((candidate) => !!candidate.finishReason);
      if (hasFinishReason && sawTerminalUsage) sawTerminal = true;
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

      // Provider-declared errors and clean EOFs without the provider's terminal
      // completion contract are failures: never emit `done` or store partial
      // text as a successful replay. Declared errors already emitted an error
      // frame; truncated clean closes deliberately emit none, so the browser
      // maps the missing `done` to "stream ended early". Await finalization so a
      // same-turn retry cannot race the Durable Object's in-flight marker.
      if (sawError || !sawTerminal) {
        if (onFailure) await onFailure(usage, fullText);
        return;
      }

      const payload = buildDonePayload(fullText, usage);
      controller.enqueue(enc.encode(sseFrame("done", payload)));
      // Commit the turn EXACTLY as the non-streaming path does.
      if (onSettle) {
        try { await onSettle(payload, usage, fullText); } catch { /* never surface to client */ }
      }
    },
  });
}

// Pipe a provider body through the normalizer while preserving failure order.
// `preventAbort` keeps the normalized readable pending after a source error;
// only after onFailure has cleared budget/turn state do we abort the transform,
// which makes the browser's next read reject. That ordering makes a retry with
// the same turn_id safe instead of racing a still-in-flight reservation.
export function pipeProviderStream({ upstreamBody, transform, onFailure }) {
  const readable = transform.readable;
  void upstreamBody.pipeTo(transform.writable, { preventAbort: true }).catch(async (error) => {
    try {
      if (onFailure) await onFailure(error);
    } finally {
      try { await transform.writable.abort(error); } catch { /* already closed */ }
    }
  });
  return readable;
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
