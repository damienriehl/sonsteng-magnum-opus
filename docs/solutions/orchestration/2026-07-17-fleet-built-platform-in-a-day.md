---
title: "Fleet-building a 20-matter legal-ed platform in one day"
category: orchestration
tags: [multi-agent, parallel-fleet, schema-first, validator-gate, uat, byok, prompt-caching, leak-safety]
module: platform
symptom: "Massive greenfield build (data corpus + worker + UI + site) needed in one day with consistent quality across ~40 parallel agents"
root_cause: "n/a — process learnings from the 2026-07-17 curriculum build-out"
---

# What worked (repeat these)

1. **Vocabulary before fan-out.** The unanimous multi-review finding held true in practice: JSON Schema guarantees shape, not vocabulary. The Phase-0 "consistency contract" (frozen matter manifest with client IDs, deterministic matter-prefixed IDs `m06.per.baines`, closed rapport-trigger enum, fixed 8-part section keys, strict rubric shape, countable depth floor) is why 20 parallel Opus authors produced a corpus that joined cleanly on the first validator run. Zero ID collisions all night.
2. **Per-agent self-gates beat end-stage QA.** Every matter agent ran `validate_spine.py --matter mNN` to 0 ERRORs as its own completion gate; the post-fleet QA wave then only handled cross-matter concerns (surname dedup, label curation). 20/20 green on first collection.
3. **The validator is the product's spine as much as the data.** 29 machine checks (money-math in Decimal, fact-anchor tracing, trigger-enum enforcement) made "all deep" and "internally consistent" *countable*. When the firm's AR drifted from the authored matters ($3,095), reconciliation was a query, not an argument.
4. **UAT catches what unit gates cannot.** After 56 unit tests, clean dry-runs, and link checks, real-browser UAT still found a HIGH defect in 30 minutes: the critique page never minted a session — its primary deep-link flow was dead. Lesson: "drive the flow like a stranger" is a distinct, non-optional gate class.
5. **Adversarial review earns its tokens on guarantees, not style.** The overnight reviewers converted two prompt-level guarantees into code-level ones: debrief-oracle protection moved from "the prompt says topic-labels-only" to server-side redaction rebuilt from ground truth; the instructor-leak sweep moved from HTML-only to whole-output-tree with a sentinel canary. Rule: any safety property stated in a prompt should eventually be enforced in code.
6. **Design-token convergence contract.** One `theme.css` with named primitives + a written aesthetic direction let ~6 different agents produce visually indistinguishable pages ("The Practicum Press"). The convergence contract section (named classes, accent discipline) mattered more than the mood-board prose.
7. **The 4096-token cache floor.** Haiku silently no-ops prompt caching below 4096 tokens — structure system prompts as shared-boilerplate-first and *assert* `cache_read_input_tokens > 0` in tests. ($0.164 → $0.055 per session.)
8. **Keyless demo insurance.** When the API key slipped a day, the scripted sample replay (reusing the live renderers, loudly labeled) kept the demo fully experiential. Build the no-dependency demo path *before* you need it.

# What bit us (avoid these)

1. **Deploy script defaulted to `main`** — first DEV deploy silently shipped the old pitch site. Scripts that take a branch should *require* it or print the ref in neon.
2. **Interface drift between parallel agents:** the taxonomy agent wrote collection files (`{"skills":[...]}`) while the validator classified per-file objects — found only when the validator's own module ran. Where two agents share a data seam, pin the *file-level* shape in the Phase-0 contract, not just the entity shape.
3. **Schema too strict for its consumers:** `firm.schema.json` (additionalProperties:false) couldn't hold four datasets the dashboard spec required; caught late because the schema author and the viz-spec author never shared an interface doc. The fix (optional fields, version bump 1.1.0) was cheap only because the schema-freeze hadn't started.
4. **Auto-derived "topic labels" leaked fact content** ("were withdrawals from her account" for a concealed fact) — truncating text is not redaction. Content-free placeholders + human-curated sidecars was the right pattern.
5. **A shared mutable file (`tools/build_site.py`) edited by 3 concurrent agents** worked out (disjoint anchors) but produced transient broken-link noise mid-write and was luck as much as design. Prefer one owner per file per wave; if unavoidable, section-scoped anchors and re-read-before-edit.
6. **Monthly spend limits kill agents mid-file.** The site generator died halfway through writing a Python file. Resume-from-transcript (SendMessage) recovered it cleanly — background agents' transcripts are the crash insurance; design prompts so partial output is inspectable.

# Numbers (for future estimation)

One day, ~45 Opus agents + 12 review/UAT agents across 8 waves: 10 schemas · 26+5 skills / 108 tasks / 232 subtasks with live-verified FOLIO crosswalk · 20 matters (407 files, 56 personas, 475 curated labels) · 29-check validator · CF Worker (3 providers, 62 tests) · race-proofed chat UI · 31-page generated site · 20 instructor answer keys · full UAT + fixes. Fleet phase alone: ~3.07M subagent tokens, 26 min wall-clock, 20/20 green.
