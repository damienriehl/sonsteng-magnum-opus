# Decision Sheet — Resume, persona UAT, and repository hygiene

Date: September 2, 2026
Companion to `2026-08-23-plan-closeout-decision-sheet.md` (Packets A–F), which stays authoritative
for every human or external gate. This sheet adds the four decisions the September resume needs.
Purpose: one copy-friendly sheet Damien can answer from a phone while travelling. Every question
carries a recommendation; the agent proceeds on the recommendation when a question goes unanswered
and records that choice as provisional.

Filed here rather than through the Cockpit pipeline because `cockpit-freeze` is on and the Cockpit
repository must not receive new ask, sheet, or engine files until it is thawed.

## What the resume found (read-only, September 2)

- `origin/main`, the primary checkout, and the trusted daemon checkout agree at
  `4e57593f69ef452f08fe043c22919cf9e40d59f1` (PR #31). GitHub reports no open pull requests.
- Every handoff and CE plan from July 17 through August 23 is audited closed. The August 29 handoff
  states that no autonomous repository task is unblocked; the six packets each wait on a human,
  account, source-material, or supervised-window prerequisite.
- Live surfaces answer: `legalpracticum.org` and its `/platform/` return HTTP 200; the DEV site
  returns 200; the editor door redirects unauthenticated requests to Cloudflare Access; both Worker
  session endpoints return the expected Turnstile 403 on a bare request.
- Nine unmerged branches sit in worktrees under `~/worktrees/`, each 100–216 commits behind `main`,
  last touched August 6–17, and none carries a patch already equivalent to anything on `main`.
  Seven `workspace cleanup` stashes from August 20 remain. Seventy-five local branches are fully
  merged.
- The `sonsteng-apply` DEV timer is active; the production release timer is inactive and config-off,
  exactly as the Publisher lane requires.

## Q1. What "push to prod" means during this trip

**Why you are needed:** production changes only when a human Publisher authorizes an immutable
candidate (Packet C1) and the release executor is enabled. There is no agent-operable bypass; the
legacy direct-deploy script is a disabled tripwire by design.

Options:

- **A (recommended):** "prod" for this trip means merged `main` plus the automatic DEV deployment.
  Production publication stays queued behind your Packet C1 authorization when you are back at a
  desktop. Nothing on `legalpracticum.org` changes until then.
- **B:** you will perform C1 from the road at a stated time; the agent prepares the read-only
  readiness proof first and operates the canary and restoration drill while you observe.
- **C:** something else — one sentence.

## Q2. Have any Packet A–F prerequisites changed?

**Why you are needed:** the packets are blocked on facts only you hold. If nothing changed, the
agent skips them entirely and does not re-ask.

Options:

- **A (recommended assumption):** nothing changed. Skip Packets A–F; spend the trip on persona UAT
  and hygiene.
- **B:** OpenAI credit was added and/or a current Anthropic key now sits in protected machine
  storage. Run the credential-safe Packet B smokes (path only, never the key).
- **C:** another packet changed. Paste back using that packet's template from the August 23 sheet.

## Q3. Authority to clean stale branches, worktrees, and stashes

**Why you are needed:** deleting unmerged branches and stashes is destructive. Merged branches are
recoverable from `main` and the reflog and are not the question.

Options:

- **A (recommended):** delete the 75 merged local branches now. For each of the nine unmerged
  branches, prove its content is superseded on `main` (patch-level comparison, recorded in the
  closeout handoff), then remove its worktree, branch, and matching stash. Any branch with unique,
  still-valuable work is rebased onto `main` and shipped as its own PR or reported for your call.
- **B:** delete merged branches only; keep every unmerged branch, worktree, and stash for your own
  review.
- **C:** touch nothing; report the inventory only.

## Q4. Persona set and emphasis for user-acceptance testing

**Why you are needed:** this is taste. The agent will write personas, user stories, and run UAT
for all of them; your answer sets the weighting and any exclusions.

Proposed personas: prospective reader (dean, faculty, funder) on the pitch; student on the
platform, matters, packets, client-interview simulator, memo critique, hours log, and downloads;
instructor on rubrics, assessment signer review, calibration, and the instructor bundle; author-editor
(John) on the edit door, history, and revert; contributing editor (Roger) with RSH attribution;
admin-Publisher (Damien) on review, publish, release readiness, and operations; open-source adopter
self-hosting from the README; school administrator or ABA reader of the competency proposal and
cost-per-credit page; accessibility-dependent user (screen reader, keyboard-only, 390px, 200% zoom)
across every public surface; hostile actor probing the chat and edit surfaces.

Options:

- **A (recommended):** all ten personas, weighted student, author-editor, and prospective reader
  first. Access-authenticated rows run through the headless harnesses and are marked NOT RUN for
  the live human leg, exactly as the existing UAT matrix does.
- **B:** student-facing personas only this trip.
- **C:** editor and publisher personas only this trip.

## Standing rules the agent follows without asking

- Work happens on branches in worktrees under `~/worktrees/`, never on the daemon-owned `main`.
  Each change ships as a PR merged after the full preflight passes, then the branch is deleted and
  `main` is pushed explicitly.
- UAT failures are fixed through `ce-debug`; a failure large enough to need design goes to a new
  `ce-plan` document rather than an ad-hoc patch.
- No production publication, credential use, Day Zero corpus mutation, repository rename, or
  Cockpit repository write occurs during this trip.
- Nothing in this sheet or its answers should contain a credential, one-time code, roster data, or
  private authored content.

## Paste back

```text
Q1 prod meaning: A | B [time] | C [sentence]
Q2 packet change: A | B [OpenAI CREDIT ADDED / WAIVE; Anthropic path only] | C [packet + template]
Q3 cleanup authority: A | B | C
Q4 personas: A | B | C [exclusions or emphasis]
```
