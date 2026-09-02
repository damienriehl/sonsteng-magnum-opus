---
title: Pitch Page Accessibility Conformance - Plan
type: fix
date: 2026-09-02
topic: pitch-accessibility-conformance
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
---

# Pitch Page Accessibility Conformance - Plan

## Goal Capsule

**Objective.** A prospective reader who depends on sufficient text contrast can read every word of the pitch at `legalpracticum.org/`, and the repository's accessibility gate keeps it that way.

**Means.** A palette pass on the pitch's muted, gold, and dark-section text tokens; a `main` landmark and skip link; the pitch page added to the default accessibility audit and the preflight gate.

**Product authority.** Persona UAT finding, September 2, 2026: `node tools/a11y_audit.js https://legalpracticum.org/` reported 29 text-contrast failures on the pitch page while every generated platform page passed. The evidence is `docs/uat/persona-uat-record.md` (A9 rows) and the audit transcript recorded there. Bounded items from the same audit (`lang`, navigation target sizes, close-button size, viewport meta) shipped separately under the UAT program; this plan owns the remaining design-level items.

**Open blockers.** The pitch palette is Damien's and John's taste. Resolve Before Planning: which of the two remedies below Damien prefers.

## Product Contract

### Summary

Bring the pitch page's text contrast to WCAG AA on every surface it paints, add the missing landmark and skip link, and put the page under the same automated accessibility gate as the platform.

### Problem Frame

The pitch is the first thing a dean, faculty member, or funder sees, usually on a phone, and it is the one public page the accessibility audit never covered. Its muted caption token (`#8a7f6d` on cream `#f4efe4`, 3.43:1) and gold eyebrow token (`#a9822f` on cream, 3.09:1) sit below the 4.5:1 threshold across captions, section numerals, chart labels, and citations. The hero call to action paints `#a46367` on claret (2.19:1). Inside the dark sections, claret text on near-black (`#7c1e2b` on `#1d1a16`, 1.71:1) is close to invisible for the proof summaries and the license links. Several failures were measured during the scroll-reveal transition, so the audit must run with reveal completed or reduced motion before the numbers are trusted.

### Key Decisions

- **Colors are decided by Damien, not the agent.** The agent proposes two remedies and implements the chosen one. Governs R1, R2.
- **The gate follows the fix.** The pitch joins the default audit list only when it passes, so preflight never fails on a known design debt. Governs R5.

### Requirements

- R1. Every text run on the pitch meets WCAG AA contrast (4.5:1 for normal text, 3:1 for large text) on the background it actually paints, including the claret hero button and the dark sections.
- R2. The remedy preserves the pitch's cream, claret, and gold identity: tokens are darkened or lightened, not replaced.
- R3. The pitch has a `main` landmark and a "Skip to content" link that moves keyboard focus, matching the generated platform pages.
- R4. Decorative oversized numerals and eyebrow ornaments that are not meant to be read are marked `aria-hidden` rather than recolored, and the audit treats them accordingly.
- R5. `tools/a11y_audit.js` includes the pitch in its default page list, and `tools/preflight.sh` fails on any pitch accessibility failure.
- R6. The audit runs with scroll-reveal completed (reduced motion or forced `.in` state) so measured colors are the resting colors.

### Acceptance Examples

- AE1. **Covers R1, R6.** Running the audit against the built pitch with reduced motion reports zero contrast failures.
- AE2. **Covers R3.** Pressing Tab once on the pitch focuses "Skip to content"; Enter moves focus into the main landmark.
- AE3. **Covers R5.** Reintroducing the old muted token makes the preflight accessibility gate fail.

### Outstanding Questions

**Resolve Before Planning**

- Remedy A (recommended): darken the muted token to about `#6b6155` and the gold eyebrow token to about `#8a6a24`, lighten the hero button text to cream, and lighten claret-on-dark to a cream or gold tone in the dark sections. Remedy B: keep the tokens and raise all affected text to large-text sizes and weights so 3:1 applies. Damien chooses.

**Deferred to Planning**

- Whether the chart labels in the skills-gap figure need their own token.

### Sources

- `docs/uat/persona-uat-record.md` — the A9 rows and audit transcript.
- `tools/a11y_audit.js` — default page list and thresholds.
- `site/index.html` — the pitch's inline stylesheet and tokens.
