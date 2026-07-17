# Brainstorm — Curriculum Build-Out (The Big Push)

**Date:** 2026-07-17
**Participants:** Damien Riehl (+ Claude Fable orchestrating; Opus subagents executing)
**Status:** Brainstorm → ready for `/ce:plan`
**Predecessor:** [2026-07-15 Magnum Opus brainstorm](2026-07-15-magnum-opus-brainstorm.md) (layered book + curriculum + platform; MIT; centaur pedagogy)

---

## What We're Building

One large push that turns the repo's pedagogical spec (`docs/master-outline.md`) into real, shippable substance — five workstreams hanging off **one machine-readable data spine**:

1. **Skills & task taxonomy** — Sonsteng's 17 legal-practice + 9 practice-management skills as the canonical spine, **extended** with a primary-task → subtask decomposition per skill, and **mapped to FOLIO** (full Services branch + the most common tasks most lawyers perform). Machine-readable (JSON) with FOLIO IRIs.
2. **Synthetic matter corpus — 20 matters, all deep.** Ten matter shapes (Sonsteng's six: arbitration, attorney-discipline, tort jury trial, real-estate negotiation, DWI, non-compete; plus his extensible bank: UCC/commercial, juvenile, dissolution, wills) × **two jurisdiction tiers**: a fresh open-source **fictional jurisdiction** and **real jurisdictions** (MN, NY, CA, FL, IL, TX) so students do live legal research against actual state law. Every matter: full case file, procedural/factual history, witness statements, documents/exhibits, following the 8-part exercise anatomy. All facts, parties, and documents original — MIT-clean, no Trialbook/NITA material.
3. **Client-persona engine + web chat app.** Per-matter structured persona files (facts, personality, emotional state, **disclosure rules** — what the client only reveals if asked the right question) + a chat app on the sonsteng platform. **Demo mode:** hosted Haiku behind a Cloudflare Worker with hard spend caps. **Production mode:** customer brings their own API key.
4. **Business-of-law synthetic data — per-matter + firm-level.** Every matter ships its money layer (intake, conflicts check, engagement letter/fee agreement, time entries → billing statement, trust-ledger entries) AND a firm-level dataset — a simulated student law firm with a book of business, AR aging, realization/collection rates, budgets — for the 9 practice-management skills.
5. **Web-first curriculum site.** Module pages (Foundational → Substantive+Skills → Transition to Practice), exercise packets, rubrics, matter files, and the chat app rendered from the data spine into the existing deploy (CF Pages PROD / Hetzner DEV). The pitch page stays; the platform grows beside it.

**Tonight's bar:** demo-ready for John & Roger — taxonomy browsable, matters readable, chat app live on DEV with several personas working end-to-end, curriculum site navigable.

---

## Why This Approach

- **Data-spine monorepo (Approach A, chosen).** At 20-deep-matters scale built by a parallel Opus fleet, a shared schema is what keeps quality consistent: one spine, everything cross-linked (skill → module → exercise → matter → rubric → persona), machine-readable forever. Site pages and packets are *rendered from* the data; the app *reads* the data. Rejected: materials-first (drift at scale, no data layer) and app-centric (heaviest engineering before any curriculum exists).
- **Fresh matters, same shapes.** The six named Sonsteng matters are Trialbook/NITA-adjacent; we mirror their *shapes* 1:1 so the pedagogy maps, but every fact is ours to MIT-license.
- **Two jurisdiction tiers.** Fictional tier isolates skills practice from research risk; real-state tier (6 states) adds legal-research realism — students find the actual statutes and regulations.
- **FOLIO alignment.** The taxonomy doubles as a standards contribution: Sonsteng skills ⟷ FOLIO Services-branch tasks, usable by any legal-ed or legal-tech system.
- **Demo-Haiku / BYOK-production split.** Keeps Damien's spend capped for the demo while making the open-source app cost-free to operate for adopters.

---

## Key Decisions (locked today)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Skills spine | Sonsteng 17+9, extended with task/subtask decomposition + FOLIO Services branch + most-common-lawyer-tasks |
| 2 | Case universe | Fresh original matters mirroring Sonsteng's shapes; MIT-clean |
| 3 | Jurisdiction tiers | Fictional jurisdiction + real states (MN, NY, CA, FL, IL, TX) |
| 4 | Corpus scale | Go wide: 10 shapes × 2 tiers = 20 matters, **all deep** |
| 5 | Simulator | Persona engine (structured files w/ disclosure rules) + web chat app |
| 6 | Chat LLM economics | Hosted Haiku demo w/ hard caps; production = bring-your-own-key |
| 7 | Business-of-law data | Per-matter money layer + firm-level simulated law firm dataset |
| 8 | Delivery form | Web-first platform site rendered from the data spine |
| 9 | Architecture | Data-spine monorepo (schema-first; site/packets/app all derive from it) |
| 10 | Orchestration | Fable session orchestrates; Opus subagents execute |
| 11 | Success bar tonight | Demo-ready for John & Roger |

---

## Resolved Questions

1. **Fictional jurisdiction name → State of Meridian.** Capital city, court structure ("Meridian District Court"), and statute-numbering scheme ("Mer. Stat. § ___") invented once and reused across all fictional-tier matters. ("Midstate" stays with the Trialbook lineage.)
2. **Demo spend guards → $10/day hard cap, 20-turn session limit, per-IP rate limiting** on the demo Worker (Haiku; ~100+ full interview sessions/day).

---

## Next

- Resolve open questions → `/ce:plan` the build (plan will decompose into fleet-sized workstreams with schemas first).
