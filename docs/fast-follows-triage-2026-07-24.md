# Fast-follow backlog — re-triage 2026-07-24

> **Deployment-policy update (2026-08-04):** the PROD approval hold recorded
> below is superseded. Merged, release-green engineering changes may deploy to
> PROD without a separate confirmation. The remaining text is retained as the
> historical July triage record.

*Requested by Damien's answer to `q7-fast-follows` (cockpit brief
`sonsteng-2026-07-18-decisions`): **"None for now — revisit after the walkthrough … revisit
next week with fresh capacity."** This is that revisit, done against reality (live DEV, the
deployed Worker, the home box's systemd timers, and the current branch tips) rather than
against the 2026-07-18 backlog text.*

**Headline:** the 2026-07-18 fast-follow list is **not a backlog anymore**. All six items were
built during the weekend wave (WP1–WP7) and merged; four of them are already **live**. What
survives is three *decisions/ops steps* (not builds), plus five items that surfaced **after**
the list was written. **Nothing on the original list needs building before the ~Jul 29
walkthrough.**

Scope note: triage only — **nothing on this list was built in this pass.**

---

## A. The original six (from the 2026-07-18 `q7-fast-follows` sheet)

| # | Item | Reality on 2026-07-24 | Still relevant? | Effort | Recommendation |
|---|------|------------------------|-----------------|--------|----------------|
| 1 | **PROD injector wiring** (`EDIT_UPSTREAM` → Pages) | **Built, not flipped.** `wrangler.jsonc` `env.production` points `EDIT_UPSTREAM` at `https://sonsteng.damienriehl.com/platform/` and `EDIT_ORIGIN` at `sonsteng-chat-production…`; one-command runbook in `docs/prod-enable.md`. PROD verified still pitch-only (every path returns the pitch page; `/platform/` serves the pitch `<title>`). | Yes — but it is a **PROD deploy**, gated by Damien (decision q3: hold until after the walkthrough), and PROD deploys always come back to him regardless. | S (config + one deploy) | **NEXT** — after the walkthrough, on Damien's explicit go. Not engineering work. |
| 2 | **Roger as 2nd editor** | **Done, including the secret.** `EDIT_TOKEN_ROGER` is set on the live Worker (confirmed in `wrangler versions view`); `EDIT_TOKEN_SCOPES` grants roger `edit`+`instructor`; attribution label **RSH** ships; cross-editor overlay hydration landed 2026-07-19 so John and Roger see each other's pending edits on a shared page. | Yes as an **ops step**, no as a build: all that remains is resolving + sending his magic link. | XS (2 min) | **NOW-ish** — bundle with John's link when Damien sends it (week of Jul 27). Reclassify from "build" to "send". |
| 3 | **Automated digest push** (ntfy/email) | **Shipped and live.** `sonsteng-digest.timer` enabled + active, 09/13/17/21 America/Chicago, `Persistent=true`. Last run 2026-07-24 13:00 CDT, exit 0, `[digest] nothing pending; quiet.`; admin `GET /review` → **200**. Dedupe state file correctly absent (nothing has ever been pending). | No — closed. An **email** channel remains theoretically open and is not worth building; ntfy is the home box's notification bus. | — | **DROP** from the backlog (shipped). |
| 4 | **SSE streaming for chat** | **Built, flag OFF.** `STREAMING="false"` in all three env blocks (top-level, `env.dev`, `env.production`); the typing indicator is the default path. Damien: keep OFF through the walkthrough. | Yes, as a **flip decision** after the walkthrough. Rationale for OFF stands: Haiku turns land in 2–4 s and the indicator covers it, and flipping introduces a new live-behavior variable right before a demo. | XS to flip; M to validate across the three BYOK providers | **NEXT** — flip on DEV *after* Jul 29, validate all three providers, then decide whether it stays on. |
| 5 | **Value-sync formatting-preserving serializer** | **Mostly shipped.** WP5 gave minimal-diff scalar edits (key order/spacing preserved); WP7 added formatted-block span-splice that auto-applies when every formatted span's text is unchanged in order. Residual: blocks whose *formatted spans themselves* changed still route to `needs_human`. | **Unproven need.** The review queue has been empty all week, so no real `needs_human` sample exists. Building the general splice now would be speculative. | M–L | **DROP until data** — revisit only if John/Roger actually hit `needs_human` on formatted blocks. The `needs_human` unmask already prevents silent loss, so the failure mode is visible and safe. |
| 6 | **Turnstile on session mint** | **Live.** `TURNSTILE_ENABLED="true"`, sitekey `0x4AAAAAAD4uPMN8eNwzYvAy`; untokened `GET /v1/session` → 403 `turnstile_failed`, demo bypass → 200; 12 gate tests green inside the 218-test Worker suite. Managed mode confirmed OK by Damien (q6). | No — closed. | — | **DROP** from the backlog (shipped). |

**Net for the original six:** 0 builds required. 2 shipped-and-closed, 2 waiting on a Damien
decision (PROD flip, streaming flip), 1 reduced to a 2-minute send (Roger's link), 1 deferred
pending real-world evidence.

---

## B. Items that surfaced after the list was written

| # | Item | Why it exists | Still relevant? | Effort | Recommendation |
|---|------|---------------|-----------------|--------|----------------|
| 7 | **Runbook told the pre-direct-apply story** | `docs/demo-runbook-2026-07-18.md` still said "every change is a suggestion; nothing goes live until Damien accepts" — false since 2026-07-19. It would have misdescribed the headline feature to John & Roger. | — | XS | **DONE in this pass** (commit `docs(runbook): editors-preview matches direct-apply reality`). Listed here so the triage is complete. |
| 8 | **`feat/canonical-docs` is unmerged to `main`** | DEV runs the branch (the daemon deploys `APPLY_DEPLOY_BRANCH=feat/canonical-docs`); `main`'s tip is the 2026-07-19 hardening/overlay merge. Anything built from `main` — a fresh clone, an OSS adopter, a PROD deploy — lacks direct-apply, the History browser, and revert. | **Yes — the single largest correctness gap in the repo today.** | S–M (merge + regen + full gates; the daemon must be repointed or stopped for the swap, so it needs its own session) | **NEXT** — a dedicated session, deliberately **out of scope** for this batch. Do it before any PROD conversation; ideally before Jul 29 so the walkthrough and `main` tell the same story. |
| 9 | **Daemon runs from the interactive checkout** | The apply engine fast-forward-merges into `feat/canonical-docs`, which is checked out at `~/Coding Projects/sonsteng-magnum-opus` — so interactive sessions and the every-2-min daemon share one working tree. A session that parks a dirty tree blocks the daemon's clean-tree gate (revert execution). | Yes — a live foot-gun, and worse during a walkthrough. | S (a dedicated checkout + repoint `APPLY_DEPLOY_BRANCH`'s home) | **NEXT** — pair it with #8, which has to touch the same wiring anyway. Mitigation until then: sessions here must leave the tree clean (this batch used a separate worktree for exactly that reason). |
| 10 | **Live `redteam.mjs` has never run** | Under BYOK-forever (q4) there is no house key, so the live adversarial harness has never driven a real model. The 2026-07-19 evidence pack is explicit that a well-worded prompt is *not* proof a model obeys it — the 40/40 HARDENED matrix describes the prompt's defenses, not observed behavior. | Yes — it is the only way to convert the prompt layer from "designed" to "verified". | XS **given a key** (the harness is ready and untouched) | **OPPORTUNISTIC — NOW if a key appears.** Damien is pasting a key on walkthrough day for the Rule 4.2 beat (q8); run `redteam.mjs` against the deployed Worker in that same window. Cheap, and it closes the honest gap in the evidence pack. |
| 11 | **C1 residual: `facts_elicited` is model-supplied** | The fail-closed leak scan carves out facts the model reports as elicited. A model that both leaks a concealed fact *and* falsely reports it elicited would evade the scan. | Marginal. That same lie is a separate, less-severe pedagogy failure, and the realistic threat (leak without falsification) is closed. Deriving `facts_elicited` independently from the transcript is a real piece of work. | M | **DROP** — documented honestly in `EP-2026-07-19-offline-redteam.md` §4. Revisit only if #10 ever shows a live model doing it. |
| 12 | **Rule 4.2 keyless demo fallback** | The no-contact lesson only fires after a live turn to a represented party, so beat 7 needs a key. The runbook offers option (c): a scripted `?sample=1` replay. | Only if the key plan wobbles. Damien chose "demo it live with a key" (q8). | S | **DROP unless the key plan changes** — decide on walkthrough morning, not now. |
| 13 | **Midstate / Trialbook originals** | Deferred by decision for copyright avoidance; pivot path recorded in `docs/decisions/2026-07-18-midstate-deferred.md` (separate-license `data/midstate/`, © Sonsteng). | Yes, but the owner is **John**, not us. | L | **DROP / hold** — it is a walkthrough *ask* (runbook ask #5), not a build. Revisit only if John says yes on the day. |

---

## C. What this means for the ~Jul 29 walkthrough

- **Nothing on the fast-follow list blocks it.** The demo path is keyless end-to-end except the
  two 🔑 beats, and every fast-follow that touches the demo (Turnstile, digest, Roger, streaming
  OFF) is already in its final state.
- **Two things would improve it,** in priority order: (a) #8 — get `feat/canonical-docs` into
  `main` so the branch DEV demos and the branch the repo advertises are the same thing; (b) #10 —
  run `redteam.mjs` with the key that is already going to be pasted that day.
- **One thing to hold the line on:** #1 (PROD) stays held until Damien says otherwise, per q3.

## D. Recommendation summary

| Verdict | Items |
|---|---|
| **NOW / opportunistic** | #2 (send Roger's link with John's) · #10 (run `redteam.mjs` on walkthrough day if a key is pasted) |
| **NEXT (own session)** | ~~#8 + #9~~ **DONE 2026-07-24 eve** (merge `7efff2e`, daemon worktree at `~/.local/share/sonsteng-daemon/checkout` on `main`, E2E re-proven) · #1 (PROD flip, on Damien's go) · #4 (streaming flip on DEV, after the walkthrough) |
| **DROP / closed** | #3, #6 (shipped) · #5 (no evidence of need) · #11 (documented residual) · #12 (contingent) · #13 (John's call) |
| **Done in this pass** | #7 (runbook corrected) |

**Update 2026-07-24 (evening session):** items **#8 and #9 are closed** — see the RESUME
addendum "main is canonical + the daemon has its own checkout". That session also found and
fixed a latent stall (the post-apply rebuild left `.build-stamp.json` dirty, so the *second*
edit of a session would have failed the engine's clean-tree gate) and surfaced a **new item
#14: the DEV box is out of disk** (`/var/lib/containerd` 64 GB on the 75 GB root while the
49 GB `/mnt/docker-data` volume idles at 2%). #14 is the top remaining walkthrough risk —
cockpit ask `sonsteng-2026-07-24-devbox-disk`.
