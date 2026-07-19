# EditorStore + Apply Engine — Implementable Spec

*Produced 2026-07-18 by the plan-deepening pass (data-integrity review, adopted with the security review's allowlist invariant). The engineer codes directly from this. P0 = corruption-prevention; nothing ships without it.*

## The two prime invariants

1. **The map is the universal allowlist (security):** every client-influenced reference — the `/edit/<path>` proxy path, every `source_ref`, every `json_path` — validates against the generator-emitted `editor-map.generated.json` server-side at BOTH suggest-time and apply-time. Unknown → uniform 404/validation_error. No client value ever reaches a filesystem path, shell, or JSON write unchecked; `json_scalar` writes go parse→set-at-path→serialize, never text splice; searches are `shell=False` fixed-string.
2. **Patches reach canonical + deploy ONLY after validate + build + parity all pass (integrity):** all patching happens in a git worktree; rollback = discard worktree; canonical stays byte-clean throughout.

## EditorStore DDL (DO SQLite, migration tag v2 append)

Tables exactly as specified: `suggestions` (id=client-uuid PK/idempotency, editor+original_text SERVER-written only, scope, origin human|companion|ai_rewrite, kind prose|json_scalar|comment, page, block_anchor, source_ref, json_path, original_text, original_hash, new_text, comment, context, map_version, group_id, supersedes, status, decision_note, apply_batch_id, lease_expires_at, timestamps) + 6 indexes (status; source_ref+status; group_id; page+status; editor+status+created; batch) + `suggest_counts` (editor, day, count) + `apply_batches` journal (batch_id, base_sha, phase claimed|patched|validated|built|parity_ok|deployed|merged|done|rolled_back, lease).

## Status machine (owners; ⛔ = terminal)

`pending → superseded⛔` (same editor re-edits source_ref, atomic in one DO call) · `pending → declined⛔/accepted` (admin decide only — the SOLE writer of accepted) · `pending/accepted → drift` (hash recheck) · `accepted → in_flight` (apply claim + lease) · `in_flight → applied⛔ | accepted_blocked | drift | needs_human | accepted` (crash reconciliation) · `accepted_blocked → accepted/declined⛔` · `drift → pending` (re-anchor forces RE-REVIEW, never straight to accepted) · `needs_human → applied⛔/accepted/declined⛔` (Damien hand-resolution). Companions/ai_rewrites are born `pending` and can only be accepted via admin decide → structurally cannot auto-apply.

## Pending overlay (WYSIWYG across reloads)

The `/edit` client reproduces the *just-after-save* visual state on every reload: an outstanding suggestion's `new_text` is painted into its block (Google-Docs "suggestion" model), marked pending, and attributed. **Hydrate set** = the store's `listAll()` active set MINUS `drift`: `pending`, `accepted`, `accepted_blocked`, `in_flight`, `needs_human` (their `new_text` is the block's intended content). **Never hydrated**: `drift` (anchor moved — `new_text` was authored against text that no longer exists there; the client stale-guard would drop it anyway), `applied` (already live in the served HTML), `declined`/`superseded` (terminal reverts — block shows its original). The projection (`projectPendingItems`) ships the FULL `new_text` (edit kinds only; comments stay margin bubbles), `base_hash` (= the suggestion's `original_hash`) + `map_version` for the client stale guard, and `attribution` (JOS/RSH, from the server-resolved `editor` identity — same stamp as the review surface). Hydration is **display-only** (textContent, never innerHTML): it never mutates `originalText`/`originalHash`/snapshot/dirty/`suggestionId`, so a stale/bad overlay can never poison a save — the block still re-edits + re-saves against the **canonical** map hash, which flows through the existing same-editor supersede path unchanged. Guards, in order: block IDLE (never clobber a live edit) → **unsent draft WINS** (newer intent) → `base_hash === block.original_hash` and `map_version === island.version` (else pill-only fallback). Cross-editor visibility (John sees Roger's, admin preview) is a projection+client capability today; it activates fully once the injector/`/pending` source page-scoped rows instead of `listForEditor(caller)` (one router/store line, out of the overlay change's file scope).

## Apply transaction (tools/apply_suggestions.py)

flock (`.locks/apply.lock`, host-local intra-host guard; the DO in_flight lease is the cross-host mutex) → assert clean, record base_sha → DO claim (accepted→in_flight, whole group_id groups only, lease) → `git worktree add` → **pre-apply drift gate: re-resolve original_text from CURRENT source, hash mismatch → drift, drop** → patch sorted (file, position-descending): exact-match → context-anchor → json_path write; ambiguous → needs_human; **formatting rule: block has_inline_formatting → needs_human in v1 (no merge algorithm; fast-follow: splice only when every formatted span's text is unchanged in-order)** → value-sync proposer (below) → `validate_spine.py --strict --json`: **RED = whole-batch accepted_blocked + rollback** (structural rejects were pulled pre-patch; P1: bisect salvage, never splitting groups) → `build_site.py --check` + persona + instructor bundles → **parity gate** → deploy site + Worker (idempotent) → merge worktree → DO finalize applied → word-diff digest. Crash recovery: startup reconciliation before any claim reads `apply_batches.phase` — pre-`merged` = rollback + re-queue (re-deploy canonical if phase=deployed), post-`merged` = complete to applied; lease expiry breaks any limbo.

## Value-sync proposer (scope rules — no NLP)

Only prose-embedded values (json_scalar path already syndicates via regenerate). Trigger only when the changed substring matches money/date/proper-noun patterns. Exact old-literal search, **matter-prefix-bounded** (never crosses mNN; firm scope = this matter's book entry + business file only); match on (value, type, structural anchor) — bare unanchored digits and low-entropy values (`$1,000`, bare years) route to manual; party names match only this matter's declared roles/personas; money compared via the validator's Decimal ±$0.01. Companions carry per-target provenance, land as `pending/origin=companion` in a `group_id`; group accept is one atomic DO call; lone-member accept rejected.

## AI-rewrite pipeline (v1, Damien-confirmed)

Accepting a conceptual comment sets it to a queued state the APPLY-LOOP AGENT (this harness — no API key, no Worker LLM call) consumes: the agent authors a rewrite as a new `pending` suggestion, `origin='ai_rewrite'`, attributed "AI (from JOS comment)", subject to the same admin-accept gate and text-node-only rendering everywhere.

## Two-bundle parity (new)

`tools/spine_stamp.py`: `spine_build_id = sha256(spine_version + sorted (relpath, filehash) over data/ inputs)`; emitted by all three builds (site meta/catalog, personas bundle, instructor bundle); apply aborts on any mismatch; standalone `tools/check_build_parity.py` as the manual/CI backstop.

## Ceilings & hygiene

Per-editor pending ceiling (200) + daily cap (500, `suggest_counts`) + 16KB pre-insert size cap (graceful large-type 413) + GLOBAL pending ceiling + retention purge N days after terminal status + "don't paste confidential client info" warning at the input + digest endpoint admin-scope only, constant-time checks throughout.
