# Legal Practicum — TODO

The tracked work list. Source of truth for what is outstanding, who owns it, and
where it came from. Git remembers it; `tools/todo_report.py` reminds us about it
(see [Reminders](#reminders) at the bottom).

Everything here traces to the John ↔ Damien call of **2026-08-06** unless the
`origin:` tag says otherwise. Decisions live in
[`decisions/2026-08-06-john-meeting-outcomes.md`](decisions/2026-08-06-john-meeting-outcomes.md);
this file is what those decisions oblige us to *do*.

> **Reconciled 2026-08-17** against the codebase by the 21-day plan completion audit
> ([`plans/2026-08-17-1154-chore-21-day-plan-completion-audit-plan.md`](plans/2026-08-17-1154-chore-21-day-plan-completion-audit-plan.md)).
> Eight items were finished but still marked open; they are now closed with their
> evidence. Two more were finished during that audit (T12, T17).
>
> **A later record also governs.** `decisions/2026-08-12-john-pitch-docket-outcomes.md`
> postdates this file and amends T06, T11, T21 and T23. Where the two disagree, the
> August 12 record wins. Read it before acting on anything below.
>
> **Autonomous readiness reconciled 2026-08-24.** The remaining open boxes below are human,
> source-material, account, or supervised-live outcomes—not missing preparer code. The repository now
> contains protected assessment-UAT preparation, aggregate calibration analysis, committed-tree Day
> Zero verification/compensation, read-only Cloudflare and Publisher inspection, automatic dual
> representation enforcement, U18 post-apply wiring, and read-only rename inventory. Exact merge
> evidence is recorded in `docs/handoffs/2026-08-23-plan-closeout-handoff.md` after integration.

## Format — keep it, the parser depends on it

```
- [ ] **T01 — Short imperative title** `@owner` `due:2026-08-06` `origin:call-2026-08-06`
      Optional indented detail lines. Anything, as many as you like.
```

- `[ ]` open · `[x]` done · `[-]` dropped (keep the line; say why in the detail)
- `@damien` · `@john` · `@roger` · `@agent` — one owner, the person who moves it next
- `due:` only when there is a real date. No date is better than a fake one.
- IDs are permanent. Never renumber; append new ones.

---

## Today

- [x] **T01 — Collect John's CD** `@damien` `due:2026-08-06` `origin:call-2026-08-06`
      1:00 PM at **401 Southeast Main, West Building** — John meets Damien at the
      front, on Main Street. He cannot drive right now, so Damien goes to him.
      John has it all labeled: the print files, the video, and the needed case.
- [x] **T02 — Homepage: "advocates" → "lawyers"** `@agent` `origin:call-2026-08-06`
      John: naming them advocates makes readers think trial advocacy. Hero H1 in
      `site/index.html`; the claret italic `.em` styling is preserved on the new word.

## Copy and framing

- [x] **T03 — Decide the "trusted advisors" line** `@damien` `origin:call-2026-08-06`
      John's framing, and he thinks it is the important one: *"the lawyer as a
      trusted advisor… that's the only way we're going to make money, is if we're
      trusted advisors rather than the stuff that AI can do."* Candidate:
      "Training the next generation of lawyers as trusted advisors." Damien's call
      was to iterate on it in the editor rather than settle it on the call.
      **Done 2026-08-17** — Damien approved John's exact line and `d57da25`
      published it on the pitch. The language contract pins the wording.
- [x] **T04 — Sweep the remaining "advocates"** `@agent` `origin:call-2026-08-06`
      T02 changed the hero only. Still standing: `site/index.html` lede ("the finest
      advocates they can be") and the open-source paragraph ("how advocates are
      trained"), plus `README.md:5` and the July 15 brainstorm. Same reasoning
      applies; needs Damien's yes because it is more than the quick edit he asked for.
      **Done 2026-08-17** — Damien approved the sweep and `d57da25` removed the
      public-copy occurrences. `tools/tests/test_platform_language_contract.py`
      now fails on an advocate stem in the pitch, and `README.md` is clean.
- [x] **T05 — Byline on the site** `@agent` `origin:call-2026-08-06`
      Currently "The work of John O. Sonsteng · with Roger S. Haydock." Should be
      Sonsteng, then Riehl, then Haydock — John: *"it'll be my name and authorship,
      then you, and then if Roger gets involved, his name… it could say 'with Roger.'"*
      Roger has since offered material (T24), so the "with" form is live.
      **Done** — `README.md:8,102` and `site/index.html:273,658` all read
      "John O. Sonsteng · Damien Riehl · with Roger S. Haydock". Note the
      Legal Practicum plan pins a *without-"with"* variant for the new cover
      byline; that supersession is U4's, not a reopening of T05.

## Title, home, and rights

- [ ] **T06 — Adopt "Legal Practicum" throughout** `@agent` `origin:call-2026-08-06`
      Repo, site, docs, platform. Locked with a noted reservation — John: *"it
      doesn't say enough, but… let's just put legal practicum and stick with it."*
      Revisiting later is explicitly allowed; shipping under it is not blocked.
      **Partly done; the rest is owned elsewhere — do not re-derive it here.** The
      name ships in copy and UI. **Domain cutover done 2026-08-23:** the public
      property now serves at `legalpracticum.org`, the editor is Access-protected
      at `edit.legalpracticum.org`, and both personally named public/editor hosts
      are redirect-only. **Migration tooling done 2026-08-23:** PR #26 merged the
      atomic date/JSON-LD rewrite, permanent validator, and exact-head copied-corpus
      rehearsal. The remaining scope is the supervised live corpus window and the
      later repository rename. Automatic date-offset and Legal Practicum identifier
      enforcement, committed-tree verification/compensation, GET-only pair inspection,
      and a read-only rename inventory are implemented. Recompute the exact inventory
      at the freeze boundary instead of reusing a historical file count.
- [x] **T07 — We host it; drop any Mitchell hosting claim** `@agent` `origin:call-2026-08-06`
      Damien hosts. Mitchell Hamline may adopt it and is welcome to help craft it,
      but gets no hosting byline. John: *"I don't think they're interested… I don't
      want to spend time."*
      **Done** — `site/index.html:273,658` and `README.md:8` say "Hosted by
      Damien Riehl". Mitchell Hamline survives only as co-runner of the separate
      Open Resource Tool (`site/index.html:384`), which is accurate history, not
      a hosting claim.
- [x] **T08 — Add the CC-BY 4.0 content license** `@agent` `origin:call-2026-08-06`
      Beside the existing MIT code license, not replacing it. John's own materials
      go in a separately-licensed directory marked © John O. Sonsteng. Joint work
      is all three authors'. Platform users get attribution-licensed use.
      **Done** — `CONTENT-LICENSE.md` sits beside MIT and scopes `data/copy/`,
      `data/curriculum/`, `data/jurisdictions/`, `data/matters/` and the
      `site/index.html` prose, with the separately-licensed Sonsteng-originals
      carve-out. Note the 2026-08-12 record (C2) since retired the
      `data/midstate/` exclusion; U4 of the Legal Practicum plan owns that edit.
- [ ] **T09 — Record the chain of title** `@john` `origin:call-2026-08-06`
      John bought the Midstate materials **from Anita** — not from Mitchell, which
      he corrected himself mid-sentence. Worth one written line confirming it, since
      the whole CC-BY grant rests on his ownership.
- [ ] **T10 — Clear the CD's other lawyers** `@john` `origin:call-2026-08-06`
      The short narrow-topic briefings on the disc were recorded by other lawyers.
      Their copyrights until cleared for publication.

## Midstate and Rogers

- [ ] **T11 — Ingest John's originals** `@agent` `origin:call-2026-08-06`
      Print files plus the video, from the T01 disc, into the separately-licensed
      directory.
      **Blocked on Damien**, not on an agent: the disc contents must be digitised
      or handed off before anything can be ingested. `data/midstate/` does not
      exist. Blocks T13, T14 and T15 in turn.
      The old pointer to `decisions/2026-07-18-midstate-deferred.md` is retired —
      that record self-marked SUPERSEDED on 2026-08-12. Current authority is
      `decisions/2026-08-12-john-pitch-docket-outcomes.md` (C2): Midstate joins
      the existing MIT/CC-BY split and needs no separate carve-out.
- [x] **T12 — Enforce the naming rule** `@agent` `origin:call-2026-08-06`
      **"Midstate and Rogers"** — no "v.", because arbitrations take no versus.
      The court conversion is **"Rogers v. Midstate"**, and the remedy changes from
      reinstatement to **money damages**. Every filename, page title, and packet header.
      **Done 2026-08-17** in `2584b55`. The fix was written on 2026-08-06
      (`fix/midstate-naming`) and then never merged — `main` kept shipping
      "Midstate University v. Pat Rogers & SPEU" for eleven days. Now landed,
      with `tools/midstate_contract.py` as a fail-closed preflight gate so the
      legacy caption cannot return.
- [ ] **T13 — Build the arbitration to full depth** `@agent` `origin:call-2026-08-06`
      First posture. Everything else layers onto the same facts.
- [ ] **T14 — Capture the settlement layer** `@agent` `origin:call-2026-08-06`
      If Rogers wins, it resolves in a large negotiated settlement — years of pay,
      the kids' college. John: *"that's all in there, it's all about negotiations
      and mediation,"* in the case file and in the CD video. This is the negotiation
      and mediation teaching material, not a footnote.
- [ ] **T15 — Add the other four postures** `@agent` `origin:call-2026-08-06`
      Court trial, negotiation, mediation, contract interpretation — same facts.

## Teaching platform

- [x] **T16 — AI is the default speaker** `@agent` `origin:call-2026-08-06`
      John chose AI outright, for a practical reason: *"it takes so much time to get
      anybody comfortable in front of a camera."* Human speakers are optional and
      deprioritized — not a planned marquee layer.
      **Done** — stated in `data/copy/home.json:63` and enforced by
      `docs/content-style-guide.md:130`.
- [x] **T17 — Enforce the locked vocabulary** `@agent` `origin:call-2026-08-06`
      "Assessment and feedback," never "grading." "Planning Guide and Checklist" as
      one term — the guide alone went unread, the checklist alone made students think
      every item was mandatory.
      **Done 2026-08-17** in `cff323a`. The rule landed on the generated platform
      on 2026-08-06, but the contract test binds its scan root to the *generated*
      tree, so the hand-authored `site/index.html` was never scanned and carried
      ten "grad*" uses the whole time. All ten replaced; the pitch page is now in
      the contract's scope with a mutation canary beside it.
- [x] **T18 — Catalog: histories free, everything downloadable** `@agent` `origin:call-2026-08-06`
      Each case shows its procedural and factual history; the full materials are
      downloadable from the public repo. **No paywall** — John: *"I'm ready to give
      it."* "Ordering" means downloading.
      **Done** — `6eda4c1`; all 20 matters have student-material archives built by
      `tools/student_archives.py`, indexed via `site/platform/data/index.json`.
- [x] **T19 — Keep the catalog uncluttered** `@agent` `origin:call-2026-08-06`
      John's actual worry about listing everything: *"I didn't want to put all that
      into this. It's too cluttered."* Histories in the index, materials behind them.
      The shape has to survive growing toward a thousand items.
      **Done** — paginated index, composable filters and the synthetic 1,000-matter
      fixture all ship; `tools/tests/test_catalog_contract.py` holds the shape.
- [ ] **T20 — Ship the two named syllabi** `@agent` `origin:email-2026-08-05`
      General Practice Practicum and Small Business Practicum — syllabus and
      materials, as the reference implementations.

## Credit and assessment

- [x] **T21 — Weekly hours log** `@agent` `origin:email-2026-08-06`
      Students submit hours spent on projects, including class time, and hours they
      could bill a client. The gap between the two is the lesson.
      **Done 2026-08-17** — `9bb280f`, merged by `2cab8cc`, added `app/hours/`,
      `data/schemas/weekly-hours-log.schema.json`, local-only persistence, JSON/CSV
      export and reload, and the per-deliverable contribution log. The no-network
      contract is covered by `tools/tests/test_weekly_hours_log.py`.
      Scope narrowed by the 2026-08-12 record (D2): keep the weekly hour report,
      replace the 50-50 attestation with a per-deliverable contribution log.
- [x] **T22 — Competency-based credit proposal** `@agent` `origin:call-2026-08-06`
      A proposal for schools and the ABA. Evidence base: the list of what students
      demonstrably learn *quickly* rather than inefficiently, drawn from T21.
      Meanwhile schools set whatever credits they want.
      **Done locally 2026-08-20** — `53960c9`, merged by `19bc135`, added
      `docs/proposals/competency-based-credit.md`, its synthetic example, consent
      floor and anti-causal-claim checks. `dfec204` binds the study projection to
      protected assessment audits and defines time-to-first-competence, time-to-six,
      attempts-to-competence, task-level uncertainty, missingness, censoring, and
      recomputable synthetic evidence. Schools retain credit authority.
- [x] **T23 — Implement the 7-point scale, thresholds configurable** `@agent` `origin:call-2026-08-06`
      John's scale, from the University of Minnesota: 7 points total, 7s rare,
      1 and 2 effectively never given, **3 is failure, 4 is average and therefore
      competent**. So the credit floor is an average of **4 of 7** — not 3.
      Anything under **6** may be redone to reach a 6. Instructors and schools can
      set any thresholds they want, so both numbers are settings, not constants.
      Note John's caution: students read a 4 as terrible because it looks like a C.
      **Done locally 2026-08-20** — `f43f168` through `2076e68`, simplified by
      `c0baeef` and hardened by `11fcde2`. The
      2026-08-12 record found "seven-point scale" ambiguous; Damien resolved it on
      2026-08-13 as *the memo template's seven analytic headings*, not a rating
      scale. Recording review on 2026-08-17 confirmed that 1–7 is a section-level
      assessment scale, with competence at 4 and redo below 6. Damien chose the
      seven analytic memo headings as the scored dimensions; weighted criteria,
      totals, and letter maps remain for non-memo and legacy exercises only. The
      often-cited "40 rubrics" are
      **20 authored** `data/matters/*/rubric.json` files plus **20 generated
      mirrors** under `site/platform/data/matters/`, not 40 authored instruments.
      The runtime is formative-only, requires exact evidence spans and canonical
      instrument/config provenance, persists a retention-bounded protected audit,
      and gives the Access-authenticated signer an attributed override workflow.
      Summative use remains blocked on human-human calibration and provider-terms
      review; the authenticated 1280/390 browser exercise is still external UAT.
      Credential-safe audit preparation and aggregate-only calibration tooling are
      implemented. The human calibration result and its numeric kappa/bias thresholds
      remain external policy decisions; the tool intentionally invents no defaults.

## Institutional

- [ ] **T24 — Get Roger's materials in** `@damien` `origin:call-2026-08-06`
      Roger offered to contribute all of his books that are *not* collaborations
      with John. John on Roger: *"the best writer I've met… an incredible mind."*
- [ ] **T25 — Show the school only after it is built** `@damien` `origin:call-2026-08-06`
      Sequence is deliberate: finish the practicum and get Midstate in and right,
      **then** approach Greg Buck, maybe Frank Harris, maybe the dean — "here is
      what we built; if you want on board, cool; if not, we roll out with Stanford
      and others." John: *"a good way to negotiate without negotiating… up front…
      pleasant rather than confrontational."* Do not front-run this.
- [x] **T26 — Alumni are not assessors** `@agent` `origin:call-2026-08-06`
      Reversal of the Aug 5 email idea, and John was blunt: *"they wouldn't be
      giving feedback because you can't trust them. The AI is going to do it."*
      Alumni involvement stays a recommendation to schools for engagement and
      development. Nothing in the platform should route feedback to alumni.
      **Done** — `app/worker/src/validate.js` fails closed on `alumni_assessor`,
      reviewer, recipient, notification and feedback-destination fields, proved by
      a canary in `app/worker/test/platform-language-contract.test.js`.
- [ ] **T27 — John's editor pass** `@john` `origin:call-2026-08-06`
      John marks up the practicum with the editor's pencils. He said there would
      not be many comments.

---

## Reminders

`tools/todo_report.py` parses this file and pushes an ntfy nudge — same wire and
the same topic file as the editor digest (`docs/digest-push.md`), so there is one
notification convention in this repo, not two.

```bash
python3 tools/todo_report.py --dry-run     # print the report, notify nothing
python3 tools/todo_report.py               # push if something is open and changed
bash tools/install-todo-timer.sh           # 09:00 daily, America/Chicago
bash tools/install-todo-timer.sh --uninstall
```

The push is deduped: it fires when the open set *changes*, plus once a day for
anything overdue. Editing a task's text does not re-fire it; opening, closing, or
missing a due date does.
