# Design Direction — "The Practicum Press"

*Produced 2026-07-17 by the deepen-plan pass (frontend-design skill applied against the existing pitch page). This is the binding aesthetic contract for every page, the chat app, and the critique view. The convergence contract (§10) is what keeps ~20 agents producing one volume.*

## 1. Aesthetic direction

**"The Practicum Press" — Letterpress Chambers meets the Machine Spine.** A 50-year legacy rendered as a *living casebook*: warm letterpressed law-review authority (deckled cream stock, brass hairline rules, claret running heads, forest-green cloth binding) — with the machine spine (FOLIO IRIs, disclosure-tier meters, turn counters, rubric point-weights, ledger math) rendered in a precise technical mono that reads as *legal machinery in the margins*. The legacy is the page; the AI future is the apparatus around the page.

**The one unforgettable thing:** the **brass hairline rule + running head** system — every page/card bound by thin brass rules with a small-caps mono running head (module code, matter slug, IRI, turn count) sitting *in* the rule, like a printed brief's header.

**Tone by surface:** platform site = editorial law-review; chat app = hushed consultation room (in-fiction moments as italic *stage directions* in brass-ruled inserts); critique view = editor's marked-up galley proof with a grader's ledger margin.

## 2. Typography (all OFL, all embedded, zero external requests)

| Role | Face | Notes |
|---|---|---|
| Display/headings | **Fraunces** (already embedded in `site/index.html`) | variable; `opsz` high, `wght` 500–900; `letter-spacing:-0.02em` large |
| Body | **Spectral** (already embedded) | 400/500/600/700 + italic for stage directions & witness quotes |
| Instrument/mono | **Fragment Mono** (NEW — download woff2, OFL, log in THIRD-PARTY.md; fallback face if unavailable: Spline Sans Mono, never a system mono) | uppercase labels `letter-spacing:.08em`; `font-variant-numeric:tabular-nums` for all numbers/money |

Extract the six existing `@font-face` blocks into shared **`site/platform/assets/fonts.css`**; add Fragment Mono there. Fallback stacks: Fraunces/Spectral → `Georgia, serif`; Fragment Mono → `ui-monospace, Menlo, Consolas, monospace`. **Never** Inter/Roboto/Arial/system-ui.

Type scale (rem, fluid): `--fs-mono-xs:.72rem; --fs-xs:.82rem; --fs-sm:.92rem; --fs-base:1.06rem; --fs-md:1.2rem; --fs-lg:1.45rem; --fs-xl:clamp(1.7rem,1.2rem+2vw,2.3rem); --fs-2xl:clamp(2.2rem,1.4rem+3.5vw,3.4rem); --fs-display:clamp(2.8rem,1.6rem+5vw,5rem)`. Body line-height 1.65; headings 1.12; prose measure ≤68ch (62ch in chat).

## 3. Palette (CSS variables — extends the pitch page; do not alter inherited hexes)

```css
:root{
  /* inherited */
  --paper:#f4efe4; --ink:#1d1a16; --ink-soft:#544d43; --ink-faint:#8a7f6d;
  --brass:#a9822f; --brass-lite:#c19a44; --claret:#7c1e2b; --claret-deep:#5c141d;
  --green:#2c4636; --line:rgba(29,26,22,.16); --line-soft:rgba(29,26,22,.09);
  --shadow:0 22px 60px -28px rgba(40,20,10,.55);
  /* platform extensions */
  --paper-2:#efe8d9; --paper-3:#e8dfcc; --paper-edge:#dcd0b8;
  --brass-wash:rgba(169,130,47,.10); --claret-wash:rgba(124,30,43,.07); --green-wash:rgba(44,70,54,.09);
  /* semantic */
  --tier-volunteered:#2c4636; --tier-revealed:#a9822f; --tier-rapport:#7c1e2b;
  --tier-concealed:#544d43; --tier-unknown:#8a7f6d; --flag-ethics:#7c1e2b;
  --meter-track:rgba(29,26,22,.10);
  --ok:#2c4636; --warn:#a9822f; --stop:#7c1e2b; --ink-invert:#f4efe4;
}
```

- **Real vs fictional tier signal:** Meridian = brass family + mono "⌘ MERIDIAN" chip; real-state = forest green + postal chip (`MN`, `NY`…). Never color alone — always the chip label.
- **Contrast pre-verified:** `--ink`/`--paper` ≈13:1; `--ink-soft` ≈7:1; **brass is decorative/large-text only** (≈3.3:1 — small colored text uses claret/ink); `--ink-invert` on green/claret-deep ≥7:1.

## 4. Shared tokens & texture

```css
:root{
  --space:1rem; --sp-2:.5rem; --sp-3:.75rem; --sp-6:1.5rem; --sp-8:2rem; --sp-12:3rem; --sp-16:4rem;
  --maxw:72rem; --maxw-read:44rem; --gutter:clamp(1.1rem,3vw,2.5rem);
  --radius:3px; --radius-card:5px;      /* print-crisp, never pill */
  --rule:1px; --rule-bold:2.5px;
  --ease:cubic-bezier(.2,.7,.2,1); --dur-fast:140ms; --dur:260ms; --dur-slow:520ms;
}
```

- Page base: `--paper` + the pitch page's two fixed radial washes (brass top-right, claret top-left).
- Paper grain: inline-SVG `feTurbulence` noise (data-URI), opacity .03, `mix-blend-mode:multiply`, on `body::before`.
- Brass rules: 1px brass dividers with a 2px claret left tick; cards = 1px `--line` + `--paper-edge` inner hairline bevel; data wells = `--paper-3` inset.
- **No** glassmorphism, blur, neon, text drop-shadows, flat solid fills.

## 5. Layout per deliverable

- **Global chrome:** sticky masthead (brass top rule; "SONSTENG PRACTICUM" left; mono docket-code running head right, e.g., `M2 · MATTERS · REAGAN-V-JACOBSON`); mono brass breadcrumbs; forest-green cloth footer (MIT + "no platform fees; bring your own key" + THIRD-PARTY link). 12-col grid `--maxw`; prose `--maxw-read`; headings hang into the left gutter with claret ticks; mono metadata in a right rail.
- **Module pages (M1/M2/M3):** oversized Fraunces module numeral bleeding off-margin; one-line italic thesis; ruled index rows of skills/matters. Volume accents: M1 brass, M2 claret, M3 green.
- **Skills browser:** table-of-authorities feel; per-skill brass-ruled card with mono FOLIO chip + `mapping_confidence` badge (`EXACT`/`NEAR`/`PARENT`/`NO-FOLIO` in green/brass/faint/claret); expand → tasks → subtasks as nested ruled rows. **Extension set visually quarantined** under a claret-ruled "EXTENSION" header. Bidirectional skill↔matter chips.
- **Matter library:** shape-first rows; **Meridian ⇄ real-state segmented toggle** top-right (accent family brass↔green, cross-fade `--dur`, no layout shift); matter cards with side chips, skill chips, "OPEN PACKET →"; two-sided matters show a split rule + per-side lock icons.
- **8-part packets:** single long page, `--maxw-read`, sticky mono TOC rail (the 8 canonical parts, scroll-spied); Fraunces part numerals `01`–`08` hanging in the gutter; case-file sub-docs as inset `--paper-2` document cards. Instructor notes NEVER on this page — one muted "Instructor materials →" link. **Print CSS required:** strip chrome/washes, black-on-white, URLs after links, `break-inside:avoid` on document cards, page-break per part, instructor notes never in print output.
- **Firm dashboard:** "practice ledger" broadsheet — firm identity card + KPI tiles (Fraunces big numbers, mono deltas, tabular-nums); charts per `firm-dashboard-viz-spec.md` **mapped to this palette** (see reconciliation note there); ledger tables mono right-aligned; JSON/CSV download chips.

## 6. Chat app (consultation room)

Centered column ≤52rem, base type ≥`--fs-md`. Brass-ruled case header with matter title, persona name/role, and the **turn counter `03 / 20` living in the rule**. No bubble tails: lawyer = right, `--paper-2` card, brass left rule; client = left, `--paper`, claret name label, slightly larger Spectral. First load = a tappable "chambers card" suggested opening (*"You may wish to begin: 'Thank you for coming in…'"*) + "suggest another" mono link — never an empty box. Input: tall brass-ruled textarea, ≥18px, ≥48px targets. Turn ~15 warning + turn-20 wrap-up + connection errors all render as *stage directions* (`--paper-3` well, italic, claret tick): "[The client glances at their watch.]" **Rule 4.2 flag:** in-line claret-bound "PROFESSIONAL RESPONSIBILITY — RULE 4.2 · NO CONTACT" insert with a two-sentence teaching note + mono "LOGGED TO DEBRIEF" tag (claret reserved for this + real errors). Transcript export card: **Copy (execCommand fallback mandatory)** + Download .txt/.md; privacy note always visible. **Debrief view:** post-interview memo on paper — tier sections (Elicited = green ✓ chips / Askable-never-asked = brass / Rapport-gated-never-earned = claret with the needed trigger in mono), Rule 4.2 claret box, Axis-B relational scores, encouraging "graded return" tone.

## 7. Critique view (galley proof)

Two-column desktop / stacked mobile: left = pasted memo on `--paper`, manuscript feel; right = grader's ledger — per-criterion brass-ruled cards (Fraunces criterion name, mono `7 / 10 PTS` + slim brass meter, Spectral feedback; weak = claret tick, strong = green), total card up top. Closing green-bound "Revise & resubmit" insert (Sonsteng re-write loop). Oversize rejection = calm brass insert, never a raw error. Small mono spend/turn note consistent with chat.

## 8. Motion

Print-inspired, restrained: staggered page reveal (opacity + 8px rise, 60ms steps); brass rules **draw in** (`scaleX 0→1`, `--dur-slow`); link underlines wipe left→right; cards lift 2px on hover; client messages fade+rise; claret ruled-tick "considering" indicator (not bouncing dots); meters animate width on first view. **`prefers-reduced-motion: reduce` disables all transforms/staggers — required on every surface.**

## 9. Accessibility (WCAG 2.1 AA — hard requirements)

Contrast per §3 pairings; never color-alone (chips/icons/labels accompany every hue signal); visible 2px claret focus ring on everything interactive; real landmarks + one h1 + ordered headings; `<table>` for ledgers; `<button>` for actions; `aria-current` on TOC/nav; `aria-live="polite"` on chat stream, turn counter, cap banners; ethics flag `role="status"`. Touch ≥48px; inputs ≥16px (iOS zoom); 200% zoom without horizontal scroll. **Large-type mode (first-class, for John & Roger):** persistent toggle setting `.type-lg` on `<html>` (`--fs-base:1.28rem` etc.), persisted in localStorage. Respect `prefers-contrast` (darken brass→ink/claret for text).

## 10. Convergence contract (binding on every implementing agent)

1. One tokens partial: **`site/platform/assets/theme.css`** (variables, fonts import, grain/washes, base type, rule/running-head/card primitives, chips, meters, focus rings, reduced-motion, `.type-lg`). Compose from primitives; never invent per-page palettes/fonts/radii/shadows.
2. Named primitives (identical class names everywhere): `.brass-rule`, `.running-head`, `.card`, `.doc-card`, `.chip` (+ `.chip--folio/--tier-*/--state/--meridian/--coming-soon`), `.meter`, `.stage-direction`, `.ethics-flag`, `.toc-rail`, `.kpi-tile`, `.ledger`, `.segmented-toggle`.
3. Self-contained: zero external network requests (fonts local, SVG textures data-URIs); clipboard = execCommand fallback.
4. Mono voice only for machine content (codes, IRIs, counts, money, timestamps, tags) — never prose.
5. Accent discipline: brass = primary/interactive; claret = emphasis/in-fiction errors/Rule 4.2 (reserve it); green = success/real-tier/binding. Backgrounds always textured.

*Note on the "self-contained" ethos (performance-review reconciliation): "no external requests" means no third-party/CDN. Shared **same-origin** assets (`theme.css`, `fonts.css`) are required — with ~150 generated pages, per-page embedded CSS re-downloads ~30–50KB on every navigation. Packet page budget: target ≤150KB, ceiling 250KB HTML+CSS; split oversized case files into linked sub-pages.*
