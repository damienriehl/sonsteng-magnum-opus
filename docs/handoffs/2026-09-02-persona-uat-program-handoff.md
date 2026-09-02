---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-02T18:30:00Z"
title: "Persona UAT program — mid-execution handoff"
summary: "Plan and runner shipped, first fixes deployed to production; the receiving session finishes the DEV/production journey runs, remaining fixes, the final full revalidation, and closeout."
keywords: ["persona-uat", "journey-runner", "pre-user-prod-deploy", "branch-cleanup", "handoff"]
cwd: "/home/damienriehl/Coding Projects/sonsteng-magnum-opus"
resume_focus: "Continue U3–U8 of the persona UAT plan on branch feat/persona-uat-program; Codex workers only."
repository: "damienriehl/sonsteng-magnum-opus"
branch: "feat/persona-uat-program"
worktree_path: "~/worktrees/sonsteng-uat-program"
---

# Persona UAT program — mid-execution handoff

## Authority and decisions (do not re-ask)

`docs/decisions/2026-09-02-resume-and-uat-decision-sheet.md` records Damien's September 2 answers:
production pushes are allowed throughout the pre-user stage (Q1), no Packet A–F prerequisite changed
(Q2), full branch cleanup with evidence (Q3), all ten personas with student, author-editor, and
prospective reader first (Q4). Packets A–F in the August 23 sheet stay human-gated.

Cockpit guardrails from Damien's message stand: cockpit-freeze is on; no writes into the Cockpit
repository; push work in this repository explicitly.

## What is done

- Plan: `docs/plans/2026-09-02-1108-test-persona-uat-program-plan.md` (implementation-ready, six-persona
  doc review folded in). Research dossier: `docs/research/2026-09-02-uat-harness-inventory.md`.
- Production is deployed from `main` twice under the runbook `docs/pre-user-prod-deploy.md`; the record
  is `docs/uat/pre-user-prod-deploys.md` (latest: `0643a54`, Worker `cb2e029d…`, Pages `dbff9a7b`).
  Provenance check must use GET, not HEAD.
- Repository hygiene: 74 merged local and 31 merged remote branches deleted; nine unmerged branches,
  their worktrees, and six stashes removed with evidence in
  `docs/evidence/2026-09-02-branch-supersession-evidence.md`. One stash remains (see questions).
- PR #32 (decision sheet), #33 (plan, runbook, evidence, dossier), #34 (first bounded fixes) merged.
- Branch `feat/persona-uat-program` (pushed) carries U1, U2, U2b, U2c, the OPEN plan
  `docs/plans/2026-09-02-1226-fix-pitch-accessibility-conformance-plan.md`, and the deploy record.
  `main` is merged into it.
- Evidence gathered by the orchestrator (to import into the record): bot-gate checks all `403` on DEV and
  production; Access-policy audit PASS (one IdP, one allow policy, three email selectors, no bypass or
  service-token rules; no addresses recorded); editor harness 89/89, Publisher client PASS, Worker
  tests 580/580, offline red-team 0/8 exposed; accessibility audit clean on every platform page on both
  environments, pitch page down to 29 text-contrast findings (the OPEN plan).

## In flight at handoff

- A Codex worker (report `build/ce-work/report-U2d.md` in the feature worktree, gitignored) is fixing
  the runner: scroll-reveal opacity handling with reduced-motion emulation, CDP-based download capture,
  polling text assertions, and the `pitch-phone-nav-hidden` zoom case. Verify its tree, run
  `python3 -m pytest tools/tests/test_persona_journeys_contract.py tools/tests/test_render_persona_uat_record.py -q`
  and `node --test tools/tests/verify_persona_journeys.test.js`, then commit path-limited.
- A local `--bindings` run may have written run files under `build/uat/runs/` from the detached run
  worktree `~/worktrees/sonsteng-uat-run`; re-run after the runner lands.

## What remains (plan units)

1. U3: `node tools/verify_persona_journeys.js --base https://sonsteng-dev.damienriehl.com --env-label dev`,
   then the same with `--base https://legalpracticum.org --env-label prod`; all steps journeys should
   pass except the ten canaries. Triage any FAIL through the Chrome DevTools tooling before classifying.
2. U4/U5: `--bindings --env-label local` (harness and adopter legs), then `--bindings --env-label dev
   --only hostile-bot-gate` and the same for `prod`. The two Google-gated bindings stay BLOCKED unless a
   protected credential path is supplied through `SONSTENG_UAT_GOOGLE_CREDENTIALS_FILE`.
3. Render: `python3 tools/render_persona_uat_record.py`; commit `docs/uat/persona-uat-record.md`; add the
   orchestrator evidence above as rows or an evidence section.
4. U6: any new bounded FAIL → fix worktree, PR, full preflight (run
   `python3 tools/build_instructor_bundle.py` and `node app/worker/scripts/bundle-editor-data.mjs` first
   in a fresh worktree or three Worker gates fail spuriously), merge.
5. U7: `bash deploy/deploy-dev.sh origin/main`, then the runbook for production, then the final full
   revalidation on both environments at the final SHA; append the deploy record.
6. U8: summary section in the record, closeout handoff, PR for the feature branch (full preflight first),
   merge, deploy, and the comprehensive report Damien asked for (what was done, how to see it, open
   questions).

## Questions for Damien (report them; do not block)

- Pitch text contrast: choose Remedy A or B in the OPEN plan.
- `stash@{0}` on the old `codex/pitch-proof-u3-final` line holds an unlanded proof-summary rewording
  ("$5,562,480 faculty cost model") and `aria-live` on the comment counts; keep, land, or drop.
- Packets A–F remain human-gated exactly as before.

## Working rules the receiving session inherits

- Workers are Codex, at every tier; the orchestrator verifies every worker claim by re-running tests and
  inspecting the tree before committing. Dispatch through the Codex companion in the background with a
  per-job status loop; `agents/worker-wrapper.sh` is not used while the Cockpit freeze is on because it
  writes into the Cockpit repository.
- The orchestrator performs only credentialed actions (deploys, live runs, Cloudflare API reads) and
  planning documents itself.
- Nothing in this handoff or the record may contain a credential, one-time code, roster data, or private
  authored content.
