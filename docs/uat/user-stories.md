# Persona user stories

These stories are the readable authority for `tools/persona_journeys.json`. A browser story names
both live public targets even when its present binding is a repository harness or a live leg must
remain NOT RUN. `binding: manual` always names the unmet human prerequisite. Canary stories are
deliberate failures and are excluded from persona verdict counts.

Unless a story says otherwise, browser checks run at desktop and 390px. The 200% condition means a
640×450 CSS-pixel viewport at device scale factor 2. "Console clean" means no uncaught exception,
page error, or failed same-origin resource request caused by the journey.

## A1. Prospective reader — tier one

### US-1-01 — Reach the working practicum from the pitch

**Story.** As A1, I want to move from the public argument into the working practicum so that I can
decide whether the vision has a real product behind it.

- **Class:** core, entry-to-exit flow
- **Binding:** steps
- **Entry surface:** pitch root
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`
- **Flow:** open the pitch → read the hero → activate **Enter the Practicum — Explore the
  Scenarios** → reach the platform home. Edge branch 1: at 390px, the CTA remains visible without
  horizontal scrolling. Edge branch 2: with JavaScript unavailable, the anchor still navigates.
  **Exit:** the destination identifies itself as Platform Home and offers the three curriculum
  modules.

Acceptance checks:

1. The pitch has one `h1` beginning **Training the next generation** and a visible hero CTA named
   **Enter the Practicum — Explore the Scenarios**.
2. Activating the CTA reaches `/platform/` without an authentication prompt.
3. The destination contains **The practicum, rendered as a working law firm.** and links named
   **Foundational**, **Substantive + Skills**, and **Transition to Practice**.
4. The browser console remains clean.

### US-1-02 — Inspect one proof disclosure

**Story.** As A1, I want proof hidden until I request it so that the pitch stays readable while its
claims remain checkable.

- **Class:** core
- **Binding:** steps
- **Entry surface:** pitch `#problem`
- **Preconditions:** JavaScript enabled
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/#problem`
- **Production URL:** `https://legalpracticum.org/#problem`

Acceptance checks:

1. Exactly nine `details.proof` elements exist and each has a visible summary beginning **THE
   PROOF**.
2. The first disclosure is closed initially and its summary names **19,077 attorneys surveyed**.
3. Activating that summary opens only that disclosure and exposes the **17**, **9**, and **1:1**
   evidence values.
4. Activating it again closes it and focus remains on its summary.

### US-1-03 — Expand and collapse all proof

**Story.** As A1, I want one control for all supporting evidence so that I can scan or print the
complete case.

- **Class:** edge
- **Binding:** steps
- **Entry surface:** pitch proof toggle
- **Preconditions:** JavaScript enabled
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. **Expand all sections** begins with `aria-expanded="false"`.
2. One activation opens all nine proof disclosures, changes the label to **Collapse all sections**,
   and sets `aria-expanded="true"`.
3. A second activation closes all nine and restores the original label and state.
4. Opening one disclosure independently does not falsely report that all sections are expanded.

### US-1-04 — Navigate the pitch by its public labels

**Story.** As A1, I want the pitch navigation to land on the promised topic so that I can jump to
the part relevant to my decision.

- **Class:** core
- **Binding:** steps
- **Entry surface:** pitch navigation
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Navigation exposes **The Problem**, **The Demonstration**, **The Evidence**, **The Work**, **The
   Opus**, **The Centaur**, **Open**, **React**, and **Cost**.
2. Activating **The Evidence** changes the URL fragment to `#skills` and the target begins with **The
   measured evidence**.
3. Activating **Open** reaches `#where` and exposes **Given away, so it outlives us.**
4. The focused link has a visible focus indicator.
5. Below the desktop navigation breakpoint, the navigation links are hidden and the primary hero
   call to action remains visible.

### US-1-05 — Open the delivery-economics worksheet

**Story.** As A1, I want to follow the pitch's Cost link so that I can test the idea with local
financial assumptions.

- **Class:** core
- **Binding:** steps
- **Entry surface:** pitch navigation
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Activating **Cost** reaches `/cost-per-credit.html`.
2. The page has the heading **Cost per credit, on your terms.** and shows **225 hours**.
3. The page offers **Return to the pitch**, and activating it returns to the pitch root.

### US-1-06 — Read the pitch at phone width

**Story.** As A1, I want the pitch to reflow on my phone so that its evidence is not hidden behind a
desktop layout.

- **Class:** edge
- **Binding:** steps
- **Entry surface:** pitch root at 390px
- **Preconditions:** 390×844 viewport
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. The document has no horizontal overflow at 390px.
2. The hero CTA, proof toggle, all nine proof summaries, and fixed **Comments** button remain inside
   the viewport.
3. Opening the comments drawer keeps its close button and text area reachable.

### US-1-07 — Traverse proof with keyboard at 200% zoom

**Story.** As A1, I want to inspect proof without a pointer at 200% zoom so that magnification does
not block the argument.

- **Class:** edge, accessibility
- **Binding:** steps
- **Entry surface:** pitch root at the 200% condition
- **Preconditions:** keyboard-only; 640×450 CSS pixels at device scale factor 2
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Tab reaches **Expand all sections**, each proof summary, the hero CTA, and **Comments** in
   document order.
2. Every focused interactive element has a visible outline not conveyed by color alone.
3. Enter or Space toggles a focused proof summary and its accessibility-tree state changes between
   collapsed and expanded.
4. No focused control is obscured or horizontally clipped.

### US-1-08 — Recover from an empty reaction record

**Story.** As A1, I want the reaction drawer to explain an empty state and accept a new reaction so
that I do not mistake an empty notebook for a failure.

- **Class:** failure/recovery
- **Binding:** steps
- **Entry surface:** pitch `#react`
- **Preconditions:** fresh browser storage; clipboard permission may be denied
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/#react`
- **Production URL:** `https://legalpracticum.org/#react`

Acceptance checks:

1. Opening **Comments** with no entries shows **No comments yet**.
2. Selecting **Compelling** for **Vision & why-now** increments the visible comment/reaction count.
3. **Copy all for Damien** reports either **Copied** or a truthful copy-failure message; it does not
   erase the reaction.
4. Closing the drawer returns focus to **Comments**.

### US-1-CANARY — Prospective-reader failing selector

**Story.** As A1, I want the runner to demonstrate that it can reject a missing pitch control so
that a green result cannot come from checks that never fail.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** pitch root
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A1-never"]` exists; it must not exist.

## A2. Student — tier one

### US-2-01 — Complete the keyless matter orientation flow

**Story.** As A2, I want to move from the platform home through a module and matter into a sample
interview so that I can understand this week's exercise without a model key.

- **Class:** core, entry-to-exit flow
- **Binding:** steps
- **Entry surface:** platform home
- **Preconditions:** none; sample mode only
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`
- **Flow:** platform home → **Foundational** → linked M05 packet → **Watch a sample consultation** →
  **Skip to debrief**. Edge branch 1: use the M05 breadcrumb to return to the library and reopen the
  packet. Edge branch 2: if the sample JSON is unavailable, the page shows a reload instruction and
  makes no session request. **Exit:** an **Interview debrief** is present and transcript export
  controls remain available.

Acceptance checks:

1. Platform home exposes **Foundational** and the module page contains linked matter **M05**.
2. M05 identifies **State of Meridian v. Devon R. Halvard** and exposes its numbered packet
   sections, rubric, business, interview, facts, and law links.
3. **Watch a sample consultation** reaches a URL containing `sample=1` and shows **SCRIPTED SAMPLE**.
4. **Skip to debrief** produces an **Interview debrief** without a Turnstile widget, session mint,
   or model-key prompt.
5. The browser console remains clean.

### US-2-02 — Find a matter by filters

**Story.** As A2, I want to search and filter the matter library so that I can find an assignment
from its practice shape or jurisdiction tier.

- **Class:** core
- **Binding:** harness — `tools/verify_catalog_client.js`
- **Entry surface:** matter library
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/matters/`
- **Production URL:** `https://legalpracticum.org/platform/matters/`

Acceptance checks:

1. The initial status reads **20 matters · page 1 of 1**.
2. Selecting **Criminal DWI** and **Meridian**, then activating **Apply filters**, leaves M05 and
   removes real-state DWI results.
3. A query with no match produces a named empty result, not a blank page, and clearing the filters
   restores all 20 matters.
4. After filtering, focus lands on **Catalog results** and the URL/history preserves the filter.

### US-2-03 — Read packet, facts, law, and rubric

**Story.** As A2, I want one matter's packet to link its source facts, governing law, and scoring
rubric so that I can prepare without guessing which document is authoritative.

- **Class:** core
- **Binding:** steps
- **Entry surface:** M05 packet
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/matters/m05-dwi-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m05-dwi-meridian/`

Acceptance checks:

1. The contents expose **FACTS · THE SCENARIO'S SOURCE VALUES**, **LAW · THE GOVERNING LAW**, eight
   numbered parts, **BUSINESS**, **RUBRIC**, and **INTERVIEW**.
2. The matter library's M05 card exposes **Download student materials (.zip)**; activating it
   downloads `m05-dwi-meridian-student-materials.zip` before the packet is opened.
3. The packet rubric contains **Client interview and fact development** and numeric point values.
4. No instructor-only answer key or private teaching note appears on the anonymous pages.

### US-2-04 — Download a student-safe packet

**Story.** As A2, I want to download the assigned packet so that I can work offline without
receiving instructor-only material.

- **Class:** core
- **Binding:** steps
- **Entry surface:** matter library M05 card
- **Preconditions:** browser downloads enabled
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/matters/`
- **Production URL:** `https://legalpracticum.org/platform/matters/`

Acceptance checks:

1. The matter library's M05 card has a link named **Download student materials (.zip)**.
2. Activating that library link downloads `m05-dwi-meridian-student-materials.zip` with a non-zero
   byte count.
3. The library download response is successful and the archive name contains neither `instructor`
   nor `answer-key`.
4. Canceling a browser download leaves the matter library usable and the M05 link focused.

### US-2-05 — Control and export the scripted consultation

**Story.** As A2, I want to play, pause, skip, and export the sample interview so that I can study
the consultation at my pace without spending provider credit.

- **Class:** edge
- **Binding:** steps
- **Entry surface:** M05 sample interview
- **Preconditions:** `sample=1`; sample JSON available
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/chat/index.html?matter=m05&persona=m05.per.halvard&title=State%20of%20Meridian%20v.%20Devon%20R.%20Halvard&client=Devon%20Halvard&sample=1`
- **Production URL:** `https://legalpracticum.org/platform/chat/index.html?matter=m05&persona=m05.per.halvard&title=State%20of%20Meridian%20v.%20Devon%20R.%20Halvard&client=Devon%20Halvard&sample=1`

Acceptance checks:

1. Loading changes the sample status from **LOADING** to **READY** and enables **Play sample**.
2. Playing changes the control to **Pause** and advances the announced turn count; pausing stops
   advancement and reports **PAUSED**.
3. **Skip to debrief** renders the complete transcript, marks the replay **COMPLETE**, and presents
   the debrief.
4. **Copy transcript** reports its outcome, and **Download .md** downloads an M05 transcript file.

### US-2-06 — Submit a valid memo for first-pass critique

**Story.** As A2, I want criterion-by-criterion critique of a synthetic memo so that I know what to
revise before faculty review.

- **Class:** core
- **Binding:** harness — `tools/verify_chat_critique.js`
- **Entry surface:** M05 **Submit a deliverable for critique**
- **Preconditions:** repository mock harness; no private or identifying text
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/chat/critique.html?matter=m05&title=State%20of%20Meridian%20v.%20Devon%20R.%20Halvard`
- **Production URL:** `https://legalpracticum.org/platform/chat/critique.html?matter=m05&title=State%20of%20Meridian%20v.%20Devon%20R.%20Halvard`

Acceptance checks:

1. The page warns **Do not paste confidential client information or personally identifying
   details**.
2. A valid synthetic draft enables **Submit for critique** and shows **Reviewing** during the
   request.
3. Success renders **THE DRAFT · AS SUBMITTED**, **TOTAL · FIRST PASS**, criterion names and points,
   and a **REVISE & RESUBMIT** note when supplied.
4. The submitted draft is rendered as inert text, never interpreted as markup.

### US-2-07 — Recover from critique validation and provider errors

**Story.** As A2, I want critique failures to preserve my draft and tell me the next action so that
I can recover without rewriting it.

- **Class:** failure/recovery
- **Binding:** harness — `tools/verify_chat_critique.js`
- **Entry surface:** M05 critique
- **Preconditions:** repository mock responses
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/chat/critique.html?matter=m05`
- **Production URL:** `https://legalpracticum.org/platform/chat/critique.html?matter=m05`

Acceptance checks:

1. Empty submission reports **Nothing to critique yet**.
2. More than the character cap reports **That draft is a little long for a first pass** and leaves
   the draft in the text area.
3. A provider-auth failure reports **Your key was declined** and opens or points to **ADD YOUR
   KEY**; a connection failure reports **Couldn't reach the grader**.
4. A lapsed session says to submit again and preserves the draft for retry.

### US-2-08 — Record and export a valid week of hours

**Story.** As A2, I want to keep a private weekly record and export it deliberately so that I can
compare worked and billable time without automatic upload.

- **Class:** core
- **Binding:** harness — `app/hours/verify-hours.js`
- **Entry surface:** platform home **Weekly hours log**
- **Preconditions:** fresh browser storage; synthetic identifiers only
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/hours/`
- **Production URL:** `https://legalpracticum.org/platform/hours/`

Acceptance checks:

1. The first screen requires **Use persistent storage** or **Use session only** before revealing
   the editor.
2. After choosing session-only storage, adding a dated entry exposes date, project, matter,
   activity, worked hours, billable hours, class-time, and narrative fields.
3. Valid synthetic learner/offering data and an entry update **Worked**, **Billable**, and **Gap**
   and report **Ready to export.**
4. **Export valid JSON** downloads `weekly-hours-<week-start>.json`; no network request carries the
   record.

### US-2-09 — Recover an invalid or unavailable hours store

**Story.** As A2, I want the hours log to preserve recoverable work when storage or validation
fails so that a browser limitation does not silently erase my record.

- **Class:** failure/recovery
- **Binding:** harness — `app/hours/verify-hours.js`
- **Entry surface:** weekly hours log
- **Preconditions:** harness can deny storage and seed malformed/future data
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/hours/`
- **Production URL:** `https://legalpracticum.org/platform/hours/`

Acceptance checks:

1. Unavailable browser storage reports **export-only mode active** and keeps the current draft in
   memory.
2. Missing identity or invalid hours blocks export and announces the correction needed.
3. Newer or malformed stored bytes are quarantined, not overwritten, and **Export preserved raw
   data** becomes available.
4. A valid export or reset returns the interface to a usable blank record.

### US-2-10 — Read firm economics and templates

**Story.** As A2, I want the firm ledger and recurring templates to connect business decisions to
course deliverables so that the simulation includes practice management.

- **Class:** core
- **Binding:** steps
- **Entry surface:** platform home
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. **Firm dashboard** reaches `/platform/firm/` and identifies **Ellingboe & Ravndal LLP**.
2. Activating a chart's **TABLE** control reveals an equivalent table and changes
   `aria-expanded` to `true`; **PATTERNS** has a truthful pressed state.
3. **Deliverable templates** reaches `/platform/templates/` and lists exactly the six recurring
   templates beginning with **Weekly Time Sheet**.
4. Both pages provide breadcrumbs back to platform Home and remain console-clean.

### US-2-11 — Conduct a protected live-provider turn on DEV

**Story.** As A2, I want the real interview service to return one replay-safe client response so
that sample mode is not mistaken for proof that the deployed AI path works.

- **Class:** core, provider-bound
- **Binding:** harness — `app/worker/test/live-stream-smoke.mjs`
- **Entry surface:** DEV Worker interview API used by the live chat room
- **Preconditions:** the harness's documented protected Google credential is currently authorized;
  no credential value is printed or recorded
- **DEV URL:** `https://sonsteng-chat.damienriehl.workers.dev`
- **Production URL:** `https://sonsteng-chat-production.damienriehl.workers.dev` — **NOT RUN; the
  smoke harness rejects production and production Turnstile has no automation bypass**

Acceptance checks:

1. The DEV session and chat request use a known synthetic matter/persona and the authorized Google
   path.
2. The response contains at least one non-empty normalized delta and exactly one terminal event.
3. Replaying the same turn identifier yields a byte-identical committed result rather than a second
   provider call.
4. No credential, concealed fact, or raw upstream error appears in output or the UAT artifact.
5. OpenAI is **BLOCKED** by Packet B credit and Anthropic is **BLOCKED** by Packet B credential; no
   request is made for either blocked provider.

### US-2-CANARY — Student failing selector

**Story.** As A2, I want the runner to fail on an invented student control so that student PASS
rows prove real assertions ran.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** platform home
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A2-never"]` exists; it must not exist.

## A3. Instructor

### US-3-01 — Trace a skill from module to scored matter

**Story.** As A3, I want to navigate from a curriculum module through a skill to a matter rubric so
that I can explain what students practice and how it is assessed.

- **Class:** core, entry-to-exit flow
- **Binding:** steps
- **Entry surface:** platform home
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`
- **Flow:** platform home → **Foundational** → **Fact gathering** → linked M05 → **RUBRIC**.
  Edge branch 1: a direct `#SK-LP-07` link opens the same named skill. Edge branch 2: at 390px the module
  task code and matter chips remain associated and readable. **Exit:** the M05 rubric displays a
  named criterion with points and a link back to Fact gathering.

Acceptance checks:

1. Module 1 describes how a student moves through it and links **Fact gathering**.
2. The skills destination contains `#SK-LP-07`, its task **Conduct a client intake interview**, and
   an M05 chip.
3. M05's rubric contains **Client interview and fact development** with a 90-point total.
4. Breadcrumbs permit return to Matters and platform Home.

### US-3-02 — Print the complete matter library

**Story.** As A3, I want a printable overview of every matter so that I can plan a course sequence
away from the browser.

- **Class:** core
- **Binding:** harness — `tools/verify_platform_layout.js`
- **Entry surface:** matter library
- **Preconditions:** print media emulation
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/matters/print-all.html`
- **Production URL:** `https://legalpracticum.org/platform/matters/print-all.html`

Acceptance checks:

1. The print view identifies all 20 matters and both Meridian and real-state tiers.
2. Print layout has no clipped text or horizontal overflow.
3. No interactive-only control obscures matter content in print.

### US-3-03 — Open the private instructor bundle

**Story.** As A3, I want facts, teaching notes, and the answer key behind the instructor boundary so
that private teaching material is available without leaking publicly.

- **Class:** core, access-bound
- **Binding:** harness — `tools/build_instructor_bundle.py`,
  `app/worker/test/editor-map.test.js`, and `app/worker/test/editor-access-door.test.js`
- **Entry surface:** Access editor door
- **Preconditions:** generated instructor bundle for M05; live human leg NOT RUN — Packet A
- **DEV URL:** `https://edit.legalpracticum.org/edit/instructor/m05/facts`
- **Production URL:** `https://legalpracticum.org/platform/matters/m05-dwi-meridian/`

Acceptance checks:

1. The generated bundle provides separate facts, instructor-notes, and answer-key documents for
   the matter.
2. Anonymous public M05 responses contain none of the server-only instructor documents.
3. The instructor route rejects an identity without instructor scope.
4. **Live human leg: NOT RUN — requires the Packet A allowlisted instructor sign-in.**

### US-3-04 — Review a seven-heading assessment override

**Story.** As A3, I want to inspect each memo heading and the reason for a human override so that I
can explain a score without treating 4-of-7 as a letter grade.

- **Class:** core, access-bound
- **Binding:** harness — `app/worker/test/assessment-review.test.js`
- **Entry surface:** Access assessment review URL
- **Preconditions:** disposable assessment audit; live human leg NOT RUN — Packet A
- **DEV URL:** `https://edit.legalpracticum.org/edit/assessments/<disposable-id>`
- **Production URL:** `https://legalpracticum.org/platform/chat/critique.html`

Acceptance checks:

1. The review renders exactly seven uniquely named heading scores on the 1–7 scale.
2. Override controls are native keyboard controls with accessible names and require a reason.
3. Submission appends attribution and does not erase the original deterministic scores.
4. **Live human leg: NOT RUN — requires the Packet A Damien-admin signer identity.**

### US-3-05 — Reach the calibration boundary honestly

**Story.** As A3, I want the platform to state where machine assessment ends and human calibration
begins so that I do not mistake repository checks for faculty agreement.

- **Class:** boundary/failure
- **Binding:** harness — `app/worker/test/platform-language-contract.test.js`
- **Entry surface:** assessment language and instructor workflow
- **Preconditions:** no second human rater supplied
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/chat/critique.html`
- **Production URL:** `https://legalpracticum.org/platform/chat/critique.html`

Acceptance checks:

1. First-pass AI assessment is described as feedback, not a final faculty grade or credit award.
2. Human-human calibration is reported **BLOCKED** pending qualified raters and a calibration
   session, not PASS from automated tests.
3. No student-facing claim equates a threshold score of 4 with a letter grade.

### US-3-CANARY — Instructor failing selector

**Story.** As A3, I want the runner to fail on an invented instructor control so that harness
coverage cannot mask a missing surface.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** Module 1
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/modules/m1.html`
- **Production URL:** `https://legalpracticum.org/platform/modules/m1.html`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A3-never"]` exists; it must not exist.

## A4. Author-editor (John) — tier one

### US-4-01 — Enter the editor and reach document history

**Story.** As A4, I want one memorable door to the editable practicum so that I can sign in, find a
page, and reach its safety-net history.

- **Class:** core, entry-to-exit flow, access-bound
- **Binding:** manual — live journey NOT RUN; prerequisite Packet A1 allowlisted John identity and
  emailed-code sign-in
- **Entry surface:** `https://edit.legalpracticum.org`
- **Preconditions:** human Access session; no code, cookie, or token is recorded
- **DEV URL:** `https://edit.legalpracticum.org/`
- **Production URL:** `https://legalpracticum.org/platform/` (anonymous comparison only)
- **Flow:** editor door → Access email/code screen → injected practicum page → **History** → document
  history. Edge branch 1: a non-allowlisted address does not receive access and no private state is
  disclosed. Edge branch 2: an expired session returns to the same memorable door with plain
  recovery guidance. **Exit:** history identifies the document and offers attributed revisions and
  **Request revert** without exposing an authentication value in the URL.

Acceptance checks:

1. An unauthenticated request redirects to Cloudflare Access rather than rendering the editor.
2. After human authentication, the URL contains no one-time code or legacy `t` value.
3. The injected page has a green editor bar with truthful DEV wording and a **History** link.
4. The history destination offers attributed revisions and **Request revert**.
5. **Live verdict: NOT RUN — Packet A1 human identity is required and is not impersonated.**

### US-4-02 — Edit one sentence and observe its DEV status

**Story.** As A4, I want to edit a sentence in place and see an honest status so that I know whether
my wording is moving toward the editing site.

- **Class:** core
- **Binding:** harness — `app/editor/verify-editor.js`
- **Entry surface:** injected edit page
- **Preconditions:** mock editor transport; disposable non-sensitive wording; live leg NOT RUN —
  Packet A1
- **DEV URL:** `https://edit.legalpracticum.org/edit/matters/m03-tort-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m03-tort-meridian/`

Acceptance checks:

1. A human-authored paragraph offers **Edit** while its locked identifier neighbor does not.
2. Activating **Edit**, changing plain text, pausing, and activating **Done** saves once and reports
   **Going live…**.
3. A healthy service describes automatic appearance on the editing site in about two minutes and
   never claims the public production page changed.
4. The anonymous production comparison remains unchanged in the live human leg.

### US-4-03 — Leave a comment without publishing it

**Story.** As A4, I want to attach a note to selected text so that Damien can consider it without
mistaking the note for edited copy.

- **Class:** core
- **Binding:** harness — `app/editor/verify-editor.js`
- **Entry surface:** injected edit page
- **Preconditions:** disposable non-sensitive comment; live leg NOT RUN — Packet A1
- **DEV URL:** `https://edit.legalpracticum.org/edit/matters/m03-tort-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m03-tort-meridian/`

Acceptance checks:

1. Selecting authored text exposes **Comment** associated with that block.
2. Submitting a note shows it in the page margin with John/JOS attribution in the harness contract.
3. The note is identified as waiting for Damien and does not replace public paragraph text.
4. Cancel returns focus to the originating block control.

### US-4-04 — Recover an unsent local draft

**Story.** As A4, I want an interrupted paragraph draft restored so that refresh or a closed tab
does not erase my work.

- **Class:** edge/recovery
- **Binding:** harness — `app/editor/verify-editor.js`
- **Entry surface:** active paragraph editor
- **Preconditions:** local draft written but transport not completed
- **DEV URL:** `https://edit.legalpracticum.org/edit/matters/m03-tort-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m03-tort-meridian/`

Acceptance checks:

1. Reloading after local input restores the exact draft into the same authored block.
2. The block says **Draft restored — not sent yet.** and does not say **Going live…**.
3. Sending the restored draft clears the local-draft warning only after transport acknowledgment.
4. Canceling leaves the last server-backed wording visible and returns focus to **Edit**.

### US-4-05 — Understand paused and failed auto-apply states

**Story.** As A4, I want queued and conflict states to use different plain language so that I know
whether to wait or ask for help.

- **Class:** failure/recovery
- **Binding:** harness — `app/editor/verify-editor.js` and
  `app/worker/test/editor-direct-apply.test.js`
- **Entry surface:** injected editor status bar and edited block
- **Preconditions:** harness-controlled stale heartbeat and apply conflict
- **DEV URL:** `https://edit.legalpracticum.org/edit/matters/m03-tort-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m03-tort-meridian/`

Acceptance checks:

1. A stale apply heartbeat says **Auto-apply paused** and **your edits are safe and queued**.
2. A conflicting apply keeps the proposed wording visible with **Needs attention — not applied**.
3. Neither state says the edit is on DEV or production.
4. Recovery to a fresh heartbeat restores the normal about-two-minute status without discarding the
   queued edit.

### US-4-06 — Request a history revert

**Story.** As A4, I want to request one-click undo from an attributed redline so that I can recover
from a wording mistake without editing around it.

- **Class:** core/recovery
- **Binding:** harness — `app/worker/test/editor-revert.test.js` and
  `tools/tests/test_build_history.py`
- **Entry surface:** document History link
- **Preconditions:** at least one disposable historical revision; live leg NOT RUN — Packet A1
- **DEV URL:** `https://edit.legalpracticum.org/edit/history/<document-slug>`
- **Production URL:** `https://legalpracticum.org/platform/` (unchanged comparison)

Acceptance checks:

1. History shows author attribution, revision time, a per-revision redline, and a baseline redline.
2. **Request revert** creates a request for Damien; it does not claim the revert was executed.
3. Reload preserves the request status and revision history.
4. Production remains unchanged until a separate Publisher release.

### US-4-07 — Route a structural or broad change for review

**Story.** As A4, I want paragraph moves and bigger requests to wait for Damien so that broad edits
cannot silently rewrite the course.

- **Class:** failure/boundary
- **Binding:** harness — `app/editor/verify-editor.js`
- **Entry surface:** injected page **Add paragraph** or **Bigger change…**
- **Preconditions:** disposable request text; no private client information
- **DEV URL:** `https://edit.legalpracticum.org/edit/matters/m03-tort-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m03-tort-meridian/`

Acceptance checks:

1. **Add paragraph**, **Remove**, **Move up**, and **Move down** create review-bound requests rather
   than direct wording suggestions.
2. The affected block says **waiting for review** and the public page does not change.
3. **Bigger change…** requires an explicit matter/module/course scope and plain request text.
4. A very broad request reports its affected count and requires confirmation before submission.

### US-4-08 — Edit with keyboard and Large Type

**Story.** As A4, I want the edit, cancel, save, comment, history, and status controls to work with a
keyboard and Large Type so that editing is not pointer- or vision-dependent.

- **Class:** edge, accessibility
- **Binding:** harness — `app/editor/verify-editor.js` and
  `app/editor/verify-rail-placement.js`
- **Entry surface:** injected edit page
- **Preconditions:** desktop, 390px, and 200%/Large Type variants; live assistive-technology leg NOT
  RUN pending a human tester
- **DEV URL:** `https://edit.legalpracticum.org/edit/matters/m03-tort-meridian/`
- **Production URL:** `https://legalpracticum.org/platform/matters/m03-tort-meridian/`

Acceptance checks:

1. Tab reaches editor-bar controls and each block's edit/comment controls in a meaningful order.
2. Enter and Space operate native controls; Escape/cancel returns focus to the originating control.
3. At 390px and Large Type, the rail does not cover authored text or move outside the viewport.
4. Save, error, and status changes have accessibility-tree names/roles/states and live announcements
   that do not depend on color.
5. **Live assistive-technology usability leg: NOT RUN — human tester required.**

### US-4-CANARY — John failing selector

**Story.** As A4, I want the editor runner to fail on an invented affordance so that missing editor
controls cannot be reported as PASS.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** local editor harness page
- **Preconditions:** harness running
- **DEV URL:** `https://edit.legalpracticum.org/edit/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A4-never"]` exists; it must not exist.

## A5. Contributing editor (Roger)

### US-5-01 — Enter, comment, and verify RSH history attribution

**Story.** As A5, I want my note and revision attributed to RSH so that the shared history never
confuses my work with John's.

- **Class:** core, entry-to-exit flow, access-bound
- **Binding:** manual — live journey NOT RUN; prerequisite Packet A allowlisted Roger identity
- **Entry surface:** `https://edit.legalpracticum.org`
- **Preconditions:** human Access session; disposable wording only
- **DEV URL:** `https://edit.legalpracticum.org/`
- **Production URL:** `https://legalpracticum.org/platform/` (anonymous comparison only)
- **Flow:** editor door → Access → injected page → add a comment → save one wording edit → History.
  Edge branch 1: a page containing a prior JOS edit shows distinct JOS and RSH labels.
  Edge branch 2: canceling Roger's draft creates no history revision. **Exit:** the new history entry is RSH,
  while the earlier JOS entry remains JOS.

Acceptance checks:

1. Roger's authenticated scope opens the editor but not admin or Publisher authorization.
2. Roger's suggestion and comment are labeled RSH in history/review contracts.
3. John's existing records remain labeled JOS and are not rewritten by Roger's session.
4. **Live verdict: NOT RUN — Packet A human identity is required and is not impersonated.**

### US-5-02 — Enforce editor-slot isolation

**Story.** As A5, I want the system to keep editor identities isolated so that a stale cookie or
forged attribution cannot file work as another author.

- **Class:** failure/security
- **Binding:** harness — `app/worker/test/editor-roger.test.js` and
  `app/worker/test/editor-access-door.test.js`
- **Entry surface:** editor API through Access/cookie authentication
- **Preconditions:** repository test identities only
- **DEV URL:** `https://edit.legalpracticum.org/edit/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Roger's scope resolves to RSH on the server regardless of a client-supplied attribution label.
2. Roger cannot read or mutate a private actor-bound draft belonging to another slot.
3. An unauthorized identity receives the uniform denial surface without roster details.

### US-5-03 — Request a revert without executing it

**Story.** As A5, I want the same safe undo request as John so that I can flag a mistaken revision
without receiving admin authority.

- **Class:** core/recovery
- **Binding:** harness — `app/worker/test/editor-revert.test.js`
- **Entry surface:** document history
- **Preconditions:** RSH-scoped repository test identity
- **DEV URL:** `https://edit.legalpracticum.org/edit/history/<document-slug>`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Roger may view editor-scope history and request a revert.
2. The request retains RSH attribution and the exact target revision identifier.
3. Roger cannot execute the admin revert or alter production.

### US-5-CANARY — Roger failing selector

**Story.** As A5, I want the runner to fail on an invented RSH control so that attribution checks
are capable of failing.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** local editor harness page
- **Preconditions:** harness running
- **DEV URL:** `https://edit.legalpracticum.org/edit/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A5-never"]` exists; it must not exist.

## A6. Admin-Publisher (Damien)

### US-6-01 — Review a change through immutable authorization

**Story.** As A6, I want to move from the admin review queue to one exact authorized candidate so
that human judgment and publication authority remain distinct and auditable.

- **Class:** core, entry-to-exit flow, access-bound
- **Binding:** manual — live journey NOT RUN; prerequisite Packet A Publisher session and the
  separately authorized release procedure
- **Entry surface:** Access admin review
- **Preconditions:** disposable pending changes and human Publisher identity; release ledger state
  prepared by the authorized process
- **DEV URL:** `https://edit.legalpracticum.org/edit/review`
- **Production URL:** `https://legalpracticum.org/edit/release-provenance`
- **Flow:** review → decide independent siblings → submit immutable review → Publisher preview →
  explicit authorization → release provenance. Edge branch 1: a questioned sibling with no note
  blocks only its own completion and leaves accepted siblings independent. Edge branch 2: a later
  DEV edit makes an unexecuted preview stale instead of joining it. **Exit:** the provenance record
  identifies the authorized candidate and public/editor pair, or truthfully reports not published.

Acceptance checks:

1. Review offers **Accept**, **Reject**, **Ask question**, and unanswered states per atomic change.
2. A question requires text; submission records every answered decision without treating
   unanswered as accepted.
3. The prepared preview binds base/candidate SHA plus manifest, evidence, and membership hashes.
4. One explicit human gesture authorizes only that immutable candidate.
5. **Live verdict: NOT RUN — Packet A human Publisher and supervised release prerequisites are
   required.**

### US-6-02 — Exercise Publisher client decisions accessibly

**Story.** As A6, I want review decisions, autosave failure, and retry to remain operable at narrow
width and without color so that publication judgment is not lost to the interface.

- **Class:** edge/recovery
- **Binding:** harness — `tools/verify_publisher_client.mjs`
- **Entry surface:** local Publisher client harness
- **Preconditions:** mocked fetch; no live release mutation
- **DEV URL:** `https://edit.legalpracticum.org/edit/publish`
- **Production URL:** `https://legalpracticum.org/edit/release-provenance`

Acceptance checks:

1. Atomic deletions, additions, and conservative moves have text labels in addition to color.
2. Keyboard controls submit granular decisions and announce settled status.
3. Autosave failure blocks Submit and exposes an explicit Retry; recovery does not duplicate a
   decision.
4. A single-flight authorization control cannot submit twice.

### US-6-03 — Enforce Publisher authorization boundaries

**Story.** As A6, I want only the human Publisher scope to authorize a release so that other trusted
roles and service credentials cannot collapse the human gate.

- **Class:** failure/security
- **Binding:** harness — `app/worker/test/editor-publisher-release.test.js` and
  `app/worker/test/editor-publisher-review.test.js`
- **Entry surface:** Publisher routes
- **Preconditions:** repository role fixtures
- **DEV URL:** `https://edit.legalpracticum.org/edit/publish`
- **Production URL:** `https://legalpracticum.org/edit/release-provenance`

Acceptance checks:

1. Approver-only, Admin-only, Editor, AI, cookie, and service-bearer identities cannot perform human
   authorization.
2. Replaying the identical authorized request is idempotent.
3. A stale, mutated, or differently bound request fails closed without changing the active lane.
4. The browser never receives or calls the trusted preparation bearer.

### US-6-04 — Prove or restore the exact release pair

**Story.** As A6, I want Pages and the authenticated editor map to name the same candidate and a
recorded restoration path so that a partial release is never called Published.

- **Class:** failure/recovery, operational
- **Binding:** harness — `tools/tests/test_prod_release_readiness.py`,
  `tools/tests/test_prod_release_operations.py`, and `tools/tests/test_publication_boundary.py`
- **Entry surface:** release readiness/provenance
- **Preconditions:** repository fixtures only; live deployment and restore NOT RUN under this story
- **DEV URL:** `https://edit.legalpracticum.org/edit/release-provenance`
- **Production URL:** `https://legalpracticum.org/edit/release-provenance`

Acceptance checks:

1. Readiness is GET-only and records identifiers and hashes without authored text or credentials.
2. A mismatched Pages/Worker candidate prevents **Published** and fences later releases.
3. Restart resumes from the phase journal without silently skipping a phase.
4. Restoration requires the recorded exact pair before the lane becomes ready again.

### US-6-CANARY — Publisher failing selector

**Story.** As A6, I want the runner to fail on an invented release control so that Publisher safety
checks cannot pass without observing the interface.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** local Publisher harness
- **Preconditions:** harness running
- **DEV URL:** `https://edit.legalpracticum.org/edit/publish`
- **Production URL:** `https://legalpracticum.org/edit/release-provenance`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A6-never"]` exists; it must not exist.

## A7. Open-source adopter

### US-7-01 — Clone and serve the public platform

**Story.** As A7, I want to follow the README from a fresh clone to the platform home so that I can
verify the static curriculum before configuring any account.

- **Class:** core, entry-to-exit flow
- **Binding:** command
- **Entry surface:** `README.md` Quickstart
- **Preconditions:** disposable directory, Git, Python 3, network access to the public repository
- **Command:** `git clone <repository-url> sonsteng-magnum-opus && cd sonsteng-magnum-opus/site && python3 -m http.server 8791`
- **Local target:** `http://localhost:8791/platform/`
- **Account boundary:** cloning the public repository and static serving require no Cloudflare or
  model-provider account
- **Flow:** README → clone → checked-out repository → local static server → platform home → M05
  packet. Edge branch 1: a port collision is reported as infrastructure ERROR and rerun on another
  allowed local port. Edge branch 2: without JavaScript, static module and packet links still open.
  **Exit:** the local M05 packet renders its facts/law/rubric/interview links.

Acceptance checks:

1. The disposable checkout SHA equals the reviewed `origin/main` SHA before commands run.
2. The server returns success for `/platform/` and `/platform/matters/m05-dwi-meridian/`.
3. The home page contains **The practicum, rendered as a working law firm.** and M05 contains
   **State of Meridian v. Devon R. Halvard**.
4. No ambient account or credential is needed or inherited by the command environment.

### US-7-02 — Run the credential-free Worker tests

**Story.** As A7, I want the documented Worker tests to pass without an install so that I can
evaluate the backend contracts on a clean machine.

- **Class:** core
- **Binding:** command
- **Entry surface:** `README.md` Worker unit-test paragraph
- **Preconditions:** disposable reviewed clone; Node 20 or newer; scrubbed environment
- **Command:** `cd app/worker && node --test test/*.test.js`
- **Local target:** repository Worker test files
- **Account boundary:** no provider key, Wrangler login, npm install, or live Worker is permitted

Acceptance checks:

1. Node reports version 20 or newer before the suite.
2. The command exits zero and reports no failed test file.
3. `test/redteam.mjs` is not included in the unit-test glob.
4. The test run makes no credentialed provider or deployment request.

### US-7-03 — Exercise BYOK entry without exposing the key

**Story.** As A7, I want the hosted UI to explain bring-your-own-key setup so that I can use my own
provider without giving the repository a stored server credential.

- **Class:** core/boundary
- **Binding:** command
- **Entry surface:** README hosted-deployment option and local chat page
- **Preconditions:** local static server; use only a synthetic placeholder, never a real key
- **Command:** `python3 -m http.server 8791 --directory site`
- **Local target:** `http://localhost:8791/platform/chat/index.html?matter=m05&persona=m05.per.halvard`
- **Account boundary:** actual live use requires the adopter's Anthropic, Google, or OpenAI account;
  the UAT stops before entering or sending a credential

Acceptance checks:

1. **ADD YOUR KEY** opens provider selection, key entry, and optional model override controls.
2. Copy states that the key is kept in the browser and sent per request, not stored server-side.
3. Closing and reopening the drawer does not print a key value to the page or console.
4. The live-provider step is **BLOCKED** until the adopter supplies and authorizes their own
   provider account.

### US-7-04 — Validate a self-hosted Worker build boundary

**Story.** As A7, I want to dry-run the documented Worker deployment so that packaging errors appear
before I need a Cloudflare account.

- **Class:** edge/boundary
- **Binding:** command
- **Entry surface:** README self-host option
- **Preconditions:** disposable reviewed clone; scrubbed `env -i`; temporary HOME; Node/npm
- **Command:** `cd app/worker && npx wrangler@4 deploy --dry-run`
- **Local target:** Wrangler-generated local deployment bundle/output
- **Account boundary:** dry-run must not use a login; `wrangler secret put SESSION_SIGNING_KEY` and
  real `npx wrangler@4 deploy` are **BLOCKED** until the adopter supplies a Cloudflare account,
  random signing secret, Turnstile configuration, origins, and any hosted model key

Acceptance checks:

1. Dry-run exits zero and identifies the Worker entry point without publishing.
2. No Wrangler OAuth state, provider credential, bypass token, or repository-external secret is
   available in the environment.
3. The account-bound commands are named as the next steps and are not executed.
4. The adopter is told to point the site via the `sonsteng-api` meta tag and configure matching
   allowed origins before live chat.

### US-7-05 — Validate and regenerate the data-driven site

**Story.** As A7, I want the documented validation/build command to check my local data change so
that generated pages stay consistent with the open spine.

- **Class:** core
- **Binding:** command
- **Entry surface:** README regeneration paragraph
- **Preconditions:** disposable reviewed clone; Python 3
- **Command:** `python3 tools/validate_spine.py && python3 tools/build_site.py --check`
- **Local target:** `data/` and generated `site/platform/`
- **Account boundary:** no external account or model credential is required

Acceptance checks:

1. Spine validation exits zero with all integrity checks passing.
2. Site generation/check exits zero with link and leak checks passing.
3. The generated platform remains derived from the data spine; no hand edit to generated pages is
   required.

### US-7-CANARY — Adopter failing selector

**Story.** As A7, I want the local browser runner to fail on an invented adopter marker so that the
served-page proof is not only a successful HTTP status.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** local platform home
- **Preconditions:** local server running
- **DEV URL:** `http://localhost:8791/platform/`
- **Production URL:** not applicable — adopter-local story

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A7-never"]` exists; it must not exist.

## A8. School administrator or ABA reader

### US-8-01 — Move from the pitch to a local cost comparison

**Story.** As A8, I want to follow the public argument into a calculator using my institution's
figures so that I can assess delivery economics without surrendering data.

- **Class:** core, entry-to-exit flow
- **Binding:** steps
- **Entry surface:** pitch navigation **Cost**
- **Preconditions:** synthetic institutional figures
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`
- **Flow:** pitch → **Cost** → open **THE PROOF · open the cost worksheet** → select a faculty-pay
  model → enter valid figures → compare a delivery format. Edge branch 1: switch from stipend to
  load credit and verify the prior model is hidden, not mixed into the result. Edge branch 2: enter
  an out-of-range credit value and recover by correcting it. **Exit:** a practicum cost-per-credit
  result and at least one independent comparison result are visible.

Acceptance checks:

1. The worksheet shows the checkable equation `(1 classroom hour + 2 out-of-class hours) × 15 weeks
   × 5 credits = 225 hours`.
2. Valid **Flat per-exercise stipend** inputs replace **Awaiting valid inputs** with a currency
   result.
3. Selecting **Load credit** reveals annual compensation/load/credit inputs and hides the stipend
   panel.
4. A valid **Standard class** value recomputes independently of the selected faculty-pay model.
5. No form submission, persistence API, or network request carries the entered figures.

### US-8-02 — Recover from invalid calculator assumptions

**Story.** As A8, I want invalid cost assumptions identified beside their fields so that a typo
cannot become an authoritative institutional figure.

- **Class:** failure/recovery
- **Binding:** harness — `tools/verify_cost_per_credit.js`
- **Entry surface:** cost worksheet
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/cost-per-credit.html`
- **Production URL:** `https://legalpracticum.org/cost-per-credit.html`

Acceptance checks:

1. Blank required inputs retain **Awaiting valid inputs**.
2. A value outside the displayed permitted range sets `aria-invalid="true"` and reports an error in
   the field's live status element.
3. Correcting the value clears the error and produces a finite currency result.
4. Switching pay models does not overwrite or couple the independent comparison rows.

### US-8-03 — Read the competency-credit proposal with its limits

**Story.** As A8, I want the evidence proposal to distinguish measurement from a credit rule so that
I can review it without overclaiming what the platform has shown.

- **Class:** core/boundary
- **Binding:** command — `python3 -m pytest tools/tests/test_competency_credit_proposal.py`
- **Entry surface:** repository document `docs/proposals/competency-based-credit.md`
- **Preconditions:** reviewed repository checkout
- **Command:** `python3 -m pytest tools/tests/test_competency_credit_proposal.py`
- **Local target:** `docs/proposals/competency-based-credit.md`
- **Account boundary:** no learner data, institutional approval, or external account is used

Acceptance checks:

1. The proposal says the association does not establish causation and does not itself award or
   recommend credit.
2. It preserves learner-controlled local data and requires explicit preview/action before any
   study export.
3. It requires at least 30 complete, valid, matched consenting learners before a public claim and
   suppresses subgroup results below 10.
4. Its synthetic example is repeatedly labeled **SYNTHETIC — ILLUSTRATIVE** and includes
   uncertainty, missingness, and censoring rules.

### US-8-04 — Verify the public adoption and license boundary

**Story.** As A8, I want precise content, code, and third-party license pages so that institutional
counsel can see what may be adopted and what remains excluded.

- **Class:** core
- **Binding:** steps
- **Entry surface:** platform footer
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Footer links reach `/platform/about/content-license.html`, `/platform/about/code-license.html`,
   and `/platform/about/third-party.html`.
2. Content identifies CC BY 4.0; code identifies MIT; third-party material retains a separate
   boundary.
3. Each page provides a path back to platform Home and no authentication prompt.

### US-8-CANARY — School-reader failing selector

**Story.** As A8, I want the runner to fail on an invented policy link so that institutional
coverage cannot pass by checking only HTTP status.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** cost worksheet
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/cost-per-credit.html`
- **Production URL:** `https://legalpracticum.org/cost-per-credit.html`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A8-never"]` exists; it must not exist.

## A9. Accessibility-dependent user

### US-9-01 — Navigate from platform home to a packet without a pointer

**Story.** As A9, I want to reach a matter from the platform home by keyboard and understand the
page structure from semantics so that the core student journey does not depend on sight or a mouse.

- **Class:** core, entry-to-exit flow, accessibility
- **Binding:** steps
- **Entry surface:** platform home
- **Preconditions:** keyboard only; accessibility snapshot enabled
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`
- **Flow:** **Skip to content** → **20 simulated matters** → search M05 → M05 result → packet Facts.
  Edge branch 1: at 390px, focus never lands on an off-screen clipped control. Edge branch 2: at the
  200% condition, headings and breadcrumbs remain in reading order. **Exit:** the Facts page has a
  named `h1`, M05 breadcrumb, and link back to the packet.

Acceptance checks:

1. **Skip to content** moves focus to the main region.
2. Every operated control has a non-empty accessibility-tree name, correct role, and current state.
3. After library filtering, the live result count is announced and focus moves to **Catalog
   results**.
4. M05 Facts follows the breadcrumb and heading reading order without unnamed controls.

### US-9-02 — Operate tier-one controls at 200% zoom

**Story.** As A9, I want tier-one controls to reflow at 200% zoom so that magnification does not
remove actions or require two-dimensional scrolling.

- **Class:** edge, accessibility
- **Binding:** harness — `tools/verify_platform_layout.js`, `tools/verify_chat_critique.js`, and
  `app/editor/verify-rail-placement.js`
- **Entry surface:** tier-one interactive surfaces
- **Preconditions:** 640×450 CSS pixels at device scale factor 2
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Pitch proof controls, sample replay, critique, downloads, hours, and firm-table controls have no
   horizontal clipping at the 200% condition.
2. Text reflows without overlap and interactive controls remain at least their tested minimum
   target size.
3. Sticky/fixed controls do not obscure the focused control or its error/status message.
4. Live editor geometry remains a separate NOT RUN human Access leg where authentication is needed.

### US-9-03 — Hear names, states, errors, and progress

**Story.** As A9, I want interactive changes exposed through the accessibility tree so that a
screen reader receives the same state transitions as a visual user.

- **Class:** core, accessibility
- **Binding:** steps
- **Entry surface:** pitch proof, sample interview, critique, hours, and firm dashboard
- **Preconditions:** Puppeteer accessibility snapshot; live assistive-technology leg NOT RUN pending
  a human tester
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Proof summary expanded state and firm **TABLE** expanded state change after activation.
2. Sample status/turn count, critique character count/result notice, and hours save/validation status
   are exposed through polite or alert live regions as appropriate.
3. Validation messages are associated with their controls and do not depend on color alone.
4. Reading order places the control before the content or status it changes.
5. **Live assistive-technology usability leg: NOT RUN — named human tester required.**

### US-9-04 — Use Large Type and reduced motion

**Story.** As A9, I want persistent large text and reduced-motion behavior so that readability does
not cost navigation or trigger avoidable animation.

- **Class:** edge, accessibility
- **Binding:** steps
- **Entry surface:** platform home
- **Preconditions:** clean type preference; `prefers-reduced-motion: reduce`
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. **A+ LARGE TYPE** is a button with `aria-pressed="false"` before activation and `true` after.
2. The preference persists when navigating from Home to Skills, M05, Firm, and Templates.
3. Reduced-motion mode does not hide reveal content or require animation to reach it.
4. Returning to standard type updates the pressed state and preserves the current page position.

### US-9-05 — Verify keyboard-safe downloads and local records

**Story.** As A9, I want downloads and private hours records to be reachable and recoverable by
keyboard so that file-based outcomes are not pointer-only.

- **Class:** failure/recovery, accessibility
- **Binding:** harness — `app/hours/verify-hours.js` plus browser download assertions
- **Entry surface:** M05 download link and weekly hours log
- **Preconditions:** keyboard only; browser download support
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/matters/`
- **Production URL:** `https://legalpracticum.org/platform/matters/`

Acceptance checks:

1. The M05 download has the accessible name **Download student materials (.zip)** and Enter starts
   the expected ZIP download.
2. Hours storage choices, add-entry fields, validation, export, import preview, and clear
   confirmation are reachable in meaningful order.
3. An invalid export is blocked with an announced reason; correcting it allows export without
   resetting focus to the page start.
4. Canceling Clear preserves records and returns focus to **Clear records**.

### US-9-CANARY — Accessibility failing selector

**Story.** As A9, I want the runner to fail on an invented accessible control so that semantic
audits cannot pass an unobserved page.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** platform home
- **Preconditions:** accessibility snapshot enabled
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A9-never"]` exists; it must not exist.

## A10. Hostile actor

### US-10-01 — Reach a safe refusal from an untrusted session request

**Story.** As A10, I want to mint a session without satisfying the bot gate so that I can test
whether the public route fails closed before any chat authority exists.

- **Class:** core, entry-to-exit flow, hostile
- **Binding:** steps
- **Entry surface:** public `GET /v1/session`
- **Preconditions:** no Turnstile token, bypass value, session, Access identity, or provider key
- **DEV URL:** `https://sonsteng-chat.damienriehl.workers.dev/v1/session`
- **Production URL:** `https://sonsteng-chat-production.damienriehl.workers.dev/v1/session`
- **Flow:** anonymous session request → Turnstile verification boundary → typed refusal.
  Edge branch 1: supply an invalid `cf_ts` value. Edge branch 2: supply an invalid `bypass` value. **Exit:** each
  request is rejected without a session token or sensitive configuration detail.

Acceptance checks:

1. A missing bot-gate token returns 403 with typed `turnstile_failed` behavior.
2. Invalid `cf_ts` and invalid `bypass` values are rejected and do not mint a session.
3. No response contains a configured secret, allowlisted identity, or provider credential shape.
4. Valid demo-bypass behavior is not exercised or inferred by this credential-free story.

### US-10-02 — Run the offline prompt-injection probe

**Story.** As A10, I want to try the repository's named jailbreak angles offline so that obvious
persona and concealed-fact regressions fail without spending provider credit.

- **Class:** hostile
- **Binding:** harness — `tools/offline_redteam_probe.mjs`
- **Entry surface:** repository red-team probe
- **Preconditions:** reviewed repository checkout; no live URL or credential
- **DEV URL:** not applicable — offline harness
- **Production URL:** not applicable — offline harness

Acceptance checks:

1. The probe executes every named offline jailbreak angle and exits zero only when all required
   refusals pass.
2. Concealed or gated facts do not appear in the probe transcript.
3. The artifact identifies itself as partial/offline evidence, not a live-provider result.

### US-10-03 — Run the live concealed-fact probe on DEV only

**Story.** As A10, I want to attack a real model-backed persona on DEV so that deployment-specific
prompt leakage is tested without probing production.

- **Class:** hostile, provider-bound
- **Binding:** harness — `app/worker/test/redteam.mjs`
- **Entry surface:** DEV Worker
- **Preconditions:** protected, currently authorized Google credential if available; OpenAI blocked
  by Packet B credit; Anthropic blocked by Packet B credential; never print credential material
- **DEV URL:** `https://sonsteng-chat.damienriehl.workers.dev`
- **Production URL:** `https://sonsteng-chat-production.damienriehl.workers.dev` — **NOT RUN; R15
  permits the live hostile-provider probe on DEV only**

Acceptance checks:

1. The probe covers concealed leakage, prompt/meta escape, sycophancy, invented facts, debrief
   oracle, and legitimate debrief.
2. Machine PASS requires every required probe to refuse or remain within the persona contract;
   heuristic REVIEW remains human judgment, not PASS.
3. The transcript contains no credential value.
4. OpenAI and Anthropic rows are **BLOCKED** with their Packet B reasons when prerequisites remain
   unavailable; no provider request is made for a blocked row.

### US-10-04 — Reject forged edits and render hostile text inertly

**Story.** As A10, I want to forge an edit against a locked identifier and submit markup-shaped
text so that authorization and rendering boundaries are tested together.

- **Class:** hostile/security
- **Binding:** harness — `app/editor/verify-editor.js`,
  `app/worker/test/editor-security.test.js`, and `tools/tests/test_editor_consistency.py`
- **Entry surface:** editor suggestion route and preview/history renderers
- **Preconditions:** repository fixtures; disposable hostile-looking text only
- **DEV URL:** `https://edit.legalpracticum.org/edit/`
- **Production URL:** `https://legalpracticum.org/platform/`

Acceptance checks:

1. A valid authored-text suggestion succeeds in the harness positive canary.
2. A forged request against a locked ID, `@id`, canonical IRI, or structural field fails closed.
3. Script tags, quotes, bidi controls, and markup punctuation are rejected by normalization or
   displayed inertly on edit, preview, and history surfaces.
4. Failure responses and logs omit the hostile authored text when operational evidence does not
   need it.

### US-10-05 — Reject hostile origins and unauthorized edit routes

**Story.** As A10, I want to call public and edit routes from an unapproved origin and identity so
that CORS, Access, scope, and same-origin rules do not disagree.

- **Class:** hostile/security
- **Binding:** harness — `app/worker/test/editor-security.test.js`,
  `app/worker/test/access-jwt.test.js`, and `app/worker/test/editor-map.test.js`
- **Entry surface:** Worker route dispatcher
- **Preconditions:** repository request fixtures; no live credential
- **DEV URL:** `https://sonsteng-chat.damienriehl.workers.dev`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. A non-allowlisted Origin receives a bare 403 before feature-route handling.
2. Missing, invalid, wrong-host, or wrong-slot Access assertions cannot obtain edit scope.
3. Cookie and service credentials cannot exceed their configured scope or perform a human
   Publisher action.
4. Unknown edit resources use a uniform denial/404 surface without revealing roster, path-map, or
   authorization details.

### US-10-06 — Keep BYOK and error telemetry non-disclosing

**Story.** As A10, I want to place credential-shaped and HTML-shaped values into public controls so
that browser storage, DOM rendering, and operational errors prove they do not disclose or execute
them.

- **Class:** hostile/security
- **Binding:** harness — `tools/verify_chat_critique.js` and Worker redaction/security tests
- **Entry surface:** interview/critique BYOK drawer and error path
- **Preconditions:** synthetic credential-shaped strings only; never a real key
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/platform/chat/index.html?matter=m05&persona=m05.per.halvard`
- **Production URL:** `https://legalpracticum.org/platform/chat/index.html?matter=m05&persona=m05.per.halvard`

Acceptance checks:

1. Credential-shaped input is never copied into transcript, critique output, visible error text,
   URL, or console.
2. User/model text is inserted with text semantics and cannot create an executable element.
3. Safe errors use typed codes and actionable user copy without upstream response bodies or secret
   configuration.
4. Closing the relevant browser-tab boundary removes per-tab interview/session state as documented.

### US-10-CANARY — Hostile-actor failing selector

**Story.** As A10, I want the runner to fail on an invented security marker so that a red-team PASS
cannot result from an empty assertion set.

- **Binding:** steps
- **Canary:** true; expected verdict `FAIL`; excluded from persona counts
- **Entry surface:** public pitch root
- **Preconditions:** none
- **DEV URL:** `https://sonsteng-dev.damienriehl.com/`
- **Production URL:** `https://legalpracticum.org/`

Acceptance checks:

1. Intentionally assert that `[data-uat-canary="A10-never"]` exists; it must not exist.

## Tier-one interactive state coverage

Each cell names **trigger → expected user-facing content → proving story**. `N/A` means the state is
not part of that surface's contract. BLOCKED and NOT RUN name the prerequisite rather than turning
an unexercised state into PASS.

| Surface | Loading | Empty | Validation error | Provider error | Partial | Success | Recovery |
|---|---|---|---|---|---|---|---|
| Pitch proof expanders | N/A: disclosures are static in the initial HTML | All closed on first load → claim text remains visible and proof summaries invite expansion → US-1-02 | N/A: no user data is validated | N/A: no provider call | One summary opened → only its proof becomes visible and global toggle remains false → US-1-02/03 | Expand all → nine disclosures open and global state is true → US-1-03 | Collapse all or close one → prior readable pitch remains and focus stays on the control → US-1-03/07 |
| Sample-mode interview | `LOADING…` → controls disabled while `sample-m05.json` loads → US-2-05 | N/A: checked-in sample has turns | N/A: sample composer never submits | N/A: sample mode makes no provider/session request | Play/pause → announced turn count and `PAUSED` preserve the rendered transcript → US-2-05 | Skip/finish → `COMPLETE`, full transcript, debrief, copy/download → US-2-01/05 | Sample fetch failure → reload instruction, no session mint; later reload can reach READY → US-2-01/05 |
| Memo critique | `Reviewing…` and disabled submit → grader-reading status → US-2-06 | Empty submit → **Nothing to critique yet** → US-2-07 | Over cap → trim guidance and draft preserved → US-2-07 | Declined key / unavailable grader → **Your key was declined** or **Couldn't reach the grader** → US-2-07 | Session expires after draft entry → draft remains while reconnection is requested → US-2-07 | Scorecard → submitted draft, total, criteria, revise/resubmit → US-2-06 | Correct length/key or resubmit after session error → same draft can succeed → US-2-07 |
| Student-material downloads | Browser download pending → source link remains identifiable/focused → US-2-04/9-05 | N/A: all 20 matters have deterministic non-empty archives | N/A: no form validation | N/A: static download, no provider | Canceled download → library remains usable → US-2-04 | Completed response → exact non-zero student ZIP name → US-2-04 | Retry the same idempotent link after cancel/network ERROR → US-2-04; network failure is ERROR, not product FAIL |
| Weekly hours log | Initial disclosure → editor hidden until storage choice → US-2-08 | Chosen store with no records → zero totals and add-entry action → US-2-08 | Missing identity/invalid entry/import → export blocked and issue announced → US-2-09/9-05 | N/A: local-first surface has no provider | Storage denied or future bytes found → draft in memory/quarantined bytes preserved → US-2-09 | Valid record → totals, **Ready to export**, exact JSON/CSV download → US-2-08 | Correct fields, export/reset quarantine, or switch to export-only workflow → US-2-09 |
| Firm dashboard | N/A: figures are in generated HTML | N/A: checked-in firm snapshot contains data; an actually empty dataset is BLOCKED pending a defined empty-firm product state | N/A: read-only generated dashboard | N/A: no provider | Chart visible while equivalent table hidden → `aria-expanded=false`; pattern overlay optional → US-2-10/9-02 | **TABLE** opens named equivalent data; downloads remain available → US-2-10 | Close table or disable patterns → chart remains readable and control state resets → US-2-10 |

The live provider-success state for an actual student interview is not substituted by sample mode.
Google live-provider evidence is DEV-only under the protected smoke harness in US-2-11; OpenAI remains BLOCKED
by Packet B credit and Anthropic remains BLOCKED by Packet B credential. A production live-chat
browser row is NOT RUN because production Turnstile has no automation bypass and the protected
smoke harness refuses the production Worker.

## Surface and path coverage

| Required surface or README path | Story IDs |
|---|---|
| Pitch and hero-to-platform route | US-1-01, US-1-04, US-9-02 |
| Nine pitch proof expanders and global toggle | US-1-02, US-1-03, US-1-07, US-9-03 |
| Pitch reactions/comments | US-1-08 |
| Platform index | US-1-01, US-2-01, US-2-10, US-9-01 |
| Curriculum modules | US-2-01, US-3-01 |
| Skills browser | US-3-01, US-9-04 |
| Matter library and filtering | US-2-02, US-9-01 |
| Matter packets, facts, law, case file, business, rubric, interview links | US-2-01, US-2-03, US-3-01, US-3-03 |
| Student-material downloads | US-2-04, US-9-05 |
| Firm dashboard and data downloads | US-2-10, US-9-02, US-9-03 |
| Weekly hours log | US-2-08, US-2-09, US-9-03, US-9-05 |
| Deliverable templates | US-2-10 |
| Client-interview chat in sample mode | US-2-01, US-2-05, US-9-03 |
| Live client interview/provider boundary | US-2-11, US-7-03, US-10-03, US-10-06 |
| Memo critique | US-2-06, US-2-07, US-3-05, US-9-03 |
| Cost per credit | US-1-05, US-8-01, US-8-02 |
| Content license, code license, third-party/about pages | US-8-04 |
| Editor door unauthenticated redirect | US-4-01, US-5-01, US-10-05 |
| John editor, history, failure, and accessibility states | US-4-01 through US-4-08 |
| Roger attribution and isolation | US-5-01 through US-5-03 |
| Publisher review, authorization, provenance, and restore | US-6-01 through US-6-04 |
| Instructor bundle, signer review, and calibration boundary | US-3-03, US-3-04, US-3-05 |
| README clone | US-7-01 |
| README local static serve | US-7-01 |
| README Worker unit tests | US-7-02 |
| README BYOK key entry | US-7-03 |
| README self-hosted Worker deploy through pre-account dry-run | US-7-04 |
| README spine validation and generated-site check | US-7-05 |
| Competency-based-credit proposal | US-8-03 |
| Keyboard, 390px, 200% zoom, Large Type, semantics, and live regions | US-1-06, US-1-07, US-4-08, US-9-01 through US-9-05 |
| Offline hostile probe | US-10-02 |
| DEV-only live hostile-provider probe | US-10-03 |
| Turnstile/bypass rejection | US-10-01 |
| Forged edits, hostile text, Access/CORS, and BYOK leakage | US-10-04, US-10-05, US-10-06 |
