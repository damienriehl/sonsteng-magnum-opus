---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: session-decisions
title: "feat: Editable taxonomy and Publisher-controlled production batches"
date: 2026-08-09
type: feat
depth: deep
origin: ../briefs/qa/sonsteng-magnum-opus-2026-08-07-current-edit-and-publish-decisions-answers.json
supersedes: docs/plans/2026-08-05-001-feat-prod-editor-promotion-plan.md on origin/feat/prod-editor-promotion
---

# feat: Editable taxonomy and Publisher-controlled production batches

**Target repo:** sonsteng-magnum-opus  
**Implementation branch:** `feat/taxonomy-publisher-batches`, based on current `origin/main`

## Goal Capsule

Let John edit every human-readable skill-taxonomy label through the same plain-language Editor he
already uses, without exposing IDs or crosswalk structure. Keep approval and publication separate:
approved changes remain queued until a human Publisher reviews an immutable batch and explicitly
releases it to production, after which automation may build, deploy, verify, audit, and recover that
exact batch.

## Product Contract

### Summary

This plan reconciles Damien's two August 8 decisions with the code now on `main` and with the
unmerged production-promotion work. It extends the existing generated editor map for taxonomy
wording and selectively ports the dormant branch's durable preview, ledger, manifest, verification,
and restoration primitives. It explicitly removes confidence-based or timed automatic publication.

### Problem Frame

Current `main` includes the catalog and editor-UAT fixes from PRs #9 and #10, but it still exposes
only task names and descriptions from the taxonomy. Skill names, alternate names, and subtask names
and descriptions are deliberately read-only. Worse, the taxonomy generator can rewrite the JSON
that the Editor would patch, so a later regeneration could silently erase John's wording.

The existing DEV direct-apply lane also conflates approval with execution. Eligible human edits
become `accepted`, and the daemon sweeps accepted rows into canonical content and DEV. Production
has only an imperative deployment script; it has no immutable batch membership, distinct Publisher
authorization, exact preview, coordinated Pages/Worker release, or release receipt.

A dormant branch contains substantial production-promotion machinery, but its product contract
auto-publishes high-confidence candidates and promises a five-minute automatic lane. That policy is
superseded. The branch is implementation evidence and a selective source of proven primitives, not
a merge target.

### Actors

- **Editor:** John or another human who proposes wording changes.
- **AI Editor:** Drafts suggestions through the same allowlist and review store, never authorizes a
  release.
- **Approver:** Accepts or declines suggestions; acceptance does not publish.
- **Publisher:** A human with an independent `publisher` scope who freezes and authorizes an exact
  production batch.
- **Release executor:** Automation that may execute only an already-authorized batch.
- **Administrator:** Operates and recovers the lane but gains no implicit Publisher authority.

### Requirements

- **R1. All human-readable taxonomy text is editable.** This includes taxonomy introductions,
  skill names, alternate names, task names/descriptions, and subtask names/descriptions.
- **R2. Machine identity remains locked.** Schema/spine versions, IDs, `@id`, joins, FOLIO IRIs and
  crosswalk keys, categories, modules, Bloom values, survey values, and structural flags never
  become editor-map leaves.
- **R3. Authored leaves stay leaf-pure.** Each editable scalar renders in its own walker candidate;
  generated framing, chips, IDs, and punctuation are outside the authored leaf.
- **R4. Generator regeneration preserves edited prose.** Structural taxonomy regeneration is keyed
  by immutable identity and cannot overwrite existing editable wording; new records receive seed
  wording deliberately.
- **R5. Browser and AI Editor have action/context parity.** They discover the same taxonomy fields,
  use the same `source_ref`, original hash, limits, review queue, and audit model. AI-origin changes
  remain review-required even when direct apply is enabled.
- **R6. Approval is not production publication.** Preserve the existing accepted→applied canonical
  and DEV lifecycle so John can verify approved wording promptly. DEV-applied content then remains
  “Available on DEV — waiting for Publisher” for production. The production executor cannot deploy
  it to PROD or mark it production-published until a Publisher authorizes its immutable batch.
- **R7. A Publisher explicitly authorizes an immutable batch.** The authorization binds human
  identity and a contiguous sequence of complete canonical DEV apply batches from the last verified
  production frontier through a chosen target. No apply batch may be split or skipped. Authorization
  binds every enclosed suggestion/group, canonical commit identity, production base SHA, candidate SHA,
  evidence, release manifest, target environment, and timestamp.
- **R8. Publisher authority is distinct and human-only.** Editor, instructor, approver, admin-only,
  service bearer, and AI paths cannot authorize or alter production batch membership. A person may
  hold multiple scopes, but each action records the scope used and actor separately.
- **R9. Automation may execute but not enlarge authorization.** The production executor claims only
  the frozen batch, rejects stale or invalid membership, serializes releases, and never sweeps
  unrelated accepted or DEV-applied rows. The DEV daemon retains accepted-row application.
- **R10. Pages and the production Worker/map release as one recoverable manifest.** Publication
  builds both from the same canonical SHA, proves compatibility, records provider identifiers and
  provenance, and finalizes only after both live surfaces match.
- **R11. Fail closed and recover exactly.** Any ambiguity, stale base, dirty daemon checkout,
  partial deployment, provenance mismatch, or failed live check fences later releases and resumes or
  restores toward the recorded release—not toward ambient HEAD.
- **R12. Every transition is durable and attributable.** Suggestion approval, Publisher
  authorization, execution phases, failures, restoration, completion, and reverts remain auditable
  without leaking edited content into notifications.
- **R13. Direct production bypasses are disabled or guarded.** The imperative deploy script,
  production configuration, branch writers, and scheduled jobs cannot silently publish outside the
  release ledger.
- **R14. Real gates decide readiness.** Unit and generated-artifact checks must be supplemented by
  John-like browser editing, Publisher release UAT, and live Pages/Worker provenance verification on
  the real box.
- **R15. Editable wording is inert content.** Taxonomy leaves accept normalized plain text only and
  are contextually escaped everywhere they render. HTML/script, event-handler, unsafe URL-scheme,
  bidi-control, and markup-breaking payloads remain inert in public Pages, Editor/Publisher previews,
  history, audit, and notification surfaces.
- **R16. Deployment credentials remain least-privilege operational secrets.** DEV and PROD
  credentials are separate, live outside source control and release manifests, grant only required
  provider actions, are redacted from commands/journals/receipts/notifications, and have a documented
  rotation, revocation, and post-rotation verification path.

### Session-Settled Decisions

- **Editable taxonomy:** Damien chose “Make all human-readable taxonomy text editable” over keeping
  skill labels read-only or introducing a narrower display-label layer (2026-08-08).
- **Production trigger:** Damien chose “A Publisher explicitly releases an approved batch” over
  immediate, scheduled, or confidence-based automatic production publication (2026-08-08).
- **Role separation:** Author, Approver, Publisher, Instructor, and Admin remain distinct actions;
  one person may hold multiple roles, but the audit must preserve which role acted.
- **Live-gate policy:** Production claims require real-system evidence; green mocks alone are not a
  release gate.

### Acceptance Examples

- **AE1 — taxonomy coverage:** A skill name, alternate name, task name/description, and subtask
  name/description each offer Edit, save through the real Worker, reach DEV, and retain their
  immutable IDs and crosswalks.
- **AE2 — regeneration:** After John edits a taxonomy leaf, running the authoritative taxonomy
  generator preserves the wording byte-for-byte while structural IDs and crosswalks remain equal.
- **AE3 — locked identity canary:** A forged suggestion targeting an ID or FOLIO path is rejected by
  human and AI endpoints; a deliberately allowlisted wording leaf is accepted by both, proving the
  gate can fail and pass.
- **AE4 — approval separation:** An Approver accepts wording; the DEV daemon applies it to canonical
  content and DEV. It remains absent from public PROD and no production claim occurs until a
  Publisher acts.
- **AE5 — immutable authorization:** A Publisher sees the exact diff, attribution, blast radius,
  candidate/base identity, and manifest, then authorizes it. Reusing the request is idempotent;
  changing membership under the same authorization is rejected.
- **AE6 — least authority:** Editor, approver-only, admin-only, AI, and service credentials cannot
  authorize a batch. Service credentials may execute an already-authorized batch.
- **AE7 — frozen membership:** An edit accepted after authorization remains queued and does not join
  the active release. Partial atomic groups and stale/superseded members are rejected.
- **AE8 — coordinated success:** The authorized batch builds from an isolated clean worktree,
  releases Pages and the production Worker/map from one SHA, passes live checks, records a receipt,
  and only then marks its suggestions applied.
- **AE9 — partial failure:** If one production target succeeds and the other fails, the batch stays
  recoverable/fenced, later batches do not run, and reconciliation proceeds toward its recorded SHA.
- **AE10 — real-user journey:** In a clean browser John can edit taxonomy wording but not identity;
  Damien can approve, explicitly publish the immutable batch, and verify the public copy and editor
  map agree.

### Scope Boundaries

**In scope:** taxonomy wording coverage and generator preservation; source-ref migration safety;
human/AI parity; Publisher auth and UI; immutable release batches; selective port of proven promotion
primitives; coordinated Pages/Worker deployment, audit, recovery, docs, and live UAT.

**Out of scope:** editing taxonomy IDs or externally canonical FOLIO labels; autonomous AI release
authority; confidence scoring as publication authority; scheduled publication; two-person release
approval; parallel production releases; unrelated structural-editor work.

## Planning Contract

### Assumptions

- Current `origin/main` is the integration base. The old promotion branch predates PRs #9/#10 and
  must not be merged wholesale because it would overwrite newer identity, catalog, and editor work.
- The dormant branch's ledger, candidate builder, bound preview, release manifest, fencing,
  verification, restoration, UI, and test harnesses are reusable after product-policy review.
- Its bounded-AI risk authority, automatic publication transitions, five-minute publication promise,
  and measured auto-rollout are obsolete and will not be ported.
- Existing `apply_batch_id` is a useful join but not sufficient as release authorization; execution
  journaling and human authorization need distinct durable records or append-only phases.
- Changing the skills-page walker reindexes blocks. Migration must either require a proven empty
  active queue or reproject unresolved suggestions by durable `source_ref`; it may not silently drift
  pending work.
- Deployment order between Pages and Worker will be chosen only after a compatibility gate proves
  which old/new map pairings are safe. No order is assumed in advance.
- The daemon's dedicated checkout and live production configuration must be inspected before any
  migration or release enablement.

### Key Technical Decisions

- **KTD1 — Explicit taxonomy field allowlist.** Register only named human-readable scalar paths;
  do not infer editability by JSON type. This governs R1-R5.
- **KTD2 — Leaf-pure DOM plus mirrored walker contract.** Skill summaries, alternate names, and
  subtasks render their authored values separately from generated metadata. Any walker change is
  mirrored in generator, injector, client, map bundle, and contract tests. This governs R1-R3.
- **KTD3 — Stabilize identity, then preserve wording by immutable ID.** Existing positional task and
  subtask IDs first become literal generator inputs and generation rejects missing, duplicate, or
  reassigned IDs. Only then may authored wording be overlaid by stable ID. This governs R2 and R4.
- **KTD4 — Separate DEV suggestion and PROD release lifecycles.** Suggestions move through review,
  canonical application, and DEV states;
  releases separately move through draft, authorized, executing, deployed, verified, complete, or
  failed/restored states. Neither accepted nor DEV-applied state alone is production authority; a
  release records the exact applied suggestions and canonical commits it promotes. This governs
  R6-R9 and R12.
- **KTD4a — Production advances through a contiguous canonical frontier.** The minimum selectable
  membership grain is a complete canonical apply batch. A release covers every apply batch after the
  last verified PROD frontier through its chosen target, rejecting gaps, partial groups/batches,
  non-ancestor targets, changed generator identity, or any tree that does not reproduce the recorded
  candidate SHA. This is chosen over cherry-picking individual suggestions, which can hide commit
  dependencies or include unselected edits.
- **KTD5 — Human Publisher authorization is a cryptographically/contextually bound event.** Require
  current human Access identity, independent `publisher` scope, same-origin CSRF defense, exact
  immutable membership and release evidence. Bearers may execute but not authorize. This governs
  R7-R9.
- **KTD6 — Selective port, not branch merge.** Recover the minimum known-good promotion primitives
  commit-by-commit or file-by-file onto current main, with characterization tests before policy
  changes. This prevents old auto-publish semantics and stale generated files from returning.
- **KTD7 — Release manifest is the recovery authority.** Candidate/base SHA, Pages artifact, Worker
  bundle/map, evidence hashes, provider IDs, and phase journal determine retry/restoration. Ambient
  HEAD and local generated state do not. This governs R10-R13.
- **KTD8 — Positive canaries accompany every absence gate.** Editable coverage, locked identity,
  auth denial, bypass prevention, artifact parity, and live verification tests must demonstrate
  catch power, not merely assert empty results. This governs R2, R8, R11, R13-R14.
- **KTD9 — Recovery cannot mint new publication authority.** Automatic retry/reconciliation may
  operate only on the active batch's frozen manifest. A manual retry of that exact manifest requires
  an attributed operator action; any restore/revert to a different production manifest requires a
  fresh human Publisher authorization bound to that target. Fence/pause actions may be performed by
  Admin because they reduce authority; fence overrides may not publish content. This governs R8,
  R11-R12.

### System-Wide Impact

- **Data:** `data/taxonomy/*.json` and `_build_taxonomy.py` share explicit structural/authored
  ownership; IDs remain durable.
- **Site generator:** taxonomy markup and editor-map annotations expand; generated site, semantic
  baseline, personas/instructor bundles, and map parity may change.
- **Worker:** auth gains Publisher scope; store/API gain release records and transitions; editor map
  and client walker stay synchronized.
- **Editor/Admin UI:** accepted wording changes status; Publisher receives a distinct immutable batch
  review/release surface; history exposes release receipts and recovery state.
- **Daemons/deploy:** accepted-row sweeping ends for production; the executor consumes authorized
  batches, coordinates Pages/Worker, and uses the dedicated worktree under flock.
- **Operations:** competing production writers are inventoried and disabled; rollout begins
  config-off, then real supervised canary, then normal explicit-Publisher operation.

## Implementation Units

### U1. Baseline, migration fence, and stale-contract correction

**Goal:** Establish live/current truth before changing persisted identity or publication authority.

**Changes:**

1. Characterize current taxonomy counts, editor-map paths, pending/accepted queue state, daemon
   checkout cleanliness, production Worker variables, Pages deployment source, and all production
   writers.
2. Add `.worktrees/` to the branch's ignore rules without deleting or modifying live worktrees.
3. Add characterization tests for current direct-apply behavior and dormant promotion primitives.
4. Mark the old automatic-promotion plan and stale docs as superseded by this plan; preserve them as
   history rather than rewriting their recorded decisions.
5. Choose and prove the walker/source-ref migration route: empty-queue fence or deterministic
   reprojection of unresolved rows.

**Tests / evidence:** exact queue audit, daemon checkout audit, environment/config snapshot without
secrets, current generated-map parity, and a red characterization showing approval currently permits
automatic claim.

**Depends on:** none. **Governs:** R6, R11, R13-R14; AE4.

### U2. Editable taxonomy leaves with generator preservation

**Goal:** Expose every human-readable taxonomy scalar without exposing identity or losing wording on
regeneration.

**Changes:**

1. Check in a field inventory for every human-readable taxonomy string and source, including
   document descriptions, page introductions/headings, no-FOLIO notes, skill/alternate names,
   tasks, and subtasks; explicitly mark external FOLIO and structural/numeric labels locked.
2. Migrate generator inputs to carry every current task and subtask ID literally; preserve today's
   IDs byte-for-byte and reject missing, duplicate, or reassigned identities.
3. Define the authoritative allowed field families in the generator/map contract.
4. Refactor the skills page into leaf-pure authored nodes: skill summary wording, optional alternate
   name, task text, and separate subtask name/description, with locked chips outside candidates.
5. Enforce normalized plain-text input and contextual output escaping across public HTML, editor and
   Publisher redlines, history/audit, and notification renderers; add hostile-payload canaries.
6. Mirror any candidate-tag/walker change across generator, Worker injector, browser client, bundled
   editor data, and tests.
7. Update `_build_taxonomy.py` to preserve existing human-readable values by immutable record IDs
   while regenerating structural data and crosswalks.
8. Rebuild the site, editor map, semantic baseline, persona/instructor bundles, and Worker data only
   through authoritative generators.

**Tests / evidence:** exact coverage calculation for all current allowed fields; exact exclusion of
every locked field family; human/AI allowlist parity; generator round-trip preservation; reorder/add
fixtures; source-ref migration proof; browser edit of every leaf kind; `check_build_parity.py` and a
clean tree after rebuild.

**Depends on:** U1. **Governs:** R1-R5; AE1-AE3.

### U3. Durable Publisher authorization and immutable batch API

**Goal:** Make publication authority a separate human action over a frozen batch.

**Changes:**

1. Selectively port the promotion ledger, append-only events, candidate/evidence records, and bound
   preview primitives onto current main, excluding auto-authority code.
2. Add independent `publisher` scope and credential-channel awareness. Require a current human
   Access identity and CSRF-protected same-origin request to authorize production.
3. Build release candidates as a contiguous sequence of complete canonical apply batches after the
   last verified PROD frontier through a Publisher-chosen target. Freeze all enclosed suggestions,
   groups and commits plus production base/candidate SHA, generator identity, evidence and manifest;
   attribute approver separately from Publisher.
4. Make authorization idempotent for the same payload and reject mutation, replay with different
   content, stale members, partial groups, wrong target, and unauthorized actors.
5. Expose machine-readable release status and audit primitives through the same canonical store.

**Tests / evidence:** request-level auth matrix; bearer-execution/human-authorization distinction;
membership and group invariants; stale/replay/idempotency tests; wrapper forwarding tests for every
new Durable Object method; migration rollback on a production-like store copy.

**Depends on:** U1. **Governs:** R6-R9, R12; AE4-AE7.

### U4. Publisher review/release experience and truthful statuses

**Goal:** Give Damien a clear, accessible “review exact batch → release” journey while keeping John
informed about approved-but-unpublished work.

**Changes:**

1. Preserve truthful DEV states: accepted means approved/queued for DEV and applied means available
   on DEV. Add a separate production status, “Available on DEV — waiting for Publisher.”
2. Add a distinct Publisher surface showing immutable membership, per-change attribution/redline,
   blast radius, base/candidate identity, gate evidence, manifest, and active-lane conflicts.
3. Make entry explicit from the review/history navigation with an eligible-change count. The flow is
   choose the end of the next contiguous apply-batch frontier → disclose every enclosed change/group
   → generate immutable preview
   → review → confirm or cancel. Define empty, stale, already-claimed, and active-release outcomes.
4. Require an explicit production release gesture bound visibly to target, batch ID, count, expanded
   groups, and base/candidate identity. Disable duplicate submission while pending and make replay,
   stale-preview, conflict, denial, success, focus restoration, and status announcement explicit.
   Do not let preview, approval, page load, timers,
   AI results, or service credentials trigger authorization.
5. Add a state/action matrix for draft, authorized, executing, delayed, failed/fenced, restoring,
   restored, verified, and complete: explanation, evidence/timestamps, permitted actor and controls,
   disabled release actions, and terminal destination.
6. At phone widths stack each change in semantic before/after order without color dependence; put
   batch identity/consequence before the release control and secondary evidence in native disclosure.
7. Keep keyboard, screen-reader, large-type, phone-width, focus restoration, and no-JS safety aligned
   with existing editor accessibility contracts.

**Tests / evidence:** generated markup semantics; real-browser editor and Publisher flows; narrow and
large-type matrix; denied-role UI/API consistency; approval-without-production canary.

**Depends on:** U3. **Governs:** R6-R8, R12, R14; AE4-AE7, AE10.

### U5. Authorized-batch executor and coordinated production saga

**Goal:** Execute only frozen authorized work and make Pages/Worker publication recoverable.

**Changes:**

1. Port the candidate builder, immutable manifest, serialized lease/fencing, live verifier,
   restoration, and reconciliation scaffolding from the dormant branch; remove risk/AI auto-publish
   transitions and timing promises.
2. Keep `direct_apply_daemon.py` responsible for accepted→canonical→DEV. Give the production daemon a
   separate entry point that claims only authorized release batches and their frozen applied
   IDs/commits; preserve dirty-tree protection, isolated worktrees, flock, validation, and journals.
3. Build site, editor map, Worker bundle, persona/instructor artifacts, and history from the recorded
   base/candidate; prove post-build byte parity and clean tracked state.
4. Add a compatibility gate that chooses/permits deployment order only when the transient old/new
   Pages/Worker pairing is safe.
5. Implement concrete ledger-HTTP, git/ref, Cloudflare Pages upload/deployment, Worker
   version/activation, and live-provenance adapters. Make each idempotent and reconcile ambiguous
   provider responses without guessing.
6. Deploy both production targets, verify live provenance and editor-map agreement, finalize the
   production release, and mark its DEV-applied suggestions production-published exactly once.
   Canonical `main` may contain later DEV work and is not rewritten to pretend it equals PROD.
7. On crash or partial failure, resume toward the recorded SHA or restore the last known-good exact
   pair; fence later batches until reconciliation is proven.

**Tests / evidence:** accepted-without-authorization no-op; exact frozen membership; concurrency and
lease tests; crashes before/after merge and each deploy; Pages-only and Worker-only failure drills;
provenance mismatch; restore failure fence; idempotent finalization; DEV health invariant; multi-edit
apply commits, later dependency commits, partial/gapped selection rejection, and exact tree replay.

**Depends on:** U3. **Governs:** R6-R13; AE5-AE9. U2 proceeds in parallel and converges in U6.

### U6. Bypass closure, operations, documentation, and live UAT

**Goal:** Enable the explicit-Publisher lane safely and prove it through John's and Damien's real
journeys.

**Changes:**

1. Inventory and disable or ledger-gate `deploy-prod.sh`, automatic Pages deploys, direct branch
   writers, cron/systemd jobs, and production `DIRECT_APPLY` settings that can bypass authorization.
2. Inventory credentials consumed by the DEV/PROD Editor and release lane; enforce separate
   least-privilege DEV/PROD
   credentials outside tracked state and manifests, redact all evidence surfaces, and document/test
   rotation and emergency revocation.
3. Install/update the executor in the daemon's dedicated worktree under its existing flock; keep the
   feature config-off until migrations, bootstrap known-good manifest, and recovery drills pass.
4. Update API contracts, editor guide, direct-apply runbook, PROD enablement docs, decision records,
   and UAT scripts so approval, DEV visibility, and PROD publication are never conflated.
5. Run a clean-browser John flow for each taxonomy leaf kind and locked metadata; run a human
   Publisher flow from a DEV-applied batch through public verification and audit.
6. Run a supervised low-risk production canary and rollback/reconciliation drill. Verify anonymous
   public Pages content and authenticated production editor map carry matching provenance.
7. Record any genuinely unresolved product choice in a new Decision Sheet; implementation facts are
   resolved in code/tests and are not re-asked of Damien.

**Tests / evidence:** full real-box preflight with trusted exit codes and working XAUTHORITY; Worker
suite; Python suite; generated parity; semantic and leak sweeps; editor browser verifier; production
release/restore drill; live public and editor provenance; clean git status after every generator and
deployment preparation step.

**Depends on:** U4, U5. **Governs:** R10-R14; AE8-AE10.

## Verification Contract

Run focused red/green tests per unit, then the repository's full real-box gates. Exact commands may
be adjusted to current scripts, but the evidence classes are mandatory:

| Gate | Required proof |
|---|---|
| Taxonomy contracts | Every allowed current leaf appears exactly as declared; every locked family is absent; mutation canaries prove both sides |
| Generator stability | Edited wording survives regeneration; IDs and crosswalks are unchanged |
| Editor parity | Browser and AI endpoints accept/reject the same refs and hashes; AI stays review-required |
| Publisher authorization | Human Publisher succeeds; every non-Publisher and service authorization path fails closed |
| Batch immutability | Membership, base, evidence, manifest, target, and actor cannot change after authorization |
| Executor recovery | No unapproved claim; exact-ID execution; crash/partial-deploy/live-mismatch drills reconcile or fence |
| Artifact parity | Site, editor map, Worker bundle, instructor/persona/history artifacts share one release identity; rebuild leaves tracked tree clean |
| Security/leak sweep | No instructor keys, markers, answer keys, credentials, or edited content in public/log/notification surfaces |
| Browser/a11y | Real headful editor and Publisher flows, phone width, large type, keyboard, screen reader semantics, trusted exit code |
| Live UAT | John-like edit reaches DEV; approval stays out of PROD; explicit Publisher release updates public Pages and matching editor map; restoration drill passes |

The final test run must occur on the real box. A sandbox browser-launch failure is environmental, not
product evidence; a skipped live gate is not a pass.

## Definition of Done

- Every currently authored human-readable taxonomy field is editable through John’s real editor and
  survives authoritative regeneration; identifiers, joins, and canonical crosswalk metadata remain
  locked.
- Human and AI editing share discovery, validation, review, attribution, and stale-source semantics;
  AI suggestions never gain publication authority.
- Accepted suggestions do not execute or reach production without an immutable, human-Publisher-
  authorized batch.
- Only the exact authorized membership can be built and deployed, and Pages plus the production
  Worker/map are verified as one release before completion.
- Crashes, partial deployments, stale bases, live mismatches, and restoration failures leave durable
  evidence and recover or fence without publishing unrelated work.
- Direct production bypasses are disabled or forced through the release ledger.
- Docs and UI distinguish saved, approved, waiting for Publisher, publishing, published, and failed.
- Focused and full suites pass; generated artifacts are rebuilt and parity-checked; real headful UAT,
  live production canary, and recovery drill pass with recorded evidence.
- The branch is clean, committed in coherent units, pushed, reviewed, and landed without modifying
  privileged/out-of-scope directories or secrets.

## Sources and Research

- `briefs/qa-state.json` — Damien's August 8 settled answers.
- `docs/decisions/2026-08-07-editor-uat-decision-sheet.md` — prior decision framing, superseded where
  the answers now settle it.
- `docs/plans/2026-07-28-002-feat-word-like-practicum-editing-plan.md` — editor-map and durable-source
  contract.
- `origin/feat/prod-editor-promotion` and its historical plan — reusable implementation evidence,
  with automatic-publication policy explicitly rejected.
- `docs/solutions/editor/2026-07-28-{checks-that-cannot-fail,generated-artifacts-are-not-tracked-state,durable-block-identity,headful-harness-needs-xauthority}.md`.
- `docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md` — live-gate requirement.
- Current-main code research across the site generator, taxonomy builder, editor map/injector/client,
  Worker auth/store/endpoints, direct-apply daemon, apply engine, deployment scripts, and tests.
