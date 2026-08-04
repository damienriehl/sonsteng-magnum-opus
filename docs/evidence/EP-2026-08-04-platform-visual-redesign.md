# Platform visual redesign — release evidence

This evidence packet covers the Radical Casebook visual refresh without changing authored content. The executable source of the viewport and surface selection is [`tools/platform_browser_matrix.json`](../../tools/platform_browser_matrix.json).

## Acceptance matrix

- Widths: 1280px desktop; both sides of the 960px and 672px breakpoints (960/959 and 672/671); and 390px phone.
- Modes: refreshed baseline and Large Type.
- Families: home, module, skills, matter library, packet, facts, law, firm, templates, interview, and critique.
- Full-corpus safeguard: every generated HTML page is checked at 390px in both type modes, independently of the curated screenshot set.
- Print: templates, packet, facts, and law are rendered with print media; substantive `main` content must remain visible and interactive chrome must disappear.

## Automated evidence

Run these commands in a shell with access to the active Xwayland display and authorization cookie:

```sh
DISPLAY=:0 node tools/verify_platform_layout.js
DISPLAY=:0 node tools/verify_platform_layout.js --print
DISPLAY=:0 node tools/verify_chat_critique.js
DISPLAY=:0 node tools/a11y_audit.js
```

The layout report records page family, named viewport, type mode, failures, and computed primary/supporting/section/metadata values in `build/platform-layout-report.json`. Print results are written to `build/platform-print-report.json`. Browser launch failures exit nonzero; preflight reports an unreachable display as an explicit skip and never as a pass.

## Visual review set

Capture the matrix's `screenshots` families at 1280px baseline and 390px Large Type. Review for:

- page titles or propositions clearly dominant over supporting text;
- section headings stronger than labels and metadata;
- warm paper with differentiated card and inset surfaces;
- muted claret emphasis that does not resemble an alert;
- no text clipping, document overflow, card/control overlap, or inaccessible navigation;
- equivalent reading order at desktop and phone widths.

Screenshot and print review remains a release action: only record it as passed after inspecting the actual browser output. The machine-readable reports and screenshots should be retained with the release artifacts rather than committed as generated state.

## Observed release run — 2026-08-04

- Responsive and Large Type matrix: **244 / 244 passed**, including 68 generated pages in both modes at 390px.
- Print matrix: **8 / 8 passed** across packet, facts, law, and templates.
- Mock interview and critique matrix: **24 / 24 passed** across all named viewports and starting modes.
- Accessibility audit: **0 failures and 0 warnings** across 18 representative page/mode cases.
- Visual inspection: desktop home capture confirmed the proposition dominates its supporting paragraph, “The Three Volumes” and “The Apparatus” read as section landmarks, cards use differentiated stock, and the featured M2 card uses muted rather than alert-like claret.

The authoritative run used `/usr/bin/google-chrome` in headless mode because Chrome DevTools MCP was not exposed in this harness. The same Puppeteer/Chrome rendering path backs the repository’s headful browser gates.
