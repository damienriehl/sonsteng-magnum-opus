// index.js — router for the Sonsteng client-interview / debrief / critique Worker.
//
// Invariants:
//  * withCors() wraps EVERY return (success AND error) for allowlisted origins,
//    plus an explicit OPTIONS preflight (see cors.js).
//  * The Worker builds the ENTIRE Anthropic request server-side. Client input is
//    whitelisted to specific fields; model/system/max_tokens/tools are never read
//    from the client (see anthropic.js).
//  * The BudgetCounter DO is the single authority for spend + turn caps + mint
//    throttle + turn_id dedupe + the debrief ≥6-turn oracle guard.
//  * Non-streaming: await the full JSON, settle the exact usage, then return.

import bundle from "../personas/personas.generated.json" with { type: "json" };
import { parseAllowedOrigins, matchOrigin, handlePreflight, withCors } from "./cors.js";
import { mintSession, verifySession, timingSafeEqualStr } from "./session.js";
import { callChat, callEvaluator } from "./anthropic.js";
import { buildSystemPrompt, renderPersona, buildDebriefPrompt, buildCritiquePrompt } from "./prompts.js";
import { validateDebriefScorecard, validateCritiqueScorecard, parseModelJson } from "./validate.js";
import { json, errorEnvelope } from "./errors.js";

export { BudgetCounter } from "./budget.js";

// ---- Tunables ---------------------------------------------------------------
const PER_IP_MINT_CEILING = 20;      // coarse per-IP session-mint brake (~20/day)
const MAX_MESSAGE_CHARS = 4000;      // per-message input cap
const MAX_MESSAGES = 60;             // message-array length ceiling
const MAX_TOTAL_INPUT_CHARS = 24000; // total chat input cap
const CRITIQUE_MAX_CHARS = 18000;    // /critique deliverable size cap (graceful 413)
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

// ---- GET /v1/session --------------------------------------------------------
async function handleSession(request, env, origin) {
  const url = new URL(request.url);
  const bypass = url.searchParams.get("bypass");
  const caps = capsFor(env);

  let isDemo = false;
  if (bypass && env.DEMO_BYPASS_TOKEN) {
    isDemo = await timingSafeEqualStr(bypass, env.DEMO_BYPASS_TOKEN);
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
  const turnId = typeof body.turn_id === "string" ? body.turn_id : null;
  const messages = validMessages(body.messages);
  if (!messages) return errorEnvelope("validation_error", "messages failed validation (role/content/size).", 400);
  if (typeof personaId !== "string" || typeof matterId !== "string")
    return errorEnvelope("validation_error", "matter_id and persona_id are required.", 400);

  const persona = bundle.personas[personaId];
  if (!persona || persona.matter_id !== matterId)
    return errorEnvelope("validation_error", "Unknown persona for this matter.", 400);

  const caps = capsFor(env);
  const stub = budgetStub(env);
  const personaTail = renderPersona(persona);
  const msgChars = messages.reduce((n, m) => n + m.content.length, 0);
  const inputEst = estTokens(bundle.segment_a.length + personaTail.length + msgChars);
  const reserveCents = Math.ceil((inputEst * 100 + CHAT_MAX_TOKENS * 500) / 1_000_000);

  const pre = await stub.preflight(session.sid, {
    personaId, pool: session.p,
    capPublicCents: caps.capPublicCents, capDemoCents: caps.capDemoCents,
    maxTurns: caps.maxTurns, reserveCents, turnId,
  });

  if (pre.replay) return json(pre.result); // idempotent: never re-bill
  if (!pre.ok) {
    if (pre.reason === "turn_limit")
      return errorEnvelope("turn_limit", "This interview has reached its turn limit.", 429);
    if (pre.reason === "duplicate")
      return errorEnvelope("validation_error", "A request for this turn is already in progress.", 409);
    return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);
  }

  const result = await callChat(env, {
    segmentA: bundle.segment_a, personaTail, messages, maxTokens: CHAT_MAX_TOKENS,
  });

  if (!result.ok) {
    await stub.rollback(session.sid, turnId); // no turn burned, no spend
    logMeta({ ev: "chat_upstream_fail", kind: result.kind, status: result.status || 0 });
    return errorEnvelope("upstream_unavailable", "The interview service is temporarily unavailable.", 503);
  }

  const turn = pre.turn;
  const state = turn >= caps.maxTurns ? "ended" : turn >= WARNING_TURN ? "warning" : "active";
  const payload = {
    reply: result.text,
    turn,
    remaining: pre.remaining,
    state,
    usage: {
      input_tokens: result.usage.input_tokens || 0,
      output_tokens: result.usage.output_tokens || 0,
      cache_read_input_tokens: result.usage.cache_read_input_tokens || 0,
    },
  };
  await stub.settle(session.sid, result.usage, turnId, payload);
  logMeta({ ev: "chat_ok", pool: session.p, turn, cache_read: payload.usage.cache_read_input_tokens });
  return json(payload);
}

// ---- POST /v1/debrief -------------------------------------------------------
async function handleDebrief(request, env, origin) {
  const body = await readJson(request);
  if (!body) return errorEnvelope("validation_error", "Malformed JSON body.", 400);

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

  const caps = capsFor(env);
  const stub = budgetStub(env);

  // Debrief-oracle guard: the sid must have really conducted this interview.
  const committed = await stub.committedTurnsForPersona(session.sid, personaId);
  if (committed < DEBRIEF_MIN_TURNS)
    return errorEnvelope("session_invalid", "No completed interview found for this persona.", 403);

  const gate = await stub.checkPool(session.p, caps.capPublicCents, caps.capDemoCents);
  if (!gate.ok) return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);

  const prompt = buildDebriefPrompt(bundle.debrief_template, {
    matterId, personaId, persona,
    factMap: bundle.fact_map[personaId] || {},
    transcript, interviewerOnOpposingSide: false,
  });
  const result = await callEvaluator(env, { prompt, maxTokens: DEBRIEF_MAX_TOKENS });
  if (!result.ok) {
    logMeta({ ev: "debrief_upstream_fail", kind: result.kind, status: result.status || 0 });
    return errorEnvelope("upstream_unavailable", "The debrief service is temporarily unavailable.", 503);
  }

  const parsed = parseModelJson(result.text);
  const check = parsed && validateDebriefScorecard(parsed);
  if (!parsed || !check.ok) {
    logMeta({ ev: "debrief_invalid", errors: check ? check.errors.slice(0, 5) : ["unparseable"] });
    return errorEnvelope("validation_error", "The debrief could not be generated. Please try again.", 502);
  }

  await stub.charge(session.p, result.usage);
  logMeta({ ev: "debrief_ok", pool: session.p });
  return json({ scorecard: parsed });
}

// ---- POST /v1/critique ------------------------------------------------------
async function handleCritique(request, env, origin) {
  const body = await readJson(request);
  if (!body) return errorEnvelope("validation_error", "Malformed JSON body.", 400);

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

  const caps = capsFor(env);
  const stub = budgetStub(env);
  const gate = await stub.checkPool(session.p, caps.capPublicCents, caps.capDemoCents);
  if (!gate.ok) return errorEnvelope("cap_exceeded", "The daily demo budget has been reached.", 429);

  const prompt = buildCritiquePrompt(bundle.critique_template, {
    matterId, rubricId, rubric, deliverable,
  });
  const result = await callEvaluator(env, { prompt, maxTokens: CRITIQUE_MAX_TOKENS });
  if (!result.ok) {
    logMeta({ ev: "critique_upstream_fail", kind: result.kind, status: result.status || 0 });
    return errorEnvelope("upstream_unavailable", "The critique service is temporarily unavailable.", 503);
  }

  const parsed = parseModelJson(result.text);
  const check = parsed && validateCritiqueScorecard(parsed);
  if (!parsed || !check.ok) {
    logMeta({ ev: "critique_invalid", errors: check ? check.errors.slice(0, 5) : ["unparseable"] });
    return errorEnvelope("validation_error", "The critique could not be generated. Please try again.", 502);
  }

  await stub.charge(session.p, result.usage);
  logMeta({ ev: "critique_ok", pool: session.p });
  return json({ scorecard: parsed });
}

// ---- router -----------------------------------------------------------------
export default {
  async fetch(request, env, ctx) {
    const allowed = parseAllowedOrigins(env.ALLOWED_ORIGINS);
    const url = new URL(request.url);

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
