# Pre-user production deploy — operator procedure

**Authority:** Damien's dated answer of September 2, 2026 (Q1 in
`docs/decisions/2026-09-02-resume-and-uat-decision-sheet.md`): while the site has no users, an
agent may deploy merged `main` directly to `legalpracticum.org` and the production Worker.

**Expiry:** the first real user. After that, production changes only through the Publisher lane in
`docs/prod-release-operations.md`. This document then becomes history; do not extend it.

**What this procedure is not.** It is not a repository script. `deploy/deploy-prod.sh` stays a
disabled tripwire, `tools/prod_release_daemon.py` stays the only repository writer of production,
and `tools/tests/test_publication_boundary.py` keeps enforcing both. The commands below are run by
the operator (Damien, or the orchestrating agent under his authority) from a clean worktree at the
exact `main` commit being released. They mirror the executor's provider adapters in
`tools/prod_release_executor.py` so the live provenance headers stay meaningful.

## Preconditions

1. The worktree is clean and at the commit to release: `git status --short` prints nothing and
   `git rev-parse HEAD` equals `origin/main`.
2. The full preflight passed on that commit (`bash tools/preflight.sh`), or the PR that produced it
   recorded a passing preflight before merge.
3. Wrangler is authenticated to the project's Cloudflare account. `npx wrangler@4 whoami` names the
   account; no token is printed or stored by this procedure.
4. The generators have run in this worktree so the Worker's custom build step finds its inputs:
   `python3 tools/build_site.py --check` then `python3 tools/build_instructor_bundle.py`. Restore
   the tracked build stamp afterwards (`git checkout site/platform/data/.build-stamp.json`) so the
   tree stays clean.
5. `npx wrangler@4 deploy --env production --dry-run` from `app/worker/` exits cleanly and reports
   no route for the Access hostname.

## Wrangler top-level vars warning

Wrangler warns that the following top-level vars are absent from `env.production.vars`. This is
expected. Per Damien's 2026-09-03 D2 decision in
`docs/handoffs/2026-09-02-next-steps-and-open-decisions.md`, production should contain only values
its request paths need; do not silence the warning by copying DEV-only host or Access config.

| Var | Read by (file) | Production behavior when unset | Verdict |
|---|---|---|---|
| `EDIT_ACCESS_AUD` | `app/worker/src/access-jwt.js` | Access config is incomplete, so JWT verification returns `null` before any network fetch. | deliberately absent |
| `EDIT_ACCESS_TEAM_DOMAIN` | `app/worker/src/access-jwt.js` | Access config is incomplete, so no JWKS host is selected and verification returns `null`. | deliberately absent |
| `EDIT_ACCESS_HOST` | `app/worker/src/access-jwt.js`, `editor-auth.js`, `editor.js`, `host-routing.js` | The Access identity branch and bare-host doorway are inert; the legacy redirect also has no target. Token/cookie auth remains unchanged. | deliberately absent |
| `EDIT_LEGACY_HOST` | `app/worker/src/host-routing.js` | No legacy-editor redirect is attempted. Production has no route for that DEV hostname. | not needed |
| `PUBLIC_CANONICAL_HOST` | `app/worker/src/host-routing.js` | Public-host redirect handling returns `null`; requests continue to the normal Worker router. | not needed |
| `PUBLIC_REDIRECT_HOSTS` | `app/worker/src/host-routing.js` | The redirect-source set is empty (and the absent canonical host short-circuits first), so requests continue to the normal Worker router. | not needed |

In particular, a request to `sonsteng-chat-production.damienriehl.workers.dev` is not redirected or
rejected by these omissions. It proceeds to the normal `/edit` or API routing in `index.js`. The
three Access vars must remain absent unless production is deliberately given its own Access door.

## Record the rollback pair first

From `app/worker/`:

- `npx wrangler@4 deployments list --env production` — the last entry's `Version(s)` is the active
  Worker version. Record it.
- `npx wrangler@4 pages deployment list --project-name sonsteng --environment production` — the
  first row is the active Pages deployment. Record its ID and source commit.

Write both into the deploy record before any upload.

## Deploy order: Worker, then Pages

The Worker is deployed first because a newer Worker serves an older static site safely, while a
newer site may call an endpoint an older Worker lacks.

1. **Worker upload.** From `app/worker/`:
   `npx wrangler@4 versions upload --env production --message "release:pre-user:<sha>" --var "RELEASE_SHA:<sha>"`
   Capture the `Worker Version ID` from the output.
2. **Worker activate.** `npx wrangler@4 versions deploy <version-id> --env production --yes`.
3. **Worker verify.** After a few seconds,
   `curl -s -D - -o /dev/null https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance`
   must return `204` with `x-release-sha: <sha>`. Use GET, not HEAD: the route answers only GET, so
   `curl -I` reports a misleading `404`. A `503` means `RELEASE_SHA` did not reach the version.
   `curl -s -o /dev/null -w '%{http_code}' .../v1/session` returns `403` (the Turnstile gate; expected).
4. **Pages stage.** Copy `site/` to a temporary directory and append a `_headers` file:
   ```
   /*
     X-Release-SHA: <sha>
   ```
5. **Pages deploy.** From the repository root:
   `npx wrangler@4 pages deploy <staged-site> --project-name sonsteng --branch main --commit-hash <sha> --commit-message "release:pre-user:<sha>"`
   Capture the deployment URL; its subdomain is the deployment ID.
6. **Pages verify.** `curl -sI https://legalpracticum.org/platform/` returns `200` with
   `x-release-sha: <sha>`. Spot-check `https://legalpracticum.org/` and one deep page. Pages serves
   clean URLs, so `…/index.html` answers `308` to the directory form; that is expected.
7. **Parity.** Fetch the same page from DEV and production and confirm the only difference is the
   `spine-build` meta value (the build stamp), which encodes the base commit each was built from.

## Rollback

- **Worker:** `npx wrangler@4 versions deploy <previous-version-id> --env production --yes`.
- **Pages:** roll back in the Cloudflare dashboard to the recorded deployment ID, or through the
  Pages rollback API the executor uses (`tools/prod_release_executor.py`, `WranglerPagesAdapter.restore`).
- Verify both provenance headers return the previous SHA.

## Deploy record

Append one block per deploy to `docs/uat/pre-user-prod-deploys.md`:

```
Date (UTC):
Candidate SHA:
Previous Worker version / Pages deployment:
New Worker version / Pages deployment:
Worker provenance: 204 + sha | other
Pages provenance: 200 + sha | other
DEV/production parity: SAME except build stamp | other
Operator:
```

No credential value, token, or account secret belongs in the record. Account and project identifiers
are not secrets and may appear.
