// index.js — router for the Sonsteng client-interview / debrief / critique Worker.
//
// Invariants:
//  * withCors() wraps EVERY return (success AND error) for allowlisted origins,
//    plus an explicit OPTIONS preflight (see cors.js).
//  * The Worker builds the ENTIRE upstream request server-side. Client input is
//    whitelisted to specific fields; system prompt and max_tokens are never read
//    from the client. The one client-controlled upstream input is the BYOK block
//    (their provider + THEIR key + a model from a per-provider allowlist).
//  * PROVIDER-AGNOSTIC BYOK: requests may carry byok {provider, api_key, model?}
//    for anthropic | openai | google. BYOK skips the spend counter (their money)
//    but every other guard still applies (turn caps, dedupe, session validity,
//    input caps, persona injection, ≥6-turn debrief guard). The user's key is
//    NEVER stored and NEVER logged (see the log-scan unit test).
//  * No byok + no hosted ANTHROPIC_API_KEY -> typed no_hosted_key error.
//  * The BudgetCounter DO is the single authority for spend + turn caps + mint
//    throttle + turn_id dedupe + the debrief ≥6-turn oracle guard.
//  * Non-streaming: await the full JSON, settle the exact usage, then return.

import bundle from "../personas/personas.generated.json" with { type: "json" };
import { parseAllowedOrigins, matchOrigin, handlePreflight, withCors } from "./cors.js";
import { mintSession, verifySession, timingSafeEqualStr } from "./session.js";
import { gateSessionMint } from "./turnstile.js";
import { getProvider } from "./providers/registry.js";
import { resolveUpstream, resolvePanelUpstreams } from "./byok.js";
import { renderPersona, buildDebriefPrompt, buildCritiquePrompt, rubricCriteriaLabels } from "./prompts.js";
import { validateDebriefScorecard, validateCritiqueScorecard, validateLearnerResultRequest, parseModelJson, redactDebriefOracle, detectDebriefOracleLeak } from "./validate.js";
import { runFormativeMemoPanel } from "./panel.js";
import { json, errorEnvelope } from "./errors.js";
import { editorFetch, accessDoorwayRedirect } from "./editor.js";
import { streamingEnabled, supportsStreaming, startProviderStream, makeChatTransform, pipeProviderStream } from "./chat-stream.js";

export { BudgetCounter } from "./budget.js";
export { EditorStore } from "./editor-store.js";

// ---- Tunables ---------------------------------------------------------------
const PER_IP_MINT_CEILING = 20;      // coarse per-IP session-mint brake (~20/day)
const MAX_MESSAGE_CHARS = 4000;      // per-message input cap
const MAX_MESSAGES = 60;             // message-array length ceiling
const MAX_TOTAL_INPUT_CHARS = 24000; // total chat input cap
const CRITIQUE_MAX_CHARS = 18000;    // /critique deliverable size cap (graceful 413)
const MEMO_ASSESSMENT_MAX_CHARS = 18000;
const DEBRIEF_MIN_TURNS = 6;         // debrief-oracle guard
const WARNING_TURN = 15;             // in-character "checks watch"
const CHAT_MAX_TOKENS = 300;
const DEBRIEF_MAX_TOKENS = 1200;
const CRITIQUE_MAX_TOKENS = 1500;

// ---- helpers ----------------------------------------------------------------
async function hmacHex(signingKey, value) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(signingKey), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(value)));
  return [...sig].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || request.headers.get("x-forwarded-for") || "unknown";
}

function capsFor(env) {
  return {
    capPublicCents: (parseInt(env.PUBLIC_BUDGET_USD, 10) || 7) * 100,
    capDemoCents: (parseInt(env.DEMO_RESERVE_USD, 10) || 3) * 100,
    maxTurns: parseInt(env.MAX_TURNS, 10) || 20,
    maxSessionsPerDay: parseInt(env.MAX_SESSIONS_PER_DAY, 10) || 200,
  };
}

function budgetStub(env) {
  return env.BUDGET.getByName("global-v1");
}

function logMeta(fields) {
  // Metadata-only structured log (no message content, no query strings). Synchronous.
  try { console.log(JSON.stringify({ t: Date.now(), ...fields })); } catch {}
}

// Reserve worst-case cents helper input estimate (chars -> ~tokens).
function estTokens(chars) {
  return Math.ceil(chars / 3.5);
}

// Resolve the upstream (BYOK or hosted) for a request body, or return the typed
// error Response. NEVER log the resolved object — it carries an API key.
function upstreamOrError(env, body) {
  const up = resolveUpstream(env, body.byok);
  if (!up.ok) return { error: errorEnvelope(up.code, up.message, up.status) };
  return { up };
}

// One upstream completion via the resolved provider adapter.
function callUpstream(up, { system, messages, maxTokens, jsonMode }) {
  const provider = getProvider(up.provider);
  return provider.complete({
    system, messages, maxTokens,
    providerCfg: { apiKey: up.apiKey, model: up.model, jsonMode: !!jsonMode },
  });
}

// Map an upstream failure to the right envelope. A 4xx "config" failure on a
// BYOK call almost always means the USER'S key or model was rejected — surface
// it as a plain validation_error (never in-character). Hosted config failures
// are OUR bug: log-and-alert semantics, generic upstream error to the client.
function upstreamFailureResponse(up, result, ev) {
  logMeta({ ev, mode: up.mode, provider: up.provider, kind: result.kind, status: result.status || 0 });
  if (result.kind === "config" && up.mode === "byok") {
    return errorEnvelope(
      "validation_error",
      `The ${up.provider} API rejected the request (HTTP ${result.status}). Check your API key and model.`,
      400
    );
  }
  return errorEnvelope("upstream_unavailable", "The interview service is temporarily unavailable.", 503);
}

// ---- GET /v1/session --------------------------------------------------------
async function handleSession(request, env, origin) {
  const url = new URL(request.url);
  const bypass = url.searchParams.get("bypass");
  const caps = capsFor(env);

  let isDemo = false;
  if (bypass && env.DEMO_BYPASS_TOKEN) {
    isDemo = await timingSafeEqualStr(bypass, env.DEMO_BYPASS_TOKEN);
  }

  // WP6 bot-gate: verify Turnstile BEFORE minting, unless this is a valid demo
  // bypass (keyless demo/professor flows) or the gate is disabled. The token
  // rides the query string (never logged — same channel as `bypass`). A
  // failure is retryable (reload re-runs the widget), never silent-open.
  const tsToken = url.searchParams.get("cf_ts") || "";
  const gate = await gateSessionMint({ env, token: tsToken, isDemo, ip: clientIp(request) });
  if (!gate.ok) {
    logMeta({ ev: "mint_turnstile_reject", reason: gate.reason });
    return errorEnvelope(gate.code, gate.message, gate.status);
  }

  const ipHash = await hmacHex(env.SESSION_SIGNING_KEY, clientIp(request));
  const stub = budgetStub(env);
  const mint = await stub.mint(ipHash, {
    maxSessionsPerDay: caps.maxSessionsPerDay,
    perIpCeiling: PER_IP_MINT_CEILING,
    bypass: isDemo,
  });
  if (!mint.ok) {
    logMeta({ ev: "mint_denied", ipHash, reason: mint.reason });
    return errorEnvelope("rate_limited", "Too many sessions have been started today. Please try again later.", 429);
  }

  const pool = isDemo ? "demo" : "public";
  const { token, sid } = await mintSession(env.SESSION_SIGNING_KEY, { pool });
  logMeta({ ev: "mint", ipHash, pool });
  return json({ session_token: token, sid, pool, max_turns: caps.maxTurns });
}

// ---- shared: verify token + read whitelisted body ---------------------------
async function readJson(request) {
  try { return await request.json(); } catch { return null; }
}

function validMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0 || messages.length > MAX_MESSAGES) return null;
  let total = 0;
  const clean = [];
  for (const m of messages) {
    if (!m || (m.role !== "user" && m.role !== "assistant") || typeof m.content !== "string") return null;
    if (m.content.length > MAX_MESSAGE_CHARS) return null;
    total += m.content.length;
    if (total > MAX_TOTAL_INPUT_CHARS) return null;
    clean.push({ role: m.role, content: m.content });
  }
  return clean;
}

// ---- POST /v1/chat ----------------------------------------------------------
async function handleChat(request, env, origin) {
  const body = await readJson(request);
  if (!body) return errorEnvelope("validation_error", "Malformed JSON body.", 400);

  const session = await verifySession(env.SESSION_SIGNING_KEY, body.session_token);
  if (!session) return errorEnvelope("session_invalid", "Invalid or expired session token.", 401);

  const personaId = body.persona_id;
  const matterId = body.matter_id;
  // Keep the internal preflight/finalizer key non-null so failure finalization
  // remains idempotent even for legacy callers that omit a client turn_id.
  const turnId = typeof body.turn_id === "string" && body.turn_id ? body.turn_id : crypto.randomUUID();
  const messages = validMessages(body.messages);
  if (!messages) return errorEnvelope("validation_error", "messages failed validation (role/content/size).", 400);
  if (typeof personaId !== "string" || typeof matterId !== "string")
    return errorEnvelope("validation_error", "matter_id and persona_id are required.", 400);

  const persona = bundle.personas[personaId];
  if (!persona || persona.matter_id !== matterId)
    return errorEnvelope("validation_error", "Unknown persona for this matter.", 400);

  const { up, error } = upstreamOrError(env, body);
  if (error) return error;

  const caps = capsFor(env);
  const stub = budgetStub(env);
  const personaTail = renderPersona(persona);
  const msgChars = messages.reduce((n, m) => n + m.content.length, 0);
  const inputEst = estTokens(bundle.segment_a.length + personaTail.length + msgChars);
  const reserveCents = Math.ceil((inputEst * 100 + CHAT_MAX_TOKENS * 500) / 1_000_000);

  // BYOK: skipBudget — no spend gate, no reserve (their key, their money). Turn
  // cap, turn_id dedupe, and persona-turn counting still enforced in the DO.
  const pre = await stub.preflight(session.sid, {
    personaId, pool: session.p,
    capPublicCents: caps.capPublicCents, capDemoCents: caps.capDemoCents,
    maxTurns: caps.maxTurns, reserveCents, turnId, skipBudget: up.skipBudget,
  });

  if (pre.replay) return json(pre.result); // idempotent: never re-bill
  if (!pre.ok) {
    if (pre.reason === "turn_limit")
      return errorEnvelope("turn_limit", "This interview has reached its turn limit.", 429);
    if (pre.reason === "duplicate")
      return errorEnvelope("validation_error", "A request for this turn is already in progress.", 409);
    return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);
  }

  const system = { prefix: bundle.segment_a, tail: personaTail };
  const turn = pre.turn;
  const state = turn >= caps.maxTurns ? "ended" : turn >= WARNING_TURN ? "warning" : "active";
  // Assemble the client-facing turn payload from the settled `usage`. Shared by
  // BOTH the non-streaming and streaming paths so the DO stores a byte-identical
  // replay record and the client sees the same numbers either way.
  const buildPayload = (reply, usage) => ({
    reply,
    turn,
    remaining: pre.remaining,
    state,
    usage: {
      input_tokens: usage.input_tokens || 0,
      output_tokens: usage.output_tokens || 0,
      cache_read_input_tokens: usage.cache_read_input_tokens || 0,
    },
  });

  // ---- Streaming path (DEV flag ON + supported provider) -----------------
  // Proxy the provider's SSE through a TransformStream: relay text deltas as
  // they arrive, capture terminal usage server-side, and settle the DO in flush
  // with the EXACT same {usage, payload} contract the
  // non-streaming path uses below. Each provider's native SSE dialect is
  // normalized by chat-stream.js before it reaches the browser.
  if (streamingEnabled(env) && supportsStreaming(up.provider)) {
    const started = await startProviderStream({ up, system, messages, maxTokens: CHAT_MAX_TOKENS });
    if (!started.ok) {
      await stub.rollback(session.sid, turnId, personaId); // no turn burned, no spend
      return upstreamFailureResponse(up, started, "chat_upstream_fail");
    }
    let failurePromise = null;
    const finalizeStreamFailure = (usage, kind) => {
      if (!failurePromise) {
        failurePromise = stub.fail(
          session.sid,
          up.skipBudget ? null : usage,
          turnId,
          personaId
        ).then(() => {
          logMeta({ ev: "chat_stream_fail", kind, mode: up.mode, provider: up.provider, pool: session.p, turn });
        }).catch((error) => {
          failurePromise = null;
          throw error;
        });
      }
      return failurePromise;
    };
    const transform = makeChatTransform({
      provider: up.provider,
      buildDonePayload: (fullText, usage) => buildPayload(fullText, usage),
      onSettle: async (payload, usage) => {
        // usage=null on BYOK: nothing billed to the hosted pools, replay stored.
        await stub.settle(session.sid, up.skipBudget ? null : usage, turnId, payload);
        logMeta({ ev: "chat_ok", stream: true, mode: up.mode, provider: up.provider, pool: session.p, turn, cache_read: payload.usage.cache_read_input_tokens });
      },
      onFailure: (usage) => finalizeStreamFailure(usage, "provider"),
    });
    const clientStream = pipeProviderStream({
      upstreamBody: started.upstream.body,
      transform,
      // A transport abort has no trustworthy terminal usage. Refund the reserve
      // and clear the turn before the normalized stream is allowed to error.
      onFailure: () => finalizeStreamFailure(null, "transport"),
    });
    // withCors() (applied by the router) preserves this streaming body + headers.
    return new Response(clientStream, {
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-store",
        "x-sonsteng-stream": "1", // robust client-side detection signal
      },
    });
  }

  // ---- Non-streaming path (DEFAULT — byte-for-byte unchanged) --------------
  const result = await callUpstream(up, { system, messages, maxTokens: CHAT_MAX_TOKENS });

  if (!result.ok) {
    await stub.rollback(session.sid, turnId, personaId); // no turn burned, no spend
    return upstreamFailureResponse(up, result, "chat_upstream_fail");
  }

  const payload = buildPayload(result.text, result.usage);
  // usage=null on BYOK: nothing billed to the hosted pools, replay still stored.
  await stub.settle(session.sid, up.skipBudget ? null : result.usage, turnId, payload);
  logMeta({ ev: "chat_ok", mode: up.mode, provider: up.provider, pool: session.p, turn, cache_read: payload.usage.cache_read_input_tokens });
  return json(payload);
}

// ---- POST /v1/debrief -------------------------------------------------------
async function handleDebrief(request, env, origin) {
  const body = await readJson(request);
  if (!body) return errorEnvelope("validation_error", "Malformed JSON body.", 400);
  const routing = validateLearnerResultRequest(body);
  if (!routing.ok) return errorEnvelope("validation_error", routing.error, 400);

  const session = await verifySession(env.SESSION_SIGNING_KEY, body.session_token);
  if (!session) return errorEnvelope("session_invalid", "Invalid or expired session token.", 401);

  const personaId = body.persona_id;
  const matterId = body.matter_id;
  const transcript = validMessages(body.transcript);
  if (typeof personaId !== "string" || typeof matterId !== "string" || !transcript)
    return errorEnvelope("validation_error", "matter_id, persona_id, and transcript are required.", 400);

  const persona = bundle.personas[personaId];
  if (!persona || persona.matter_id !== matterId)
    return errorEnvelope("validation_error", "Unknown persona for this matter.", 400);

  const { up, error } = upstreamOrError(env, body);
  if (error) return error;

  const caps = capsFor(env);
  const stub = budgetStub(env);

  // Debrief-oracle guard: the sid must have really conducted this interview.
  // Applies to BYOK too — the guard protects the answer key, not the budget.
  const committed = await stub.committedTurnsForPersona(session.sid, personaId);
  if (committed < DEBRIEF_MIN_TURNS)
    return errorEnvelope("session_invalid", "No completed interview found for this persona.", 403);

  if (!up.skipBudget) {
    const gate = await stub.checkPool(session.p, caps.capPublicCents, caps.capDemoCents);
    if (!gate.ok) return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);
  }

  const prompt = buildDebriefPrompt(bundle.debrief_template, {
    matterId, personaId, persona,
    factMap: bundle.fact_map[personaId] || {},
    transcript, interviewerOnOpposingSide: false,
  });
  const result = await callUpstream(up, {
    system: null, messages: [{ role: "user", content: prompt }],
    maxTokens: DEBRIEF_MAX_TOKENS, jsonMode: true,
  });
  if (!result.ok) return upstreamFailureResponse(up, result, "debrief_upstream_fail");

  const parsed = parseModelJson(result.text);
  const check = parsed && validateDebriefScorecard(parsed);
  if (!parsed || !check.ok) {
    logMeta({ ev: "debrief_invalid", errors: check ? check.errors.slice(0, 5) : ["unparseable"] });
    return errorEnvelope("validation_error", "The debrief could not be generated. Please try again.", 502);
  }

  // DEBRIEF-ORACLE hard guard: rebuild the Axis-A "missed" fields from server
  // ground truth so no un-elicited fact TEXT can leak to the student, even if the
  // (possibly BYOK/weak) evaluator model ignored the prompt's own oracle rule.
  redactDebriefOracle(parsed, persona, bundle.fact_map[personaId] || {});

  // C1 fail-closed scan: redactDebriefOracle rebuilds only the two Axis-A "missed"
  // fields. If an un-elicited concealed/rapport-gated fact TEXT slipped into a
  // model-authored FREE-TEXT field (narrative, self_reflection_prompt, an axis_b
  // comment, or a rule_4_2 flag), REJECT the scorecard rather than ship the answer
  // key — same retryable validation_error the client already handles for invalid
  // model output. Never mangle; a leak means the whole scorecard is untrustworthy.
  const leakField = detectDebriefOracleLeak(parsed, persona, bundle.fact_map[personaId] || {});
  if (leakField) {
    logMeta({ ev: "debrief_oracle_leak", field: leakField });
    return errorEnvelope("validation_error", "The debrief could not be generated. Please try again.", 502);
  }

  if (!up.skipBudget) await stub.charge(session.p, result.usage);
  logMeta({ ev: "debrief_ok", mode: up.mode, provider: up.provider, pool: session.p });
  return json({ scorecard: parsed });
}

// ---- POST /v1/memo-assessment ---------------------------------------------
async function handleMemoAssessment(request, env, origin) {
  const body = await readJson(request);
  if (!body) return errorEnvelope("validation_error", "Malformed JSON body.", 400);
  const routing = validateLearnerResultRequest(body);
  if (!routing.ok) return errorEnvelope("validation_error", routing.error, 400);

  // U11 is deliberately formative-only. No request field can promote the
  // assessment_use while calibration and provider-terms prerequisites remain.
  if (body.assessment_use != null && body.assessment_use !== "formative") {
    return errorEnvelope("validation_error", "Memo assessment is available for formative use only.", 400);
  }

  const session = await verifySession(env.SESSION_SIGNING_KEY, body.session_token);
  if (!session) return errorEnvelope("session_invalid", "Invalid or expired session token.", 401);

  const submission = body.deliverable_text;
  if (typeof submission !== "string" || submission.length === 0) {
    return errorEnvelope("validation_error", "deliverable_text is required.", 400);
  }
  if (submission.length > MEMO_ASSESSMENT_MAX_CHARS) {
    return errorEnvelope(
      "validation_error",
      `Deliverable exceeds the ${MEMO_ASSESSMENT_MAX_CHARS}-character limit.`,
      413
    );
  }

  const panel = resolvePanelUpstreams(env, { byok: body.byok, byokPanel: body.byok_panel });
  if (!panel.ok) return errorEnvelope(panel.code, panel.message, panel.status);

  const caps = capsFor(env);
  const stub = budgetStub(env);
  const usesHostedPool = panel.graders.some((grader) => !grader.skipBudget);
  if (usesHostedPool) {
    const gate = await stub.checkPool(session.p, caps.capPublicCents, caps.capDemoCents);
    if (!gate.ok) return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);
  }

  const run = await runFormativeMemoPanel({
    submission,
    instrument: bundle.assessment_instrument,
    scorecardTemplate: bundle.memo_scorecard_template,
    graders: panel.graders,
    complete: ({ grader, prompt, maxTokens, jsonMode }) => callUpstream(grader, {
      system: null,
      messages: [{ role: "user", content: prompt }],
      maxTokens,
      jsonMode,
    }),
  });
  if (!run.ok && run.kind === "upstream") {
    return upstreamFailureResponse(run.grader, run.upstreamResult, "memo_assessment_upstream_fail");
  }
  if (!run.ok) {
    logMeta({ ev: "memo_assessment_invalid", errors: (run.errors || []).slice(0, 5) });
    return errorEnvelope("validation_error", "The memo assessment could not be generated. Please try again.", 502);
  }

  const blockers = new Set(run.result.summative_blockers || []);
  if (run.result.assessment_use !== "formative" || run.result.summative_eligible !== false ||
      !blockers.has("human_human_calibration") || !blockers.has("provider_terms_review")) {
    logMeta({ ev: "memo_assessment_safety_contract_failed" });
    return errorEnvelope("validation_error", "The memo assessment safety contract failed.", 502);
  }

  if (usesHostedPool) await stub.charge(session.p, run.usage);
  logMeta({
    ev: "memo_assessment_ok",
    pool: session.p,
    assurance: run.result.assurance,
    providers: run.result.providers.map((provider) => provider.provider).join(","),
  });
  return json({ assessment: run.result });
}

// ---- POST /v1/critique ------------------------------------------------------
async function handleCritique(request, env, origin) {
  const body = await readJson(request);
  if (!body) return errorEnvelope("validation_error", "Malformed JSON body.", 400);
  const routing = validateLearnerResultRequest(body);
  if (!routing.ok) return errorEnvelope("validation_error", routing.error, 400);

  const session = await verifySession(env.SESSION_SIGNING_KEY, body.session_token);
  if (!session) return errorEnvelope("session_invalid", "Invalid or expired session token.", 401);

  const matterId = body.matter_id;
  const deliverable = body.deliverable_text;
  if (typeof matterId !== "string" || typeof deliverable !== "string")
    return errorEnvelope("validation_error", "matter_id and deliverable_text are required.", 400);
  if (deliverable.length > CRITIQUE_MAX_CHARS)
    return errorEnvelope("validation_error", `Deliverable exceeds the ${CRITIQUE_MAX_CHARS}-character limit.`, 413);

  const rubric = bundle.rubrics && bundle.rubrics[matterId];
  if (!rubric) return errorEnvelope("validation_error", "No rubric is available for this matter.", 400);
  const rubricId = rubric.id && /^m\d{2}\.rub$/.test(rubric.id) ? rubric.id : matterId + ".rub";

  const { up, error } = upstreamOrError(env, body);
  if (error) return error;

  const caps = capsFor(env);
  const stub = budgetStub(env);
  if (!up.skipBudget) {
    const gate = await stub.checkPool(session.p, caps.capPublicCents, caps.capDemoCents);
    if (!gate.ok) return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);
  }

  const prompt = buildCritiquePrompt(bundle.critique_template, {
    matterId, rubricId, rubric, deliverable,
  });
  const result = await callUpstream(up, {
    system: null, messages: [{ role: "user", content: prompt }],
    maxTokens: CRITIQUE_MAX_TOKENS, jsonMode: true,
  });
  if (!result.ok) return upstreamFailureResponse(up, result, "critique_upstream_fail");

  const parsed = parseModelJson(result.text);
  const check = parsed && validateCritiqueScorecard(parsed);
  if (!parsed || !check.ok) {
    logMeta({ ev: "critique_invalid", errors: check ? check.errors.slice(0, 5) : ["unparseable"] });
    return errorEnvelope("validation_error", "The critique could not be generated. Please try again.", 502);
  }

  if (!up.skipBudget) await stub.charge(session.p, result.usage);
  logMeta({ ev: "critique_ok", mode: up.mode, provider: up.provider, pool: session.p });
  return json({ scorecard: parsed, criteria_labels: rubricCriteriaLabels(rubric) });
}

// ---- router -----------------------------------------------------------------
export default {
  async fetch(request, env, ctx) {
    const allowed = parseAllowedOrigins(env.ALLOWED_ORIGINS);
    const url = new URL(request.url);

    // The /edit surface is a self-contained router with its OWN auth, CORS
    // allowlist (the worker's edit origin only), CSRF guard, and strict security
    // headers. It never touches the chat/BYOK path below. Delegated before the
    // chat CORS handling so /edit responses carry the edit headers, not chat CORS.
    // ---- Access door: the bare hostname is a doorway (plan KTD7) ------------
    // Placed before the /edit delegation so it cannot shadow any real path. The
    // decision itself lives in editor.js so it is testable — this module imports
    // cloudflare:workers and cannot be loaded by the test runner.
    const doorway = accessDoorwayRedirect(env, url);
    if (doorway) return doorway;

    if (url.pathname === "/edit" || url.pathname.startsWith("/edit/")) {
      try {
        return await editorFetch(request, env, ctx);
      } catch (err) {
        logMeta({ ev: "edit_unhandled", msg: String(err && err.message) });
        return new Response("Not found.", {
          status: 500,
          headers: { "content-type": "text/plain; charset=utf-8", "Cache-Control": "private, no-store" },
        });
      }
    }

    if (request.method === "OPTIONS") return handlePreflight(request, allowed);

    const origin = matchOrigin(request, allowed);
    // Origin present but not allowlisted -> 403 with NO CORS (browser blocks it).
    if (request.headers.get("Origin") && !origin) {
      return errorEnvelope("origin_forbidden", "Origin not allowed.", 403);
    }

    let response;
    try {
      if (request.method === "GET" && url.pathname === "/v1/session") {
        response = await handleSession(request, env, origin);
      } else if (request.method === "POST" && url.pathname === "/v1/chat") {
        response = await handleChat(request, env, origin);
      } else if (request.method === "POST" && url.pathname === "/v1/debrief") {
        response = await handleDebrief(request, env, origin);
      } else if (request.method === "POST" && url.pathname === "/v1/memo-assessment") {
        response = await handleMemoAssessment(request, env, origin);
      } else if (request.method === "POST" && url.pathname === "/v1/critique") {
        response = await handleCritique(request, env, origin);
      } else {
        response = errorEnvelope("validation_error", "Not found.", 404);
      }
    } catch (err) {
      // Never passThroughOnException; always an in-character-safe JSON error + CORS.
      logMeta({ ev: "unhandled", msg: String(err && err.message) });
      response = errorEnvelope("upstream_unavailable", "An unexpected error occurred.", 500);
    }

    return withCors(response, origin);
  },
};
