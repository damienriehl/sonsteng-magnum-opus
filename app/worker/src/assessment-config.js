// Request-scoped threshold resolution for the seven-heading memo instrument.
// School and instructor records are local claims, not verified institutional
// policy. Unknown fields fail closed so a misspelled threshold never silently
// falls back to the canonical defaults.

const REQUEST_SCHEMA_VERSION = "memo-assessment-threshold-config/v1";
const RESOLUTION_SCHEMA_VERSION = "memo-assessment-threshold-resolution/v1";
const RESOLUTION = "instructor>school>default";
const ENVELOPE_KEYS = new Set(["schema_version", "school", "instructor"]);
const RECORD_KEYS = new Set(["id", "competence_score", "redo_eligible_below"]);
const SOURCE_ID = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(value, keys) {
  return Object.keys(value).every((key) => keys.has(key));
}

function validScore(value) {
  return Number.isInteger(value) && value >= 1 && value <= 7;
}

function validThresholdRecord(value) {
  return isRecord(value) && hasOnlyKeys(value, RECORD_KEYS) &&
    Object.keys(value).length === RECORD_KEYS.size && SOURCE_ID.test(value.id || "") &&
    validScore(value.competence_score) && validScore(value.redo_eligible_below) &&
    value.competence_score <= value.redo_eligible_below;
}

function canonicalDefaults(instrument) {
  const thresholds = instrument?.content?.thresholds;
  if (!isRecord(thresholds) || !validScore(thresholds.default_competence_score) ||
      !validScore(thresholds.default_redo_eligible_below) ||
      thresholds.default_competence_score > thresholds.default_redo_eligible_below ||
      typeof instrument?.id !== "string" || !SOURCE_ID.test(instrument.id) ||
      typeof instrument?.instrument_version !== "string" ||
      typeof instrument?.content_hash !== "string") {
    return null;
  }
  return {
    id: instrument.id,
    competence_score: thresholds.default_competence_score,
    redo_eligible_below: thresholds.default_redo_eligible_below,
  };
}

function resolvedConfig(source, record, instrument) {
  const locallySupplied = source !== "default";
  return {
    schema_version: RESOLUTION_SCHEMA_VERSION,
    source,
    source_id: record.id,
    competence_score: record.competence_score,
    redo_eligible_below: record.redo_eligible_below,
    resolution: RESOLUTION,
    locally_supplied: locallySupplied,
    authority_status: locallySupplied ? "claimed_locally_supplied" : "canonical_default",
    verified_institutional_authority: false,
    version: instrument.instrument_version,
    content_hash: instrument.content_hash,
  };
}

export function validResolvedAssessmentThresholdConfig(config, instrument) {
  if (!isRecord(config) || !hasOnlyKeys(config, new Set([
    "schema_version", "source", "source_id", "competence_score",
    "redo_eligible_below", "resolution", "locally_supplied", "authority_status",
    "verified_institutional_authority", "version", "content_hash",
  ])) || config.schema_version !== RESOLUTION_SCHEMA_VERSION ||
      !["default", "school", "instructor"].includes(config.source) ||
      !SOURCE_ID.test(config.source_id || "") || !validScore(config.competence_score) ||
      !validScore(config.redo_eligible_below) ||
      config.competence_score > config.redo_eligible_below || config.resolution !== RESOLUTION ||
      config.version !== instrument?.instrument_version ||
      config.content_hash !== instrument?.content_hash ||
      config.verified_institutional_authority !== false) return false;
  const local = config.source !== "default";
  if (config.locally_supplied !== local ||
      config.authority_status !== (local ? "claimed_locally_supplied" : "canonical_default")) {
    return false;
  }
  if (!local) {
    const defaults = canonicalDefaults(instrument);
    return defaults !== null && config.source_id === defaults.id &&
      config.competence_score === defaults.competence_score &&
      config.redo_eligible_below === defaults.redo_eligible_below;
  }
  return true;
}

export function resolveAssessmentThresholdConfig(requestConfig, instrument) {
  const defaults = canonicalDefaults(instrument);
  if (!defaults) {
    return { ok: false, kind: "server", error: "Canonical assessment thresholds are invalid." };
  }
  if (requestConfig === undefined) {
    return { ok: true, config: resolvedConfig("default", defaults, instrument) };
  }
  if (!isRecord(requestConfig) || !hasOnlyKeys(requestConfig, ENVELOPE_KEYS) ||
      requestConfig.schema_version !== REQUEST_SCHEMA_VERSION ||
      (requestConfig.school === undefined && requestConfig.instructor === undefined) ||
      (requestConfig.school !== undefined && !validThresholdRecord(requestConfig.school)) ||
      (requestConfig.instructor !== undefined && !validThresholdRecord(requestConfig.instructor))) {
    return {
      ok: false,
      kind: "request",
      error: "assessment_config must be a closed v1 envelope with valid school or instructor thresholds.",
    };
  }
  const source = requestConfig.instructor ? "instructor" : "school";
  return { ok: true, config: resolvedConfig(source, requestConfig[source], instrument) };
}

export { REQUEST_SCHEMA_VERSION, RESOLUTION_SCHEMA_VERSION };
