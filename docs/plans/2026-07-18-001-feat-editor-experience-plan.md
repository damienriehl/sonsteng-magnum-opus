---
title: "feat: Sonsteng Editor Experience — Worker-injected edit mode, Word-style comments, instructor view, value-synced apply loop"
type: feat
status: active
date: 2026-07-18
origin: docs/brainstorms/2026-07-18-editor-experience-brainstorm.md
---

# ✨ The Sonsteng Editor Experience

## Enhancement Summary (deepening pass, 2026-07-18)

Four reviewers (security, simplicity, frontend-races, data-integrity) + Damien's follow-up decisions. **Binding amendments:**

1. **The map is the universal allowlist (security P0):** `editor-map.generated.json` validates the proxy path, every `source_ref`, and every `json_path`, server-side, at suggest AND apply time — closing SSRF, arbitrary-file-write, and anchor forgery in one invariant. Full spec: `docs/research/editor-apply-spec.md`.
2. **API mounts under `/edit/v1/*`** (the `Path=/edit` cookie would never reach `/v1/…` — caught at plan stage). Cookie: `HttpOnly; Secure; SameSite=Strict; Path=/edit`; `?t=` → cookie exchange 302s to the clean URL; mutations require a custom header + Origin/Sec-Fetch-Site checks (CSRF layers); strict CSP incl. `base-uri 'self'`; ALL `/edit` responses `no-store` + `Vary: Cookie`; clean upstream subrequests (no cookie forwarding, strip upstream Set-Cookie, `redirect: manual`); pending items injected as an escaped JSON island, NEVER interpolated HTML; text-node-only rendering extended to decline notes + review diffs; opaque token → server-side scope record (independent rotation, uniform-404 + constant-time on instructor routes); retention purge + PII warning + global caps + admin-only digest.
3. **Editor-client race rules (all eight adopted):** blur = draft-only (three verbs: draft/commit/discard, one event each); Comment button captures the stored Range on mouseup + `mousedown`-`preventDefault` (tremor-debounced to last stable selection); `suggestion_id` minted once per edit-session, Save disabled synchronously pre-await; all same-origin links rewritten into `/edit/` space + capture-phase interception + banner on every page + Back/Forward stay in-proxy; 401-on-save preserves draft + id through the friendly re-auth; drafts keyed to `(source_ref, original_hash)` — hash moved → draft discarded with a gentle note, purged on applied; origin-page handlers neutralized in edit regions (capture-phase stopPropagation; scroll-spy/toggles silenced where they'd fight contenteditable); active buffer per-tab (sessionStorage), localStorage recovery-only, `visibilitychange`/`pageshow` re-poll of pending status. **Two named spikes before P3:** blur/Save semantics; selection capture.
4. **Simplifications adopted:** v1 formatting rule — `has_inline_formatting` blocks go `needs_human`, plain blocks apply directly (no merge algorithm; span-splice = labeled fast-follow); nth-editable-element document-order indices (no bespoke shared walk); per-block `original_hash` is the integrity boundary (map_version kept as a stored column, not a gate); sibling-grouping UI dropped (one editor + supersede = can't occur; state-machine edge stays dormant); P0 folded into P1 (the generator output IS the contract; normalization = one shared module); value-sync = exact-literal, structurally-scoped search (spec'd).
5. **Integrity architecture (adopted in full):** git-worktree transaction (canonical tree never dirty mid-apply; rollback = discard worktree), `in_flight` status + lease + `apply_batches` phase journal + startup reconciliation (no limbo states), all-or-nothing on validator RED (P1 bisect salvage), flock host-local + DO lease as the true mutex, **`spine_build_id` parity gate** across site/persona/instructor bundles (new `tools/spine_stamp.py` + `check_build_parity.py`) — the machine-checkable two-bundle staleness invariant.
6. **Damien's deepening decisions:** AI-rewrite proposals **in v1**, implemented in the apply-loop agent (this harness authors the rewrite — no API key required, inherently human-gated, same accept gate + text-node rendering); digest = **review page + Claude mentions pending items in normal sessions** (no cron; automated push only if ever missed).

## Overview

One magic link turns the real platform site into an editor for **Prof. John O. Sonsteng (84, Windows PC primary, iPad secondary, zero training)**: click a paragraph to edit it in place; highlight text to leave a Word-style comment. Everything is a **suggestion** — stored in a new Worker Durable Object, reviewed by Damien on a token-gated review page, applied to the **data-spine sources** by an apply engine that exact-matches, value-syncs into validator-owned JSON, runs the full validator, regenerates, and redeploys. A Worker-served **instructor view** exposes the back-of-house materials (facts, teaching notes, answer keys) for editing without ever touching the public static build. A **cumulative daily digest** tells Damien what's pending — days accumulate, one sweep reviews all.

**Companion decision:** [Midstate deferred](../decisions/2026-07-18-midstate-deferred.md) (brainstorm's other workstream — dropped for copyright avoidance; pivot path recorded).

## Problem Statement

The corpus is validator-gated, money-exact, and leak-swept — but its most important editor cannot use git, markdown, or a CMS. The system must accept his judgment (line edits + conceptual comments) through the most familiar surface possible — the site itself, with Word-like idioms — without ever letting free-text WYSIWYG bypass the spine's guarantees. SpecFlow's framing: *all the risk lives in the seam between a statically-generated relational spine and a free-text editing surface.* The plan's job is to make that seam a contract.

## Decisions (locked — brainstorm + planning Q&A with Damien)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Delivery architecture | **Worker-injected (A):** edit mode is served from the Worker as a wrapping proxy (`/edit/<path>?token`) that fetches the static page, injects editor JS + block stamps + pending suggestions, and serves. **Students' origins are untouched — zero editor bytes, zero source-path leakage in the public build.** No infra change to Pages/nginx. |
| 2 | Token scopes | **Two scopes, one bookmark:** `edit` scope (prose suggestions) and `instructor` scope (back-of-house) are distinct tokens server-side; John's single magic link carries both via one opaque token mapped to both scopes, but instructor routes enforce the stricter gate (tighter rate limits, independent rotation) so a leaked edit capability alone cannot dump answer keys. |
| 3 | Value-synced money/name edits | **The smart path (Damien):** (1) blocks rendered *from* JSON (ledgers, tables, KPI text) are stamped with their **JSON path** — editing them edits the JSON source directly; (2) values embedded in hand-authored prose get a **companion-edit proposer**: the apply engine detects money/date/party-token changes, finds the same value's other occurrences in the matter+firm spine, and proposes linked companion edits for one-accept syndication; (3) the **full validator** gates every apply, so divergence can never ship. |
| 4 | Comment UX | **Selection-first, Word-style** (John is Windows-primary — mouse selection is natural); per-block "Comment" affordance retained as the touch/tremor fallback; cross-block selections clamp to the starting block. |
| 5 | Change model | Suggestions only; statuses `pending / superseded / accepted / accepted_blocked / declined / drift / needs_human / applied`. Accept ≠ ship: apply is a transaction (below). |
| 6 | Review + digest | Token-gated `/edit/review` (Accept/Decline, drift resolution) — **the review page IS the cumulative digest** (all `pending`, any age, one sweep); Claude flags pending items during normal sessions; automated push deferred (deepening decision). |
| 7 | Editable v1 scope | All student-facing prose + pitch page + instructor markdown (facts.md, instructor-notes.md, answer-key.md). JSON-path-stamped scalars editable (Decision 3). Persona tier-facts, rubric structure, business tables' *structure* = **comment-only** (semantics too fragile for free text). Computed blocks (KPIs, TOCs, breadcrumbs, counters) = not editable, not commentable. |
| 8 | John's closure | His pending/accepted/declined statuses render inline in edit mode (with optional decline notes); "Sent ✓" confirmations; drafts autosave locally. |

## Technical Approach

### Architecture

```
JOHN (Windows Chrome/Edge; iPad secondary)
  bookmark → https://<worker>/edit/platform/matters/m03.../?t=<opaque-token>
      │ Worker: verify token (scopes) → fetch static page from DEV/PROD origin
      │        → inject <base>, editor.css/js, per-page BLOCK MAP, John's pending items
      ▼
  click block → contenteditable (plain text) → Save (keyboard-tracking bar) → POST /v1/edits/suggest
  select text → floating "Comment" → bubble → POST  (block-affordance fallback)
      │
      ▼                            DAMIEN
  EditorStore DO (SQLite) ◀──────  /edit/review?t=<admin> — grouped, diffed, Accept/Decline
      │  suggestions {id, editor, kind, source_ref, json_path?, page, original_text(hash),
      │               new_text|comment, context, status, timestamps, decision_note}
      ▼
  APPLY ENGINE (tools/apply_suggestions.py, agent-run, flock-locked)
      1 pull accepted → 2 patch source (exact-match → context-anchor → JSON-path write → drift/needs_human)
      3 value-sync proposer (companion edits) → 4 FULL validate_spine.py → RED = accepted_blocked + rollback
      5 build_site.py --check → 6 rebuild worker bundles (instructor + editor maps) → 7 deploy DEV (+Worker)
      8 mark applied → 9 word-level diff digest to Damien
```

### The round-trip contract (Phase 0 — everything hangs on this)

- **Editable-block allowlist**, emitted by the generator into `editor-map.generated.json` per page: `{block_anchor → {source_ref, kind: prose|json_scalar|comment_only, original_hash, json_path?}}`. Public HTML carries only bland sequential anchors (`data-eb="platform/matters/m03/:34"`)? **No — public HTML carries nothing**; the Worker injects anchors at serve time by walking the same deterministic DOM order the map was built from (generator and injector share the block-walk algorithm; a build-stamp version pins map↔page compatibility, mismatch = friendly "page just updated, reload").
- **`original_text` comes from the SOURCE, not the client** (SpecFlow critical #3): the suggestion carries the anchor + John's new plain text + a hash of the rendered text he saw; the Worker resolves `original_text` server-side from the bundled source extract. Inline formatting (bold/links/citations) is preserved by treating John's text as *plain-text intent*: the apply engine re-merges formatting where markers are unambiguous, else flags `needs_human` — **never silently strips formatting** (E7).
- **Normalization spec** both sides: NFC, whitespace-collapse, smart-quote/dash folding, `contenteditable` artifact stripping (`<div>/<br>` → newlines) before hashing/matching.
- **Idempotency & supersede:** client `suggestion_id` (uuid) dedupes double-saves (E8); a second edit by the same editor on the same `source_ref` supersedes the first (E9); `original_text` stays pinned to source.
- **Stable anchors across regenerates:** anchors derive from source refs (file + structural path), not rendered position, so rebuilds don't orphan pending items; the map version catches true drift.

### Worker additions (`app/worker/`)

- **`/edit/<path>` proxy-injector** (edit scope): fetch `EDIT_UPSTREAM` + path, inject; `Referrer-Policy: no-referrer`; token accepted once via `?t=` then moved to a session cookie scoped to `/edit` (keeps URLs clean, survives John's bookmark, revocable). Token scrubbed from all logs (mirrors `?bypass=`).
- **`/edit/instructor/<matter>/<doc>`** (instructor scope): serves pre-rendered instructor HTML from a new server-only `instructor-bundle.generated.json` (built by `tools/build_instructor_bundle.py`; **never in personas.generated.json, never in the static build** — the public leak-sweep guarantee is unchanged; note updates the "never bundle instructor content" rule to "never in the *persona/chat* bundle").
- **`/v1/edits/suggest|pending|decide|digest`** on the EditorStore DO (new class, `migrations` tag v2 append): size cap 16KB (graceful large-type 413), per-token rate limit + pending ceiling (mint-throttle pattern), timingSafeEqual token checks, CORS-on-every-response, **text-node-only rendering of every echoed suggestion everywhere** — review page, inline pending, AI-rewrite echoes (E14).
- **`/edit/review`** (admin scope): grouped by source_ref (accepting one sibling auto-declines the rest — E-series #8), word-level diffs, drift items with re-anchor/decline/ask-John actions, bulk accept, plain-language everything.
- **AI-rewrite proposals:** accepting a *conceptual comment* spawns (human-gated, never editor-triggerable) an agent rewrite that lands as a new `pending` suggestion attributed "AI (from JOS comment)".

### Editor client (`app/editor/editor.js` — served ONLY by the Worker injector)

Selection-first commenting (Windows mouse), block-affordance fallback (iPad/tremor); word-labeled affordances ("Edit", "Comment", "Save my change", "Cancel", "Sent ✓") — no bare icons; persistent banner "You're editing — changes go to Damien for review"; keyboard-tracking save bar (`visualViewport`); autocorrect preview before submit; drafts autosave per-block (localStorage) + `pageshow`/bfcache recovery (reuse chat's fix); large-type mode honored; minimal motion; every error in plain large-type language ("Your link expired — text Damien for a new one"), never a raw 4xx. Practicum-Press styled per `docs/research/design-direction.md` primitives.

### Apply engine (`tools/apply_suggestions.py`)

Transaction per batch: `flock` (guards any concurrent automation) → work on a scratch git branch → patch sources (prose exact-match → context-anchor → JSON-path write for `json_scalar` blocks) → **value-sync proposer** (changed money/date/party tokens grepped across matter+firm; companions become linked suggestions unless Damien pre-accepted the group) → full `validate_spine.py` (RED → `accepted_blocked` + validator report + rollback, E12) → `build_site.py --check` → `build_worker_personas.py` + `build_instructor_bundle.py` → deploy site + Worker → merge branch → mark `applied` → word-diff digest. Ambiguous matches → `needs_human` (E11); post-accept source changes → `drift` (E10).

### Pitch page

Hand-authored HTML: gets a hand-built block map (section anchors) + an HTML-direct apply path (no regenerate) — included in v1 since the injector architecture makes it uniform at serve time (E16).

## System-Wide Impact

- **Interaction graph:** editor → Worker injector → static origin (fetch) + EditorStore DO; apply engine → spine sources → validator → generator → two deploy targets (site, Worker bundles). The Worker gains its second DO class (migration append — never rename/remove the v1 migration).
- **Error propagation:** all editor errors land as plain-language large-type states; apply failures never partially ship (transaction + rollback); validator remains the single gate for all content changes regardless of origin (human fleet, John, AI rewrites).
- **State lifecycle:** suggestions are the only new server state (DO SQLite, trivially small; statuses terminal at `applied/declined`); drafts client-side only.
- **API surface parity:** API-CONTRACTS.md gains the editor section; the machine catalog (`data/index.json`) is unaffected; agent-appliers use the same documented endpoints Damien's review page uses.
- **Leak guarantee:** unchanged for the public build (leak-sweep still proves zero instructor content); instructor content now ALSO lives in a second server-only bundle — the sweep gains a check that `instructor-bundle` content never appears in the static output or the persona bundle.
- **Prior-art reuse (docs/solutions/orchestration/2026-07-17):** bfcache/pageshow, ≥16px/≥48px, text-node rendering, timingSafeEqual, DO idempotency, graceful 413, CORS-everywhere, "prompt-level guarantees must become code-level" (the value-sync + redaction lessons), single-owner-per-file waves.

## Implementation Phases

- **P0 — Round-trip contract** (schemas, allowlist, anchor+normalization spec, EditorStore schema + status enum, token-scope spec, API contract): the consistency contract; blocks everything.
- **P1 — Generator emissions:** `editor-map.generated.json` (block maps + source extracts + hashes), site-copy extraction to `data/site-copy.json`, pitch-page map, `tools/build_instructor_bundle.py`.
- **P2 — Worker:** EditorStore DO + endpoints, `/edit/` proxy-injector, instructor routes, review page, token/cookie handling. 62-test suite grows accordingly.
- **P3 — Editor client:** edit + comment interactions, pending/status inline, drafts, friendly errors; Windows Chrome/Edge primary matrix, iPad secondary.
- **P4 — Apply engine + value-sync** + AI-rewrite proposal flow.
- **P5 — Docs (README + API contracts + editor guide for John — one printable page), UAT (E1–E20 driven literally on Windows-viewport + iPad-viewport + one real-device pass when available), red-team the token surfaces, deploy DEV.** (Digest cron cut per deepening decision 6.)

**Binding reference specs for implementers:** `docs/research/editor-apply-spec.md` (store DDL, state machine, apply transaction, value-sync scope rules, parity gate) + Enhancement Summary items 2–3 (security + client race rules).

## Acceptance Criteria

Adopt SpecFlow's **E1–E18 verbatim** as the gate (student-isolation, leaked-link containment, John's happy path with keyboard-safe Save, block-comment fallback, draft survival, paste/unicode grace, formatting round-trip, idempotency, supersede, drift, ambiguity, accepted-blocked, full two-target round trip, review XSS, spam/DoS caps, pitch-page path, cumulative digest, token lifecycle) — plus:
- [ ] **E19 Value-sync:** editing a JSON-rendered scalar patches the JSON source and syndicates on regenerate; editing a prose-embedded value proposes companion edits; a value edit that breaks reconciliation ends `accepted_blocked`, never ships.
- [ ] **E20 Windows-primary:** the full happy path driven on Windows-Chrome viewport with mouse selection-commenting; Word-idiom familiarity check (selection → bubble → note reads like Word's flow).
- [ ] All existing gates stay green: validator PASS, `--check` + leak-sweep (extended), Worker tests (62 + new), chat/critique untouched.

## Dependencies & Risks

- **Highest-uncertainty seam:** contenteditable→markdown round trip with inline formatting — de-risked by the source-side `original_text` rule + plain-text-intent merge + `needs_human` escape; spike it first in P3.
- **Injector fragility:** DOM-walk mismatch between generator map and served page — pinned by build-stamp versioning; friendly reload state.
- **Token leak blast radius:** suggestions-spam only (rate-capped) for edit scope; instructor scope independently rotatable; review/admin scope never leaves Damien.
- **No API key needed** for any of this (AI-rewrite proposals queue as agent tasks; they run whenever keys/models are available).

## Out of Scope (v1)

Roger as second editor (token model ready, one more mapping); PROD wiring of the injector (DEV first; PROD needs only pointing `EDIT_UPSTREAM` at Pages); real-time co-editing; comment threads/replies (single note per item + Damien's decline note); Turnstile on suggest endpoints (fast-follow); email digests.

## Sources & References

- **Origin brainstorm:** [docs/brainstorms/2026-07-18-editor-experience-brainstorm.md](../brainstorms/2026-07-18-editor-experience-brainstorm.md) — decisions 1–8 carried forward; open questions resolved here (digest channel = scheduled-agent session; AI-rewrite = human-gated proposals).
- **SpecFlow gap analysis** — incorporated throughout (delivery-arch decision, round-trip contract, status enum, E1–E18, a11y specifics).
- **Planning Q&A (Damien, 2026-07-18):** Worker-inject; two scopes/one bookmark; value-sync "smart path"; selection-first (Windows-primary).
- Prior architecture: `docs/plans/2026-07-17-001-feat-curriculum-buildout-plan.md`; learnings: `docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md`; design system: `docs/research/design-direction.md`.
