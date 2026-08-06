---
artifact_contract: "ce-handoff/v1"
created_at: "2026-08-06T16:45:00Z"
title: "Sonsteng: John's Aug 6 decisions reconciled against the recording, and the repo TODO/reminder system"
summary: "Eleven decisions from the 2026-08-06 John Sonsteng call, five of which the recording reversed relative to the same-day notes; plus docs/TODO.md and its ntfy reminder."
keywords: ["sonsteng", "legal-practicum", "john-sonsteng", "decision-record", "todo-reminder", "midstate-and-rogers", "cc-by", "systemd-timer"]
cwd: "sonsteng-magnum-opus"
resume_focus: "Work docs/TODO.md T01-T27; merge PR #7; do not trust the folded Cockpit ask"
repository: "sonsteng-magnum-opus"
repo_root_sha: "be839bfa3946"
branch: "feat/prod-editor-promotion"
---

# Handoff — John's decisions, and the TODO system that now tracks them

Written for a session that has never seen this repo and cannot ask me anything.
Commit messages already say what changed; this spends its words on what they cannot.

## What this session was

Damien met John Sonsteng (the professor whose life's work this repo consolidates) for
a 30-minute call on 2026-08-06. I built the decision sheet beforehand, took the
answers back, then received the **call transcript** afterward and reconciled everything
against it. Then I built a TODO system so the resulting work is tracked and reminded.

## The one thing that will mislead you

**The closed Cockpit ask contains answers that are now wrong. Do not read them as truth.**

`briefs/qa/sonsteng-magnum-opus-2026-08-06-0940-john-meeting-decisions-answers.json`
(in the *cockpit* repo, not this one) was written from Damien's copy-paste of the
sheet, folded by the board generator, and closed. The transcript arrived afterward and
**reversed five of those answers.** I deliberately left the folded file alone — it is
generator-owned after folding, and hand-editing it risks racing the 15-minute fold.

The authoritative record is **`docs/decisions/2026-08-06-john-meeting-outcomes.md`**,
which opens with a table of exactly what changed. Read that before acting on anything
decision-shaped. In short, the recording says:

| Item | The folded ask says (wrong) | Actually decided |
|---|---|---|
| Home | Mitchell Hamline adopts/hosts | **Damien hosts.** Mitchell may adopt; no hosting byline |
| Briefings | AI + a curated marquee human-speaker layer | **AI outright**; human recordings unplanned |
| Catalog | Materials ordered through the site | **No paywall.** Public repo, free download |
| Credit floor | 3 | **4 of 7.** Redo threshold is separately "under 6" |
| Alumni / board | Alumni assess; board approached in parallel | **Alumni do not assess** (AI does); board comes **after** the build |

## Decisions made in conversation that never reached a commit message

- **The title is "Legal Practicum," and John does not love it.** He said it "doesn't say
  enough" and then chose to stop shopping: "let's just put legal practicum and just stick
  with it." Shipping under it is not blocked, but a future rename is explicitly allowed.
  Do not treat the name as settled-with-enthusiasm.
- **Chain of title: John bought the Midstate materials from Anita.** He began to say
  "from Mitchell's" and corrected himself mid-sentence. This is the answer to the
  ownership question that had blocked `docs/decisions/2026-07-18-midstate-deferred.md`
  since July. It is still only spoken, not written — that is T09.
- **Roger Haydock is now genuinely contributing** (his books that are *not*
  collaborations with John), which he offered after the Aug 5 email. The byline moved
  from hypothetical to real: Sonsteng, Riehl, then "with" Haydock.
- **John's "trusted advisor" framing is the thing he actually cares about**, more than
  the advocates/lawyers fix he agreed to. "That's the only way we're going to make money,
  is if we're trusted advisors rather than the stuff that AI can do." Damien parked it
  for editor iteration rather than settling it live. That is T03, and it is not a
  cosmetic copy tweak — it is a positioning decision.
- **The board approach is sequenced on purpose and must not be front-run.** Finish the
  practicum and get Midstate in and right, *then* go to Greg Buck / Frank Harris / the
  dean with a finished thing. John: "a good way to negotiate without negotiating…
  pleasant rather than confrontational." An agent that helpfully drafts outreach early
  destroys the entire leverage model.
- **John is doing an editor pass with "the pencils"** (the platform's suggestion editor).
  He expects few comments. Suggestions may land in the editor review queue.

## Approaches tried and abandoned

- **Cherry-picking the hero copy fix directly onto `main` — impossible here.** `main` is
  checked out by the prod-promotion daemon's own worktree, so git refuses a second
  checkout and `git switch main` fails. The working path: `git branch <new> main`, then
  `git worktree add` for that new branch, cherry-pick inside it, push, remove the
  worktree. That produced PR #7. **You will hit this wall too.**
- **Quoting both `WorkingDirectory=` and `ExecStart=` in the systemd unit — wrong.** The
  repo path contains a space, and the two settings disagree: `ExecStart` is a command
  line split on whitespace and *must* be quoted; `WorkingDirectory` is a path taken
  literally and quoting it yields "path is not absolute."
- **`Documentation=file://<repo path>/docs/TODO.md` — rejected and silently dropped**,
  because a `file://` URL with a raw space is invalid. Points at the remote URL now.
- **An em dash in the ntfy `Title` header — fatal.** `urllib` encodes header values as
  latin-1, so "Legal Practicum — 25 open" raised `UnicodeEncodeError` inside `urlopen`
  and lost the whole notification. All three of these failed *after* a clean install, at
  run time. Full write-up:
  `docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md`.

## Current state

**Complete and verified**

- `docs/decisions/2026-08-06-john-meeting-outcomes.md` — the authoritative record.
- `docs/TODO.md` — 27 items, T01–T27, permanent ids, owner tags, origin tags.
- `tools/todo_report.py` + `tools/tests/test_todo_report.py` (38 tests, passing) +
  `tools/install-todo-timer.sh`. The timer is installed on the home box and was fired
  once end-to-end: service exited 0 and logged a real nudge.
- Hero copy fix in `site/index.html:270` — one word, `.em` claret-italic span preserved.

**Open, and precisely where it stops**

- **PR #7** (`fix/hero-copy-lawyers` → `main`) is *open, not merged*. The copy is not
  live. `deploy/deploy-prod.sh` is config-off by design and exits 64; publication is
  owned by the promotion coordinator. I ran no promotion and touched nothing in it.
- **26 of 27 TODO items are open.** Only T02 (the hero copy) is done.
- **This branch (`feat/prod-editor-promotion`) is 24 commits ahead of `main` and
  unmerged.** My docs/tooling commits ride it. The copy fix does not — that was the
  whole point of PR #7.

## Things I believe but did not verify

- **T01 (the 1 PM CD pickup) — status unknown.** I originally marked it `[x]` and
  reverted it to `[ ]` at the end of the session, because I never got confirmation it
  happened. The published decision artifact still renders it as done; `docs/TODO.md` is
  the source of truth and says open. **Ask Damien before assuming the disc is in hand** —
  T11 (ingest John's originals) depends on it entirely.
- **That merging PR #7 is sufficient to make the hero copy live.** I did not read the
  promotion coordinator's selection logic. Unverified.
- **That the ntfy nudge reached Damien's device.** I verified the POST returned and the
  service exited 0; delivery itself is unverified.
- **That `docs/decisions/cockpit-forms/*.json`** (two older sonsteng ask files) are
  inert history. I did not open them.

## The gotchas worth having on day one

1. **The arbitration naming rule is the most likely thing to be silently violated.**
   It is **"Midstate and Rogers"** — no "v.", because arbitrations take no versus. John
   corrected this twice on the call. Converted to a court posture it becomes
   **"Rogers v. Midstate"** *and* the remedy changes from reinstatement to **money
   damages**. Any agent generating matter files, filenames, or page titles will
   auto-correct this to "v." unless told not to. **Nothing enforces it.** The only test
   that mentions it (`test_the_naming_rule_survives`) merely asserts the TODO item still
   exists — it does not check the corpus. That is T12 and it is unbuilt.
2. **`docs/TODO.md`'s format is load-bearing.** `tools/todo_report.py` parses it, and
   exits 3 on an empty parse *on purpose* — a silent format break would otherwise read
   as "nothing open," which is the worst possible failure for a reminder. If you
   restructure that file, run `python3 -m pytest tools/tests/test_todo_report.py`.
   Task ids are permanent; never renumber.
3. **The repo path contains a space.** It broke the systemd unit three separate ways.
   Assume it will break the next thing that writes a config file or a shell command.
4. **Never put typographic punctuation in an HTTP header** in this repo's tooling.
   `header_safe()` in `tools/todo_report.py` exists for exactly this.
5. **This repo is outside the privilege perimeter** — it is open-source legal-education
   work, not client or personal-matter content, so third-party model routing is fine
   here. That is *not* true of every repo under this root.

## Where to look first

- `docs/decisions/2026-08-06-john-meeting-outcomes.md` — start here, read the change
  table at the top before anything else.
- `docs/TODO.md` — the work list; three items sit with John, three with Damien, the
  rest is build work.
- `docs/decisions/2026-07-18-midstate-deferred.md` — the July deferral this call
  unblocked; its "pivot path" section describes how John's originals should land.
- `docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md` —
  read before touching any timer or notification code here.
