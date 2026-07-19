---
date: 2026-07-19
topic: canonical-docs-redline-history
status: open-questions-pending
decision_record: dashboard.damienriehl.com/canonical-docs-brainstorm-2026-07-19.html (11 answers, 2026-07-19)
companion: fence-litigation/docs/brainstorms/2026-07-19-canonical-discovery-editor-brainstorm.md
---

# Canonical Documents: Direct-Apply Editing + Git-Backed Redline History

## What We're Building

The `/edit` editor moves from suggestion-gated to **direct-apply**: every edit by an
authorized editor (JOS / RSH / admin) flows through the existing apply engine straight
into the canonical source files, git-commits with author attribution, and rebuilds — so
the edit is durable across refresh for everyone. Saving becomes **auto-save** (no save
affordance; debounced on typing pause). The pre-approval gate is replaced by two things:

1. **A History browser** — word-level redlines rendered from git history: per-revision
   diffs, cumulative diffs against **named baselines** (e.g. "walkthrough version"),
   compare-any-two, author chips, and **one-click revert** (an attributed inverse revision).
2. **A strict editorial pass** — the system analyzes applied edits post-hoc and flags
   problems (inconsistencies, factual/self-contradiction errors, validator warnings) back
   to the editors, instead of a human pre-approving each edit.

## Why This Approach (git-backed — "Approach A")

Git already records who/what/when, and the apply engine already writes suggestions into
canonical sources through safe worktree transactions with validator/parity gates. We keep
both and add rendering + direct-apply mode. Rejected: an app-level revision database
(duplicates git, second source of truth to trust) and CRDT real-time collaboration
(overkill for 2–3 editors; YAGNI).

## Key Decisions (Damien's answers, 2026-07-19)

- **1b — scope:** everything John/Roger edit becomes direct-apply, with the strict
  editorial pass (timing = open question 1).
- **3a — gate = revert, not approve:** direct-apply for all authorized editors;
  one-click revert; full history.
- **4a — redline baselines:** both per-revision and cumulative-vs-named-baselines.
- **6a — concurrency:** last-write-wins + attributed history + easy revert.
- **8a — granularity:** one revision per save, and saves are **auto-saves** (his note).
- **11a — visibility:** every authorized editor sees full history + attribution.
- **Kept from existing architecture:** validator + parity gates still run as automatic
  pre-write checks; a failing edit is flagged to the editor (needs-attention), never
  silently dropped and never pre-approval-queued.
- **BYOK constraint:** no provider key exists in the Worker; the editorial pass therefore
  runs as an agent on the home box (authorized under the Claude-Max building allowance),
  not as a live Worker-side model call.

## Open Questions

1. **Editorial-pass timing** — per-edit, end-of-session (inactivity), or periodic
   (end-of-day)? (Damien's 1b note explicitly deferred this.)
2. **Auto-save revision coalescing** — with auto-save, does every typing pause become a
   revision, or do we coalesce same-author-same-block bursts into one revision?

## Next Steps

→ `/ce:plan` (after open questions resolve), implementation queued per Damien's answers.
