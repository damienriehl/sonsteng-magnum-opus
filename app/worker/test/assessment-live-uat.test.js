import assert from "node:assert/strict";
import { Readable } from "node:stream";
import test from "node:test";

import {
  FIXED_DISPOSABLE_MEMO,
  runAssessmentAuditPreparation,
} from "./assessment-live-uat.mjs";
import { loadCredentials } from "./live-stream-smoke.mjs";

const API_KEY = "assessment-provider-secret-sentinel";
const BYPASS = "assessment-bypass-secret-sentinel";
const SESSION = "assessment-session-secret-sentinel";
const ORIGIN = "https://legalpracticum.org";
const WORKER = "https://worker.example.test";
const AUDIT_ID = "memo-assessment-123e4567-e89b-42d3-a456-426614174000";
const HEADINGS = [
  "governing_law",
  "strengths_and_weaknesses_both_sides",
  "issues",
  "suggested_solutions",
  "theory_and_themes",
  "elements_to_prevail",
  "liabilities_and_remedies",
];

function jsonResponse(body, { status = 200, origin = ORIGIN, headers = {} } = {}) {
  return Response.json(body, {
    status,
    headers: {
      "access-control-allow-origin": origin,
      ...headers,
    },
  });
}

function validAssessment(overrides = {}) {
  return {
    assessment: {
      assessment_use: "formative",
      summative_eligible: false,
      headings: HEADINGS.map((heading_id, index) => ({ heading_id, score: index + 1 })),
      ...overrides,
    },
    assessment_audit_id: AUDIT_ID,
  };
}

function successfulWorker(assessment = validAssessment()) {
  const calls = [];
  return {
    calls,
    async fetch(url, init = {}) {
      calls.push({ url: String(url), init });
      assert.equal(init.redirect, "manual");
      assert.ok(init.signal instanceof AbortSignal);
      assert.equal(init.headers.Origin, ORIGIN);
      if (calls.length === 1) {
        const parsed = new URL(url);
        assert.equal(parsed.pathname, "/v1/session");
        assert.equal(parsed.searchParams.get("bypass"), BYPASS);
        assert.equal(init.method, "GET");
        return jsonResponse({ session_token: SESSION });
      }
      assert.equal(new URL(url).pathname, "/v1/memo-assessment");
      assert.equal(init.method, "POST");
      const body = JSON.parse(init.body);
      assert.deepEqual(body, {
        session_token: SESSION,
        deliverable_text: FIXED_DISPOSABLE_MEMO,
        assessment_use: "formative",
        byok: { provider: "google", api_key: API_KEY },
      });
      return jsonResponse(assessment);
    },
  };
}

async function run(fetchImpl) {
  return runAssessmentAuditPreparation({
    workerUrl: WORKER,
    provider: "google",
    apiKey: API_KEY,
    bypassToken: BYPASS,
    fetchImpl,
    allowTestOrigin: true,
  });
}

function containsSecret(value) {
  const serialized = JSON.stringify(value);
  return [API_KEY, BYPASS, SESSION, FIXED_DISPOSABLE_MEMO]
    .some((secret) => serialized.includes(secret));
}

test("importing the preparer creates no session or live assessment", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw new Error("import must not fetch");
  };
  try {
    await import(`./assessment-live-uat.mjs?import-only=${Date.now()}`);
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("one explicit invocation submits only fixed formative evidence and returns two bounded fields", async () => {
  const worker = successfulWorker();
  const result = await run(worker.fetch);

  assert.equal(worker.calls.length, 2);
  assert.deepEqual(result, {
    assessment_audit_id: AUDIT_ID,
    assessment_url: `https://edit.legalpracticum.org/edit/assessments/${AUDIT_ID}`,
  });
  assert.deepEqual(Object.keys(result).sort(), ["assessment_audit_id", "assessment_url"]);
  assert.equal(containsSecret(result), false);
});

test("requires both protected credentials before issuing any request", async () => {
  for (const omitted of ["apiKey", "bypassToken"]) {
    let calls = 0;
    const options = {
      workerUrl: WORKER,
      provider: "google",
      apiKey: API_KEY,
      bypassToken: BYPASS,
      fetchImpl: async () => { calls += 1; },
      allowTestOrigin: true,
    };
    options[omitted] = "";
    await assert.rejects(
      runAssessmentAuditPreparation(options),
      (error) => error.code === "credentials" && !containsSecret(error.message),
    );
    assert.equal(calls, 0);
  }
});

test("the assessment CLI credential mode rejects direct environment secrets", async () => {
  await assert.rejects(
    loadCredentials({
      provider: "google",
      env: { GOOGLE_API_KEY: API_KEY, DEMO_BYPASS_TOKEN: BYPASS },
      allowDirectEnvironment: false,
    }),
    (error) => error.code === "credentials" && !containsSecret(error.message),
  );
  const loaded = await loadCredentials({
    provider: "google",
    env: { CREDENTIALS_STDIN: "1" },
    stdin: Readable.from([JSON.stringify({ api_key: API_KEY, demo_bypass_token: BYPASS })]),
    allowDirectEnvironment: false,
  });
  assert.deepEqual(loaded, { apiKey: API_KEY, bypassToken: BYPASS });
});

for (const options of [
  { workerUrl: "https://third-party.example.test", origin: ORIGIN },
  { workerUrl: "http://sonsteng-chat.damienriehl.workers.dev", origin: ORIGIN },
  { workerUrl: "https://sonsteng-chat.damienriehl.workers.dev/proxy", origin: ORIGIN },
  { workerUrl: "https://sonsteng-chat.damienriehl.workers.dev", origin: "https://evil.example" },
  { workerUrl: "https://sonsteng-chat.damienriehl.workers.dev", origin: `${ORIGIN}/path` },
]) {
  test(`rejects the unapproved target/origin pair ${options.workerUrl} / ${options.origin}`, async () => {
    let calls = 0;
    await assert.rejects(
      runAssessmentAuditPreparation({
        ...options,
        provider: "google",
        apiKey: API_KEY,
        bypassToken: BYPASS,
        fetchImpl: async () => { calls += 1; },
      }),
      (error) => error.code === "config" && !containsSecret(error.message),
    );
    assert.equal(calls, 0);
  });
}

for (const mutate of [
  (response) => { response.assessment.headings.pop(); },
  (response) => { response.assessment.headings[6].heading_id = HEADINGS[0]; },
  (response) => { response.assessment.headings[0].score = 8; },
  (response) => { response.assessment.headings[0].score = "4"; },
]) {
  test("fails closed when the seven-heading 1–7 assessment contract is incomplete", async () => {
    const response = validAssessment();
    mutate(response);
    const worker = successfulWorker(response);
    await assert.rejects(
      run(worker.fetch),
      (error) => error.code === "assessment_contract" && !containsSecret(error.message),
    );
  });
}

for (const assessment of [
  { assessment_use: "summative", summative_eligible: false },
  { assessment_use: "formative", summative_eligible: true },
]) {
  test("rejects an assessment that is not formative-only", async () => {
    const worker = successfulWorker(validAssessment(assessment));
    await assert.rejects(
      run(worker.fetch),
      (error) => error.code === "assessment_contract" && !containsSecret(error.message),
    );
  });
}

test("rejects malformed assessment JSON without surfacing its body", async () => {
  const worker = successfulWorker();
  worker.fetch = async (url, init) => {
    if (new URL(url).pathname === "/v1/session") return jsonResponse({ session_token: SESSION });
    return new Response(`malformed private body ${"x".repeat(30)}`, {
      status: 200,
      headers: { "content-type": "application/json", "access-control-allow-origin": ORIGIN },
    });
  };
  await assert.rejects(
    run(worker.fetch),
    (error) => error.code === "assessment_json" && !error.message.includes("malformed private body"),
  );
});

test("maps a request timeout to a bounded error without exposing the thrown error", async () => {
  let abortObserved = false;
  const fetchImpl = async (_url, init) => new Promise((resolve, reject) => {
    // AbortSignal.timeout() does not keep the event loop alive. A ref'ed fallback
    // makes the test deterministic across supported Node versions and also fails
    // the assertion below if the real abort signal never fires.
    const fallback = setTimeout(() => reject(new Error("timeout signal did not fire")), 1_000);
    init.signal.addEventListener("abort", () => {
      abortObserved = true;
      clearTimeout(fallback);
      reject(new Error(`timeout ${API_KEY}`));
    }, { once: true });
  });
  await assert.rejects(
    runAssessmentAuditPreparation({
      workerUrl: WORKER,
      provider: "google",
      apiKey: API_KEY,
      bypassToken: BYPASS,
      fetchImpl,
      requestTimeoutMs: 5,
      allowTestOrigin: true,
    }),
    (error) => error.code === "session_network" && !containsSecret(error.message),
  );
  assert.equal(abortObserved, true);
});

for (const reflected of [API_KEY, BYPASS, SESSION]) {
  test("rejects credential reflection anywhere in the assessment response body", async () => {
    const worker = successfulWorker({
      ...validAssessment(),
      nested: { values: ["safe", { reflected }] },
    });
    await assert.rejects(
      run(worker.fetch),
      (error) => error.code === "credential_reflection" && !containsSecret(error.message),
    );
  });
}

test("rejects credential reflection in response headers", async () => {
  let call = 0;
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return jsonResponse({ session_token: SESSION });
    return jsonResponse(validAssessment(), { headers: { "x-debug-value": API_KEY } });
  };
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "credential_reflection" && !containsSecret(error.message),
  );
});

test("rejects credential reflection in the HTTP status text", async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ session_token: SESSION }), {
    status: 200,
    statusText: API_KEY,
    headers: { "content-type": "application/json", "access-control-allow-origin": ORIGIN },
  });
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "credential_reflection" && !containsSecret(error.message),
  );
});

test("rejects credential reflection in an otherwise valid session response", async () => {
  const fetchImpl = async () => jsonResponse({ session_token: SESSION, nested: { reflected: BYPASS } });
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "credential_reflection" && !containsSecret(error.message),
  );
});

test("rejects a response that does not echo the approved origin", async () => {
  const fetchImpl = async () => jsonResponse({ session_token: SESSION }, { origin: "https://evil.example" });
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "session_origin" && !containsSecret(error.message),
  );
});

test("rejects redirects and never follows them", async () => {
  let calls = 0;
  await assert.rejects(
    run(async (_url, init) => {
      calls += 1;
      assert.equal(init.redirect, "manual");
      return new Response(null, { status: 302, headers: { location: "https://evil.example" } });
    }),
    (error) => error.code === "session_network" && !containsSecret(error.message),
  );
  assert.equal(calls, 1);
});

test("rejects non-success HTTP responses without exposing their bodies", async () => {
  let call = 0;
  const privateBody = "private upstream diagnostics that must stay bounded";
  const fetchImpl = async () => {
    call += 1;
    if (call === 1) return jsonResponse({ session_token: SESSION });
    return jsonResponse({ error: { message: privateBody } }, { status: 502 });
  };
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "assessment_http" && !error.message.includes(privateBody),
  );
});

test("rejects an unsafe or overlong audit ID", async () => {
  for (const id of ["../assessment", `memo-assessment-${"x".repeat(128)}`]) {
    const response = validAssessment();
    response.assessment_audit_id = id;
    const worker = successfulWorker(response);
    await assert.rejects(
      run(worker.fetch),
      (error) => error.code === "assessment_contract" && !error.message.includes(id),
    );
  }
});

test("rejects oversized response declarations before parsing", async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ session_token: SESSION }), {
    status: 200,
    headers: {
      "content-type": "application/json",
      "content-length": String(600 * 1024),
      "access-control-allow-origin": ORIGIN,
    },
  });
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "session_transport" && !containsSecret(error.message),
  );
});

test("stops reading an undeclared oversized response body", async () => {
  const chunk = new Uint8Array(256 * 1024);
  let pulls = 0;
  let canceled = false;
  const fetchImpl = async () => new Response(new ReadableStream({
    pull(controller) {
      pulls += 1;
      controller.enqueue(chunk);
    },
    cancel() { canceled = true; },
  }), {
    status: 200,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": ORIGIN,
    },
  });
  await assert.rejects(
    run(fetchImpl),
    (error) => error.code === "session_transport" && /size limit/.test(error.message),
  );
  assert.equal(canceled, true);
  assert.ok(pulls <= 4);
});
