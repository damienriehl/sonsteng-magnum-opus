# Redline History browser (editor-gated)

The History browser gives editors (John/Roger) and Damien a durable, attributed,
redlined change history for every canonical document, named baselines, and a
one-click (admin-executed) revert. Under the direct-apply plan
(`docs/plans/2026-07-19-001-feat-canonical-direct-apply-plan.md`) it REPLACES the
pre-approval gate: editors ship freely; history + revert are the safety net.

This note is the contract the parallel **worker lane** wires up. The HISTORY lane
owns generation + assets; the worker lane owns routing + scope-gated serving.

## Pipeline (HISTORY lane — built)

| Tool | Role |
|---|---|
| `tools/render_diff_lib.py` | Word-level HTML diff engine (ported from the fence build; first-party, no THIRD-PARTY entry). `diff_html()` → escaped `<ins>`/`<del>`/collapsible `<details>` fragment + region counts. |
| `tools/build_history.py` | Reads git history (`git log --follow`), coalesces revisions, pre-renders the bounded redline set, emits the servable bundle + per-doc debug/preview files. Runs the public-site leak assertion. |
| `tools/make_baseline.py` | Creates an annotated `baseline-<name>` tag + regenerates history. Never pushes, never rewrites history. |

### Outputs (all under `build/`, gitignored — NEVER under `site/`)

```
build/history-bundle.generated.json   servable bundle (Worker inlines this)
build/history/index.json              doc list + generation metadata
build/history/<slug>.json             per-doc history (debug/inspection)
build/history/<slug>.preview.html     self-contained preview (QA only — inlines
                                       assets; production uses external assets)
```

Bundle shape:

```jsonc
{
  "sentinel": "SONSTENG-HISTORY-EDITOR-GATED",
  "generated_at": "...", "coalesce_window_secs": 600, "anyvsany_cap": 20,
  "head": "<sha>",
  "docs": {
    "data/firm/firm.json": {
      "doc": "data/firm/firm.json", "slug": "data__firm__firm.json",
      "revisions": [ /* newest-first */ {
        "run": ["<first_sha>","<last_sha>"], "tip": "<sha>", "parent": "<sha|EMPTY>",
        "n_commits": 2, "author": "...", "attribution": "JOS|RSH|DVR|APPLY",
        "ts_start": "...", "ts_end": "...", "kind": "edit|external|revert|baseline",
        "batches": ["<batch-id>"], "summary": "...", "baselines": ["baseline-..."]
      }],
      "baselines": [ {"name":"baseline-x","sha":"...","date":"...","message":"..."} ],
      "diffs": { "<from>..<to>": {"from","to","category","html","n_ins","n_del"} },
      "dropped_pairs": 0
    }
  }
}
```

`<from>`/`<to>` are a full sha, a baseline tag name, or `EMPTY` (diff against the
empty string, for a first-revision redline).

## Grouping + attribution (as built)

**Coalescing rule** (display-layer only; git stays append-only). Walking a file's
commits oldest→newest, a commit CONTINUES the current display revision when ALL:

- both the run and the commit are `kind == "edit"`; AND
- same `author_email`; AND
- the gap to the **previous commit in the run** is ≤ 600 s (rolling
  consecutive-gap window — measured between consecutive author-timestamps, NOT
  from the run's start).

`external` / `revert` / `baseline` commits never coalesce — each is its own
revision.

**Deviation from the fence build (documented):** fence additionally requires
block-set overlap because its service commits carry per-block `edit(<id>)`
subjects. sonsteng's apply engine commits whole batches
(`apply: batch <id> (<n> suggestions)`, identity `apply-engine
<apply@sonsteng.local>`, per `tools/apply_suggestions.py` step 11) with no
per-block subject — so there is no block set to overlap on. The coalescing key is
`author_email + kind + the 10-min window`, exactly as the plan specifies.

**Kind classification:**

- `edit` — apply-engine commit (apply identity or `apply: batch` subject).
- `revert` — `revert(...)` (daemon) or a plain `git revert` (`Revert "..."`).
- `baseline` — a `baseline(...)` subject (belt-and-braces; tags are primary).
- `external` — any other commit (a direct home-box session edit).

**Attribution chips** (`JOS`/`RSH`/`DVR`/`APPLY`): a commit-body trailer wins
(`Editor:`/`Attribution:`/`Co-authored-by:` — apply-engine batches can carry the
originating human there); else the git author maps via a small table; else the
author's initials. To attribute apply-engine batches to the real editor, have the
daemon add an `Editor: JOS` trailer to the apply commit body.

## Compare cap (as built)

Pre-rendered redline categories per doc:

- `revision` — each revision's own redline (`parent → tip`; `EMPTY → tip` for the first).
- `adjacent` — consecutive revision tips.
- `baseline` — each revision tip vs each baseline; `cumulative` — each baseline → HEAD.
- `full` — `EMPTY → HEAD` (whole-document redline).
- `anyvsany` — **all pairs among the last 20 revisions only** (`anyvsany_cap = 20`).

Full any-vs-any across ALL revisions is NOT precomputed. `dropped_pairs` =
`C(n,2) − C(min(n,20),2)`; `build_history.py` `log()`s exactly what was dropped.
Older revisions remain reachable via per-revision, adjacent, and baseline
redlines. The UI compare picker offers ONLY keys present in `diffs` — pairs that
were not pre-rendered are never selectable.

## Serving contract (worker lane — TO WIRE)

Assets and data follow the EXISTING review-page pattern
(`app/worker/src/editor-review.js` + `editor-assets.js` `serveAsset`).

1. **Bundle inlining** — add to `app/worker/scripts/bundle-editor-data.mjs`
   `FILES`:
   `["build/history-bundle.generated.json", "history-bundle.generated.json"]`,
   and add `app/history/history.{js,css}` to its `CLIENT` list (exports
   `HISTORY_JS` / `HISTORY_CSS`). FAIL-LOUD if the bundle is missing (same as the
   editor-map).
2. **Assets** — in `editor-assets.js` `serveAsset()` add
   `"history.css"` / `"history.js"` (read `CLIENT.HISTORY_CSS/JS`, fall back to
   inline stubs). Served at `/edit/assets/history.*` under `script-src 'self'`.
3. **Page route** — `GET /edit/history/<doc>` (edit/instructor scope, same gate as
   `/edit/v1/pending`). Render a shell exactly like `renderReviewPage`:
   an escaped island `<script id="history-data" type="application/json">` carrying
   `bundle.docs[<doc>]`, plus
   `<link rel="stylesheet" href="/edit/assets/history.css">` and
   `<script src="/edit/assets/history.js" defer></script>`.
   An index route `GET /edit/history/` can list `bundle.docs` keys.

**Well-known URL pattern:** `/edit/history/` (index) and `/edit/history/<doc-slug>`
(per-doc). The editor chrome's "History" link (worker lane's file) points here.
The client (`app/history/history.js`) reads the `history-data` island — no network
fetch required for the timeline/redlines (all pre-rendered).

### CSP note

`history.js`'s own JSDoc mentions the island `<script>` tag, so its literal
`</script>` would close an inline tag early. The **preview** generator escapes it
(`</script` → `<\/script`); **production serves `history.js` as an external
asset**, so this never arises there. The pre-rendered `diffs[*].html` is
build-time escaped by `render_diff_lib` (only `ins/del/details/summary` tags
survive), so the client assigns it via `innerHTML` safely.

## Revert-request contract (endpoint absent today)

`POST /edit/v1/revert-request` does **NOT** exist yet (checked 2026-07-19). The
"Request revert" button therefore renders **disabled** with the title
*"lands with the daemon's next update"* and a printed note. When the daemon lane
adds the endpoint, expose it to the client via `window.__HX_REVERT__ =
"/edit/v1/revert-request"` in the shell; the button then POSTs:

```json
{ "doc": "data/firm/firm.json", "run": ["<first_sha>", "<last_sha>"] }
```

**Contract:** editors *request*; an admin *executes* (SL8). The revert itself is a
file-scoped, 3-way, conflict-aware inverse commit (the fence build's
`service/history.py::perform_revert` is the proven implementation to port into
the daemon): clean → one attributed inverse commit touching only the doc; overlap
since the run → 409 abort (never partial); reverting past a baseline sets a
warning flag.

## Leak assertion (proof)

History redlines of canonical sources expose INSTRUCTOR-ONLY material (`facts.md`,
answer keys, concealed persona facts) that the public-site sweep
(`build_site.check_no_instructor_leaks`) keeps out of `site/platform/`. Therefore
history output is **editor-gated only**: it lives under `build/` and is served
exclusively through the authenticated `/edit` proxy.

`build_history.assert_no_history_leak(site/platform)` proves the public build
contains ZERO history output — two nets:

1. **Path net** — no `history-bundle*`, no `*.preview.html`, no `history/` dir
   anywhere under `site/platform/`.
2. **Content net** — the `SONSTENG-HISTORY-EDITOR-GATED` sentinel (stamped into
   every history artifact) appears in NO generated file under `site/platform/`.

`python3 tools/build_history.py --check` runs it and exits non-zero on any
violation. Verified green against a fresh `build_site.py --check` build.

## Baselines

`python3 tools/make_baseline.py walkthrough-2026-07-23 -m "before walkthrough"`
creates the annotated tag `baseline-walkthrough-2026-07-23` at HEAD and
regenerates history. `--list` shows existing baselines. Cut a baseline as a
deliberate editorial act (e.g. just before the John/Roger walkthrough); do NOT
create tags in CI. No tag is created by this build.

## Tests

`tools/tests/test_render_diff_lib.py`, `tools/tests/test_build_history.py`,
`tools/tests/test_make_baseline.py` cover: tokenization + escaping + collapse,
kind classification, attribution, the coalescing boundaries (exact-window join,
601 s split, rolling-window-not-from-start, author/kind breaks), redline
correctness, baseline surfacing + diffs, the compare cap (`dropped_pairs`), and
the leak assertion (sentinel/path nets + clean pass).
