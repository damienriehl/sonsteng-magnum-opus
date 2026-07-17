---
title: "feat: Curriculum Build-Out — Data Spine, 20-Matter Corpus, Client-Interview Simulator, Business-of-Law, Platform Site"
type: feat
status: active
date: 2026-07-17
origin: docs/brainstorms/2026-07-17-curriculum-buildout-brainstorm.md
---

# ✨ Curriculum Build-Out — The Big Push

## Enhancement Summary

**Deepened on:** 2026-07-17 (ultrathink pass) · **Agents:** 12 parallel (architecture, security, simplicity/YAGNI, performance, agent-native, frontend-races, data-integrity reviewers + claude-api, Cloudflare, frontend-design, dataviz, legal-ed-pedagogy researchers) · **SpecFlow gap analysis** ran pre-plan.

**Key improvements (all folded in below):**
1. **Phase 0 is now a hard "consistency contract"** — frozen ID registry + full matter manifest, deterministic matter-prefixed IDs, `schema_version` + schema freeze, fixed 8-part section keys, closed rapport-trigger enum, strict rubric schema, countable depth floor, structured debrief/critique scorecards. (The unanimous finding: JSON Schema guarantees shape, not vocabulary — vocabulary drift is the #1 20-agent risk.)
2. **Worker hardened** — request-field whitelisting, preflight-reserve/settle-actual DO accounting, session-mint throttling, debrief-oracle protection, input-size caps, CORS-on-every-response; full build contract embedded below + `docs/research/worker-llm-facts.md`.
3. **The 4096-token cache floor** — Haiku silently won't cache a 3k persona prompt; shared-boilerplate-first prompt structure + history cache breakpoint takes a session from ~$0.164 to ~$0.055.
4. **Two-axis debrief** — disclosure-tier fact coverage + standardized-client relational scoring (in-character), signed −2/+2 ethics, T-funnel criterion (`docs/research/interview-pedagogy.md`).
5. **Chat UI race-proofing** — client state machine, per-turn idempotency key, server-authoritative `{turn, remaining, state}`, `pageshow`/bfcache recovery, storage guards.
6. **Simplifications adopted** — direct-browser BYOK cut to "future"; intentional-error injection + full answer keys deferred (teaching-notes stub tonight); firm↔matter reconciliation demoted to WARN; non-streaming tonight.
7. **Design + viz + validator specs are now implementable contracts:** `docs/research/design-direction.md` ("The Practicum Press"), `docs/research/firm-dashboard-viz-spec.md`, `docs/research/validator-spec.md`.

**Conflicts resolved:** DO vs KV counter → **DO kept** (architecture/Cloudflare/performance vs simplicity; single-authority cap + SQLite persistence, with in-character graceful degradation). Streaming vs not → **non-streaming tonight** (claude-api/Cloudflare vs performance; typing indicator covers 2–4s; streaming is the named fast-follow if D7 rehearsal feels slow). Turnstile vs cut-the-token-subsystem → **middle path**: session-mint throttling via the DO `mints` table + per-IP issuance ceiling tonight; Turnstile as fast-follow.

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
- **Instructor layer (SpecFlow A2, trimmed per simplicity review):** each matter ships a **light `exercise/instructor-notes.md` stub** (teaching notes; key statutes where apt, instructor-verified) — separate file from student packets so casual browsing doesn't spoil (B1 note). **Full answer keys + intentional-error injection are deferred** to the faculty-portal push (they're ×20 authoring load that fights the money-math validator and serves no one tonight).

### Workstream 3 — Persona engine + chat app

**Persona schema** (`data/schemas/persona.schema.json`):
- Identity, background, personality, emotional state, communication style, objectives/fears.
- **Five disclosure tiers (brainstorm Decision 5):** `volunteered` / `revealed_if_asked` / `rapport_gated` / `concealed` / `unknown`.
  - **Rapport-gated is operationalized (SpecFlow C2):** each rapport-gated fact carries machine-checkable triggers (e.g., `min_turns: 6`, `requires: [open_ended_wellbeing_question, no_interruption]`) rendered into the system prompt as concrete conditions. **The trigger vocabulary is a CLOSED enum defined in `persona.schema.json` in Phase 0** (architecture + simplicity + agent-native reviews, unanimous) — free-string triggers would give 20 dialects the Worker and `/debrief` can't reason over; unknown token = validator ERROR.
  - **Disposition parameter** (DepoSim pattern): `disposition: cooperative | guarded | over_talker | distressed` — a schema field, reused by the debrief ("this client was guarded — did you earn trust?").
  - **Anti-sycophancy clause, verbatim in every persona prompt** (published sycophancy failure mode): social pressure, insistence, or flattery never unlocks rapport-gated facts — only the specified preconditions do. See `docs/research/interview-pedagogy.md`.
  - **Stable `@id`s + JSON-LD context** (agent-native): every skill/task/matter/persona/rubric-criterion gets a canonical `@id`; cheap now (FOLIO IRIs already in play), expensive to retrofit across 20 matters.
  - **Fact-fidelity pinning (SpecFlow B4 — first-class schema field):** `knowledge_boundary` pins all material facts to the case file; case-relevant unknowns answer "I don't remember / I'm not sure"; free improvisation allowed only for listed `color_topics`. No legal knowledge beyond the persona's layperson background (B3).
- `interviewable_by: [role…]` per persona (SpecFlow A6). Opposing-party personas (confirmed by Damien): interviewing a represented opposing party triggers an in-app **professional-responsibility teaching moment** (Rule 4.2 no-contact flag, logged in the debrief), not a silent block.

**Worker architecture** (`app/worker/`):
- **Server-side persona injection (SpecFlow B1 — architecture constraint):** client sends `{matter_id, persona_id, messages, session_token}`; the Worker holds persona files and builds the system prompt server-side. Known property (stated in docs): the MIT repo publishes personas anyway — concealment is honor-system for real students, same as Sonsteng's paper confidential-fact handouts.
- **Demo mode:** hosted **Haiku** (`claude-haiku-4-5` — ID + pricing verified, see `docs/research/worker-llm-facts.md`), per-request `max_tokens: 300` (chat) / dedicated budgets for debrief+critique. **Prompt caching, corrected:** Haiku's minimum cacheable prefix is **4096 tokens** — the system prompt puts shared boilerplate (tier rules, deflection, anti-sycophancy, Rule 4.2, format) FIRST, padded past the floor, persona-specifics after, byte-stable; plus a second `cache_control` breakpoint on the latest conversation turn. Cached session ≈ **$0.055** (~127/$7); assert `cache_read_input_tokens > 0` in the test harness.
- **Request-field whitelisting (security, CRITICAL):** the Worker constructs the *entire* Anthropic request server-side — hard-coded model, server-built system prompt, server-set `max_tokens`; client input is untrusted `messages` content only. The client can never influence model, system, max_tokens, or tools (else the key becomes a free general-purpose proxy).
- **Input-size caps, server-side (security):** per-message byte cap, total-input-token cap per request, message-array length derived from DO session state (never trusted from the client); `/critique` input size-checked with the graceful 413.
- **Non-streaming tonight** (claude-api + Cloudflare reviews): Haiku replies land in 2–4s; typing indicator covers it; spend accounting reads `usage` from one JSON response. Streaming = named fast-follow only if the D7 rehearsal feels slow.
- **Caps, all server-side (SpecFlow B5):** 1 turn = user message + reply; in-character warning ~15 ("the client checks their watch"), in-character wrap-up at 20 + transcript-export prompt. **$10/day** total (brainstorm resolved q.2) tracked in a **Durable Object** counter (KV is eventually consistent), reset at midnight UTC; **$7 public / $3 reserved for the demo-bypass token** (SpecFlow B6).
- **NAT-safe limiting (SpecFlow B6):** session-token-based limits primary (HMAC-signed stateless token = identity; DO = authoritative turn counter), per-IP only as a coarse abuse brake; **demo bypass token** (unlisted URL param, `timingSafeEqual`, scrubbed from all logs — it's in URLs/history, so rotatable with blast radius = the $3 reserve) exempts John & Roger from IP limits and draws on the reserve. **Session-mint throttling (security — the real DoS vector):** free unlimited token minting would drain the $7 pool by script; the DO `mints` table caps issues/day + a per-IP issuance ceiling; Turnstile = fast-follow, not tonight.
- **Spend accounting (security, CRITICAL):** `preflight` reserve-check *before* the Anthropic call (atomic in the DO, increments the turn), `settle` actual usage after — both awaited; bounds overshoot to ~one turn. A scripted concurrent client must not be able to race past the cap (acceptance D5).
- **BYOK production mode (brainstorm Decision 6, SpecFlow B1):** the ONE documented path = **deploy-your-own-Worker** with your key as a secret (matches OSS-adopter flow; no third-party key custody; reuses the exact Worker, so API parity is real). ~~Direct-browser convenience tier~~ **cut from tonight** (architecture + security + simplicity, unanimous: third code path, no caps, breaks parity, XSS-exfiltrable localStorage key) — documented as future only (mechanics preserved in `docs/research/worker-llm-facts.md` §5).
- **Debrief-oracle protection (security, HIGH):** `/debrief` must never enumerate missed *facts* — it names the tier/topic missed ("you never explored the client's financial pressure"), requires a session token tied to a real completed interview, and never echoes concealed content verbatim; else an empty-transcript call dumps the whole answer key. Dedicated red-team probe (D3).
- **Failure & abuse:** API 429/529 → retry once, then in-character "bad phone connection," no turn burned (B5). Abusive input → persona ends the interview in character ("walks out") (B7). CORS allowlist = exactly the DEV/PROD origins (B10).
- **Privacy policy, published on the chat page (SpecFlow B8, security-amended):** transcripts never stored server-side; browser-only (`sessionStorage`, survives refresh — B5); Worker logs metadata only — **HMAC-with-server-salt IP** (a plain IPv4 hash is brute-forceable), token counts, timestamps, no message content, no query strings (bypass token). The notice **discloses third-party processing** (conversations transit Anthropic's API + its retention posture) — else "never stored" is misleading. A **"don't paste confidential client info / PII"** warning sits by the `/critique` paste box.

**Chat UI** (`app/chat/`): vanilla JS, self-contained (repo ethos), responsive + large-type friendly (John & Roger on iPads — B9), suggested opening question on first load (B9), transcript **copy/download** at session end (SpecFlow A3). Design per `docs/research/design-direction.md` §6 (consultation-room aesthetic, stage-direction inserts, execCommand clipboard fallback). **Race-proofing (frontend-races review — the ~40-line fix set, no libraries):**
- Client **state machine** `IDLE → SENDING → RETRYING → CAPPED → ENDED`; a turn may only begin from `IDLE`; input disabled synchronously before the `await`, re-enabled in `.finally()`.
- **Per-turn idempotency key** (`turn_id`): the DO dedupes on it (retry returns the already-computed result, never double-counts); retries are sequential (await settlement first), never `Promise.race`.
- **Server-authoritative counter:** every reply carries `{turn, remaining, state}`; the client *displays* the server number and drives the cap banner/lockout only from server `state` — no client-side prediction. The ~15 "checks watch" warning may be client-cosmetic; the 20-stop is server-confirmed.
- **Two-phase sessionStorage writes** (`pending` → `committed`/`unresolved` keyed by `turn_id`); on boot, reconcile `unresolved` turns against the server before rehydrating. Export builds from committed turns only, gated on `IDLE|ENDED`.
- **Session token in `sessionStorage`** (per-tab) so a second tab = a new session, never two tabs interleaving one server session.
- **iPad Safari:** `pageshow` handler resets state on `event.persisted` (bfcache restores a frozen SENDING state → bricked input); probe-write storage detection with in-memory fallback (private mode throws); input font ≥16px (auto-zoom); ≥48px targets.
- **XSS/CSP (security):** model output rendered as text nodes (never `innerHTML`); strict CSP (no inline script; `connect-src` limited to the Worker); XSS probe in the red-team script.
- **Rehearsal probes:** a 6s-delayed Worker mode + "fire two sends" test button (flushes double-submit/retry/cap races); real-iPad private-mode + back-swipe pass.

**Centaur back-half tonight (SpecFlow A1, confirmed by Damien — full first-pass loop):**
- **IN — `/debrief` endpoint, two axes** (per `docs/research/interview-pedagogy.md`):
  - **Axis A — fact/task coverage:** *elicited / revealed-if-asked you never asked / rapport-gated you never earned* (+ Rule 4.2 flags). Topic-level naming only — never the missed fact's content (debrief-oracle rule above).
  - **Axis B — standardized-client relational scoring**, rated by the persona in character (the SC-movement instrument): rapport & opening · listening (T-funnel: broad-before-narrow rewarded) · understanding my goals · explanation & next steps · "would I come back?" **Ethics on a signed −2/+2 scale** — a Rule 4.2 violation can go negative. Post-debrief self-reflection prompt.
  - **Actor ≠ evaluator:** the debrief call scores against tier definitions + transcript independently; never the persona's self-report.
- **IN — `/critique` endpoint:** paste-your-deliverable (memo, letter, pleading) → rubric-based first-pass critique against the matter's `rubric.json` (criterion-by-criterion, point-weighted, Sonsteng re-write-loop framing). Sized for 4-page memos (~4k input tokens; own `max_tokens` budget; counts against the daily cap like chat turns; ≈$0.014 each).
- **Structured scorecards are the primary artifact (agent-native):** `/debrief` and `/critique` return typed JSON (per-tier arrays / per-criterion `{criterion_id, score, weight, evidence, suggestions}`); the prose narrative is a *field*; the UI renders from the JSON. Scorecard schemas are Phase 0 deliverables. A versioned **`app/worker/API-CONTRACTS.md`** documents all endpoints' request/response/typed-error-envelope (`cap_exceeded` vs `rate_limited` vs `validation_error` — the UI renders the in-character version *from* the structured error).
- **OUT (labeled "coming soon"):** faculty submission & review channel, accounts.

### Workstream 4 — Business-of-law data

- **Build order (SpecFlow C6): firm first.** `data/firm/` — simulated two-person student firm (per the course's firm model): identity, rate card, book of business (the 20 matters + closed-matter history), AR aging, realization/collection rates, budget. Matter agents then *reference* the firm canon (rates, client IDs) so money data reconciles.
- **Per-matter money layer (brainstorm Decision 7):** intake form, conflicts check, engagement letter/fee agreement (fee type varies by shape: hourly, contingency (tort), flat (DWI), retainer), time entries → billing statement, trust-ledger entries.
- **Trust-ledger correctness (SpecFlow C7):** ledgers are pedagogically clean; any intentional errors only in instructor notes.
- **Money-math machine-checked:** validator sums time entries ↔ billing statements, balances trust ledgers, reconciles matter ↔ firm book (acceptance D2).
- **Surfacing (SpecFlow A4):** money-layer docs embedded in each matter packet as exhibits **and** a **Firm Dashboard** page rendered from `data/firm/` (dataviz skill applies); raw JSON/CSV downloads linked.

### Workstream 5 — Curriculum platform site

- `tools/build_site.py` (Python 3, **stdlib only** — zero new dependencies) renders: platform home, 3 module pages (Foundational → Substantive+Skills → Transition), **skills browser** (26 + extension set, tasks/subtasks, FOLIO IRIs), **matter library** (shape-first with Meridian/real-state toggle — SpecFlow A5), per-matter packet pages (single page + in-page TOC per 8-part anatomy, print stylesheet for the faculty flow — C5/A2), rubric pages, firm dashboard (per `docs/research/firm-dashboard-viz-spec.md`), chat entry per matter/persona, **and `data/index.json`** — the machine-readable catalog (every matter/persona/rubric/taxonomy JSON URL) that is the agent/LMS entry point and 80% of a future MCP surface (agent-native, ~30 lines).
- Pitch page stays; platform grows beside it. **Self-contained ethos, clarified (performance review):** zero *third-party/CDN* requests; shared **same-origin** assets are required — one `site/platform/assets/theme.css` + `fonts.css` (per `docs/research/design-direction.md` §10) instead of ~30–50KB embedded CSS re-downloaded on every one of ~150 pages. **Page budget:** packet pages target ≤150KB, ceiling 250KB; oversize case files split into linked sub-pages. Print CSS strips chrome, black-on-white, `break-inside:avoid`, and **can never pull instructor notes into student packet output**. Design contract: `docs/research/design-direction.md` (aesthetic, tokens, named primitives, a11y guardrails incl. large-type mode). Skills-browser ↔ matter-library links bidirectional (D8); every nav path ends in content or an explicit "coming soon" (D8/D10).
- **OSS adopter quickstart (SpecFlow A8):** README section — clone → serve site → deploy-your-own-Worker for chat; wording "no platform fees; bring your own Anthropic key." Executed once from a clean clone as acceptance (D9).

### Worker implementation guidance (Cloudflare best-practices, applied)

*Distilled from the Cloudflare `workers-best-practices` + `durable-objects` skills and current CF docs (2026-07-17). This is the build contract for the Phase 4 Worker; treat each numbered item as a decision, not an option.*

**1. Project layout + `wrangler.jsonc` (Worker + one SQLite DO).**
Keep the repo's zero-build, self-contained ethos: write the Worker in **plain JavaScript ES modules** (no TypeScript toolchain, no bundler config — `wrangler deploy` bundles native JS and native JSON imports for you). Layout under `app/worker/`:

```
app/worker/
├── wrangler.jsonc
├── .dev.vars                     # gitignored — local secrets only (never committed)
├── .gitignore                    # .dev.vars, .wrangler/, node_modules/
├── src/
│   ├── index.js                  # router: /session /chat /debrief /critique + CORS + OPTIONS
│   ├── cors.js                   # allowlist + preflight + header attach helper
│   ├── session.js                # mint + verify HMAC-signed session token
│   ├── budget.js                 # BudgetCounter DO (SQLite): preflight/settle/lazy-reset
│   ├── anthropic.js              # Anthropic call helper (non-streaming; usage extraction)
│   └── prompts.js                # buildSystemPrompt(persona, tiers) / debrief / critique
└── personas/
    └── personas.generated.json   # BUILD ARTIFACT — confidential persona+fact fields only,
                                   #   generated from data/matters/*/ by a tools/ script; server-only
```

`wrangler.jsonc` (pin the CLI version at deploy — `npx wrangler@<X> deploy` — rather than adding a `package.json`, honoring the repo's no-new-deps rule; a minimal dev-only `package.json` pinning wrangler is an acceptable alternative if reproducibility is preferred, but flag it under the deps gate first):

```jsonc
{
  "name": "sonsteng-chat",
  "main": "src/index.js",
  "compatibility_date": "2026-07-17",
  "compatibility_flags": ["nodejs_compat"],
  "observability": { "enabled": true, "logs": { "head_sampling_rate": 1 } },
  "durable_objects": {
    "bindings": [{ "name": "BUDGET", "class_name": "BudgetCounter" }]
  },
  "migrations": [
    { "tag": "v1", "new_sqlite_classes": ["BudgetCounter"] }
  ],
  "vars": {
    "ALLOWED_ORIGINS": "https://sonsteng-dev.damienriehl.com,https://sonsteng.damienriehl.com",
    "ANTHROPIC_MODEL": "claude-haiku-4-5",
    "PUBLIC_BUDGET_USD": "7",
    "DEMO_RESERVE_USD": "3",
    "MAX_TURNS": "20",
    "MAX_SESSIONS_PER_DAY": "200"
  }
  // Secrets set out-of-band: ANTHROPIC_API_KEY, SESSION_SIGNING_KEY, DEMO_BYPASS_TOKEN
}
```

**2. DO pattern — atomic spend counter + daily reset; is one global DO a bottleneck/SPOF?**
- **One global instance is correct here, not a smell.** A single-cap ($10/day) budget *requires* a single authority — that is exactly the coordination atom a DO exists for. Route to it with a constant name: `env.BUDGET.getByName("global-v1")`. At ~100 sessions/day (~2k turns/day, single-digit req/s peak), this is orders of magnitude under a DO's throughput ceiling; it is not a bottleneck. It *is* a deliberate SPOF — but the state is persisted to SQLite, so a DO reschedule/eviction loses nothing, and "the global budget lives in one place" is the property you want. If it ever became hot (it won't tonight), shard into N sub-counters and sum — out of scope.
- **Atomicity comes from the DO's single-threaded model, not from locks.** Do the read-modify-write *inside one RPC method with no `await` between the read and the write* (synchronous `sql.exec` calls). No `blockConcurrencyWhile` per request — that is only for one-time schema init in the constructor.
- **Reset: use LAZY reset-on-access, not an alarm (recommended).** On every `preflight`/`settle`, compare the stored UTC day string (`YYYY-MM-DD`) to today; if it changed, zero the counters first. Rationale: one counter, reset only matters the next time someone spends — an alarm adds a moving part (must be re-armed, fires on idle days, can drift) for zero benefit. (Alarm alternative exists via `ctx.storage.setAlarm` + an `alarm()` handler that zeroes and re-arms for next midnight UTC — document it as the fallback, don't ship it.)
- **Reserve-check then settle-actual** (bounds overshoot to one turn, which the plan already sanctions): `preflight` gates *before* calling Anthropic (turn cap + pool not exhausted, increments the turn counter); `settle` records the *actual* cost from the Anthropic `usage` after the reply. Both must be **`await`ed inline** (they are authoritative — never `ctx.waitUntil` the spend write, or you can under-count and over-serve).

```js
// src/budget.js
import { DurableObject } from "cloudflare:workers";

const CENTS_PER_MTOK_IN = 100;   // set from claude-api skill at build time (Haiku 4.5)
const CENTS_PER_MTOK_OUT = 500;  // placeholder — confirm live pricing

export class BudgetCounter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {          // schema init ONLY
      this.ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS budget (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          day TEXT NOT NULL, public_cents INTEGER NOT NULL, demo_cents INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          sid TEXT PRIMARY KEY, day TEXT NOT NULL, turns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS mints (day TEXT PRIMARY KEY, count INTEGER NOT NULL);`);
    });
  }

  _today() { return new Date().toISOString().slice(0, 10); }

  _rollover(today) {                                  // lazy reset-on-access
    const row = this.ctx.storage.sql.exec("SELECT day FROM budget WHERE id=1").toArray()[0];
    if (!row) this.ctx.storage.sql.exec(
      "INSERT INTO budget (id,day,public_cents,demo_cents) VALUES (1,?,0,0)", today);
    else if (row.day !== today) this.ctx.storage.sql.exec(
      "UPDATE budget SET day=?, public_cents=0, demo_cents=0 WHERE id=1", today);
  }

  // called BEFORE the Anthropic request — atomic gate, no await in the middle
  preflight(sid, pool, capsCents) {                   // pool = "public" | "demo"
    const today = this._today();
    this._rollover(today);
    const b = this.ctx.storage.sql.exec("SELECT public_cents,demo_cents FROM budget WHERE id=1").toArray()[0];
    const spent = pool === "demo" ? b.demo_cents : b.public_cents;
    if (spent >= capsCents[pool]) return { ok: false, reason: "budget" };

    const s = this.ctx.storage.sql.exec("SELECT day,turns FROM sessions WHERE sid=?", sid).toArray()[0];
    const turns = s && s.day === today ? s.turns : 0;
    if (turns >= capsCents.maxTurns) return { ok: false, reason: "turns", turns };
    this.ctx.storage.sql.exec(
      "INSERT INTO sessions (sid,day,turns) VALUES (?,?,1) " +
      "ON CONFLICT(sid) DO UPDATE SET turns=CASE WHEN day=? THEN turns+1 ELSE 1 END, day=?",
      sid, today, today, today);
    return { ok: true, turns: turns + 1 };
  }

  // called AFTER the reply, with real token usage — records actual cost
  settle(pool, usage) {
    const today = this._today();
    this._rollover(today);
    const cents = Math.ceil(
      (usage.input_tokens * CENTS_PER_MTOK_IN + usage.output_tokens * CENTS_PER_MTOK_OUT) / 1_000_000);
    const col = pool === "demo" ? "demo_cents" : "public_cents";
    this.ctx.storage.sql.exec(`UPDATE budget SET ${col} = ${col} + ? WHERE id=1`, cents);
  }
}
```

**3. Session-token design — simplest NAT-safe approach: signed stateless token + DO-held turn counter (hybrid).**
- The token is a **stateless HMAC-signed** blob minted at `GET /session` (no DO round-trip to mint): payload `{sid: crypto.randomUUID(), d: "<UTC-day>"}`, signature = `HMAC-SHA256(SESSION_SIGNING_KEY, payload)`. This authenticates the `sid` cheaply and unforgeably.
- **Do NOT put the turn count in the token** — the client holds it and would replay a low count. The *authoritative* turn counter lives in the DO keyed by `sid` (table above). Signed token = identity; DO = enforcement.
- **This is the NAT-safe primary limit:** caps key off `sid`, not IP, so 30 students behind one classroom NAT each get their own session/turn budget. Per-IP is only a *coarse* secondary brake (a hashed-IP daily counter — optional tonight; can live in the same DO). `MAX_SESSIONS_PER_DAY` throttles mint volume via the `mints` table.
- **Demo bypass:** an unlisted `?bypass=<token>` URL param, compared with `crypto.subtle.timingSafeEqual` against the `DEMO_BYPASS_TOKEN` secret; a valid bypass mints a session that draws on the `"demo"` pool and skips the per-IP brake (John & Roger).
- Use `crypto.randomUUID()` / `crypto.getRandomValues()` for `sid` and nonces — never `Math.random()`.

**4. Persona files — BUNDLE them into the Worker (recommended), not KV, not DO storage.**
- ~40 small JSON files, deployed *with* the code, mutated only by redeploy → bundling is the right call. `import personas from "./personas/personas.generated.json"` (wrangler bundles JSON natively). Zero read latency, versioned with the deploy, no eventual-consistency window, no extra binding.
- **Security-critical:** these hold concealed/confidential facts. Bundling into the Worker JS keeps them **server-side only** (never publicly fetchable). Do **NOT** ship them as Workers Static Assets or in the `app/chat/` static bundle — those are public URLs and would leak the tiers. (Server-side injection per SpecFlow B1 is exactly why bundling-into-code, not static-assets, is correct.)
- KV is only justified if personas changed without redeploy — they don't (they're repo content). DO storage is wrong (that's mutable per-entity state).
- **Keep the bundle lean:** generate `personas.generated.json` with *only* the fields the Worker needs for injection (identity, disclosure-tier facts, knowledge_boundary, color_topics). **Never bundle `instructor-notes.md`, answer keys, or the full data spine** into the Worker — that risks the ~3 MB gzipped bundle limit and leaks spoilers server-side. A `tools/` generator builds this artifact from `data/matters/*/personas/` + `facts.md`.

**5. Secrets + local dev.**
- Set with `wrangler secret put`: `ANTHROPIC_API_KEY`, `SESSION_SIGNING_KEY` (random 32 bytes), `DEMO_BYPASS_TOKEN`. **Never** in `wrangler.jsonc` `vars` or source. **Gate on Damien before wiring `ANTHROPIC_API_KEY`** (global credentials rule) — Phase 4 already flags this.
- Local dev: put the same keys in `app/worker/.dev.vars` (gitignored); `wrangler dev` loads them automatically. Confirm `.dev.vars`, `.wrangler/`, and `node_modules/` are in `.gitignore`.
- Non-secret config (origins, model id, budget numbers, caps) stays in `vars`.

**6. CORS preflight gotchas (this bit is where demo-night CORS failures come from).**
- Handle `OPTIONS` explicitly → `204` with `Access-Control-Allow-Origin` set to the **matched** origin (echo it, with `Vary: Origin`) — not `*` — plus `Access-Control-Allow-Methods: POST, OPTIONS`, `Access-Control-Allow-Headers: content-type` (add any custom header you introduce, e.g. `x-session-token`, or the preflight fails), and `Access-Control-Max-Age: 86400`.
- **Attach the same `Access-Control-Allow-Origin` header to the actual POST responses AND to every error/cap/limit response.** The classic failure: a 429/500/cap banner returned without CORS headers → the browser masks the real error as an opaque "CORS error," and the demo shows a blank box instead of the in-character "bad phone connection." One `withCors(response, origin)` helper wrapping *all* returns (success and error) prevents this.
- The preflight fires because requests are `application/json` + custom header. Origin not in the allowlist → return `403` (still with no ACAO, so the browser blocks it — that's the allowlist working). Keep the allowlist to exactly the DEV + PROD origins (B10).

**7. SSE streaming pass-through — trivial mechanically, but ship NON-STREAMING tonight.**
- Mechanically it *is* trivial: `return new Response(anthropicResponse.body, { headers: { ...cors, "content-type": "text/event-stream" } })` streams Anthropic's SSE straight to the browser (confirmed against CF streams docs — a Worker that forwards a subrequest body verbatim is already optimal).
- **But** streaming complicates the one thing this design depends on: **spend accounting**. Token `usage` arrives only in the terminal `message_delta` SSE event, so to `settle` the real cost you'd have to `tee`/parse the stream (a `TransformStream` that sniffs the final event, then `ctx.waitUntil(stub.settle(...))`). That's extra moving parts for demo night.
- **Recommendation: non-streaming for the demo.** `await` the full JSON response, read `usage.input_tokens`/`usage.output_tokens`, `await stub.settle(...)`, then return. Interview turns are short and Haiku is fast, so latency is fine; accounting stays exact and simple. Streaming is a clean fast-follow (the `TransformStream`-sniff pattern above), not a tonight item.

**8. Workers anti-patterns this specific design risks — guard each.**
- **Floating promises on logging/metrics** → structured `console.log(JSON.stringify({...}))` is synchronous and fine; if you fire a metrics/webhook `fetch`, wrap it in `ctx.waitUntil(...)`. But the **spend `settle` write must be `await`ed, never `waitUntil`'d** (authoritative — see item 2).
- **Global request-state leak** → do not stash the session, sid, or per-request data in module-level `let`/`var`. The `import`ed `personas` JSON is immutable and fine at module scope; nothing request-scoped goes there.
- **Destructuring `ctx`** (`const { waitUntil } = ctx`) → throws "Illegal invocation." Always call `ctx.waitUntil(...)`.
- **Oversized bundle** → keep `personas.generated.json` to injection-only fields; never bundle instructor notes / answer keys / the data spine (item 4). Watch the ~3 MB gzip limit.
- **`Math.random()` for tokens/ids** → use Web Crypto (item 3). **Direct `===` on the bypass token / HMAC** → `crypto.subtle.timingSafeEqual` on fixed-size digests.
- **`passThroughOnException`** → never; use explicit `try/catch` returning in-character error JSON **with CORS headers** (item 6). On Anthropic 429/529, retry once then return the in-character "bad phone connection" and **do not burn the turn** (don't call `preflight` again — or roll the turn back).
- **`blockConcurrencyWhile` per request** → only in the DO constructor for schema init; never in the hot path.
- **`await response.text()` on unbounded data** → not a real risk here (inputs bounded: ~4k-token memos, short chat turns), but keep the `/critique` input size-checked and reject oversized memos with a graceful 413 (D11).

## Implementation Phases (fleet orchestration)

**Phase 0 — Foundations (sequential, small; the consistency contract — where the parallel build holds or shatters).**
All schemas; `meridian.json` canon; real-state jurisdiction stubs; persona system-prompt template (shared boilerplate ≥4096-token cacheable prefix); content style guide (voice, collision-resistant naming); validator scaffold. **Plus the vocabulary contracts the reviews demanded (unanimous across architecture/simplicity/data-integrity/agent-native):**
1. **Frozen ID registry + full matter manifest:** the 20 matters enumerated up front — `m01`…`m20` + slug, shape, sides, jurisdiction, client identity/ID, fee type — handed to each fleet agent verbatim (the firm's book *is* these matters; firm agents and matter agents both read this manifest). All skill/task/jurisdiction/firm IDs frozen here.
2. **Deterministic matter-prefixed IDs** (`m06.per.baines`, `m06.te.0007`, `m06.fact.014`) — collisions impossible by construction; a matter's files may contain only its own prefix.
3. **`schema_version` on every entity + `data/spine-manifest.json`; schemas FREEZE when Phase 3 launches** (additive-optional only — 20 in-flight agents can't be re-briefed).
4. **Fixed 8-part section keys** in the exercise schema (intro, objectives, activities, instructions, case_file, history, considerations, substantive_info) — generator and validator key off them.
5. **Closed rapport-trigger enum** in `persona.schema.json`.
6. **Strict rubric schema:** `criteria[].{id, description, weight, skill_id/task_id}` + letter-grade maps — `/critique` is generic code over 20 rubrics.
7. **Countable depth-floor manifest** ("all deep" made machine-checkable): min witness statements, documents, personas (≥1 client, ≥1 with rapport-gated facts), case-file word range, all 8 sections non-trivial — validator ERROR below floor.
8. **Debrief/critique scorecard schemas + `API-CONTRACTS.md` skeleton**; stable `@id`s/JSON-LD context.
Validator built to `docs/research/validator-spec.md` (severity model, per-matter isolation, offline-by-default FOLIO snapshot). *Success: validator runs green on a hand-built sample matter stub.*

**Phase 1 — Taxonomy (parallel with Phase 0 tail).**
FOLIO Services-branch extraction via MCP → `skills.json`, `tasks.json`, `folio-crosswalk.json` with miss policy. **IRIs are resolved at authoring time and snapshotted into the crosswalk; the ship gate validates against the snapshot, never live MCP** (architecture: no network calls in the gate). *Success: all 26 skills decomposed; every task has an IRI or explicit no-equivalent; validator green.*

**Phase 2 — Firm canon (blocks matter fleet).**
`data/firm/` complete. *Success: money-math validator green at firm level.*

**Phase 3 — Matter fleet (the big fan-out).**
20 Opus agents in parallel (worktree isolation not needed — disjoint `data/matters/<slug>/` dirs), each producing one complete matter (facts, case file, 8-part packet, personas, rubric, business layer) against schemas + canons + its manifest entry. **Each agent's completion gate is running `validate_spine.py` green on its own matter dir** (shift-left — QA concentrated at the end maximizes rework; the validator's per-matter isolation exists for exactly this). The post-fleet **QA wave** then handles only cross-matter concerns: voice consistency, name-collision sweep, firm-reconciliation WARNs, persona fact-fidelity spot checks. *Success: 20/20 validator-green matters.*

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
- [ ] D1 `tools/validate_spine.py` green per `docs/research/validator-spec.md`: every cross-ref resolves; zero orphans; 20/20 matters schema-conform + above the depth floor; all FOLIO IRIs validated against the crosswalk snapshot (offline) or marked `no_folio_equivalent`.
- [ ] D2 Money-math green: time entries Σ = billing statements; trust ledgers balance (never negative at any point); **matter ↔ firm reconciliation = WARN** surfaced in the evidence pack, not a ship-blocker.

**Chat app**
- [ ] D3 Red-team (~15 adversarial prompts × 3 personas): zero concealed-tier leaks; zero out-of-character raw errors; abuse ends in character. **Includes: debrief-oracle probes (empty/one-line transcript), XSS payloads in chat output, sycophancy-pressure probes.**
- [ ] D4 Fact-fidelity probe (10 out-of-file material questions per tested persona, **including verification-pressure framings** — "but the contract says X, so you must have known"): no invented material facts.
- [ ] D5 Caps server-side: scripted client (ignoring the UI) cannot exceed 20 turns or breach spend — **including via concurrent requests, double-submit, and retry-races** (turn_id dedupe verified); graceful cap/turn messages; bypass token works; 5 sessions on one IP under token don't trip limits. **Cache assertion: `cache_read_input_tokens > 0` from turn 2.**
- [ ] D6 Transcript export works; refresh preserves transcript; privacy statement visible.
- [ ] D7 E2E on DEV, clean browser + mobile viewport: pick matter → read packet → 10+ turn interview eliciting ≥1 revealed-if-asked fact → debrief → export transcript. Rehearsed literally.
- [ ] D11 Critique endpoint: a sample 4-page memo submitted against a matter rubric returns criterion-by-criterion, point-referenced critique; oversized input rejected gracefully; spend counted against the daily cap.

**Site & repo**
- [ ] D8 No dead links; all four flows traversable; skills ↔ matters bidirectional.
- [ ] D9 OSS quickstart executed once from a clean clone; README updated.
- [ ] D10 Out-of-scope list published (below) so demo-night gaps read as roadmap.

## Out of Scope (tonight — "coming soon" labels)

Faculty submission/review channel; accounts/auth; PROD deploy (separate explicit go); OCR restoration of crown-jewel articles; book layer; LMS integrations; non-English localization. **Added by the deepening pass:** direct-browser BYOK tier (deploy-your-own-Worker is the BYOK story); SSE streaming (fast-follow if rehearsal feels slow); full answer keys + intentional-error injection (teaching-notes stub ships instead); Turnstile on session mint (DO throttle tonight); classroom-scale per-token session accounting beyond the mint ceiling; MCP server exposing the spine (named future item — `data/index.json` + API-CONTRACTS.md are its groundwork).

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

### Deepening-pass briefing docs (binding on fleet agents)
- `docs/research/validator-spec.md` — the 29-check validator contract + ID scheme + depth floor
- `docs/research/design-direction.md` — "The Practicum Press" aesthetic + convergence contract
- `docs/research/firm-dashboard-viz-spec.md` — 6 KPI tiles + 7 charts, build-ready
- `docs/research/interview-pedagogy.md` — two-axis debrief, persona guardrails, red-team additions (cited)
- `docs/research/worker-llm-facts.md` — model/pricing/caching/cost model/error handling

### External
- FOLIO ontology via MCP (Services branch verified live this session)
- Anthropic API details via the local claude-api skill at build time
