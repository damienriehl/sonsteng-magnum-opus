---
artifact_contract: ce-unified-plan/v1
artifact_readiness: superseded
execution: code
product_contract_source: ce-plan-bootstrap
title: "feat: make the taxonomy, jurisdictions and firm content editable"
date: 2026-07-28
type: feat
depth: deep
origin: briefs/qa/sonsteng-2026-07-28-taxonomy-not-editable.json (cockpit ask, in the coding-projects repo)
---

# feat: make the taxonomy, jurisdictions and firm content editable

> **SUPERSEDED 2026-07-28 by
> [`2026-07-28-002-feat-word-like-practicum-editing-plan.md`](2026-07-28-002-feat-word-like-practicum-editing-plan.md).**
>
> Damien reframed the goal the same day: John and Roger want to edit *all* the exercises like Word
> documents, including adding and deleting paragraphs, plus AI-drafted changes scoped to a part, a
> matter, a module or the whole course. This plan addresses only the coverage gap — content missing
> from the editor map — which is one unit (U3) of the replacement.
>
> **Still accurate and worth reading:** the "How editability actually works" section below. It is
> the reference write-up of the walker contract, the `data-ebsrc` mechanism and the two tiers, and
> the replacement plan cites it rather than repeating it.

**Target repo:** sonsteng-magnum-opus

---

## Goal Capsule

John opened the Skills browser to edit it and found nothing editable. Make the parts of the
practicum that are *authored prose* editable through the editor — the 26 skills and their tasks
and subtasks, the jurisdiction notes, the firm profile — without disturbing the 3,474 blocks that
already work, and without breaking the walker/injector contract that holds the whole editing
surface together.

---

## Problem Frame

Damien hit this on 2026-07-28: he opened the Skills browser and saw no editing affordances. Two
things were wrong, and only the second matters here.

1. He was on `sonsteng-dev.damienriehl.com/platform/…`, the **public student view**. The editing
   layer only exists when the Worker proxies and injects it. *(Not a defect — that is what the
   student view is. Resolved by using `edit.sonsteng.damienriehl.com/edit/…`.)*
2. **That page has zero editable blocks even in the editor.** So the right URL would not have
   helped.

Measured, by pulling the injected island out of the served editor page:

| page | editable blocks |
|---|---|
| `skills/index.html` | **0** |
| `matters/index.html` | **0** |
| `index.html` | **0** |
| `firm/index.html` | **0** |
| `about/third-party.html` | **0** |
| `modules/m1.html` | 15 |
| `matters/m01-arbitration-meridian/index.html` | 182 |

The editor map covers **exactly two source trees** — `data/curriculum/` and `data/matters/` — across
280 distinct files. Uncovered content, by file count:

| tree | files | what it is | in map? |
|---|---|---|---|
| `data/taxonomy/` | 3 | 37 skills, 113 tasks, 144 crosswalk rows (~294 entries) | **no** |
| `data/jurisdictions/` | 7 | per-jurisdiction notes | **no** |
| `data/firm/` | 1 | the fictional firm's profile | **no** |
| `data/schemas/` | 20 | JSON schemas | no — correct, not content |

Every skill name, task title, task description and subtask on the Skills browser is unreachable
through the editor. The one apparent exception is a red herring: "Conduct a preliminary case
analysis" *does* appear in the map, at `data/curriculum/m2.md#p5`, because that same sentence was
independently written as prose in the module text.

**Why now:** the walkthrough is the week of Aug 3. John and Roger review curriculum and matter
content, which is editable — but the taxonomy is the spine the whole practicum is organised
around, and "I can't change these words" is a bad answer to give a reviewer mid-session.

---

## How editability actually works — read this before touching anything

`tools/build_site.py` (3,171 lines) is the generator. The mechanism is two-phase and the second
phase is where the danger lives.

1. **While rendering**, an editable block gets a temporary attribute `data-ebsrc="<source_ref>"` on
   its element, and `EDMAP.register(source_ref, kind, original_text, …)` records the metadata.
   Two helpers do this today:
   - `md(...)` with a `src` base — auto-registers each markdown block as `kind: "prose"` with
     `source_ref` = `<file>#p<n>`.
   - `_eb_scalar_attr(source_ref, original_text, json_path, context)` — returns the attribute
     string and registers `kind: "json_scalar"`. This is the one the JSON-backed content uses.
2. **After the whole site is written**, `build_editor_map()` re-parses each page and walks
   candidate elements inside `<main>` in document order. **THE WALKER CONTRACT**, verbatim from the
   generated map:

   > Within `<main>`, candidate elements in document order are `p`, `li`, `h1`-`h6`, `blockquote`
   > (outermost). index = 0-based position in that list. Editable blocks are candidates whose index
   > is present here; all other candidates are read-only. **Mirror byte-for-byte in the injector.**

**The consequence that shapes this entire plan:** a candidate becomes editable *iff it carries
`data-ebsrc`*. Adding that attribute to an element that is **already a candidate** changes nothing
about the candidate list — same elements, same order, same indices. Adding a **new** candidate
element, or changing which tags count as candidates, **renumbers every subsequent index on that
page**.

That is the difference between the two tiers below, and it is the difference between a safe change
and one that needs a migration.

### What the Skills browser actually renders

Measured on `site/platform/skills/index.html`: 460 candidates in `<main>` (227 `p`, 232 `li`,
1 `h1`, 3 `h2`), and **zero** `data-ebsrc`.

| content | container | candidate? | tier |
|---|---|---|---|
| task name | `<p style="margin:0">` → `<span class="task-name">` | **yes** | A |
| task description | `<p class="subtask">` | **yes** | A |
| subtask text | `<li>` | **yes** | A |
| **skill name** | `<summary>` → `<span class="skill-card__name">` | **no** | B |

So most of it is Tier A. The skill *names* are Tier B, because `<summary>` is not in the candidate
set — and `<summary>` is used by the disclosure widgets, so adding it to the contract would
renumber indices on every page that uses one.

---

## Product Contract

### Requirements

- **R1.** Every authored prose string rendered by the Skills browser — skill names, task names,
  task descriptions, subtask text — is editable through the editor.
- **R2.** The FOLIO crosswalk identifiers (`RNLAIVO9PK…`), skill/task IDs (`SK-LP-01`, `TSK-001.02`),
  Bloom levels and module chips are **NOT** editable. They are ontology keys and join keys that
  other pages resolve against; editing one silently breaks a cross-link rather than changing a word.
- **R3.** `data/jurisdictions/` and `data/firm/` prose becomes editable on the pages that render it.
- **R4.** The 3,474 blocks that are editable today stay editable, at the **same page indices**, so
  no already-issued suggestion drifts. *(See KTD1 — this is the property most at risk.)*
- **R5.** The walker and the injector still agree byte-for-byte; `check_build_parity.py` passes.
- **R6.** Attribution, scopes and the Access door are untouched. This plan changes what is
  editable, never who may edit.
- **R7.** The five zero-block pages are each given an explicit verdict — made editable, or recorded
  as legitimately aggregate — so "0 blocks" is never again ambiguous between "correct" and "broken".

### Key Decisions

- **KD1. Tier A first, and ship it on its own.** Everything reachable by adding `data-ebsrc` to an
  element that is already a candidate goes first, because it cannot shift an index and therefore
  cannot drift an existing suggestion. Tier B (the skill names, needing a DOM or contract change) is
  a separate unit with its own decision point.
- **KD2. IDs and crosswalk keys stay read-only** (R2). *(Recommended, pending Damien —
  `sonsteng-2026-07-28-taxonomy-not-editable` q1 offered "only the human-readable text" as an
  option. Treat R2 as settled unless he says otherwise.)*
- **KD3. Do the index-shifting work only while the review queue is empty.** It was 0 rows on
  2026-07-27. A shift while suggestions are pending would mark them `drift` and force re-anchoring.

### Scope Boundaries

**In scope:** `build_site.py` block registration for taxonomy / jurisdictions / firm, the injector
mirror if the candidate set changes, the map regeneration, and the per-page verdict for the five
zero-block pages.

**Out of scope:** the Access door (done, working), the editor client UI, the apply daemon, any
change to scopes or attribution, and `data/schemas/` (not content).

---

## Planning Contract

### Assumptions

- **A1.** The Skills browser's rendering code lives in `build_site.py` and emits the `<p>`/`<li>`
  structure measured above. *(Verify in U1 — the renderer function has not been located yet.)*
- **A2.** `data/taxonomy/*.json` entries have stable keys usable as `json_path` locators, the way
  the matter business files already do. *(Verify in U1.)*
- **A3.** The review queue is empty when Tier B lands (KD3).

### Key Technical Decisions

- **KTD1. Tier A adds attributes to existing candidates only — no new elements, no contract
  change.** This is the whole reason Tier A is safe. Any patch in U2/U3 that introduces a new
  `p`/`li`/`h*`/`blockquote` inside `<main>`, or wraps existing content in one, is out of contract
  and must be rejected in review even if the page looks right.
- **KTD2. Use `_eb_scalar_attr` with a real `json_path`, not a synthetic locator.** The endpoint
  validates `json_path` against the map for `json_scalar` blocks (`validateJsonScalar`) specifically
  to stop path forgery. A made-up locator would either fail validation or, worse, write to the wrong
  field on apply.
- **KTD3. The editable unit is the smallest element that contains ONLY the authored string.** The
  task name lives in `<p style="margin:0"><span class="task-name">…</span> TSK-001 BLOOM · …</p>` —
  marking that `<p>` editable would hand the reviewer the ID chips too, violating R2. Tier A must
  therefore restructure *within* the candidate (move the chips out of the editable element's text,
  or mark a narrower element) **without adding a candidate**. If that proves impossible for a given
  field, it drops to Tier B rather than being forced.
- **KTD4. Regenerate and diff the map, do not eyeball the page.** The proof that R4 holds is a
  mechanical diff of `build/editor-map.generated.json` before and after: every pre-existing
  `(page, index) → source_ref` triple must be unchanged. Add that as a test, not a habit.

---

## Implementation Units

### U1. Locate the renderers and confirm the locators

- **Goal:** know exactly which functions render the five zero-block pages, and whether the taxonomy
  JSON has usable `json_path` locators.
- **Requirements:** R1, R3, R7
- **Dependencies:** none
- **Files:** none (investigation) — record findings in this plan
- **Approach:** find the Skills-browser renderer in `build_site.py`; read `data/taxonomy/skills.json`
  and `tasks.json` for their key shapes; check how `data/matters/*/business/*.json` derive
  `json_path` today and copy that idiom. Produce a table: page → renderer function → source file →
  proposed `source_ref`/`json_path` per field.
- **Verification:** the table exists and every proposed locator resolves against real JSON.

### U2. Baseline the map so R4 is provable

- **Goal:** a test that fails if any existing block moves.
- **Requirements:** R4
- **Dependencies:** none (do this BEFORE U3)
- **Files:** `tools/tests/test_editor_map_stability.py` (new)
- **Approach:** snapshot the current `(page, index) → source_ref` triples from
  `build/editor-map.generated.json` into a fixture. The test regenerates (or reads the current
  build) and asserts every snapshotted triple is still present and unmoved. New blocks are allowed;
  moved or vanished ones are not.
- **Execution note:** write this first. It is the only thing standing between "added editability"
  and "silently drifted 3,474 blocks."
- **Test scenarios:**
  - Passes against the current tree.
  - Fails if a triple's index changes (mutate the fixture to prove the guard bites).
- **Verification:** green now; demonstrably red under a simulated shift.

### U3. Tier A — make the task-level taxonomy prose editable

- **Goal:** task names, descriptions and subtasks on the Skills browser are editable.
- **Requirements:** R1, R2, R4, R5
- **Dependencies:** U1, U2
- **Files:** `tools/build_site.py`
- **Approach:** in the Skills renderer, register each authored string with `_eb_scalar_attr` and
  attach `data-ebsrc` to the element that already is a candidate, per KTD1. Keep IDs, Bloom levels,
  FOLIO chips and module chips outside the editable element's text (KTD3, R2). Regenerate.
- **Test scenarios:**
  - `skills/index.html` block count moves from 0 to a non-zero number matching the field inventory
    from U1.
  - U2's stability test still passes — no pre-existing block moved.
  - `check_build_parity.py` passes (walker/injector still agree).
  - A `json_scalar` suggest against one of the new `source_ref`s is accepted; the same suggest with
    a forged `json_path` is rejected `validation_error`.
  - No ID, Bloom level or FOLIO key appears inside any new block's `original_text` (R2 — assert it).
- **Verification:** the served editor page's island shows the new blocks; a round-trip save lands.

### U4. Tier A — jurisdictions and firm

- **Goal:** `data/jurisdictions/` and `data/firm/` prose is editable where rendered.
- **Requirements:** R3, R4, R5
- **Dependencies:** U1, U2
- **Files:** `tools/build_site.py`
- **Approach:** same idiom as U3 on the firm page and wherever jurisdiction notes render.
- **Test scenarios:** `firm/index.html` moves off 0; U2 stable; parity passes.
- **Verification:** round-trip save on one field of each.

### U5. Verdict on the five zero-block pages

- **Goal:** no page is ambiguously empty.
- **Requirements:** R7
- **Dependencies:** U3, U4
- **Files:** `tools/build_site.py` (a comment per page), this plan
- **Approach:** for each of `skills/`, `matters/`, `index.html`, `firm/`, `about/third-party.html`,
  record either "now editable, N blocks" or "legitimately aggregate — every string is derived".
  `matters/index.html` deserves particular care: it was already fixed once for *reachability*, which
  suggests someone expected it to be editable.
- **Verification:** each page has a one-line recorded verdict; a reader can tell broken from correct.

### U6. Tier B — skill names (decision point, not automatic)

- **Goal:** decide and, if approved, implement editable skill names.
- **Requirements:** R1, R4, R5
- **Dependencies:** U3, U5
- **Files:** `tools/build_site.py`, `app/worker/src/editor-inject.js` (only if the contract changes)
- **Approach:** skill names live in `<span>` inside `<summary>`, which is not a candidate. Three
  options, in preference order:
  1. **Move the name into a candidate** already inside the disclosure (if one exists) — no contract
     change, no index shift.
  2. **Add `summary` to the candidate set** — a walker-contract change that must be mirrored
     byte-for-byte in `editor-inject.js` and **renumbers indices on every page using `<summary>`**.
     Requires KD3's empty queue and U2's stability test to be re-baselined deliberately.
  3. **Leave skill names read-only** and record why.
- **Execution note:** **stop and ask Damien** before taking option 2. It is the only step in this
  plan that can drift existing suggestions, and the plan's own R4 forbids it by default.
- **Test scenarios:** if option 2 is taken, parity passes and the re-baselined stability test is
  regenerated in the same commit, with the drift consequence stated in the message.
- **Verification:** skill names editable, or a recorded decision that they are not.

---

## Verification Contract

- `bash tools/preflight.sh` — 9 gates, 0 skipped.
- The new `test_editor_map_stability.py` passes at every step.
- Live, after deploy: the served `/edit/skills/index.html` island contains the new blocks; a save
  against one of them round-trips into the store; the review queue is returned to its prior state.
- Both trees clean; merge to `main` from `~/.local/share/sonsteng-daemon/checkout` under
  `flock .locks/daemon.lock`.
- **Do not forget:** regenerate the sources before deploying —
  `python3 tools/build_site.py --check && python3 tools/build_instructor_bundle.py`. The bundler
  itself runs automatically via wrangler's `build.command`; it is the *inputs* that go stale.

## Definition of Done

R1–R7 hold; preflight 9/9; no pre-existing block moved; the five pages each carry a verdict; and
Tier B is either implemented with Damien's explicit go-ahead or recorded as declined.

---

## Risks & Dependencies

- **Index drift (R4, KTD1).** The dominant risk. Adding any candidate element renumbers everything
  after it on that page, marking live suggestions `drift`. Mitigated by U2's stability test written
  *before* any change, and by KD3's empty-queue rule.
- **Walker/injector divergence (R5).** The contract says "mirror byte-for-byte." Any candidate-set
  change touches two files in two languages. Mitigated by `check_build_parity.py` and by confining
  Tier A to attribute-only edits.
- **Over-broad editable units (R2, KTD3).** Marking the containing `<p>` editable is the easy
  implementation and the wrong one — it hands reviewers the ID chips. Mitigated by asserting no
  ID/Bloom/FOLIO string appears in a new block's text.
- **`json_path` forgery surface (KTD2).** New `json_scalar` blocks widen what `validateJsonScalar`
  must cover. Mitigated by the forged-path rejection test in U3.

## Open Questions

- **OQ1.** Confirm KD2 — IDs and crosswalk keys stay read-only. Recommended; Damien was offered a
  narrower option in the origin ask and has not answered it.
- **OQ2.** Tier B: are editable skill names worth a walker-contract change and an index re-baseline?
  Default answer in this plan is no; U6 stops and asks.
- **OQ3.** `matters/index.html` — is the Matter Library landing *meant* to be editable? It was fixed
  once for reachability, which hints yes, but every string on it may be derived.

## Sources & Research

- Origin: cockpit ask `sonsteng-2026-07-28-taxonomy-not-editable`.
- Measured this session: page block counts pulled from the injected island on the live editor;
  `build/editor-map.generated.json` counts and coverage; candidate-element census of
  `site/platform/skills/index.html` (460 candidates, 0 `data-ebsrc`).
- Read: `tools/build_site.py` lines ~169–310 (the `EDMAP` register / `_eb_scalar_attr` / `md()`
  registration path) and ~2766–2812 (the walker and `_extract_page_blocks`).
- The walker contract is quoted verbatim from `walker_contract` in the generated map — it is the
  authority, not this document.
