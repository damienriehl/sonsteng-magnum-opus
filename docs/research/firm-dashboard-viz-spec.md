# Firm Dashboard — Visualization Spec

*Produced 2026-07-17 by the deepen-plan pass (dataviz skill applied). Build-ready: forms, encodings, axes, legends, tooltips, table twins, print, and layout are all fixed — the implementing agent needs no design judgment. Static self-contained HTML, inline SVG + CSS-only marks, no libraries.*

**Palette reconciliation (binding):** this spec's `.viz-root` tokens are swap-ready. The dashboard lives inside "The Practicum Press" (see `design-direction.md`): page chrome, cards, and typography use the Press tokens (Fraunces/Spectral/Fragment Mono, paper/brass/claret/green). For **chart marks**, map the slots below to Press-derived hues *only if* they preserve this spec's stated contrast/ΔE requirements; any slot that fails keeps the validated hex below (both palettes below passed the dataviz validator in light and dark). Status semantics (good→critical) always keep icon+label pairing regardless of hue.

## 0. Global foundations

### 0.1 Validated CSS variables (paste verbatim; swap per the rule above)

```css
.viz-root{
  --surface-1:#fcfcfb; --page:#f9f9f7;
  --ink-1:#0b0b0b; --ink-2:#52514e; --ink-muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --pos:#006300;
  --cat-1:#2a78d6; --cat-2:#1baf7a; --cat-3:#eda100; --cat-4:#008300;   /* categorical, fixed order */
  --ord-1:#86b6ef; --ord-2:#3987e5; --ord-3:#184f95;                    /* ordinal 3-step (funnel) */
  --div-under:#2a78d6; --div-mid:#f0efec; --div-over:#e34948;           /* diverging (budget) */
  --st-good:#0ca30c; --st-warning:#fab219; --st-serious:#ec835a; --st-critical:#d03b3b;  /* status */
}
@media (prefers-color-scheme:dark){ .viz-root:not([data-theme="light"]){
  --surface-1:#1a1a19; --page:#0d0d0d; --ink-1:#ffffff; --ink-2:#c3c2b7; --ink-muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10); --pos:#0ca30c;
  --cat-1:#3987e5; --cat-2:#199e70; --cat-3:#c98500; --cat-4:#008300;
  --ord-1:#6da7ec; --ord-2:#2a78d6; --ord-3:#184f95;
  --div-under:#3987e5; --div-mid:#383835; --div-over:#e66767;
}}
```

3-state theme control (Auto/Light/Dark) via `data-theme` on `.viz-root`; dark is a *selected* palette, never filter-invert.

### 0.2–0.8 Rules (apply to every chart)

- **Type:** dashboard-internal text follows the Press type system; stat values proportional figures; `tabular-nums` only on table rows and axis ticks.
- **Marks:** bars ≤24px, 4px rounded data-end/square baseline; lines 2px round-join; markers r≥4 with 2px surface ring; area fills 10% opacity; **2px surface-color gap** between touching/stacked segments; never a stroke to separate marks. Gridlines/axes hairline 1px solid, recessive.
- **Legend/labels:** legend for ≥2 series only; direct-label selectively (endpoint/extreme/the-one-that-matters); **text always in ink tokens, never series color** (identity via swatch); in-fill labels pick white-or-ink by luminance.
- **Interaction:** mark = hit target (≥24px); tooltip = value leads (strong), label secondary, swatch-keyed; `textContent` only, never `innerHTML` (names are untrusted JSON). Tooltips enhance, never gate: every card ships a **"Table" toggle** rendering the equivalent `<table>` (also the WCAG/CVD relief channel). Keyboard focus mirrors hover.
- **Print:** force light tokens, white cards, no hover/shadows, `break-inside:avoid`, expand every table twin inline.
- **Patterns toggle:** 45°/135° line textures on categorical/ordinal fills; off by default; auto-on under `forced-colors`.
- **Filter row (one, global):** Reporting period (This month / QTD / YTD / Trailing 12 mo) · Timekeeper (All/A/B) · Matter status (All/Open/Closed). Never per-card filters. Re-slice holds the old render at reduced opacity — no skeleton flash.
- **Card shell:** 12-col grid; each card = title (sentence case) + one-line **teaching caption** + chart + Table/Patterns toggles; no nested scroll.

## 1. KPI row — 6 stat tiles (tile 1 = hero ≥48px; others ~28px; 2-col wrap on mobile)

| # | Label | Value | Delta vs | Spark | Lesson (skill) |
|---|---|---|---|---|---|
| 1 | Fees collected, YTD | $ compact | prior year | 12-mo | the number the firm lives on (S1, S5) |
| 2 | Realization rate | billed ÷ worked % | prior qtr | 12-mo | write-downs erode worked value (S1) |
| 3 | Collection rate | collected ÷ billed % | prior qtr | 12-mo | billing ≠ cash (S1) |
| 4 | AR over 90 days | $ + (NN%) | prior month | 12-mo | old AR rarely collects (S1, S4) |
| 5 | Trust balance | $ + ✓/⚠ reconcile chip | — | none | client money ≠ firm money (S8/ethics) |
| 6 | Budget variance, YTD | ±% | plan | none | plan vs reality (S5) |

Deltas colored by direction×goodness (`--pos` good / `--st-critical` bad / `--ink-muted` neutral) with named comparison period. Tile 4 chip `--st-serious` if AR>90 exceeds 10% of AR. Tile 5 chip ✓ "Reconciled" / ⚠ "Discrepancy". Sparklines bare (no tooltip), `--ink-muted` low-opacity with current point `--cat-1`.

## 2. The seven charts (story order)

1. **Book of business — fees by matter** *(lesson: revenue concentration; S6, S8)*. Horizontal bars, sorted desc, **all bars single hue `--cat-1`** (nominal categories never get a value ramp). Annotation: thin `--axis` marker at the cumulative-80% boundary, "Top 5 = 80% of fees". Direct-label top 3 tips only. Tooltip: name + fees (strong) + fee type + % of book. Table: matter · fee type · fees · cumulative %.
2. **Fee-arrangement mix** *(lesson: how you're paid shapes risk & cash timing; S1)*. Single 100% stacked horizontal bar ($ share); optional second bar (matter-count share) beneath. Hourly `--cat-1`, Contingency `--cat-2`, Flat `--cat-3`, Retainer `--cat-4` — fixed order, never recolored on filter. Legend required; in-segment % labels only where they fit. Table: fee type · matters · fees · %.
3. **Utilization — billable hours vs target** *(lesson: time is inventory; S4, S5)*. Grouped monthly columns × 2 timekeepers (A `--cat-1`, B `--cat-2`), 2px surface reference line "Target 150h" labeled once. Direct-label final month only. Table: month · A · B · total · % of target.
4. **Realization funnel: Worked → Billed → Collected** *(the crown lesson: every dollar leaks twice; S1)*. 3 descending horizontal bars, shared baseline, ordinal ramp `--ord-1/2/3`. Between bars, `--ink-muted` brackets: "−$XX write-downs · realization 88%" / "−$XX write-offs · collection 94%" — **these annotations are the lesson, always shown**. No legend. Table: stage · $ · % of worked · step loss · step rate.
5. **AR aging** *(lesson: old invoices don't pay; S1, S4 — the honest home of status color)*. Stacked horizontal bar by bucket (firm total + one row per top-8 AR matter): 0–30 `--st-good`, 31–60 `--st-warning`, 61–90 `--st-serious`, 90+ `--st-critical`, **every bucket icon+label** (never color-alone). Direct-label the 90+ segment. Table: matter × buckets × total.
6. **Trust balances & reconciliation** *(lesson: to-the-penny or it's an ethics violation; S8)*. Horizontal bars (matters with trust balances) in single `--cat-1`; per-row ✓/⚠ status chip; firm-total "Trust ledger vs bank" banner above (green ✓ balanced / red ⚠ off-by-$X). Direct-label all tips. Table: matter · balance · reconciled? · last reconciled.
7. **Budget vs actual** *(lesson: watching divergence is the discipline; S5)*. Diverging horizontal bars centered on zero; color by **`variance_is_good`**, not sign (over on revenue = good; over on expense = bad): favorable `--div-under`, unfavorable `--div-over`. Legend: Favorable/Unfavorable with icons. Direct-label variances. Table: line · budget · actual · variance · % · favorable?

## 3. Layout (single scroll)

Filter row → KPI row → [VIZ 1 | VIZ 2] → [VIZ 3 | VIZ 4] → VIZ 5 (full) → VIZ 6 (full) → VIZ 7 (full) → downloads footer (raw `firm.json` + per-table CSV — plan A4). Pairs stack on iPad-portrait; axis bands always included.

## Validator evidence

Categorical slots light PASS (aqua/yellow sub-3:1 → relief = direct labels + table twins, both shipped) and dark PASS (green↔yellow floor-band ΔE 10.3 → relief = 2px gaps + direct labels). Ordinal all-pass both modes. Status/diverging use fixed documented steps with mandatory icon+label.
