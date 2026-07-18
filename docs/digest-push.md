# Digest push — ntfy nudge for pending editor suggestions

*Weekend fast-follow WP3. Ships the batched, cumulative push that taps Damien on
the shoulder when suggestions pile up in the editor review queue. The **review
page stays canonical** (editor plan decision 6); this push is **strictly
additive** — a nudge with a count and a link, never a second inbox and never the
suggestion content.*

## What it does

`tools/digest_push.py` reads the current pending set from the Worker's
admin store and, when something is waiting **and** the set has changed since the
last ping, fires **one** ntfy notification whose tap opens the review page.

- **Read path:** `GET {EDIT_API_BASE}/review` (admin scope, Bearer
  `EDIT_SERVICE_TOKEN`) → all outstanding rows. Same wire contract
  `tools/apply_suggestions.py` speaks, but `digest_push.py` is **standalone** —
  it imports zero apply-engine code (one-writer rule; the apply engine is being
  edited by another lane).
- **Reviewable set:** `pending + drift + needs_human + accepted_blocked` — every
  status that is "in the reviewer's court." `accepted`, `in_flight`, and terminal
  states (`applied`/`declined`/`superseded`) are excluded.
- **Publish path:** `POST {SONSTENG_NTFY_SERVER}/{topic}` (default
  `https://ntfy.sh`) with a content-light body and a `Click` header set to the
  review-page URL. Topic is resolved from `~/.config/claude-rc/ntfy-topic` (the
  home box's canonical rc-notify topic — a rotatable secret, read by path), or
  the `SONSTENG_NTFY_TOPIC` override.

## Batched + cumulative semantics

One digest summarizes **all** currently-pending suggestions. Suggestions
accumulate across days; a single sweep on the review page clears them. We
**never** notify per-suggestion — no spam. The push says "N suggestions are
waiting" + a per-matter breakdown + the link, and that's it.

Body is **content-light** by design (matches the MootLoop notification rule):
counts and locations only, never `new_text`/`original_text`. Client-authored
suggestion text never leaves the review page.

## Dedupe

A tiny state file (default
`${XDG_CACHE_HOME:-~/.cache}/sonsteng-digest/last-notified.json`, override with
`SONSTENG_DIGEST_STATE` or `--state-file`) stores a **signature** =
`sha256(sorted pending suggestion IDs)`.

- Signature **equal** to the stored one → the pending set is unchanged since we
  last told Damien → stay quiet.
- Any add / accept / decline / apply that changes membership flips the signature
  → re-notify. The signature is membership-exact (order-independent, immune to
  count-collisions where one item leaves as another arrives).
- Pending drains to **zero** → the state file is cleared, so the next
  accumulation notifies again even if it reuses an old ID.

## Cadence / trigger

A **systemd user timer** (the home box convention — cf. `rc-wip.timer`,
`coding-projects-sync.timer`) fires the oneshot service a few times a day
(09:00 / 13:00 / 17:00 / 21:00 America/Chicago). Because the run is cheap and
dedupe-guarded, cadence only bounds *latency to the first ping*, never volume.

## Install (one-liner — run manually; NOT auto-installed)

```
bash tools/install-digest-timer.sh
```

First run drops a template env file at `~/.config/sonsteng-digest/env` (0600);
fill `EDIT_SERVICE_TOKEN` from `~/.secrets/sonsteng-editor-tokens` (path only —
never commit), then re-run. Uninstall: `bash tools/install-digest-timer.sh
--uninstall`.

**Cron alternative** (if you prefer crontab over systemd) — one line:

```
0 9,13,17,21 * * * EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1 EDIT_SERVICE_TOKEN="$(cat ~/.secrets/sonsteng-editor-tokens-admin)" /usr/bin/python3 ~/Coding\ Projects/sonsteng-magnum-opus/tools/digest_push.py
```

## Environment

| Var | Purpose | Default |
| --- | --- | --- |
| `EDIT_API_BASE` | Worker edit API base (`…/edit/v1`, no trailing slash) | *(required)* |
| `EDIT_SERVICE_TOKEN` | Admin/service bookmark token (Bearer; never logged) | *(required for live)* |
| `EDIT_REVIEW_URL` | Click-through URL | `EDIT_ORIGIN`+`/edit/review`, else the workers.dev review URL |
| `SONSTENG_NTFY_TOPIC` | ntfy topic override | `~/.config/claude-rc/ntfy-topic` |
| `SONSTENG_NTFY_SERVER` | ntfy server | `https://ntfy.sh` |
| `SONSTENG_DIGEST_STATE` | dedupe state file path | `~/.cache/sonsteng-digest/last-notified.json` |

## Dry run / verify

```
EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1 \
EDIT_SERVICE_TOKEN=… python3 tools/digest_push.py --dry-run
```

`--dry-run` prints the exact title / click URL / body it *would* send and
publishes nothing and writes no state file. Tests:
`python3 tools/tests/test_digest_push.py` (22 pure-logic + dedupe tests, no
network).
