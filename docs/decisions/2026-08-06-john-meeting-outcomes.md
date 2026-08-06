# Decisions with John Sonsteng — 2026-08-06, 10:00 AM

Source: John's emails of Aug 5 and Aug 6, 2026, worked through on a thirty-minute call.
Sheet: `docs/decisions/2026-08-06-john-meeting-decision-sheet.html`.
Cockpit ask: `briefs/qa/sonsteng-magnum-opus-2026-08-06-0940-john-meeting-decisions.json` (answered).

All ten decisions were taken. Two came back materially expanded from the recommendation —
item 4 (naming) and item 9 (credit) — and those expansions are the ones to read.

---

## 1. Title and institutional home — DECIDED

**The title is "Legal Practicum."** Not *The Legal Practicum Method* (John's book), not
*The Practicum Method*. Two words.

**The home is GitHub.** The work lives in the open repository, and Mitchell Hamline
**adopts** it rather than hosts it. This settles the home in the same breath as the title
rather than deferring it: MHSL becomes the first adopter and a credited origin, not a
gatekeeper with a veto on later decisions. It also removes the dependency on the Buck /
Harris / dean conversation in item 10 — that meeting is now about adoption and leadership,
not about permission.

## 2. Byline order — DECIDED

**Sonsteng, Riehl, Haydock**, exactly as John proposed, with Roger third if he takes an
authorship role.

## 3. Copyright and license — DECIDED

**John retains sole copyright and grants CC-BY 4.0** to the Practicum software.
Attribution to Sonsteng is required on every copy, adaptation, and school derivative,
permanently.

Layering: John's **content** under CC-BY 4.0; the **software** this project wrote stays
MIT. Two licenses, two layers, no conflict.

**This unblocks `2026-07-18-midstate-deferred.md`**, which deferred John's original
Midstate/Trialbook materials for one reason only — the license was unsettled. It is
settled. See item 4.

Two open follow-ons, neither of which blocks building:

- **Chain of title in writing.** John's statement that he already owns all needed
  materials should be confirmed in writing, covering anything produced under a school
  appointment or with school resources.
- **CD contributor clearances.** The narrow-topic briefings on John's CD are by other
  lawyers; those are their copyrights until cleared. See item 5.

## 4. Model case file — DECIDED, with a naming correction

**"Midstate and Rogers" — not "Midstate v. Rogers."** John was explicit. It is an
**arbitration**, and an arbitration has no *versus*. Every artifact, filename, page title,
and packet header uses the conjunctive form.

**The court variant flips the caption and the remedy.** If the same facts are converted to
a court posture, the matter becomes **"Rogers v. Midstate"** — Rogers is the plaintiff
there — and the remedy changes from **reinstatement** to **money damages**. That is not
cosmetic: it is precisely the pedagogy of the reuse. The same facts, entered through a
different door, produce a different caption, a different moving party, and a different
thing worth asking for.

Build order: **arbitration first to full depth**, then trial, negotiation, mediation, and
contract interpretation onto the same facts.

John's originals are **incorporated into Legal Practicum** under the item 3 license, in
a separately-licensed directory marked `© John O. Sonsteng`, per the pivot path already
written into `2026-07-18-midstate-deferred.md`.

## 5. The CD — DECIDED

**Damien collects the disc from John at 1:00 PM today, Aug 6**, digitizes it, and returns
a plain contents index so John can say what to keep. The disc holds briefings,
demonstrations, analysis, and many short briefings on narrow topics by other lawyers —
which may already be a first season of the item 6 marquee layer, subject to the item 3
clearances.

## 6. AI briefings and recorded speakers — DECIDED

**AI briefings are the default layer; a curated set of recorded human speakers is the
marquee layer.** Every briefing exists as an AI-delivered module — consistent in format,
printable, rewatchable, updatable without re-recording. A handful of extraordinary lawyers
get studio-background recordings as signature pieces on top.

## 7. Vocabulary — LOCKED

- **"Assessment and feedback."** Never "grading."
- **"Planning Guide and Checklist."** One term. Never either half alone — John ran both
  experiments: the guide alone went unread, the checklist alone made students think every
  item was mandatory.

## 8. Exercise catalog and ordering — DECIDED

**Free public catalog, ordered materials.** Every exercise gets a free public page
carrying its **procedural and factual history** — enough for a professor to know whether
it fits their course, not enough to run it. Full materials are ordered through the site.

First two packages, both named by John: the **General Practice Practicum** and the
**Small Business Practicum**, syllabus and materials.

Under CC-BY anyone may redistribute free, so ordering sells assembly, print-ready and
LMS-ready packaging, currency, and support — not exclusivity.

## 9. Credit hours and assessment — DECIDED, expanded to both tracks

The recommendation was to log hours and leave credits alone. **John took both.**

**Track one — the weekly log.** Students submit hours spent on projects (including class
time) and hours they could bill a client. Course credits stay conventional for the
registrar and the ABA. The gap between the two numbers is the lesson, and it is what
answers John's question of whether a student can make a living being a lawyer.

**Track two — a competency-based credit proposal.** Write a proposal that schools can put
to their own accreditors, arguing credit by demonstrated competency rather than seat time.
**The log is the evidence base for the proposal**: the list of what students demonstrably
learn *quickly* rather than inefficiently is the exhibit that supports the claim. This is
the reform John has been describing for fifty years, and the log makes it arguable with
data rather than assertion.

**Assessment and feedback scoring — thresholds are configurable.**

| Rule | Value |
|---|---|
| Score below **6** | May be redone |
| Minimum score for credit (here) | **3** |
| Minimum score for credit (some other schools) | **4** |
| Who sets the threshold | The instructor and the school |

The platform must therefore treat both the redo threshold and the credit threshold as
**per-school, per-instructor settings**, not constants.

## 10. Alumni faculty and the board — DECIDED

**Both in parallel.** John opens the conversation with Greg Buck (new board chairman),
Frank Harris, and perhaps the dean now, while the alumni-assessor pilot starts
immediately — so the meeting has something working to show. Alumni are recruited through
each school's alumni advisor: recognition rather than a funding ask, an expected lift in
contributions as a second-order effect, and a school able to say it has a national faculty
in its practicum courses.

Note that item 1 changes this conversation's shape. With the home on GitHub, MHSL is being
offered the chance to **lead by adopting first**, not asked for permission to proceed.

---

## What follows from this

| # | Work | Gated on |
|---|---|---|
| 1 | Adopt "Legal Practicum" as the title across repo, site, and docs | — |
| 2 | Add the CC-BY 4.0 content license beside the MIT code license; mark the separate-license directory | — |
| 3 | Digitize the CD, return a contents index to John | 1 PM pickup |
| 4 | Build "Midstate and Rogers" arbitration to full depth; naming convention enforced (no "v.") | CD contents, John's originals |
| 5 | Make assessment thresholds per-school/per-instructor settings (redo < 6, credit ≥ 3 default) | — |
| 6 | Draft the competency-based credit proposal, with the fast-learning evidence list | Log design |
| 7 | Catalog pages: procedural and factual history per exercise | — |
| 8 | General Practice and Small Business Practicum syllabi as reference implementations | — |
| 9 | Chain-of-title confirmation and CD contributor clearances | John |
| 10 | Alumni pilot; Buck / Harris / dean outreach | John |
