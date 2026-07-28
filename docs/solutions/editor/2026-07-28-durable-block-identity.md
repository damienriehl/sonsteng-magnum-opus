---
title: "Durable block identity: making content addressable when it can move"
category: editor
tags: [block-identity, migration, editor-map, source-ref, equivalence-proof, structural-editing]
module: editor
symptom: "Structural editing (insert/delete/move a paragraph) was inexpressible: inserting one paragraph renumbered every later block and silently invalidated every pending suggestion"
root_cause: "Content was addressed twice and BOTH addresses were positional — the page-anchor index among walker candidates, and the source locator ordinal (file.md#p2)"
related: [docs/plans/2026-07-28-002-feat-word-like-practicum-editing-plan.md, docs/research/editor-apply-spec.md]
---

# The problem in one line

You cannot add a paragraph to a document whose every block is identified by
counting.

## What was true before 2026-07-28

`source_ref` was `<file>#p<n>` where `n` is the block's ordinal within the file,
and the editor map's `index` was the block's position among `<main>`'s walker
candidates. Both renumber on insert. The store already modelled the
consequence — `drift` — but drift is a *recovery* mechanism, not a licence to
cause it on every structural edit.

## The design that fixed it (settled with Damien, 2026-07-28)

**A generated, never-reused ID stamped into the source file itself.** Not a
content hash (changes exactly when the content is edited — the one moment
identity must survive), not a position (changes when neighbours move), not a
sidecar index (an edit made outside the pipeline — a hand edit, a git merge —
silently orphans it, which is the drift problem wearing a hat).

- **Markdown prose** (including `body_md` strings inside JSON): a trailing
  marker `{#b:xxxxxxxx}`, 8 lowercase hex, on the block's last source line.
- **JSON scalars: nothing was needed.** They are already addressed by dotted
  path, and the corpus contains **zero array-index paths** — so `json_path`
  was durable identity all along. This is why no schema change was required
  and the strict `additionalProperties: false` posture stood untouched.
- Locator grammar after migration: `<file>#b<hex8>`,
  `<file>#<json.path>.b<hex8>`, and `<file>#<json.path>` unchanged for scalars.

**The positional index survives as a placement hint, not identity** — the
injector still needs it to put the affordance somewhere. A moved block is now
a re-render, not a drift.

## Three properties that made it safe

1. **The renderer strips the marker before emit AND before registration**, so
   a marker can never render and can never enter `original_text`. Verified on
   400 corpus blocks before writing any migration: stripped rendering is
   byte-identical.
2. **Text edits preserve identity for free.** The apply engine locates a
   markdown block by *unique exact-string match* of the marker-less
   `original_text` — so the trailing marker (and the identity) is simply not
   part of what gets replaced. Discovery found this; it was assumed to need
   work and needed none.
3. **An unmarked block is safe, not broken.** It renders normally, is absent
   from the map (read-only), and is counted in a build warning +
   `build/editor-unmarked.generated.json`. The stamper is rerunnable and mints
   only what is missing.

## The migration protocol (reuse this shape for any corpus-wide re-keying)

The migration rewrote 5,952 blocks across 320 files. What made it provable:

1. **Write the equivalence checker FIRST**, before the migration exists.
2. **Empty the queue and prove it** — `GET /edit/v1/review` → 0 rows — and
   pause the apply timer for the window. A migration that renumbers anchors
   while suggestions are pending drifts them all.
3. **Capture BEFORE artifacts from a clean pre-change worktree**, not from
   memory.
4. **Prove, don't inspect:** every block must map 1:1 to the same page, same
   index, same text, same kind, same file. Only the locator may differ.
5. **Prove the rendered output too** — the `site/` diff must contain zero
   non-stamp lines (only the build fingerprint may move).
6. **Add a permanent leak sweep** so the invariant outlives the migration:
   `--check` now fails if `{#b:` appears anywhere under `site/`.

## The trap this exposed: verbatim exports

Pages render the marker away, but the build also **copies student-safe source
files verbatim** into the public data catalog. Those copies shipped the
markers. Any "we strip it at render time" claim must be checked against every
path that emits the file *without* rendering it (`copy_student_safe` now
scrubs). The permanent sweep is what turns this from a caught bug into a
closed class.
