// prompts.js — assembles the three server-built prompts.
//
// Segment A (the shared, byte-stable, cacheable prefix) and the debrief/critique
// templates are extracted VERBATIM from app/worker/prompts/*.md at BUILD time by
// tools/build_worker_personas.py and embedded in personas.generated.json. This
// module never re-parses the .md files; it renders Segment B mechanically from a
// persona's injection JSON (per the SEGMENT B algorithm documented in
// system-template.md) and fills the {{SLOTS}} in the evaluator templates.
//
// These functions are PURE (no I/O, no globals) so the golden-file unit test can
// drive them directly. index.js supplies the embedded strings from the bundle.

// Fixed trigger -> plain-language gloss map (mirrors Segment A definitions).
const TRIGGER_GLOSS = {
  open_ended_invitation: "invited your story with a genuinely open question",
  wellbeing_question: "asked how you are holding up as a person",
  acknowledged_emotion: "named and validated a feeling you showed",
  no_interruption_streak: "let you finish without interrupting",
  confidentiality_reassurance: "assured you this is private and privileged",
  nonjudgmental_response: "met a hard admission without any judgment",
  follow_up_on_hint: "gently followed up on a hint you dropped",
  explained_process: "explained what happens next, easing your worry",
};

const TIER_LEAD = {
  volunteered:
    "These are the things you came here to say — offer them naturally and early, in your own words:",
  revealed_if_asked:
    "These are true things you will say plainly, but ONLY when the interviewer actually asks about that subject — never volunteered, never hidden:",
  rapport_gated:
    "These are sensitive things you share ONLY after trust is genuinely earned. For each, the conditions that must be met before it can surface are stated with it:",
  concealed:
    "These you actively protect. Do not raise them; if the interviewer probes near them, deflect in character — a partial answer, a change of subject, a quiet hedge — never confirm, and never invent anything new to cover:",
  unknown:
    "These are case-relevant things you genuinely do NOT know. If they come up, answer with your unknown-response style — never guess:",
};

const EMPTY_TIER = "(nothing in this tier).";

function renderIdentity(identity) {
  let line = "You are " + identity.name;
  if (identity.age !== undefined && identity.age !== null) line += ", age " + identity.age;
  if (identity.occupation) line += ", " + identity.occupation;
  if (identity.pronouns) line += ". Your pronouns are " + identity.pronouns + ".";
  else line += ".";
  line += " In this matter you are the " + identity.role + ".";
  return line;
}

function renderRapportItem(item) {
  let s = item.text + " — hold this until ";
  const hasTurns = item.min_turns !== undefined && item.min_turns !== null;
  const reqs = Array.isArray(item.requires) ? item.requires : [];
  const gloss = reqs.map((t) => TRIGGER_GLOSS[t] || t).join(" and ");
  if (hasTurns && reqs.length) {
    s += "at least " + item.min_turns + " turns have passed AND the interviewer has genuinely " + gloss + ".";
  } else if (hasTurns) {
    s += "at least " + item.min_turns + " turns have passed.";
  } else {
    s += "the interviewer has genuinely " + gloss + ".";
  }
  return s;
}

function renderTierBody(tier, items) {
  if (!items || items.length === 0) return EMPTY_TIER;
  if (tier === "rapport_gated") return items.map(renderRapportItem).join(" ");
  if (tier === "concealed") return items.map((it) => it.text + " — keep this concealed.").join(" ");
  return items.map((it) => it.text).join(" ");
}

// Render Segment B (the per-persona slot) from a persona injection object.
// Returns the text with NO trailing newline; buildSystemPrompt adds framing.
export function renderPersona(persona) {
  const blocks = [];
  blocks.push("# THE PERSON YOU ARE PLAYING");
  blocks.push(renderIdentity(persona.identity));
  blocks.push("Background: " + persona.background);
  blocks.push("Personality: " + persona.personality);
  blocks.push("How you feel right now: " + persona.emotional_state);
  blocks.push("How you talk: " + persona.communication_style);

  const of = persona.objectives_fears || {};
  const objectives = of.objectives || [];
  const fears = of.fears || [];
  let ofBlock = "What you want out of this: " + objectives.join("; ") + ".";
  if (fears.length) ofBlock += "\n" + "What you are afraid of: " + fears.join("; ") + ".";
  blocks.push(ofBlock);

  blocks.push(
    "Your disposition is " + persona.disposition +
      ". Let it color everything, exactly as your instructions describe that disposition."
  );

  blocks.push("## What you know, and what it takes to say it");
  const disclosure = persona.disclosure || {};
  for (const tier of ["volunteered", "revealed_if_asked", "rapport_gated", "concealed", "unknown"]) {
    blocks.push(TIER_LEAD[tier] + "\n" + renderTierBody(tier, disclosure[tier]));
  }

  blocks.push("## The edges of what you know");
  const kb = persona.knowledge_boundary || {};
  let kbBody =
    'For anything case-relevant that is not written above, you do not know it — say it in character, like this: "' +
    kb.unknown_response_style + '"';
  const colors = kb.color_topics || [];
  if (colors.length) {
    kbBody += "\n" + "The only things you may add freely, as harmless texture, are: " +
      colors.join("; ") + ". Never let color turn into a case fact.";
  }
  blocks.push(kbBody);

  const r42 = persona.rule_4_2 || {};
  if (r42.applies === true) {
    blocks.push("## Someone should be here with you");
    blocks.push(
      "You are represented by your own lawyer" +
        (r42.counsel_name ? ", " + r42.counsel_name : "") +
        ", and the person interviewing you is on the OTHER side of this. React as your " +
        "instructions describe: once, in character, uneasy — ask whether your lawyer " +
        "should be here — then stay guarded. Do not lecture; you just sense something is off."
    );
  }

  blocks.push("Stay this person. Speak only as them.");
  return blocks.join("\n\n");
}

// The complete system prompt: verbatim Segment A + blank line + Segment B, with a
// trailing newline. Byte-identical to test/fixtures/rendered-system-prompt-m00.txt
// for persona m00.per.tester. `rule_4_2.applies` must already reflect whether THIS
// interviewer is on an opposing side (index.js resolves that before calling).
export function buildSystemPrompt(segmentA, persona) {
  return segmentA + "\n\n" + renderPersona(persona) + "\n";
}

// ---- Evaluator prompt builders ----------------------------------------------

function fillSlots(template, slots) {
  let out = template;
  for (const [key, val] of Object.entries(slots)) {
    out = out.split("{{" + key + "}}").join(val);
  }
  return out;
}

// Build the {{TIER_DATA}} ground-truth block for /debrief. The evaluator sees the
// fact text (to detect what was elicited) plus the out-of-band topic_label; the
// DEBRIEF-ORACLE RULE in the template forbids echoing un-elicited text back out.
export function buildTierData(persona, factMap) {
  const disclosure = persona.disclosure || {};
  const lines = [];
  for (const tier of ["volunteered", "revealed_if_asked", "rapport_gated", "concealed", "unknown"]) {
    const items = disclosure[tier] || [];
    lines.push("[" + tier + "]");
    if (items.length === 0) {
      lines.push("  (none)");
      continue;
    }
    for (const it of items) {
      const meta = (factMap && factMap[it.fact_ref]) || {};
      const label = meta.topic_label || "(unlabeled topic)";
      let line = "  - fact_ref: " + it.fact_ref + " | topic_label: " + label + " | text: " + it.text;
      if (tier === "rapport_gated") {
        const parts = [];
        if (it.min_turns !== undefined && it.min_turns !== null) parts.push("min_turns=" + it.min_turns);
        if (Array.isArray(it.requires) && it.requires.length) parts.push("requires=[" + it.requires.join(", ") + "]");
        if (parts.length) line += " | " + parts.join(" ");
      }
      lines.push(line);
    }
  }
  return lines.join("\n");
}

function buildRule42Block(persona, interviewerOnOpposingSide) {
  const r42 = persona.rule_4_2 || {};
  const represented = persona.represented_by_counsel === true || r42.applies === true;
  const parts = [];
  parts.push("represented_by_counsel: " + (represented ? "true" : "false"));
  parts.push("rule_4_2.applies: " + (r42.applies === true ? "true" : "false"));
  if (r42.counsel_name) parts.push("counsel_name: " + r42.counsel_name);
  parts.push("interviewer_on_opposing_side: " + (interviewerOnOpposingSide ? "true" : "false"));
  return parts.join("\n");
}

function renderTranscript(transcript) {
  return transcript
    .map((m, i) => {
      const who = m.role === "assistant" ? "CLIENT" : "INTERVIEWER";
      return "[" + (i + 1) + "] " + who + ": " + m.content;
    })
    .join("\n");
}

// Build the /debrief evaluator prompt. `template` = bundle.debrief_template.
export function buildDebriefPrompt(template, opts) {
  const { matterId, personaId, persona, factMap, transcript, interviewerOnOpposingSide } = opts;
  return fillSlots(template, {
    MATTER_ID: matterId,
    PERSONA_ID: personaId,
    DISPOSITION: persona.disposition || "",
    TIER_DATA: buildTierData(persona, factMap),
    RULE_4_2: buildRule42Block(persona, interviewerOnOpposingSide),
    TRANSCRIPT: renderTranscript(transcript || []),
  });
}

// {criterion_id: name} for every criterion AND subcriterion in a rubric, so the
// UI can render real names instead of "Criterion NN". Returned by /v1/critique
// as a backward-compatible sibling of the scorecard (see API-CONTRACTS.md).
export function rubricCriteriaLabels(rubric) {
  const labels = {};
  for (const c of (rubric && rubric.criteria) || []) {
    if (c && typeof c.id === "string" && typeof c.name === "string") labels[c.id] = c.name;
    for (const s of (c && c.subcriteria) || []) {
      if (s && typeof s.id === "string" && typeof s.name === "string") labels[s.id] = s.name;
    }
  }
  return labels;
}

// Build the /critique evaluator prompt. `template` = bundle.critique_template.
export function buildCritiquePrompt(template, opts) {
  const { matterId, rubricId, rubric, deliverable } = opts;
  const rubricJson = typeof rubric === "string" ? rubric : JSON.stringify(rubric, null, 2);
  return fillSlots(template, {
    MATTER_ID: matterId,
    RUBRIC_ID: rubricId,
    RUBRIC_JSON: rubricJson,
    DELIVERABLE: deliverable,
  });
}
