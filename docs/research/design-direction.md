# Design Direction — Radical Casebook

*Adopted 2026-08-04. This document is the binding visual contract for generated Platform pages, the interview, and the critique view. It supersedes “The Practicum Press” direction while preserving the shared-asset, accessibility, editor-identity, and generated-source boundaries.*

## 1. Aesthetic direction

**Radical Casebook** is a bold contemporary editorial system: warm casebook stock, dark ink, deep desaturated claret, differentiated paper surfaces, decisive Fraunces display type, readable Spectral prose, and restrained Fragment Mono instrumentation.

The hierarchy is the memorable feature. A primary proposition must dominate its supporting paragraph; section landmarks must read as editorial signposts rather than incidental metadata; card titles must outrank their descriptions. Claret supports emphasis without resembling an alert. Large saturated-red content fields are not part of the ordinary vocabulary.

## 2. Typography

All fonts are embedded and same-origin through `site/platform/assets/fonts.css`.

| Role | Face | Treatment |
|---|---|---|
| Proposition, page title, section and component headings | Fraunces | Bold to black; tight tracking; compact leading |
| Body, explanatory copy, quotations | Spectral | Comfortable baseline; generous leading; readable measure |
| Metadata, codes, counts, actions | Fragment Mono | Uppercase where appropriate; tabular numerals |
| Editorial label | Fragment Mono | Promoted above micro-metadata, bold, claret, never tiny |

The shared scale lives in `theme.css`. Its relationship is binding: proposition/page title → section landmark → component title → body copy → metadata. The default is readable without assistance. `html.type-lg` overrides every hierarchy-bearing scale variable and remains visibly larger without flattening those relationships.

## 3. Palette and surfaces

The named tokens are authoritative:

```css
:root {
  --paper: #f3ead5;
  --paper-deep: #e1cfa9;
  --surface-card: #fffaf0;
  --surface-featured: #ede0c7;
  --surface-inset: #eadcbe;
  --ink: #281e18;
  --ink-soft: #5f5046;
  --claret: #78363e;
  --claret-strong: #642b33;
  --brass: #9a7131;
  --green: #30483a;
  --border-strong: #3a2920;
}
```

- Page and cards must not collapse into an identical field: ordinary cards use `--surface-card`, inset documents use `--surface-inset`, and offset shadows use `--paper-deep`.
- Claret is a quiet editorial accent. `--claret-strong` is for accessible text and focus; broad saturated red surfaces are reserved for genuine status or safety meaning.
- Brass supports rules and secondary accents. Green communicates success and real-jurisdiction state. Meaning never depends on color alone.
- Borders may be unapologetically printed and structural. Corners remain crisp; avoid pills, glass, blur, neon, and ornamental gradients.

## 4. Shared primitives

`site/platform/assets/theme.css` is the single token and global-primitive authority. Consumers compose `.editorial-label`, `.brass-rule`, `.running-head`, `.card`, `.card--featured`, `.doc-card`, chips, meters, stage directions, ethics flags, TOC rails, KPI tiles, ledgers, segmented controls, and shared chrome. Page-specific layout remains with the generator or owning interface.

A featured card may use a warmer contrasting paper and claret border, but it must remain quieter than its heading and must not resemble an alert. Actions use mono instrument voice; substantive prose does not.

## 5. Surface application

- **Home and modules:** oversized propositions, promoted section labels, strong section headings, differentiated volume cards, and clear apparatus separation.
- **Skills and matters:** editorial index rhythm, visible scanning landmarks, shape and taxonomy metadata kept subordinate.
- **Packets and documents:** restrained reading column, strong part landmarks, inset case-file surfaces, print-safe reading order.
- **Firm views:** broadsheet ledger structure, typographic KPI hierarchy, and tabular machine data.
- **Interview:** a quiet consultation room with readable dialogue, clear speaker hierarchy, accessible controls, and editorial stage directions.
- **Critique:** a manuscript and grader-ledger relationship that stacks cleanly at narrow widths.

Existing authored wording, data, destinations, curriculum, information architecture, and editor identity are not visual-design material and must remain unchanged.

## 6. Accessibility and modes

WCAG 2.1 AA is a floor. Preserve one visible `h1`, ordered headings, semantic landmarks and controls, non-color labels, 48px touch targets, 16px inputs, visible focus, reduced motion, increased contrast, and 200% zoom without horizontal scrolling.

Large Type is a first-class persistent mode shared by generated pages, interview, and critique. It enlarges the readable baseline while maintaining clear hierarchy and usable controls. Apply the mode before paint when possible to prevent a flash at the wrong size.

## 7. Responsive, motion, and print

Layouts preserve reading order as columns collapse. Text must not clip or overlap. Motion is restrained to short rule, underline, reveal, and lift transitions; `prefers-reduced-motion` removes transforms and nonessential animation.

Print removes interactive chrome, texture and decorative washes; uses dark ink on white; preserves document order; avoids breaking cards where practical; and never exposes instructor-only material in learner packets.

## 8. Convergence and source boundaries

1. Reuse the three local font roles and the shared theme; introduce no third-party runtime requests.
2. Put tokens and reusable primitives in `theme.css`; do not invent per-page palettes, fonts, radii, or shadows.
3. Edit generator templates and authored assets, then regenerate. Generated Platform HTML and generated `platform.css`/`platform.js` are derived state.
4. Preserve durable editor identity: stable block IDs and source references identify content; positional order is only a placement diagnostic.
5. Keep substantive copy intact. Casing, wrapping, grouping, and placement may change only as presentation without changing words or meaning.
6. Verify representative desktop, narrow, default, Large Type, contrast, reduced-motion, and print presentations before release.
