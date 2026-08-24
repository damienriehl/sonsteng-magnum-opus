# 21-day plan closeout — current handoff

Date: 2026-08-24 (updated; first issued 2026-08-23)
Supersedes: `2026-08-20-21-day-plan-completion-handoff.md` and the residual-action section of
`2026-08-21-operational-closeout-handoff.md`

## Outcome

All eleven CE plans in the July 30 through August 20 audit window remain accounted for. Every plan
unit is complete, superseded, or represented below by a prerequisite-bound queue item. No newly
unblocked autonomous implementation unit remains at this handoff.

Since the prior handoffs:

- U10 infrastructure is complete: `legalpracticum.org` is live on Cloudflare Pages,
  `edit.legalpracticum.org` is protected by Cloudflare Access, the old Access app is retired, and
  legacy public/editor hosts redirect with path and query preserved.
- Google U19 live streaming is complete on `gemini-2.5-flash`. The retired
  `gemini-2.0-flash` default was replaced consistently in runtime configuration, tests, and the
  published API contract.
- PR #23 merged the domain cutover as `9c8669782536abcd173e666b21445148727ef973`.
- PR #24 merged the Google provider repair as `f8d8dd47f29ec397e548fa375ee0f4ca5659d957`.
- PR #25 merged the durable closeout sheet and handoff as
  `443d932c9110823853d2b4fbca7d30fd4e6a0412`.
- PR #26 merged the atomic combined Day Zero date/identifier implementation as
  `8d898d9011320b484c85db023452d2cf7977c3e7`.
- PR #28 merged all autonomous packet-readiness behavioral units (U1-U7 and U9) as
  `d9ed7d1b67d698bf25e145d2373cf24e9f9ea564`. The trusted daemon checkout was
  fast-forwarded cleanly to that exact merge.
- The primary checkout is on this focused closeout branch from the PR #28 merged `main`.
- Production publication and the Day Zero corpus migration remain deliberately off/not run.

## Current evidence

- DEV Worker version: `c4d21de6-ea7d-454f-905a-d0203d800af0`.
- Google live smoke: normalized SSE, two deltas, one terminal event, non-empty output, normalized
  usage, and identical replay.
- OpenAI key validity: provider model-list endpoint HTTP 200. Generation is externally blocked by
  HTTP 429 `insufficient_quota` / `credit_balance_exhausted`.
- Anthropic live smoke: not run; no currently authorized active credential was available. Legacy
  credential-shaped files were not read or tested.
- Full PR #26 repository suite: 824 passed. The final exact-head rehearsal at
  `b54c8b3854d0dabde038530293b1e567df5f61be` passed governed verification, atomic write,
  generated build, build parity, strict Day Zero enforcement, and network preflight with
  `production_mutations: 0`.
- The rehearsed write converts 1,236 dates and rewrites 368 identifier occurrences across 167 files.
  It rejects missing governed roots, old-base residue, unclassified values, and proof mismatches.
- CE review found six implementation/test gaps; all were validated and fixed. GitHub review then
  found one additional legal-JSON-escape mismatch; it was fixed semantically, covered by regression
  tests, answered, and resolved before merge.
- PR #28 implements automatic dual strictness, committed-tree
  migration verification and compensation, GET-only Cloudflare pair inspection, read-only Publisher
  readiness, explicit protected assessment-UAT preparation, aggregate calibration analysis, read-only
  repository-rename inventory, and post-apply U18 consistency wiring.
- Formal correctness/security review found four issues and GitHub review found two more. All six were
  fixed with regression coverage before merge. Final validation recorded 950 Python tests plus 21,687
  subtests, all 45 Worker test files, both governed validators, and 21/21 full preflight gates passing.

## De-duplicated remaining queue

### Agent-executable only after an external prerequisite

1. **U16b live acceptance evidence** — automatic date-offset and identifier enforcement is
   implemented; observe it after the supervised Day Zero rewrite produces accepted sidecars and the
   representation marker changes.
2. **U18 live acceptance evidence** — the daemon hook and durable result states are implemented;
   inspect the first result after the Publisher workflow produces a real published revision and an
   accepted DEV batch.
3. **Source ingestion** — after the files and applicable permissions/provenance arrive.
4. **Combined production corpus migration** — implementation, committed-tree verification,
   compensation, and copied-corpus rehearsal are complete; live execution still waits for the
   scheduled freeze window. The repository rename follows only after that migration is accepted.

### Human, identity, account, or external operations

1. Complete the authenticated editor punctuation/restoration round-trip and assessment signer
   exercise at desktop and 390px.
2. Restore minimal OpenAI API credit or explicitly leave that provider untested.
3. Provide a current Anthropic key or explicitly authorize a credential-safe legacy-key status test;
   then revoke legacy credentials and separately authorize cleanup of known plaintext copies.
4. Complete the Publisher content judgments, exact-candidate authorization, supervised canary, and
   exact-pair restoration; then decide whether routine publication remains off or becomes enabled.
5. Schedule and execute the supervised combined Day Zero date/identifier production window after
   notifying John and proving all queues empty.
6. Finish chain-of-title confirmation, third-party permissions/exclusions, source delivery, John's
   editor pass, faculty calibration, and provider-terms review.
7. Replace John and Roger's temporary token paths with proven Access sign-ins and restored edits,
   retiring each token only after its named replacement succeeds.
8. Schedule the repository rename after the combined corpus migration is accepted.

Detailed, paste-back-ready instructions are in
`docs/decisions/2026-08-23-plan-closeout-decision-sheet.md`.

## Truthful closure state

- **Complete:** every unblocked repository implementation unit; domain infrastructure; Google live
  provider validation; combined Day Zero implementation and exact-head rehearsal; autonomous
  preparation for Packets A, C, D, E, and F; merge/integration and trusted-daemon synchronization
  through PR #28 at `d9ed7d1b67d698bf25e145d2373cf24e9f9ea564`.
- **Queued:** U16b and U18 live acceptance evidence, source ingestion, the supervised corpus
  migration, and the later repository rename, each behind its named prerequisite.
- **Human/external:** the six packets in the current Decision Sheet.
- **Not authorized or not run:** production publication, Day Zero corpus mutation, OpenAI paid
  generation, Anthropic credential testing, authenticated human actions, and the repository rename.

This handoff contains no secrets, one-time codes, personal roster data, or edited private content.
