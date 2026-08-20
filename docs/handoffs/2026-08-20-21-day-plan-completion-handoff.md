# 21-day plan-completion handoff

Date: 2026-08-20
Audit window: 2026-07-30 00:00 CDT through 2026-08-20
Working branch: `chore/audit-truthfulness-repairs`

## Outcome

Every logical CE plan touched anywhere in Git during the window has been accounted for. Eleven plans qualify after treating the renamed August 4 shared-scalar plan as one artifact and excluding the curriculum's `client-interview-plan.md` product template.

The ten implementation plans contain 94 raw plan-unit rows. Those rows overlap heavily because later plans supersede or carry forward earlier work; they are not 94 distinct pieces of backlog. At the audit point, the conservative raw roll-up was:

- 50 complete
- 9 incomplete
- 10 decision-required
- 17 superseded
- 8 external/manual

This session completed the only unblocked behavior-bearing code unit, U19 multi-provider DEV streaming. Its live credentialed three-provider exercise remains an external validation step. The audit plan itself has 15 Queue A checks, of which 13 are now complete, one assessment check is blocked, and one obsolete descriptor check is superseded.

No plan task is now unaccounted for: each is complete, superseded, represented in the residual queue below, or depends on a question in the Decision Sheet.

## Plans audited

| Plan | Evidence-backed state | Remaining obligation |
|---|---|---|
| `2026-08-04-001-feat-shared-and-computed-text-plan.md` | 8/8 complete | None |
| `2026-08-04-002-feat-platform-visual-redesign-plan.md` | 5/5 complete | None; current browser recheck passed 28/28 on 2026-08-20 |
| `2026-08-05-001-feat-prod-editor-promotion-plan.md` | 8/8 superseded | None under the obsolete automatic/confidence contract |
| `2026-08-05-002-feat-cockpit-sonsteng-promotion-summary-plan.md` | Retired by the 2026-08-20 answer | Branch lineage recorded; obsolete local and remote refs authorized for deletion |
| `2026-08-06-001-feat-august-decision-wave-plan.md` | U1-U5 complete; U6 released; U7 partial | Score the seven memo headings 1–7, then bind the proposal and add the four missing competence measures |
| `2026-08-09-001-feat-taxonomy-publisher-batches-plan.md` | U1-U3/U5 complete; U4 superseded | U6 live protected Publisher/PROD journey |
| `2026-08-10-001-feat-granular-publisher-review-plan.md` | U1-U4/U7 complete; U6 superseded | Authenticated DEV smoke and the human/live portions of U8 |
| `2026-08-10-002-feat-prose-publisher-production-rollout-plan.md` | Code/runbooks complete | Protected revision review, exact-candidate authorization, supervised canary, recovery, and routine activation |
| `2026-08-13-001-feat-legal-practicum-buildout-plan.md` | Nine units complete; seven carried forward; domain cutover remains | Domain cutover through the August 17 successor plan |
| `2026-08-17-1244-feat-outstanding-work-execution-plan.md` | Core build largely complete | U8-U13, U15/U16b, U17/U18, live U19 verification, and external operational gates |
| `2026-08-17-1154-chore-21-day-plan-completion-audit-plan.md` | Audit queues reconciled | Assessment chain and the external action queue below |

## Canonical residual queue

This is the de-duplicated execution queue. Later successor units own the work where plans overlap.

### Agent-executable after decisions or prerequisites

1. **Assessment U8-U13.** Implement the seven-point assessment serially, scoring the seven analytic memo headings while retaining weighted criteria and letter maps only for non-memo and legacy exercises. Do not merge `d76c268`; the repository records that implementation as wrong.
2. **Proposal tail U7.** Bind the proposal to the final assessment export and add `time-to-first-competence`, `time-to-six`, `attempts-to-competence`, and task-level uncertainty.
3. **Date-offset enforcement U16b.** Run after the supervised U15 corpus rewrite has produced accepted sidecars.
4. **Consistency daemon U18.** Run after U17 yields a real published revision against which `editor_consistency` can be wired and verified.

### Decisions required

The Cockpit ask `sonsteng-magnum-opus-2026-08-20-0908-plan-residual-decisions` was answered on 2026-08-20:

1. Score the seven analytic memo headings 1–7; keep weighted criteria, totals, and letter maps only for non-memo and legacy exercises.
2. Retire the obsolete promotion-summary contract and delete its two local and remote branch refs after recording lineage.

The durable decision and branch lineage are recorded in `docs/decisions/2026-08-20-assessment-map-and-promotion-retirement.md`.

### Human or external operations

1. Exercise Anthropic, OpenAI, and Google streaming with real DEV credentials and confirm delta, terminal usage, errors, output, cost, and logging parity.
2. Complete the protected Publisher review and authenticated DEV smoke, authorize one exact candidate, run the supervised canary, prove restoration, then decide whether to enable routine production publication.
3. Run the Day Zero date-offset corpus migration/deploy/recovery at the keyboard. Current evidence remains zero offset sidecars and enforcement off.
4. Put Cloudflare Access in front of the new host before repointing the domain; then complete the registrar/nameserver work and verify the old hostname is gone from active configuration.
5. Complete the remaining named-user sign-ins and retire temporary access paths/tokens only after successful replacement access.
6. Finish source-material ingestion and clearances, the editor pass, faculty calibration, and provider-terms review.

The durable UAT matrix remains `docs/uat/editor-publisher-matrix.md`. Its Access actor, live edit, prepared/authorized release, provider IDs/provenance, canary, and recovery fields are explicitly NOT RUN; production therefore remains configuration-off.

## Work completed in this session

- `177f9ed feat(worker): stream all BYOK providers on DEV` — implements native Anthropic, OpenAI, and Google SSE normalization with regression tests; production remains disabled.
- `4890514 docs(status): reconcile the live work frontier` — repairs stale `docs/TODO.md` and `RESUME.md` claims.
- `adf3961 refactor(worker): centralize streaming adapters` — moves request construction and canonical usage parsing behind provider-owned adapters.
- `80603bf fix(review): finalize failed provider streams` — prevents provider errors, clean truncations, and transport aborts from becoming successful replay records or stranded in-flight turns; known partial usage is accounted and same-turn recovery is preserved.
- `DISPLAY=:0 XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.A5FGU3 node tools/verify_chat_critique.js` — 28/28 passed across desktop, wide, narrow, compact, and phone layouts in baseline and large type.

Focused verification recorded during the audit also includes:

- Shared/computed editor contracts: 128 passed, 2 skipped, 8,365 subtests.
- Current Python/build validation: 709 passed, 21,679 subtests, one expected domain-cutover xfail; pitch, build, validator, and parity checks passed.
- U19 review-fix verification: all 37 non-hanging Worker test files passed, with zero failures, cancellations, or skips; JavaScript syntax and `git diff --check` passed.
- The pre-existing `editor-norm-parity.test.js` harness remained pending/cancelled when run both in the suite and alone. It was intentionally excluded from the successful 37-file run and remains a test-harness residual, not a hidden full-suite pass.

## Next CE work order

1. Fold the two Decision Sheet answers.
2. Execute assessment U8-U13 with `ce-work`; verify the assessment and legacy compatibility before continuing.
3. Finish the dependent proposal U7 measures and export binding.
4. Schedule the human/external operations in prerequisite order: protected Publisher review and live DEV validation, supervised Publisher canary/recovery, then U18; supervised date migration, then U16b; Access-before-domain cutover.
5. Re-audit this handoff and the UAT matrix after each external gate. Retire this handoff only when every residual above is either evidenced complete or explicitly superseded by a newer committed plan.

## Workspace and publication state

Pre-existing user-owned paths were excluded throughout and must stay excluded:

- `site/platform/data/.build-stamp.json`
- `.codex/`
- `Failed to create stream fd: Operation not permitted/`

The Decision Sheet was generated from `../cockpit/briefs/qa/sonsteng-magnum-opus-2026-08-20-0908-plan-residual-decisions.json` and published at `https://dashboard.damienriehl.com/sonsteng-magnum-opus-2026-08-20-0908-plan-residual-decisions.html`. Cloudflare Access protects the page. Cockpit auto-sync committed the ask and `briefs/on-deck.json` update locally in `815913c9`; Cockpit `master` remains ahead of `origin/master`, so that source commit has not been pushed even though the generated remote page is available.

At handoff authoring time, the Sonsteng branch and its four focused implementation/status commits were local only and not merged or pushed. Local `main` itself was two commits ahead of `origin/main`, so the CE publish guard intentionally keeps this work local until the user asks to publish that pre-existing local stack.
