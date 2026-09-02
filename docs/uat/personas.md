# Persona catalog

This catalog defines the people represented by the persona-driven UAT program. It is a
testing instrument, not a claim that every person in a group works in the same way. The
story catalog in `docs/uat/user-stories.md` turns each profile into observable journeys.

Tier one receives core, edge, and failure journeys. Every other persona receives the core
journey they came for and the safety or boundary checks most likely to change its outcome.

| Actor | Persona | Tier | Primary outcome |
|---|---|---:|---|
| A1 | Prospective reader | 1 | Understand the argument in under a minute and inspect proof on demand |
| A2 | Student | 1 | Complete a matter cycle from packet through practice, critique, and records |
| A3 | Instructor | 2 | Teach and assess from the rubric, bundle, and signer-review evidence |
| A4 | Author-editor (John) | 1 | Correct wording safely and see its truthful DEV status |
| A5 | Contributing editor (Roger) | 2 | Suggest with RSH attribution and inspect shared history |
| A6 | Admin-Publisher (Damien) | 2 | Review, authorize, publish, prove, and restore an exact release |
| A7 | Open-source adopter | 2 | Clone, run, test, configure, and self-host without hidden dependencies |
| A8 | School administrator or ABA reader | 2 | Judge the workload, cost, evidence, and claim boundaries |
| A9 | Accessibility-dependent user | 2 | Reach the same outcomes with keyboard, reflow, or accessibility semantics |
| A10 | Hostile actor | 2 | Find a leak or authorization bypass without being trusted by the system |

## A1. Prospective reader

- **Tier:** 1.
- **Goals:** learn what the Legal Practicum is, decide quickly whether the argument is worth
  following, open the supporting evidence only when wanted, and reach the working platform or
  delivery-economics worksheet.
- **Context:** arrives from a shared link with no prior project vocabulary. May be a dean,
  faculty member, funder, journalist, or curious practitioner. Attention is limited and the
  pitch must distinguish vision, proof, and working product.
- **Device and assistive profile:** phone is the default; desktop is common for a later review.
  Keyboard use and 200% zoom are included because an institutional reader may use either.
- **Entry points:** the pitch root `/`; a deep link to a pitch section; the cost link shared
  separately.
- **Surfaces touched:** pitch hero and navigation, all nine `THE PROOF` disclosures, the global
  proof toggle, reaction prompts, cost-per-credit worksheet, platform home, and public license
  pages.

## A2. Student

- **Tier:** 1.
- **Goals:** find the assigned matter, understand the packet, download student-safe materials,
  practice an interview, revise a deliverable from critique, log time, and understand the firm's
  economics.
- **Context:** a second- or third-year law student working across a semester. The student knows a
  matter number more often than a file path and must be able to recover from a bad filter, invalid
  form value, declined provider key, or interrupted browser session.
- **Device and assistive profile:** laptop for drafting and interviews; phone for checking the
  week's assignment; sometimes Large Type or 200% zoom. Records may need session-only storage on a
  shared machine.
- **Entry points:** platform home `/platform/`; a module link supplied by faculty; a direct matter
  packet link.
- **Surfaces touched:** modules, skills browser, matter library, packet/facts/law pages, downloads,
  scripted and live interview, debrief, memo critique, firm dashboard, weekly hours log, and
  templates.

## A3. Instructor

- **Tier:** 2.
- **Goals:** connect exercises to skills, explain point-weighted rubrics, obtain private teaching
  materials, inspect the seven-heading memo assessment, and calibrate human judgment.
- **Context:** prepares a course on desktop, then may review a signer page at phone width. Public
  packet content and Access-protected instructor material have different disclosure boundaries.
- **Device and assistive profile:** desktop and 390px phone; keyboard and screen-reader semantics
  for signer controls; print/PDF for classroom use.
- **Entry points:** platform home; a matter packet; the Access editor door for instructor bundle
  and assessment review.
- **Surfaces touched:** modules, skills browser, packet rubric, instructor facts/notes/answer key,
  memo critique/assessment contracts, signer review, templates, and printable materials.

## A4. Author-editor (John)

- **Tier:** 1.
- **Goals:** enter through one memorable address, fix a paragraph in place, know whether the edit
  is queued or available on DEV, leave comments, inspect history, and request one-click undo.
- **Context:** signs in through Cloudflare Access with an emailed code. Small wording changes
  auto-apply to the editing/DEV site in about two minutes; structural or broad changes wait for
  Damien. Comments are notes, not published edits.
- **Device and assistive profile:** Chrome or Edge on a Windows desktop, with iPad as a secondary
  device; Large Type, 390px, keyboard-only, and announced status changes are required variants.
- **Entry points:** `https://edit.legalpracticum.org`; a bookmark to an injected edit page.
- **Surfaces touched:** Access redirect/login boundary, injected editor rail, paragraph editing,
  comments, local draft recovery, scoped/bigger-change request, status pills, history, and revert
  request.

## A5. Contributing editor (Roger)

- **Tier:** 2.
- **Goals:** use the same editor door, have every suggestion attributed as RSH, see another
  editor's accepted wording without identity confusion, and leave a durable note for Damien.
- **Context:** shares the authoring workflow but not John's attribution slot. Isolation between
  JOS and RSH is a correctness and audit requirement.
- **Device and assistive profile:** desktop browser with occasional tablet use; keyboard traversal
  of edit/comment/history controls.
- **Entry points:** `https://edit.legalpracticum.org`; a deep edit-page bookmark.
- **Surfaces touched:** Access door, injected editor, comments, status, shared history, and revert
  request.

## A6. Admin-Publisher (Damien)

- **Tier:** 2.
- **Goals:** review independent redlines, hold only questioned changes, authorize an immutable
  candidate, prove public/editor provenance parity, and restore the recorded pair after failure.
- **Context:** performs distinct Approver, Admin, and human Publisher duties. Repository harnesses
  can prove authorization boundaries; human text judgment and live release authorization remain
  Access-authenticated legs.
- **Device and assistive profile:** desktop first, plus 480px, keyboard-only, and color-disabled
  review. Operational evidence records identifiers and hashes, never authored text or secrets.
- **Entry points:** Access editor door, admin review page, or Publisher link from review.
- **Surfaces touched:** admin dashboard, review, Publisher decision UI, release preparation and
  authorization, public release provenance, phase journal, and restoration procedure.

## A7. Open-source adopter

- **Tier:** 2.
- **Goals:** clone the repository, serve the static site, run Worker unit tests, understand BYOK,
  and reach a safe self-hosted Worker deployment boundary.
- **Context:** a professor or clinic technologist on a fresh machine. They should not need platform
  fees or an install for the Node test runner, but self-hosting requires their own Cloudflare
  account, secrets, Turnstile configuration, CORS choice, and model account.
- **Device and assistive profile:** terminal plus a desktop browser; Node 20 or newer and Python 3.
  Verification uses a disposable clone and scrubbed environment so ambient credentials cannot make
  the path appear easier than it is.
- **Entry points:** repository `README.md`; repository clone URL.
- **Surfaces touched:** clone, local static server, platform home, Worker unit tests, BYOK drawer,
  Worker dry-run/deploy boundary, `sonsteng-api` meta configuration, licenses, validator, and site
  generator.

## A8. School administrator or ABA reader

- **Tier:** 2.
- **Goals:** verify the 225-hour workload arithmetic, compare local delivery costs, understand what
  evidence is proposed, and see the limits on any competency or credit claim.
- **Context:** evaluates institutional feasibility and accreditation defensibility. The calculator
  is public; the competency-based-credit proposal is currently a repository document rather than a
  public HTML surface.
- **Device and assistive profile:** phone for first read, desktop for figures and policy review,
  keyboard for the calculator, and print/PDF for circulation.
- **Entry points:** pitch Cost link; direct cost worksheet; repository proposal link supplied for
  review.
- **Surfaces touched:** pitch proof, cost-per-credit calculator, comparison table, competency-credit
  proposal, privacy boundary, sample-size rule, uncertainty example, and license/about pages.

## A9. Accessibility-dependent user

- **Tier:** 2.
- **Goals:** obtain the outcome of another persona without relying on pointer, color, animation,
  wide layout, or visual-only labels.
- **Context:** may be any public reader, student, instructor, or editor using a screen reader,
  keyboard only, 390px viewport, forced reflow at 200% zoom, Large Type, or reduced motion.
- **Device and assistive profile:** keyboard-only desktop; 390px phone; 1280px physical window at
  the 640×450 CSS-pixel/2× test condition; accessibility-tree inspection for name, role, value,
  state, reading order, errors, and live regions.
- **Entry points:** platform home, pitch, a direct interactive surface, or the Access editor door.
- **Surfaces touched:** proof disclosures, sample interview, critique, downloads, hours log, firm
  dashboard, matter/library navigation, and editor/signer controls where authentication permits.

## A10. Hostile actor

- **Tier:** 2.
- **Goals:** induce concealed-fact disclosure, override the client persona, forge locked edits,
  inject active markup, bypass Access or Turnstile, misuse BYOK state, or obtain sensitive content
  from logs and error responses.
- **Context:** has no trusted identity and may control URLs, request bodies, prompts, origins, and
  visible editor text. A blocked hostile request is the intended user outcome.
- **Device and assistive profile:** scripted HTTP client and headless browser; no privileged token,
  no production credential, and no assumed allowlisted identity.
- **Entry points:** public chat/session routes, public pages, the unauthenticated editor door, and
  direct Worker requests.
- **Surfaces touched:** offline and DEV-only live red-team probes, Turnstile/session mint, CORS,
  chat/debrief, editor mutation routes, Access redirects, hostile-text rendering, uniform 404s, and
  log/redaction boundaries.
