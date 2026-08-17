# Legal Practicum

Consolidating **Prof. John O. Sonsteng's** life work on legal-education reform into a
layered whole — a canonical text, a structured practicum curriculum, and an
AI-assisted delivery platform — that trains the next generation of lawyers through a
**human-and-AI ("centaur") apprenticeship**.

**John O. Sonsteng · Damien Riehl · with Roger S. Haydock.** Hosted by Damien Riehl;
open-source and **MIT-licensed**. Extends the open-access
spirit of the [Open Resource Tool](https://www.openresourcetool.info/) (Mitchell Hamline
C-LAB × IGUL / Bahçeşehir University) and [Trialbook](https://trialbook.org/)
(Sonsteng, Haydock & Riehl).

> **Status:** the pitch site AND a working practicum platform v0 — a complete open data
> spine (20 deep simulated matters, a FOLIO-mapped skills taxonomy, a simulated law
> firm), a generated curriculum site, and a live client-interview simulator with AI
> debrief and memo critique. Direction to be confirmed with John & Roger.

## What's here

| Path | What it is |
|------|-----------|
| `site/index.html` | The single-page **pitch walkthrough** (unchanged). |
| `site/platform/` | The **generated practicum platform**: curriculum modules, skills browser, matter library, 20 exercise packets, firm dashboard, client-interview chat. Never edit by hand — regenerate. |
| `data/` | **The data spine** (single source of truth): JSON Schemas, the skills/tasks taxonomy with live-verified FOLIO IRIs, the fictional **State of Meridian** + six real-state jurisdiction files, the frozen 20-matter manifest, 20 matter corpora (facts, case files, personas, rubrics, business layers), and the **Ellingboe & Ravndal LLP** firm dataset. |
| `app/worker/` | Cloudflare Worker: `/v1/session · /v1/chat · /v1/debrief · /v1/critique` — server-side persona injection, turn caps, spend-capped hosted pool (dormant until a key is set), **provider-agnostic BYOK** (Anthropic / Google Gemini / OpenAI). Contract: `app/worker/API-CONTRACTS.md`. |
| `app/chat/` | Source of the chat + critique UI (copied into the site at build). Mock harness: `app/chat/test.html`. |
| `tools/build_site.py` | Regenerates `site/platform/` from the spine (`--check` = fatal link/leak checks). |
| `tools/validate_spine.py` | The 29-check integrity gate (referential, money-math, persona, rubric, taxonomy). Run per matter with `--matter mNN`. |
| `tools/build_worker_personas.py` | Builds the Worker's server-only persona bundle + leak-safe debrief fact map. |
| `docs/` | Design record: master outline, brainstorms, the build plan, research briefs (validator spec, design direction, viz spec, interview pedagogy, LLM facts), style guide, data-spine conventions. |

## The idea in one breath

Fifty years of Sonsteng scholarship forms a trilogy — *A Legal Education Renaissance*
(the diagnosis) → *The Legal Practicum Method* + his live Skills Practicum (the solution)
→ the Open Resource Tool (the commons). AI now makes the proven-but-unscalable 1:1
apprenticeship scalable: **AI gives unlimited reps + first-pass critique; human faculty
coach judgment.** Students run 20 matters through a simulated two-lawyer firm — interviewing
AI clients whose facts are tiered (volunteered → earned-by-rapport → concealed), drafting
against point-weighted rubrics, and keeping the books — the substantive law *and* the
business of law, together.

## Quickstart (open-source adopters)

**No platform fees; bring your own model API key** (Anthropic, Google Gemini, or OpenAI).

```bash
git clone <this repo> && cd sonsteng-magnum-opus
cd site && python3 -m http.server 8791        # → http://localhost:8791/platform/
```

The curriculum, packets, and dashboard work immediately. For the client-interview
simulator you need an API backend — either:

1. **Use a hosted deployment** (e.g., the demo at sonsteng-dev.damienriehl.com): open any
   matter → "Interview the client" → **ADD YOUR KEY** → pick your provider, paste a
   (low-limit!) key. It is stored only in your browser and sent per-request; never stored
   server-side.
2. **Self-host the Worker** (recommended for courses): `cd app/worker`, set
   `wrangler secret put SESSION_SIGNING_KEY` (random) and optionally
   `ANTHROPIC_API_KEY` (enables the keyless hosted pool with a $10/day cap), then
   `npx wrangler@4 deploy`; point the site at it via the `sonsteng-api` meta tag.

The Worker unit tests need no `npm install` (Node's built-in runner, Node ≥ 20):
```bash
cd app/worker && node --test test/*.test.js        # 56 tests
```
(`test/redteam.mjs` is an adversarial probe, not a unit test — it needs a live
Worker URL + API key; run it separately, not via the glob.)

Regenerate the site after editing any spine data:
```bash
python3 tools/validate_spine.py && python3 tools/build_site.py --check
```

## Deployments

| Env | URL | Host | Deploy |
|-----|-----|------|--------|
| **PROD** | https://sonsteng.damienriehl.com | Cloudflare Pages (project `sonsteng`) | `bash deploy/deploy-prod.sh` |
| **DEV** | https://sonsteng-dev.damienriehl.com | Hetzner box, nginx behind Coolify/Traefik | `bash deploy/deploy-dev.sh <branch>` |
| **API** | https://sonsteng-chat.damienriehl.workers.dev | Cloudflare Worker | `cd app/worker && npx wrangler@4 deploy` |

- ⚠️ The DEV stack uses an explicit Compose project name (`-p sonsteng`) and **must not**
  use `--remove-orphans` — other stacks on the box share the `deploy/` dir naming.
  (Learned the hard way.)
- `deploy-dev.sh` deploys `main` by default — pass the branch explicitly when shipping
  feature work.
- **Production policy (adopted 2026-08-04):** after a change is merged and its
  release gates pass, agents may deploy it to PROD without requesting a separate
  approval. Preserve environment-specific prerequisites, deploy ordering, live
  verification, and rollback guidance. This policy covers engineering releases;
  it does not collapse the editor's distinct Approver and Publisher roles.

## Provenance & license

Original educational content in the paths enumerated by
[CONTENT-LICENSE.md](CONTENT-LICENSE.md) is available under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); software and associated
code remain under the [MIT License](LICENSE). The content attribution is:
**Legal Practicum — John O. Sonsteng · Damien Riehl · with Roger S. Haydock**.

All 20 matters, every party, fact, and document are **original synthetic content**
(shapes mirror the Sonsteng course; nothing derives from Trialbook/NITA materials).
Third-party assets retain their own terms in [THIRD-PARTY.md](THIRD-PARTY.md), and
taxonomy/research sources, uncleared recordings, and future separately licensed
Midstate originals are excluded from the CC BY grant. Skills survey provenance:
`docs/research/skills-survey.md`.
