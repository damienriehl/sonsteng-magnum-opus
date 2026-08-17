---
title: "21-Day Plan Completion Audit - Plan"
type: chore
date: 2026-08-17
topic: 21-day-plan-completion-audit
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
audit_window: 2026-07-27 .. 2026-08-17
---

# 21-Day Plan Completion Audit - Plan

## Goal Capsule

- **Objective.** Establish, with evidence, which tasks from every plan authored in the
  21 days ending 2026-08-17 are finished, and place every unfinished task into exactly
  one of three queues: agent-autonomous, needs-Damien's-decision, or needs-human-action.
- **Authority.** The plans and decision records themselves govern intent. This document
  governs only the *status claim* about each unit, and every status claim carries its
  evidence.
- **Method.** Nine independent auditors, one per plan family, each dispatched into a
  fresh context and required to verify against the codebase and git history rather than
  trust commit messages. Findings that two auditors disputed were re-verified directly.
- **Open blockers.** None for the audit itself. Twelve decisions and twelve human
  actions block downstream work; both are enumerated below.

---

## Product Contract

### Summary

Twelve plans fall inside the window — **not nine**. Three exist only on unmerged
branches and are invisible from `main`, which is why they had gone unaccounted. Across
all twelve, the great majority of authored work is genuinely done and test-covered;
the outstanding work concentrates almost entirely in one plan (Legal Practicum
buildout, 2 of 17 units) plus one never-built wave from an unmerged plan.

The audit also surfaced four problems that no plan document records, because each is a
gap *between* plans rather than inside one.

### Headline numbers

| Measure | Value |
|---|---|
| Plans in window | **12** (9 on `main`, 3 only on unmerged branches) |
| Implementation units audited | **~95** |
| Units DONE | **~66** |
| Units SUPERSEDED (deliberately) | **~7** |
| Units PARTIAL | **~6** |
| Units NOT DONE | **~16** |
| Test suite | **597 passed, 5 xfailed, 21,678 subtests, 0 failures** |

### Plan-by-plan status

| # | Plan | Status | Outstanding |
|---|---|---|---|
| 1 | `2026-07-27-001` Cloudflare Access door | 7/7 units DONE | One coding gap (per-page student-view link); PIN sign-in; token retirement |
| 2 | `2026-07-28-001` Editable coverage | SUPERSEDED — cleanly absorbed by #3 | Nothing dropped (verified item by item) |
| 3 | `2026-07-28-002` Word-like practicum editing | 10/10 units DONE | Inconsistency-checker daemon wiring (one decision) |
| 4 | `2026-08-04-001` Shared and computed text | 8/8 units DONE | None |
| 5 | `2026-08-04-002` Platform visual redesign | 5/5 units DONE | Two live browser gates inconclusive under box load |
| 6 | `2026-08-09-001` Taxonomy + Publisher batches | U1–U3 DONE, U4–U5 superseded, U6 partial | Live production enablement |
| 7 | `2026-08-10-001` Granular Publisher review | 8/8 units DONE | Inherited production-activation gate |
| 8 | `2026-08-10-002` Prose Publisher prod rollout | U1–U4 DONE, U5 partial, U6 NOT DONE | UAT evidence record; supervised canary |
| 9 | `2026-08-13-001` **Legal Practicum buildout** | **2/17 units DONE** | **Phases A, B, C, D — the bulk of all outstanding work** |
| 10 | `2026-08-05-001` PROD editor promotion *(branch only)* | SUPERSEDED by #6 | ~2,500 lines abandoned on three branches |
| 11 | `2026-08-05-002` Cockpit promotion summary *(branch only)* | Parked | Confirm parked or retire |
| 12 | `2026-08-06-001` August decision wave *(branch only)* | U1–U4 DONE, **U5/U6/U7 never built** | Hours log, 7-point assessment, credit proposal |

### The four gaps no plan records

- **G1. `fix/midstate-naming` was never merged.** Two commits from 2026-08-06 implement
  T12 — the "Midstate and Rogers" naming John corrected twice on the call. `main` still
  ships `Midstate University v. Pat Rogers & SPEU` at `site/index.html:444`, and
  `Midstate v. Rogers` at `:474` and `:564`. The branch merges cleanly except for one
  trivial ordering conflict in `tools/preflight.sh`.

- **G2. The hand-authored pitch page is outside the language contract.**
  `tools/tests/test_platform_language_contract.py` binds its scan root to
  `build_fresh_site()` — the *generated* platform tree — so `site/index.html` is never
  scanned. This single gap is why "advocates" (T04, 4 spots), the Midstate captions
  (T12, 3 spots), and "grading/graded" (T17, 8+ spots) all survive on the most
  public-facing page in the repo while the generated platform stays clean. It is the
  same class of gap that `tools/verify_pitch.py` (U16) was built to close.

- **G3. Wave 3 of the August decision wave was never built.** `app/hours/`,
  `data/schemas/weekly-hours-log.schema.json`, `data/schemas/assessment-config.schema.json`,
  and `docs/proposals/competency-based-credit.md` are all absent from `main`. These are
  T21, T23, and T22. The plan that specifies them is itself unmerged, so nothing on
  `main` records the obligation.

- **G4. `app/worker/src/editor-diff.js` is dead code.** 284 lines, fully unit-tested,
  imported nowhere outside its own test. The live atomic-diff engine is the Python
  `build_review_revisions` in `tools/apply_suggestions.py`, called from three production
  paths.

### One correction worth recording

Two auditors reported that **all five** production-activation evidence artifacts were
absent, having searched only `docs/evidence/`. That was wrong. The bootstrap and
restoration drill completed on 2026-08-11 and their receipts live in operator state
*outside* the repository:

- `~/.local/share/sonsteng-prod-release/bootstrap-receipts.jsonl` —
  `reactivation_verified: true`, `restoration_verified: true`
- `~/.local/state/sonsteng-prod-release/known-good-pairs.json`

Both the live Worker and the live Pages site currently serve
`x-release-sha: 6837ae91ece49c386500997c933fef81b265479c`, matching the receipt exactly.

What is genuinely missing is `authorized-manifest.json` — **no release candidate has
ever been prepared or authorized** — plus the filled-in UAT evidence record.
`SONSTENG_PROD_RELEASE_ENABLED=false` is therefore correct, not stalled by accident.

An absence claim is a claim about where you looked.

### Stale documents

| Document | Staleness |
|---|---|
| `RESUME.md` | 20 days stale; its opening STATE line asserts the Access door is unmerged and `EDIT_ACCESS_AUD` empty. Both false. First thing a cold session reads. |
| `docs/TODO.md` | Predates its own controlling decision record by three days. Seven items marked open are done. |
| `2026-08-13-001` handoff | Says the branch was never pushed; `origin/feat/legal-practicum-buildout` exists. |
| `2026-07-27-001` plan tail | Still says U2 is blocked; resolved the same day in `7b84be4`. |
| `2026-08-04` page-copy handoff | Describes a blocker that plan #4 resolved. |

---

## Queue A — Agent-autonomous

No decision required. Ordered by value per unit of effort.

| ID | Work | Source | Effort |
|---|---|---|---|
| A1 | Repair `docs/TODO.md`: close T05, T07, T08, T16, T18, T19, T26; amend T06, T11, T21, T23 | G3, staleness | S |
| A2 | Rewrite the stale `RESUME.md` STATE line | staleness | S |
| A3 | Close G2: add `site/index.html` to the language contract's scan scope | G2 | S |
| A4 | Sweep "grading/graded" from `site/index.html` (8+ spots, locked vocabulary) | T17 | S |
| A5 | Land the T12 Midstate naming fix (3 spots + `master-outline.md`) | G1 | S |
| A6 | Access-door U6: per-page student-view link in the editor chrome, plus its test | plan 1 | S |
| A7 | Re-run `verify_chat_critique.js` on a quiet box to settle plan 5's two gates | plan 5 | S |
| A8 | Legal Practicum U15: `tools/audit_nine_parts.py`, report-only, zero dependencies | plan 9 | M |
| A9 | Legal Practicum U6 + U7: day-zero equivalence harness and converter — produces the holdout list and anchor audit a human must review *before* U8 can be scheduled | plan 9 | L |
| A10 | Legal Practicum U11: draft 42 band descriptors (7 headings × 6 bands) — John cannot start his edit pass until a draft exists | plan 9 | L |
| A11 | Legal Practicum U2: draft the condensed pitch, then stop for approval of the nine teaser figures | plan 9 | L |
| A12 | Legal Practicum U4: de-name body prose — **must include `CONTENT-LICENSE.md`**, which the plan's file list omits, or two strict xfails cannot clear | plan 9 | M |
| A13 | Legal Practicum U12→U13→U14→U17: the Worker panel chain, serial, autonomous up to calibration | plan 9 | L |
| A14 | Wire `verify_pitch.py` into `preflight.sh` — safe only after A12 | plan 9 | S |
| A15 | Prod rollout U5: run the background UAT matrix and fill the evidence record | plan 8 | M |

### The xfail contract

Five strict `xfail` markers in `tools/tests/test_identity_rights_contract.py` convert to
hard failures the moment their assertion starts passing, so the unit that makes each one
true must delete the marker in the same commit.

| Line | Retired by | What must become true |
|---|---|---|
| 104 | U4 | Pitch carries `John O. Sonsteng · Damien Riehl · Roger S. Haydock` |
| 111 | U4 | `verify_pitch` returns zero author-surname-in-body violations |
| 136 | U10 | `README.md` uses `legalpracticum.org` |
| 166 | U4 | `CONTENT-LICENSE.md` no longer excludes `data/midstate/` |
| 173 | U4 | `CONTENT-LICENSE.md` carries the new byline |

---

## Queue B — Needs Damien's decision

Twelve decisions. Each carries a recommendation.

| ID | Question | Recommendation |
|---|---|---|
| D1 | Should the inconsistency checker run automatically after every accepted batch? | Yes — wire `--since` into the apply-daemon tick |
| D2 | Should the pitch open with the problem statement or with the Midstate demonstration? | Ship problem-first provisionally; it is a paragraph move if John holds his position |
| D3 | Which faculty pay model should the cost-per-credit page default to? | Flat per-exercise stipend, per the plan — the page ships a switch either way |
| D4 | What is the production route for a bulk `data/` rewrite, given the Publisher lane is prose-only? | One-off supervised direct deploy, with the abort sequence rehearsed on a branch first |
| D5 | Adopt John's "trusted advisors" line now? | Yes — ship his exact wording |
| D6 | Sweep the remaining "advocates" (4 spots)? | Yes |
| D7 | Does the memo's 7-heading template replace or sit alongside the 40 point-weighted rubrics? | Sit alongside — memo template governs memo-type deliverables only |
| D8 | Delete the 284 dead lines of `editor-diff.js`? | Delete |
| D9 | Are the three PROD-editor-promotion branches (~2,500 lines) confirmed dead? | Confirm dead; record the lineage, then delete the branches |
| D10 | **Does Legal Practicum Phase C supersede the August wave's Wave 3?** Both build assessment configuration; they were authored nine days apart and neither references the other | Treat Phase C as authoritative and retire Wave 3's U6; keep U5 (hours log) and U7 (credit proposal), which Phase C does not cover |
| D11 | Run the first supervised canary now against the single backfilled revision, or accumulate more DEV edits first? | Run now — the canary is a pipeline proof, not a content checkpoint |
| D12 | Flip `STREAMING` to true, held since 2026-07-24? | Flip on DEV, validate across the three BYOK providers, then decide |

D10 is the one genuinely new conflict this audit found: two plans independently specify
an assessment-configuration surface, and no document reconciles them.

---

## Queue C — Needs human action

Nothing an agent can do. Grouped by who acts.

**Damien, at a keyboard:**
- C1. Add `legalpracticum.org` as a zone in the Cloudflare dashboard. The available
  credential is refused on `zone.create`. **Not blocked by Namecheap** — can happen now.
- C2. Log in to Namecheap and repoint nameservers at the pair Cloudflare assigns. No
  Namecheap credential exists on this machine. `dig` confirms the domain still sits on
  `dns1/dns2.registrar-servers.com` today.
- C3. Complete one real PIN sign-in through Cloudflare Access.
- C4. As Publisher: review the one backfilled prose revision and authorize the candidate.
  Only then can the supervised canary run.
- C5. Collect Roger's non-collaboration materials (T24).
- C6. Digitize or hand off John's CD originals (T11) — blocks T13, T14, T15.

**John:**
- C7. One written line recording the chain of title — he bought the Midstate materials
  from Anita. The entire CC-BY grant rests on his ownership (T09).
- C8. Clear the other lawyers' briefings on the disc for publication (T10).
- C9. Sign in through Access and do the editor pass (T27, UAT01).

**Roger:**
- C10. Sign in through Access, after which his `?t=` token can be retired.

**Third parties:**
- C11. Two faculty independently scoring 40–60 works, to establish the human-human
  calibration baseline U13 requires before any summative use.
- C12. Confirm provider terms exclude submitted content from training and bound
  retention (R20) — a procurement and legal check, not a code check.

### One ordering rule that must not be shortcut

When the domain is live: create the Access application for the new editor hostname and
**verify it enforces on an unauthenticated request before repointing `EDIT_ACCESS_HOST`**.
In this Worker the host gate — not the token's presence — is what makes the Access JWT
unforgeable. Repointing first opens a window in which anyone who can set the assertion
header reaches the editor.

Also still unverified: whether the available credential can *write* DNS and Access
records at all. Only zone creation was attempted, and it was refused. Find out before
scheduling the cutover, not during it.

---

## Recommended sequence

**This session (autonomous, no dependencies):** A1, A2, A3, A4, A5, A6 — the truthfulness
repairs and the three copy gaps G2 exposed. All small, all reversible, none touching
production.

**Next session (autonomous, still no decisions):** A8 (U15 — closes an entire phase),
A10 (U11 band anchors — unblocks John's edit pass, the long pole in Phase C), A9 (U6+U7 —
converts U8 from "gated and unspecified" into "gated with a reviewable artifact").

**Blocked on Queue B:** A11 and A12 need D2. The cost page needs D3. The corpus rewrite
needs D4. Phase C scope needs D10.

**Blocked on Queue C:** everything touching the domain, production publication, the
Midstate corpus, and John's editorial pass.

---

## Verification note

Every status claim above rests on file contents, git history, live endpoint responses, or
operator-state receipts — not on commit messages. Where two auditors disagreed
(`editor-diff.js` liveness; production evidence), the conflict was re-verified directly
and the correction recorded. Two of plan 5's browser gates remain **inconclusive rather
than failing**: the box carried 486 chrome/node processes at audit time, 23 of them more
than a day old, which is the most likely cause of the "frame got detached" signature.
A7 settles it.
