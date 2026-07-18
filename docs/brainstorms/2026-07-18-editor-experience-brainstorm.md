# Brainstorm — The Sonsteng Editor Experience

**Date:** 2026-07-18
**Participants:** Damien Riehl (+ Claude). Editor persona: **Prof. John O. Sonsteng, 84** — the
system must demand zero new skills from him.
**Status:** Brainstorm → ready for `/ce:plan`
**Companion decision:** [Midstate deferred](../decisions/2026-07-18-midstate-deferred.md) — the
originally-proposed Midstate workstream is dropped for copyright avoidance; pivot path recorded.

---

## What We're Building

**Edit Mode**: a magic link that turns the *real* platform site into the editor. John opens the
same pages students see; with his link, every prose block becomes editable and every text span
commentable:

1. **Line-level edits (WordPress-simple):** tap/click a paragraph → it becomes editable in place
   (contenteditable) → change words, add sentences → **Save** floats right there. What he sees is
   exactly what ships.
2. **Conceptual comments (Word-style):** select/highlight any text → a comment bubble appears →
   "This section should be more about…" → **Comment**. His pending comments render inline
   (margin bubbles), so he sees what he's already said.
3. **Everything is a SUGGESTION, never a live change:** all edits/comments POST to the Worker and
   persist in the suggestion store (Durable Object). Nothing publishes until Damien accepts.
4. **Instructor view included (Phase 1):** a token-gated instructor surface renders the
   back-of-house materials (facts files, instructor notes, answer keys) so John can edit the
   pedagogy itself — served through the Worker (never in the public static build, so the
   student-side leak guarantee is untouched).
5. **Review & apply:** a token-gated **/review** surface lists every pending suggestion in
   context with Accept/Decline for Damien, plus a **cumulative daily digest** (all `pending`
   items regardless of age — review Day 3, see Days 1–3 in one sweep; delivered only when
   non-empty). Accepted edits are applied to the **data spine sources** by agents (never to
   generated HTML), then validator → regenerate → redeploy → word-level diff digest back to
   Damien.

**Editable surface (Phase 1):** all student-facing prose — packet sections, case-file documents,
curriculum volumes, templates, skills descriptions, site copy — **plus the pitch page** and the
**instructor materials** via the instructor view.

---

## Why This Approach

- **The site IS the editor** (chosen over Proof, Google-Docs round-trip, and a git CMS): zero new
  tools, zero logins, one link on John's iPad. WYSIWYG fidelity is perfect because he edits the
  rendered page itself.
- **Provenance-stamped blocks make round-trip tractable:** the generator knows the source of
  every rendered block at build time — it stamps `data-source="<file>#<anchor>"` on each editable
  element. A suggestion carries `{source_ref, original_text, edited_text | comment, editor,
  timestamp}`, so agents can apply it to markdown/JSON sources deterministically and flag drift
  (source changed since the suggestion) instead of mis-applying.
- **Suggestions-not-publishing** respects the spine: the corpus is validator-gated, money-exact,
  leak-swept; direct WYSIWYG writes would bypass all of it. The agent-apply step keeps every
  guarantee (re-validate, re-sweep, regenerate).
- **Worker-served instructor view** keeps the public static build provably instructor-free (the
  existing leak sweep stays meaningful) while giving John full access behind his token.
- **Reuses everything shipped:** the Worker (session/signing machinery, DO storage), the design
  system (stage-direction/comment styling), the generator (block stamping), the deploy loop.

## Key Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Midstate materials | **Dropped** (copyright avoidance); pivot path documented |
| 2 | Editor architecture | In-platform Edit Mode via magic link (signed, revocable token) |
| 3 | Edit scope | Everything: student-facing + pitch page + instructor view |
| 4 | Instructor view | Worker-served, token-gated; never in the public static build |
| 5 | Change model | Suggestions only; Damien accepts; agents apply to spine sources |
| 6 | Review flow | Token-gated /review page + Damien's apply-digest loop |
| 7 | Notification | **Cumulative daily digest** (all pending, only when non-empty); no instant pushes |
| 8 | Editor UX bar | Tap-to-edit + highlight-to-comment; large-type friendly; iPad-first; zero training |

## Resolved Questions

1. **Cumulative digests** — suggestions carry status (`pending/accepted/declined`); the digest
   always lists all `pending`, so multi-day accumulation reviews in one sweep by construction.
2. **Attribution** — the magic token embeds the editor identity ("JOS"); multi-editor later
   (Roger) = a second token.

## Open Questions (for planning, not blocking)

1. Digest delivery channel: this chat (default) vs. email — default to chat until Damien says otherwise.
2. Whether accepted **conceptual** comments auto-spawn an agent rewrite proposal for Damien's
   approval (recommended) or just sit as tasks.

## Next

`/ce:plan` the build: generator block-stamping → Worker suggestion + instructor endpoints →
edit-mode JS (editable blocks, comment bubbles, pending-state rendering) → /review surface →
daily-digest cron → apply-loop agent workflow.
