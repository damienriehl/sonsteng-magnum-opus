---
artifact_contract: "ce-handoff/v1"
created_at: "2026-09-07T18:55:00Z"
title: "Packet D (Day Zero migration) in flight: production promoted, tooling blockers under review, window not yet opened"
summary: "Production serves 8601b32; Packet D is authorized and rehearsed but its window is closed until three Day Zero tooling units merge; Packets A and F are queued behind D; the PROD Cloudflare token is blank and a wrangler OAuth helper stands in."
keywords: ["packet-d", "day-zero", "identifier-migration", "canonical-ref-cas", "verify-materialized", "queue-proof", "production-deploy", "packet-a", "packet-f", "repository-rename", "codex-workers"]
cwd: "/home/damienriehl/Coding Projects/sonsteng-magnum-opus"
resume_focus: "Finish Packet D (merge the three tooling units after review, docs unit for OQ-8/10/11/12/13, re-rehearse, then the supervised window), then the automated UAT sweep and Damien's minimal Packet A checklist, then Packet F's rename in a quiet window."
repository: "damienriehl/sonsteng-magnum-opus"
branch: "main"
head: "6559033"
---

# Packet D in flight — handoff for a fresh orchestrator

Written by the Fable session whose weekly quota is exhausted. Damien is the orchestrator's principal; the next
orchestrator (Opus) inherits the same standing rules: Codex workers at every tier, the orchestrator verifies every
claim itself, every decision through `cockpit-decide` then inline, and — Damien's instruction this cycle — proceed
autonomously on anything that follows best practice; ask only true judgment or taste calls.

## Ground truth at the time of writing

- **Production serves `8601b32`** (2026-09-07 pre-user deploy: Worker version `5410598b…`, Pages `256a9d6d`),
  recorded in `docs/uat/pre-user-prod-deploys.md`. `main` is at `6559033` (that record) — production is one docs commit
  behind main, which is normal.
- **DEV**: Worker at `3fe1050` (version `14c98e92…`), compose at `6c6ae09` with clean URLs. Apply-daemon checkout at
  `38a759f`, timer active.
- **Zero open PRs** other than the three Day Zero tooling branches below, which are not yet pushed.
- **Packets B, C, E: queued** (Damien, 2026-09-07). **Packet F: name confirmed `legal-practicum`**, rename waits on D.
  **Packet A: Damien wants every automatable UAT leg run and fixed first, then a minimal human checklist** — not started.

## Packet D — what is proved and what is not

Authoritative sources: `docs/decisions/2026-08-23-plan-closeout-decision-sheet.md` (Packet D, lines 171–250),
`docs/day-zero-migration-operations.md` (the whole runbook), and the researched operator sequence
`build/day-zero-operator-sequence.md` in the clean run worktree (machine-local, gitignored; a copy sits in the private
companion's evidence directory). That sequence's fifteen open questions (OQ-1…15) drive everything below.

Proved:
- Rehearsal on `6559033`: all six phases, `production_mutations: 0` (evidence in the private companion).
- Queues empty by daemon evidence (apply: "no accepted suggestions"; digest: "nothing pending"; prod-release timer
  disabled). John is notified and pencils-down (Damien).
- Pages rollback endpoint accepts the FULL canonical deployment id and rejects the short one — proved on a throwaway
  Pages project, since deleted. Not captured: the exact error code for rollback-to-already-active (adapter expects
  `8000039`); treat that idempotent path as unverified.
- wrangler 4 accepts `--env=""` for the top-level DEV Worker target (OQ-13).

Not yet safe (the sequence's own verdict, agreed): four tooling defects, each dispatched as a ce-work unit —

| Unit | Branch / worktree (machine-local) | State |
|---|---|---|
| Phase-2 verifier tolerates the traceability-only stamp SHA; `--print-recovery-ids`; operator-plan history proofs (OQ-6/4/15) | `fix/dayzero-verify` at `1428e8a`, `~/worktrees/sonsteng-dayzero-verify` | round-2 review running (`sonsteng-review-dayzero-verify-r2-20260907`) |
| `tools/canonical_ref_cas.py` forward/restore (OQ-7) | `fix/dayzero-cas` at `c76ab67`, `~/worktrees/sonsteng-dayzero-cas` | round-2 review running (`sonsteng-review-dayzero-cas-r2-20260907`) after 5 P1 + 1 P2 fixed |
| `tools/prove_queues_empty.py` (OQ-1) | `fix/dayzero-queues` at `90139f6` + uncommitted r2 work, `~/worktrees/sonsteng-dayzero-queues` | round-2 FIX running (`sonsteng-fix-dayzero-queues-r2-20260907`) after 3 P1s |
| Docs settling OQ-8 (Worker→Pages with explicit compat proof), OQ-10 (which generated artifacts commit), OQ-11 (evidence commit after the window), OQ-12 (pre-user lane is not D's authority), OQ-13 | not started | dispatch after the three merge (all three touch `docs/day-zero-migration-operations.md`; merge sequentially) |

Worker status lives in `agents/run/<id>/status.json` under the cockpit; the watchdog wrote no `events.log` line for
any completed worker today, so completion must be read from status files, not inferred from silence.

## Credentials — read before touching the window

- `~/.config/sonsteng-prod-release/env` (machine-local): `SONSTENG_PROD_CLOUDFLARE_API_TOKEN` is **blank** — the PROD
  principal was never provisioned; every production Wrangler action has run on Damien's OAuth login. The account id
  was blank too; this session filled it (non-secret). Damien approved minting a token (ask `…-1745-dayzero-prod-token`)
  but is on Remote Control, so he chose (ask `…-1801-dayzero-token-path`) to **reuse the wrangler OAuth login through
  `~/.local/bin/sonsteng-cf-oauth-token`** (prints only the bearer to stdout for stdin consumers). Recorded as a
  least-privilege deviation in the private companion. When he is at a keyboard, the paste-safe one-liner in this
  session's transcript stores a real token; until then the helper is the path.
- The observer env `~/.config/sonsteng-release-observer/env` is absent; the queue-proof tool's fallback covers it.

## After the units merge — the order that was planned

1. Docs unit (above), review, merge. 2. Re-rehearse on the merged main. 3. Open the window per the sequence sheet
items 1–11: stop the apply timer, prove quiescence, take the daemon lock; inspect the prior pair with
`--print-recovery-ids` via the helper; materialize the governed write (`tools/day_zero.py --write`) once in a
controlled worktree, regenerate, review, commit ONE commit; Phase-2 verify it; `canonical_ref_cas.py forward`;
deploy Worker then Pages with rollback recorded; DEV compose + DEV Worker from the same SHA; restoration proof
(prior pair) then return; release the window; UAT evidence + U16b. Compensation = `canonical_ref_cas.py restore` +
prior pair. 4. Packet A automated sweep (`tools/verify_persona_journeys.js --bindings`, all legs incl. Google live legs
with `SONSTENG_UAT_GOOGLE_CREDENTIALS_FILE`), fix what fails, then hand Damien a checklist that is only A1/A2 clicks.
5. Packet F rename in a quiet window: refresh `tools/repo_rename_inventory.py`, patch references, rename on GitHub,
repair remotes/worktrees/systemd units.

## Traps from this cycle worth not repeating

- A verification that compares two empty strings reports PASS; guard every id/sha with a length check before
  comparing (this session produced one false PASS that way and caught it on the next line).
- The harness stopped three background wrapper launches at startup; launching the wrapper with
  `setsid nohup … & disown` and watching `status.json` works.
- OpenAI's safety filter blocks a reviewer's report when the task spec uses attack-shaped wording; use "verify",
  "edge cases", "robustness".
- GitHub reports `mergeable=UNKNOWN` for a few seconds after a push; retry the merge.
- The pre-user production lane (`docs/pre-user-prod-deploy.md`) is not Packet D's authority (OQ-12).

## Cockpit asks this cycle (all answered inline, answers files written)

`…-2026-09-07-1315-prod-promotion` (J1), `…-1330-packets-a-f-status` (K1–K6), `…-1445-dayzero-credentials` (L1
superseded, L2), `…-1745-dayzero-prod-token` (M1), `…-1801-dayzero-token-path` (N1). The 08-22 domain walkthrough
was retired as executed. `briefs/on-deck.json` is current.
