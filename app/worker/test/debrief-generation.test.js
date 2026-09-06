import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEBRIEF_INITIAL_MAX_TOKENS,
  DEBRIEF_RETRY_MAX_TOKENS,
  completeBudgetedOneShot,
  debriefInputTokenUpperBound,
  debriefValidationMessage,
  generateDebriefScorecard,
} from "../src/debrief.js";
import {
  completeWithRetry,
  DEBRIEF_PROVIDER_MAX_ATTEMPTS,
} from "../src/providers/common.js";

function scorecard(overrides = {}) {
  return {
    schema_version: "1.0.0",
    matter_id: "m00",
    persona_id: "m00.per.tester",
    axis_a: {
      facts_elicited: [],
      revealed_if_asked_missed: [],
      rapport_gated_unearned: [],
      rule_4_2_flags: [],
    },
    axis_b: {
      rapport_opening: { score: 5, comment: "Clear opening." },
      listening_t_funnel: { score: 5, comment: "Good listening." },
      understanding_goals: { score: 5, comment: "Goals identified." },
      explanation_next_steps: { score: 5, comment: "Next steps explained." },
      overall_confidence: { score: 5, comment: "A sound first pass." },
    },
    ethics_score: 0,
    narrative: "A sound first pass.",
    self_reflection_prompt: "What would you ask next?",
    ...overrides,
  };
}

const PERSONA = { disclosure: {} };

test("a truncated debrief retries exactly once at 2400 and returns the retry scorecard", async () => {
  const calls = [];
  const expected = scorecard();
  const responses = [
    { ok: true, text: '{"schema_version":"1.0.0"', stop_reason: "max_tokens", usage: {} },
    { ok: true, text: JSON.stringify(expected), stop_reason: "stop", usage: {} },
  ];

  const result = await generateDebriefScorecard({
    complete: async (maxTokens) => {
      calls.push(maxTokens);
      return responses.shift();
    },
    persona: PERSONA,
    factMap: {},
  });

  assert.equal(result.ok, true);
  assert.deepEqual(result.scorecard, expected);
  assert.deepEqual(result.attempt, {
    count: 2,
    initial_stop_reason: "max_tokens",
    outcome: "recovered",
  });
  assert.deepEqual(calls, [DEBRIEF_INITIAL_MAX_TOKENS, DEBRIEF_RETRY_MAX_TOKENS]);
  assert.equal(DEBRIEF_INITIAL_MAX_TOKENS, 1200);
  assert.equal(DEBRIEF_RETRY_MAX_TOKENS, 2400);
});

test("a second truncation fails distinctly and never attempts a third completion", async () => {
  const calls = [];
  const result = await generateDebriefScorecard({
    complete: async (maxTokens) => {
      calls.push(maxTokens);
      return { ok: true, text: "{", stop_reason: "max_tokens", usage: {} };
    },
    persona: PERSONA,
    factMap: {},
  });

  assert.deepEqual(calls, [1200, 2400]);
  assert.deepEqual(
    { ok: result.ok, kind: result.kind, subtype: result.subtype },
    { ok: false, kind: "validation", subtype: "truncated" },
  );
  assert.deepEqual(result.attempt, {
    count: 2,
    initial_stop_reason: "max_tokens",
    outcome: "retry_truncated",
  });
  assert.notEqual(
    debriefValidationMessage(result.subtype),
    debriefValidationMessage("wrong_shape"),
  );
});

test("a failed truncation retry reports its safe attempt outcome", async () => {
  const calls = [];
  const result = await generateDebriefScorecard({
    complete: async (maxTokens) => {
      calls.push(maxTokens);
      if (calls.length === 1) {
        return { ok: true, text: "{", stop_reason: "max_tokens", usage: {} };
      }
      return { ok: false, kind: "upstream", status: 529 };
    },
    persona: PERSONA,
    factMap: {},
  });

  assert.equal(result.ok, false);
  assert.equal(result.kind, "upstream");
  assert.deepEqual(result.attempt, {
    count: 2,
    initial_stop_reason: "max_tokens",
    outcome: "retry_upstream_failed",
  });
});

test("a structurally wrong debrief does not retry", async () => {
  let calls = 0;
  const result = await generateDebriefScorecard({
    complete: async () => {
      calls += 1;
      return {
        ok: true,
        text: JSON.stringify({ schema_version: "1.0.0" }),
        stop_reason: "stop",
        usage: {},
      };
    },
    persona: PERSONA,
    factMap: {},
  });

  assert.equal(calls, 1);
  assert.equal(result.ok, false);
  assert.equal(result.subtype, "wrong_shape");
});

test("an oracle leak keeps the existing rejection behavior and does not retry", async () => {
  const secret = "The client forged the incident report timestamp before the interview.";
  const persona = {
    disclosure: {
      concealed: [{ fact_ref: "m00.fact.001", text: secret }],
    },
  };
  let calls = 0;
  const result = await generateDebriefScorecard({
    complete: async () => {
      calls += 1;
      return {
        ok: true,
        text: JSON.stringify(scorecard({ narrative: secret })),
        stop_reason: "stop",
        usage: {},
      };
    },
    persona,
    factMap: {},
  });

  assert.equal(calls, 1);
  assert.equal(result.ok, false);
  assert.equal(result.subtype, "oracle_leak");
  assert.equal(result.leakField, "narrative");
  assert.equal(
    debriefValidationMessage(result.subtype),
    "The debrief could not be generated. Please try again.",
  );
});

test("a retry reserves and settles each provider attempt exactly once", async () => {
  const reservations = [];
  const settlements = [];
  const usages = [
    { input_tokens: 1000, output_tokens: 1200, thought_tokens: 300 },
    { input_tokens: 1000, output_tokens: 900, thought_tokens: 100 },
  ];
  const responses = [
    { ok: true, text: "{", stop_reason: "max_tokens", usage: usages[0] },
    { ok: true, text: JSON.stringify(scorecard()), stop_reason: "stop", usage: usages[1] },
  ];
  const budget = {
    async reserveOneShot(id, options) {
      reservations.push({ id, ...options });
      return { ok: true };
    },
    async settleOneShot(id, usage, retainedCents) {
      settlements.push({ id, usage, retainedCents });
      return { ok: true };
    },
  };
  let attempt = 0;

  const result = await generateDebriefScorecard({
    complete: (maxTokens) => completeBudgetedOneShot({
      budget,
      reservationId: `debrief-${++attempt}`,
      pool: "public",
      caps: { capPublicCents: 700, capDemoCents: 300 },
      inputTokens: 1000,
      maxTokens,
      complete: async () => responses.shift(),
    }),
    persona: PERSONA,
    factMap: {},
  });

  assert.equal(result.ok, true);
  assert.deepEqual(reservations.map((r) => r.id), ["debrief-1", "debrief-2"]);
  assert.deepEqual(reservations.map((r) => r.reserveCents), [2, 4]);
  assert.deepEqual(settlements, [
    { id: "debrief-1", usage: usages[0], retainedCents: 0 },
    { id: "debrief-2", usage: usages[1], retainedCents: 0 },
  ]);
});

test("a lost settle response is retried with the same id and preserves the paid completion", async () => {
  const settlements = [];
  let providerCalls = 0;
  const usage = { input_tokens: 1000, output_tokens: 300 };
  const budget = {
    async reserveOneShot() {
      return { ok: true };
    },
    async settleOneShot(id, settledUsage, retainedCents) {
      settlements.push({ id, usage: settledUsage, retainedCents });
      if (settlements.length === 1) throw new Error("response lost after commit");
      return { ok: true, replay: true };
    },
  };

  const result = await completeBudgetedOneShot({
    budget,
    reservationId: "stable-reservation-id",
    pool: "public",
    caps: { capPublicCents: 700, capDemoCents: 300 },
    inputTokens: 1000,
    maxTokens: 1200,
    complete: async () => {
      providerCalls += 1;
      return { ok: true, text: "paid completion", usage };
    },
  });

  assert.equal(result.ok, true);
  assert.equal(providerCalls, 1);
  assert.deepEqual(settlements, [
    { id: "stable-reservation-id", usage, retainedCents: 0 },
    { id: "stable-reservation-id", usage, retainedCents: 0 },
  ]);
});

test("the debrief reserve bound covers token-dense accepted UTF-16 content", () => {
  const acceptedMessages = Array.from({ length: 6 }, (_, index) => ({
    role: index % 2 === 0 ? "user" : "assistant",
    content: "\u0800".repeat(4000),
  }));
  const denseContent = acceptedMessages.map((message) => message.content).join("");
  const denseBytes = new TextEncoder().encode(denseContent).length;

  assert.ok(acceptedMessages.every((message) => message.content.length === 4000));
  assert.equal(denseContent.length, 24_000);
  assert.equal(denseBytes, denseContent.length * 3);
  assert.ok(debriefInputTokenUpperBound(denseContent) > denseBytes);
  assert.ok(
    debriefInputTokenUpperBound(denseContent) > Math.ceil(denseContent.length / 3.5),
  );
});

test("a truncation retry composes to at most four provider requests", async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    new Response("retry", { status: 429, headers: { "retry-after": "0" } }),
    new Response(JSON.stringify({ text: "{", stop_reason: "max_tokens" }), { status: 200 }),
    new Response("retry", { status: 429, headers: { "retry-after": "0" } }),
    new Response(JSON.stringify({ text: JSON.stringify(scorecard()), stop_reason: "stop" }), { status: 200 }),
  ];
  let providerCalls = 0;
  const logicalCalls = [];
  globalThis.fetch = async () => {
    providerCalls += 1;
    return responses.shift();
  };
  try {
    const result = await generateDebriefScorecard({
      complete: (maxTokens) => {
        logicalCalls.push(maxTokens);
        return completeWithRetry(
          () => ({ url: "https://provider.example.test", headers: {}, body: { maxTokens } }),
          (data) => ({ text: data.text, usage: {}, stop_reason: data.stop_reason }),
          DEBRIEF_PROVIDER_MAX_ATTEMPTS,
        );
      },
      persona: PERSONA,
      factMap: {},
    });

    assert.equal(result.ok, true);
    assert.deepEqual(logicalCalls, [1200, 2400]);
    assert.equal(DEBRIEF_PROVIDER_MAX_ATTEMPTS, 2);
    assert.equal(providerCalls, 2 * DEBRIEF_PROVIDER_MAX_ATTEMPTS);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
