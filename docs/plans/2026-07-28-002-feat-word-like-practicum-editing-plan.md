---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: ce-brainstorm
title: "feat: Word-like editing of the whole practicum, with scoped AI-drafted changes"
date: 2026-07-28
type: feat
depth: deep
origin: briefs/qa/sonsteng-2026-07-28-editing-scope.json (cockpit ask, in the coding-projects repo)
supersedes: docs/plans/2026-07-28-001-feat-editable-coverage-plan.md
---

# feat: Word-like editing of the whole practicum, with scoped AI-drafted changes

**Target repo:** sonsteng-magnum-opus

---

## Goal Capsule

Let John and Roger — both 83, both eminent, neither interested in a tool — rewrite the practicum
the way they would rewrite a Word document. Change any word, add a paragraph, delete one, move one.
And when the change is bigger than a paragraph, say what they want in their own words at the scope
they mean — this exercise, this part, this module, the whole course — and have the system draft it
everywhere, show them the redline, and wait for Damien before it lands.

---

## Problem Frame

Damien, 2026-07-28: *"My 83-year-old colleagues want to edit all of the exercises, almost as if they
were Word documents. Where they have control over editing every aspect of the scenarios, to
customize them."* Two kinds of edit: line edits, and **suggested broader changes** — global in the
scenario, global for the section, global for the entire course.

Today's editor does exactly one thing: **replace the text of a block that already exists.** Verified
against the endpoint surface — there is no insert, no delete, no split, no merge, no reorder. That
is not a missing button; it is the whole vocabulary. So "rewrite this exercise" is not currently
expressible, and neither is anything at a scope larger than one block.

### The obstacle underneath, stated precisely

Editable content is addressed twice, and **both addresses are positional**:

1. **Page anchor** — 0-based index among candidate elements (`p`, `li`, `h1`-`h6`, `blockquote`)
   inside `<main>`. The walker contract in `build/editor-map.generated.json` is the authority.
2. **Source locator** — `source_ref` = `<file>#p<n>`, e.g.
   `data/matters/m01-arbitration-meridian/case-file/statement-osgard.md#p2`. The `p2` is an
   **ordinal within the file**.

Insert one paragraph and *both* renumber: every later page index shifts, and every later `#pN`
shifts. Existing pending suggestions anchored to the old numbers become wrong — the store already
models this outcome as `drift`, which is a recovery mechanism, not a licence to cause it.

**So structural editing is not a feature that can be added on top. It requires block identity that
does not move.** That is U1, and everything else depends on it.

### What already exists in our favour

- **Atomic batches.** The store carries `group_id` throughout (21 references) and enforces
  group semantics: a group accept is one transaction, and accepting a lone member of a group is
  rejected `409 group_accept_required`. A broad change is exactly a group — this machinery is the
  right shape and is already load-bearing for the apply engine.
- **Drift and re-anchoring** (`drift` → `reanchor` → `pending`) already exist for when content moves
  under a suggestion.
- **Versioning and one-click undo** already exist; nothing here needs to invent history.
- **A scoped-rewrite foothold** — `tools/editor_ai_rewrite.py` already reasons about matter scope,
  and the store already distinguishes `origin: human | companion | ai_rewrite`, with system origins
  never auto-accepted.
- **Comments are a separate channel** from edits and are never auto-applied, so "suggest, don't
  change" is already expressible.

---

## Product Contract

### Requirements

- **R1. Any authored prose in the practicum is editable in place** — the 20 matters, the curriculum
  modules, the templates, and the trees currently missing from the map (`data/taxonomy/`,
  `data/jurisdictions/`, `data/firm/`).
- **R2. Structural editing.** An editor can **add, delete, split, merge and reorder** blocks, not
  merely replace their text. *(session-settled: user-directed, 2026-07-28 — "Yes.")*
- **R3. Facts are the UPSTREAM editing surface, and the flow is staged.** *(session-settled:
  user-directed, 2026-07-28 — Damien: facts "are more likely the things that the editors will want
  to change", including "adding additional facts", and he specified the flow.)* The editing flow is:
  - **Stage 1 — edit the Facts** (if at all): a first-class facts surface per matter, where an
    editor can **change** a fact (party name, date, amount, jurisdiction) or **add** a new one —
    not hunt for where a fact happens to render in prose.
  - **Stage 1.5 — the system propagates** the factual change through the prose. Two classes that
    behave differently, and the distinction is the design:
    - **derived** — the fact lives in one JSON field and every rendering follows automatically on
      rebuild; nothing to draft;
    - **restated** — the same fact was independently written into prose ("Meridian's filing
      deadline of 14 days…"). These do NOT follow. The system finds them by corpus search and
      drafts a per-site edit as an AI batch for review — Stage 1.5 IS a scoped change (R5's
      machinery) with a mechanical instruction, not a separate invention.
  - **Stage 2 — edit the Prose** (if at all): line edits on the fast path, and broader conceptual
    edits ("Throughout this section, please <X>…") through the scoped-change machinery.
  A **new** fact has no renderings yet, so adding one offers — but never forces — a drafted set of
  prose insertions mentioning it (structural inserts under one group).
- **R4. A four-level scope ladder**, all of which a broader change may target
  *(session-settled: user-directed, 2026-07-28 — Damien specified both levels rather than choosing
  between them)*:
  **part** (`case-file` / `exercise` / `personas` / `business`) → **matter** (m01–m20) →
  **module** (M1/M2/M3, cutting across all matters) → **course**.
- **R5. Broad changes are AI-drafted and human-approved.** The editor describes the change in
  natural language at a chosen scope; the system enumerates the affected blocks and drafts a
  per-block edit; the editor sees a redline; nothing lands until Damien approves.
  *(session-settled: user-directed — "AI drafts and User approves.")*
- **R6. Line edits keep the fast path; anything wider is gated.** A single-block human edit
  continues to auto-apply in ~2 minutes as it does today. Every scoped change waits for approval,
  regardless of size. *(session-settled: user-directed — "Yes, fast path for line edits and gating
  anything wider.")*
- **R7. A scoped change is one reversible unit.** It applies atomically under a single `group_id`
  and is undone by a single gesture — never by reverting N edits by hand.
- **R8. No existing suggestion is silently drifted by this work.** Migrations that renumber anchors
  happen only against an empty queue, and are proven by a mechanical before/after diff.
- **R9. The affordances suit the users.** Large type already exists; adding and deleting a paragraph
  must be as obvious as typing in one, and destructive actions must be plainly reversible on screen.
- **R10. Scope, attribution and the Access door are untouched.** This changes *what* can be edited
  and *how*, never *who* may edit.

### Key Decisions

- **KD1. Durable block identity before anything else.** Structural editing is impossible while both
  address layers are ordinal. U1 introduces an identifier that survives insertion, deletion and
  reordering, and everything else is sequenced behind it.
- **KD2. Treat a module-scoped change as a refactor, not an edit.** *(session-settled:
  user-directed, 2026-07-28 — Damien: "the Module suggested changes might cascade throughout the
  entire practicum, so we'll have to be careful how we implement that. It's a bit dangerous, almost
  akin to a code refactor.")* The plan takes that literally and imports refactor safety practice:
  blast radius reported **before** drafting, preview always, atomic group with single-gesture undo,
  explicit ceilings, and a canary matter applied and reviewed before the remainder.
- **KD3. Scoped changes never auto-apply,** even when they touch one block (R6). The gate is the
  *scope of the request*, not the count of the result — a course-scoped change that happens to match
  three blocks is still a course-scoped change, and next time it will match four hundred.

### Scope Boundaries

**In scope:** block identity, structural operations end-to-end (store → endpoints → apply engine →
UI), editable coverage for the missing trees, fact editing with propagation preview, the scope
ladder, AI-drafted scoped changes with redline review, and the gating rule.

**Deferred to follow-up work:**
- Editing the FOLIO crosswalk identifiers, skill/task IDs and Bloom levels. They are join keys other
  pages resolve against; editing one breaks a cross-link rather than changing a word. Read-only
  until someone asks for otherwise.
- Real-time collaborative editing. Two people in the same block at once is a different problem.
- Any change to the PROD door.

**Outside this product's identity:** self-service accounts, roles beyond the existing slot model,
and letting an editor change how the site is generated rather than what it says.

---

## Planning Contract

### Assumptions

- **A1.** Every editable source file is text we control (`.md` and `.json` under `data/`), so a
  durable identifier can be written into the file itself. *(Verify in U1.)*
- **A2.** The review queue can be emptied on demand for the migration window (it was 0 rows on
  2026-07-27). *(R8, KD2.)*
- **A3.** The apply engine (`tools/direct_apply_daemon.py`, `tools/apply_suggestions.py`) applies
  edits by locating the block in the source file; teaching it insert/delete is an extension of that
  path, not a rewrite. *(Verify in U1.)*

### Key Technical Decisions

- **KTD1. Block identity is a stable ID stamped into the source file, not a content hash and not a
  position.** Content hashes change when the content is edited — which is the one moment identity
  must survive. Positions change when neighbours move. A generated, never-reused ID (e.g. an
  eight-character token recorded in the markdown as a trailing marker, and as a `_bid` key in JSON
  objects) is the only anchor stable under all three operations. `source_ref` becomes
  `<file>#<bid>` and stops being ordinal.
- **KTD2. Keep the positional page index as a *hint*, not an identity.** The walker still computes
  indices — the injector needs them to place the affordance — but a suggestion is keyed by `bid`. A
  moved block is then a re-render, not a drift.
- **KTD3. Structural operations are new suggestion *kinds*, carried through the existing pipeline.**
  `insert_after`, `delete`, `split`, `merge`, `move` join `prose` / `json_scalar` rather than
  becoming a parallel system, so they inherit review, attribution, grouping, history and undo for
  free. A structural op is never auto-applied on its first outing (see the staging note in U4).
- **KTD4. A scoped change is a `group_id` batch, produced in three phases:** *enumerate* (which
  blocks does this scope contain, and which does the request actually match) → *draft* (AI proposes
  a per-block edit for each) → *review* (redline, then approve or reject as one). Enumeration is
  deterministic and reported before any model runs, so the blast radius is known before a single
  token is spent.
- **KTD5. Ceilings and a canary on scoped changes.** A batch above a configured block count refuses
  to draft without explicit confirmation. Module- and course-scoped batches apply to one matter
  first; the remainder waits on that matter verifying clean. This is KD2's refactor discipline made
  mechanical.
- **KTD6. Stage 1.5 is implemented AS a scoped batch, not as new machinery.** For a fact edit,
  enumerate the JSON field's render sites from the editor map (derived — automatic on rebuild,
  nothing to draft) and separately search the prose corpus for the old literal (restated — each
  needing its own edit). The restated set becomes an AI-drafted `group_id` batch through the same
  enumerate → draft → review pipeline as any scoped change (KTD4), with the instruction generated
  mechanically ("this fact changed from X to Y; update this sentence accordingly"). One review, one
  approval, one undo. Silently doing the derived half and not the restated half is how a scenario
  ends up internally contradictory — so the review screen always shows both classes, and completion
  is never implied while restated edits sit undecided.
- **KTD7. The facts surface is generated from the matter's JSON + schemas, not hand-curated.** Each
  matter's `business/*.json` and friends render as a labelled facts panel (label, current value,
  where-used count). Adding a fact appends a schema-valid key. IDs and join keys stay read-only on
  this surface exactly as everywhere else. This is what makes Stage 1 feel like "editing the facts
  of the scenario" rather than "editing JSON".

---

## Implementation Units

### U1. Discovery and the identity design

- **Goal:** confirm A1–A3, and settle the exact identifier format before any migration.
- **Requirements:** R2, R8
- **Dependencies:** none
- **Files:** none (investigation); record the outcome in this plan
- **Approach:** read the apply engine's block-location path; confirm how `.md` and `.json` blocks are
  found and rewritten; choose the ID syntax for each (a markdown trailing marker that survives the
  renderer without appearing on the page; a `_bid` key in JSON objects that schemas tolerate);
  confirm `data/schemas/` permits an added key or plan the schema change.
- **Verification:** a written spec for the ID format in both file types, plus proof it round-trips
  the renderer without rendering.

### U2. Stamp durable IDs across the corpus, and re-key the map

- **Goal:** every editable block has an ID that will never change; `source_ref` uses it.
- **Requirements:** R2, R8
- **Dependencies:** U1
- **Files:** `tools/stamp_block_ids.py` (new), `tools/build_site.py`, `data/**`
- **Approach:** one-time migration stamping IDs into all editable source files; `build_site.py`
  emits `source_ref = <file>#<bid>`; the map records both `bid` and the current index.
- **Execution note:** **empty the review queue first** (A2, R8). Write the before/after equivalence
  test *before* running the migration — for every currently-editable block, the new `bid`-keyed
  entry must resolve to the same file, the same text and the same page.
- **Test scenarios:**
  - Every pre-migration block maps 1:1 to a post-migration block with identical `original_text`.
  - IDs are unique corpus-wide and stable across two consecutive builds.
  - Inserting a paragraph by hand into a source file leaves every other block's `bid` unchanged
    (the property the whole plan rests on).
  - The stamped markers do not appear in rendered output.
- **Verification:** block count unchanged; `check_build_parity.py` passes; no rendered diff.

### U3. Editable coverage for the missing trees

- **Goal:** taxonomy, jurisdictions and firm content become editable.
- **Requirements:** R1
- **Dependencies:** U2
- **Files:** `tools/build_site.py`
- **Approach:** as specified in the superseded plan
  (`2026-07-28-001-feat-editable-coverage-plan.md`, still accurate on mechanism): register the
  authored strings with `_eb_scalar_attr` and attach `data-ebsrc` to elements that are **already**
  walker candidates, so no index shifts. Keep IDs, Bloom levels and FOLIO keys read-only.
- **Test scenarios:** `skills/index.html` and `firm/index.html` move off zero blocks; no pre-existing
  block moves; no ID or crosswalk key appears inside a new block's text.
- **Verification:** a round-trip save on a skill task description.

### U4. Structural operations, end to end

- **Goal:** add, delete, split, merge and reorder work from the browser to the canonical files.
- **Requirements:** R2, R7, R9
- **Dependencies:** U2
- **Files:** `app/worker/src/editor-store-core.js`, `editor-endpoints.js`, `editor.js`,
  `app/editor/editor.js`, `tools/apply_suggestions.py`, `tools/direct_apply_daemon.py`
- **Approach:** add the operations as suggestion kinds (KTD3) with the same validation posture as
  today — server-resolved editor identity, allowlisted `source_ref`, size ceilings. An insert
  addresses "after `<bid>`" and mints a new `bid` at apply time. A delete is reversible by history
  like any other change. Teach the apply engine each operation against the source file.
- **Execution note:** structural ops **do not** take the auto-apply fast path on first release even
  though they are single-block (R6 covers *scope*, not risk). Ship them queued, watch one real
  session, then decide.
- **Test scenarios:**
  - Insert after a block: the new block gets a fresh `bid`; no other `bid` changes; the paragraph
    appears in the right place in the source file and on the page.
  - Delete: the block leaves the page and the source; history retains it; undo restores it with its
    original `bid`.
  - Split and merge preserve total text exactly (assert on concatenation).
  - Move reorders without changing any `bid`.
  - Two structural ops from different editors on the same file do not corrupt it.
  - Every operation is attributed and appears in history like a text edit.
- **Verification:** a full add → edit → move → delete → undo cycle through the browser on DEV.

### U5. The facts surface, and Stage 1.5 propagation

- **Goal:** Stage 1 and Stage 1.5 of the flow — facts are edited on their own surface, and changes
  propagate legibly.
- **Requirements:** R3
- **Dependencies:** U2, U3; the propagation batch reuses U7's pipeline, so U5's Stage 1.5 half lands
  with or after U7
- **Files:** `app/worker/src/editor-endpoints.js`, `app/editor/editor.js`, `tools/build_site.py`,
  `app/worker/src/editor-admin.js` (a facts entry point per matter)
- **Approach:**
  1. **Stage 1 (the facts panel, KTD7):** render each matter's facts from its JSON + schema as a
     labelled panel — label, current value, where-used count — reachable from the matter's pages and
     the editor landing. Editing a value is a `json_scalar` suggestion exactly as today. **Adding a
     fact** appends a schema-valid key via a new `json_add` suggestion kind (same validation
     posture; schema decides what may be added and of what type).
  2. **Stage 1.5 (propagation, KTD6):** on accept of a fact change, enumerate derived sites (report
     only — rebuild handles them) and search the corpus for restated matches of the old value; draft
     the restated edits as one `group_id` batch with mechanical instructions; present the redline.
     Approving applies the batch; declining leaves prose untouched and says so plainly.
  3. **New facts:** offer a drafted set of prose insertions mentioning the new fact (structural
     inserts under one group) — offered, never forced.
- **Test scenarios:**
  - The facts panel for a real matter lists the schema-derived fields with correct where-used counts.
  - A party-name change reports derived sites correctly, and a fact restated in a narrative is
    classed restated, not derived.
  - The Stage 1.5 batch drafts one edit per restated site, applies atomically, undoes in one gesture.
  - Declining the batch leaves the derived change applied and the prose untouched — stated plainly,
    completion never implied.
  - `json_add` accepts a schema-valid new key and rejects a schema-invalid one; IDs/join keys are
    not addable or editable from the panel.
  - A short-string fact (a common first name) does not flood the restated search with false
    positives — matching is value+context, and the review screen makes over-matching cheap to reject.
- **Verification:** on a real matter, the counts match a hand audit; one fact edit round-trips
  through Stage 1 → 1.5 on DEV.

### U6. The scope ladder and deterministic enumeration

- **Goal:** name a scope, get an exact, reviewable list of blocks — before any model runs.
- **Requirements:** R4, KD2
- **Dependencies:** U2, U3
- **Files:** `app/worker/src/editor-map.js`, `editor-endpoints.js`
- **Approach:** resolve **part / matter / module / course** to block sets from the map. Module is the
  dangerous one: it cuts across all 20 matters, so its enumeration must report matters touched,
  files touched and block count as a blast-radius summary.
- **Test scenarios:**
  - Each level returns the expected block count on a known matter.
  - Module scope spans all matters that carry that module's content.
  - Enumeration is deterministic across runs and cheap enough to show interactively.
- **Verification:** counts reconcile against the map totals.

### U7. AI-drafted scoped changes with redline review

- **Goal:** a natural-language request becomes a reviewable batch.
- **Requirements:** R5, R7, KD2, KTD5
- **Dependencies:** U6
- **Files:** `tools/editor_ai_rewrite.py`, `app/worker/src/editor-endpoints.js`,
  `app/editor/editor.js`
- **Approach:** three phases per KTD4 — enumerate (show blast radius, ask to proceed), draft (one
  proposed edit per matched block, `origin: ai_rewrite`, one `group_id`), review (redline; approve or
  reject as a unit). Enforce the ceiling and the canary from KTD5.
- **Test scenarios:**
  - "Change the filing deadline from 14 to 30 days" across a matter drafts only the blocks that
    mention it, and the blast radius shown beforehand matches what is drafted.
  - A course-scoped request above the ceiling refuses to draft without explicit confirmation.
  - Approving applies every member atomically; a mid-batch failure leaves none applied.
  - One undo reverts the whole group (R7).
  - A drafted batch is never auto-accepted — `origin: ai_rewrite` already forbids it; assert it.
  - The canary matter applies and the remainder waits until it is verified.
- **Verification:** a real scoped change on DEV, reviewed and undone cleanly.

### U8. Gating, and the affordances for an 83-year-old

- **Goal:** the fast path stays fast, the wide path is gated, and both are obvious on screen.
- **Requirements:** R6, R9, R10
- **Dependencies:** U4, U7
- **Files:** `app/editor/editor.js`, `app/editor/editor.css`, `app/worker/src/editor-endpoints.js`
- **Approach:** single-block human edits keep today's auto-apply; anything carrying a scope waits for
  admin approval (KD3). Add plainly-labelled affordances for adding and deleting a paragraph, with
  the same honesty the existing status pills have, and a visible undo.
- **Test scenarios:**
  - A line edit still goes live in ~2 minutes with no review.
  - A scoped change never auto-applies, even when it matches one block.
  - The add/delete affordances meet the existing a11y gate at 0 FAIL and survive the headful
    editor-client gate.
- **Verification:** headful UAT; the a11y audit stays at 0 FAIL.

### U9. Tell them what changed

- **Goal:** the guide matches the tool.
- **Requirements:** R9
- **Dependencies:** U4–U8
- **Files:** `docs/editor-guide-for-john.md`, `docs/prod-enable.md`, `RESUME.md`
- **Approach:** rewrite the guide around the new verbs in the same plain register as today's —
  adding and removing paragraphs, and asking for a bigger change in your own words. State plainly
  that a bigger change goes to Damien first, so the absence of an instant update is expected rather
  than alarming.
- **Verification:** a reader following only the guide can add a paragraph and request a scoped
  change without prior context.

---

## Verification Contract

- `bash tools/preflight.sh` — 9 gates, 0 skipped, including the headful editor client and the a11y
  audit at 0 FAIL.
- The U2 equivalence test and the map-stability test pass at every step.
- Live on DEV: add → edit → move → delete → undo through the browser; a scoped change enumerated,
  drafted, reviewed, applied and undone as one unit; a line edit still auto-applying.
- Both trees clean; merge to `main` from `~/.local/share/sonsteng-daemon/checkout` under
  `flock .locks/daemon.lock`.
- **Do not forget:** regenerate the sources before deploying —
  `python3 tools/build_site.py --check && python3 tools/build_instructor_bundle.py`. The bundler runs
  automatically via wrangler's `build.command`; it is the inputs that go stale.

## Definition of Done

R1–R10 hold; preflight 9/9; no pre-existing block drifted; a scoped change proven reversible in one
gesture; and John's guide describes the new verbs.

---

## Risks & Dependencies

- **The identity migration (U2) is the highest-stakes step in this plan.** It rewrites every editable
  source file. Mitigated by an equivalence test written first, an empty queue, and a rendered-output
  diff that must be empty.
- **Module scope is a refactor** (KD2, Damien's own framing). A single request can touch all 20
  matters. Mitigated by blast radius before drafting, ceilings, the canary matter, atomic groups and
  one-gesture undo.
- **Restated facts are the silent failure** (KTD6). Changing the JSON and missing the prose leaves a
  scenario that contradicts itself — worse than not editing at all, because it looks finished.
  Mitigated by making propagation a first-class stage (1.5) rather than a footnote on fact edits,
  and by never implying completeness while restated edits sit undecided.
- **Restated-search over- and under-matching.** Searching for the old literal misses paraphrases
  ("two weeks" for a 14-day deadline) and over-matches short values. Under-matching is mitigated but
  not eliminated by value+context search — the plan does not promise to find paraphrases, and the
  review screen says the list is "mentions found", not "all mentions". Over-matching is cheap to
  reject in review.
- **Structural ops meeting the apply engine.** Insert and delete change file shape, not just content.
  Mitigated by teaching one operation at a time with round-trip tests, and by not auto-applying them
  initially.
- **Scale of the whole.** This is materially larger than the Access door. U1–U4 are the spine; U5–U8
  are independently shippable behind it. If the Aug 3 walkthrough approaches with work outstanding,
  ship U1–U4 and defer the scoped-change machinery — line editing plus structural editing already
  delivers "like a Word document."

## Open Questions

- **OQ1.** Ceiling for a scoped batch before it demands explicit confirmation (KTD5) — a number, once
  U6 reports real enumeration counts.
- **OQ2.** Should a *part*-scoped change (e.g. every persona in one matter) also be gated, or is
  gating only worthwhile from matter scope upward? R6 says gate anything scoped; confirm that is not
  too heavy for the smallest scope.
- **OQ3.** Does the markdown ID marker belong in the file, or in a sidecar index keyed by content
  position? In-file is stable under external edits, which is the point — but it does mean the
  curriculum source carries machine tokens. Settle in U1.
- **OQ4.** Where does the facts panel live — a per-matter facts page linked from the matter's pages
  and the admin landing (recommended), or inline panels on each page that renders the facts? Decide
  in U5; the recommended shape matches "edit the facts of the scenario" as one sitting.
- **OQ5.** When a new fact's prose insertions are declined, is the fact still added (recommended —
  it simply renders nowhere yet), or does the add wait until at least one mention exists?

## Sources & Research

- Origin: cockpit ask `sonsteng-2026-07-28-editing-scope`, answered 2026-07-28 (all five questions).
- Measured this session: no insert/delete/reorder verb exists in `editor-endpoints.js`; `source_ref`
  is ordinal (`…statement-osgard.md#p0/#p1/#p2`); the walker contract is index-based; `group_id`
  appears 21 times in `editor-store-core.js`; `skills/index.html` has 460 walker candidates and 0
  `data-ebsrc`; the editor map covers only `data/curriculum/` and `data/matters/` across 280 files.
- Superseded plan retained for its mechanism write-up:
  `docs/plans/2026-07-28-001-feat-editable-coverage-plan.md`.
