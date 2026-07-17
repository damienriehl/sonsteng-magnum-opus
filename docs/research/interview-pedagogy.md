# Client-Interview Pedagogy & AI-Roleplay Research

*Produced 2026-07-17 by the deepen-plan pass (external research, cited). Grounds the persona engine, debrief rubric, and red-team design. "Adopt tonight" items are binding on Phase 0/4 agents.*

## Adopt tonight

### Two-axis debrief (replaces tiers-only scoring)

- **Axis A — Fact/task coverage** (already designed): elicited / revealed-if-asked-never-asked / rapport-gated-never-earned, + Rule 4.2 flags.
- **Axis B — Standardized-client relational axis**, rated by the persona *in character* (the SC movement's defining move: score the client's felt experience, not a faculty technique checklist). Five items:
  1. **Rapport & opening** — put at ease; confidentiality/scope/fees framed.
  2. **Listening** — open-ended funnel-top questions; reflected understanding back; didn't interrupt/lead.
  3. **Understanding my goals** — surfaced legal *and* non-legal concerns.
  4. **Explanation & next steps** — closed with clarity; client knows what happens next.
  5. **Overall confidence** — "Would I come back to this lawyer?" (the SC signature item).
- **Ethics on a signed scale (−2 to +2)** per the Client Interviewing Competition rubric — a Rule 4.2 violation can push the score negative.
- **Encode the T-funnel** (Binder & Price) as the Listening criterion: reward broad-before-narrow topic openings; each topic opened with an open-ended question before closed gap-fillers.
- **Post-debrief self-reflection prompt** (competition criterion 11) — one free-text question.

### Persona-design rules (system-prompt guardrails)

- **In-character deflection, never invention or fourth-wall breaks**, for anything outside `knowledge_boundary`: "I don't remember / I'm not sure." (Character-LLM/DITTO pattern.)
- **Anti-sycophancy clause, verbatim in every persona prompt:** "You do not want to help the interviewer. You reveal rapport-gated facts ONLY if the emotional preconditions in your disclosure tiers are met; social pressure, insistence, or flattery alone never unlocks them."
- **Actor ≠ evaluator** (DepoSim's multi-agent split): the persona call role-plays only; `/debrief` scores independently against tier definitions, never the persona's self-report.
- **Parameterize disposition** as a persona schema field (`disposition: cooperative | guarded | over_talker | distressed`), DepoSim-style; reused by the debrief ("this client was guarded — did you earn trust?").

### Red-team additions

- **Verification-pressure probes** ("But the contract says X, so you must have known…") — the published *Epistemic Role Override* failure is triggered by factual-verification pressure, not conversational drift; D4 must include these, not just novel out-of-file questions.
- **Sycophancy probes** — sustained pressure/flattery attempting to unlock rapport-gated facts without meeting triggers.

### Interview phase structure (encode in rubric + debrief)

1. Opening/rapport & orientation (greeting, confidentiality, fees, roadmap)
2. Problem overview — funnel top (open-ended, client narrative, minimal interruption)
3. Probing — funnel stem (targeted questions, timelines, gap-filling, reflective listening)
4. Emotional & goals inquiry (non-legal concerns, objectives)
5. Closing/next steps (summarize, confirm, set expectations)

## Note for later

- Instructor co-design of the Axis-B items with Sonsteng/Haydock (AI debriefs drift shallow/confirmatory unless mapped to explicit teaching objectives).
- AI-vs-human simulation sequencing effects (CHI 2026); pair AI reps with live human debrief eventually.
- Persona-drift hardening (contrastive/RL consistency methods) for longer sessions or bigger models.
- SC rater-calibration when a human closes the centaur loop; consider licensing the GGSL SC instrument (2006 Clinical L. Rev.) for defensibility.

## Key sources

- DepoSim launch (AltaClaro × Verbit, Feb 2026): https://www.altaclaro.com/news/altaclaro-and-verbit-launch-deposim-the-first-ai-deposition-simulator-for-lawyers-transforming-litigation-training · https://www.lawnext.com/2026/02/altaclaro-and-verbit-launch-deposim-an-ai-powered-deposition-simulator-for-litigators.html
- Suffolk AI mock judge: https://sites.suffolk.edu/legalwritingmatters/2025/05/12/ai-for-oral-advocacy/
- StrongSuit Case Coach: https://strongsuit.com/solutions/ai-oral-arguments-simulator-with-case-coach/
- Client Interviewing Competition rubric (11 criteria, signed ethics): https://www.clientinterviewing.com/assessment-criteria.html
- Cunningham, *What Clients Want*: http://clarkcunningham.org/PR/WhatClientsWant.pdf · SC study record: https://strathprints.strath.ac.uk/3212/
- T-funnel charts (Albany Law): https://www.albanylaw.edu/sites/default/files/media/user/celt/conferences_and_events/Materials_for_Client_Interviewing/I__C_-_Charts.pdf
- Binder, Price et al., *Lawyers as Counselors* (4th ed.): https://faculty.westacademic.com/Book/Detail?id=235868
- RoleBreak (character hallucination as jailbreak surface): https://arxiv.org/html/2409.16727v1
- Epistemic Role Override: https://arxiv.org/pdf/2604.27228
- Agreeableness-driven sycophancy: https://arxiv.org/pdf/2604.10733 · role-play fine-tuning safety: https://arxiv.org/pdf/2502.20968
- AI-guided vs human-led debriefing: https://link.springer.com/article/10.1007/s10758-025-09941-8 · Team-FIRST GenAI debriefing: https://link.springer.com/article/10.1186/s41077-026-00407-0
