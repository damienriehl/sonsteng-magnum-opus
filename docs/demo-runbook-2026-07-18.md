# Demo Runbook — John & Roger Walkthrough

*Prepared overnight 2026-07-17→18. Everything below works with ZERO API keys except the two marked 🔑 steps. Total time: ~20 minutes + discussion.*

## Before the meeting (5 minutes)

1. 🔑 **Paste your key (when it arrives):** on any chat page → **ADD YOUR KEY** → provider + key. Or enable the house pool: `cd app/worker && npx wrangler@4 secret put ANTHROPIC_API_KEY` (the $10/day cap is already enforced; the $3 demo reserve rides the bypass token at `~/.secrets/sonsteng-demo-bypass` — append `?bypass=<token>` to the chat URL).
2. 🔑 **Run the red-team gate once** (5 min): `cd app/worker && WORKER_URL=https://sonsteng-chat.damienriehl.workers.dev PROVIDER=anthropic API_KEY=sk-… node test/redteam.mjs` — expect all PASS.
3. **Fallback check:** the scripted sample needs nothing — verify it plays: https://sonsteng-dev.damienriehl.com/platform/chat/index.html?matter=m05&persona=m05.per.halvard&title=State%20v.%20Halvard&client=Devon%20Halvard&sample=1
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
| 7 | **Rule 4.2 flag** (1 min) | m03 packet → the claret **ATTEMPT INTERVIEW (RULE 4.2)** button | "The realism trap is itself curriculum — try to interview the represented opponent and the platform teaches the no-contact rule, logged to the debrief's ethics score." |
| 8 | **The money side** (2 min) | `/platform/firm/` | "Your 9 practice-management skills, as a living ledger. Realization 87.9% — write-downs erode worked value. Every number reconciles to the billing statements inside the packets." |
| 9 | **Templates + modules** (1 min) | `/platform/templates/`, then a module page | "The deliverable set — time sheets, SSNPs, the Kolb portfolio — and the three volumes as real teaching text." |
| 10 | 🔑 **Memo critique** (2 min, needs key) | any packet → *Submit a deliverable for critique* — paste any paragraph | "First-pass critique, criterion by criterion against the rubric. The re-write loop — AI does the first read, faculty coach the judgment." |

## If something goes wrong

- **Chat errors out** → the sample replay (step 3's URL) always works; it's scripted, labeled as such, and shows the identical experience.
- **Wifi/NAT weirdness** → the bypass token exempts you from IP limits (append `?bypass=…`).
- **A page looks off on the conference screen** → LARGE TYPE toggle; everything reflows.
- **"Is this real law?"** → Meridian tier: internally consistent invented canon, on purpose. Real tier: facts-only by design — *students find the law*; instructor keys cite the authorities, flagged for your verification.

## The asks to close on (from the standing open questions)

1. The name — "Sonsteng Magnum Opus" / "Advocacy Renaissance" / John's call.
2. Institutional home — Mitchell Hamline C-LAB, IGUL, independent, joint.
3. Their roles — authors, namesakes, advisors.
4. Blessing to restore + republish the crown-jewel articles (the OCR project).
5. Whether Trialbook's corpus folds in or stays adjacent.
