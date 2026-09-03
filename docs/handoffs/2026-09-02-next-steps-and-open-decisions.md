---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-02T22:55:00Z"
title: "Next steps after the persona UAT program, with Damien's open decisions"
summary: "Nothing autonomous is unblocked; seven decisions collected for Damien, three of which would unblock bounded agent work; Packets A–F unchanged."
keywords: ["next-steps", "open-decisions", "packets-a-f", "dev-worker", "preflight-gate", "clean-urls", "persona-uat"]
cwd: "/home/damienriehl/Coding Projects/sonsteng-magnum-opus"
resume_focus: "Fire the seven collected decisions to Damien inline as one batch, then activate only what his answers unblock."
repository: "damienriehl/sonsteng-magnum-opus"
repo_root_sha: "be839bfa39467199befa451a2d3f30f63432bab2"
branch: "docs/next-steps-handoff-2026-09-02"
head: "6c262d59b7205be9ff4037de8514e34f08c21db8"
worktree_path: "~/worktrees/sonsteng-next-steps"
---

# Next steps after the persona UAT program

Written by the session that closed the persona UAT program (see
`docs/handoffs/2026-09-02-persona-uat-closeout.md` for what shipped and what each persona can
do today). Damien asked for next steps, said to continue in a new session if that made more
sense, and asked that decisions be collected and kept for him rather than acted on. This
session judged a fresh session the better overseer (a long, deep session on one subject is a
poor one), so this document is the resume point. The private companion with machine-local
paths is `~/.local/state/ce-handoffs/sonsteng-magnum-opus/2026-09-02-next-steps.md`.

## Ground truth at handoff

- `origin/main` is `6c262d5`; production serves `49e24f4` (Worker `6932fea4…`, Pages
  `37a2f2f0`); DEV serves the same site build. No open pull requests; no feature branches
  local or remote; no stashes; one clean detached run worktree at `origin/main`.
- The remaining planned queue is Packets A–F from
  `docs/decisions/2026-08-23-plan-closeout-decision-sheet.md`. Each waits on a prerequisite
  only Damien controls; his September 2 answer (Q2 in
  `docs/decisions/2026-09-02-resume-and-uat-decision-sheet.md`) confirmed none had changed.
  The persona UAT program did not change any of them either.
- The cockpit was frozen throughout (sentinel dated 2026-08-30); `briefs/on-deck.json` still
  carries July and August items for this repository that are long done. It could not be
  refreshed under the freeze.

## Findings this session left for a decision (my observations, not Damien's asks)

1. **The DEV Worker is three weeks stale and carries no release SHA.** The default
   (non-production) Worker at `sonsteng-chat.damienriehl.workers.dev`, which also serves the
   editor behind `edit.legalpracticum.org`, was last deployed 2026-08-11T22:58Z, before every
   Worker change merged since. Its `/edit/release-provenance` answers 503 because `RELEASE_SHA`
   was never set on that deploy, so the record's DEV binding rows carry a null build key.
   Redeploying it is a live change for John and Roger's editor, so it is not autonomous.
2. **Production Worker vars gap (hygiene, not exposure).** `app/worker/wrangler.jsonc` defines
   `EDIT_ACCESS_AUD`, `EDIT_ACCESS_TEAM_DOMAIN`, `EDIT_ACCESS_HOST`, `EDIT_LEGACY_HOST`,
   `PUBLIC_CANONICAL_HOST`, and `PUBLIC_REDIRECT_HOSTS` at the top level but not under
   `env.production.vars`; wrangler warns on every production upload. Verified read-only at
   handoff: production `/edit/index.html` and `/edit/pending` answer 404 without a token,
   the editor door redirects to Access, provenance answers 204. So nothing is open; the
   question is whether production's redirect and Access code paths ever need those values.
3. **DEV has no clean-URL rewrite.** `deploy/docker-compose.yml` runs stock `nginx:alpine`
   over `site/`; `/cost-per-credit` is 404 on DEV and 200 on production Pages. A mounted
   nginx config with `try_files $uri $uri.html $uri/ =404` would give parity. DEV-only,
   in-repo, reversible; still an environment change nobody asked for.
4. **The persona journeys are not a preflight gate.** `tools/preflight.sh` has 21 gates; the
   local browser leg of `tools/verify_persona_journeys.js` (about five minutes, 44 attempts,
   needs the snap Chromium) is not among them, so a regression in a journey would surface
   only when someone runs the program again.
5. **Accessibility warnings.** The audit reports 0 FAIL but 126 WARN across the twelve audited
   page cases, mostly the project's own AAA 44×44 target-size rule (skip links, header
   wordmarks, `button.hot` rows). Pursuing AAA target sizes is a design choice.
6. **Two BLOCKED legs need one credential.** `student-live-provider-dev` and
   `hostile-live-redteam-dev` stay BLOCKED until `SONSTENG_UAT_GOOGLE_CREDENTIALS_FILE` names a
   protected Google credential file (Packet B territory; the runner records BLOCKED honestly).
7. **`final-revalidation.sh` lives in session scratch.** The driver that ran every leg at one
   SHA is not in `tools/`; if the program is to be repeatable it should be.

## Decisions collected for Damien (fire as one inline batch; recommendations are mine)

| # | Decision | Recommendation | Unblocks |
|---|---|---|---|
| D1 | Redeploy the default DEV Worker from `main` with `RELEASE_SHA`, at a quiet moment with John and Roger told? | Yes, after checking the Worker diff since 2026-08-11 for anything editor-facing | finding 1; DEV binding provenance |
| D2 | Add the six missing vars to `env.production.vars` (or record why production does not need them)? | Investigate the code paths first; add only what production reads | finding 2 |
| D3 | Give DEV a clean-URL nginx config for parity with Pages? | Yes | finding 3; drops the `.html` special case in audits |
| D4 | Add the persona journeys' local browser leg to `tools/preflight.sh` as a gate? | Yes, local leg only, after the Chromium requirement is documented | finding 4 |
| D5 | Pursue AAA target sizes on the warned controls, or accept AA and record the WARNs as accepted? | Accept AA for now; revisit with the platform redesign | finding 5 |
| D6 | Supply the protected Google credential path for the two live legs? | Damien's action; agent then runs `--bindings --env-label dev --only student-live-provider-dev,hostile-live-redteam-dev` | finding 6 |
| D7 | Promote the revalidation driver into `tools/` with a contract test? | Yes | finding 7 |

Standing, not a question: Packets A–F stay human-gated until Damien says a prerequisite changed;
the pre-user production runbook expires at the first real user.

## What a fresh session should read before acting

- `docs/handoffs/2026-09-02-persona-uat-closeout.md` (what shipped; the per-persona table).
- `docs/solutions/uat/2026-09-02-browser-journeys-measure-the-wrong-thing.md` (the traps:
  generators before preflight in a fresh worktree; merge on `, 0 failed`; snap `/tmp`).
- `docs/decisions/2026-08-23-plan-closeout-decision-sheet.md` (Packets A–F, unchanged).
- `docs/pre-user-prod-deploy.md` and `docs/uat/pre-user-prod-deploys.md` (runbook and the
  four deploy records; rollback pair is in the last block).

## Working rules the session inherited and kept

- Workers are Codex at every tier; the orchestrator verifies each report by re-running the
  tests and inspecting the tree, and waits for the job's `completed` status, because a job
  keeps editing after it writes its report.
- Questions for Damien go through the inline Q&A UI, batched, with the recommended option
  first; he asked for this explicitly on 2026-09-02.
- Production writes run through the runbook script under the Wrangler permission he granted
  the same day; the permission rules live in the untracked `.claude/settings.local.json` of
  the primary checkout.
