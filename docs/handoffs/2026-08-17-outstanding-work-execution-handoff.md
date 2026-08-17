# Handoff: executing the outstanding-work plan (2026-08-17)

Read this before touching anything. Written by the session that ran the 21-day completion
audit, answered twelve decisions with Damien, and dispatched the first wave of workers.

**Canonical plan:** `docs/plans/2026-08-17-1244-feat-outstanding-work-execution-plan.md`
**The audit behind it:** `docs/plans/2026-08-17-1154-chore-21-day-plan-completion-audit-plan.md`
**The twelve answers:** `briefs/qa-state.json`, ask
`sonsteng-magnum-opus-2026-08-17-1703-plan-completion-decisions` (12 of 12 answered)

---

## Read this first: three things that will bite you

1. **`tools/verify_pitch.py` does not exist on `main`.** It lives on
   `feat/legal-practicum-buildout`. Lane A invokes it in six places. Land that branch
   (U1) before starting any pitch work, or your first command fails.

2. **`validate_spine.py` fails *silent*, not loud, on Day Zero dates.** `parse_date`
   returns `None` and every date check is guarded, so it exits **clean while checking
   nothing**. Never read a green `validate_spine` as evidence during the corpus work —
   U16a exists to add a checked-date count, and it must land *before* U15.

3. **The seven-point instrument question is not what it looks like.** Damien settled it on
   2026-08-13 (the seven-point form is the memo template, *not* a rating scale). His
   2026-08-17 answer proposes amending that settlement. Do not treat Lane B as unblocked;
   U8 is genuinely open, and six units hang off it.

---

## Branch state at handoff

`main` is at `7d22ad6`. Nothing below is merged yet.

| Branch | Commits | What it carries | Action |
|---|---|---|---|
| `chore/audit-truthfulness-repairs` | 6 | Midstate naming fix (T12), pitch vocabulary sweep (T17), reconciled `docs/TODO.md`, the audit, this plan | Merge — U2 builds on its language-contract additions |
| `feat/legal-practicum-buildout` | 6 | `tools/verify_pitch.py`, the four strict xfails, `793447a` | **Merge first** — Lane A blocks on it |
| `plan/aug6-implementation-wave` | 23 | The only copy of the August wave plan | Land the plan doc; its *code* is superseded |
| `chore/dead-code-and-orphan-plans` | 1 | KTD10 deletion + three rescued plan docs | Merge |
| `feat/day-zero-harness` | 5 | Day-zero harness, converter, holdout list, anchor audit | Merge; then U14b reviews its output |
| `feat/weekly-hours-log` | 1 | Local weekly hours log (R10) | Merge after running AE6 |
| `feat/seven-point-assessment` | 1 | Wave assessment config | **HOLD — do not merge until U8 lands** |
| `feat/nine-part-audit` | 0 | Nothing yet | Re-dispatch (U21) |

### Why `feat/seven-point-assessment` is held

That worker was specced before the instrument question was settled, so whatever band scale
it encoded is a guess. U8's execution note is explicit: building on a guess means rebuilding
all of Lane B. A green test suite does not make a guessed scale correct.

### Worker branches are zero-commit-checkable

`git rev-list --count main..<branch>`. A zero-commit branch merges as a clean no-op and the
suite passes, so "tests are green" cannot distinguish delivered work from an empty branch.
Two workers already completed having produced nothing.

---

## What Damien decided, and what he did not

Eleven of twelve decisions closed. They are encoded as KTD1–KTD11 in the plan; read them
there rather than re-deriving. The ones that change how you work:

- **Assessment is layered, wave first.** The August wave is the base layer; John's panel
  requirements build on top. Not the other way round.
- **The pitch opens problem-first** — shipped provisionally, since John asked for the
  reverse. It is a paragraph move if he holds his position.
- **The corpus reaches production by one-off supervised direct deploy**, rehearsed on a
  branch first. This knowingly waives the Publisher lane for one migration.
- **Branches are not to be deleted.** Damien approved rescuing the orphaned plan documents,
  not deleting their branches. Do not infer the second from the first.

**Still open, and blocking Lane B:** Q1a–Q1c in the plan's Open Questions. Q1a is the real
one. Recommend re-checking the 2026-08-06 and 2026-08-12 call recordings before asking him
again — they hold the only evidence that separates the readings, and nobody has listened
to them.

---

## Suggested order

1. **U1** — reconcile and land the branches above. Nothing else is safe first.
2. **U21** (nine-part audit) and **U2** (advocates sweep + trusted-advisors line) — both
   independent, both cheap, no decisions outstanding.
3. **U3 → U4 → U5** — the pitch chain, strictly serial. Stop after U3's draft for Damien's
   approval of the nine teaser figures.
4. **U14b** — the 674-entry holdout review. This is Lane C's real long pole; start it early
   because it is 715 human judgements, not a scripted pass.
5. **U16a**, then **U15**, then **U16b** — in that order, for the silent-failure reason above.
6. Lane B (U8 → U13) only once Q1a is answered.

---

## Conventions this repo expects

- Work goes to Codex workers via `agents/worker-wrapper.sh`, each in its own worktree under
  `~/worktrees/`. Dispatch, then stop — the watchdog monitors, not you. Events arrive in
  `agents/events.log`.
- **Verify worker output yourself.** Of six workers dispatched, one produced nothing, one
  did the work correctly and exited without committing it, and two were still empty at
  handoff. Read the branch, not the status field.
- Every plan claim in this repo carries its evidence. An absence claim is a claim about
  where you looked — the production-release receipts live in operator state *outside* the
  repo, and two auditors wrongly reported them missing by searching only `docs/evidence/`.
- Full suite: `python3 -m pytest tools/tests -q`. Real-box gate: `bash tools/preflight.sh`.
  If a browser gate throws "frame got detached", check the box's Chromium process count
  before diagnosing a regression — hundreds of stale processes have accumulated before.

---

## Retire this handoff when

A session has resumed from it, completed U1 plus at least one full lane, and folded what it
learned into the plan or `docs/solutions/`. Then delete it and name what absorbed it in the
commit message.
