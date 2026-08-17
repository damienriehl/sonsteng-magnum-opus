# Decisions: John pitch-docket walkthrough (2026-08-12)

Source: John's two emails of 2026-08-12, the live walkthrough with Damien run against the
[Sonsteng Pitch Docket](https://claude.ai/code/artifact/612ad43d-4817-4246-8dac-04b73f895de6)
(17 decisions, 5 blocks), and the call transcript. Nine decisions were walked with John; eight
carried on the standing recommendation. **This record has been reconciled against the
transcript** — three items changed after the sheet was submitted, and they are marked
**[transcript]** below.

Section numbers refer to the live pitch spine in `site/index.html`:
I problem · II survey · III trilogy · IV consolidation · V how it teaches · VI human+AI ·
VII coverage · VIII where it lives · IX reactions.

---

## The framing John set

> "What we've written is very good. It's just that people don't have attention spans at all."

> "This document should be a sales pitch. It should be easy to read. It should be fun. And it
> should be simple enough for people like me to understand."

The agreed metaphor is an opening argument: hit the reader in the face with the thing that
matters, then let anyone who wants depth go find it. Detail is never deleted — it moves behind
**THE PROOF**, John's own device from the reform article.

---

## Block A — The pitch itself

**A1 · Cut deep; detail moves to the proof.** Body copy comes down about 40%. Every statistic,
table and citation moves into a "THE PROOF" expander under its section. John, on the material
being moved: *"I loved it, but most people [don't] read that stuff."*

**A2 · Section order — amended in the call. [transcript]** The sheet recorded the standing
recommendation (Problem → How it teaches → Human+AI → survey proof → …). The transcript goes
further: John wants the pitch to **open with the worked example**.

> "One of the things that's important is the Midstate and Rogers and SPEU. If we started with
> that and we started showing how it works in each of the things, that would get people
> looking at it."

Two consequences. First, the Midstate/SPEU/Pat Rogers arbitration is named in the pitch, not
kept generic — which resolves the parking-lot item from the sheet. Second, the remaining
exercises get **cover-sheet treatment**: *"the rest of them … is just a sort of a cover sheet
for each of the exercises later on. Saying here's an example."* The exact opening order still
needs one pass with John.

**A3 · Attribution — broader than §V. [transcript]** The sheet said "cover byline only." The
call widened it and gave the reason:

> "I would take my name out only because it would irritate some people, and you want to assume
> it's not just me, because that's a little bit arrogant."

Worked example agreed on the call: *"For 50 years, John Sonsteng has documented this gap"*
becomes *"For 50 years, the gap has been documented."* Scope check: **"Sonsteng" appears 25
times in `site/index.html`.** The de-naming is a sweep, not an edit. See open item 1 — the
repo name and the spine's IRI base carry the name too.

**A3b · "Center for Law and Business" is retired. [transcript]** John: *"We have to get rid of
the term Center for Law and Business. It's gone. It's totally gone."* Cheap to do — the term
appears **zero times** in the pitch and only in `docs/master-outline.md` as a
source-corpus citation. Question of scope in the sheet below.

---

## Block B — Exercise architecture

**B1 · The nine required parts are locked** — short introduction · syllabus · planning guide
and checklist · learning objectives · legal and factual history · the dates method ·
description of witnesses and participants · the facts · assessment and feedback form.

John amended this twice:

- **Absorb what already exists.** Exercise parts already built map into the nine rather than
  sitting alongside them. Standard: *everything a student needs to be successful* is inside
  the template.
- **Depth on demand.** In the pitch the nine appear as titles; a reader who wants more clicks
  in. *"They can click on if they want to go into depth."*

**B2 · Day Zero — the dates mechanism.** The driver is error elimination, not freshness alone:

> "Everybody wants to make all the exercises current, and if the teacher has to fill in all the
> dates, they screw it up."

Today the syllabus tells the student that Day Zero is the first day of class and each exercise
has blanks to fill in by hand. That becomes automatic: every date in every exercise is authored
as an **offset from Day Zero** and resolved when the student starts. Build consequence — dates
become a typed offset field in the spine, never a literal string in the facts.

**B3 · The instructor chooses, and one exercise serves many purposes. [transcript]** The sheet
recorded "school sets a default; instructor may trim." The transcript makes clear the emphasis
is on instructor autonomy, and adds an architecture point:

> "Each exercise could be used for a variety of things, many, many things. Let's say the teacher
> says 'I just want to use this first one, Midstate and Rogers, for contract drafting' … or
> just for opening statements. I thought the professor would make those decisions, because you
> want to give them academic freedom."

So an exercise is not a fixed-purpose unit. It is a **matter viewed through a skill lens** —
the same Midstate file can run as contract drafting, negotiation, witness interviewing or oral
argument. The school publishes the full set of options; the instructor picks the lens and may
modify freely.

**B4 · The business of law is strongly suggested, not required.** John's reasoning is a pitch
asset, not just a setting:

> "As AI becomes more important to the practice of law, the business of law is going to be ever
> more important — as the AI does more of the substantive law."

Personal context that sharpens it: John funded the Center for Law and Business, the school
swept the gift into the general fund, and the subject was dropped. The argument he lost then is
the one the pitch now makes.

---

## Block C — AI and the faculty

**C1 · A four-AI mixture-of-experts panel replaces single-pass grading.** The largest new build
item. Confirmed in detail on the call:

1. **Three AIs independently** grade *and* give feedback on each exercise — each producing a
   grade plus written feedback, each on the assessment and feedback form.
2. **A fourth AI** reads all three and issues the assessment on the same form, taking the best
   of the three.

The motivation is John's own experience of human inconsistency:

> "We'd have three different people grade a group of people [and they'd] be inconsistent. Some
> people would see a 4, somebody would give it a 6 … and I'd have to go over every one of
> those. With AI they're going to be consistent."

Human sign-off still sits above the panel on summative grades.

**C2 · Faculty-workload argument stays out of the pitch** — it lives in the proof layer. Damien
on the call: *"it's not a sales [point]."*

**C3 · Faculty pay is REOPENED — John has no answer yet. [transcript]** The sheet recorded
"small per-exercise stipend"; that was Damien's framing and John did not adopt it. What he
actually said:

> "We want them as employees. I paid them per hour. It got too confusing. The school wouldn't
> do it. I can't figure out how do you pay them? If you don't pay them, they don't follow
> directions." … "Either we do volunteer, or some kind of stipend. I don't know the answer."

He also located *where* humans are needed: **at the culminating exercises.** *"When we get to,
let's say, a final trial or something like that — final exercises — that's live people doing
it, live consulting."* Treat C3 as open. See the sheet.

**C4 · Alumni are phase two — and the argument is a development argument. [transcript]** The
logistics are unchanged (dean-led, remote, alumni pick parts matching their expertise). What
the transcript adds is *why a dean says yes*, which is the part that belongs in the pitch:

> "What the schools are all trying to do is engage their alumni … it's a cheap way to engage
> the alumni or any people anywhere, and then when you ask them for money later on they feel
> engaged. The reason people don't give money is they're not engaged."

John stopped giving Mitchell Hamline $15,000 a year because *"they wouldn't talk to me."* The
alumni section should be pitched at the advancement office, not only the registrar.

There is also a ready-made contribution format hiding in the video: the twenty short commentary
segments are, in John's words, *"sort of post-briefing involvement by alumni."* A two-minute
recorded commentary is a low-friction ask that scales nationally.

---

## Block D — Student work standards

**D1 · Publish ranges, not maximums.** John: *"Really everything in here is customizable.
People can ask for longer or shorter assignments; fonts and margins are negotiable."* But the
purpose of having a standard at all survives: *"that sets the standards, so students know they
have to write to a standard — write short rather than long."*

**D2 · Weekly hour report kept; 50-50 attestation replaced** by a short contribution log per
deliverable.

**D3 · No jury trials.** John: *"We aren't going to have any. It's too complicated. No one does
it."* Stated in the pitch as deliberate scope — this trains lawyering, not trial advocacy.

---

## Block E — Numbers and next steps

**E1 · 235 hours, and John has a model behind it. [transcript]** John reaffirmed 235 twice when
Damien offered 225, and then said the thing that matters:

> "I built a 235-hour model. I can demonstrate that. But the ABA assumes one hour for every hour
> in class you do two hours at home. Of course nobody does that — they just make an assumption."

That reframes the arithmetic question entirely. John is not estimating; he has a built model,
and his point is that the ABA's three-hours-per-credit figure is a **fiction schools assume**
while the practicum's hours are **actually worked and verified**. The right move is to obtain
the model, not to argue the number. (Straight ABA Standard 310(b) arithmetic — 3 hours × 15
weeks × 5 credits — gives 225; sixteen weeks gives 240. 235 implies 47 hours per credit, so the
model must be doing something the standard formula does not. Worth understanding before we
publish it, because the number appears in a document written for deans.) He also referenced
building "a two-credit model."

**E2 · Cost model** — John sends his earlier calculations; Damien builds the per-credit
comparison against standard classes, seminars, clinics and internships.

**E3 · Materials are in hand.** John has delivered the videos and the PDFs; Damien ingests them.
Copyright in the Midstate materials and in the videos rests with John — *"now that we have the
copyright kind of settled, you have the copyright for these things."*

**E4 · Contributor releases are missing. [transcript]** The video's roughly twenty two-minute
commentary segments were recorded by practising lawyers. John wrote the material, but the
speakers' releases are lost: *"Somewhere I have their releases, but I don't know where they are
anymore. I'll write to Nita and ask her."* Fallbacks discussed and acceptable to John: leave
those segments out, or have AI synthesise equivalent commentary. **Owner: John → Nita.**

**E5 · John needs an editing surface. [transcript]** *"Now I can edit this, your comments —
can I? … That's what I don't know how to do."* John must be able to revise pitch text himself
without Damien in the loop. The repo already has `docs/editor-guide-for-john.md` and a built
editor; which surface he uses for the pitch is unsettled.

**E6 · The product needs a name. [transcript]** Raised by Damien, unresolved. `legalpracticum.com`
was floated but not checked. Interacts directly with A3 — the repo is `sonsteng-magnum-opus`
and the spine's JSON-LD base is `sonsteng.damienriehl.com`.

---

## Open items carried to the next call — *dispositions in "Round two" below*

Full walkthrough sheet:
**[Practicum Open Questions](https://claude.ai/code/artifact/1878a9f1-4548-41d3-ba13-709438ae45ea)**
— 14 questions, 5 blocks, 20 minutes.

1. **How far the de-naming goes.** 25 occurrences in the pitch is the easy part. The repo is
   named `sonsteng-magnum-opus`, and `sonsteng.damienriehl.com` is the JSON-LD `@id` base
   embedded in **364 places across 163 spine files**. Renaming the public identity is a decision
   about permanent identifiers, not just copy.

2. **What "the seven-point scale" actually is.** John said the assessment and feedback form is
   *"the seven-point scale, which is the one that works."* The repo has two different
   seven-point things and neither is a grading scale: the **seven-point analytic template** for
   the four-page memo (governing law; strengths and weaknesses; issues; suggested solutions;
   theory and themes; elements to prevail; liabilities and remedies), which is already in
   `data/curriculum/m2.md`; and 40 **point-weighted rubrics** in `data/matters/*/rubric.json`
   (m01 totals 202 points). If John means a seven-point *rating scale* on the feedback form,
   that is a third artifact we do not have, and the four-AI panel needs it before it can be
   built.

3. **Faculty pay** (C3 above) — John explicitly has no answer.

4. **The 235-hour model** (E1 above) — obtain it and reconcile it with Standard 310.

5. **Contributor releases** (E4 above) — pending Nita.

6. **Does the Midstate deferral reverse?** `docs/decisions/2026-07-18-midstate-deferred.md`
   deferred all use of John's original Midstate materials specifically to avoid the copyright
   question. That premise is now largely resolved, and the transcript has us *leading the pitch*
   with Midstate. The licensing and labelling path in that record needs a decision.

7. **Customization versus consistency.** B3, B4 and D1 all moved toward flexibility, while the
   case for AI assessment rests on consistency. They reconcile if the pitch says so plainly:
   **structure fixed** — the nine parts, Day Zero, the assessment and feedback form — with
   **length, content and lens** the instructor's. Recommend adopting that as a stated principle.

8. **Panel cost and provider diversity.** Four model calls per assessment instead of one, on
   bring-your-own-key. Whether the panel runs on every exercise or only summative ones, and
   whether the three graders should be three *different* providers — which is the real
   diversity win and which `app/worker/` already supports.

---

## Round two — Damien's dispositions (2026-08-12, pre-call)

Answers to the
[Practicum Open Questions](https://claude.ai/code/artifact/1878a9f1-4548-41d3-ba13-709438ae45ea)
sheet, recorded by Damien **before** the follow-up call with John. These are build-authoritative
— work proceeds on them — but three of them have not yet been put to John, and two diverge from
what he said on the 12 August call. Those are marked **⚠ pending John**.

**A1/A2 · The product is "The Legal Practicum," and the rename happens now.** Damien has
purchased **`legalpracticum.org`** (Namecheap). Migrate off `sonsteng.damienriehl.com` —
including subdomains — rather than deferring. Scope: `jsonld_context_base` in
`data/spine-manifest.json`, the `@id` and `@context` on every spine entity (**364 occurrences
across 163 files**), the deployed hostnames, and the repository name itself.

**A3 · "Center for Law and Business" is scrubbed from public-facing text only**; the internal
source-corpus citation in `docs/master-outline.md` stands as a fact of provenance.

**B1 · A short problem statement first, then the demonstration. ⚠ pending John.** On the 12
August call John asked to *open* with Midstate/SPEU/Rogers. Damien's read is a brief punch
followed by the demonstration. The two are close but not the same, and John made his preference
explicit on the record — worth one sentence of confirmation rather than a silent override.

**B2 · Cover-sheet cards for the other exercises** — matter name, the skills it can be run as,
the length options, and a link into the full packet.

**C1 · All twenty commentary segments come out; AI writes replacements.** Damien's constraint is
precise and governs the generation prompt:

> "Pull uncopyrightable facts and ideas, not copyrightable expressions of those ideas. Provide
> AI commentary that is *aligned with* but not *equivalent to* the commentary from those humans."

So: the idea–expression dichotomy is the spec, not a caveat. The generator takes the substance
of what a segment teaches and writes fresh expression. This also removes the dependency on
Nita finding the releases — that search becomes optional upside, not a blocker.

**C2 · Midstate lands inside the repository's existing dual licence.** John gives his materials
away freely, so no new licence and no carve-out are needed — the scheme already in the tree
covers it: **MIT for software, CC BY 4.0 for content.** `CONTENT-LICENSE.md` already grants
CC BY 4.0 over `data/copy/`, `data/curriculum/`, `data/jurisdictions/`, `data/matters/` and the
authored prose in `site/index.html`, explicitly sitting beside the MIT terms rather than
replacing them. Midstate's scenarios, facts and exercises are content and join that grant;
anything executable stays MIT.

This **fully retires** `docs/decisions/2026-07-18-midstate-deferred.md` — no separate-licence
directory, no `data/midstate/` carve-out, no per-artifact provenance labelling.

*Correction:* an earlier draft of this record said "everything ships MIT, Midstate included."
That was wrong — it collapsed a dual-licence repository into one licence. The distinction
matters: CC BY 4.0 carries an attribution requirement that MIT's notice clause does not, and
the scenarios are the part other schools will reuse.

*Not affected by the de-naming sweep:* the copyright notice in `LICENSE` names Damien Riehl,
John O. Sonsteng and Roger S. Haydock. That is a legal attribution, not body copy — A3 strips
names from the prose, never from the licence.

**C3 · The seven-point form is the memo template. Settled 2026-08-13 by Damien** — no longer
pending John; he confirmed the reading is correct. The assessment and feedback
form scores against the seven analytic headings already in `data/curriculum/m2.md` — governing
law; strengths and weaknesses of both sides; issues; suggested solutions; theory and themes;
elements to prevail; liabilities and remedies. Open build question this creates: how the seven
headings relate to the 40 existing point-weighted rubrics in `data/matters/*/rubric.json`. Most
likely the memo template governs *memo* assessment while the weighted rubrics continue to govern
exercise-level criteria — but that needs deciding before the panel is built.

**C4 · All video lives in the substantive layer**; the pitch stays text and stills. A homepage
clip is possible later and is explicitly out of scope for now.

**D1 · Humans run the culminating exercises and sign the final grade**; AI carries everything
before that.

**D2 · Model both pay options in the cost comparison and let each school choose.** Damien:
"Schools will likely want to run their own numbers and work within their constraints." The cost
model therefore has a pay-model switch rather than a single recommended figure.

**D3 · The panel is configurable, with an opinionated default.** Default: three graders on
**three different providers** for summative work, single-pass AI on formative drafts. Schools
may reduce to a single model for budget or vendor-relationship reasons. Configurability is a
requirement, not a nicety.

**E1 · Publish 225; keep John's 235-hour model internal. ⚠ pending John.** John reaffirmed 235
twice on the call and said he had built a model behind it. Damien's disposition is to publish
the Standard 310 figure and reconcile the difference with John separately. Do not publish 235
until that conversation happens.

**E2 · John edits in the browser editor** — one live walkthrough from Damien, plus a one-page
card.
