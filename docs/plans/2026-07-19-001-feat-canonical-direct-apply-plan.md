---
title: "feat: Canonical direct-apply mode + redline history for the /edit editor"
type: feat
status: completed
date: 2026-07-19
completed: 2026-07-19
origin: docs/brainstorms/2026-07-19-canonical-docs-redline-history-brainstorm.md
sequencing: builds AFTER the fence-litigation canonical editor ships (its plan is the priority track today)
---

# ✨ Canonical Direct-Apply Mode + Redline History (sonsteng /edit)

## Overview

Everything John/Roger edit becomes **direct-apply** (brainstorm 1b): auto-saved
suggestions flow to `accepted` automatically, a home-box **apply daemon** runs the
existing apply engine + rebuild + DEV deploy to converge canonical within ~1–2
minutes, and the just-shipped pending-overlay hydration keeps the interim state
visible. The pre-approval gate is replaced by (a) a **History browser** with
per-revision and named-baseline redlines + one-click revert, and (b) a **strict
editorial pass**: a session-end (~30 min inactivity) + daily home-box agent that
analyzes applied edits and files flags. All existing safety gates (validator,
parity, span-splice conservatism) remain as automatic pre-write checks.

## Problem Statement / Motivation

Suggestion-mode requires Damien to review every edit before it lands. His 2026-07-19
decision: trust the editors, gate with revert + post-hoc analysis instead, and give
everyone a durable, redlined change history (brainstorm answers 1b, 3a, 4a, 6a–8a, 11a).

## Proposed Solution

- **Auto-save client:** debounce ~2.5s on typing pause (no Save button); reuse the
  fence durability spec: localStorage buffer, status pills (`aria-live`), flush on
  `visibilitychange`/`pagehide`, `beforeunload` guard, auth-expiry buffer+replay
  with idempotency key.
- **Status pipeline (SL2 fix):** the client NEVER clears an edit optimistically.
  Suggestion → `accepted` (auto, server-side) → daemon applies → `applied`
  (confirmed in git) → overlay converts to live text on next rebuild. Validator/
  parity rejection → `needs_human` and the overlay **unmasks**: the block shows an
  explicit warning state + the ntfy digest alerts. "Applied" is only ever shown
  after git confirms (SL1 fix — the overlay is honest about latency AND failure).
- **Apply daemon (home box):** systemd timer every 2 min (+ ntfy-nudged immediate
  runs) invoking the existing apply engine → rebuild → DEV deploy. Coalescing per
  Damien's answer: history displays same-editor-same-block runs within ~10 min as
  one revision (display-layer grouping; git commits stay append-only — same
  resolution as the fence plan). Flush-on-session-end: the 30-min-inactivity
  editorial trigger also forces a final apply run (SL3 fix). Daemon publishes a
  heartbeat the Worker surfaces in the editor banner ("changes going live
  normally" / "apply paused — last run N min ago") (SL6 fix).
- **Cross-editor overwrites (SL4):** same-block edits flow through the existing
  supersede semantics before apply — the superseded edit stays in history with
  attribution and shows in the digest; losing an argument to LWW is visible,
  never silent.
- **Editorial pass:** home-box Opus agent (Claude-Max allowance) at session-end +
  daily sweep; reviews the window's applied diffs against the spine (voice,
  consistency, factual self-contradiction, validator WARNs); files flags as
  comments on the affected blocks + a digest entry. Post-hoc by design (Damien
  accepted the detection lag, SL5).
- **History browser:** reuse the fence redline engine (factored `render-diff.py`)
  over sonsteng's git history; per-revision + named baselines (e.g.
  "walkthrough-2026-07-23") + compare-any-two; one-click revert = inverse commit
  via the daemon, admin scope (SL8 noted: editors request, admin confirms).

## Technical Considerations

- Worker changes: auto-accept transition, heartbeat surfacing, needs_human unmask
  state in the overlay projection. Apply engine: no core changes — the daemon
  wraps `apply_suggestions.py` (keep worktree txn here; multi-page rebuild+deploy
  justifies it, unlike fence). Rebuild/deploy stays DEV-only; PROD untouched.
- BYOK invariant: no provider key anywhere; the editorial agent runs on the home
  box only.
- Security posture unchanged: scopes, CSRF, Turnstile, redaction gates all as-is.

## System-Wide Impact

- **Silent-loss vectors closed:** SL1 (overlay masks daemon failure) → unmask +
  alert; SL2 (auto-accept vs validator reject limbo) → server-status-driven UI;
  SL3 (last-edit swallowed) → session-end flush; SL4 (LWW cross-editor) →
  supersede + visible history; SL6 (home box down) → heartbeat banner.
- **Integration tests:** daemon-failure unmask (kill daemon mid-apply → block
  shows warning, digest fires); validator-reject round-trip (bad edit →
  needs_human visible to the editor); session-end flush (edit then idle 30 min →
  applied without further action); heartbeat (stop timer → banner degrades);
  cross-editor supersede visibility.

## Acceptance Criteria

- [x] John/Roger edits reach canonical (git) without any Damien action; DEV
      reflects them within ~2 min under normal operation. *(E2E 2026-07-19: john
      edit → auto-accept → installed apply-daemon tick → `apply: batch` commit on
      feat/canonical-docs + DEV showed the new text; heartbeat_age_s fresh.)*
- [x] No silent loss: every edit ends `applied` (git-confirmed) or in a visible
      failure/needs_human state with digest alert — verified by the integration
      tests above. *(status machine + daemon failure→ntfy + needs_human unmask
      unit-tested; E2E edit ended `applied`.)*
- [x] History browser: attributed revisions (display-coalesced ~10 min),
      per-revision + named-baseline redlines, compare-any-two, one-click revert.
      *(LIVE at /edit/history/; revert round trip E2E 2026-07-19: admin
      revert-request → daemon tick → `revert(history)` commit → DEV restored →
      history shows the kind=revert revision.)*
- [x] Editorial pass files flags at session-end + daily; flags visible in the
      editor and the digest. *(built + unit-tested; sonsteng-editorial.timer
      installed for the 21:30 daily sweep + the daemon's session-end dispatch —
      first live model-produced flag lands on the next sweep.)*
- [x] All existing gates stay green (worker 189+→218, pytest 88→180, client
      32+→40/40, validator PASS, probe clean); PROD untouched.

## Dependencies & Risks

- Fence build ships first today (shared redline engine + durability client
  patterns land there and are reused here).
- Home box is the apply/editorial SPOF — mitigated by heartbeat + digest alerts,
  accepted per brainstorm.
- Damien's editor test-drive + John link (tonight's reminder) are unaffected;
  direct-apply changes editor semantics — brief John/Roger before flipping the
  mode on (a one-line note in the editor guide ships with this).

## Sources & References

- **Origin brainstorm:** docs/brainstorms/2026-07-19-canonical-docs-redline-history-brainstorm.md
  — carried forward: 1b direct-apply-everything + strict editorial pass
  (session-end + daily, resolved 2026-07-19); revert-not-approve; auto-save with
  ~10-min coalescing; full history visible to all editors.
- Companion (priority) plan: fence-litigation
  `docs/plans/2026-07-19-001-feat-canonical-discovery-editor-plan.md` — shared
  resolutions: display-layer coalescing, durability spec, honest-overlay rule.
- SpecFlow silent-loss analysis (SL1–SL8): this session, 2026-07-19.
- Existing machinery: `docs/research/editor-apply-spec.md`, apply engine in
  `tools/`, pending-overlay hydration (merged 5b9e9b3), digest push (WP3).
