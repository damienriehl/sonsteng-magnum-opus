---
title: "Outstanding Work Execution - Plan"
type: feat
date: 2026-08-17
topic: outstanding-work-execution
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
depth: deep
origin: docs/plans/2026-08-17-1154-chore-21-day-plan-completion-audit-plan.md
decisions: briefs/qa-state.json (ask sonsteng-magnum-opus-2026-08-17-1703-plan-completion-decisions, 12 of 12 answered)
---

# Outstanding Work Execution - Plan

**Target repo:** sonsteng-magnum-opus

---

## Goal Capsule

- **Objective.** Finish every outstanding task the 2026-08-17 completion audit found across
  the twelve plans in the 21-day window, under the twelve decisions Damien answered the
  same day.
- **Authority.** The answered decisions govern product intent and are recorded in
  `briefs/qa-state.json`. The audit governs status claims. Where a plan document
  contradicts either, say so on the owning unit rather than silently overriding.
- **Execution profile.** Six lanes. Lane A (pitch) and Lane E (small fixes) are
  independent and can run at any time. Lane B (assessment) is strictly sequential and
  starts blocked. Lane C (corpus) runs under a freeze protocol. Lane D (production)
  needs Damien acting as Publisher.
- **Stop conditions.** Stop and ask if: the day-zero round-trip proof fails on any file; a
  corpus rewrite would change a `{#b:}` block ID; any **summative** claim is made for the
  assessment panel before the two-faculty human-human baseline and the provider-terms check
  exist (this plan obtains neither, so a "calibration falls below baseline" guard could
  never fire — the enforceable guard is the claim, not the comparison); or the seven-point
  instrument question (U8) is still unconfirmed when Lane B reaches it.
- **Tail ownership.** This plan owns commits and PRs through to merge on `main`.
  Production publication and the domain cutover remain separate human acts.

---

## Product Contract

### Summary

The practicum ships under one consistent identity on a pitch page that reads impact-first
and states its assessment claims in John's locked vocabulary; students record their own
worked-versus-billable hours locally; assessment runs on a single explicit instrument
whose result is a 1–7 competency judgment rather than a letter grade; the matter corpus
carries relative Day Zero offsets so exercises stay teachable regardless of calendar
date; and prose edits reach production through the Publisher lane the repo already built.

### Key Decisions

Damien responded to all twelve on 2026-08-17 — four inline, eight from the sheet — but
**answered is not the same as settled**: eleven closed, and the twelfth (KTD12) returned a
provisional read that opened a new question rather than closing one. Lane B is blocked on
it. Full text in `briefs/qa-state.json` under ask
`sonsteng-magnum-opus-2026-08-17-1703-plan-completion-decisions`.

- **KTD1 — Assessment is layered, wave first.** (session-settled: user-directed — chosen
  over "Phase C governs": the August wave lands as the base layer and John's later panel
  requirements build on top of it, rather than replacing it.) Governs R6–R9.
- **KTD2 — The pitch opens problem-first.** (session-settled: user-directed — chosen over
  John's Midstate-first ask, shipped provisionally so John reacts to a live page; it is a
  paragraph move to reverse.) Governs R1.
- **KTD3 — The trusted-advisors line is John's exact wording.** (session-settled:
  user-directed.) The line is *"Training the next generation of lawyers as trusted
  advisors."* Governs R2.
- **KTD4 — Sweep all four remaining "advocates".** (session-settled: user-directed.)
  Governs R2.
- **KTD5 — The cost page defaults to a flat per-exercise stipend.** (session-settled:
  user-directed.) Faculty pay remains reopened upstream; the page ships a switch, so the
  default is reversible. Governs R4.
- **KTD6 — The corpus reaches production by one-off supervised direct deploy.**
  (session-settled: user-directed — chosen over extending the Publisher lane, which is a
  lane redesign that dwarfs the migration it would serve.) The abort sequence is
  rehearsed on a branch before the freeze window opens. Governs R11.
- **KTD7 — Run the first production canary now, on the single backfilled revision.**
  (session-settled: user-directed.) The canary is a pipeline proof, not a content
  checkpoint. Governs R12.
- **KTD8 — Streaming flips on DEV first, validated across the three BYOK providers.**
  (session-settled: user-directed.) Governs R14.
- **KTD9 — The inconsistency checker runs after every accepted batch.**
  (session-settled: user-directed.) Wire `--since` against the last published revision
  into the apply-daemon tick. Governs R13.
- **KTD10 — Delete the dead atomic-diff module.** (session-settled: user-directed.)
  Documentation references never block this; only a live runtime import would.
- **KTD11 — Land the orphaned plan documents; do not delete the branches.**
  (session-settled: user-approved — Damien approved landing the plans. Branch deletion
  was not separately confirmed and is explicitly **not** authorized by this plan.)
- **KTD12 — The seven-point instrument question is OPEN.** See R6 and U8 below. This is
  the one decision that is not settled, and Lane B is blocked on it.

### Actors

- **A1. Student** — reads the practicum, drafts memos against the seven analytic
  headings, records weekly worked and billable hours, receives assessment and feedback.
- **A2. John (author and editor)** — edits the practicum in his own words, edits the
  drafted band descriptors, holds authorship.
- **A3. Damien (publisher and operator)** — holds the Publisher role, authorizes
  production releases, owns credentials and the domain.
- **A4. Dean or faculty reader** — reads the pitch and the cost-per-credit page.
- **A5. Automated services** — build the site, validate contracts, run assessment, and
  execute authorized releases.

### Requirements

**Pitch and identity**

- R1. The pitch must open with a short problem statement and then the Midstate
  demonstration, with each major section collapsed behind a summary teaser, and must pass
  `tools/verify_pitch.py`.
- R2. Public copy must use "lawyers", not "advocates", and must carry John's
  trusted-advisors line verbatim.
- R3. Body prose must not name the authors; attribution lives in the byline and the
  licence, not in the sentences.
- R4. A cost-per-credit page must let a dean compare the practicum against their current
  model, defaulting to a flat per-exercise stipend and showing the ABA Standard 310(b)
  arithmetic for 225 hours.
- R5. Every hand-authored page outside `site/platform/` must be covered by the same
  content gates as generated pages.

**Assessment**

- R6. There must be exactly one explicit assessment instrument, and its relationship to
  the existing per-matter weighted rubrics must be stated in data, not inferred.
  **Open — see U8.**
- R7. An assessment result must be an integer 1–7 with visible provenance, where 4 reads
  as competent and is never presented as a letter grade.
- R8. Competence and redo thresholds must be configurable, resolving instructor > school
  > default, and must be labelled locally-supplied and unverified.
- R9. Assessment, debrief and critique must return to the requesting learner and must
  never route to an alumni assessor or recipient.
- R10. A student must be able to record weekly dated worked hours, billable hours and a
  per-deliverable contribution log locally, and export them without any of it leaving the
  browser.

**Corpus and release**

- R11. Matter dates must be expressible as offsets from a per-matter Day Zero, with a
  declared holdout list of facts that stay absolute, and no conversion may alter a
  durable block ID.
- R12. An authenticated human Publisher must review and authorize an immutable candidate
  before any production publication, and the exact prior pair must be restorable.
- R13. Content drift between published revisions must be detected automatically after
  every accepted batch.
- R14. Chat streaming must be provable on DEV across all three BYOK providers before it
  is considered for production.

### Acceptance Examples

- AE1 (R1, R2). A fresh load of the pitch opens on the problem, then Midstate; a reader
  finds "lawyers" and the trusted-advisors line, and no occurrence of "advocates".
  `tools/verify_pitch.py` exits 0.
- AE2 (R3). `tools/verify_pitch.py` reports zero author-surname-in-body violations, and
  the four strict xfails in `tools/tests/test_identity_rights_contract.py` that **U5** owns
  are gone rather than still marked. (Their `reason=` strings say "U4" because they cite
  the *Legal Practicum plan's* numbering, not this plan's.)
- AE3 (R5). A canary that inserts "graded" into `site/index.html` fails the language
  contract; the same word inside `docs/decisions/**` does not.
- AE4 (R7, R8). A result of 4 renders as competent with its provenance shown; a result of
  5 is redo-eligible; an instructor override supersedes a school override and is labelled
  unverified.
- AE5 (R9). A request carrying `alumni_assessor` is rejected by the Worker.
- AE6 (R10). A student records 3.5 worked and 2.0 billable hours, exports JSON and CSV,
  reloads the JSON, and no network request occurs.
- AE7 (R11). Converting the corpus and converting back yields byte-identical files, and a
  deliberately mutated fixture fails the round-trip proof.
- AE8 (R12). A candidate cannot publish without a recorded human authorization, and the
  prior Pages/Worker pair is restored exactly in a drill.

### Scope Boundaries

**In scope.** Everything the audit placed in Queue A, plus the units unblocked by the
twelve answered decisions.

**Deferred to follow-up work.**
- Deleting the three superseded promotion branches. Damien approved landing their plan
  documents only; branch deletion is a separate confirmation.
- The two named syllabi (T20) and the Midstate corpus build (T13–T15), which depend on
  John's disc reaching the repo.
- The competency-credit proposal's evidence claims, which need real consented data.

**Outside this plan.**
- The domain cutover and every credentialed act in Lane F — those are Damien's, tracked
  but not units.
- John's editorial pass and Roger's materials.
- Retiring `?t=` tokens, which waits on John and Roger signing in through Access.
- Confirming BYOK provider terms exclude submitted content from training and bound
  retention — a procurement and legal check, and a prerequisite of any summative use of
  the panel (see U11).

**One carried constraint that must not be lost with the cutover.** The domain work is out
of scope, but this rule travels with it and exists nowhere else once the origin audit is
retired: **when `legalpracticum.org` goes live, create the Access application for the new
editor hostname and verify it enforces on an unauthenticated request BEFORE repointing
`EDIT_ACCESS_HOST`.** In this Worker the host gate — not the token's presence — is what
makes the Access assertion unforgeable. Repointing first opens a window in which anyone who
can set the assertion header reaches the editor. Also still unverified: whether the
available credential can *write* DNS and Access records at all; only zone creation was
attempted, and it was refused.

---

## Planning Contract

### The open question that blocks Lane B

Damien asked whether John meant that **the 7-point scale IS the rubric**. Investigated
2026-08-17; here is the evidence and the answer.

**Three artifacts have been conflated, and all three are real.**

1. **The seven-point memo template** — seven *content sections* a four-page memo must
   contain: governing law; strengths and weaknesses of both sides; issues; suggested
   solutions; theory and themes; elements to prevail; liabilities and remedies. Quoted
   verbatim in `data/curriculum/m2.md`. This is a *drafting* structure.
2. **John's seven-point scale** — a 1–7 *rating*: 7s rare, 1 and 2 effectively never
   given, 3 is failure, 4 is average and therefore competent, under 6 may be redone.
   Recorded in `docs/TODO.md` T23 from the 2026-08-06 call.
3. **The per-matter rubrics** — weighted criteria with a `declared_total` and a
   `letter_grade_map` producing A / A− / B+ / B / C. In `data/matters/*/rubric.json`.

**This question is NOT undecided — it was settled, and the proposal below would amend
that settlement.** Two independent reviewers caught an earlier draft of this section
presenting it as never-decided. The record:

- `docs/decisions/2026-08-12-john-pitch-docket-outcomes.md` C3: *"The seven-point form is
  the memo template. **Settled 2026-08-13 by Damien** — no longer pending John; he
  confirmed the reading is correct."*
- The same record's open-question 2: *"The repo has two different seven-point things and
  **neither is a grading scale**… If John means a seven-point *rating scale* on the
  feedback form, that is **a third artifact we do not have**."*
- `docs/TODO.md` T23: Damien *"resolved it on 2026-08-13 as the memo template's seven
  analytic headings, **not a rating scale**."*

So the standing position is: **the seven-point form is the memo template, and no rating
scale is attached to it.**

**The proposal, stated honestly as an amendment.** The seven headings could be *what* gets
scored and John's 1–7 could be *how* each one is scored, making one composite instrument.
That is coherent, and it would explain John's *"the seven-point scale, which is the one
that works"* without needing a missing artifact. But it is a **new proposal that reverses
the 2026-08-13 settlement**, not a reading the record already supports — C3's "scores
against the seven analytic headings" clause is silent on the scoring dimension and must
not be cited as corroboration.

**A competing reading the amendment must beat.** `data/curriculum/m2.md` calls the memo
structure *"the Method's fixed seven-point template"* — which fully explains John's phrase
with no rating scale attached. An equally consistent alternative: the 1–7 is an *overall*
competence judgment (T23's "credit floor is an average of 4 of 7") while the headings are
scored on their own separate band scale. Nothing in the repo distinguishes these; the
evidence that would is in the 2026-08-06 and 2026-08-12 call recordings, which U8 should
re-check before Damien is asked.

**Three things a naive amendment gets wrong.**

- **The existing bands are 0–5, not "six bands" in the abstract.** The Legal Practicum
  plan's U11 says *"six band descriptors (0–5)"* and its memo scorecard schema rejects a
  score of 6. A 4 on 0–5 is near the top; a 4 on 1–7 is the middle. Choosing "six bands"
  without noticing this silently contradicts R7, AE4 and U13.
- **Dropping 1 and 2 from a 1–7 scale leaves five bands, not six.** An earlier draft
  offered that as the reconciliation; the arithmetic does not work, so the six-band figure
  has an independent origin and cannot be reconciled to John's scale by truncation.
- **`letter_grade_map` is not what John's caution targets.** It maps weighted *point
  totals* to grades (m01: A=202 … C=152 against a `declared_total` of 202) and never maps
  a 1–7 score to anything. John's warning is about how a **4 is presented in the new
  instrument** — a presentation problem in the thing being built, not an artifact of the
  existing map. Its shape is not uniform either: criteria counts run 4–6 per matter and
  `letter_grade_map` lengths run 4–6, never seven.

**A fourth artifact the reconciliation missed.** The Worker already ships two scorecard
schemas — `data/schemas/critique.scorecard.schema.json` and
`debrief.scorecard.schema.json` — both validated in `app/worker/src/validate.js` and
returned by `/critique` and `/debrief`. R6's "exactly one instrument" has to be provable
against those too, or U10's memo scorecard becomes a third.

**A factual correction, restated correctly.** The 2026-08-12 record says "40
point-weighted rubrics" while citing `data/matters/*/rubric.json`. Both halves are partly
right: there are **20 authored** rubrics under `data/matters/`, plus **20 tracked mirror
copies** under `site/platform/data/matters/` — **40 files repo-wide**. So "40" is a
repo-wide count with a mis-stated path, not a fabricated figure. Any change to rubric
semantics must reach both trees. An earlier draft of this plan, the audit, the Decision
Sheet and the published docket all called it a flat error; that was itself wrong.

### Implementation Strategy

Six lanes. Land each unit as its own commit; open one PR per lane rather than one per unit.

Lane A (pitch) is strictly serial within itself because U2–U5 all rewrite
`site/index.html` and U5 sweeps against the final section set. Lane B is serial and starts
blocked on U8. Lanes C, D, E are independent of A and B.

Work continues to run through Codex workers dispatched from the cockpit with
`agents/worker-wrapper.sh`, each in its own worktree under `~/worktrees/`. The
orchestrator verifies artifacts and does not monitor; the watchdog escalates through
`agents/events.log`.

### Work already in flight

Six workers were dispatched on 2026-08-17 before this plan was written. A fresh session
must reconcile these before starting new work — that is U1.

| Worker | Branch | State at plan-write |
|---|---|---|
| `sonsteng-hours-log` | `feat/weekly-hours-log` | Completed, 1 commit |
| `sonsteng-day-zero` | `feat/day-zero-harness` | Running, 2 commits |
| `sonsteng-nine-part-audit` | `feat/nine-part-audit` | Working, **0 commits — delivers nothing yet** |
| `sonsteng-assessment-config` | `feat/seven-point-assessment` | Starting, **0 commits — delivers nothing yet** |
| `sonsteng-cleanup` | `chore/dead-code-and-orphan-plans` | Completed as a no-op — spec was too strict |
| `sonsteng-cleanup-2` | same branch | Re-dispatched with a corrected spec |

**Three further branches must land in U1 or Lane A cannot start.** They are not worker
output, which is why an earlier draft omitted them — and omitting them broke the plan:

| Branch | Carries | Why it blocks |
|---|---|---|
| `feat/legal-practicum-buildout` | `tools/verify_pitch.py`, the four strict xfails, commit `793447a` | **`verify_pitch.py` does not exist on `main`.** U3, U4, U6, U7, AE1, AE2, R1, R5 and the Definition of Done all invoke it; every one fails at its first command without this branch. |
| `chore/audit-truthfulness-repairs` | commits `2584b55`, `cff323a`, `ad0966f` | The Midstate naming fix, the pitch vocabulary sweep and the reconciled `docs/TODO.md`. U2 builds directly on the language-contract additions in `cff323a`. |
| `plan/aug6-implementation-wave` | `docs/plans/2026-08-06-001-feat-august-decision-wave-plan.md` | The only copy of the plan U14 and the wave assessment config implement. Sources already assumes U1 lands it. |

Note also that `tools/tests/test_identity_rights_contract.py:29` — cited by U5 as the
pinned byline string — is that line **only on `feat/legal-practicum-buildout`**. On `main`
line 29 sits inside the MIT licence block.

The first cleanup worker stopped correctly: it was told to halt if anything outside the
test referenced the module, and two *plan documents* name it. Documentation is not a live
reference. The corrected spec stops only for a runtime import, bundler entry or non-test
code path, and decouples the orphaned-plan rescue from that gate.

---

## Implementation Units

### U1. Reconcile the in-flight worker branches

**Goal.** Establish what the dispatched workers actually produced, land what is good, and
land the three non-worker branches Lane A depends on.

**Requirements.** R10 (the hours log lands here, via `feat/weekly-hours-log`). Precondition
for every other unit.

**Dependencies.** None.

**Files.** No source changes of its own; merges into `main`.

**Approach.**
1. **Check each branch is actually ahead before trusting it:**
   `git rev-list --count main..<branch>`. A zero-commit branch merges as a clean no-op and
   the suite passes — so "the suite is green" cannot distinguish delivered work from an
   empty branch. Name every zero-commit branch as delivering nothing.
2. For each branch, read the commits and the worker's report.
3. Run the full suite on each branch before merging; merge the ones that pass.
4. **Hold any assessment-config commit from `feat/seven-point-assessment` unmerged until
   U8 lands.** That worker was specced before the instrument question was settled, so its
   band scale is a guess — and U8's execution note says building on a guess means
   rebuilding all of Lane B. Green tests do not make a guessed scale correct.
5. For `chore/dead-code-and-orphan-plans`, verify the two decisions actually landed rather
   than inferring them from a green suite: no runtime import, bundler entry or non-test
   code path still references `editor-diff.js` (KTD10), and each orphaned plan document is
   present on `main` (KTD11).
6. Land the three non-worker branches named above.
7. Do **not** delete any branch or worktree — KTD11 authorized the rescue, not the deletion.

**Verification.** `tools/verify_pitch.py` is present on `main` before Lane A begins. Every
branch is either merged, or has a named reason it is not, recorded in the handoff. AE6's
scenarios pass against the merged hours log.

**Test expectation:** none of its own — this is integration of other units' tests, plus the
AE6 run named in Verification.

### U2. Sweep "advocates" and land the trusted-advisors line

**Goal.** Make the public copy say what John asked it to say.

**Requirements.** R2. Governed by KTD3, KTD4.

**Dependencies.** None.

**Files.** `site/index.html`, `README.md`, `tools/tests/test_platform_language_contract.py`.

**Approach.**
1. Replace all four "advocates" uses: `site/index.html` lede, the exercise-library line,
   the open-source paragraph, and `README.md` line 5.
2. Place John's line verbatim: *"Training the next generation of lawyers as trusted
   advisors."* Do not paraphrase it.
3. Add an assertion to the language contract that the pitch contains no "advocate" stem,
   mirroring the "grading" assertion added on 2026-08-17.

**Patterns to follow.** The pitch-scope assertions added in commit `cff323a`.

**Test scenarios.**
- The pitch contains no "advocate"/"advocates" occurrence.
- The pitch contains John's trusted-advisors line exactly.
- A mutation canary inserting "advocates" into the pitch fails the contract.
- `README.md` carries no "advocates".

**Verification.** `python3 -m pytest tools/tests/test_platform_language_contract.py -q`
and a read of the rendered hero and lede.

### U3. Condense the pitch behind THE PROOF

**Goal.** Cut roughly 40% of the visible copy and collapse each section behind a summary teaser.

**Requirements.** R1. Legal Practicum plan U2.

**Dependencies.** U2.

**Files.** `site/index.html`.

**Approach.**
1. Native `<details>` per section, collapsed by default; print stylesheet force-expands.
2. Preserve the six base64 fonts and the reduced-motion handling.
3. The size ceiling applies to *authored payload* — total bytes minus base64 data URIs —
   per the correction in `793447a`. Do not re-litigate it against transfer weight.

**Execution note.** Draft all nine section teasers, then stop for Damien's approval before
U4 starts. The teaser figures are nine editorial picks; a late reversal invalidates U4 and U5.

**Test scenarios.**
- Every major section is wrapped in a collapsed `<details>`.
- The print stylesheet expands all sections.
- `tools/verify_pitch.py` reports no size violation against authored payload.
- Reduced-motion preferences are still respected.

**Verification.** `python3 tools/verify_pitch.py` and a visual read at 390px and desktop.

### U4. Reorder the pitch and build the cover cards

**Goal.** Problem statement first, then the Midstate demonstration, then 20 matter cover cards.

**Requirements.** R1. Governed by KTD2.

**Dependencies.** U3, and Damien's approval of U3's teasers.

**Files.** `site/index.html`, a new length-options file under `data/copy/`.

**Approach.**
1. Move the problem statement ahead of the Midstate section. This is KTD2 and is shipped
   provisionally — record on the unit that John asked for the reverse.
2. Build **20** cover cards. Read the matter list from `data/matters/manifest.json`, and
   each card's `skill_refs` from `data/matters/<slug>/matter.json` — **`skill_refs` is not
   in the manifest**, which carries only `id`, `slug`, `shape`, `tier`, `jurisdiction`,
   `caption`, `sides`, `client_id` and prose fields.
3. No length vocabulary exists under `data/copy/` — author one and propose the enumerated
   set for approval rather than inventing it silently.

**Test scenarios.**
- The problem section precedes the Midstate section in document order.
- All 20 matters have a cover card, with no duplicate IDs.
- Every card's skill references resolve against the skill catalogue.
- The length vocabulary validates against its schema.
- A card exposes hover and `:focus-visible` states and a keyboard-reachable click target.
- The 20-card grid holds at 390px without horizontal overflow.

**Verification.** `python3 tools/verify_pitch.py`, `python3 tools/build_site.py --check`.

### U5. De-name the body prose

**Goal.** Remove author surnames from body prose while preserving byline and licence attribution.

**Requirements.** R3. Retires four strict xfails.

**Dependencies.** U4 — the sweep runs against the final section set.

**Files.** `site/index.html`, `README.md`, **`CONTENT-LICENSE.md`**.

**Approach.**
1. Rewrite each surname occurrence to institutional-artifact attribution, occurrence by
   occurrence against `tools/verify_pitch.py`'s report.
2. Target byline is pinned by exact string at
   `tools/tests/test_identity_rights_contract.py:29` — note it drops the "with" before
   Haydock.
3. **Scope correction the plan's own file list omits:** two of the four xfails this unit
   retires assert on `CONTENT-LICENSE.md`. Without it in scope those xfails cannot clear
   and Phase A cannot be declared done.
4. Delete each `xfail` marker in the same commit that makes its assertion true — they are
   `strict=True`, so a passing xfail is a hard failure.

**Test scenarios.**
- `verify_pitch` reports zero author-surname-in-body violations.
- The pitch carries the exact new cover byline.
- `CONTENT-LICENSE.md` carries the new byline and no longer excludes `data/midstate/`.
- All four owned xfail markers are gone and the suite is green.

**Verification.** `python3 -m pytest tools/tests/test_identity_rights_contract.py -q` with
no xfails remaining for U5's four.

### U6. Cost-per-credit page

**Goal.** Let a dean compare the practicum against their current model.

**Requirements.** R4. Governed by KTD5.

**Dependencies.** U4.

**Files.** a new page under `site/`, nav link in `site/index.html`, copy under `data/copy/`.

**Approach.**
1. Default the pay model to a flat per-exercise stipend; ship the switch so load credit is
   one click away.
2. Publish 225 hours with the ABA Standard 310(b) arithmetic shown — that is already
   settled and is not reopened here.
3. Blank comparator cells, an inputs table with units and ranges, last-valid-value on
   invalid input, and no persistence.
4. `tools/verify_pitch.py` discovers any new page outside `site/platform/` automatically,
   so this page is gated for free.

**Test scenarios.**
- The page computes on load under the stipend default.
- Switching to load credit recomputes without a reload.
- An invalid input retains the last valid value rather than clearing the row.
- The 310(b) arithmetic renders and reconciles to 225 hours.
- `verify_pitch` picks the page up with no violations.

**Verification.** `python3 tools/verify_pitch.py`, plus a read at 390px and in print.

### U7. Wire the pitch gate into preflight

**Goal.** Make the pitch page's gate part of the standard build.

**Requirements.** R5.

**Dependencies.** U5 — wiring it earlier turns the build red.

**Files.** `tools/preflight.sh`.

**Approach.** Add `tools/verify_pitch.py` as a preflight gate beside the Midstate contract
gate landed in `2584b55`.

**Test scenarios.**
- Preflight fails when the pitch has a violation.
- Preflight passes on the clean pitch.

**Verification.** `bash tools/preflight.sh` reaches the new gate and passes.

### U8. Settle and encode the assessment instrument

**Goal.** Turn the seven-point question into one instrument definition in data.

**Requirements.** R6. **BLOCKED — KTD12.**

**Dependencies.** None, but blocks U9–U13.

**Files.** a new instrument schema under `data/schemas/`, an instrument file under
`data/curriculum/`, `data/spine-manifest.json`.

**Approach.**
1. Put the Planning Contract's finding above to Damien and get one answer: are the seven
   analytic headings the scored dimensions, with 1–7 as the scale applied to each?
2. Resolve the band-count mismatch — six bands per the Legal Practicum plan, or seven per
   John's scale.
3. Decide what happens to `letter_grade_map` for memo assessment: retired, hidden, or kept
   for non-memo exercises only.
4. Encode the answer once, versioned with a content hash, so nothing downstream infers it.

**Execution note.** Do not start U9 before this lands. Every downstream unit reads this
instrument, and building on a guess means rebuilding all of Lane B.

**Test scenarios.**
- The instrument validates against its schema.
- The band set is complete, non-overlapping and monotonic.
- The instrument's version hash changes when a descriptor changes.
- A memo-type deliverable resolves to the instrument; a non-memo exercise resolves per the
  decision in step 3.

**Verification.** Schema tests plus a read-back of the encoded instrument by a human.

### U9. Draft the band descriptors

**Goal.** Produce the full descriptor set so John has something to edit.

**Requirements.** R6, R7. Governed by KTD1 — layered on the wave's config, not beside it.

**Dependencies.** U8, and the wave assessment config from U1's `feat/seven-point-assessment`.

**Files.** the instrument file from U8, `data/curriculum/`.

**Approach.** Draft one descriptor per heading per band, grounded in the existing weighted
rubrics and the curriculum prose. AI drafts and John edits — that is the settled mechanism,
so a draft existing is the unblocking act.

**Test scenarios.**
- Every heading has a descriptor at every band, with no gaps.
- Descriptors are monotonic — each band strictly stronger than the one below.
- No descriptor mentions a letter grade.
- The instrument hash updates on edit.

**Verification.** Schema validation plus a legibility read by a human who has not read the code.

### U10. Memo scorecard schema and prompts

**Goal.** Give the evaluator a validated output shape.

**Requirements.** R7.

**Dependencies.** U9.

**Files.** a new scorecard schema under `data/schemas/`, `app/worker/prompts/`,
`app/worker/src/validate.js`, `tools/build_worker_personas.py`.

**Approach.** Enum-constrained scores with verbatim evidence spans per heading. The score
is derived in code from validated evidence — never invented by the model, and a
model-supplied overall score is ignored or rejected.

**Test scenarios.**
- Valid evaluator output validates.
- A missing heading fails closed.
- A duplicate heading fails closed.
- A model-supplied overall score is ignored rather than displayed.
- Evidence spans are verbatim from the submission.

**Verification.** Worker unit tests against real request shapes.

### U11. Panel orchestration and aggregation

**Goal.** Run several graders and reduce them to one defensible result.

**Requirements.** R7, R9.

**Dependencies.** U10.

**Files.** `app/worker/src/panel.js`, `byok.js`, `index.js`, `prompts.js`, plus tests.

**Approach.** Blind, shuffled inputs; median computed in code; adjudication only at spread
≥ 2 and constrained to the min–max range; credentials stripped before persistence.

**Execution note.** Two human prerequisites gate *summative* use, and neither is obtained
by this plan. Build and ship the panel for **formative** use only until both exist:
1. **Calibration** — two faculty independently scoring 40–60 works, for the human-human
   baseline.
2. **Provider terms** — a provider is eligible for summative grading only under terms that
   exclude submitted content from training and bound retention. The assessment record names
   the providers used, and a single-key run is labelled reduced-assurance. Without this,
   student memo text goes to three third-party providers under no stated condition.

**Test scenarios.**
- Median is computed in code and matches a hand-checked fixture.
- Spread ≥ 2 triggers adjudication; spread < 2 does not.
- Adjudication cannot return a value outside the observed min–max.
- Identical evidence repeats deterministically.
- No credential reaches persistence.
- A request naming an alumni assessor is rejected.

**Verification.** Worker integration tests plus a deterministic repeat run.

### U12. Assessment audit record and override capture

**Goal.** Make every result reconstructable, including human overrides.

**Requirements.** R7, R8.

**Dependencies.** U11.

**Files.** `app/worker/src/editor-store-core.js`, `editor-store.js`, plus tests.

**Approach.** Persist raw evidence, the derived result, the config provenance and any
override with its author, under a scope-gated read and a declared retention period. **Strip
every authorization header and BYOK key value from any stored request or response body
before persistence** — U11 carries that invariant, but this is the unit that actually
writes to storage, so it has to be enforced here too.

**Test scenarios.**
- A result is reconstructable from its stored evidence.
- An override records who made it and when.
- A scope-gated read is refused without the right scope.
- Retention expiry removes the record.
- A record written from a request carrying a sentinel BYOK key contains no occurrence of it.

**Verification.** Worker tests plus a round-trip reconstruction.

### U13. Signer review surface

**Goal.** Let a human review and sign an assessment.

**Requirements.** R7.

**Dependencies.** U12.

**Files.** `app/worker/src/assessment-view.js`, `app/editor/`, plus tests.

**Approach.** Show the result, its provenance, the evidence, and the override control; make
the 4-is-competent language explicit at the point of reading.

**Test scenarios.**
- The view renders result, provenance and evidence together.
- A 4 renders as competent, never as a letter grade.
- A 5 renders as redo-eligible.
- Keyboard and screen-reader coverage for the override control.

**Verification.** Browser check at the repo's usual viewports.

### U14. Competency-credit proposal

**Goal.** A written proposal for schools and the ABA, with an honest evidence threshold.

**Requirements.** Deferred evidence claims stay deferred.

**Dependencies.** The hours log from U1's `feat/weekly-hours-log`.

**Files.** `docs/proposals/competency-based-credit.md`.

**Approach.** Define the pseudonymous join between hours exports and assessment attempts,
specify the measures, include one hand-checkable synthetic example labelled illustrative,
and state the consent and sample-size requirements before any public claim.

**Test scenarios.**
- The document names every required measure and join key.
- The synthetic example recomputes by hand.
- Every output is labelled illustrative.
- No causal claim appears.

**Verification.** Documentation contract check plus a hand-recompute.

### U14b. Clear the holdout and anchor review

**Goal.** Turn 715 pending line-item judgments into a declared holdout list, so U15 has the
prerequisite it names.

**Requirements.** R11. Precondition for U15.

**Dependencies.** U1's `feat/day-zero-harness`.

**Files.** `data/day-zero-holdouts.json`, `data/day-zero-anchor-audit.json`.

**Approach.**
1. `data/day-zero-holdouts.json` currently reports **674 entries, every one marked
   `candidate_pending_human_review`**. `data/day-zero-anchor-audit.json` reports **41
   `attention_required`**. U15 names "a human-reviewed holdout list and anchor audit" as a
   dependency, and until this unit existed **no unit owned producing it** — so the
   realistic outcome was a stalled freeze window or a conversion run against unreviewed
   holdouts, which defeats R11.
2. Triage the 674 into declared holdouts versus convertible, in batches by matter.
3. Adjudicate the 41 attention-required anchor cases.
4. An agent can propose every classification; a human confirms. Ask Damien whether the
   subject-matter calls need John.

**Execution note.** This is the real long pole in Lane C — 715 judgments, not a scripted
pass. Budget for it rather than discovering it with the freeze window open.

**Test scenarios.**
- No entry remains `candidate_pending_human_review`.
- No anchor case remains `attention_required`.
- Every declared holdout carries a stated reason.
- The declared list round-trips through the harness unchanged.

**Verification.** Both files report zero pending entries, and a human has signed the
declared list.

### U15. The corpus rewrite

**Goal.** Convert the **1,156 convertible dates** of a 1,242-date inventory to Day Zero
offsets. (An earlier draft said "roughly 1,239", conflating the inventory with the
convertible subset — the harness reports both.)

**Requirements.** R11. Governed by KTD6.

**Dependencies.** U14b (the holdout review) and U16a (the day-aware checks), both below.
The harness and converter come from U1's `feat/day-zero-harness`. **Must run with
`SONSTENG_PROD_RELEASE_ENABLED=false` and the release timer disabled — U15 completes
before U17 step 4**, or the timer's ledger and this direct deploy contend for the same
production target.

**Files.** `data/matters/**`, `data/spine-manifest.json`, `site/platform/**`.

Not `data/curriculum/**` or `data/jurisdictions/**`: the converter walks only
`data/matters` and classifies dates elsewhere as *"outside data/matters and has no matter
open_date anchor"*. R11's anchor model is per-matter and those trees have no matter to
anchor to, so listing them promised a third of the scope the harness structurally cannot
deliver.

**Approach.**
1. **Rehearse the abort sequence on a branch first.** KTD6 chose the direct deploy
   specifically on that condition.
2. **Record the pre-deploy production state**: the exact Pages deployment ID, the Worker
   version ID, and the live `x-release-sha`.
3. Open the freeze window: stop `sonsteng-apply.timer`, hold `.locks/daemon.lock`.
   **Tell John before opening it** — he is an active editor and his saves will queue.
4. Convert, proving the round trip file by file.
5. **Rebuild the generated mirror**: `site/platform/data/**` is a verbatim mirror of
   `data/**`, and `tools/check_build_parity.py` is a preflight gate comparing the two.
   Skipping this turns the next preflight red after the window has already closed.
6. Deploy under the **Cloudflare PROD principal** defined in
   `docs/prod-release-operations.md` — Pages artifact upload plus named production Worker
   version activation only, with no DNS, Access-policy, account-admin or DEV mutation
   rights — executed by **Damien at the keyboard** as a supervised credentialed act.
   `deploy/deploy-prod.sh` is a disabled tripwire that exits 64; it is **not** the route.
7. **Read back** the Pages deployment ID, Worker version ID and `x-release-sha` and confirm
   they match the intended candidate.
8. **Record the new pair into the recovery registry**
   (`~/.local/state/sonsteng-prod-release/known-good-pairs.json`) before the window closes.
   Without this, R12's "the exact prior pair must be restorable" would restore production
   to *pre-corpus* content — the rollback story silently becomes a data-loss story.
9. **Unwind on every exit path, including abort**: restart `sonsteng-apply.timer` and
   release `.locks/daemon.lock`. Read the timer's enabled state back.

**Execution note.** Stop immediately if any block ID would change or any round trip fails.
This is the highest-risk unit in the plan and the only one that touches the corpus at scale.
R12's Publisher-authorization requirement is **knowingly waived here per KTD6, for this one
migration only**; steps 2, 7 and 8 are the compensating control.

**Test scenarios.**
- Round trip is byte-identical for every touched file.
- No `{#b:}` block ID changes.
- Every holdout date remains absolute.
- Dates under `data/curriculum/**` and `data/jurisdictions/**` are recorded as
  out-of-anchor holdouts rather than converted.
- A mutated fixture fails the proof.
- The abort sequence restores the pre-window state in the rehearsal.
- After a simulated abort, `sonsteng-apply.timer` is active and `.locks/daemon.lock` is free.
- The recorded prior pair is restorable by the same route.

**Verification.** The **day-aware** validator from U16a with an explicit assertion that the
date checks executed (a non-zero checked-date count — see U16a for why a clean exit proves
nothing), `python3 tools/check_build_parity.py`, the round-trip proof over the full touched
set, and a clean `git diff` review of a sampled matter.

### U16a. Day-aware checks, authored BEFORE the freeze window

**Goal.** Make the validator able to see the new representation at all — before U15 relies
on it.

**Requirements.** R11.

**Dependencies.** None. **Must land before U15.**

**Files.** `docs/research/validator-spec.md` first, then `tools/validate_spine.py`,
`tools/tests/test_validate_spine.py`.

**Approach.**
1. Add the numbered checks to the spec before the code — the module docstring and `--help`
   both cite the spec as the registry, and they currently claim "the 29 checks".
2. Put the day-aware checks behind a switch keyed on the offset representation, so they can
   land while the corpus is still absolute-dated.
3. **Emit a checked-date count.** This is the load-bearing part. `parse_date` returns
   `None` on failure and every date check is guarded (`if as_of and open_d`,
   `if d and d > as_of`) — so once dates become offsets the checks **stop firing instead of
   failing**, and `validate_spine.py` exits clean while validating nothing. B10's
   trust-ledger check additionally sorts by raw date string, so a running balance would be
   validated in the wrong order. A green exit is not evidence; a non-zero checked-date
   count is.

**Execution note.** An earlier draft made U16 depend on U15, assuming the checks would fail
loudly until the rewrite landed. They fail *silently* instead, which inverts the
dependency: U15 has no trustworthy gate available unless these checks land first.

**Test scenarios.**
- An offset-form date is parsed and checked rather than skipped.
- The checked-date count is non-zero on the converted fixture and on the absolute one.
- A trust ledger out of order fails under offset dates.
- The spec's check count matches the implementation's.

**Verification.** Both fixtures report a non-zero checked-date count.

### U16b. Enforce the converted representation

**Goal.** Reject anything that regresses the corpus.

**Requirements.** R11.

**Dependencies.** U15.

**Files.** `tools/validate_spine.py`, `tools/tests/test_validate_spine.py`.

**Approach.** Flip the U16a switch to enforcing once the corpus is converted.

**Test scenarios.**
- An un-offset date fails.
- A holdout date passes.
- A changed block ID fails.

**Verification.** `python3 tools/validate_spine.py` clean on the converted corpus, with a
non-zero checked-date count.

### U17. Production UAT evidence and the supervised canary

**Goal.** Publish one prose revision to production under full evidence.

**Requirements.** R12. Governed by KTD7.

**Dependencies.** Damien acting as Publisher.

**Files.** `docs/uat/editor-publisher-matrix.md` evidence fields; no source changes expected.

**Approach.**
1. Run the background UAT matrix and fill the evidence record — this part is autonomous.
2. Damien reviews the single backfilled revision and authorizes the candidate.
3. Run the supervised canary and the exact-pair recovery drill.
4. Enable production by the **runbook's routine-activation ordering** in
   `docs/prod-release-operations.md`, not by editing one flag: stop and disable the timer;
   prove no release process is running and no lease is held; compute the config digest with
   `tools/print_prod_release_config_digest.py` and bind
   `SONSTENG_PROD_EXPECTED_CONFIG_DIGEST` while stopped; set the enabled flag; read both
   values back; record operator, time, reason, code SHA and digest; enable the timer last
   and read its state back. On any failed readback, follow the runbook's compensating
   config-off procedure.

Step 3's canary is process-scoped: `SONSTENG_PROD_RELEASE_MODE=canary` bound to one
authorized `SONSTENG_PROD_CANARY_RELEASE_ID`, with the persisted environment staying
config-off and returned to config-off before the recovery drill.

**Execution note.** The legacy-pair **bootstrap** is done and receipted in operator state
outside the repo (2026-08-11) — do not re-run it, and do not read an empty `docs/evidence/`
as proof it never happened. But the recorded `restoration_verified: true` was captured
against a **single self-referential pair** (`candidate_sha` equals `source_sha`, both
`6837ae91…`, both events at the same timestamp). Restoring the prior pair after a *new*
revision publishes has never been exercised — so **step 3's exact-pair recovery drill is
still required**, and it is the only place AE8 is actually tested.

**Test scenarios.**
- The UAT matrix's evidence fields are populated with real IDs.
- Publication without a recorded authorization is refused.
- The canary publishes exactly the authorized candidate.
- The recovery drill restores the exact prior pair.

**Verification.** The live `x-release-sha` matches the authorized candidate, and the drill
record exists.

### U18. Inconsistency checker on the daemon tick

**Goal.** Detect content drift automatically.

**Requirements.** R13. Governed by KTD9.

**Dependencies.** U17 — it produces the first published revision this checker compares
against. (An earlier draft said "none", which would have shipped a permanently no-op gate.)

**Files.** `tools/direct_apply_daemon.py`, plus tests.

**Approach.**
1. Call `tools/editor_consistency.py` with `--since <last-published-rev>` after every
   accepted batch. The checker is proven at 16 of 16 with no false flags; only wiring is new.
2. **Give `--since` a source.** The daemon persists only `last_applied_ts`,
   `last_batch_id`, `last_run_ts` and `batch_reviewed` — no published revision — and the
   checker hard-refuses an unresolvable `--since`. Combined with "a checker failure does
   not abort the apply", every tick would log a refusal while the gate looked green.
   Persist the published revision SHA into the daemon's state at publish time and read it
   from there.

**Test scenarios.**
- An accepted batch triggers a check.
- A seeded inconsistency is reported.
- A clean batch reports nothing.
- A checker failure does not abort the apply.
- An absent or unresolvable published revision is logged and skipped **visibly**, never
  swallowed into a green result.

**Verification.** Daemon tests plus one seeded end-to-end run.

### U19. Streaming on DEV

**Goal.** Prove streaming across all three BYOK providers.

**Requirements.** R14. Governed by KTD8.

**Dependencies.** None.

**Files.** `app/worker/wrangler.jsonc` (DEV block only).

**Approach.** Flip `STREAMING` on DEV only. Validate all three providers. Report; do not
touch production.

**Test scenarios.**
- Each of the three providers streams a response on DEV.
- A provider error surfaces without hanging the client.
- Production config is unchanged.

**Verification.** A live DEV exercise per provider, with the results recorded.

### U21. Nine-part exercise conformance audit

**Goal.** A report-only audit of every matter's nine-part exercise structure.

**Requirements.** Queue A item A8 of the origin audit; Legal Practicum plan U15.

**Dependencies.** U1 (the `feat/nine-part-audit` branch, if it delivers).

**Files.** `tools/audit_nine_parts.py`, `tools/tests/test_audit_nine_parts.py`,
`data/matters/*/exercise/`.

**Approach.** Report-only. **No schema widening**, and no editorial changes to matter
content — anything needing judgement goes in the report. Inputs are all present: 20 matter
directories, 20 `exercise/` directories, and an `exercise.schema.json` whose `sections`
object has 8 required keys with `additionalProperties: false`.

**Execution note.** Zero dependencies on any other lane and no decisions outstanding — the
cheapest complete win in the plan. An earlier draft left this as a row in the in-flight
worker table with no unit, so the Definition of Done could not catch it.

**Test scenarios.**
- A conforming matter reports clean.
- Each distinct non-conformance class is detected.
- A deliberately broken fixture fails — a gate that cannot fail proves nothing.
- The report is deterministic across runs.
- No schema file is modified by the run.

**Verification.** The tool runs against all 20 matters and its summary is recorded.

### U20. Per-page student-view link

**Goal.** Let an editor check a change they just made on the public page.

**Requirements.** The one unbuilt piece of the Access door plan's U6.

**Dependencies.** None.

**Files.** `app/worker/src/editor-inject.js`, `app/worker/src/editor.js`,
`app/worker/test/editor-inject.test.js`.

The routing and upstream-URL logic lives in the Worker, not the client. An earlier draft
named `app/editor/editor-inject.js`, which does not exist — that would have pointed the
work at the wrong layer. The existing admin-page wiring is at `app/worker/src/editor.js`
(`studentViewUrl: env.EDIT_UPSTREAM`) feeding `renderStudentView` in `editor-admin.js`.

**Approach.** Map the current `/edit/<path>` to the same `<path>` under `EDIT_UPSTREAM` and
render it in the existing overlay rail. The admin page already has its own link; this is
the per-page placement the plan specified and nobody built.

**Test scenarios.**
- The link on `/edit/<path>` points at the same `<path>` upstream.
- The link is absent where no upstream page exists.
- It is keyboard reachable with a visible focus state.

**Verification.** The editor's headless harness.

---

## Verification Contract

Every unit:

1. Run the unit's own tests.
2. Run `python3 -m pytest tools/tests -q` — the full suite must stay green.
3. Run `python3 tools/build_site.py --check` for anything generator-owned.
4. Run the Worker suite for Worker changes.
5. Run `bash tools/preflight.sh` on the real box before declaring a lane done — note the
   box has carried hundreds of stale Chromium processes, which produces a
   "frame got detached" signature unrelated to the code. Check process count before
   diagnosing a browser gate failure as a regression.
6. Confirm a clean worktree and no credential material in the diff.

Lane C and Lane D additionally require their named drills to have run and been recorded.

---

## Definition of Done

- Every unit above is merged to `main`, or explicitly deferred with a reason.
- `docs/TODO.md` reflects reality — no finished item marked open, **and no stale content**:
  the 20-authored-plus-20-mirrored rubric count replaces the bare "40" in T23 and in
  `docs/decisions/2026-08-12-john-pitch-docket-outcomes.md`, so the next session does not
  re-copy it.
- `tools/verify_pitch.py` is present on `main` (U1) before any Lane A unit is called done.
- The four strict xfails U5 owns are gone, not merely still marked.
- `tools/verify_pitch.py` runs in preflight and passes.
- The assessment instrument exists in data, with its relationship to the weighted rubrics
  stated rather than inferred.
- Lane B is not declared done while U8 is unconfirmed.
- Production publication and the domain cutover are recorded as Damien's acts, complete or
  explicitly outstanding.

---

## Open Questions

Q1 was originally one bundled question; a single answer could not have unblocked Lane B,
because a partial reply leaves the reader unable to tell which half failed. Split into four,
each separately answerable:

- **Q1a (blocks U8, U9). Does the 2026-08-13 settlement stand?** That settlement says the
  seven-point form is the memo template and **not** a rating scale. The alternative is to
  amend it: John's 1–7 becomes an overlay scored against each of the seven headings.
  *Recommendation: re-check the 2026-08-06 and 2026-08-12 call recordings before answering
  — they contain the only evidence that distinguishes the readings, and neither the plan
  nor the audit has consulted them.*
- **Q1b (blocks U9). Which scale?** The existing artifact is **0–5** (six bands) and its
  scorecard schema rejects 6; R7, AE4 and U13 assume **1–7**. This is not a band count, it
  is a choice between adopting 1–7 and re-banding the 42 descriptors, or keeping 0–5 and
  amending R7, AE4 and U13. *Recommendation: adopt 1–7, since it is John's own scale and
  the one the product requirements already encode.*
- **Q1c (blocks U8). What are the default competence and redo thresholds?** John's recorded
  rule makes 4 competent *and* "anything under 6 may be redone" — so 4 and 5 are
  simultaneously competent and redo-eligible, while AE4 and U13 present them as disjoint.
  Whoever writes the code decides this unless it is answered. *Recommendation: confirm with
  John rather than inferring.*
- **Q1d (deferred, does not block).** What happens to `letter_grade_map` for non-memo
  exercises, and does `/critique` keep emitting a rubric-weighted `total`? Note this is
  **not** implied by John's caution — see the Planning Contract.
- **Q2 (deferred).** Do the three superseded promotion branches get deleted once their
  plan documents are on `main`? Damien approved the rescue, not the deletion.
- **Q3 (deferred).** Faculty pay remains reopened upstream; U6 ships a reversible default,
  not an answer.

---

## Sources

- `docs/plans/2026-08-17-1154-chore-21-day-plan-completion-audit-plan.md` — the audit.
- `briefs/qa-state.json`, ask `sonsteng-magnum-opus-2026-08-17-1703-plan-completion-decisions` — the twelve answers.
- `docs/plans/2026-08-13-001-feat-legal-practicum-buildout-plan.md` — Phases A–D.
- `docs/plans/2026-08-06-001-feat-august-decision-wave-plan.md` — on `plan/aug6-implementation-wave` until U1 lands it.
- `docs/decisions/2026-08-12-john-pitch-docket-outcomes.md` — on `feat/legal-practicum-buildout`.
- `data/curriculum/m2.md` — the seven analytic headings, verbatim.
