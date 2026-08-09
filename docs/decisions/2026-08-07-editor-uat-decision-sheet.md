# Decision Sheet — Editor UAT

Date: August 7, 2026  
Scope: John’s copy-editing experience after production UAT

> **Resolved 2026-08-08.** Damien chose to make all human-readable taxonomy text
> editable and to require a Publisher to explicitly release an approved immutable
> batch. Approval is not publication. This sheet remains historical framing; the
> current implementation contract is
> `docs/plans/2026-08-09-001-feat-taxonomy-publisher-batches-plan.md`.

The UAT found and fixed mechanical failures; those did not need a decision. The two product boundaries below are now resolved as recorded above.

## D1 — Should the 26 skill taxonomy names be directly editable?

**What UAT found.** Task names and task descriptions on the Skills page are editable. The 26 skill names themselves are deliberately read-only because they are rendered inside `<summary>`, outside the editor’s stable block-walker contract. They are also recorded as exact survey terminology and act as taxonomy labels across the practicum.

**Options.**

1. **Keep skill names canonical and read-only.** John can edit all explanatory task copy, but changing a survey skill label remains a deliberate source change. Lowest risk; preserves exact terminology and every existing editor block index.
2. **Create a separate editable display label.** Preserve the canonical survey name as data, while allowing a friendlier public label. Clear semantics, but adds a second name that must be explained and synchronized.
3. **Change the editor walker to edit `<summary>` content.** Directest UI, but it redefines the shared walker contract and reindexes pages using summaries. Highest regression and migration cost.

**Recommendation:** Option 1 unless John has specifically identified a skill name he needs to rewrite. If he has, choose Option 2 rather than changing the universal walker contract.

**Damien’s decision:** ________________________________________________

## D2 — When should John’s accepted wording reach the public production site?

**What UAT found.** `edit.sonsteng.damienriehl.com` is the friendly, email-authenticated authoring door. Its direct-apply daemon commits accepted wording to canonical `main` and deploys the editing/DEV site. It intentionally does **not** deploy Cloudflare Pages production. The production Worker is a separate `workers.dev` origin, matching the July 18 origin decision. The prior one-page guide blurred “editing site” and “public production”; this branch corrects that wording.

**Options.**

1. **Keep production publishing separate.** John authors and sees changes on the editing site in about two minutes; Damien or an authorized release agent publishes production after release gates. Preserves the distinct Editor, Approver, and Publisher roles.
2. **Publish production on a schedule.** Batch validated editor changes to production at a fixed cadence (for example, nightly). Less manual work, but still introduces unattended public releases.
3. **Publish every successful direct edit immediately to production.** Fastest feedback, but collapses the Publisher role into the Editor path and makes every wording edit a production deployment.

**Recommendation:** Option 1. It matches the current safety model and the documented rule that routine agent release authority does not collapse the editor’s distinct roles.

**Damien’s decision:** ________________________________________________

## Verification that is not a product decision

John’s real email-code login and one harmless production-shaped edit still require a human-owned identity session. The clean browser reached the correct Cloudflare Access form and preserved the destination; UAT did not request an email code or access credential files.
