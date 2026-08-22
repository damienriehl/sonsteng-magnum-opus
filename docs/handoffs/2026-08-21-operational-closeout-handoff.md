# Operational closeout handoff

Date: 2026-08-21
Branch: `chore/post-merge-closeout`
Base: `56f392e74bb3a6bb096f650e17252584f5497477`

## Outcome

PR #19 merged to `main` as `56f392e74bb3a6bb096f650e17252584f5497477`. The trusted
daemon checkout was then fast-forwarded to that exact merge under the shared lock. Its protected
production environment was migrated in place while remaining mode 0600 and config-off; the
migration is idempotent. Ignored generated bundles were refreshed, parity passed, a config-off
production service tick exited before release work, and the normal apply service completed a
no-op tick. The production release timer remains disabled; production was not deployed or enabled.

The branch's focused implementation and review commits close the remaining repository defects:

- `3ad3a8d fix(day-zero): prove every governed conversion`
- `3939c48 test(stream): add credential-safe live smoke harness (U19)`
- `2a68ef1 feat(day-zero): add fail-closed migration rehearsal (U15)`
- `eaabcab fix(release): migrate legacy config-off environments`
- `2c7a75f docs(handoff): record operational closeout evidence`
- `bdf5003 fix(day-zero): centralize durable locator resolution`
- `afb4cf2 fix(review): harden migration and stream verification`
- `98c26af chore(build): refresh operational closeout artifacts`
- `00216f8 fix(day-zero): make exact-SHA rehearsal representative (U15)`
- `100d206 fix(review): preserve rehearsal candidate fidelity`

## Evidence

- Day Zero dry projection: 1,236 staged conversions, 1,236 proofs, 635 approved holdouts,
  zero unclassified dates, and a reconciled inventory of 1,242.
- Copied-corpus Day Zero write plus strict offset enforcement passed. The real corpus was not
  rewritten.
- Full Python tools suite after the post-merge review fixes: 789 passed, 1 expected xfail, and
  21,679 subtests.
- Full Worker suite with the hardened live-stream harness: passed; focused harness behavior: 15
  cases.
- Production-environment migration suite and neighbors: 41 passed.
- Real headless editor matrix: 89/89 passed at desktop, Large Type, and `390x844`.
- Publisher client harness: passed.
- Production Wrangler compile-only dry run: passed; production routes remain absent and feature
  controls remain off.
- Exact-SHA Day Zero rehearsal at `100d20688a41f6a1c260bd0e14e6d33da612bec2` passed all six
  phases: governed verification, governed write, generated build, parity, strict offset
  enforcement, and full repository preflight. The receipt reports `production_mutations: 0`.
- The rehearsal now uses a detached standalone clone with Git metadata but no local hardlinks or
  object alternates. This keeps uncommitted files out while allowing tracked-file and history
  preflight guards to run under their real contract.
- Day Zero offset fields and `date-offsets.json` sidecars are excluded from authored/editable fact
  surfaces. The transformed-corpus semantic baseline therefore remains unchanged.
- DEV Worker version: `5ae43990-84b2-4772-9cde-73bde49246f7`.
- Every registered Git worktree is clean. The obsolete promotion/Cockpit refs selected for
  retirement are absent locally and remotely.
- `legalpracticum.org` still delegates to Namecheap nameservers; `edit.legalpracticum.org` has no
  DNS record. No DNS or Access state changed.

## Autonomous follow-through after merge

The post-merge daemon synchronization, protected config migration, parity rebuild, config-off
production tick, normal apply tick, and exact-SHA Day Zero rehearsal are complete. The daemon
checkout is clean on merged `main`; its apply timer is enabled and its production release timer is
disabled. No autonomous post-merge operational action remains before the human/external gates
below.

The Day Zero production command remains deliberately unwired. Its complete rehearsal now passes,
but that does not authorize the supervised production window.

## Decision and human-action sheet

No remaining repository implementation choice is unanswered. These are the true human/external
gates, in prerequisite order:

1. **Provider validation.** Supply or authorize protected Anthropic, OpenAI, and Google DEV keys
   (plus the DEV session bypass when needed) and accept the small provider usage cost. Run
   `app/worker/test/live-stream-smoke.mjs` once per provider. Healthy evidence is normalized SSE,
   one terminal event, usage, and exact replay without credential reflection.
2. **Assessment review.** Use Damien's Access-authenticated session to create/read one real
   assessment audit at desktop and 390px, then submit one attributed override. This is a human
   identity action, not a code decision.
3. **Publisher judgment.** Review the reconciled revision and choose Accept, Reject, Ask question,
   or leave unanswered for each item; then authorize the exact immutable candidate. No service
   credential can make these choices.
4. **Production canary.** Supervise the first process-scoped canary and exact-pair restoration drill.
   After evidence passes, decide whether routine publication automation should be enabled. The
   recommendation is to keep routine mode off until the canary and recovery matrix is complete.
5. **Day Zero production window.** Notify John, prove the queue empty, capture exact live Pages and
   Worker IDs/provenance, run the supervised migration, record the new pair, restore the prior pair,
   and return to the intended pair. The operator sheet is generated by
   `tools/day_zero_migration.py --print-operator-plan`.
6. **Domain cutover.** Choose the final editor hostname; `edit.legalpracticum.org` is recommended.
   Move the zone to Cloudflare or otherwise provide the required DNS authority, create the Access
   application first, prove an unauthenticated redirect, and only then change Worker/DNS bindings.

U18 consistency-daemon verification follows a real published revision. Default-on Day Zero offset
enforcement follows the supervised corpus migration. Until those prerequisites exist, both remain
queued rather than falsely marked complete.

## Publication state

[PR #19](https://github.com/damienriehl/sonsteng-magnum-opus/pull/19) merged to `main` at
`56f392e74bb3a6bb096f650e17252584f5497477`. The post-merge rehearsal correction is committed on
`chore/post-merge-closeout` and is being prepared for integration. Production remains config-off.
