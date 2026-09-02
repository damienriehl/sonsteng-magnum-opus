---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-02T22:30:00Z"
title: "Persona UAT program — closeout"
summary: "All ten personas have current verdicts at production build 49e24f4; every bounded defect the program found is merged and deployed; the remaining NOT RUN and BLOCKED rows name their human prerequisite."
keywords: ["persona-uat", "closeout", "journey-runner", "pre-user-prod-deploy", "remedy-a", "packets-a-f"]
cwd: "/home/damienriehl/Coding Projects/sonsteng-magnum-opus"
resume_focus: "Nothing is in flight. The next session activates only work whose human prerequisite (Packets A–F, the Google credential, or a real user) has changed."
repository: "damienriehl/sonsteng-magnum-opus"
branch: "docs/persona-uat-closeout"
worktree_path: "~/worktrees/sonsteng-closeout"
---

# Persona UAT program — closeout

This retires `docs/handoffs/2026-09-02-persona-uat-program-handoff.md` (resumed by this
session, tasks completed, learnings landed in `docs/solutions/uat/` and the record). The
program's plan is `docs/plans/2026-09-02-1108-test-persona-uat-program-plan.md`; the record
Damien reads is `docs/uat/persona-uat-record.md`.

## What each persona can do today

See `docs/uat/persona-uat-record.md` ("Current verdicts" and "Per-persona counts"). The
table below is the human-readable summary the record's generator does not produce: which
rows are not PASS, and what unblocks them.

Final revalidation at `49e24f4` (production Worker `6932fea4…`, Pages `37a2f2f0`), run
2026-09-02 21:34–22:10Z from a clean checkout: browser journeys 34/34 non-canary PASS on
local, DEV, and production; the ten deliberate canaries FAIL on every environment (by
design, proving the runner can fail); local bindings 38 PASS; bot gate PASS on DEV and
production; accessibility audit 0 FAIL across 12 page cases on both environments.

| Persona | Stories | Not PASS today | Human prerequisite |
|---|---|---|---|
| A1 Prospective reader (pitch) | 8 | none | — |
| A2 Student | 11 | US-2-11 live provider on DEV: BLOCKED | `SONSTENG_UAT_GOOGLE_CREDENTIALS_FILE` naming a protected Google credential |
| A3 Instructor | 5 | none (signer-review live leg is by design harness-only, R8) | — |
| A4 John (editor) | 8 | harness verdicts only; every authenticated live leg NOT RUN | Packet A1 (authenticated editor session) |
| A5 Roger (editor) | 3 | harness verdicts only; live attribution leg NOT RUN | Packet A1 |
| A6 Damien (Publisher) | 4 | harness and Access-audit verdicts only; live release NOT RUN | Packet A2 (Publisher release lane) |
| A7 Adopter | 5 | none; account boundary recorded (no Cloudflare or provider account touched) | — |
| A8 School reader | 4 | none | — |
| A9 Accessibility | 5 | live assistive-technology leg NOT RUN by design (KTD10) | a human AT session |
| A10 Hostile | 6 | US-10-03 live red-team on DEV: BLOCKED | the same Google credential |

Rows marked NOT RUN for the local environment with "binding restricted to dev" or
"{{WORKER_URL}} is unavailable for local" are the same stories proven on DEV or
production; the record keeps them so the local column is honest.

## What was done (this session, 2026-09-02 18:30Z → closeout)

- **Runner and journeys (U2d–U2g, PR #36 → main 460a0f3).** Resting-state visibility,
  CDP download capture under the snap Chromium, exact-name control ranking (the skip-link
  `<main tabindex="-1">` had been swallowing name-based clicks), DOM live-region text,
  document-complete navigation waits, polled attribute and control lookups, a fixed
  bot-gate probe URL, binding provenance, and a record renderer that keeps browser and
  binding rows side by side. Each fix carries a unit or contract test.
- **Product defects the program surfaced, all merged with green preflights and deployed:**
  - PR #35 — README documents the three Worker generator commands; three test modules
    import standalone; the assessment timeout test is Node 22 safe.
  - PR #37 — the pitch's comments dialog moves focus in on open and returns it on close.
  - PR #38 — the pitch's comment counters are `aria-live="polite"` (the accessibility half
    of the last retired stash; the wording change was dropped, stash deleted).
  - PR #39 — pitch text contrast to WCAG AA with **Remedy A** (Damien's choice, inline,
    2026-09-02): muted `#8a7f6d`→`#675e51`, gold `#a9822f`→`#785816`, cream hero-button
    text, light-gold claret-on-dark; skip link and `main` landmark; decorative numerals
    `aria-hidden`; the pitch joins the default accessibility audit, which now emulates
    reduced motion and composites translucent backgrounds correctly. The OPEN plan
    `docs/plans/2026-09-02-1226-fix-pitch-accessibility-conformance-plan.md` is closed.
  - PR #40 — the bot-gate probe runs sequentially, ending an order-dependent flake.
- **Production deploys** under the pre-user runbook (`docs/uat/pre-user-prod-deploys.md`):
  `7606463` (Damien ran the runbook script after the agent's upload was blocked by the
  session's permission classifier) and `49e24f4` (agent, after Damien granted the Wrangler
  permission inline). DEV redeployed from `origin/main` at each step. Rollback pair before
  the last deploy: Worker `5cc72a1c-0ad1-45e7-af86-b1c763885c86` / Pages `32bb3b9e`.
- **Evidence beyond the record:** bot gate 403 on DEV and production (probe 3/3 each);
  Access-policy audit PASS (one IdP, one allow policy, three email selectors, no bypass
  rules, no addresses recorded); editor harness 89/89; Publisher client PASS; Worker tests
  580/580 under Node 22 and 24; offline red-team 0/8 exposed; accessibility audit 0 FAIL
  on every audited page on both environments at `49e24f4`.
- **Repository hygiene:** merged worktrees and branches removed after each merge; the
  only stash dropped by decision; `docs/solutions/uat/2026-09-02-browser-journeys-measure-the-wrong-thing.md`
  captures the eight defects and the merge-gate lesson.

## Decisions Damien made (inline, 2026-09-02)

- Pitch text contrast: **Remedy A**.
- Last stash: **land the aria-live counters only**; drop the proof-summary rewording.
- Production deploys: **grant the session the Wrangler permission** (rules live in the
  untracked `.claude/settings.local.json` of the primary checkout).
- Standing: questions for Damien are asked inline through the Q&A UI, every time.

## Process error to own

PR #37 was merged while its preflight showed 4 failures. The merge command's guard matched
the failure line as readily as the success line. The failures were the fresh-worktree
generator trap (bundle parity and three dependent gates), not the change; a clean preflight
on the merged main passed 21/21 before anything was deployed. Every later merge was gated
on the literal `, 0 failed`. Recorded in the solutions entry.

## Still human-gated (unchanged)

- Packets A–F from the August 23 decision sheet remain human-gated exactly as before.
- The two Google-gated live legs (`student-live-provider-dev`, `hostile-live-redteam-dev`)
  stay BLOCKED until `SONSTENG_UAT_GOOGLE_CREDENTIALS_FILE` names a protected credential.
- Every authenticated editor and Publisher live leg stays NOT RUN, naming Packet A1 or A2.
- The pre-user runbook expires at the first real user.

## Environment notes (FYI, not defects)

- DEV's nginx serves no clean-URL rewrite: `/cost-per-credit` is 404 on DEV and 200 on
  production Pages; audit DEV with the `.html` form.
- Wrangler warns that `EDIT_ACCESS_*` and `PUBLIC_*` vars are top-level rather than under
  `env.production.vars`; the editor is served by the default Worker deploy, so production
  does not need them. Pre-existing.
- The DEV Worker's release-provenance route answers without a SHA (no `RELEASE_SHA` on the
  default deploy), so DEV binding rows carry a null build key; the renderer handles it.

## Where to look

- Record: `docs/uat/persona-uat-record.md`. Schema and timing contract: `docs/uat/journey-schema.md`.
- Runner: `tools/verify_persona_journeys.js`; journeys: `tools/persona_journeys.json`;
  probe: `tools/verify_bot_gate.js`; adopter clone: `tools/uat_adopter_journey.sh`.
- Deploys: `docs/uat/pre-user-prod-deploys.md`. Branch cleanup evidence:
  `docs/evidence/2026-09-02-branch-supersession-evidence.md`.
- Run files and screenshots are gitignored build artifacts; the record cites digests. Per
  KTD2 the final run's run files, retained screenshots, and logs are copied to
  `~/.local/state/legal-practicum-uat/49e24f4f301ea509017d2c4dfa3105adfb7e0b2b/`
  (machine-local, 90-day retention note inside).
