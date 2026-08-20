# Assessment mapping and promotion-branch retirement

Date: 2026-08-20
Authority: Damien's submitted answers to Cockpit ask
`sonsteng-magnum-opus-2026-08-20-0908-plan-residual-decisions`

## Assessment instrument

For memo deliverables, the seven analytic memo headings are the scored dimensions. Each
heading receives the recording-confirmed integer score from 1 through 7. The default
competence threshold is 4, and a section below 6 is redo-eligible. Existing configurable
precedence remains instructor over school over default.

The seven headings are:

1. Governing law.
2. Strengths and weaknesses of both sides.
3. Issues.
4. Suggested solutions.
5. Theory and themes.
6. Elements to prevail.
7. Liabilities and remedies.

Existing matter-specific weighted criteria, totals, and `letter_grade_map` data remain
available only for non-memo and legacy exercise assessment. They do not determine, label,
or transform the memo's 1–7 section scores. A memo score of 4 must be presented as
competent, never as a letter grade.

This decision releases U8 through U13 of
`docs/plans/2026-08-17-1244-feat-outstanding-work-execution-plan.md`. The old 0–5 draft on
`feat/seven-point-assessment` remains rejected implementation evidence and is not a merge
source.

## Obsolete promotion contract

The automatic confidence/eligibility promotion contract and its Cockpit projection are
retired. The current product contract is the authenticated human Publisher ledger built by
`docs/plans/2026-08-09-001-feat-taxonomy-publisher-batches-plan.md` and its successor
granular/release plans. Porting the obsolete confidence model into that ledger is not
authorized.

### Final lineage before deletion

| Ref | Tip at retirement | Historical contents | Disposition |
|---|---|---|---|
| `feat/prod-editor-promotion` | `49492a8e1c1d2bded8ab4b0fdfe0bf3f666fb18b` | Automatic promotion ledger, candidate preparation, risk policy, preview, publication saga, lifecycle UI, config-off operations, rollout policy, hardening, and handoff | Delete local and `origin` refs; retain this final-tip identifier as lineage, without promising permanent object retention |
| `feat/cockpit-sonsteng-promotion-summary` | `555bf19e5150fcfd75b16355851b6ea1091f2f18` | The common obsolete promotion stack plus atomic attention projection, read-only attention endpoint, race analysis, bounded revision reads, and parked-work handoff | Delete its clean worktree, then local and `origin` refs; retain this final-tip identifier as lineage, without promising permanent object retention |

The reusable lessons and selective-port rationale already survive on current history in
`docs/evidence/2026-08-09-editor-publication-baseline.md` and the taxonomy/Publisher plan.
Deleting these refs removes active branch claims and may eventually allow unreachable Git
objects to be pruned. The recorded identifiers preserve the historical lineage claim, not
permanent object storage, and do not make that lineage part of the current product contract.
