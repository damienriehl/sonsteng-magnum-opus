# PROD injector enable — one-command sequence

## Promotion risk and advisory AI boundary

The PROD promotion policy lives in `tools/prod_promotion.py`. It is a pure,
versioned calculation: named preparation gates are absolute, normalized risk
signals are clamped to `[0, 1]`, their configured weights produce a confidence
score in `[0, 100]`, and the score selects `automatic`, `awaiting_approval`, or
`low_confidence`. Missing gates, missing signals, non-finite values, and
oversized inputs fail closed. Identical inputs replay to the same evidence hash.

AI review is advisory and has no lifecycle, approval, branch, publication, or
restoration capability. The adapter sends only the allowlisted score envelope;
raw candidate content, filenames, validator output, credentials, and provider
errors are excluded. Its dedicated credential must select a provider mode that
prohibits training and retains requests for no more than 30 days. A response is
accepted only when its strict schema, evidence hash, model, and prompt version
match. Malformed, stale, unsafe, timed-out, or unavailable responses create a
content-light `hold` record with zero adjustment. Hard-failed candidates never
invoke the provider.

Upward adjustment defaults to zero and its kill switch is independent of
deterministic automatic promotion. Do not raise the cap until the measured
launch contract reports all of the following: at least 50 reviewed candidates
over at least 14 days, at least 90% admin agreement, zero hard-gate escapes,
zero false automatic promotions, successful restart and restoration drills,
at least 95% of automatic candidates completing within five minutes, and an
explicit disposition for every AI-unavailable sample. Reducing the cap back to
zero does not disable deterministic promotion.

**Status:** BUILT, NOT FLIPPED. This doc is the exact sequence to turn on the
Worker-injected `/edit` editor on PROD. The former per-deploy approval hold was
superseded on 2026-08-04: merged, release-green engineering changes may proceed
to PROD without another confirmation. The prerequisites and verification in
this runbook remain mandatory.

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
    npx wrangler@4 secret put TURNSTILE_SECRET    --env production   # WP6 bot-gate secret for the shared Turnstile widget (sitekey 0x4AAAAAAD4uPMN8eNwzYvAy). REQUIRED: env.production sets TURNSTILE_ENABLED="true", so without this secret GET /v1/session rejects every non-bypass mint with turnstile_failed (503). The DEV worker already has it; the widget's domain list already covers sonsteng.damienriehl.com, so no new widget is needed — only this secret.
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

---

# Access door runbook (`edit.sonsteng.damienriehl.com`)

*Added 2026-07-27 with the Cloudflare Access door. Plan:
`docs/plans/2026-07-27-001-feat-cloudflare-access-door-plan.md`.*

## Topology

| Thing | Value |
|---|---|
| Editor hostname | `edit.sonsteng.damienriehl.com` (Worker custom domain, DEV/default env) |
| Access team | `young-unit-68fd.cloudflareaccess.com` |
| Access application | self-hosted, one policy, one-time PIN, 30-day session |
| AUD tag | **set at U2** — paste into `EDIT_ACCESS_AUD` in `wrangler.jsonc` (top-level **and** `env.dev`) |
| Admin page | `edit.sonsteng.damienriehl.com/edit/admin` (`admin` scope) |
| Bare hostname | 302s into `/edit/`; the landing is scope-aware |
| Fallback door | `?t=` links on `sonsteng-chat.damienriehl.workers.dev`, still live |

**Both doors are open on purpose.** `EDIT_ORIGIN` is a comma-separated list carrying
*both* origins. It is the sole `/edit` allowlist and is enforced twice — `editCorsHeaders`
and `csrfOk` — so dropping the `workers.dev` entry while any `?t=` token is still issued
makes every save from those bookmarks return `403 csrf_failed` while pages keep loading
normally. Drop it only in the same deploy that removes the last token secret.

## ⚠ workers_dev must stay true (incident, 2026-07-27)

`wrangler.jsonc` declares `"workers_dev": true` at the top level. **Do not remove it.**
Wrangler defaults it to *false* the moment any `routes` key exists, and says so only in an
advisory warning during deploy. Adding the Access-door route therefore silently unbound
`sonsteng-chat.damienriehl.workers.dev` — the fallback door — which served a bare Cloudflare
`error code: 1042` until it was caught by probing the live host. `env.production` declares
`routes: []`, which is enough to trip the same default there.

After any deploy that touches routing, **read the trigger list wrangler prints**. It must name
both:

    Deployed sonsteng-chat triggers
      https://sonsteng-chat.damienriehl.workers.dev
      edit.sonsteng.damienriehl.com (custom domain)

## Enabling it (the two credentialed steps)

1. **Attach the hostname.** The `routes` block is already in `wrangler.jsonc`; a default-env
   deploy provisions DNS + certificate:

       cd app/worker
       npx wrangler@4 deploy --env production --dry-run   # MUST show no route for the Access host
       npx wrangler@4 deploy                              # default env == DEV

2. **Create the Access application** on team `young-unit-68fd`. ✅ **DONE 2026-07-27** — — self-hosted, hostname above,
   policy allowing the three addresses, one-time PIN included, session 30 days. Capture the
   **AUD tag**, put it in `EDIT_ACCESS_AUD` in both var blocks, and redeploy.

   ⚠ **Do NOT use the one-click "Access for Workers" flow.** It covers `workers.dev` and
   Preview URLs only — using it would put a login screen in front of the *fallback* door and
   leave the custom domain ungated, which is the exact inverse of the intent.

3. **Map the addresses to slots.** ✅ **DONE 2026-07-27** — `EDIT_ACCESS_EMAILS` is set on
   `sonsteng-chat` (confirmed by `wrangler secret list`). It was piped via stdin, so the addresses
   never touched a file, the repo, or shell history. To change it later:

       npx wrangler@4 secret put EDIT_ACCESS_EMAILS
       # {"<john>":"john","<roger>":"roger","<damien>":"damienadmin"}

   …or set it in the dashboard: **Workers & Pages → sonsteng-chat → Settings → Variables and
   Secrets**. Secrets are not touched by deploys, so either sticks. Lowercase the keys —
   `lookupAccessSlot` lowercases the JWT's `email` claim before lookup.

   ⚠ **Never put this in `wrangler.jsonc`.** `tools/tests/test_no_committed_pii.py` gates it, and
   also sweeps `app/worker/`, `docs/` and `tools/` for any real address. (`data/` and `site/` are
   exempt — they are the practicum's own fictional client correspondence.)

   Lowercase keys. `damienadmin` is the Access-only slot carrying edit+instructor+admin with
   **no** `EDIT_TOKEN_DAMIENADMIN` secret — that is what lets one identity hold admin scope and
   DR attribution without making admin reachable from any `?t=` token.

**Until `EDIT_ACCESS_AUD` is set the Access door is inert** — `access-jwt.js` returns null when
any of the three `EDIT_ACCESS_*` vars is empty, so the only working door is `?t=`. That is the
correct, safe intermediate state, and it is also why PROD (which carries none of these vars)
cannot take the Access branch at all.

## The application, as built (2026-07-27)

| Field | Value |
|---|---|
| App ID | `589cfc99-eb6d-40da-a225-f0f0c5828f74` |
| Name | Sonsteng Practicum Editor |
| Domain | `edit.sonsteng.damienriehl.com` |
| Type | `self_hosted` |
| Session | `730h` (matches the Cockpit and Fence Edit) |
| Identity | One-time PIN only (`9be03666-eb42-44b2-b3a0-7fa448455e4a`) |
| AUD | `ff942be4…4ecc` — in `EDIT_ACCESS_AUD`, both var blocks |
| Policy | "Practicum editors" · allow · 3 emails · no require/exclude |

**The credential that can manage it is `~/.secrets/cloudflare-zt-token`.** Neither the wrangler
OAuth token nor `~/.config/cloudflare/creds.env` can touch Access (both 403); the Cloudflare MCP
can read Access but not write it. If you need to distinguish "no permission" from "bad request"
on this API, POST an empty body: a permissions failure returns `10000 Authentication error`, a
capable token returns a validation code such as `12130`.

Verified live immediately after: the hostname 302s to
`young-unit-68fd.cloudflareaccess.com/cdn-cgi/access/login/…` carrying the same AUD;
`sonsteng-chat.damienriehl.workers.dev` still serves the editor's own uniform 404 and
`/edit/assets/editor.css` 200 (R4 intact); PROD still serves the pitch title (R7 intact).

## Revoking someone — order matters

**Remove them from `EDIT_ACCESS_EMAILS` FIRST, then from the Access policy.**

    npx wrangler@4 secret put EDIT_ACCESS_EMAILS   # re-put without that address
    npx wrangler@4 deploy

Access re-evaluates its policy at **session establishment**, and the session is 30 days — so
editing the policy alone leaves an already-signed-in browser working for up to a month. The
secret is consulted on **every request** (the Access path mints no cookie), which is what makes
it the instant lever. Do the policy edit second, to stop new sign-ins.

To revoke a `?t=` token instead, rotate its scope version in `EDIT_TOKEN_SCOPES` — that
invalidates every cookie already minted under the old version.

## ⚠ The workers.dev origin is load-bearing for the machine clients

Access gates `edit.sonsteng.damienriehl.com` **at the edge**, so a Bearer service token is not
enough there — `GET /edit/v1/review` with the admin token returns **200** on `workers.dev` and
**302** to the Access login on the Access hostname. The apply daemon and the digest are both
configured against the ungated origin:

    ~/.config/sonsteng-apply/env    EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1
    ~/.config/sonsteng-digest/env   EDIT_API_BASE=… (same)   EDIT_REVIEW_URL=…/edit/review

**Retiring the `?t=` tokens does not mean retiring that origin.** Gating or removing it stops the
every-2-minute apply loop and the 4×/day digest. If it ever must go, the daemon needs an Access
service token first — and `access-jwt.js` deliberately returns null for a service-token assertion
(`common_name`, no `email`), so that is a code change, not just config.

## Retiring the tokens (per person)

Once *that person* has completed one Access sign-in **and** saved one edit through the new door:

    npx wrangler@4 secret delete EDIT_TOKEN_JOHN     # …or ROGER
    # keep EDIT_TOKEN_ADMIN as Damien's break-glass

Then, when the last one is gone: drop the `workers.dev` entry from `EDIT_ORIGIN` in both var
blocks, and delete the "your old personal link still works" line from
`docs/editor-guide-for-john.md` — otherwise the one document John keeps points at a door that
no longer exists.
