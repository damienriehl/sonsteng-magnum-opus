#!/usr/bin/env node
// Explicit, credential-safe preparation of one disposable formative assessment
// audit for supervised signer UAT. Importing this module performs no work.

import { pathToFileURL } from "node:url";

import { MEMO_HEADING_IDS } from "../src/validate.js";
import {
  DEV_WORKER_ORIGIN,
  REQUEST_TIMEOUT_MS,
  SmokeError,
  assertCredentialAbsent,
  credentialValues,
  loadCredentials,
  nonempty,
  parseJsonResponse,
  request,
  responseText,
  validateProvider,
  workerBaseUrl,
  workerEndpoint,
} from "./live-stream-smoke.mjs";

const DEFAULT_REQUEST_ORIGIN = "https://legalpracticum.org";
const ACCESS_ORIGIN = "https://edit.legalpracticum.org";
const APPROVED_REQUEST_ORIGINS = new Set([
  DEFAULT_REQUEST_ORIGIN,
  "https://sonsteng-dev.damienriehl.com",
]);
const AUDIT_ID = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$/;
const MAX_RESPONSE_BYTES = 512 * 1024;

// Fictional, non-identifying, disposable evidence with one paragraph for each
// canonical memo heading. The command never accepts or prints alternate text.
export const FIXED_DISPOSABLE_MEMO = [
  "Governing law: For this fictional exercise, assume a shop must use reasonable care after receiving notice of a dangerous spill.",
  "Strengths and weaknesses of both sides: Avery has a timestamped notice message, while the shop can dispute whether staff had enough time to respond.",
  "Issues: The central questions are when the shop received notice and whether its response time was reasonable.",
  "Suggested solutions: Preserve the notice message and camera footage, interview the employees, and consider an early confidential settlement conference.",
  "Theory and themes: Avery's theory is preventable harm after clear notice; the shop's theme is a sudden condition followed by a reasonable response.",
  "Elements to prevail: Avery must establish duty, breach, causation, and damages with admissible evidence.",
  "Liabilities and remedies: Potential liability is limited to the fictional negligence claim, with compensatory damages as the requested remedy.",
].join("\n\n");

function fail(code, message) {
  throw new SmokeError(code, message);
}

function approvedRequestOrigin(origin, { allowTestOrigin = false } = {}) {
  let parsed;
  try {
    parsed = new URL(origin);
  } catch {
    fail("config", "ORIGIN must be an approved absolute HTTPS origin.");
  }
  if (parsed.href !== `${parsed.origin}/` || parsed.username || parsed.password ||
      parsed.protocol !== "https:") {
    fail("config", "ORIGIN must be an approved absolute HTTPS origin.");
  }
  if (!APPROVED_REQUEST_ORIGINS.has(parsed.origin) && !allowTestOrigin) {
    fail("config", "ORIGIN is not approved for assessment preparation.");
  }
  return parsed.origin;
}

function assertResponseHeadersSafe(response, secrets) {
  assertCredentialAbsent({
    status_text: response.statusText,
    headers: [...response.headers.entries()],
  }, secrets);
}

function assertCors(response, origin, code) {
  if (response.headers.get("access-control-allow-origin") !== origin) {
    fail(code, "The Worker did not confirm the approved request origin.");
  }
}

async function boundedResponseText(response, code) {
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
    fail(code, "The Worker response exceeded the audit preparer's size limit.");
  }
  const text = await responseText(response, code);
  if (Buffer.byteLength(text) > MAX_RESPONSE_BYTES) {
    fail(code, "The Worker response exceeded the audit preparer's size limit.");
  }
  return text;
}

function assertJsonResponse(response, code) {
  if (!/^application\/json(?:;|$)/i.test(response.headers.get("content-type") || "")) {
    fail(code, "The Worker did not return JSON.");
  }
}

function validateSession(value) {
  if (!value || Array.isArray(value) || typeof value !== "object" ||
      !nonempty(value.session_token) || value.session_token.length > 8192) {
    fail("session_contract", "Session mint returned no bounded session token.");
  }
  return value.session_token;
}

function validateAssessment(value) {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    fail("assessment_contract", "The assessment response was not an object.");
  }
  const assessment = value.assessment;
  if (!assessment || Array.isArray(assessment) || typeof assessment !== "object" ||
      assessment.assessment_use !== "formative" || assessment.summative_eligible !== false) {
    fail("assessment_contract", "The Worker did not return a formative-only assessment.");
  }
  if (!Array.isArray(assessment.headings) || assessment.headings.length !== MEMO_HEADING_IDS.length) {
    fail("assessment_contract", "The assessment did not contain all seven memo headings.");
  }
  const seen = new Set();
  for (const heading of assessment.headings) {
    if (!heading || Array.isArray(heading) || typeof heading !== "object" ||
        !MEMO_HEADING_IDS.includes(heading.heading_id) || seen.has(heading.heading_id) ||
        !Number.isInteger(heading.score) || heading.score < 1 || heading.score > 7) {
      fail("assessment_contract", "The assessment did not independently score the seven memo headings on the 1–7 scale.");
    }
    seen.add(heading.heading_id);
  }
  if (MEMO_HEADING_IDS.some((headingId) => !seen.has(headingId))) {
    fail("assessment_contract", "The assessment did not contain all seven memo headings.");
  }
  if (!nonempty(value.assessment_audit_id) || !AUDIT_ID.test(value.assessment_audit_id)) {
    fail("assessment_contract", "The Worker returned no bounded assessment audit ID.");
  }
  return value.assessment_audit_id;
}

export async function runAssessmentAuditPreparation({
  workerUrl = DEV_WORKER_ORIGIN,
  provider,
  apiKey,
  bypassToken,
  origin = DEFAULT_REQUEST_ORIGIN,
  fetchImpl = fetch,
  requestTimeoutMs = REQUEST_TIMEOUT_MS,
  allowTestOrigin = false,
} = {}) {
  validateProvider(provider);
  if (!nonempty(apiKey) || !nonempty(bypassToken)) {
    fail("credentials", "A provider API key and DEV bypass token are required.");
  }
  if (!Number.isInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > REQUEST_TIMEOUT_MS) {
    fail("config", "The request timeout must be between 1 and 90000 milliseconds.");
  }

  const base = workerBaseUrl(workerUrl, { allowTestOrigin });
  if (!allowTestOrigin && base.href !== `${DEV_WORKER_ORIGIN}/`) {
    fail("config", "WORKER_URL must be the exact approved DEV Worker origin.");
  }
  const requestOrigin = approvedRequestOrigin(origin, { allowTestOrigin });
  const initialSecrets = credentialValues(apiKey, bypassToken);
  const headers = { Origin: requestOrigin };

  const sessionUrl = workerEndpoint(base, "/v1/session");
  sessionUrl.searchParams.set("bypass", bypassToken);
  const sessionResponse = await request(
    fetchImpl,
    sessionUrl,
    { method: "GET", headers },
    "session_network",
    requestTimeoutMs,
  );
  assertResponseHeadersSafe(sessionResponse, initialSecrets);
  assertCors(sessionResponse, requestOrigin, "session_origin");
  assertJsonResponse(sessionResponse, "session_contract");
  const sessionText = await boundedResponseText(sessionResponse, "session_transport");
  assertCredentialAbsent(sessionText, initialSecrets);
  if (sessionResponse.status !== 200) fail("session_http", `Session mint failed with HTTP ${sessionResponse.status}.`);
  const sessionToken = validateSession(parseJsonResponse(sessionText, "session_json"));

  const allSecrets = [...initialSecrets, sessionToken];
  const assessmentUrl = workerEndpoint(base, "/v1/memo-assessment");
  const body = {
    session_token: sessionToken,
    deliverable_text: FIXED_DISPOSABLE_MEMO,
    assessment_use: "formative",
    byok: { provider, api_key: apiKey },
  };
  const assessmentResponse = await request(
    fetchImpl,
    assessmentUrl,
    {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify(body),
    },
    "assessment_network",
    requestTimeoutMs,
  );
  assertResponseHeadersSafe(assessmentResponse, allSecrets);
  assertCors(assessmentResponse, requestOrigin, "assessment_origin");
  assertJsonResponse(assessmentResponse, "assessment_contract");
  const assessmentText = await boundedResponseText(assessmentResponse, "assessment_transport");
  assertCredentialAbsent(assessmentText, allSecrets);
  if (assessmentResponse.status !== 200) {
    fail("assessment_http", `Assessment preparation failed with HTTP ${assessmentResponse.status}.`);
  }
  const auditId = validateAssessment(parseJsonResponse(assessmentText, "assessment_json"));
  const report = {
    assessment_audit_id: auditId,
    assessment_url: `${ACCESS_ORIGIN}/edit/assessments/${encodeURIComponent(auditId)}`,
  };
  assertCredentialAbsent(report, allSecrets);
  return report;
}

async function main() {
  const provider = process.env.PROVIDER || "";
  const credentials = await loadCredentials({ provider, allowDirectEnvironment: false });
  return runAssessmentAuditPreparation({
    provider,
    apiKey: credentials.apiKey,
    bypassToken: credentials.bypassToken,
    workerUrl: process.env.WORKER_URL || DEV_WORKER_ORIGIN,
    origin: process.env.ORIGIN || DEFAULT_REQUEST_ORIGIN,
  });
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === invokedPath) {
  main()
    .then((report) => console.log(JSON.stringify(report)))
    .catch((error) => {
      const code = error instanceof SmokeError ? error.code : "unexpected";
      const message = error instanceof SmokeError ? error.message : "Unexpected assessment-preparation failure.";
      console.error(`assessment UAT preparation failed [${code}]: ${message}`);
      process.exitCode = code === "config" || code === "credentials" ? 2 : 1;
    });
}
