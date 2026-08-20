// U13 signer-review contracts: one coherent evidence/provenance view, an
// Access-human-only override path, and U11 -> U12 audit persistence.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  assessmentReviewerScopes,
  assessmentPageEndpoint,
  assessmentReadEndpoint,
  assessmentOverrideEndpoint,
} from "../src/assessment-endpoints.js";
import {
  assessmentViewModel,
  renderAssessmentReviewPage,
} from "../src/assessment-view.js";
import {
  buildAssessmentAuditInput,
  persistAssessmentAudit,
} from "../src/assessment-audit.js";
import { serveAsset } from "../src/editor-assets.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REVIEWER = {
  editor: "slot:damienadmin",
  slot: "damienadmin",
  credential_channel: "access",
  scopes: {
    edit: { granted: true, ver: 1 },
    instructor: { granted: true, ver: 1 },
    admin: { granted: true, ver: 1 },
  },
};
const UNDER_SCOPED = {
  editor: "slot:john",
  slot: "john",
  credential_channel: "access",
  scopes: {
    edit: { granted: true, ver: 1 },
    instructor: { granted: true, ver: 1 },
    admin: { granted: false, ver: 0 },
  },
};
const DEFAULT_THRESHOLDS = {
  schema_version: "memo-assessment-threshold-resolution/v1",
  source: "default",
  source_id: "memo-seven-heading-1-7",
  version: "1.1.0",
  content_hash: "sha256:instrument",
  competence_score: 4,
  redo_eligible_below: 6,
  resolution: "instructor>school>default",
  locally_supplied: false,
  authority_status: "canonical_default",
  verified_institutional_authority: false,
};

function record() {
  return {
    id: "assessment-audit-13",
    schema_version: "assessment-audit/v1",
    assessment_use: "formative",
    evidence: {
      submission: "The governing rule requires notice. Both sides dispute timing.",
      legacy: { letterGrade: "A" },
    },
    result: {
      schema_version: "1.0.0",
      assessment_use: "formative",
      assurance: "multi_provider_formative",
      letter_grade: "A",
      instrument: {
        id: "memo-seven-heading-1-7",
        version: "1.1.0",
        content_hash: "sha256:instrument",
      },
      threshold_configuration: DEFAULT_THRESHOLDS,
      providers: [
        { grader_id: "grader-1", provider: "openai", model: "gpt-test", mode: "byok" },
      ],
      headings: [
        {
          heading_id: "governing_law",
          score: 4,
          observations: [{ evidence_spans: ["The governing rule requires notice."], rationale: "Adequate rule synthesis." }],
        },
        {
          heading_id: "issues",
          score: 5,
          observations: [{ evidence_spans: ["Both sides dispute timing."], rationale: "Prioritized issue." }],
        },
      ],
    },
    provenance: {
      config: DEFAULT_THRESHOLDS,
      instrument: {
        id: "memo-seven-heading-1-7",
        version: "1.1.0",
        content_hash: "sha256:instrument",
      },
      providers: [
        { grader_id: "grader-1", provider: "openai", model: "gpt-test", mode: "byok" },
      ],
    },
    summative_blockers: ["human_human_calibration", "provider_terms_review"],
    retention: { days: 30, expires_at: Date.UTC(2026, 8, 19) },
    created_at: Date.UTC(2026, 7, 20),
    overrides: [],
  };
}

function envWith(cap = {}) {
  return {
    EDIT_ORIGIN: "https://edit.example.test",
    EDITOR: {
      getByName() {
        return {
          async readAssessmentAudit(input) {
            cap.read = input;
            return input.id === record().id ? { ok: true, record: record() } : { ok: false, reason: "not_found" };
          },
          async recordAssessmentOverride(input) {
            cap.override = input;
            return { ok: true, id: input.id, assessment_id: input.assessment_id,
              author: input.author, created_at: 1234 };
          },
          async recordAssessmentAudit(input) {
            cap.audit = input;
            return { ok: true, id: input.id, expires_at: 5678 };
          },
        };
      },
    },
  };
}

test("view renders result, provider/config/instrument provenance, and raw evidence together", async () => {
  const vm = assessmentViewModel(record());
  assert.equal(vm.headings[0].competent, true);
  assert.equal(vm.headings[0].redo_eligible, true);
  assert.equal(vm.headings[1].competent, true);
  assert.equal(vm.headings[1].redo_eligible, true);

  const html = await renderAssessmentReviewPage(record(), "DR").text();
  assert.match(html, /Assessment signer review/);
  assert.match(html, /Score 4 is competent/);
  assert.match(html, /Score 5 is competent and redo-eligible/);
  assert.match(html, /gpt-test/);
  assert.match(html, /memo-seven-heading-1-7/);
  assert.match(html, /sha256:instrument/);
  assert.match(html, /instructor&gt;school&gt;default/);
  assert.match(html, /The governing rule requires notice/);
  assert.match(html, /Adequate rule synthesis/);
  assert.doesNotMatch(html, /letter[_ -]?grade|Grade [A-F]|>A[+-]?</i);
});

test("locally supplied instructor thresholds are visibly claimed and unverified", async () => {
  const local = record();
  const config = {
    ...local.result.threshold_configuration,
    source: "instructor",
    source_id: "instructor:john-local-2026",
    competence_score: 5,
    redo_eligible_below: 7,
    locally_supplied: true,
    authority_status: "claimed_locally_supplied",
  };
  local.result.threshold_configuration = config;
  local.provenance.config = config;
  const vm = assessmentViewModel(local);
  assert.equal(vm.headings[0].competent, false);
  assert.equal(vm.headings[1].competent, true);
  const html = await renderAssessmentReviewPage(local, "DR").text();
  assert.match(html, /Resolved competence begins at score 5/);
  assert.match(html, /locally supplied, unverified instructor claim/);
  assert.match(html, /instructor:john-local-2026/);
  assert.match(html, /institutional authority has not been verified/i);
});

test("latest attributed human override becomes the effective visible score", () => {
  const overridden = record();
  overridden.overrides = [{
    id: "assessment-override-effective",
    author: "slot:damienadmin",
    created_at: Date.UTC(2026, 7, 20, 12),
    value: { heading_id: "governing_law", score: 3, note: "Evidence does not meet competence." },
  }];
  const vm = assessmentViewModel(overridden);
  assert.equal(vm.headings[0].base_score, 4);
  assert.equal(vm.headings[0].score, 3);
  assert.equal(vm.headings[0].competent, false);
  assert.equal(vm.headings[0].human_override.author, "slot:damienadmin");
});

test("override control is natively keyboard-operable and labelled for screen readers", async () => {
  const html = await renderAssessmentReviewPage(record(), "DR").text();
  assert.match(html, /<form[^>]+id="assessment-override-form"/);
  assert.match(html, /<label[^>]+for="assessment-heading"/);
  assert.match(html, /<select[^>]+id="assessment-heading"[^>]+required/);
  assert.match(html, /<label[^>]+for="assessment-score"/);
  assert.match(html, /<select[^>]+id="assessment-score"[^>]+required/);
  assert.match(html, /<label[^>]+for="assessment-note"/);
  assert.match(html, /aria-describedby="assessment-override-help"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /type="submit"/);

  const js = readFileSync(join(HERE, "..", "..", "editor", "assessment-review.js"), "utf8");
  const css = readFileSync(join(HERE, "..", "..", "editor", "assessment-review.css"), "utf8");
  assert.match(js, /X-Edit-Request/);
  assert.match(js, /crypto\.randomUUID/);
  assert.match(js, /\.focus\(\)/);
  assert.doesNotMatch(js, /innerHTML/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /@media \(max-width:\s*640px\)/);
});

test("Worker asset server exposes the built review CSS and JavaScript", async () => {
  const js = serveAsset("assessment-review.js");
  const css = serveAsset("assessment-review.css");
  assert.equal(js.status, 200);
  assert.equal(css.status, 200);
  assert.match(js.headers.get("content-type"), /javascript/);
  assert.match(css.headers.get("content-type"), /text\/css/);
  const servedJs = await js.text();
  const servedCss = await css.text();
  assert.match(servedJs, /assessment-override-form/);
  assert.match(servedCss, /\.as-review/);
  assert.equal(servedJs, readFileSync(
    join(HERE, "..", "..", "editor", "assessment-review.js"), "utf8"
  ));
  assert.equal(servedCss, readFileSync(
    join(HERE, "..", "..", "editor", "assessment-review.css"), "utf8"
  ));
});

test("only the deliberate Access reviewer maps to the store review scope", () => {
  assert.deepEqual(assessmentReviewerScopes(REVIEWER), {
    "assessment-review": { granted: true, ver: 1 },
  });
  for (const auth of [
    UNDER_SCOPED,
    { ...REVIEWER, credential_channel: "cookie" },
    { ...REVIEWER, credential_channel: "bearer" },
    { ...REVIEWER, slot: "admin", editor: "slot:admin" },
    null,
  ]) assert.equal(assessmentReviewerScopes(auth), null);
});

test("read/page routes pass only server-created scope and uniformly hide under-scoped probes", async () => {
  const cap = {};
  const env = envWith(cap);
  const get = new Request(`https://edit.example.test/edit/v1/assessment?id=${record().id}`);
  const ok = await assessmentReadEndpoint(get, env, REVIEWER);
  assert.equal(ok.status, 200);
  assert.deepEqual(cap.read.scopes, { "assessment-review": { granted: true, ver: 1 } });

  const page = await assessmentPageEndpoint(new Request(
    `https://edit.example.test/edit/assessments/${record().id}`
  ), env, REVIEWER);
  assert.equal(page.status, 200);

  const unknown = await assessmentPageEndpoint(new Request(
    "https://edit.example.test/edit/assessments/missing"
  ), env, REVIEWER);
  const deniedPage = await assessmentPageEndpoint(new Request(
    `https://edit.example.test/edit/assessments/${record().id}`
  ), env, UNDER_SCOPED);
  const deniedRead = await assessmentReadEndpoint(get, env, UNDER_SCOPED);
  assert.equal(unknown.status, 404);
  assert.equal(deniedPage.status, 404);
  assert.equal(deniedRead.status, 404);
  const unknownBody = await unknown.text();
  assert.equal(await deniedPage.text(), unknownBody);
  assert.equal(await deniedRead.text(), unknownBody);
});

test("override endpoint stamps the server identity and refuses client attribution", async () => {
  const cap = {};
  const req = new Request("https://edit.example.test/edit/v1/assessment-override", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Edit-Request": "1",
      Origin: "https://edit.example.test",
    },
    body: JSON.stringify({
      id: "assessment-override-13",
      assessment_id: record().id,
      heading_id: "governing_law",
      score: 5,
      note: "Human judgment after reviewing the evidence.",
      author: "slot:attacker",
    }),
  });
  const deniedReq = req.clone();
  const noCsrfReq = new Request("https://edit.example.test/edit/v1/assessment-override", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      id: "assessment-override-no-csrf",
      assessment_id: record().id,
      heading_id: "governing_law",
      score: 5,
      note: "Must not write.",
    }),
  });
  const noCsrfCap = {};
  const noCsrf = await assessmentOverrideEndpoint(noCsrfReq, envWith(noCsrfCap), REVIEWER);
  assert.equal(noCsrf.status, 403);
  assert.equal(noCsrfCap.override, undefined);

  const res = await assessmentOverrideEndpoint(req, envWith(cap), REVIEWER);
  assert.equal(res.status, 200);
  assert.equal(cap.override.author, REVIEWER.editor);
  assert.equal(cap.override.override.heading_id, "governing_law");
  assert.equal(cap.override.override.score, 5);
  assert.equal(cap.override.override.note, "Human judgment after reviewing the evidence.");
  assert.deepEqual(cap.override.scopes, { "assessment-review": { granted: true, ver: 1 } });

  const denied = await assessmentOverrideEndpoint(deniedReq, envWith(), UNDER_SCOPED);
  assert.equal(denied.status, 404);
});

test("successful memo persistence returns an audit id and isolates live credentials", async () => {
  const cap = {};
  const instrument = {
    id: "memo-seven-heading-1-7",
    instrument_version: "1.1.0",
    content_hash: "sha256:instrument",
    content: { thresholds: { default_competence_score: 4, default_redo_eligible_below: 6 } },
  };
  const result = record().result;
  const graders = [{ provider: "openai", model: "gpt-test", mode: "byok", apiKey: "sk-live-u13" }];
  const input = buildAssessmentAuditInput({
    id: "assessment-audit-runtime",
    submission: "A credential-free memo.",
    instrument,
    result,
    graders,
    sessionToken: "session-live-u13",
    retentionDays: 30,
  });
  assert.deepEqual(input.provenance.config, result.threshold_configuration);
  assert.deepEqual(input.credential_values.sort(), ["session-live-u13", "sk-live-u13"].sort());
  const persistedShape = { ...input };
  delete persistedShape.credential_values;
  assert.ok(!JSON.stringify(persistedShape).includes("sk-live-u13"));
  assert.ok(!JSON.stringify(persistedShape).includes("session-live-u13"));

  const out = await persistAssessmentAudit(envWith(cap), input);
  assert.deepEqual(out, { ok: true, assessment_audit_id: "assessment-audit-runtime", expires_at: 5678 });
  assert.equal(cap.audit.id, "assessment-audit-runtime");
});

test("memo endpoint persists before success and returns the server audit id without logging payloads", () => {
  const source = readFileSync(join(HERE, "..", "src", "index.js"), "utf8");
  const start = source.indexOf("async function handleMemoAssessment");
  const end = source.indexOf("// ---- POST /v1/critique", start);
  const handler = source.slice(start, end);
  const persist = handler.indexOf("persistAssessmentAudit(env");
  const success = handler.indexOf("assessment_audit_id: audit.assessment_audit_id");
  assert.ok(persist > 0 && success > persist);
  assert.match(handler, /graders: panel\.graders/);
  assert.match(handler, /sessionToken: body\.session_token/);
  const reserve = handler.indexOf("reserveOneShot(reservationId");
  const provider = handler.indexOf("completion = await callUpstream");
  const settle = handler.indexOf("settleOneShot(reservationId");
  assert.ok(reserve > 0 && provider > reserve && settle > provider);
  assert.match(handler, /catch \{\s*completion = \{ ok: false, kind: "upstream" \};\s*\}/);
  assert.doesNotMatch(handler, /checkPool\(/);
  assert.doesNotMatch(handler, /logMeta\([^)]*(submission|deliverable_text|session_token|credential_values)/s);
});

test("EditorStore schedules and re-arms automatic assessment expiry", () => {
  const source = readFileSync(join(HERE, "..", "src", "editor-store.js"), "utf8");
  assert.match(source, /async recordAssessmentAudit\(input\)/);
  assert.match(source, /await this\._scheduleAssessmentExpiry\(\)/);
  assert.match(source, /async alarm\(\)/);
  assert.match(source, /this\.core\.expireAssessmentAudits\(\)/);
  assert.match(source, /this\.storage\.setAlarm\(expiresAt\)/);
});

test("editor router wires the protected read, write, page, and same-origin assets", () => {
  const router = readFileSync(join(HERE, "..", "src", "editor.js"), "utf8");
  assert.match(router, /path === "\/edit\/v1\/assessment" && request\.method === "GET"/);
  assert.match(router, /path === "\/edit\/v1\/assessment-override" && request\.method === "POST"/);
  assert.match(router, /path\.startsWith\("\/edit\/assessments\/"\)/);
  assert.match(router, /assessmentPageEndpoint\(request, env, auth\)/);
});
