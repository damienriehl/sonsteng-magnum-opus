---
title: "Content Style Guide — The Fleet Briefing"
type: doc
status: active
date: 2026-07-17
audience: the 20 matter-authoring agents + firm-dataset agent + QA wave
binding: true
---

# Content Style Guide — The Fleet Briefing

This is the binding briefing for every agent that authors case-file content in the Legal Practicum corpus. It exists so that ~20 parallel agents produce **one coherent volume**, not twenty dialects. Read it fully before writing a single fact. Where this guide and a JSON Schema disagree on structure, the schema wins; where they disagree on *voice, naming, depth, citation policy, or dates*, this guide wins.

The single interface you author against is `data/matters/manifest.json` (the frozen 20-matter registry) plus the canons: `data/jurisdictions/meridian.json` and `data/jurisdictions/real/<st>.json`. Your matter's `id`, `slug`, shape, sides, `client_id`, jurisdiction, fee_type, caption, and party surnames are **frozen** in the manifest — do not change them.

---

## 1. Voice & tone

- **Plain, realistic, contemporary.** Write like real practice documents from **2024–2026**: clear, unshowy, professional. A witness statement should read like a real witness statement; a demand letter like a real demand letter; an engagement letter like a real engagement letter.
- **No melodrama, no TV-lawyer theatrics.** People are ordinary. Conflicts are mundane and human — an icy intersection, a missed deadline, a spoiled tank of milk, a contested easement. The drama lives in the facts, not in adjectives.
- **Register by document type.** Pleadings and orders are formal and stiff. Correspondence is businesslike. Witness statements and intake notes are in the person's own plainer voice (a mechanic and a probate lawyer do not talk alike). Personas (for the chat engine) speak as *laypeople* in their own words — see §7.
- **Show the seams of real practice.** Incomplete recollection, hedging, competing accounts, documents that don't perfectly line up. Realistic ambiguity is a feature: it is what students must interview and reason through.
- **One volume.** Match the corpus's overall editorial calm (the site aesthetic is "editorial law-review"; see `docs/research/design-direction.md`). Avoid jokes, era-anachronisms, brand names of real companies, and pop-culture references.

---

## 2. Naming rules (original, diverse, collision-resistant)

- **Everything is fictional. Always.** No real attorneys, judges, firms, companies, products, or living/notable people. No real case names. No real street addresses tied to real people.
- **Party surnames are frozen in the manifest.** Use exactly the spellings in your matter's entry and in `name_collision_sweep.surname_ledger`. **Never** introduce a surname already used by *another* matter (check the ledger). Every new minor character (a witness, a claims adjuster, a bystander) needs a surname that does not appear anywhere in the ledger and does not match a Meridian judge, county, or city.
- **Diversity, done naturally.** The corpus should reflect a real Upper-Midwest / national population across ethnicity, gender, and background — reflected in names, roles, and voices — without caricature or stereotype. Distribute gender and background across roles (judges, experts, and adverse parties, not only clients).
- **Collision-resistance = uncommon combinations.** Prefer distinctive but plausible surname + given-name pairings. Avoid famous surnames of jurists, politicians, and public figures (e.g., no Marshall, Holmes, Brennan, Scalia, Ginsburg, Vance, Ramaswamy, or the names of sitting officials), and avoid famous case names (no Palsgraf, Miranda, Roe, Erie, Hadley, Ledbetter, etc.).
- **Meridian consistency.** For all fictional-tier (m01–m10) matters, draw courts, judges (by `id` from the judge pool), counties, cities, the reporter, and citation formats from `meridian.json`. Do not invent a new Meridian county, city, judge, or reporter — reuse the canon. You may invent a specific street/business address consistent with the established geography, using only your own matter prefix for any ID.

### 2.1 EXTRA CARE — discipline (m02, m12) and DWI (m05, m15)

These shapes carry defamation-shaped risk: the fictional respondent-lawyer (discipline) and the fictional DWI defendant must **not** match any real licensed attorney's or real person's distinctive name.

- Use the **uncommon** given+surname combinations already frozen in the manifest (Winterhalt, Karsgaard, Halvard, Wenzloff) and do not "normalize" them toward common names.
- Any additional named person in these matters (complaining client, arresting officer, bar counsel, lab analyst) must likewise be uncommon and ledger-clean.
- Never imply the fictional lawyer/defendant is based on, or resembles, a real person or a real reported disciplinary/criminal matter. Keep facts generic enough that no real matter is evoked.
- The validator flags these matters for a manual name review (validator-spec A6). Assume a human will read your names.

---

## 3. The DEPTH FLOOR (countable — validator ERROR below floor)

"All deep" is machine-checkable per matter. Your matter dir fails the ship gate if any of these is below floor:

- **≥ 3 witness statements** (distinct witnesses with substantive, first-person statements/reports/depositions).
- **≥ 5 case-file documents / exhibits** (e.g., correspondence, contracts, police/incident reports, medical or business records, photos/diagrams described, ledgers). Count real, referenced exhibits — not placeholders.
- **≥ 2 personas** for the chat engine, of which:
  - **≥ 1 is the client** (the side in `interview_focus`), and
  - **≥ 1 persona carries ≥ 2 rapport-gated facts** (facts that unlock only under the closed rapport-trigger conditions in `persona.schema.json` — never through flattery or pressure; see the anti-sycophancy rule in `docs/research/interview-pedagogy.md`).
- **`facts.md` is 1,200–2,500 words** of ground-truth master narrative, with a **fact anchor on every material fact** in the form `[mNN.fact.NNN]` (zero-padded, sequential, your matter prefix only). Personas' disclosure items and rubric evidence reference these anchors.
- **All eight anatomy sections present and non-trivial.** The fixed section keys are `intro`, `objectives`, `activities`, `instructions`, `case_file`, `history`, `considerations`, `substantive_info`. **Non-trivial = ≥ 150 words each** (or the equivalent structured substance — e.g., `case_file` may be an index of real documents rather than prose, but it must be genuinely populated). No stub sections.
- **Business layer complete** per `business.schema` and this matter's frozen `fee_type`: intake form, conflicts check, engagement letter/fee agreement, time entries, billing statement, and trust-ledger entries as the fee type requires. Money must reconcile (see §5).

Personas are honor-system confidential (the repo publishes them); your job is fidelity, not obfuscation. Every disclosure item in all five tiers (`volunteered`, `revealed_if_asked`, `rapport_gated`, `concealed`, `unknown`) must carry a `fact_ref` resolving to a `facts.md` anchor. `knowledge_boundary` and `color_topics` are required and must be disjoint from material facts.

---

## 4. Length targets (per document)

Aim for realistic lengths; the depth floor is a minimum, not a target.

- **`facts.md`:** 1,200–2,500 words (hard range).
- **Witness statement:** 250–700 words each; the key eyewitness/expert may run longer.
- **Correspondence (letters, emails):** 120–400 words.
- **Pleadings / motions / agreements:** as long as the form realistically requires; use real structure (caption, numbered paragraphs, signature block) but keep exhibits and boilerplate proportionate.
- **8-part exercise sections:** ≥ 150 words each (see §3); `substantive_info` and `considerations` typically run longer.
- **Instructor-notes stub:** short (teaching notes; a few paragraphs) — a separate file, never merged into student packets.
- **Persona background:** enough to speak in character for a 20-turn interview without improvising material facts; lean on `color_topics` for texture.
- **Page-budget awareness:** the site renders packets at ≤150KB target / 250KB ceiling per page; oversize case files get split into linked sub-pages by the generator. Write substantively but not bloated.

---

## 5. Facts-only citation policy (student-facing) + fee/money discipline

- **Student-facing packets are FACTS-ONLY + jurisdiction designation.** Do **not** put statute or case citations, or statements of controlling law, into any student-facing document (facts, case file, witness statements, exhibits, the 8-part packet, personas). Finding and reading the law is the student's legal-research exercise — that is the whole point of the real tier.
  - You may state the **jurisdiction** (from the manifest) and neutral procedural posture ("a petition for a temporary injunction," "a delinquency petition," "an arbitration under the parties' agreement"). You may name a court/office from the canon (e.g., "Meridian District Court, Halden County"; "the Office of Lawyers Professional Responsibility"). These are structural facts, not legal citations.
  - Personas must not cite law or opine on legal doctrine beyond a layperson's understanding (validator flags statute/case citations in persona text).
- **Instructor-notes stub MAY cite sparingly.** In `exercise/instructor-notes.md` only, you may cite a few key statutes/rules **for the instructor**, marked instructor-verified-at-publish. Keep it light — full answer keys and intentional errors are deferred. For real-tier matters, cite only from the official research pointers in the jurisdiction file; for Meridian matters you may cite the Meridian canon (`meridian.json` supplies SOL numbers, the DWI statute, rule formats, etc.). **Never** let instructor notes bleed into student output (the print CSS and generator enforce this; you enforce it by keeping them in a separate file).
- **Money math must reconcile** (validator ERROR per matter):
  - Honor your **frozen `fee_type`**: `hourly` → fees = Σ(hours × rate) tie to the billing statement; `contingency` → fee = % × recovery, arithmetic checks; `flat` → a fixed fee line (time entries may be tracked but do not drive the invoice); `retainer` → a matching trust deposit exists and the trust ledger draws against it.
  - Rates must match the firm rate card (or a declared matter-specific engagement rate). `client_id` and fee_type must agree with the firm book. Trust ledgers never go negative at any point; ledgers are pedagogically clean (any teaching errors live only in instructor notes).
  - Read the firm canon (`data/firm/`) for rates and the client entry before writing time entries — the firm dataset is authored first for exactly this reason.

---

## 6. Meridian-canon adherence (fictional tier m01–m10)

- Pull **all** jurisdictional detail from `meridian.json`: state/county/city names, the District Court → Court of Appeals → Supreme Court ladder, the Juvenile/Family/Probate divisions, the Meridian Office of Lawyer Conduct + LPRB for discipline, citation formats (`Mer. Stat. § NNN.NN`, `Mer. R. Civ. P. NN`, `Mer. R. Prof. Conduct N.N`, `N.W. Mer.` reporter), the judge pool, the SOL table, the DWI statute (§ 787.11, per-se 0.08), and the real-estate recording/notary conventions.
- Captions follow the canon's caption styles. Assign judges by `id` from the pool; do not invent judges. Keep geography plausible (a Sable County lake matter, a Norsholm city stop, a Kettleridge range matter).
- Meridian citations belong in **instructor notes only** (student packets stay facts-only, §5). Use the canon so that when instructors *do* cite, the numbers are internally consistent across all ten Meridian matters.

---

## 7. Personas (for the chat engine)

- Personas speak as **laypeople in their own voice**, pinned to the case file. Fact-fidelity is first-class: `knowledge_boundary` pins material facts to `facts.md`; case-relevant unknowns get "I don't remember / I'm not sure"; free improvisation only within declared `color_topics` (and never legal doctrine, §5).
- **Disclosure tiers** shape what comes out and when: `volunteered` (offered early), `revealed_if_asked` (given on a direct question), `rapport_gated` (only after the closed trigger conditions — e.g., `min_turns`, an open-ended wellbeing question, no interruption — are met), `concealed` (withheld unless preconditions), `unknown` (the persona genuinely doesn't know). Every item carries a `fact_ref`.
- The **anti-sycophancy clause is verbatim in every persona prompt**: social pressure, insistence, or flattery never unlocks rapport-gated or concealed facts — only the specified preconditions do.
- Give each interviewable persona a `disposition` (`cooperative | guarded | over_talker | distressed`) and `interviewable_by` roles drawn from your matter's `sides`. Opposing-party personas that are represented trigger the in-app Rule 4.2 teaching moment (not a silent block) — configure it where applicable.

---

## 8. Date discipline

- **All matter timelines fall within 2024-01-01 … 2026-06-30.** No event, filing, contract, injury, or document date outside that window.
- **`as_of_date` = 2026-06-30** — the simulation "today." Nothing happens after it. Billing statements, invoices, and ledgers are dated on or before it; invoice dates are ≥ the latest billed entry.
- Chronology must be internally consistent: incident → intake → engagement → work → billing, in order; time entries within `[open_date, close_date | as_of_date]`.
- Ages, tenures, and "X years ago" phrasings must compute against 2026 (e.g., a "fourteen-year marriage" started in 2011–2012; a "prior DWI several years back" sits before the window's events but is referenced, not dated as a corpus event unless within window).
- Prefer concrete but bounded dates (real month/day, plausible weekday). Winter-weather facts (the m03/m13 icy-intersection, m15 stop) should land in plausible cold-season dates for their jurisdiction.

---

## 9. Quick pre-flight checklist (run before you call your matter done)

- [ ] Used the frozen manifest values (id, slug, shape, sides, client_id, jurisdiction, fee_type, caption, party surnames) unchanged.
- [ ] All IDs carry only my matter's prefix (`mNN.*`); no other matter's prefix appears in my files.
- [ ] No new surname collides with the manifest ledger, a Meridian judge, or a Meridian place.
- [ ] Depth floor met: ≥3 witness statements, ≥5 exhibits, ≥2 personas (≥1 client; ≥1 with ≥2 rapport-gated facts), facts.md 1,200–2,500 words with `[mNN.fact.NNN]` anchors on every material fact, all 8 sections ≥150 words, business layer complete.
- [ ] Student-facing docs are facts-only (no legal citations); any citations live only in the instructor-notes stub, verified.
- [ ] Money reconciles for my fee_type; rates match the firm book; trust ledger never negative.
- [ ] All dates within 2024-01-01…2026-06-30; nothing after as_of_date 2026-06-30; chronology consistent.
- [ ] Every persona disclosure item has a resolving `fact_ref`; knowledge_boundary + color_topics present and disjoint from material facts; anti-sycophancy clause present.
- [ ] `validate_spine.py` runs green on my matter dir (my completion gate).

---

*Design/aesthetic contract: `docs/research/design-direction.md`. Persona/debrief guardrails: `docs/research/interview-pedagogy.md`. Integrity gate: `docs/research/validator-spec.md`. Canons: `data/jurisdictions/meridian.json`, `data/jurisdictions/real/<st>.json`. Registry: `data/matters/manifest.json`.*
