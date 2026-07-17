---
title: "feat: Curriculum Build-Out — Data Spine, 20-Matter Corpus, Client-Interview Simulator, Business-of-Law, Platform Site"
type: feat
status: active
date: 2026-07-17
origin: docs/brainstorms/2026-07-17-curriculum-buildout-brainstorm.md
---

# ✨ Curriculum Build-Out — The Big Push

## Overview

One large orchestrated build that turns the repo's pedagogical spec (`docs/master-outline.md`) into real substance: a machine-readable **data spine** (skills/tasks taxonomy FOLIO-mapped, 20 deep synthetic matters across two jurisdiction tiers, client personas with disclosure rules, per-matter + firm-level business-of-law data) plus the software that delivers it (client-interview chat app on a capped Cloudflare Worker; a generated student-facing curriculum site on the existing deploy).

**Tonight's bar (see brainstorm: docs/brainstorms/2026-07-17-curriculum-buildout-brainstorm.md, Decision 11):** demo-ready for Profs. John Sonsteng & Roger Haydock — taxonomy browsable, matters readable, chat app live on DEV with several personas working end-to-end, curriculum site navigable.

**Orchestration (Decision 10):** this Fable session orchestrates; Opus subagents execute. Content fleet runs ~20+ parallel agents in phased waves.

## Problem Statement

The repo holds an unusually complete *description* of the Sonsteng practicum (curriculum modules, 8-part exercise anatomy, rubric system, six matter archetypes, 17+9 skills survey) but **zero materials, zero data, zero code**. The practicum cannot be demoed, adopted, or extended until the spec becomes artifacts. Simultaneously, every existing simulated-matter corpus (Trialbook/NITA) is rights-encumbered; this build creates the MIT-clean replacement.

## Proposed Solution — the Data Spine

All five workstreams hang off one schema-first spine (brainstorm Decision 9). Site pages and packets are *rendered from* the data; the chat Worker *reads* the data. Nothing is authored twice.

### Repo structure (target)

```
data/
├── schemas/            # JSON Schema per entity (skill, task, matter, persona, rubric,
│                       #   exercise, firm, time-entry, invoice, trust-entry, engagement)
├── taxonomy/
│   ├── skills.json         # Sonsteng 17+9, extended; FOLIO IRIs
│   ├── tasks.json          # primary tasks → subtasks per skill
│   └── folio-crosswalk.json
├── jurisdictions/
│   ├── meridian.json       # THE canon: courts, judges, citation scheme, geography
│   └── real/<st>.json      # MN NY CA FL IL TX: court structure + research pointers
├── firm/                   # firm-level dataset (generated BEFORE matters — C6)
├── matters/<slug>/
│   ├── matter.json         # metadata, parties, jurisdiction, skill/task mappings, sides
│   ├── facts.md            # ground-truth master fact pattern
│   ├── case-file/          # witness statements, documents, exhibits
│   ├── exercise/           # 8-part packet content (per master-outline anatomy)
│   ├── personas/*.json     # interviewable people w/ disclosure tiers
│   ├── rubric.json
│   └── business/           # intake, conflicts, engagement letter, time entries,
│                           #   billing statement, trust-ledger entries
app/
├── chat/               # static vanilla-JS chat UI (self-contained, no external requests)
└── worker/             # CF Worker: persona injection, caps, demo/BYOK modes
site/                   # existing pitch page + generated platform pages (site/platform/…)
tools/
├── build_site.py       # generator: data spine → static HTML (Python 3, stdlib only)
├── validate_spine.py   # schema + cross-link + money-math validator
└── redteam.md + probe scripts for chat acceptance
```

## Technical Approach

### Workstream 1 — Skills & task taxonomy

- Spine = Sonsteng's **17 Legal Practice Skills + 9 Law Practice Management Skills**, exact names per `docs/research/skills-survey.md` (both phrasings preserved).
- Each skill decomposes into **primary tasks → subtasks** (the "most common tasks most lawyers perform"), each task carrying: description, Bloom level, module placement (M1 Foundational / M2 Substantive+Skills / M3 Transition), exercise cross-refs.
- **FOLIO mapping:** every skill/task maps into the FOLIO **Services** branch (verified live via the folio MCP: five families — Advisory, Regulatory Non-Dispute, Dispute, Transactional, Bankruptcy/Restructuring — with task-level concepts like Discovery Practice, Opinion Memo Practice, Settlement/Demand/Collection, ADR, Lease, Purchase & Sale, Employment Transactions). **Miss policy (SpecFlow C3):** nearest-parent IRI + `mapping_confidence: exact|near|parent`, or explicit `no_folio_equivalent` — no forced mappings. Non-Services branches (areas_of_law, document_artifacts, objectives) may supplement where apt.
- AI-era extension skills (prompting, AI-output verification, centaur workflow) added as a clearly-marked **extension set**, never mixed into the surveyed 26.

### Workstream 2 — Synthetic matter corpus (20 matters, all deep)

**Shape × jurisdiction matrix** (10 shapes × 2 tiers; real-tier assignments chosen for pedagogical fit):

| # | Shape (Sonsteng analog) | Fictional tier | Real tier |
|---|---|---|---|
| 1 | Employment arbitration (*Midstate v. Rogers*) | Meridian | **IL** |
| 2 | Attorney discipline (*Halbrock*) | Meridian | **MN** (OLPR) |
| 3 | Auto-negligence jury trial (*Reagan v. Jacobson*) | Meridian | **FL** |
| 4 | Real-estate purchase negotiation (*Peters/Taylor/Thomas*) | Meridian | **TX** |
| 5 | Criminal DWI (*State v. James*) | Meridian | **MN** (§ 169A) |
| 6 | Non-compete / trade secrets (*SSHC v. Baines*) | Meridian | **NY** (CA bans non-competes — itself noted as teaching content) |
| 7 | UCC sale-of-goods dispute | Meridian | **NY** |
| 8 | Juvenile delinquency | Meridian | **CA** (WIC) |
| 9 | Marriage dissolution (custody + property) | Meridian | **CA** (community property) |
| 10 | Wills & probate contest | Meridian | **FL** (homestead) |

- Every matter: full **8-part exercise anatomy** (intro, objectives, activities, instructions, case file, procedural/factual history, considerations, substantive info) + witness statements + documents/exhibits + `rubric.json` + business layer. All parties, facts, and documents original; MIT-clean (brainstorm Decision 2).
- **Two-sided matters (SpecFlow A7):** `matter.json` declares `sides` (e.g., plaintiff/defense; buyer/seller with per-side confidential facts for the RE shape); packets and personas carry a `role` dimension.
- **Statute-citation policy (SpecFlow C1, confirmed by Damien):** student-facing packets are **facts-only + jurisdiction designation** — students find the law themselves (that's the legal-research pedagogy of the real tier). Instructor notes may cite key statutes, marked as instructor-verified-at-publish.
- **Name-collision sweep (SpecFlow C4):** validator greps all invented person/firm names; matter agents instructed to use collision-resistant names; discipline + DWI matters get an extra manual check (defamation-shaped risk).
- **Instructor layer (SpecFlow A2):** each matter ships `exercise/instructor-notes.md` (teaching notes + answer key + intentional-error flags) — kept in a separate file from student packets so casual browsing doesn't spoil (B1 note).

### Workstream 3 — Persona engine + chat app

**Persona schema** (`data/schemas/persona.schema.json`):
- Identity, background, personality, emotional state, communication style, objectives/fears.
- **Five disclosure tiers (brainstorm Decision 5):** `volunteered` / `revealed_if_asked` / `rapport_gated` / `concealed` / `unknown`.
  - **Rapport-gated is operationalized (SpecFlow C2):** each rapport-gated fact carries machine-checkable triggers (e.g., `min_turns: 6`, `requires: [open_ended_wellbeing_question, no_interruption]`) rendered into the system prompt as concrete conditions.
  - **Fact-fidelity pinning (SpecFlow B4 — first-class schema field):** `knowledge_boundary` pins all material facts to the case file; case-relevant unknowns answer "I don't remember / I'm not sure"; free improvisation allowed only for listed `color_topics`. No legal knowledge beyond the persona's layperson background (B3).
- `interviewable_by: [role…]` per persona (SpecFlow A6). Opposing-party personas (confirmed by Damien): interviewing a represented opposing party triggers an in-app **professional-responsibility teaching moment** (Rule 4.2 no-contact flag, logged in the debrief), not a silent block.

**Worker architecture** (`app/worker/`):
- **Server-side persona injection (SpecFlow B1 — architecture constraint):** client sends `{matter_id, persona_id, messages, session_token}`; the Worker holds persona files and builds the system prompt server-side. Known property (stated in docs): the MIT repo publishes personas anyway — concealment is honor-system for real students, same as Sonsteng's paper confidential-fact handouts.
- **Demo mode:** hosted **Haiku** (`claude-haiku-4-5`; confirm ID/pricing via the claude-api skill at build time — SpecFlow C8), **prompt caching** on the persona system prompt, per-request `max_tokens` cap.
- **Caps, all server-side (SpecFlow B5):** 1 turn = user message + reply; in-character warning ~15 ("the client checks their watch"), in-character wrap-up at 20 + transcript-export prompt. **$10/day** total (brainstorm resolved q.2) tracked in a **Durable Object** counter (KV is eventually consistent), reset at midnight UTC; **$7 public / $3 reserved for the demo-bypass token** (SpecFlow B6).
- **NAT-safe limiting (SpecFlow B6):** session-token-based limits primary (N sessions/token-issue/day), per-IP only as a coarse abuse brake; **demo bypass token** (unlisted URL param) exempts John & Roger from IP limits and draws on the reserve.
- **BYOK production mode (brainstorm Decision 6, SpecFlow B1):** documented path = **deploy-your-own-Worker** with your key as a secret (matches OSS-adopter flow; no third-party key custody). Convenience tier = direct browser→Anthropic (`anthropic-dangerous-direct-browser-access`, key in localStorage, client-visible prompts — documented tradeoff).
- **Failure & abuse:** API 429/529 → retry once, then in-character "bad phone connection," no turn burned (B5). Abusive input → persona ends the interview in character ("walks out") (B7). CORS allowlist = exactly the DEV/PROD origins (B10).
- **Privacy policy, published on the chat page (SpecFlow B8):** transcripts never stored server-side; browser-only (`sessionStorage`, survives refresh — B5); Worker logs metadata only (token counts, timestamps, hashed IP), no message content (consistent with B7 logging stance).

**Chat UI** (`app/chat/`): vanilla JS, self-contained (repo ethos), responsive + large-type friendly (John & Roger on iPads — B9), suggested opening question on first load (B9), transcript **copy/download** at session end (SpecFlow A3), frontend-design skill applies.

**Centaur back-half tonight (SpecFlow A1, confirmed by Damien — full first-pass loop):**
- **IN — `/debrief` endpoint:** scores the interview transcript against the persona's disclosure tiers: *facts you elicited / revealed-if-asked you never asked / rapport-gated you never earned* (+ Rule 4.2 flags). Makes the tier engine visible in the demo.
- **IN — `/critique` endpoint:** paste-your-deliverable (memo, letter, pleading) → rubric-based first-pass critique against the matter's `rubric.json` (criterion-by-criterion, point-weighted, Sonsteng re-write-loop framing). Sized for 4-page memos (~4k input tokens; own `max_tokens` budget; counts against the daily cap like chat turns).
- **OUT (labeled "coming soon"):** faculty submission & review channel, accounts.

### Workstream 4 — Business-of-law data

- **Build order (SpecFlow C6): firm first.** `data/firm/` — simulated two-person student firm (per the course's firm model): identity, rate card, book of business (the 20 matters + closed-matter history), AR aging, realization/collection rates, budget. Matter agents then *reference* the firm canon (rates, client IDs) so money data reconciles.
- **Per-matter money layer (brainstorm Decision 7):** intake form, conflicts check, engagement letter/fee agreement (fee type varies by shape: hourly, contingency (tort), flat (DWI), retainer), time entries → billing statement, trust-ledger entries.
- **Trust-ledger correctness (SpecFlow C7):** ledgers are pedagogically clean; any intentional errors only in instructor notes.
- **Money-math machine-checked:** validator sums time entries ↔ billing statements, balances trust ledgers, reconciles matter ↔ firm book (acceptance D2).
- **Surfacing (SpecFlow A4):** money-layer docs embedded in each matter packet as exhibits **and** a **Firm Dashboard** page rendered from `data/firm/` (dataviz skill applies); raw JSON/CSV downloads linked.

### Workstream 5 — Curriculum platform site

- `tools/build_site.py` (Python 3, **stdlib only** — zero new dependencies) renders: platform home, 3 module pages (Foundational → Substantive+Skills → Transition), **skills browser** (26 + extension set, tasks/subtasks, FOLIO IRIs), **matter library** (shape-first with Meridian/real-state toggle — SpecFlow A5), per-matter packet pages (single page + in-page TOC per 8-part anatomy, print stylesheet for the faculty flow — C5/A2), rubric pages, firm dashboard, chat entry per matter/persona.
- Pitch page stays; platform grows beside it. Self-contained ethos preserved (embedded styling, no external requests). frontend-design skill governs the aesthetic; skills-browser ↔ matter-library links bidirectional (D8); every nav path ends in content or an explicit "coming soon" (D8/D10).
- **OSS adopter quickstart (SpecFlow A8):** README section — clone → serve site → deploy-your-own-Worker for chat; wording "no platform fees; bring your own Anthropic key." Executed once from a clean clone as acceptance (D9).

## Implementation Phases (fleet orchestration)

**Phase 0 — Foundations (sequential, small; the consistency contract).**
All schemas; `meridian.json` canon; real-state jurisdiction stubs; shape×state matrix locked; persona system-prompt template; content style guide for fleet agents (voice, naming rules, collision-resistant names, length targets); validator scaffold. *Success: validator runs green on a hand-built sample matter stub.*

**Phase 1 — Taxonomy (parallel with Phase 0 tail).**
FOLIO Services-branch extraction via MCP → `skills.json`, `tasks.json`, `folio-crosswalk.json` with miss policy. *Success: all 26 skills decomposed; every task has an IRI or explicit no-equivalent; validator green.*

**Phase 2 — Firm canon (blocks matter fleet).**
`data/firm/` complete. *Success: money-math validator green at firm level.*

**Phase 3 — Matter fleet (the big fan-out).**
20 Opus agents in parallel (worktree isolation not needed — disjoint `data/matters/<slug>/` dirs), each producing one complete matter (facts, case file, 8-part packet, personas, rubric, business layer) against schemas + canons. Then a **consistency/QA wave**: validator + reviewer agents (cross-matter voice, name-collision sweep, money reconciliation, fact-fidelity of personas vs. facts.md). *Success: 20/20 validator-green matters.*

**Phase 4 — Chat app + Worker (parallel with Phase 3; needs Phase 0 persona template + 1 sample persona).**
Worker (chat + debrief + critique endpoints, DO spend counter, caps, bypass token, CORS) + chat/critique UI + red-team script. Worker deploy needs the Anthropic key as a secret — **stop and ask Damien before wiring credentials** (global rule). *Success: acceptance D3–D7 pass on DEV.*

**Phase 5 — Site generation (needs Phases 1–3 content).**
Generator + all pages; visual QA via headful puppeteer on Xwayland (no-DISPLAY workaround per ops memory). *Success: D8 nav-completeness; screenshots reviewed.*

**Phase 6 — Ship & rehearse.**
Full validator + red-team runs; DEV deploy (`-p sonsteng`, never `--remove-orphans`); README delta + THIRD-PARTY.md (target: none needed); evidence pack (EP-IDs, screenshots, transcripts); literal rehearsal of the John & Roger walkthrough (D7). PROD deploy only with Damien's explicit go.

## System-Wide Impact

- **Interaction graph:** chat UI → Worker `/chat` → persona file + DO counter → Anthropic API; `/debrief` → transcript + persona tiers → Anthropic; site pages → static only. Generator: data spine → HTML; validator: data spine → CI-style gate. No other systems touched; pitch site untouched.
- **Error propagation:** Anthropic errors surface as in-character recoveries (B5); cap/limit hits as designed banners, never raw errors; validator failures block ship, not runtime.
- **State lifecycle risks:** only server state = DO spend/session counters (reset UTC-midnight; overshoot tolerance stated). Transcripts deliberately client-only — refresh survives via sessionStorage; browser close loses them (documented).
- **API surface parity:** demo mode and BYOK/self-hosted Worker expose identical endpoints; direct-browser convenience mode documented as reduced-privacy variant.
- **Integration risks unit tests won't catch:** classroom-NAT rate limiting (D5 simulates shared-IP sessions); prompt-cache interaction with per-persona prompts; CORS on the DEV origin; concealed-tier leakage under sustained adversarial pressure (D3).

## Acceptance Criteria (adopted from SpecFlow §D)

**Data spine**
- [ ] D1 `tools/validate_spine.py` green: every cross-ref (skill→module→exercise→matter→rubric→persona) resolves; zero orphans; 20/20 matters schema-conform; all FOLIO IRIs verified via MCP or marked `no_folio_equivalent`.
- [ ] D2 Money-math green: time entries Σ = billing statements; trust ledgers balance; matter ↔ firm reconciles.

**Chat app**
- [ ] D3 Red-team (~15 adversarial prompts × 3 personas): zero concealed-tier leaks; zero out-of-character raw errors; abuse ends in character.
- [ ] D4 Fact-fidelity probe (10 out-of-file material questions per tested persona): no invented material facts.
- [ ] D5 Caps server-side: scripted client cannot exceed 20 turns or breach spend; graceful cap/turn messages; bypass token works; 5 sessions on one IP under token don't trip limits.
- [ ] D6 Transcript export works; refresh preserves transcript; privacy statement visible.
- [ ] D7 E2E on DEV, clean browser + mobile viewport: pick matter → read packet → 10+ turn interview eliciting ≥1 revealed-if-asked fact → debrief → export transcript. Rehearsed literally.
- [ ] D11 Critique endpoint: a sample 4-page memo submitted against a matter rubric returns criterion-by-criterion, point-referenced critique; oversized input rejected gracefully; spend counted against the daily cap.

**Site & repo**
- [ ] D8 No dead links; all four flows traversable; skills ↔ matters bidirectional.
- [ ] D9 OSS quickstart executed once from a clean clone; README updated.
- [ ] D10 Out-of-scope list published (below) so demo-night gaps read as roadmap.

## Out of Scope (tonight — "coming soon" labels)

Faculty submission/review channel; accounts/auth; PROD deploy (separate explicit go); OCR restoration of crown-jewel articles; book layer; LMS integrations; non-English localization.

## Dependencies & Risks

- **Anthropic key as Worker secret** — gated on Damien (credentials rule). Fleet token spend is large (sanctioned "go wide").
- **Real-state accuracy** — mitigated by facts-only packet policy (pending confirmation) + instructor-note citations verified at publish.
- **20-agent consistency** — mitigated by Phase 0 canons + QA wave; the validator is the backstop.
- **Haiku persona discipline** — mitigated by tier phrasing, deflection style, red-team gate (D3/D4); residual risk accepted for demo.
- **Demo-night failure modes** pre-addressed: NAT limits (B6), CORS (B10), blank-chat-box (B9), cap mid-demo (reserve).

## Resource Requirements

Fable orchestrator (this session) + Opus subagent fleet (~25–30 agents across phases); Cloudflare account (Pages + Workers + DO); Anthropic API key (demo, $10/day cap); no new repo dependencies.

## Future Considerations

More shapes/states; faculty portal (submission/review closes the human half of the centaur loop); FOLIO crosswalk upstreamed to the FOLIO project; the firm dataset can grow into a full practice-management sandbox.

## Documentation Plan

README (platform sections + quickstart); THIRD-PARTY.md if any asset sneaks in; per-matter instructor notes; privacy paragraph on chat page; this plan + brainstorm as design record.

## Sources & References

### Origin
- **Brainstorm:** [docs/brainstorms/2026-07-17-curriculum-buildout-brainstorm.md](../brainstorms/2026-07-17-curriculum-buildout-brainstorm.md) — all 11 locked decisions + 2 resolved questions carried forward (spine = Sonsteng 17+9 + FOLIO; 20 deep matters, Meridian + 6 real states; persona engine + capped-Haiku/BYOK chat app; per-matter + firm biz data; web-first site; data-spine monorepo; demo-ready bar).

### Internal
- Requirements spec: `docs/master-outline.md` (curriculum, 8-part anatomy, rubric system, matter archetypes)
- Skills canon: `docs/research/skills-survey.md` (exact 17+9 names; Table-4 percentages unreliable — do not use)
- Deploy: `deploy/deploy-dev.sh` (⚠️ `-p sonsteng`, never `--remove-orphans`), `deploy/deploy-prod.sh`
- SpecFlow gap analysis: incorporated throughout (A1–A8, B1–B10, C1–C8, D1–D10)

### External
- FOLIO ontology via MCP (Services branch verified live this session)
- Anthropic API details via the local claude-api skill at build time
