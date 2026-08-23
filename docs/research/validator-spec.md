# `tools/validate_spine.py` — Validator Specification

*Produced 2026-07-17 by the deepen-plan pass (data-integrity review). This is the implementable checklist for the spine's only integrity gate at 20-parallel-author scale. Implement in the priority order at the bottom.*

## Day-one design decisions (bind the fleet before it launches)

1. **Schema versioning.** Every entity file carries `schema_version`; each schema declares `version`; top-level `data/spine-manifest.json` maps `{entity_type: schema_version}` + `spine_version`. Validator's first check: `entity.schema_version == manifest[entity_type]` — distinguishes "author error" from "authored against an older schema."
2. **Deterministic, namespace-partitioned IDs — collisions impossible by construction.**
   - Matter index locked in Phase 0: `m01`…`m20` + slug (e.g., `m06-noncompete-ny`). Each matter agent owns a disjoint prefix.
   - All matter-scoped IDs carry the matter prefix: `m06.per.baines`, `m06.te.0007`, `m06.ex.003`, `m06.wit.troy`, `m06.rub.c01`, `m06.fact.014`.
   - Global namespaces (`SK-*`, `TSK-*`, `FIRM-*`, jurisdictions) authored sequentially in Phases 0–2, never by the fleet.
   - Deterministic (index + type + zero-padded sequence), never random/UUID.
   - Validator enforces the pattern AND that a matter's files contain only its own prefix (catches copy-paste bleed between agents).

## Operating requirements (how it runs)

- **P0 — Per-matter isolation:** pass/fail per matter + per global module; machine-readable JSON report; non-zero exit on any ERROR. One broken matter must not block the other 19.
- **P0 — Severity:** `ERROR` (blocks ship) vs `WARN` (advisory). Referential, per-matter money, schema, persona-fact-fidelity = ERROR; stylistic/name-collision heuristics = WARN. **Matter↔firm aggregate reconciliation = WARN** (surfaced in the evidence pack, not a ship-blocker — plan decision after simplicity review).
- **P0 — Offline by default:** FOLIO IRI *format* checks offline; IRI *existence* via MCP only under `--online` with a local snapshot/cache (`folio-crosswalk.json` is the snapshot; the ship gate validates against the snapshot, not live MCP).
- **P1 — Partial-spine tolerance:** "target not yet authored" = WARN during the fleet run; "target malformed/dangling at ship" = ERROR.
- **P1 — Idempotent, deterministic (sorted) output.**
- **P1 — Two-pass:** pass 1 builds a typed global symbol table of every declared ID; pass 2 resolves references.

## A. Referential integrity — P0

1. Every ID globally unique within its namespace (report both files on collision).
2. Every cross-reference resolves (`skill_id`, `task_id`, `matter_id`, `persona_id`, `rubric_id`, `criterion_id`, `witness_id`, `exhibit_id`, `client_id`, `timekeeper_id`, `fact_ref`).
3. The D1 chain walks end-to-end: `skill → module → task → exercise → matter → rubric → persona`; report first broken link per matter.
4. Bidirectional listing: every persona file listed in `matter.json` and vice versa (ERROR); same for witnesses, rubric criteria; exhibits unreferenced = WARN.
5. `matter.jurisdiction` valid and exactly per the locked shape×state matrix.
6. Name-collision sweep across matters (WARN, human-review table); extra flag on discipline (m02/m12) + DWI (m05/m15) matters.

## B. Money invariants — P0 (per-matter = ERROR; firm aggregate = WARN)

7. Rate-card consistency: every time entry's rate matches the firm rate card or a declared matter-specific engagement rate.
8. Fee-type ↔ structure: `fee_type ∈ {hourly, contingency, flat, retainer}`, agrees with the firm book AND the billing structure — hourly: fees Σ = hours×rate; flat: fixed fee line (time entries tracked but don't drive the invoice); contingency: fee = % × recovery, arithmetic checks; retainer: matching trust deposit exists.
9. Time entries ↔ billing statement per invoice period; `fees + expenses − payments = balance_due` recomputed exactly.
10. Trust ledger: running balance never negative **at any point in the date-ordered sequence**; ending balance = stated; every firm disbursement corresponds to an issued invoice ≤ earned amount. (Ledgers are pedagogically clean — C7.)
11. Matter ↔ firm book reconciliation (WARN): recompute firm aggregates from the 20 matters (book totals, AR aging buckets, realization/collection) vs stated; per-matter `client_id`/rate/fee_type = firm book entry; ±$0.01 rounding tolerance.
12. Date sanity: entries within `[open_date, close_date | as_of_date]`; chronology consistent; invoice date ≥ latest billed entry; nothing after the simulation `as_of_date`.

## C. Persona invariants

13. **(P0)** Every disclosure item in all five tiers carries a `fact_ref` resolving to a fact anchor in that matter's `facts.md` (or structured facts block). Unanchored concealed/rapport-gated fact = ERROR (the debrief scorer depends on this mapping).
14. **(P0)** `knowledge_boundary` present; `color_topics` declared and disjoint from material facts (overlap = ERROR).
15. **(P1)** Rapport-gated items: `min_turns` positive int and/or `requires: [...]` drawn from the **closed trigger vocabulary** in `persona.schema.json`; unknown trigger token = ERROR.
16. **(P0)** Every `interviewable_by` role exists in `matter.json.sides`.
17. **(P1)** Represented + interviewable personas carry well-formed Rule 4.2 teaching-moment config.
18. **(P1)** Layperson heuristic: persona text containing statute/case citations = WARN for human review.

## D. Rubric invariants — P0

19. Σ(criterion points) = declared rubric total; sub-criteria sum to parents; where master-outline pins exercise totals (Tort 325, DWI 316.5, Arbitration 202, PR 207, Non-Compete 189, Real Estate 185.5) they must match.
20. Every `criterion.skill_id`/`task_id` resolves in the taxonomy.
21. Letter-graded events: grade→points map present, monotonic, consistent.
22. **(P2/WARN)** Course-level total vs the ~1,438-pt assessment chart if declared.

## E. Taxonomy invariants

23. **(P0)** Each skill/task: valid FOLIO IRI (`^https://folio\.openlegalstandard\.org/R[A-Za-z0-9]+$` or bare `R…`) **with** `mapping_confidence ∈ {exact, near, parent}` XOR `no_folio_equivalent: true`. Neither/both/placeholder = ERROR.
24. **(P0)** All 26 surveyed skills present, names exactly per `docs/research/skills-survey.md`.
25. **(P0)** Extension skills carry `extension: true` and are excluded from the 26-count.
26. **(P0)** No duplicate skill/task IDs.
27. **(P0)** Task hierarchy: every task → exactly one skill; every subtask → one task; no orphans.
28. **(P1)** `module ∈ {M1, M2, M3}`; exercise cross-refs resolve.

## F. Schema conformance — P0 (run first)

29. Every file validates against its JSON Schema; `schema_version` matches the manifest; a schema failure short-circuits semantic checks for that file.

## G. Day Zero date integrity — P0

30. Date checks resolve the additive `*_day_zero_offset` integer siblings emitted by
`tools/day_zero.py` against each matter's absolute `open_date`, while continuing to accept
absolute dates and declared fixed-fact holdouts. The validator reports how many dates it
actually checked; trust-ledger chronology uses resolved dates rather than raw date strings.
Enforcement that every convertible date carries its offset sibling is separately switchable
and remains disabled until the corpus conversion is complete.

## H. Canonical JSON-LD identifier base — P0

31. The combined U8 migration replaces the retired
`https://sonsteng.damienriehl.com/spine/` base with
`https://legalpracticum.org/spine/` in `data/spine-manifest.json` and every file under
`data/matters/`, `data/curriculum/`, `data/jurisdictions/`, `data/firm/`,
`data/taxonomy/`, and `data/schemas/`. Enforcement reports the
number of authoritative files and recognized base values inspected; rejects missing, empty,
or symlinked governed roots and manifest matters absent from the loaded corpus; rejects every
old-base occurrence; and requires the manifest to declare the settled new base. The enforcement
switch remains opt-in until the supervised combined migration is complete.

## Implementation priority

1. F + A1–A2 (symbol table core — catches collisions/dangling refs the moment the fleet starts)
2. B (money)
3. C13–C16, D19–D20, E23–E27
4. A3–A6, remaining C/D/E, aggregates

## Additional per-matter gate (depth floor — plan decision after simplicity review)

The Phase 0 style guide defines a countable **depth manifest** the validator enforces per matter (ERROR below floor): minimum counts for witness statements, documents/exhibits, personas (≥1 client persona; ≥1 persona carrying rapport-gated facts), case-file word range, all eight anatomy section keys present and non-trivial. "All deep" must be machine-checkable, not eyeballed.
