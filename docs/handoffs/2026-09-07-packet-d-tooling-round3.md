---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-07T20:10:00Z"
title: "Packet D tooling at round 3: verifier merged, CAS and queue proof in their third fix round, window still closed"
summary: "One of three Day Zero tooling units is merged; the other two are in a third fix round after independent reviews found real defects; the supervised migration window has not opened and Packets A and F remain queued behind it."
keywords: ["packet-d", "day-zero", "canonical-ref-cas", "queue-proof", "verify-materialized", "packet-a", "packet-f", "codex-workers", "supervised-window"]
cwd: "/home/damienriehl/Coding Projects/sonsteng-magnum-opus"
resume_focus: "Land the CAS and queue-proof units (round-3 fixes running, then review, then merge with a docs conflict), add the docs unit for OQ-8/10/11/12/13, re-rehearse, then run Packet D's supervised window; afterwards the Packet A automated UAT sweep and Packet F's rename."
repository: "damienriehl/sonsteng-magnum-opus"
branch: "main"
head: "b97e394"
---

# Packet D tooling, round 3 — handoff

Supersedes `docs/handoffs/2026-09-07-packet-d-in-flight.md` (same cycle, later state). That document's Ground truth,
Credentials, planned order, and Traps sections remain accurate — read it first; this one records only what changed.
Its private companion is `~/.local/state/ce-handoffs/sonsteng-magnum-opus/2026-09-07-packet-d-in-flight.md`
(machine-local, 0600) and holds every identifier and path.

## What changed since that handoff

- **`fix/dayzero-verify` MERGED** as PR #58 (`b97e394`): Phase-2 verification can now pass on a real candidate
  (only the traceability `git_base_sha` is excluded, compared with exact JSON types and duplicate-key rejection),
  `--print-recovery-ids` gives exact recovery coordinates without a candidate, and `--repo --print-operator-plan`
  proves candidate existence, fresh-clone cleanliness, exact first parent, and a one-commit range. Two reviews;
  the second found nothing.
- **`fix/dayzero-cas` (tip `c76ab67`) is in its third fix round.** Round-2 review confirmed all six round-1 findings
  closed and found four more: a remote `post-receive` hook can create another ref invisibly; final checks could
  certify a stale readback; `forward` still used `merge --ff-only`, which can overwrite a raced local main; and
  `core.fileMode=false` defeats exact cleanliness. Worker `sonsteng-fix-dayzero-cas-r3-20260907` is applying the
  reviewer's proposed fixes (full remote ref-map delta, validate-then-snapshot ordering, a three-argument
  `update-ref` CAS instead of a merge, forced `core.fileMode=true`).
- **`fix/dayzero-queues` (tip `1ce468c`) is in its third fix round.** Round-2 review found three P1s: an impossible
  frontier variant read as empty; operation-based publication work invisible to the proof; and a live-row race
  between the sequential GETs. Worker `sonsteng-fix-dayzero-queues-r3-20260907` is fixing the first two as proposed.
- **The race (finding 3) was decided by this orchestrator, not by Damien.** The reviewer's fix suggested a new
  read-only Worker endpoint that snapshots all three queues in one Durable Object call. That grows Packet D into a
  Worker change, so the narrower reading was taken instead: the proof is taken *inside* the exclusive window, so the
  receipt is fence-bound — the tool now requires the caller to assert the fence (apply timer verified stopped, a
  window-owner id), records first/last GET timestamps, and the runbook takes the proof twice, at window open and at
  window close. **If a future session prefers the endpoint, that decision is still open.**
- **Damien's guidance this session:** proceed autonomously on best-practice items; ask only true judgment or taste
  calls. He is on Remote Control, so the Cloudflare PROD token stays blank and the wrangler OAuth helper stands in
  (both recorded in the prior handoff and its companion).

## Current state, by maturity

- **Complete:** production at `8601b32`; DEV Worker and compose current; apply-daemon checkout current; the rehearsal,
  queue evidence, Pages rollback proof, and `--env=""` check for Packet D; the verifier tooling unit.
- **In progress:** the two tooling units above (fix workers running; each will need a fresh independent review, then a
  PR — both branches will conflict with `main` on `docs/day-zero-migration-operations.md`, so merge `origin/main` into
  each branch before opening its PR).
- **Not started:** the docs unit settling OQ-8 (Worker→Pages ordering with an explicit compatibility proof), OQ-10
  (which generated artifacts belong in the one migration commit), OQ-11 (evidence commit after the window), OQ-12
  (the pre-user lane is not Packet D's authority), OQ-13 (`--env=""` now confirmed); the re-rehearsal on the merged
  main; the supervised window itself; the Packet A automated UAT sweep and Damien's minimal human checklist; Packet F's
  rename to `legal-practicum`.

## Where to look

- `build/day-zero-operator-sequence.md` in the clean run worktree (machine-local, gitignored; copy in the Packet D
  evidence directory): the eleven-step operator sequence and OQ-1…15. Everything above traces to it.
- `~/worktrees/sonsteng-next-steps-work/build/review-dayzero-*-2026-09-07.md` (machine-local): every review round.
  The `-r2` files carry the findings the current fix workers are addressing.
- `docs/day-zero-migration-operations.md` — the runbook all three branches edit; the merge conflicts live here.
- Worker state is `agents/run/<id>/status.json` under the cockpit root. The watchdog wrote no `events.log` completion
  line for any worker this cycle, so read status files; silence is not a signal.

## Verification performed

Every worker claim in this cycle was re-run by the orchestrator before commit (focused suite plus the full
`tools/tests` suite, `git diff --check`), and no pass count was relayed from a worker's own report. The Pages rollback
proof produced one false PASS from comparing two empty strings; it was caught by a length guard on the next line and
re-run correctly. That failure mode — a comparison of two empty values reporting success — is worth guarding against
in the window's own checks.
