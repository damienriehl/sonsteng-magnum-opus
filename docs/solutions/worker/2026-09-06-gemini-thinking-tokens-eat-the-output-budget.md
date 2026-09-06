---
title: "Gemini thinking tokens can consume the entire output budget"
lane: worker
tags: [google, gemini, debrief, thinking-tokens, truncation, observability]
status: resolved
related: ["app/worker/src/providers/google.js", "app/worker/src/debrief.js", "PR #49", "PR #51", "PR #52"]
---

# Symptom

The Google debrief returned `502 validation_error: The debrief exceeded the
provider output limit` on every attempt. Live requests took 16 seconds and both
attempts ended with `finishReason: MAX_TOKENS`, even after PR #49 added one
truncation retry and doubled `maxOutputTokens` from 1200 to 2400.

# What we tried

PR #49 treated the failure as an ordinary undersized response budget: retry
once after truncation with twice the tokens. Both attempts still exhausted the
budget. Chat remained healthy, which initially made the provider model itself
look unlikely; its short role-play replies required little thinking.

# Root cause

`gemini-2.5-flash` thinks by default, and thinking tokens count against
`maxOutputTokens`. The structured JSON debrief spent nearly all of its allowance
thinking, leaving little or no candidate text. Raising the shared cap did not
change that allocation problem.

# Fix

PR #51 sets `generationConfig.thinkingConfig = { thinkingBudget: 0 }` for Google
debrief and critique calls in `app/worker/src/debrief.js` and
`app/worker/src/providers/google.js`. Callers opt in because Gemini Pro rejects a
zero budget. Chat was deliberately left untouched to preserve the 44/44 journey
baseline. On a `max_tokens` stop, numeric `usageMetadata` counts for prompt,
candidates, thoughts, and total now appear in the truncation log, so the next
live run can prove or refute the diagnosis. A live hostile red-team leg then
confirmed `debrief-oracle-content` PASS.

# How to recognize it next time

Suspect hidden reasoning consumption when Gemini returns `MAX_TOKENS` with
little or no text and `thoughtsTokenCount` is near the configured cap. Inspect
the usage counts before increasing `maxOutputTokens` or adding another retry.

# Related

- `app/worker/src/providers/google.js`
- `app/worker/src/debrief.js`
- PR #49, PR #51, and PR #52
