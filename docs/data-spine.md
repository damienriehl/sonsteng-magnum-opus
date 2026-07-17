# The Data Spine — Conventions for Fleet Agents

*This is the consistency contract. JSON Schema guarantees shape, not vocabulary — vocabulary drift is the #1 risk at 20-parallel-author scale. Read this before authoring anything under `data/`. Schemas live in `data/schemas/`; minimal valid instances in `data/schemas/examples/`.*

---

## 1. ID grammar (matter-prefixed, deterministic, no UUIDs)

Every ID is deterministic: **namespace + type + zero-padded sequence or slug**. Never random. A matter's files may contain **only its own `mNN.` prefix** (the validator greps for cross-matter bleed).

### Global namespaces (authored sequentially in Phases 0–2, never by the matter fleet)

| Entity | Pattern | Example |
|---|---|---|
| Skill | `^SK-(LP\|PM)-\d{2}$` | `SK-LP-13`, `SK-PM-03` |
| Task | `^TSK-\d{3}$` | `TSK-042` |
| Subtask | `^TSK-\d{3}\.\d{2}$` | `TSK-042.01` |
| Firm | `^FIRM$` | `FIRM` |
| Firm client | `^FIRM-C-\d{2}$` | `FIRM-C-06` |
| Firm timekeeper | `^FIRM-TK-\d{2}$` | `FIRM-TK-01` |
| Firm budget line | `^FIRM-B-\d{2}$` | `FIRM-B-02` |
| Matter | `^m\d{2}$` | `m06` |
| Matter slug | `^m\d{2}-[a-z0-9-]+$` | `m06-noncompete-ny` |

### Matter-scoped namespaces (each matter agent owns its own `mNN.` prefix)

| Entity | Pattern | Example |
|---|---|---|
| Side/role | `^m\d{2}\.role\.[a-z0-9-]+$` | `m06.role.defendant` |
| Persona | `^m\d{2}\.per\.[a-z0-9-]+$` | `m06.per.baines` |
| Fact anchor | `^m\d{2}\.fact\.\d{3}$` | `m06.fact.014` |
| Witness | `^m\d{2}\.wit\.[a-z0-9-]+$` | `m06.wit.okafor` |
| Exhibit | `^m\d{2}\.exh\.\d{3}$` | `m06.exh.001` |
| Exercise packet | `^m\d{2}\.ex$` | `m06.ex` |
| Rubric | `^m\d{2}\.rub$` | `m06.rub` |
| Rubric criterion | `^m\d{2}\.rub\.c\d{2}$` | `m06.rub.c01` |
| Rubric subcriterion | `^m\d{2}\.rub\.c\d{2}\.s\d{2}$` | `m06.rub.c01.s01` |
| Business bundle | `^m\d{2}\.biz$` | `m06.biz` |
| Time entry | `^m\d{2}\.te\.\d{4}$` (4 digits) | `m06.te.0007` |
| Invoice | `^m\d{2}\.inv\.\d{3}$` | `m06.inv.001` |
| Trust entry | `^m\d{2}\.tr\.\d{3}$` | `m06.tr.002` |

> Note the digit widths: **facts/exhibits/invoices/trust = 3 digits; time entries = 4 digits.** These are load-bearing (the validator enforces the exact pattern).

---

## 2. The closed rapport-trigger vocabulary

Rapport-gated facts (`persona.disclosure.rapport_gated[]`) carry `min_turns` (positive int) **and/or** `requires[]`. Every token in `requires[]` MUST come from this closed enum (defined in `persona.schema.json`). **An unknown token is a validator ERROR** — do not invent triggers; free strings would give 20 dialects the Worker and `/debrief` cannot reason over.

| Trigger token | Semantics (what the interviewer must have done) |
|---|---|
| `open_ended_invitation` | Asked a genuinely open-ended question ("Tell me what happened") rather than a yes/no or leading one. |
| `wellbeing_question` | Asked after the client's wellbeing / how they are holding up. |
| `acknowledged_emotion` | Named and validated the client's emotion ("That sounds stressful"). |
| `no_interruption_streak` | Let the client speak without interrupting for a sustained stretch. |
| `confidentiality_reassurance` | Reassured the client that what they say is privileged / confidential. |
| `nonjudgmental_response` | Responded to a sensitive admission without blame or judgment. |
| `follow_up_on_hint` | Noticed and gently followed up on a hint the client dropped. |
| `explained_process` | Explained what happens next / how the process works, reducing uncertainty. |

The same enum is reused by `debrief.scorecard` (`axis_a.rapport_gated_unearned[].trigger_needed`).

---

## 3. The 8 fixed exercise section keys

`exercise.schema.json` requires **exactly** these eight keys under `sections` (all required; the site generator and validator key off them). Each section carries `body_md` and/or a non-empty `files[]`:

`intro` · `objectives` · `activities` · `instructions` · `case_file` · `history` · `considerations` · `substantive_info`

(`history` = procedural & factual history; `substantive_info` = substantive information.) Do not rename, add, or drop keys.

---

## 4. `schema_version` rules

- Every entity instance carries a **`schema_version`** string (`^\d+\.\d+\.\d+$`).
- Every schema file declares its own **`version`** (currently `1.0.0`).
- `data/spine-manifest.json` maps `{entity_type: schema_version}` + a top-level `spine_version`.
- The validator's first check: `entity.schema_version == manifest.schemas[entity_type]`. A mismatch distinguishes "author error" from "authored against an older schema."
- The `entity_type` keys in the manifest are: `matter, persona, rubric, exercise, business, skill, task, firm, debrief_scorecard, critique_scorecard`.

---

## 5. The schema-freeze rule

**Schemas FREEZE when Phase 3 (the matter fleet) launches.** Once ~20 agents are in flight they cannot be re-briefed. After freeze, the only permitted schema changes are **additive AND optional** (new optional properties). No new required fields, no renamed keys, no tightened patterns, no removed enum values. If a genuine breaking need appears, it waits for a `spine_version` bump after the fleet lands — not mid-flight.

---

## 6. `@id` and JSON-LD

- Every skill, task, matter, persona, rubric, and the firm carries a canonical **`@id`** (an IRI). Rubric criteria are addressable via their `id` under the rubric's `@id`.
- The JSON-LD `@context` maps `@id` onto the spine base IRI **`https://sonsteng.damienriehl.com/spine/`** (recorded in `spine-manifest.json` as `jsonld_context_base`). An optional per-file `@context` property is allowed on entities that carry `@id`.
- Convention for `@id` values: `https://sonsteng.damienriehl.com/spine/<type>/<id>` — e.g. `.../spine/persona/m06.per.baines`, `.../spine/skill/SK-LP-13`.
- This is cheap now (FOLIO IRIs are already in play) and expensive to retrofit across 20 matters, so it is mandatory from the start.

---

## 7. FOLIO mapping (skills & tasks)

Each skill/task carries **exactly one** of:
- `folio: { iri, mapping_confidence }` where `mapping_confidence ∈ {exact, near, parent}` and `iri` matches `^(https://folio\.openlegalstandard\.org/R[A-Za-z0-9]+|R[A-Za-z0-9]+)$`; **or**
- `no_folio_equivalent: true`.

Neither, both, or a placeholder = validator ERROR. No forced mappings — use nearest-parent IRI with `parent` confidence, or declare no equivalent.

---

## 8. Cross-file invariants the schema cannot express (validator ERROR/WARN)

The schema enforces shape; `tools/validate_spine.py` enforces these (see `docs/research/validator-spec.md`):
- Every `fact_ref` resolves to a fact anchor in that matter's `facts.md`.
- `knowledge_boundary.color_topics` are **disjoint** from material facts (overlap = ERROR).
- Every `interviewable_by` role exists in `matter.sides`.
- Persona↔matter, witness↔matter, criterion↔rubric listings are bidirectional.
- Σ(criterion `weight_points`) = rubric `declared_total`; subcriteria sum to their parent.
- Money math: `fees + expenses − payments_received = balance_due`; trust `running_balance` never negative in date order; time-entry rates match the firm rate card or a declared engagement rate.
- **Time-entry `hours` in 0.1 increments** — enforced here with Decimal arithmetic, NOT by JSON Schema `multipleOf` (which is unreliable under float in the Python validator).
