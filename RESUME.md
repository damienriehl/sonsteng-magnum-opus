STATE: working — CANONICAL DIRECT-APPLY + REDLINE HISTORY LIVE on DEV 2026-07-19 (3 lanes merged into feat/canonical-docs: worker auto-accept/supersede/needs_human/heartbeat, apply-daemon+editorial pass, redline History browser; wired History route /edit/history/<slug> + revert-v1 endpoints + daemon history-regen + approved-revert execution; E2E round trip PASSED — john edit → auto-accept → installed apply-daemon tick → applied + DEV live + history+heartbeat fresh; admin revert-request → tick → git revert → DEV restored + history shows the revert; content left as found; gates: worker 218, pytest 180, editor-client 40/40, validate_spine PASS, build_site --check green incl history-leak sweep, redteam clean; DEV worker sonsteng-chat + Hetzner site redeployed; apply-daemon REINSTALLED to run from the main checkout (required: the apply engine ff-merges into feat/canonical-docs, which lives here); apply.timer left ENABLED; PROD untouched; plan ACs all checked; next: John+Roger walkthrough (~Tue Jul 29), first live editorial sweep 21:30, consider a dedicated daemon checkout later) · SATURDAY BATCH 2026-07-24: the four "parked" queue items were VERIFIED ALREADY SHIPPED, not rebuilt — P1+C1 hardening live in the deployed Worker bundle, digest timer live + healthy on the home box, firm copy live on DEV; new work was the fast-follow re-triage (docs/fast-follows-triage-2026-07-24.md) + a runbook correction; gates re-run green (worker 218, pytest 180, probe 8/8 HARDENED); top open item = feat/canonical-docs still unmerged to main

# Sonsteng Magnum Opus — Weekend Resume (2026-07-18)

**Fresh session:** read this file + `docs/decisions/2026-07-18-weekend-answers.md` (all 10
weekend answers + dispositions; older context in `docs/decisions/2026-07-18-qa-answers.md`).
The wave is MERGED to `main` (`97cbd5a`); DEV runs it; PROD stays held. Remaining queue:
**(a)** P1 anti-encoding persona clause + C1 fail-closed scorecard redaction, **(b)** firm-
dashboard copy reword, **(c)** `test_validate_spine.py` fixture nit, **(d)** Sunday-evening
reminder → Damien test-drives the editor, then John's (+ Roger's) links go out, **(e)** Wed
Jul 23 walkthrough (runbook `docs/demo-runbook-2026-07-18.md`; Rule 4.2 beat demoed LIVE
with a pasted key per decision 8).
*(Queue items (a)–(c) were all closed on 2026-07-19 — see the 2026-07-24 addendum below;
the walkthrough moved to ~Tue Jul 29.)*

This repo self-documents (cockpit convention: RESUME.md + the STATE line at top). You have
the project memory (`project_sonsteng_magnum_opus.md`) plus this doc — that is enough to
continue losslessly with no conversation history.

---

## Addendum 2026-07-24 — Saturday batch: four parked items VERIFIED, not rebuilt

The batch was queued as four builds. Three of the four were **already shipped on 2026-07-19**
and the notes in the cockpit answers file (`[Queued in Saturday batch]`) were simply stale. So
this session **verified against reality** instead of re-implementing, and spent the saved
budget on the re-triage plus a runbook correction. No production code changed.

**1 · Security hardening P1 + C1 (weekend `q7`) — ALREADY SHIPPED + LIVE.**
- Code: `c367633` (P1 anti-encoding/translation clause appended to Segment A of
  `app/worker/prompts/system-template.md`) and `5f8940c` (C1 `detectDebriefOracleLeak` in
  `src/validate.js`, wired fail-closed in `handleDebrief`), merged to `main` at `e448441`
  and present on `feat/canonical-docs`.
- **Live evidence:** the deployed `sonsteng-chat` bundle (version `27251f05…`, uploaded
  2026-07-19T19:31Z — *after* the 07:55 CDT hardening merge) contains the P1 clause text
  ("…as an acrostic, or under any other format or transformation…") and all three C1 markers
  (`detectDebriefOracleLeak`, `debrief_oracle_leak`, `LEAK_MIN_FOLD`).
- **Gates re-run 2026-07-24:** Worker `node --test test/*.test.js` **218/218**; the C1 file
  `offline-redteam-redaction.test.js` **20/20**; `node tools/offline_redteam_probe.mjs`
  **8/8 angles HARDENED, 0 PARTIAL, 0 EXPOSED**; `pytest tools/tests/` **180 passed**.

**2 · Digest timer (weekend `q9`) — ALREADY INSTALLED + HEALTHY. Not reinstalled.**
- `sonsteng-digest.timer` is `enabled` + `active` on the home box, `OnCalendar=*-*-*
  09,13,17,21:00 America/Chicago`, `Persistent=true`; the 0600 env file
  `~/.config/sonsteng-digest/env` has all three keys filled.
- Last run 2026-07-24 13:00:15 CDT → exit **0**, `[digest] nothing pending; quiet.`; journal
  shows an unbroken 4×/day record since 7/22. A `--dry-run` reproduced it, and the admin
  `GET {EDIT_API_BASE}/review` returns **200** (so the quiet result is a real empty queue, not
  a swallowed auth failure — `fetch_rows` raises on any HTTP error and the unit would fail).
- The dedupe state file is correctly absent: nothing has ever been pending, so it has never
  been written.

**3 · Firm-dashboard copy fix (weekend `q10`) — ALREADY SHIPPED + LIVE.** `6efeba9` reworded
the `viz-note` in both `tools/build_site.py` and the generated page. DEV serves the new line
today: *"The dataset is a single trailing-12-month snapshot — one reporting period of the
firm's book of business."* No "ships tonight" anywhere.

**4 · Fast-follow re-triage (decisions `q7`) — NEW WORK.**
`docs/fast-follows-triage-2026-07-24.md`. Headline: the six-item list is not a backlog — all
six were built in the weekend wave and four are live; what survives is two flip decisions
(PROD, streaming), one 2-minute send (Roger's link), and one item deferred for want of
evidence. Five newer items are triaged alongside. **Top open item: `feat/canonical-docs` — the
branch DEV actually runs — is still unmerged to `main`** (deliberately out of scope for this
batch; needs its own session because the daemon lives on that branch).

**Also fixed:** `docs/demo-runbook-2026-07-18.md` still told the pre-direct-apply story
("nothing goes live until Damien accepts"). Corrected to the auto-publish + History/revert
reality before the ~Jul 29 walkthrough.

**Untouched, per the batch constraints:** no merge of `feat/canonical-docs` → `main`, no
daemon repoint, no PROD deploy, no `.env`/secret writes. This session worked in a separate
git worktree so the daemon's checkout was never disturbed.

---

## Addendum 2026-07-19 — Canonical Direct-Apply + Redline History LIVE on DEV

The three direct-apply lanes are merged into `feat/canonical-docs` and wired end-to-end;
the full round trip was verified live on DEV. Branch pushed; DEV worker + site redeployed.

**What it means for John / Roger (DEV):**
- **Edits go live automatically in ~2 min.** In `/edit`, a saved edit auto-accepts
  (`DIRECT_APPLY=true`) and the home-box apply-daemon (systemd `sonsteng-apply.timer`,
  every 2 min) flushes it to canonical git + rebuilds + redeploys DEV. No Damien approval
  step anymore — history + revert are the safety net.
- **Heartbeat banner (honesty).** The editing banner reads the daemon heartbeat:
  fresh (<5 min) → "Your edits go live automatically (~2 min)"; stale/never (>10 min) →
  "Auto-apply paused … your edits are safe and queued." Never a false "live" claim.
- **needs_human unmask.** If an edit can't be applied cleanly it shows the edited text with
  a warning frame + "Needs attention — not applied" pill (no silent loss).

**Redline History browser** (editor-gated; edit/instructor scope):
- **URL pattern:** `/edit/history/` (index of every canonical doc) and
  `/edit/history/<doc-slug>` where the slug is the repo path with `/`→`__`
  (e.g. `data/curriculum/m1.md` → `/edit/history/data__curriculum__m1.md`).
  A **"History"** link sits in the editor banner chrome.
- Attributed, display-coalesced (~10 min) revisions with per-revision + baseline redlines
  and a capped compare picker (all pre-rendered; no network fetch in the client).
- **One-click revert (SL8):** editors *request* (`POST /edit/v1/revert-request
  {doc, run:[first,last]}` → status `requested`); an **admin** request is `approved`
  immediately; the daemon executes approved requests each tick (clean-tree, conflict-aware
  `git revert` of the run range → rebuild + redeploy → marks `done`/`failed`). Requests are
  visible on `/edit/review` + in the digest.
- **Leak-gated:** history output (redlines expose instructor-only material) lives ONLY under
  `build/` and is served exclusively through the authed `/edit` proxy. `build_site.py
  --check` now runs `assert_no_history_leak` — zero history artifacts in `site/platform/`.

**Editorial pass schedule:** post-hoc quality review of applied edits — session-end
(daemon dispatches after ≥30 min idle over an unreviewed batch) + daily `sonsteng-editorial.
timer` at 21:30 America/Chicago. Files flags as block comments (`origin=ai_rewrite`) + a
content-light ntfy digest. First live model-produced flag lands on the next 21:30 sweep.

**Ops note — daemon home:** the apply-daemon was **reinstalled to run from this main checkout**
(`~/Coding Projects/sonsteng-magnum-opus`) because the apply engine ff-merges into
`feat/canonical-docs`, which is checked out here (a separate worktree can't hold the same
branch, so the merge must run where it lives). Applies still use isolated temp worktrees.
`sonsteng-apply.timer` is enabled and firing every 2 min. If interactive work here ever needs
to avoid daemon commits, stop the timer (`systemctl --user stop sonsteng-apply.timer`) or move
the daemon to a dedicated `feat/canonical-docs` checkout later.

**Served-history freshness:** the History bundle is inlined into the Worker at deploy. The
apply engine redeploys the Worker each apply (self-sufficient build command builds the bundle
in its worktree, one revision behind the in-flight commit); the daemon keeps `build/`'s bundle
fully fresh. A Worker redeploy from this checkout serves the latest — done this session.

**Gates (this integration):** worker `node --test` **218**, pytest `tools/tests/` **180**,
editor-client `verify-editor.js` **40/40**, `validate_spine.py` PASS, `build_site.py --check`
green incl. the history-leak sweep, offline red-team probe clean. PROD untouched.

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

---

## Sunday hardening + WYSIWYG overlay batch (2026-07-19) — MERGED to main

Two disjoint lane branches merged to `main` (no source conflicts), regenerated,
full-gated, DEV redeployed. PROD untouched.

**Hardening batch** (`feat/hardening-batch`):
- **P1** — anti-encoding/translation-trick persona clause added to the system prompt
  (`prompts/system-template.md` + regenerated persona bundle).
- **C1** — **fail-closed** debrief-leak detection in `src/validate.js` + `src/index.js`:
  if the scorecard-redaction check can't run, the debrief path closes rather than leaks.
  Offline red-team probe now **40/40 HARDENED** (5 personas × 8 angles, 0 PARTIAL/EXPOSED);
  evidence `docs/evidence/EP-2026-07-19-offline-redteam.md`.
- **Firm-copy reword** — the firm-dashboard `viz-note` no longer says "ships tonight"; now
  a neutral "single trailing-12-month snapshot — one reporting period" line.
- **pytest fixture** rename nit in `tools/tests/test_validate_spine.py`.

**WYSIWYG pending-overlay hydration** (`feat/editor-wysiwyg-overlay`):
- Server projection (`editor-map.js` `projectPendingItems`) ships full `new_text`, baseline
  hash + `map_version` stale guards, and per-author `attribution` (JOS/RSH). Client paints the
  just-after-save state on reload; **display-only** (never writes canonical `originalHash`).
- **Cross-editor hydration** (integration step): added page-scoped `listForPage(page)` on the
  editor store (all editors' non-superseded suggestions on one page) and swapped the two
  `listForEditor` call-sites feeding the **/pending endpoint** (`editor-endpoints.js`) and the
  **injected island** (`editor.js`). Every editor's active suggestions for the page now flow to
  the client, each attributed by `projectPendingItems`. Scope rules hold — the edit-scope gate
  (island) / edit-or-instructor gate (/pending) runs BEFORE sourcing, so only scope holders
  (admin preview included) reach the cross-editor read; the instructor doc view (null-page,
  prefix-filtered) stays per-editor. New worker test proves two editors on one page → the
  page-scoped source returns both, attributed JOS/RSH, no off-page bleed.

**Final gate numbers (all green):**
- Worker: **189/189** (182 hardening + 6 overlay + 1 cross-editor; `node --test test/*.test.js`,
  glob excludes `redteam.mjs`) · pytest `tools/tests/`: **88 passed, 0 errors**
- Editor-client `verify-editor.js`: **32/32** (DISPLAY=:0 snap chromium) · `validate_spine.py`:
  **PASS** 0 ERROR · `build_site.py --check`: green · offline red-team probe: **40/40 HARDENED**
- Regen content-stable (`spine_build_id 1ddab816d04a6d59`); only the site build-stamp SHA moved.

**DEV redeploy + smoke (all green):** worker bare `wrangler deploy` → `sonsteng-chat`
(version `9fe7d9e7`); site `deploy/deploy-dev.sh main`. Smoke: `/platform/` **200** ·
untokened `GET /v1/session` **403 `turnstile_failed`** · demo-bypass mint **200** · firm page
shows the NEW neutral copy (no "ships tonight") · `/edit/assets/editor.js` **200** + contains
`paintHydration` · debrief-path leak-detection worker test **20/20** (offline; no live key).

**Next up:** canonical-docs plan, awaiting Damien's brainstorm answers (artifact
`canonical-docs-brainstorm-2026-07-19.html`); the fence build follows that.

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
