// assessment-audit-store.test.js — U12 persistence contract under real
// node:sqlite. Assessment records are reconstructable, review-scope gated,
// credential-free at rest, override-attributed, and deleted at expiry.
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeCore } from "./editor-sql-helper.mjs";

const REVIEW_SCOPES = { "assessment-review": { granted: true, ver: 1 } };
const SENTINEL = "sk-live-u12-sentinel-never-persist";
const THRESHOLD_CONFIG = {
  schema_version: "memo-assessment-threshold-resolution/v1",
  source: "instructor",
  source_id: "instructor:john-local-2026",
  version: "1.1.0",
  content_hash: "sha256:instrument",
  competence_score: 5,
  redo_eligible_below: 7,
  resolution: "instructor>school>default",
  locally_supplied: true,
  authority_status: "claimed_locally_supplied",
  verified_institutional_authority: false,
};

function auditInput(overrides = {}) {
  return {
    id: "assessment-audit-1",
    assessment_use: "formative",
    evidence: {
      request: {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${SENTINEL}`,
          Cookie: `session=${SENTINEL}`,
          "X-Api-Key": SENTINEL,
        },
        body: {
          submission: "The memo evidence.",
          byok: { provider: "openai", api_key: SENTINEL },
        },
      },
      responses: [{
        provider: "openai",
        headers: { authorization: `Bearer ${SENTINEL}` },
        body: { heading_id: "governing-law", score: 4, credential: SENTINEL },
      }],
    },
    result: {
      schema_version: "1.0.0",
      assessment_use: "formative",
      summative_eligible: false,
      summative_blockers: ["human_human_calibration", "provider_terms_review"],
      instrument: {
        id: "memo-seven-heading-1-7",
        version: "1.1.0",
        content_hash: "sha256:instrument",
      },
      threshold_configuration: THRESHOLD_CONFIG,
      providers: [{ provider: "openai", model: "gpt-test", mode: "byok" }],
      headings: [{
        heading_id: "governing-law",
        score: 4,
        observations: [{ evidence_spans: ["The memo evidence."], rationale: `No secret ${SENTINEL}` }],
      }],
      apiKey: SENTINEL,
    },
    provenance: {
      config: THRESHOLD_CONFIG,
      instrument: {
        id: "memo-seven-heading-1-7",
        version: "1.1.0",
        content_hash: "sha256:instrument",
      },
      providers: [{ provider: "openai", model: "gpt-test", mode: "byok", api_key: SENTINEL }],
    },
    summative_blockers: ["human_human_calibration", "provider_terms_review"],
    retention: { days: 30 },
    credential_values: [SENTINEL],
    ...overrides,
  };
}

test("assessment audit round-trips reconstructable evidence and canonical provenance", () => {
  const clock = { value: Date.UTC(2026, 7, 20) };
  const core = makeCore(() => clock.value);

  const written = core.recordAssessmentAudit(auditInput());
  assert.deepEqual(written, {
    ok: true,
    id: "assessment-audit-1",
    schema_version: "assessment-audit/v1",
    expires_at: clock.value + 30 * 24 * 60 * 60 * 1000,
  });

  assert.deepEqual(core.readAssessmentAudit({ id: "assessment-audit-1", scopes: {} }), {
    ok: false,
    reason: "assessment_review_scope_required",
  });
  const read = core.readAssessmentAudit({ id: "assessment-audit-1", scopes: REVIEW_SCOPES });
  assert.equal(read.ok, true);
  assert.equal(read.record.schema_version, "assessment-audit/v1");
  assert.equal(read.record.assessment_use, "formative");
  assert.equal(read.record.evidence.request.body.submission, "The memo evidence.");
  assert.equal(read.record.evidence.request.headers["Content-Type"], "application/json");
  assert.deepEqual(read.record.provenance.instrument, auditInput().provenance.instrument);
  assert.deepEqual(read.record.provenance.config, auditInput().provenance.config);
  assert.deepEqual(read.record.provenance.providers, [
    { mode: "byok", model: "gpt-test", provider: "openai" },
  ]);
  assert.deepEqual(read.record.summative_blockers,
    ["human_human_calibration", "provider_terms_review"]);
  assert.equal(read.record.retention.days, 30);
  assert.equal(read.record.retention.expires_at, written.expires_at);
  assert.deepEqual(read.record.overrides, []);
});

test("every credential shape and known live value is absent from SQLite", () => {
  const core = makeCore(() => Date.UTC(2026, 7, 20));
  assert.equal(core.recordAssessmentAudit(auditInput()).ok, true);

  const row = core.sql.exec("SELECT * FROM assessment_audit_records WHERE id=?",
    "assessment-audit-1").toArray()[0];
  const stored = JSON.stringify(row);
  assert.ok(!stored.includes(SENTINEL), "the sentinel live key reached SQLite");
  assert.doesNotMatch(stored, /authorization|api[_-]?key|credential_values|"credential"/i);

  const read = core.readAssessmentAudit({ id: "assessment-audit-1", scopes: REVIEW_SCOPES });
  assert.equal(read.record.evidence.request.headers.Authorization, undefined);
  assert.equal(read.record.evidence.request.headers.Cookie, undefined);
  assert.equal(read.record.evidence.request.headers["X-Api-Key"], undefined);
  assert.equal(read.record.evidence.request.body.byok, undefined);
  assert.equal(read.record.result.apiKey, undefined);
  assert.equal(read.record.result.headings[0].observations[0].rationale, "No secret [REDACTED]");
});

test("human overrides require review scope and append author plus server timestamp", () => {
  const clock = { value: Date.UTC(2026, 7, 20) };
  const core = makeCore(() => clock.value);
  core.recordAssessmentAudit(auditInput());

  assert.deepEqual(core.recordAssessmentOverride({
    assessment_id: "assessment-audit-1",
    author: "slot:damien",
    scopes: {},
    override: { heading_id: "governing-law", score: 5, note: "Faculty judgment." },
  }), { ok: false, reason: "assessment_review_scope_required" });

  clock.value += 1234;
  const override = core.recordAssessmentOverride({
    id: "assessment-override-1",
    assessment_id: "assessment-audit-1",
    author: "slot:damien",
    scopes: REVIEW_SCOPES,
    override: {
      heading_id: "governing-law",
      score: 5,
      note: "Faculty judgment.",
      Authorization: `Bearer ${SENTINEL}`,
    },
    credential_values: [SENTINEL],
  });
  assert.deepEqual(override, {
    ok: true,
    id: "assessment-override-1",
    assessment_id: "assessment-audit-1",
    author: "slot:damien",
    created_at: clock.value,
  });

  const read = core.readAssessmentAudit({ id: "assessment-audit-1", scopes: REVIEW_SCOPES });
  assert.deepEqual(read.record.overrides, [{
    id: "assessment-override-1",
    schema_version: "assessment-override/v1",
    author: "slot:damien",
    created_at: clock.value,
    value: { heading_id: "governing-law", note: "Faculty judgment.", score: 5 },
  }]);
  const stored = JSON.stringify(core.sql.exec(
    "SELECT * FROM assessment_audit_overrides WHERE assessment_id=?", "assessment-audit-1"
  ).toArray());
  assert.ok(!stored.includes(SENTINEL));
  assert.doesNotMatch(stored, /authorization/i);
});

test("declared retention expiry deletes the audit and all overrides", () => {
  const day = 24 * 60 * 60 * 1000;
  const clock = { value: Date.UTC(2026, 7, 20) };
  const core = makeCore(() => clock.value);
  core.recordAssessmentAudit(auditInput({ retention: { days: 2 } }));
  core.recordAssessmentOverride({
    id: "assessment-override-expiring",
    assessment_id: "assessment-audit-1",
    author: "slot:damien",
    scopes: REVIEW_SCOPES,
    override: { heading_id: "governing-law", score: 5 },
  });

  clock.value += 2 * day - 1;
  assert.equal(core.expireAssessmentAudits().deleted, 0);
  assert.equal(core.readAssessmentAudit({ id: "assessment-audit-1", scopes: REVIEW_SCOPES }).ok, true);

  clock.value += 1;
  assert.deepEqual(core.expireAssessmentAudits(), { ok: true, deleted: 1 });
  assert.deepEqual(core.readAssessmentAudit({ id: "assessment-audit-1", scopes: REVIEW_SCOPES }), {
    ok: false,
    reason: "not_found",
  });
  assert.equal(core.sql.exec("SELECT * FROM assessment_audit_overrides").toArray().length, 0);
});

test("audit writes require explicit bounded retention and matching instrument provenance", () => {
  const core = makeCore();
  assert.deepEqual(core.recordAssessmentAudit(auditInput({ retention: undefined })), {
    ok: false,
    reason: "retention_required",
  });
  assert.deepEqual(core.recordAssessmentAudit(auditInput({ retention: { days: 0 } })), {
    ok: false,
    reason: "retention_invalid",
  });
  const mismatch = auditInput();
  mismatch.provenance.instrument.content_hash = "sha256:different";
  assert.deepEqual(core.recordAssessmentAudit(mismatch), {
    ok: false,
    reason: "instrument_provenance_mismatch",
  });
  const configMismatch = auditInput();
  configMismatch.provenance.config = { ...THRESHOLD_CONFIG, competence_score: 4 };
  assert.deepEqual(core.recordAssessmentAudit(configMismatch), {
    ok: false,
    reason: "config_provenance_mismatch",
  });
});

test("audit and override ids replay only byte-equivalent canonical payloads", () => {
  const core = makeCore(() => Date.UTC(2026, 7, 20));
  const input = auditInput();
  assert.equal(core.recordAssessmentAudit(input).ok, true);
  assert.equal(core.recordAssessmentAudit(input).replay, true);
  const conflict = auditInput();
  conflict.evidence.request.body.submission = "A different memo.";
  assert.deepEqual(core.recordAssessmentAudit(conflict), { ok: false, reason: "id_conflict" });

  const override = {
    id: "assessment-override-replay",
    assessment_id: input.id,
    author: "slot:damien",
    scopes: REVIEW_SCOPES,
    override: { heading_id: "governing-law", score: 5 },
  };
  assert.equal(core.recordAssessmentOverride(override).ok, true);
  assert.equal(core.recordAssessmentOverride(override).replay, true);
  assert.deepEqual(core.recordAssessmentOverride({
    ...override,
    override: { heading_id: "governing-law", score: 6 },
  }), { ok: false, reason: "id_conflict" });
});
