# Demo Runbook — John & Roger Walkthrough

*Prepared 2026-07-17→18; rehearsed end-to-end on DEV 2026-07-18 (WP9). Everything below works with ZERO API keys except the steps marked 🔑. Total time: ~20 minutes + discussion.*

*DEV is live: site **https://sonsteng-dev.damienriehl.com/platform/** · Worker (chat / critique / debrief + the `/edit` editor) **https://sonsteng-chat.damienriehl.workers.dev**. PROD is unchanged and stays held — enabling it is a documented one-command step in [`docs/prod-enable.md`](prod-enable.md); **do not run it during the demo.***

## What changed since the 2026-07-17 build (weekend fast-follows)

Nothing here alters the keyless demo path, but know it before you present:

- **Turnstile bot-gate (WP6).** The free session-mint (`GET /v1/session`) is now Cloudflare-Turnstile-gated. The live chat page loads a **managed** Turnstile widget parked bottom-right (it self-clears for real users, but can surface a small *"Verify you are human"* box on flagged networks). **The keyless paths are carved out and show NO widget:** `?sample=1` (never mints) and `?bypass=<token>` (server skips the gate). An untokened `GET /v1/session` now correctly returns **403 `turnstile_failed`** — that is the gate working, not an outage.
- **Streaming (WP4).** SSE token-streaming for the live interview exists behind a config flag; it ships **OFF** (`STREAMING="false"`). The client's typing indicator is the default experience. No demo impact.
- **Roger as a second editor (WP2).** Roger has his own edit token; his suggestions carry the **"RSH"** attribution label (John's carry "JOS"). Same review/accept gate.
- **Digest push (WP3).** When pending editor suggestions accumulate, a batched **ntfy** notification fires (topic `damien-homebox-736591e7`). The `/edit/review` page stays the canonical digest; push is strictly additive.
- **PROD injector wiring (WP1).** `EDIT_UPSTREAM`/`EDIT_ORIGIN` are now env-scoped; PROD enable is one documented command in `docs/prod-enable.md`. Still **not run**.

### ⚠ Added 2026-07-19 — canonical **direct-apply** + **redline history** (changes the editor story)

This landed *after* the runbook was written and it **replaces** the old "nothing goes live until
Damien accepts" framing. Present the new story, not the old one (details:
[`docs/direct-apply-daemon.md`](direct-apply-daemon.md), [`docs/history-browser.md`](history-browser.md)):

- **Edits publish themselves in ~2 minutes.** `DIRECT_APPLY=true`: a saved edit auto-accepts, the
  home-box apply daemon (`sonsteng-apply.timer`, every 2 min) patches canonical git, re-runs the
  validator + parity gates, rebuilds and redeploys **DEV**. There is no Damien approval step.
- **Safety net moved from *approval* to *history + revert*.** A **History** link in the editing
  banner opens `/edit/history/<doc-slug>` — attributed, coalesced revisions with per-revision and
  baseline redlines, plus one-click revert (editors *request*; admin approval executes on the next
  daemon tick).
- **The banner is honest about the service.** Fresh heartbeat → *"Your edits go live automatically
  (~2 min)"*; stale → *"Auto-apply paused … your edits are safe and queued."* It never claims live
  when the home box is down. An edit that can't apply cleanly surfaces as **"Needs attention — not
  applied"** with the edited text still visible (no silent loss).
- **Editorial pass.** A post-hoc quality review (session-end + daily 21:30 CT) files flags as block
  comments — quality is guarded *after* publish, not before it.
- **DEV only.** The daemon deploys `feat/canonical-docs` to the Hetzner DEV box and can never reach
  PROD.

## Before the meeting (5 minutes)

1. 🔑 **Paste your key (when it arrives):** on any chat page → **ADD YOUR KEY** → provider + key. Or enable the house pool: `cd app/worker && npx wrangler@4 secret put ANTHROPIC_API_KEY` (the $10/day cap is already enforced; the $3 demo reserve rides the bypass token at `~/.secrets/sonsteng-demo-bypass` — append `?bypass=<token>` to the chat URL).
2. 🔑 **Run the red-team gate once** (5 min): `cd app/worker && WORKER_URL=https://sonsteng-chat.damienriehl.workers.dev PROVIDER=anthropic API_KEY=sk-… node test/redteam.mjs` — expect all PASS.
3. **Fallback check:** the scripted sample needs nothing — no key, no session, **no Turnstile widget** — verify it plays: https://sonsteng-dev.damienriehl.com/platform/chat/index.html?matter=m05&persona=m05.per.halvard&title=State%20v.%20Halvard&client=Devon%20Halvard&sample=1
4. On iPads: tap **LARGE TYPE** in the masthead once — it persists.

## The walkthrough (order matters — it tells the trilogy story)

| # | Beat | URL / action | The line |
|---|------|--------------|----------|
| 1 | **The pitch** (2 min) | sonsteng-dev.damienriehl.com | "This is where we left off — the vision. Now everything after this slash is that vision, built." |
| 2 | **The platform home** (1 min) | `/platform/` | "Your practicum, rendered as a working law firm. Three volumes, twenty matters, one open data spine." |
| 3 | **Skills browser** (3 min) | `/platform/skills/` — expand *Fact gathering*, then a PM skill | "Your 17+9 survey is the spine — decomposed into 108 tasks, each mapped to the FOLIO open legal standard. The AI-era skills sit quarantined in their own extension shelf; the surveyed 26 are untouched." |
| 4 | **Matter library** (2 min) | `/platform/matters/` — flip the Meridian⇄Real toggle | "Every shape exists twice: once in the State of Meridian for pure skills work, once in a real state so students research actual law. All original, MIT-licensed — nothing NITA owns." |
| 5 | **A packet** (3 min) | open *Petimeyer v. Ashcombe* (m03) — scroll the 8-part TOC, show a witness statement, the billing exhibit, the rubric | "The 8-part anatomy from the course handbooks, point-weighted exactly like your grading system — 325 for the tort capstone." |
| 6 | **THE MOMENT — the interview** (5 min) | From m05 packet → **Watch a sample consultation** (keyless) — or live with a key: interview Devon yourself | "The client only volunteers so much. Rapport — an acknowledged emotion, an uninterrupted answer — unlocks more. Miss the right question and the debrief shows exactly what you never learned." Let the debrief render: *Askable, never asked: whether he had eaten.* |
| 7 | 🔑 **Rule 4.2 flag** (1 min) | m03 packet → the claret **ATTEMPT INTERVIEW (RULE 4.2)** button | "The realism trap is itself curriculum — try to interview the represented opponent and the platform teaches the no-contact rule, logged to the debrief's ethics score." **Needs a key:** the button opens the interview room keyless, but the no-contact flag fires only *after* your first message to the represented party (which mints a session). No keyless replay of this beat exists yet — see the decision note below. |
| 8 | **The money side** (2 min) | `/platform/firm/` | "Your 9 practice-management skills, as a living ledger. Realization 87.9% — write-downs erode worked value. Every number reconciles to the billing statements inside the packets." |
| 9 | **Templates + modules** (1 min) | `/platform/templates/`, then a module page | "The deliverable set — time sheets, SSNPs, the Kolb portfolio — and the three volumes as real teaching text." |
| 10 | 🔑 **Memo critique** (2 min, needs key) | any packet → *Submit a deliverable for critique* — paste any paragraph | "First-pass critique, criterion by criterion against the rubric. The re-write loop — AI does the first read, faculty coach the judgment." |

## Editors' preview — the reason John & Roger are here (3 min)

This is a walkthrough *for the two editors*, so close by showing the surface they'll actually use. It is DEV-only and needs no API key.

- **Their edit link.** John (and Roger) each get one bookmarkable link that opens the real practicum site wrapped in an editor: `https://sonsteng-chat.damienriehl.workers.dev/edit/<page-path>/?t=<their-token>` — e.g. `…/edit/matters/m03-tort-meridian/?t=…` (the page-path is the site path **without** the `/platform/` prefix; both `…/` and `…/index.html` resolve). Tokens live only in `~/.secrets/sonsteng-editor-tokens` — resolve the URL, send the finished link, never the token in the clear.
- **What they see.** A green editing bar across the top and **EDIT** / **COMMENT** affordances on every block (this m03 packet exposes dozens). **Since 2026-07-19 the bar reads *"Your edits go live automatically (~2 min)"* whenever the apply service is healthy** — a saved edit auto-accepts, the validator + parity gates re-run, and the change publishes to DEV on its own. (Plain-language one-pager: [`docs/editor-guide-for-john.md`](editor-guide-for-john.md).) *Demo tip: make one small edit at the start of this section and come back to it — it will be live on the page before you finish talking.*
- **The safety net is history, not approval.** The **History** link in the bar opens the attributed redline history for that document, with baseline + per-revision diffs and one-click revert. Show it — it is the answer to "what if I break something?"
- **Where suggestions and comments land.** Damien's canonical review page: `https://sonsteng-chat.damienriehl.workers.dev/edit/review?t=<admin-token>` — grouped by source, word-level diffs, plus revert requests and editorial-pass flags. John's edits are labeled **JOS**, Roger's **RSH**. Queue is expected **empty** until they start (direct-apply drains it automatically; comments still wait for Damien).

## If something goes wrong

- **Chat errors out** → the sample replay (step 3's URL) always works; it's scripted, labeled as such, needs no key/session/Turnstile, and shows the identical experience.
- **Wifi/NAT weirdness, or a "Verify you are human" box appears** → the bypass token both exempts you from IP limits **and** skips the Turnstile gate; append `?bypass=…` to the chat URL (token at `~/.secrets/sonsteng-demo-bypass`). The `?sample=1` replay never triggers Turnstile at all.
- **A page looks off on the conference screen** → LARGE TYPE toggle; everything reflows.
- **The editing bar says "Auto-apply paused"** → the home box's `sonsteng-apply.timer` isn't checking in. Edits are still saved and queued, so say exactly that; don't promise ~2 min. Recover with `systemctl --user start sonsteng-apply.timer` on the home box after the meeting.
- **"Is this real law?"** → Meridian tier: internally consistent invented canon, on purpose. Real tier: facts-only by design — *students find the law*; instructor keys cite the authorities, flagged for your verification.

## The asks to close on (from the standing open questions)

1. The name — "Sonsteng Magnum Opus" / "Advocacy Renaissance" / John's call.
2. Institutional home — Mitchell Hamline C-LAB, IGUL, independent, joint.
3. Their roles — authors, namesakes, advisors.
4. Blessing to restore + republish the crown-jewel articles (the OCR project).
5. Whether Trialbook's corpus folds in or stays adjacent.

## Open product decisions surfaced by the 2026-07-18 rehearsal (for Damien)

These are product-taste calls, not defects — noted here so the walkthrough narrative is a deliberate choice:

1. **Rule 4.2 has no keyless demo (beat 7).** The no-contact lesson fires only after a live turn to the represented party, so it needs a key. Options: (a) leave as-is and demo it live once a key lands; (b) render the Rule 4.2 no-contact banner immediately on load for `represented=1` matters (keyless-demoable, but softens the "realism trap is itself curriculum" pedagogy); (c) add a scripted `?sample=1` replay of a Rule 4.2 attempt. Recommend (a) for the walkthrough if a key is in hand by then, else (c) as a small follow-up.
2. **Streaming default (WP4).** Streaming ships OFF. Decide whether to flip `STREAMING="true"` for the live-interview beat before the walkthrough (a D7 rehearsal-with-key question).
3. **Firm page copy nit.** The ledger sub-header reads *"One reporting period ships tonight; the dataset is a single trailing-12-month snapshot."* — the "ships tonight" phrasing is a build-time aside that reads oddly in a demo; consider softening to "single trailing-12-month snapshot."
