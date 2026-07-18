STATE: working — weekend fast-follow wave queued; main = platform v0 + editor, DEV-deployed; PROD held.

# Sonsteng Magnum Opus — Weekend Resume (2026-07-18)

**Fresh session:** read this file + `docs/decisions/2026-07-18-qa-answers.md`, then execute
the Weekend Work Plan below via parallel Opus subagents (keep the orchestrator context clean —
ALL file-reading/editing/verification in subagents). Batch any decisions for Damien ~10pm
Central 2026-07-18 as a cockpit QA artifact (house rule).

This repo self-documents (cockpit convention: RESUME.md + the STATE line at top). You have
the project memory (`project_sonsteng_magnum_opus.md`) plus this doc — that is enough to
continue losslessly with no conversation history.

---

## Current state

**What is live (DEV only — PROD held by q3):**
- **DEV platform site:** https://sonsteng-dev.damienriehl.com/platform/ → 200. 30-page
  pitch + platform (Hetzner box `hetzner-dev`, `/opt/sonsteng`, docker compose project
  `sonsteng`).
- **Worker:** https://sonsteng-chat.damienriehl.workers.dev — chat/critique/debrief +
  the `/edit` proxy-injector editor. `GET /v1/session` → 200 (health/config; the interview
  flow POSTs to other routes). Config in `app/worker/wrangler.jsonc`.
- **PROD:** https://sonsteng.damienriehl.com serves only the ORIGINAL pitch. Do NOT deploy
  PROD this weekend (build wiring only — see WP1).

**Branch state:** ALL merged to `main` (fast-forward). `main` tip = editor tip
(`c10abd7`). Feature branches `feat/curriculum-buildout` and `feat/editor-experience`
are fully contained in main. main is pushed to origin.

**Test/gate counts (all green at merge):**
- Worker tests: **119**
- Apply-engine tests: **14**
- Editor-client assertions: **22**
- `validate_spine.py`: **PASS** (0 ERROR), 20/20 per-matter self-gates
- `build_site.py --check` + leak-sweep: green

**Where everything is documented:**
- Plans: `docs/plans/2026-07-17-001-feat-curriculum-buildout-plan.md`,
  `docs/plans/2026-07-18-001-feat-editor-experience-plan.md`
- Evidence packs: `docs/evidence/EP-2026-07-17-buildout.md`,
  `docs/evidence/EP-2026-07-18-editor.md` (+ screenshots in `docs/evidence/EP-2026-07-17/`)
- Demo runbook: `docs/demo-runbook-2026-07-18.md`
- Editor guide for John: `docs/editor-guide-for-john.md`
- Research/briefing docs: `docs/research/*` — key ones:
  `worker-llm-facts.md` (LLM/streaming/caching contract),
  `editor-apply-spec.md` (store DDL, state machine, apply transaction, value-sync scope,
  parity gate), `design-direction.md` (§9 = a11y),
  `validator-spec.md`, `firm-dashboard-viz-spec.md`, `interview-pedagogy.md`,
  `skills-survey.md`
- Orchestration learnings: `docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md`
- Decisions: `docs/decisions/2026-07-18-qa-answers.md` (this weekend's binding answers),
  `docs/decisions/2026-07-18-midstate-deferred.md`
- Worker API contract: `app/worker/API-CONTRACTS.md`

**Secrets — BY PATH ONLY (never print, never commit; all confirmed present):**
- `~/.secrets/sonsteng-editor-tokens` — John + admin edit tokens (opaque; set on the
  Worker via `wrangler secret put EDIT_TOKEN_JOHN` / `EDIT_TOKEN_ADMIN`)
- `~/.secrets/sonsteng-demo-bypass` — demo bypass token
- `~/.config/cloudflare/creds.env` — Cloudflare API creds for wrangler/CF API

**Cockpit decision record:** form at
`dashboard.damienriehl.com/sonsteng-decisions-2026-07-18.html`; brief
`~/Coding Projects/briefs/qa/sonsteng-2026-07-18-decisions.json` (marked ANSWERED
2026-07-18 → pointer to `docs/decisions/2026-07-18-qa-answers.md`).

---

## WEEKEND WORK PLAN

Damien's q7 answer: **build ALL fast-follows this weekend** ("do everything you can…lot of
usage to burn up"). q4: **BYOK-forever, no live provider key** — so anything needing a live
key is keyless-substituted (WP8) or deferred. q3: **never deploy PROD** — build wiring, leave
the flip to Damien.

**Every package must:** name its owned paths (one-writer rule — no two agents write the same
file), pass its gates before hand-back (worker tests green, `validate_spine.py` PASS,
`build_site.py --check` green, editor parity PASS where touched), and **must not deploy PROD**.
DEV redeploy is allowed via `deploy/deploy-dev.sh main`.

**Parallelism:** WP1–WP7 are independent → launch as parallel Opus subagents. WP8 runs after
WP7. WP9 runs after WP1–WP8 land. WP10 fills remaining budget.

### WP1 — PROD injector wiring (BUILD, do NOT flip)
- **Goal:** make PROD-enable a one-command step without performing it.
- Parameterize `EDIT_UPSTREAM` per environment. Today `wrangler.jsonc` hard-codes the DEV
  origin `https://sonsteng-dev.damienriehl.com/platform/`. Add an env-scoped config (wrangler
  `env.production` / `env.dev` vars block) so the PROD value points at the CF Pages origin
  (PROD static: `sonsteng.damienriehl.com` served by CF Pages). Keep `EDIT_ORIGIN` correct
  per env.
- Document the exact one-command PROD enable (the `wrangler deploy --env production` +
  `deploy/deploy-prod.sh` sequence) in a short section of `docs/demo-runbook-2026-07-18.md`
  or a new `docs/prod-enable.md`. **Do not run it.**
- **Owned paths:** `app/worker/wrangler.jsonc`, `docs/prod-enable.md` (or the runbook's PROD
  section — pick ONE writer). Binding ref: editor plan §"PROD injector wiring", `wrangler.jsonc`
  Editor block.
- **Gate:** worker tests green; no PROD deploy.

### WP2 — Roger as second editor
- Add a second token slot `EDIT_TOKEN_ROGER` + a scope record in `EDIT_TOKEN_SCOPES`
  (today: `{"john":{"edit":1,"instructor":1},"admin":{"admin":1}}` → add
  `"roger":{"edit":1,"instructor":1}`). Attribution label **"RSH"** on Roger's suggestions.
- Add a worker test proving Roger's token mints an edit scope and attributions carry "RSH".
  Store the actual token value only in `~/.secrets/sonsteng-editor-tokens` (path only) +
  `wrangler secret put`.
- **Owned paths:** `app/worker/wrangler.jsonc` (COORDINATE with WP1 — same file; sequence
  WP1 then WP2, or have one agent own wrangler.jsonc for both), attribution code in
  `app/worker/src/`, a new test in `app/worker/test/`. Binding ref: editor plan
  §"Roger as second editor (token model ready)".
- **Gate:** worker tests green.

### WP3 — Digest push (ntfy)
- Fire an **ntfy** notification (topic `damien-homebox-736591e7`) when pending suggestions
  > 0, **batched** and respecting **cumulative** semantics (days accumulate; one sweep
  reviews all). The **review page stays canonical**; push is strictly additive (per
  `docs/plans/2026-07-18-001…` decision 6). Trigger from the apply engine / a small cron —
  automated push only fires when pending accumulates (do not spam per-suggestion).
- **Owned paths:** apply-engine notify hook in `tools/` (apply engine) + a small cron script;
  `docs/` note. Binding ref: editor plan decision 6, `editor-apply-spec.md`.
- **Gate:** apply-engine tests green; validator PASS.

### WP4 — SSE streaming for chat (behind a flag)
- Implement TransformStream streaming per `docs/research/worker-llm-facts.md` §4
  (sniff terminal `message_delta` for `usage`) + app/worker streaming guidance. **Behind a
  config flag; non-streaming stays the default** until the D7 rehearsal says otherwise.
  Client shows a typing indicator today — keep that as the non-streaming path.
- **Owned paths:** `app/worker/src/` chat handler, `app/chat/chat.js`, a `STREAMING` flag in
  `wrangler.jsonc` (COORDINATE with WP1/WP2 on that file). Binding ref: worker-llm-facts §4.
- **Gate:** worker tests green; flag defaults OFF.

### WP5 — Value-sync formatting-preserving JSON serializer
- Apply-engine fast-follow noted in the editor plan (§ "value-sync formatting-preserving
  serializer fast-follow"). v1 value-sync is exact-literal, structurally-scoped; upgrade the
  JSON write path to preserve source formatting (key order, spacing) on scalar edits so
  value-sync diffs stay minimal.
- **Owned paths:** value-sync/serializer module in `tools/` + its test in `tools/tests/`.
  Binding ref: apply-engine report, `editor-apply-spec.md` §value-sync.
- **Gate:** apply-engine tests green; validator PASS.

### WP6 — Turnstile on `/v1/session` mint
- Bot-gate the free session-mint endpoint with Cloudflare Turnstile per the security review.
  **Use the `cloudflare:turnstile-spin` skill** (scans codebase, creates the widget via CF
  API, deploys the siteverify path, writes frontend snippets). Creds at
  `~/.config/cloudflare/creds.env`.
- **Owned paths:** session-mint handler in `app/worker/src/`, the chat frontend session call
  in `app/chat/`, Turnstile config. Binding ref: security review section of the editor plan.
- **Gate:** worker tests green; session mint still works for legit clients.

### WP7 — Formatted-block span-splice (editor apply v1.1)
- The labeled fast-follow in the editor plan: today `has_inline_formatting` blocks route to
  `needs_human`. Implement **auto-apply of formatted blocks when every formatted span's text
  is unchanged in-order** (splice the changed plain text between untouched formatted spans).
  Keep the `needs_human` fallback whenever a formatted span's own text changed or order
  shifted.
- **Owned paths:** the apply-engine block-apply module in `tools/` + tests. Binding ref:
  editor plan §"span-splice = labeled fast-follow", `editor-apply-spec.md`.
- **Gate:** apply-engine tests green; validator PASS. **Do after WP5 if both touch the same
  apply module — coordinate one-writer.**

### WP8 — Offline red-team approximation (runs AFTER WP7)
- BYOK-forever ⇒ no live key ⇒ `redteam.mjs` can't run live. Build an **Opus agent battery**
  that adversarially probes the persona SYSTEM PROMPTS + debrief redaction **offline**: an
  Opus agent plays the jailbreaking student against the *rendered* persona prompt using its
  own reasoning, and verifies the **server-side redaction paths** with synthetic model
  outputs (feed crafted model responses through the redaction code, assert leaks are
  stripped). Label it clearly as an **honest partial substitute** for `redteam.mjs` (no live
  model calls). `redteam.mjs` stays untouched and ready for any future key.
- **Owned paths:** a new offline harness under `tools/` or `app/worker/test/` (new files
  only — do NOT modify `redteam.mjs`); an evidence note. Binding ref: worker-llm-facts §2
  (cache/redaction asserts), the debrief-oracle server-side redaction code.
- **Gate:** the redaction-path assertions pass against synthetic outputs.

### WP9 — Walkthrough prep (~Wed, runs AFTER WP1–WP8)
- Rehearse `docs/demo-runbook-2026-07-18.md` end-to-end on DEV via the **keyless paths**
  (sample consultation replay, packets, dashboard, editor). Fix anything rough. Refresh
  screenshots in the evidence pack (`docs/evidence/EP-2026-07-17/` + editor EP).
- **Owned paths:** `docs/demo-runbook-2026-07-18.md`, evidence screenshots. Visual QA note:
  RC-spawned sessions have no DISPLAY → use puppeteer headful on Xwayland :0 per
  `reference_visual_qa_no_display.md` (per-section viewport shots, not tall full-page).
- **Gate:** every runbook beat plays keyless on DEV.

### WP10 — Quality passes (leftover budget)
- `/code-review`-style adversarial review of the EDITOR code paths not yet reviewed
  post-integration-fixes.
- a11y sweep of `/edit` against `docs/research/design-direction.md` §9.
- Cross-matter voice/consistency polish on the **5 thinnest matters** (pick by the depth
  stats in the buildout evidence pack).
- **Owned paths:** whatever each sub-pass touches — one writer per file; fixes only, no new
  scope. **Gate:** all existing gates stay green.

---

## Standing rules recap
- **Orchestrator-clean / Opus subagents:** orchestrator plans+judges; Opus subagents do all
  file read/edit/verify (`feedback_model_roles.md`).
- **Cockpit + artifact for decisions, proactively:** batch decisions to Damien as an
  interactive HTML cockpit QA artifact — skim→decide→Copy-answers paste-back
  (`feedback_render_for_review.md`, `feedback_qa_ui.md`).
- **One-writer-per-path** cockpit concurrency (`feedback_status_state_marker.md`,
  `project_orchestration_lessons.md`): never let two agents write the same file.
- **Hetzner compose:** always `-p sonsteng`, **NEVER `--remove-orphans`**
  (`project_hetzner_compose_project_names.md`).
- **Deploy:** `deploy/deploy-dev.sh [branch]` (defaults main); DEV free, **PROD always asks**
  (`project_sonsteng_magnum_opus.md`, global CLAUDE.md).
- **Feature branch per operation** (`feedback_feature_branch_per_operation.md`); MIT license
  default (`feedback_mit_license.md`); log OSS in THIRD-PARTY.md.

## ~10pm Central decision batch (deliver as a fresh cockpit QA artifact)
Likely to need Damien:
1. **Confirm the walkthrough date** — q6 said "maybe Wednesday" (2026-07-23) but not firm.
2. **John-link send** — q5: Damien test-drives the editor himself first, THEN sends John his
   magic link. Prompt him whether he has test-driven, and offer to build+send the URL.
3. **Any judgment calls the weekend work surfaces** — e.g., Turnstile widget provisioning
   choices, streaming default flip, PROD-enable timing, any fast-follow that hits an
   ambiguous product call.
Deliver as a new cockpit QA artifact (recommended picks flagged, notes fields, Copy-answers
paste-back with the execCommand clipboard fallback).
