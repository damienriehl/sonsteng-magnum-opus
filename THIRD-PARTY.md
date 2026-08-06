# Third-Party Notices

The Legal Practicum platform is licensed under the **MIT License**
(© 2026 Damien Riehl / Legal Practicum). It bundles the following
third-party assets. Each is embedded (base64) so the platform makes
**zero external network requests** at runtime.

---

## Fonts

All three type families ship under the **SIL Open Font License 1.1**
(<https://openfontlicense.org>). The OFL permits bundling and embedding;
the reserved font names below are preserved.

### Fraunces

- **Role:** Display / headings
- **License:** SIL Open Font License 1.1
- **Authors:** Phaéton (Undercase Type) — Flavia Zimbardi & Phil Eaton
- **Source:** <https://github.com/undercasetype/Fraunces>
- **Embedded:** weights 400 / 600 / 900 (normal), woff2, in `site/platform/assets/fonts.css`

### Spectral

- **Role:** Body text, italics for stage directions & witness quotes
- **License:** SIL Open Font License 1.1
- **Authors:** Production Type — Emmanuel Besse & the Spectral project
- **Source:** <https://github.com/productiontype/Spectral>
- **Embedded:** weights 400 / 500 / 600 (normal), woff2, in `site/platform/assets/fonts.css`

### Fragment Mono

- **Role:** Instrument / machine voice (FOLIO IRIs, disclosure meters, turn
  counters, rubric point-weights, ledger math, timestamps, tags)
- **License:** SIL Open Font License 1.1
- **Author:** Wei Huang
- **Source:** <https://github.com/weiweihuanghuang/fragment-mono>
  (via Google Fonts: <https://fonts.google.com/specimen/Fragment+Mono>)
- **Embedded:** weight 400 normal + 400 italic, Latin subset, woff2, in `site/platform/assets/fonts.css`
- **Note:** Fragment Mono is the embedded mono per the design contract.
  The documented fallback (Spline Sans Mono, also OFL) was not needed —
  the Fragment Mono download succeeded.

---

## SIL Open Font License 1.1 — summary of obligations met

- The fonts are bundled with the software (permitted).
- Reserved Font Names (Fraunces, Spectral, Fragment Mono) are not used to
  name derivative/modified fonts — the faces are embedded unmodified.
- The full OFL text accompanies each upstream project (linked above) and
  applies to the embedded binaries.
