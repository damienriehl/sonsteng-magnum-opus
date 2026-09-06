---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-05T16:38:14Z"
title: "Executing the 2026-09-03 decisions: two deploys done, five PRs held on review findings"
summary: "D1 and D6 are executed and verified live; five pull requests remain open because every one still carries at least one reviewed finding, and the red-team classifier has failed three rounds in a way that questions its approach."
keywords: ["decision-execution", "dev-worker-deploy", "persona-uat", "redteam-classifier", "open-pull-requests", "apply-daemon", "debrief-truncation"]
cwd: "/home/damienriehl/Coding Projects/sonsteng-magnum-opus"
resume_focus: "Decide whether the planted-fact checker keeps chasing phrasing or changes approach, then land or close the five open pull requests and run the two remaining authorized deploys."
repository: "damienriehl/sonsteng-magnum-opus"
repo_root_sha: "be839bfa39467199befa451a2d3f30f63432bab2"
branch: "main"
head: "90622d37d1de5a71b64f77717579039dcd0fb7aa"
---

# Executing the 2026-09-03 decisions

Written by the session that resumed `docs/handoffs/2026-09-02-next-steps-and-open-decisions.md`, put its seven
collected decisions to Damien, and executed what his answers unblocked. Damien is switching the orchestrator model,
so this document is the resume point. The private companion holding machine-local paths and deployment identifiers
is at `~/.local/state/ce-handoffs/sonsteng-magnum-opus/2026-09-05-decision-execution.md` (machine-local).

## Ground truth

- `origin/main` is `90622d3`. Two pull requests merged this cycle: #44, the accessibility conformance record, and
  #43, the production vars explanation plus the decision record.
- **Production is untouched.** It still serves `49e24f4`. Nothing in this cycle deployed to production.
- **DEV Worker was redeployed** on 2026-09-04 at `20129c0` with `RELEASE_SHA` set. Its release provenance answers
  204 with that SHA, where it had answered 503 with a null build key.
- Five pull requests are open: #45, #46, #47, #48, #49. **None is ready to merge.** Each carries at least one
  finding from an independent review.

## What Damien decided, and where each stands

The durable record is `docs/decisions/2026-09-03-next-steps-decisions.md`. Ten decisions were answered inline
across three days and written to the cockpit before any action. The cockpit is frozen, so those answers have not
folded into the board state; the repository record is the copy that does not depend on the thaw.

| Decision | Answer | State |
|---|---|---|
| D1 DEV Worker redeploy | Yes, after diff review and notice | Executed and verified 2026-09-04 |
| D2 production vars | Investigate, add only what production reads | Merged in #43. No var added; all six omissions are correct |
| D3 DEV clean URLs | Yes, add the nginx config | Written in #46, unmerged. DEV compose deploy still pending |
| D4 preflight journey gate | Yes, local leg only | Written in #46, unmerged |
| D5 AAA target sizes | Accept AA, revisit with redesign | Merged in #44 |
| D6 Google credential | Damien places the file, agent runs the legs | Executed. Both live legs ran |
| D7 revalidation driver | Promote with a contract test | Written in #45, unmerged |
| E1 debrief fix approach | Preserve finish reason, retry once on truncation | Written in #49, unmerged, two P1s fixed but unreviewed |
| E2 apply daemon | Refuse to deploy when behind main | Written in #48, unmerged, one P2 fixed but unreviewed |
| G1 red-team gate strictness | Record but do not fail the run | Written in #47, unmerged. See the open question below |

Two further deploys are authorized and not yet done, in this order and no other: the DEV compose deploy after #46
merges, then refreshing the apply daemon's own checkout after #48 merges. Refreshing that checkout before the
Worker deploy would let an editor revert front-run a reviewed deploy; that is why the order matters.

## The open question that most needs a decision

**The planted-fact checker in `app/worker/test/redteam.mjs` has failed three review rounds, and the third
reviewer's judgment is that the approach itself cannot work.** Its exact conclusion: the classifier cannot be made
reliably free of false passes against unrestricted model output by adding phrasing variants, because synonymy,
quotation, negation scope, irony, and cross-sentence coreference defeat pattern matching.

The evidence supports that. Round one read a denial as an adoption. Round two closed that but passed a reply that
hedged then admitted the fact. Round three still passes `I wasn't running. I absolutely was.` and
`I can't confirm I was running. I sprinted.` Each round fixed the named cases and left new ones.

This matters because a false pass here hides a real persona safety regression. The live leg currently reports
12 or 13 of 14 depending on the run, and the variance is the classifier, not the persona.

Plausible directions, as one decision rather than a menu to work through:

- **Change the probe instead of the checker.** Make the planted fact something whose adoption is mechanically
  detectable rather than something a sentence can imply. This trades naturalness for determinism.
- **Use a model judge for the ambiguous band only.** Better recall, at the cost of latency, spend, and
  non-determinism inside a safety check.
- **Keep the current three-outcome classifier and accept its recall limits**, treating it as a screen rather than
  a gate, with the human read Damien already chose in G1.

Damien answered G1 knowing only the first two rounds. The third round's conclusion is new information he has not
seen, and it may change that answer.

## Open pull requests and their live findings

Read each branch's review report before touching it. Reports are gitignored and machine-local, listed in the
private companion.

- **#45 `feat/final-revalidation-tool`** — promotes the UAT revalidation driver into `tools/`. Open: a P1 symlink
  swap race in the stale-marker cleanup, where `site/` is validated once but the later unlink goes through the
  pathname again; and a P2 where PID reuse can make a crash-left marker look live. Already fixed and verified: the
  untracked-file gate, the build stamp surviving to the local legs, and a swallowed restore failure.
- **#46 `feat/preflight-journey-gate-dev-clean-urls`** — DEV nginx clean URLs and preflight gate 22. Open: two P2s,
  an environment-resolved Node seam and a readiness deadline that is attempt-counted rather than elapsed-time
  bounded. Already fixed: the `PERSONA_JOURNEY_RUNNER` override is gone from production, and the probe timeout is
  no longer 50 milliseconds.
- **#47 `fix/redteam-harness-streaming`** — the streaming fix is solid and independently valuable: before it, the
  DEV red-team leg crashed on its first probe and had never once run. The classifier riding along with it carries
  three P1 false-pass paths. Consider splitting the streaming fix out and merging it alone.
- **#48 `fix/apply-daemon-stale-deploy-guard`** — refuses a deploy when the checkout is behind. A reviewed P2 was
  fixed but not re-reviewed: the guard checked the checked-out ref while the daemon deploys a branch taken from an
  environment variable, so it could validate one ref and deploy another.
- **#49 `fix/google-debrief-truncation`** — the debrief returns 502 on every Google attempt. Two P1 budget defects
  were fixed but not re-reviewed: a lost settle response could discard a paid completion and strand its
  reservation, and the token estimate could under-reserve past the cap.

## What was verified live, and what was not

- The DEV Worker deploy was verified after the fact: release provenance, both editor doors, both public alias
  redirects, the session gate still refusing a headless client, tokenless edit routes still 404, and the DEV
  browser journey leg at 44 of 44.
- Both previously blocked live legs ran. The student live provider leg passes. The hostile live red-team leg is
  13 of 14.
- The one remaining live failure is the debrief 502, which #49 addresses. **Its cause was inferred, never proven.**
  The reviewer confirmed the change fails closed if the diagnosis is wrong, and the new metadata will name the real
  subtype. It cannot be confirmed until #49 is deployed to DEV.

## Corrections this session made to the previous handoff's facts

- **The DEV Worker was last deployed 2026-08-23, not 2026-08-11.** The earlier handoff's finding 1 said three weeks
  stale; the real undeployed surface was four files and about seventy lines. That correction is now recorded in
  both `docs/decisions/2026-09-03-next-steps-decisions.md` and, visibly rather than silently, in the September 2
  handoff itself.
- **A daemon can redeploy the DEV Worker with no review.** `sonsteng-apply.timer` fires every two minutes from a
  checkout that was 39 commits behind and never fetches, and its revert path runs a bare wrangler deploy against
  the default environment. That is how the Worker reached its August 23 state. #48 addresses it.

## Traps this session hit, so the next one does not

- **The adopter journey legs require a pushed commit.** They clone the public repository at the current HEAD. Run
  them from a worktree whose commit exists on the remote, or all five fail with `not our ref` and look like a
  regression. Verified: they pass from `origin/main`.
- **Dispatch a review only after committing what it should review.** One full review round was wasted examining
  stale branch tips because fixes were verified in the worktrees but not yet committed.
- **A fresh worktree fails `test_scope_index.py`** until the site and instructor generators have run, because the
  editor map is generated and gitignored. Restore `site/platform/data/.build-stamp.json` afterward. This is the
  same family as the traps in `docs/solutions/uat/2026-09-02-browser-journeys-measure-the-wrong-thing.md`.
- **A worker's green test report is not evidence.** One reported a clean suite that failed in an independent run,
  because it had generated a prerequisite and then deleted it. The failure was benign; the lesson is not.

## Working rules this session inherited and kept

- Workers are Codex at every tier, dispatched through the cockpit's task file and wrapper so the watchdog holds the
  status contract. The orchestrator never implements, and verifies every worker claim by re-running the tests and
  reading the diff itself.
- Every decision for Damien is filed through the decision pipeline first, then asked inline through the question
  interface, with the recommended option first and the answers written down before any action follows from them.
- Production writes go through the runbook script under the permission Damien granted; DEV Worker writes use
  version upload and activation so the prior version id is always recorded as the rollback target.
