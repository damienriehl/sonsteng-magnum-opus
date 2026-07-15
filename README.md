# Sonsteng Magnum Opus

Consolidating **Prof. John O. Sonsteng's** life work on legal-education reform into a
layered **"Magnum Opus"** — a canonical text, a structured practicum curriculum, and an
AI-assisted delivery platform — that trains the next generation of advocates through a
**human-and-AI ("centaur") apprenticeship**.

Built with Roger S. Haydock; open-source and **MIT-licensed**. Extends the open-access
spirit of the [Open Resource Tool](https://www.openresourcetool.info/) (Mitchell Hamline
C-LAB × IGUL / Bahçeşehir University) and [Trialbook](https://trialbook.org/)
(Sonsteng, Haydock & Riehl).

> **Status:** Brainstorm / first walkthrough. This repo currently holds the pitch site
> and the design record — not yet the platform. Direction to be confirmed with John &
> Roger, then iterated.

## What's here

| Path | What it is |
|------|-----------|
| `site/index.html` | Single-page **walkthrough website** — the pitch to walk John & Roger through. Self-contained (embedded fonts, no external requests); has a built-in reaction-capture panel. |
| `docs/brainstorms/2026-07-15-magnum-opus-brainstorm.md` | The brainstorm: decisions, source-corpus inventory, the centaur model, and open questions. |

## Deployments

| Env | URL | Host | Deploy |
|-----|-----|------|--------|
| **PROD** | https://sonsteng.damienriehl.com | Cloudflare Pages (project `sonsteng`) | `bash deploy/deploy-prod.sh` |
| **DEV** | https://sonsteng-dev.damienriehl.com | Hetzner box, nginx behind Coolify/Traefik | `bash deploy/deploy-dev.sh` |

- **PROD** — Cloudflare Pages direct-upload of `site/` (no build). Re-run the script after edits; same URL. Custom domain + proxied CNAME already wired.
- **DEV** — standalone `deploy/docker-compose.yml` (nginx:alpine serving `site/`) on the box's external `coolify` Docker network, routed by Traefik with a Let's Encrypt cert. DNS is a grey-cloud A record → the box.
  - ⚠️ The DEV stack uses an explicit Compose project name (`-p sonsteng`) and **must not** use `--remove-orphans` — other stacks on the box put their compose file in a dir also named `deploy`, so a shared default project name + `--remove-orphans` will remove *their* containers. (Learned the hard way.)

**Local preview:** `cd site && python3 -m http.server 8791` → http://localhost:8791
**Hosted walkthrough (private Artifact):** see the project chat.

## The idea in one breath

Fifty years of Sonsteng scholarship forms a trilogy — *A Legal Education Renaissance*
(the diagnosis) → *The Legal Practicum Method* + his live Skills Practicum (the solution)
→ the Open Resource Tool (the commons). AI now makes the proven-but-unscalable 1:1
apprenticeship scalable: **AI gives unlimited reps + first-pass critique; human faculty
coach judgment.** The opus consolidates the corpus and systematizes what Trialbook began.

## Open questions

See the brainstorm doc — naming, institutional home, John's & Roger's roles, the flagship
first build, OCR restoration of the crown-jewel articles, and how Haydock's Trialbook
corpus folds in.
