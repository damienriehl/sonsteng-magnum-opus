STATE: working — 2026-07-27 (Mon): reviewer platform verified ready and heavily revised from Damien's live review. WALKTHROUGH MOVED to week of Aug 3. Editor: /edit nav reachability fixed (7 pages were 404s incl. the Matter Library landing page), 1,739 of 3,474 blocks were wrongly comment-only and are now editable, affordances rebuilt as an overlay icon rail with the edit view reserving its own margin, declined edits no longer tattoo a paragraph, Save/Cancel replaced by Done/Undo to match auto-save, friendly locked-out page. A11Y: new tools/a11y_audit.js found 484 failures -> 0 (token-level fixes; the STANDARD/LARGE TYPE toggle was 1.06:1). Skill/task codes replaced by names. Damien has his own DR editor token. DISK EMERGENCY OVER — dev-twin relocated containerd, root 99% -> 12%; the 50->100GB volume resize Damien authorised is what made that possible; alarm now watches / and /mnt/docker-data. OPEN: Cloudflare-Access door (ask sonsteng-2026-07-27-access-door), image-retention permission (sonsteng-2026-07-27-image-retention), phone-width rail assertion unresolved, a11y audit not yet wired to a gate. Gates on main: worker 224, pytest 189, editor client 43/43 headful, a11y 0 FAIL, validate PASS, parity PASS.)


---

## Addendum 2026-07-27 (Monday) — reviewer readiness, an accessibility sweep, and what other sessions need

A long session driven by Damien reviewing the editor live. **The walkthrough moved to the
week of Aug 3**, which is why the Cloudflare-Access door is now worth doing before John and
Roger ever see a token URL.

### Handoff — things another session must not re-derive or re-break

1. **The editor client is BUNDLED into the Worker.** Editing `app/editor/editor.js` or
   `editor.css` changes nothing until `node app/worker/scripts/bundle-editor-data.mjs` runs
   and the Worker is deployed. It fails silently — the old bundle keeps serving and you will
   debug a fix that was never deployed. Cost me two false diagnoses today.
2. **`main` is canonical and is checked out in the daemon worktree**
   (`~/.local/share/sonsteng-daemon/checkout`). Merge into `main` FROM there, under
   `flock <daemon>/.locks/daemon.lock`. This checkout cannot check out `main`.
3. **The disk alarm on hetzner-dev already exists — do not build a second one.**
   `/usr/local/bin/disk-alarm.sh` + `disk-alarm.{service,timer}` (15 min), config
   `/etc/disk-alarm.env` (0600 root), per-mount state in `/var/lib/disk-alarm/`. It is a
   **systemd timer, not a cron entry** — a coding-projects session looked for an exporter,
   an agent and a disk cron, found none, and concluded the box was unmonitored. It watches
   **both `/` and `/mnt/docker-data`** since the containerd relocation. Happy for dev-twin to
   adopt it as box-level infrastructure; it needs no sonsteng context to run.
4. **`/mnt/docker-data` is the disk that matters now** — 70% (65G of 99G), 16.15GB
   reclaimable. The standing-prune decision is open on `sonsteng-2026-07-27-image-retention`;
   please don't ask Damien the same question from another repo.
5. **Visual QA is unblocked for every project.** Chromium needs Xwayland here, and Xwayland is
   auth-gated by a mutter cookie whose filename regenerates on every login — `DISPLAY` alone
   was never enough, which is why screenshot verification had been broken for weeks. The
   chrome-devtools MCP now launches via `~/.claude/hooks/chrome-devtools-launch.sh`, which
   resolves the newest cookie at start. For scripts:
   `DISPLAY=:0 XAUTHORITY=$(ls -t /run/user/1000/.mutter-Xwaylandauth.* | head -1)`.

### What shipped

- **`/edit` navigation was broken.** The map registered only pages carrying editable text
  while the injector rewrote every link into `/edit` space, so platform home, the matter
  library, skills, the firm dashboard and the third-party page were 404s, and the
  client-interview links were rewritten to dead paths with their query strings stripped. Every
  hostable page is registered now; the chat surfaces are deliberately excluded (the injector
  strips page scripts, so the simulator would be inert) and unhostable links keep their real
  URL. `604f3cb`.
- **Half the blocks were not editable.** The client refused every `json_scalar` and every
  inline-formatted paragraph — 1,739 of 3,474 — though the Worker rejects only
  `comment_only` and the engine handles both (WP5 scalar splice, WP7 span-splice with a
  `needs_human` fallback). `e792476`.
- **Affordances redesigned** to icons with revealed labels, then rebuilt three times over
  placement. Final: gutter rails live in an **overlay layer** in document coordinates, aligned
  to one column edge, and **the edit view reserves its own margin** (`body` padding-right
  ≥900px) because the practicum's fluid layout leaves no gutter at 1280px. Regression test
  `app/editor/verify-rail-placement.js` checks every rail against all page text at ten widths.
  `3a45f45`.
- **Accessibility sweep.** New `tools/a11y_audit.js` (contrast, control contrast, accessible
  names, target size, alt, heading order, landmarks, lang) found **484 failures**; now 0.
  Root causes were token-level — `--ink-faint` at 3.43:1 and `--brass` at 3.09:1 colouring
  ~11.5px type sitewide. The inherited palette is marked "do not alter" and was not altered;
  three **text-safe variants** were added for the cases where a token colours words. The
  `STANDARD / LARGE TYPE` toggle was at **1.06:1**. `98e1657`.
- **Skill and task codes now say what they mean** — chips carry the names, the code moves to
  the tooltip. `skill_label()` trims survey boilerplate ("Ability to diagnose and plan
  solutions…") for display only.
- **Damien has his own editor identity** (`EDIT_TOKEN_DAMIEN`, label **DR**) so his test edits
  are not stamped JOS. Ops note: `cat file | wrangler secret put` stores the trailing newline
  and the token then never matches — pipe with `printf '%s'`.
- **The locked-out page explains itself.** The `?t=` token is consumed on arrival and stripped
  from the address bar, so a bookmark taken *after* landing works only until the cookie
  lapses — then a blank "Not found." Damien hit exactly that. The page now says how to get
  back in, with the body still byte-identical for every reason so it remains no oracle.

### Open, honestly

- **Phone-width rail placement.** `verify-rail-placement.js` reports 44 rail-over-list-item
  intersections at ≤768px. A direct probe at 768px could not reproduce it. Desktop is clean
  geometrically and by eye. Not claimed as fixed.
- **The a11y audit is not wired into any gate** — it needs a browser, so it cannot sit inside
  the pure-Python `build_site --check`. An unwired audit is how a 1.06:1 toggle shipped.

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

## Addendum 2026-07-24 (evening) — `main` is canonical + the daemon has its own checkout

Triage items **#8 and #9** are closed. `main` now carries everything DEV runs, and the
apply-daemon no longer shares a working tree with interactive sessions.

**1 · `feat/canonical-docs` → `main` (item #8).** `main` was a strict ancestor (0 commits it
had that the branch lacked, 32 the other way), so the merge was a clean `--no-ff` at
`7efff2e`; the trees were byte-identical afterward. Gates re-run on the merge result **in the
new daemon checkout**: worker `node --test test/*.test.js` **218/218**, `pytest tools/tests/`
**180** (now 183), `node tools/offline_redteam_probe.mjs` **8/8 HARDENED / 0 PARTIAL / 0
EXPOSED**, `validate_spine.py` **PASS** (0 ERROR, 7 WARN), `build_site.py --check` green incl.
link check + instructor-leak + history-leak sweeps, `check_build_parity.py` **PASS**
(`1ddab816d04a6d59`). `feat/canonical-docs` is kept for provenance.

**2 · Dedicated daemon checkout (item #9).** The daemon runs from
`~/.local/share/sonsteng-daemon/checkout` — a **git worktree** on `main`, touched by nothing
else. Both user units' `ExecStart` point there and `APPLY_DEPLOY_BRANCH=main`.
`tools/install-apply-daemon.sh` provisions it idempotently (creates the worktree, builds the
gitignored bundles a fresh worktree lacks, warns on a branch mismatch);
`SONSTENG_DAEMON_ROOT` overrides the path. Worktree, not clone, so the daemon's `apply:` /
`revert(history):` commits stay visible + pushable from here. **Consequence: `main` is checked
out there, so this checkout can't check it out** — merge into `main` from the daemon worktree
under `flock <daemon>/.locks/daemon.lock` (the flock moved there too). Full rationale:
`docs/direct-apply-daemon.md` "Deploy topology".

**3 · Latent stall found + fixed (would have hit on demo day).** `build_site.py` stamps the
current HEAD into `site/platform/data/.build-stamp.json`, so the tick's post-apply rebuild
always left that one tracked file dirty — and the engine's `assert_clean_tree` is strict. The
**second** edit of a session would have failed with "canonical tree is dirty" and flipped the
banner to "Auto-apply paused". The daemon now restores the regenerable `site/` output before
the apply and after the deploy (the tolerance `execute_revert` already carried). 3 new tests.

**4 · E2E re-proven from the new checkout (content left as found).** john suggestion on
`data/curriculum/m1.md#p5` → auto-`accepted` → daemon tick **applied 1, rebuilt, deployed
main, heartbeat sent** in 52 s → commit `24d3c9f`, DEV served the new text, **tree clean after
the apply** (the fix, observed). Admin `POST /edit/v1/revert-request` → auto-approved → next
tick → `revert(history)` commit `0c45107`, DEV restored, tree clean. History browser
`/edit/history/data__curriculum__m1.md` → **200**, 5 revisions for m1 incl. today's edit +
revert. Review queue back to **0**. `main` pushed at `0c45107`.

**5 · ⚠ DEV BOX DISK — the real walkthrough risk.** `hetzner-dev` hit **100% full**
mid-session and the first two apply attempts failed at the deploy step with `No space left on
device` (rows correctly parked `accepted_blocked`, nothing lost). Root cause is **not**
sonsteng: `/var/lib/containerd` is **64 GB on the 75 GB root** while the 49 GB
`/mnt/docker-data` volume (Docker's configured root) sits at **2%** — the data-root move was
only half done — and another Coolify app was pulling a ~10 GB torch/CUDA image at the time.
Freed this session **without touching anyone's data**: `docker builder prune -a` (9.7 GB
regenerable build cache), `journalctl --vacuum-size=50M`, `apt-get clean`, plus **one**
superseded, unused app image (`k4k4pd2…:1b37b0f8`, 8.9 GB, 2 weeks old, rebuildable). Box now
~93% used. **This will recur** — see the cockpit ask `sonsteng-2026-07-24-devbox-disk`.

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

**Ops note — daemon home:** ~~the apply-daemon was reinstalled to run from this main
checkout~~ **SUPERSEDED 2026-07-24** — the daemon now runs from its own worktree,
`~/.local/share/sonsteng-daemon/checkout` on `main`. See the 2026-07-24 addendum below and
`docs/direct-apply-daemon.md` "Deploy topology".

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
