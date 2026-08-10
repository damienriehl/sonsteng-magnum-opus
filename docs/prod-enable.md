# Production enablement — Publisher release lane

Production is not enabled by running an imperative Pages or Worker deployment. The old direct
instructions were retired on 2026-08-09 after Damien chose: **a Publisher explicitly releases an
approved batch**. `deploy/deploy-prod.sh` is now a disabled tripwire.

The authoritative release procedure is [prod-release-operations.md](prod-release-operations.md).
It keeps DEV application separate from production, requires an immutable contiguous batch,
human Access Publisher authorization, coordinated Pages + Worker activation, exact-provenance
verification, and recorded-pair recovery. The executor and timer remain config-off until every
enablement gate in that runbook is recorded.

Engineering deployment does not enable publication. The exact editing-Worker rollback proof,
process-scoped one-shot canary, first-tick configuration digest, stopped-timer activation ordering,
and compensating config-off procedure are mandatory in the authoritative runbook before routine
enablement.

The Access-door operations below remain authoritative for login, identity mapping, routing, and
revocation. The Access-only `damienadmin` slot carries the independent Publisher scope on this
canonical DEV/apply ledger. The separate production Worker carries neither Access nor Publisher
scope. These grants do not themselves publish; only the frozen release workflow may do so.

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
