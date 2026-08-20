// Runtime seam between the formative memo panel (U11) and the reconstructable
// audit store (U12). Credential values exist only in the explicit redaction
// list passed to the store and never in evidence, result, or provenance.

const DEFAULT_RETENTION_DAYS = 30;

function validRetentionDays(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 365
    ? parsed
    : DEFAULT_RETENTION_DAYS;
}

function credentialValues(graders, sessionToken) {
  const values = [];
  if (typeof sessionToken === "string" && sessionToken.length >= 4) values.push(sessionToken);
  for (const grader of graders || []) {
    if (typeof grader?.apiKey === "string" && grader.apiKey.length >= 4) {
      values.push(grader.apiKey);
    }
  }
  return [...new Set(values)];
}

export function buildAssessmentAuditInput({
  id,
  submission,
  instrument,
  result,
  graders = [],
  sessionToken,
  retentionDays,
}) {
  const thresholds = instrument?.content?.thresholds || {};
  const providers = Array.isArray(result?.providers) ? result.providers : [];
  return {
    id,
    assessment_use: "formative",
    evidence: {
      submission,
      // These are the raw, validated grader observations that the deterministic
      // aggregation used. Keeping them beside the submission makes the result
      // independently readable without retaining provider request envelopes.
      heading_observations: (result?.headings || []).map((heading) => ({
        heading_id: heading.heading_id,
        observations: heading.observations || [],
      })),
    },
    result,
    provenance: {
      config: {
        source: "default",
        version: instrument.instrument_version,
        content_hash: instrument.content_hash,
        competence_score: thresholds.default_competence_score,
        redo_eligible_below: thresholds.default_redo_eligible_below,
        resolution: "instructor>school>default",
        locally_supplied: false,
      },
      instrument: {
        id: instrument.id,
        version: instrument.instrument_version,
        content_hash: instrument.content_hash,
      },
      providers,
    },
    summative_blockers: [...(result?.summative_blockers || [])],
    retention: { days: validRetentionDays(retentionDays) },
    credential_values: credentialValues(graders, sessionToken),
  };
}

export async function persistAssessmentAudit(env, input) {
  const result = await env.EDITOR.getByName("global-v1").recordAssessmentAudit(input);
  if (!result?.ok) return result || { ok: false, reason: "audit_persistence_failed" };
  return {
    ok: true,
    assessment_audit_id: result.id,
    expires_at: result.expires_at,
  };
}

export { DEFAULT_RETENTION_DAYS };
