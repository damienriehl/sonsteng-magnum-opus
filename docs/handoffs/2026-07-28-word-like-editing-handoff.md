# Handoff — word-like editing shipped (2026-07-28)

**Read this cold?** Start here, then the plan
(`docs/plans/2026-07-28-002-feat-word-like-practicum-editing-plan.md`) for the
*why* of every decision, then the three learnings under `docs/solutions/editor/`
for the traps. This document is the state of the world as of 2026-07-28.

Everything below is **merged to `main` and deployed to DEV**. Preflight was
9 passed / 0 failed / 0 skipped at every merge.

---

## 1. The one thing that changes how you read all the old docs

**`source_ref` is no longer positional.** Anything written before 2026-07-28 —
including `docs/research/editor-apply-spec.md`, which is otherwise still the
authoritative spec — describes the old ordinal grammar `file.md#p2`. That is
now historical.

| | before | now |
|---|---|---|
| markdown prose | `file.md#p2` | `file.md#b3fa9c21e` |
| prose in a JSON string | `ex.json#sections.intro.body_md.p0` | `ex.json#sections.intro.body_md.b3fa9c21e` |
| JSON scalar | `matter.json#caption` | unchanged — already durable |

The `b<hex8>` comes from a `{#b:xxxxxxxx}` marker stamped in the source file.
5,952 blocks were stamped across 320 files; the migration is done and proven
(see `docs/solutions/editor/2026-07-28-durable-block-identity.md`). The
positional index still exists in the map as a **placement hint only**.

Corollary: **never hand-edit a `{#b:}` marker, and never let one reach a
reader.** `build_site.py --check` fails if `{#b:` appears anywhere under
`site/`.

## 2. What exists now that did not

| Unit | What it is | Where |
|---|---|---|
| U1–U2 | Durable block IDs + the corpus migration | `tools/stamp_block_ids.py`, `build_site.py` (renderer strips + keys by bid) |
| U3 | Taxonomy + firm editable (map 3,474 → 3,692) | `build_site.py` `build_skills`, `build_firm_dashboard` |
| U4 | Structural ops: insert_after / delete / split / merge / move | `tools/structural_ops.py`, `editor-store-core.js`, `editor-endpoints.js`, `app/editor/editor.js` |
| U5 | Per-matter **Facts page** + `json_add` + Stage-1.5 propagation | `build_site.py` `build_facts_pages`, `apply_suggestions.py` |
| U5b | Per-matter **Law page** (Meridian editable, real law never) | `build_site.py` `build_law_pages` |
| U6 | Scope ladder + deterministic enumeration | map `scopes` index, `editor-map.js` `enumerateScope`, `GET /edit/v1/scope` |
| U7 | AI-drafted scoped changes (enumerate → draft → redline) | `tools/editor_scoped_drafts.py`, `/edit/v1/scoped-*` |
| U8 | Client verbs: "Bigger change…" dialog, add-a-fact | `app/editor/editor.js` |
| U9 | The guide rewritten around the new verbs | `docs/editor-guide-for-john.md` |
| U10 | Inconsistency checker (**read §5 before trusting it**) | `tools/editor_consistency.py` |

Map totals: **5,722 editable blocks across 69 pages.**

New endpoints (all in `editor-endpoints.js`, routed in `editor.js`):
`GET /scope`, `POST /scoped-request`, `GET /scoped-requests`,
`POST /scoped-claim`, `POST /scoped-resolve`, `GET /group-status`.

## 3. The rules that are now load-bearing

- **Gating (R6/KD3, Damien's call):** a single-block human *text* edit keeps
  the ~2-minute auto-apply fast path. **Everything else waits for review** —
  every structural op, every scoped change, every `json_add`. The gate is the
  *scope of the request*, never the count of the result. Enforced in
  `editor-store-core.js` via `AUTO_APPLY_KINDS` (only `prose`, `json_scalar`).
- **Real law is never editable (R11):** enforced by path — only string leaves
  of `data/jurisdictions/meridian.json` register. Anything under
  `data/jurisdictions/real/` never enters the map, so the server refuses it by
  **allowlist absence**, with no special-case code. Do not add a "is this real
  law?" check anywhere; absence *is* the mechanism.
- **Scoped-change ceiling: 100 blocks** (Damien, 2026-07-28). Above it the
  request 409s with the radius and requires explicit confirmation. Module- and
  course-scoped batches apply to one canary matter first; the remainder drafts
  only after the canary group is fully applied.
- **A new fact lands even if its drafted prose mentions are declined**
  (Damien, OQ5) — it simply renders nowhere yet.

## 4. Measured blast radii (for sizing any future scoped work)

course **3,692 blocks / 282 files / 20 matters** · module M1 460 / M2 492 (all
20 matters) / M3 371 · largest matter 206 · a matter's case-file part ~122 ·
a typical targeted change 5–40.

## 5. Known-weak, deliberately: the Inconsistency checker

`tools/editor_consistency.py` has **two modes and they are not equal.**

- **`--since <rev>` is the real mode.** It diffs fact values across revisions
  and flags prose still carrying the old literal. Deterministic, 0 false flags
  on the corpus, catches what is genuinely restated.
- **No-history mode is a floor, not a check.** Correspondence there needs the
  fact's *label words* in the same paragraph as the literal, and real prose
  does not write that way. Its measured catch rate on the live corpus is
  **zero**. The tool now prints that a clean result in this mode means "not
  checked", never "consistent".

It is **not wired into the daemon tick** — deliberately, pending Damien's
call, because the useful mode needs a revision range. If you wire it, use
`--since` against the last published rev, and note that `main()` silently
downgrades to dry-run when `EDIT_API_BASE` is unset.

## 6. Traps that already bit (all fixed — don't re-learn them)

Full detail in `docs/solutions/editor/`. In brief:

1. **A revert does not restore generated artifacts.** The revert path must
   rebuild + redeploy the Worker, or the restored block is un-editable.
2. **`editor-store.js` forwards every DO RPC by hand.** Add a core method →
   add the forwarding line in the same commit; unit tests cannot see the gap.
3. **A live Durable Object keeps its script version until evicted** — a
   failure in the first minute after a deploy may be staleness, not your code.
4. **Adding a builder to `build_site.py` means adding it to the two
   fresh-build test harnesses** (`test_editable_coverage.py`,
   `test_editor_map_reachability.py`), which replicate `main()`.
5. **Verbatim exports bypass the renderer** — `copy_student_safe` scrubs
   markers; the `--check` sweep is the permanent guard.
6. **Gutter rails clamp to measured room** — a hovered label's growth ran a
   rail offscreen at 900px; `verify-rail-placement.js` catches it.

## 7. Left undone, on purpose

- **U10 daemon wiring** (§5) — Damien's call.
- **Numeric facts are read-only on the Facts page.** The `json_scalar` write
  path stores a string; a schema-typed number would fail `validate_spine` and
  take its whole apply batch down. Making them editable requires type coercion
  in `apply_suggestions.py` first. This is the one R3 promise not fully kept.
- **Deferred by the plan itself:** editing FOLIO crosswalk IDs / skill+task
  IDs / Bloom levels (join keys), real-time collaborative editing, any PROD
  door change.
- **Review residuals (P2/P3, none blocking):** add-a-fact has no headful test;
  the harness mock triggers the ceiling 409 off `level === 'course'` rather
  than a computed radius; the real-law absence assertion runs on a partial
  build; neither new dialog persists typed text across a reload the way
  `sendSuggestion` does. Full findings JSON was written under
  `/tmp/compound-engineering-1000/ce-code-review/20260728-68bc7161/`
  (ephemeral — the durable record is this list).

## 8. How to verify it still works

```bash
bash tools/preflight.sh          # must be 9 passed / 0 failed / 0 skipped
python3 tools/stamp_block_ids.py --check    # 0 blocks to stamp = corpus fully marked
python3 tools/editor_consistency.py --dry-run --no-model   # prints its own limitation notice
```

Merges go to `main` from `~/.local/share/sonsteng-daemon/checkout` under
`flock .locks/daemon.lock`; regenerate the three bundles and check parity
before pushing, or the next apply refuses to run.
