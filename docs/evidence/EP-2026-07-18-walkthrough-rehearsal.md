# Evidence Pack — EP-2026-07-18 — WP9 Walkthrough Rehearsal

**Branch:** `feat/weekend-fast-follows` · **Runbook rehearsed:** [`docs/demo-runbook-2026-07-18.md`](../demo-runbook-2026-07-18.md) · **Live:** [DEV site](https://sonsteng-dev.damienriehl.com/platform/) + Worker `https://sonsteng-chat.damienriehl.workers.dev` · **Method:** every beat driven keyless on live DEV (puppeteer headful on Xwayland `:0`; per-section viewport shots).

## Beat-by-beat rehearsal (keyless paths)

| # | Beat | Result | Notes / action |
|---|------|--------|----------------|
| pre-1 | 🔑 Paste key / house pool | N/A keyless | BYOK-forever; unchanged. Bypass token append documented. |
| pre-2 | 🔑 Red-team gate | N/A keyless | `redteam.mjs` deferred pending a key (WP8 shipped an offline approximation). |
| pre-3 | Sample fallback plays | **PASS** | `?sample=1` plays with no key/session/**Turnstile**. Runbook note added. |
| pre-4 | LARGE TYPE persists | **PASS** | Toggle present on every masthead. |
| 1 | The pitch (`sonsteng-dev…`) | **PASS** | 200. |
| 2 | Platform home `/platform/` | **PASS** | Renders; "Watch a sample" (NO KEY REQUIRED) entry present. |
| 3 | Skills browser `/platform/skills/` | **PASS** | 200; 26 surveyed skills / 108 tasks. |
| 4 | Matter library `/platform/matters/` | **PASS** | 200; Meridian⇄Real toggle present. |
| 5 | A packet (m03 Petimeyer v. Ashcombe) | **PASS** | 8-part TOC + $/rubric/interview anchors render. Path: `/platform/matters/m03-tort-meridian/`. |
| 6 | **THE MOMENT — sample consultation** | **PASS** (was ROUGH, fixed) | Keyless replay → graded debrief renders incl. *"Askable, never asked: whether he had eaten."* **ROUGH found & fixed:** a stray "Verify you are human" Turnstile widget parked bottom-right during the keyless replay (commit `a6ad21e`); now shows none. |
| 7 | Rule 4.2 flag | **ROUGH (documented)** | The no-contact flag fires only *after* a live turn to the represented party → **needs a key**; not keyless-demoable. Runbook now marks beat 7 🔑 with the caveat + a product decision note. Not a defect (deliberate "realism trap" pedagogy) → left to Damien. |
| 8 | The money side `/platform/firm/` | **PASS** | Ledger renders; realization 87.9%, book-of-business + fee-mix charts. Minor copy nit flagged ("ships tonight"). |
| 9 | Templates + module `/platform/templates/` | **PASS** | 200. |
| 10 | 🔑 Memo critique | N/A keyless | Needs a key by design; critique page mints a session (now Turnstile-gated) and calls the provider. |
| editor | Editors' preview (`/edit`, John + admin tokens) | **PASS** (path corrected) | Edit page wraps the m03 packet with the green "You're editing" bar + EDIT/COMMENT on every block; review page = "Nothing pending. All caught up." **Correct edit path: `/edit/matters/<slug>/` (no `/platform/` prefix).** New editors' preview section added to the runbook. |

## Fixes committed

| Hash | Subject |
|---|---|
| `a6ad21e` | `fix(chat): never render Turnstile widget in keyless carve-outs (?sample/?bypass)` — `render()` now honors the sample/bypass carve-out that Cloudflare's `onloadTurnstileCallback` was bypassing. Rebuilt site mirror + DEV redeploy; verified live (no widget in sample mode). |

## Gates

- Worker unit tests: **174/174** (baseline held — chat.js is static frontend, not worker src).
- `build_site.py --check`: green (31 pages, all links resolve, leak-sweep clean, the sanctioned Turnstile request is the only external).
- No PROD deploy. DEV redeployed from `feat/weekend-fast-follows` via `deploy/deploy-dev.sh`.

## Screenshots

Shots in [`EP-2026-07-18-walkthrough/`](EP-2026-07-18-walkthrough/):

- `ep-sample-debrief.png` — beat 6, the keyless graded debrief (post-fix, no Turnstile widget).
- `ep-live-chat-turnstile.png` — live interview room showing the managed Turnstile widget parked bottom-right (live mode only).
- `ep-packet.png` — beat 5, m03 packet.
- `ep-firm.png` — beat 8, practice ledger.
- `ep-editor-page.png` — editors' preview, the `/edit` wrapped packet.
- `ep-editor-review.png` — admin review page (empty queue).

## Walkthrough-readiness verdict (~Wed 2026-07-23)

**READY for a keyless walkthrough.** Every keyless beat plays on live DEV; the one rough edge on the marquee moment (beat 6) is fixed and verified. Two beats are key-gated by design (beat 7 Rule 4.2, beat 10 critique) and the live interview (beat 6 live variant) — all fine if a key lands by Wednesday, otherwise the keyless sample carries the interview story. Product-taste calls (Rule 4.2 keyless demo, streaming default flip, one firm copy nit) are batched for Damien in the runbook's decision section.
