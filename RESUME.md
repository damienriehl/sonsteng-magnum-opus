STATE: review-needed — weekend wave WP1-WP10 COMPLETE on feat/weekend-fast-follows (pushed); all gates green; DEV redeployed; PROD held; decision batch awaiting Damien at dashboard.damienriehl.com/sonsteng-weekend-2026-07-18.html

# Sonsteng Magnum Opus — Weekend Resume (2026-07-18)

**Fresh session:** read this file + `docs/decisions/2026-07-18-qa-answers.md`. The weekend
fast-follow wave (WP1-WP10) is DONE on `feat/weekend-fast-follows` — see "Weekend wave
results (2026-07-18)" below. The only thing left is Damien's decision batch (artifact
`dashboard.damienriehl.com/sonsteng-weekend-2026-07-18.html`, brief
`briefs/qa/sonsteng-2026-07-18-weekend.json`): answers → merge to main → send John his
editor link → Wed walkthrough.

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
  the `/edit` proxy-injector editor. Since WP6, `GET /v1/session` is Turnstile-gated:
  untokened → **403 `turnstile_failed`**; `?bypass=<demo-token>` → 200 (session_token).
  `/edit` unauthorized → uniform 404. Config in `app/worker/wrangler.jsonc`.
- **PROD:** https://sonsteng.damienriehl.com serves only the ORIGINAL pitch. Do NOT deploy
  PROD this weekend (build wiring only — see WP1).

**Branch state:** the weekend wave lives on `feat/weekend-fast-follows` (pushed to origin),
built on the earlier `main` (platform v0 + editor). It is **NOT merged to `main`** — held
pending Damien's decision-3 answer (see the weekend artifact). The pre-weekend `main` tip is
still editor tip (`c10abd7`); the WP10 quality-lane branches (`feat/wp10-editor`,
`feat/wp10-matters`) plus the WP1-WP8 lane branches (`feat/wp-worker`, `feat/wp-apply`,
`feat/wp-digest`) are all folded into `feat/weekend-fast-follows` and kept for history.

**Test/gate counts (all green on `feat/weekend-fast-follows`):**
- Worker tests: **175** (`cd app/worker && node --test test/*.test.js`)
- Apply-engine tests: **66** (`pytest tools/tests/test_apply_suggestions.py test_json_surgical.py test_span_splice.py`)
- Digest-push tests: **22** (`pytest tools/tests/test_digest_push.py`)
- Editor-client assertions: **25** (`DISPLAY=:0 node app/editor/verify-editor.js` — 25/25)
- `validate_spine.py`: **PASS** (0 ERROR), 20/20 per-matter self-gates
- `build_site.py --check` + leak-sweep: green; two-bundle parity (`check_build_parity.py`): PASS
  (spine_build_id `1ddab816d04a6d59`)

**Where everything is documented:**
- Plans: `docs/plans/2026-07-17-001-feat-curriculum-buildout-plan.md`,
  `docs/plans/2026-07-18-001-feat-editor-experience-plan.md`
- Evidence packs: `docs/evidence/EP-2026-07-17-buildout.md`,
  `docs/evidence/EP-2026-07-18-editor.md` (+ screenshots in `docs/evidence/EP-2026-07-17/`);
  **weekend-wave additions:** `docs/evidence/EP-2026-07-19-offline-redteam.md` (WP8 offline
  red-team), `docs/evidence/EP-2026-07-18-walkthrough-rehearsal.md` +
  `docs/evidence/EP-2026-07-18-walkthrough/` (WP9 rehearsal screenshots)
- PROD-enable one-command runbook (WP1): `docs/prod-enable.md` (documented, NOT run)
- Digest-push (ntfy) design + install (WP3): `docs/digest-push.md`
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

**Cockpit decision records:**
- Pre-weekend (ANSWERED): form `dashboard.damienriehl.com/sonsteng-decisions-2026-07-18.html`;
  brief `~/Coding Projects/briefs/qa/sonsteng-2026-07-18-decisions.json` → answers in
  `docs/decisions/2026-07-18-qa-answers.md`.
- **Weekend wave (OPEN — awaiting Damien):** artifact
  `dashboard.damienriehl.com/sonsteng-weekend-2026-07-18.html`; brief
  `~/Coding Projects/briefs/qa/sonsteng-2026-07-18-weekend.json`. Its decision-3 (merge
  `feat/weekend-fast-follows` → `main`) gates the merge; other asks: walkthrough date,
  John-link send.

---

## Weekend wave results (2026-07-18) — WP1–WP10 COMPLETE

All ten fast-follows shipped on `feat/weekend-fast-follows` (built off the pre-weekend
`main`). DEV redeployed (worker + Hetzner site); PROD held. Merge to `main` awaits Damien's
decision-3 in the weekend artifact.

**Per-WP (all done):**
- **WP1 — PROD injector wiring:** `wrangler.jsonc` env-scoped `EDIT_UPSTREAM`/`EDIT_ORIGIN`
  (`env.dev`/`env.production`); one-command PROD-enable documented in `docs/prod-enable.md`.
  Built, NOT flipped.
- **WP2 — Roger as second editor:** `EDIT_TOKEN_ROGER` slot + `EDIT_TOKEN_SCOPES` `"roger"`
  entry, attribution label **"RSH"**; worker test proves mint + attribution.
- **WP3 — Digest push (ntfy):** batched cumulative pending-suggestion push (topic
  `damien-homebox-736591e7`); review page stays canonical. Design in `docs/digest-push.md`.
- **WP4 — SSE streaming (flagged OFF):** TransformStream streaming behind `STREAMING` env
  var, defaults `false` (non-streaming typing indicator remains the default path).
- **WP5 — Value-sync formatting-preserving serializer:** minimal-diff scalar edits
  (key order/spacing preserved); apply-engine tests green.
- **WP6 — Turnstile on `/v1/session`:** bot-gate live (`TURNSTILE_ENABLED=true`,
  sitekey `0x4AAAAAAD4uPMN8eNwzYvAy`); untokened → 403 `turnstile_failed`, demo bypass → 200.
- **WP7 — Formatted-block span-splice (apply v1.1):** auto-applies formatted blocks when every
  formatted span's text is unchanged in-order; `needs_human` fallback otherwise.
- **WP8 — Offline red-team approximation:** Opus adversarial battery + synthetic-output
  redaction-path asserts (honest partial substitute; `redteam.mjs` untouched). Evidence:
  `docs/evidence/EP-2026-07-19-offline-redteam.md`.
- **WP9 — Walkthrough prep:** demo runbook rehearsed keyless end-to-end on DEV; screenshots
  refreshed. Evidence: `docs/evidence/EP-2026-07-18-walkthrough-rehearsal.md` +
  `docs/evidence/EP-2026-07-18-walkthrough/`.
- **WP10 — Quality passes:** editor attribution-surfacing fix + a11y sweep of `/edit`
  (`feat/wp10-editor`); cross-matter persona deepening on the 5 thinnest matters m11–m16 —
  new personas Fontaine (m13), Sandoval (m14), Vandermeer (m16) (`feat/wp10-matters`).

**Final gate numbers (all green):**
- Worker: **175/175** · Apply: **66/66** · Digest: **22/22** · Editor-client: **25/25**
- `validate_spine.py`: **PASS** 0 ERROR, 20/20 · `build_site.py --check`: green ·
  two-bundle parity: PASS (`1ddab816d04a6d59`)
- Persona bundle: 59 personas, 501 facts, 20 rubrics

**Branch state:** `feat/weekend-fast-follows` pushed to origin, **unmerged to `main`** pending
Damien's decision-3 (merge approval). Lane branches kept for history.

**Pending decisions (OPEN):** artifact
`dashboard.damienriehl.com/sonsteng-weekend-2026-07-18.html`; brief
`briefs/qa/sonsteng-2026-07-18-weekend.json`. Key asks: (1) walkthrough date (Wed Jul 23?),
(2) John editor-link send, (3) merge `feat/weekend-fast-follows` → `main`, plus any
judgment calls (streaming-default flip, PROD-enable timing).

**Next steps:** Damien answers the batch → merge `feat/weekend-fast-follows` → `main` →
send John his editor magic link → Wed walkthrough.

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

## Decision batch — DELIVERED (awaiting Damien's answers)
Shipped as the interactive cockpit QA artifact
`dashboard.damienriehl.com/sonsteng-weekend-2026-07-18.html` (brief
`briefs/qa/sonsteng-2026-07-18-weekend.json`). Open asks:
1. **Walkthrough date** — confirm Wed Jul 23 (runbook rehearsed READY, keyless demo carries it).
2. **John-link send** — Damien test-drives the editor first, then send John his magic link.
3. **Merge `feat/weekend-fast-follows` → `main`** (decision-3; gates the merge), plus any
   judgment calls (streaming-default flip, PROD-enable timing).
When answered: merge → send John's link → Wed walkthrough.
