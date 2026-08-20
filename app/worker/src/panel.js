// panel.js — formative-only memo panel orchestration and aggregation.
//
// This module never persists, logs, or returns a provider credential. Provider
// adapters receive credentials only through the injected `complete` call. The
// returned result is a fresh allowlisted object suitable for U12 persistence.

import { buildMemoAdjudicationPrompt, buildMemoScorecardPrompt } from "./prompts.js";
import { parseModelJson, validateMemoScorecard } from "./validate.js";
import { validResolvedAssessmentThresholdConfig } from "./assessment-config.js";

const SUMMATIVE_BLOCKERS = ["human_human_calibration", "provider_terms_review"];
const DEFAULT_SCORECARD_TEMPLATE = "{{ASSESSMENT_INSTRUMENT_JSON}}\n\n{{SUBMISSION}}";

function safeProvenance(grader) {
  return { provider: grader.provider, model: grader.model, mode: grader.mode };
}

function deterministicHash(value) {
  // FNV-1a-style 32-bit hash. It is not a security primitive; it only supplies
  // a stable shuffle key so identical evidence yields identical call ordering.
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function shuffledGraders(graders, submission, instrument) {
  const seed = `${instrument.content_hash}\u0000${submission}`;
  return graders
    .map((grader, index) => ({
      grader,
      index,
      order: deterministicHash(`${seed}\u0000${grader.provider}\u0000${grader.model}\u0000${index}`),
    }))
    .sort((a, b) => a.order - b.order || a.index - b.index)
    .map((entry) => entry.grader);
}

function discreteMedian(scores) {
  const ordered = [...scores].sort((a, b) => a - b);
  // Assessment scores must stay integral. For an even panel use the lower
  // observed middle rather than inventing a fractional score.
  return ordered[Math.floor((ordered.length - 1) / 2)];
}

function headingMap(scorecard) {
  return new Map(scorecard.headings.map((heading) => [heading.heading_id, heading]));
}

// Reduce already-validated grader scorecards. The caller may supply proposed
// adjudication scores; only headings with spread >=2 consume them, and code
// constrains each proposal to the observed range before it becomes the result.
export function aggregateMemoPanel(entries, adjudicatedScores = {}) {
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new TypeError("at least one validated grader entry is required");
  }
  const maps = entries.map((entry) => headingMap(entry.scorecard));
  const headingIds = entries[0].scorecard.headings.map((heading) => heading.heading_id);

  return {
    headings: headingIds.map((headingId) => {
      const observations = entries.map((entry, index) => {
        const heading = maps[index].get(headingId);
        if (!heading) throw new TypeError(`validated scorecard missing ${headingId}`);
        return {
          grader_id: `grader-${index + 1}`,
          ...safeProvenance(entry.grader),
          score: heading.score,
          evidence_spans: [...heading.evidence_spans],
          rationale: heading.rationale,
        };
      });
      const scores = observations.map((observation) => observation.score);
      const observedMin = Math.min(...scores);
      const observedMax = Math.max(...scores);
      const medianScore = discreteMedian(scores);
      const spread = observedMax - observedMin;
      const proposed = adjudicatedScores[headingId];
      const useAdjudication = spread >= 2 && Number.isInteger(proposed);
      const constrained = useAdjudication
        ? Math.min(observedMax, Math.max(observedMin, proposed))
        : medianScore;
      return {
        heading_id: headingId,
        score: constrained,
        median_score: medianScore,
        observed_min: observedMin,
        observed_max: observedMax,
        spread,
        adjudication: spread >= 2
          ? {
              triggered: true,
              ...(useAdjudication ? { proposed_score: proposed, score: constrained } : {}),
            }
          : { triggered: false },
        observations,
      };
    }),
  };
}

function contestedEvidence(entries, aggregate) {
  return aggregate.headings
    .filter((heading) => heading.spread >= 2)
    .map((heading) => ({
      heading_id: heading.heading_id,
      observed_min: heading.observed_min,
      observed_max: heading.observed_max,
      anonymous_observations: heading.observations.map((observation, index) => ({
        grader: `anonymous-${index + 1}`,
        score: observation.score,
        evidence_spans: observation.evidence_spans,
        rationale: observation.rationale,
      })),
    }));
}

function parseAdjudication(text, expectedHeadingIds) {
  const parsed = parseModelJson(text);
  if (!parsed || !Array.isArray(parsed.headings) || parsed.headings.length !== expectedHeadingIds.length) {
    return { ok: false, errors: ["adjudication headings invalid"] };
  }
  const expected = new Set(expectedHeadingIds);
  const scores = {};
  for (const heading of parsed.headings) {
    if (!heading || typeof heading !== "object" || Array.isArray(heading) ||
        Object.keys(heading).some((key) => key !== "heading_id" && key !== "score") ||
        !expected.has(heading.heading_id) || scores[heading.heading_id] !== undefined ||
        !Number.isInteger(heading.score) || heading.score < 1 || heading.score > 7) {
      return { ok: false, errors: ["adjudication result invalid"] };
    }
    scores[heading.heading_id] = heading.score;
  }
  if (Object.keys(scores).length !== expected.size) {
    return { ok: false, errors: ["adjudication result incomplete"] };
  }
  return { ok: true, scores };
}

function addUsage(totals, usage) {
  for (const key of ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]) {
    totals[key] = (totals[key] || 0) + (Number.isFinite(usage && usage[key]) ? usage[key] : 0);
  }
}

function containsLiveCredential(result, graders) {
  const serialized = JSON.stringify(result);
  return graders.some((grader) =>
    typeof grader.apiKey === "string" && grader.apiKey.length > 0 && serialized.includes(grader.apiKey)
  );
}

// Run each explicitly resolved grader once. The first deterministically shuffled
// grader performs a second blind pass only when at least one heading is
// contested. `complete` is injected so tests never issue provider requests.
export async function runFormativeMemoPanel({
  submission,
  instrument,
  thresholdConfig,
  graders,
  complete,
  scorecardTemplate = DEFAULT_SCORECARD_TEMPLATE,
}) {
  if (typeof submission !== "string" || !submission || !instrument ||
      !validResolvedAssessmentThresholdConfig(thresholdConfig, instrument) ||
      !Array.isArray(graders) || graders.length === 0 || typeof complete !== "function") {
    return { ok: false, kind: "validation", errors: ["memo panel input invalid"] };
  }

  const orderedGraders = shuffledGraders(graders, submission, instrument);
  const entries = [];
  const usage = {};
  for (const grader of orderedGraders) {
    const prompt = buildMemoScorecardPrompt(scorecardTemplate, { instrument, submission });
    const completion = await complete({ grader, kind: "grader", prompt, maxTokens: 2200, jsonMode: true });
    if (!completion || !completion.ok) {
      return { ok: false, kind: "upstream", grader, upstreamResult: completion || { kind: "upstream" } };
    }
    const parsed = parseModelJson(completion.text);
    const checked = parsed && validateMemoScorecard(parsed, submission, instrument);
    if (!checked || !checked.ok) {
      return { ok: false, kind: "validation", errors: checked ? checked.errors : ["unparseable grader output"] };
    }
    entries.push({ grader, scorecard: checked.scorecard });
    addUsage(usage, completion.usage);
  }

  const preliminary = aggregateMemoPanel(entries, {});
  const contested = contestedEvidence(entries, preliminary);
  let adjudicatedScores = {};
  let adjudicator = null;
  if (contested.length > 0) {
    const grader = orderedGraders[0];
    const prompt = buildMemoAdjudicationPrompt({ instrument, submission, contestedHeadings: contested });
    const completion = await complete({ grader, kind: "adjudication", prompt, maxTokens: 500, jsonMode: true });
    if (!completion || !completion.ok) {
      return { ok: false, kind: "upstream", grader, upstreamResult: completion || { kind: "upstream" } };
    }
    const checked = parseAdjudication(completion.text, contested.map((heading) => heading.heading_id));
    if (!checked.ok) return { ok: false, kind: "validation", errors: checked.errors };
    adjudicatedScores = checked.scores;
    adjudicator = safeProvenance(grader);
    addUsage(usage, completion.usage);
  }

  const aggregate = aggregateMemoPanel(entries, adjudicatedScores);
  const providers = orderedGraders.map((grader, index) => ({
    grader_id: `grader-${index + 1}`,
    ...safeProvenance(grader),
  }));
  const result = {
    schema_version: "1.0.0",
    assessment_use: "formative",
    summative_eligible: false,
    summative_blockers: [...SUMMATIVE_BLOCKERS],
    assurance: providers.length === 1 ? "reduced_assurance" : "multi_provider_formative",
    instrument: {
      id: instrument.id,
      version: instrument.instrument_version,
      content_hash: instrument.content_hash,
    },
    threshold_configuration: thresholdConfig,
    providers,
    ...(adjudicator ? { adjudicator } : {}),
    headings: aggregate.headings,
  };
  // Defense in depth: canonical construction excludes credential-shaped fields,
  // and this value scan ensures a live secret cannot enter evidence/rationale by
  // way of an anomalous provider response or the untrusted submission.
  if (containsLiveCredential(result, graders)) {
    return { ok: false, kind: "validation", errors: ["credential material rejected"] };
  }
  return {
    ok: true,
    usage,
    result,
  };
}
