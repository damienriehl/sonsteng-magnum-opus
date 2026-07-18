# PROD injector enable — one-command sequence (HELD; do not run without Damien)

**Status:** BUILT, NOT FLIPPED. PROD (`https://sonsteng.damienriehl.com`) currently
serves ONLY the original pitch. This doc is the exact sequence to turn on the
Worker-injected `/edit` editor on PROD. Per decision q3 (`docs/decisions/2026-07-18-qa-answers.md`),
the flip itself always comes back to Damien — nothing here runs as part of routine work.

The config is already wired: `app/worker/wrangler.jsonc` defines an `env.production`
block (a SEPARATE worker `sonsteng-chat-production`) whose only deltas from DEV are
`EDIT_UPSTREAM` → the PROD Cloudflare Pages origin and `EDIT_ORIGIN` → the PROD
worker's own origin. DEV (`sonsteng-chat`, the top-level/default env) is untouched
and keeps working throughout.

---

## ⚠ ONE DECISION FIRST — the PROD worker's public origin

`EDIT_ORIGIN` is the **sole `/edit` CORS allowlist**; it MUST equal the browser
origin the editor is actually served from. The committed value assumes the PROD
worker lives at its default workers.dev subdomain:

    EDIT_ORIGIN = https://sonsteng-chat-production.damienriehl.workers.dev

If instead PROD should mount the worker on a **custom route / subdomain** (e.g.
`edit.sonsteng.damienriehl.com`, or a route on `sonsteng.damienriehl.com`), then
before enabling: (a) set `env.production.vars.EDIT_ORIGIN` to that origin, and
(b) add a `routes` block to `env.production` in `wrangler.jsonc`. John/Roger's
magic links must point at the same origin.

---

## Prerequisites (secrets that must exist in the PRODUCTION env)

Secrets are **per-environment** in wrangler — the DEV secrets do NOT carry over.
Set each on the production worker (run from `app/worker/`; creds via
`source ~/.config/cloudflare/creds.env`). Values by path only — never echoed:

    cd app/worker
    npx wrangler@4 secret put SESSION_SIGNING_KEY --env production   # 32 random bytes (may reuse DEV's or mint fresh)
    npx wrangler@4 secret put DEMO_BYPASS_TOKEN   --env production   # from ~/.secrets/sonsteng-demo-bypass
    npx wrangler@4 secret put EDIT_TOKEN_JOHN     --env production   # from ~/.secrets/sonsteng-editor-tokens
    npx wrangler@4 secret put EDIT_TOKEN_ROGER    --env production   # from ~/.secrets/sonsteng-editor-tokens
    npx wrangler@4 secret put EDIT_TOKEN_ADMIN    --env production   # from ~/.secrets/sonsteng-editor-tokens
    # ANTHROPIC_API_KEY is OPTIONAL and gated on Damien (BYOK-forever per q4) — leave unset to keep the hosted pool dormant.

---

## The enable sequence (in order)

    # 0) Regenerate the server-only bundles the worker inlines (map = universal allowlist).
    python3 tools/build_site.py --check
    python3 tools/build_instructor_bundle.py

    # 1) Publish the PROD static site to Cloudflare Pages (sonsteng.damienriehl.com).
    #    This is what EDIT_UPSTREAM proxies; it must carry the /platform/ pages.
    deploy/deploy-prod.sh

    # 2) Deploy the PRODUCTION worker (separate instance; DEV worker untouched).
    cd app/worker
    npx wrangler@4 deploy --env production     # build.command bundles editor-data first

    # (secrets from the Prerequisites section must already be set for --env production)

---

## Verification

    # a) Worker health/config:
    curl -s https://sonsteng-chat-production.damienriehl.workers.dev/v1/session | jq .   # -> 200, {session_token,...}

    # b) Injector serves a PROD page (needs John's/Roger's ?t= token; expect 302 -> clean URL -> 200 injected HTML):
    #    open the magic link in a browser; confirm the editor chrome loads and a test suggestion round-trips.

    # c) DEV is still the DEV origin (regression check — must be UNCHANGED):
    npx wrangler@4 deploy --env dev --dry-run --outdir /tmp/wr-dev | grep EDIT_UPSTREAM   # -> sonsteng-dev...

---

## Rollback

The PROD **pitch site is a separate Pages deploy** and is never touched by the
worker deploy, so the public pitch cannot regress from enabling the injector.
To take the PROD injector back down:

    cd app/worker
    npx wrangler@4 delete --env production          # removes the sonsteng-chat-production worker
    # (or, to keep it deployed but dark, rotate EDIT_TOKEN_* scopes so every magic link 404s)

DEV (`sonsteng-chat`, default env) requires NO rollback — it is independent of the
production worker.

---

## Why default DEV stays safe

- A bare `wrangler deploy` (no `--env`) still targets the top-level env =
  `sonsteng-chat` DEV, exactly as today. (Wrangler now prints an advisory to pass
  `--env=""` or `--env dev` explicitly; either is equivalent to the default.)
- `vars` and `durable_objects` are non-inheritable in wrangler, so each env carries
  its own full copy — a PROD change can never leak into DEV's config.
- PROD is a **separate worker name** with its own Durable Object namespace, so the
  DEV editor store and PROD editor store never share state.
