# Decision Record — Sonsteng Magnum Opus Outstanding Decisions (2026-07-18)

**Source:** cockpit QA form `sonsteng-2026-07-18-decisions`
(`dashboard.damienriehl.com/sonsteng-decisions-2026-07-18.html`;
brief `~/Coding Projects/briefs/qa/sonsteng-2026-07-18-decisions.json`).
**Answered by:** Damien Riehl, 2026-07-18. **Recorded by:** orchestrator session (this commit).

These are the binding answers. They govern the weekend work plan in `../../RESUME.md`.

---

## q1 — Merge `feat/curriculum-buildout` → main

**YES — merge to main.** Done in this operation. The build-out is the whole platform
(data spine, 10 JSON Schemas, 20 deep matters, the Worker, chat/critique UI, 30-page
platform site). All gates were green (full-spine validator PASS, 20/20 per-matter
self-gates, worker tests, live smoke, link/leak sweep). Fast-forwarded onto main.

## q2 — Merge `feat/editor-experience` → main after q1

**YES — merge after q1.** Done in this operation. The editor layers John's
Worker-injected `/edit` proxy editor on top of the platform (suggestions only; nothing
goes live without Damien's review + validator parity gate). Tests green: worker 119,
apply-engine 14, editor-client 22; validator + leak-sweep green. Fast-forwarded onto
main after buildout, keeping main linear. **main tip = editor tip.**

## q3 — PROD deploy now or hold

**HOLD until after the John & Roger walkthrough.** PROD (`sonsteng.damienriehl.com`)
continues to serve only the original pitch. Everything demoed runs on DEV. The weekend
work may *build* the PROD injector wiring, but the **PROD deploy itself stays held** —
and PROD deploys always come back to Damien regardless.

## q4 — Model API key

**BYOK-only forever — no hosted pool.** Every user pastes their own key; there is no
provider key managed by this project. Verbatim-in-substance note from Damien:

> "the administrator can choose to have a 'house API key' later. And for the purposes of
> building a system, you can always use my Claude Max allotment."

Interpretation for builders: **agent-driven (Claude Max) testing is authorized** — an
Opus agent may reason/act as the model for building and offline testing — but **no live
provider API key will be provided**. Anything that requires a live key (the live
red-team `redteam.mjs`, live client interview) stays keyless-substituted or deferred.
`redteam.mjs` stays ready for any future key. A "house API key" remains an *optional
future admin choice*, not a commitment.

## q5 — John's magic edit link

**Damien test-drives the editor himself first, then sends.** ~10 minutes in the editor
guided by the one-page John guide, so he has seen exactly what John will see — then hand
John the link. John's tokens are staged at `~/.secrets/sonsteng-editor-tokens` (never
printed, never committed). Building + sending the URL is a 2-minute step on Damien's go.

## q6 — Walkthrough date

**Likely next week — "Maybe Wednesday?" (2026-07-23 candidate).** Not firm. The weekend
work should prep the demo runbook to be walkthrough-ready for ~Wed; confirm the exact
date/window with Damien in the ~10pm decision batch.

## q7 — Fast-follows

**BUILD ALL fast-follows this weekend.** Verbatim-in-substance:

> "Please do everything you can. This weekend, I've got a lot of usage to burn up!"

This overrides the form's pre-selected "None for now" recommendation. The full,
delegable weekend work plan lives in `../../RESUME.md` → **WEEKEND WORK PLAN**. The one
hard constraint carried through from q3: **do not deploy PROD** (build the wiring, leave
the flip to Damien).
