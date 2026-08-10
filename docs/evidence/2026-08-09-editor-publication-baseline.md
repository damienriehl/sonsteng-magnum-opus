# Editor publication baseline — 2026-08-09

This is the U1 migration fence for the Publisher-controlled production-batch work. It records
aggregate operational facts only; no suggestion text, credentials, or secret values were read into
this artifact.

## Live/current baseline

- The live review API reported **0 total rows and 0 unresolved rows**. The source-ref migration may
  therefore use an **empty-queue fence**; no unresolved row needs reprojection. Recheck immediately
  before any source-ref/schema migration because this is a point-in-time fact.
- `sonsteng-apply.timer` was active. Its most recent oneshot completed successfully and reported no
  accepted suggestions. The dedicated checkout was clean on `main` at `6837ae9`, equal to its
  `origin/main`.
- Current behavior is intentionally characterized: a row with status `accepted` is eligible for the
  existing claim/apply daemon. That daemon rebuilds canonical content and deploys **DEV only**.
  Approval/acceptance is therefore current DEV execution authority, not production publication
  authority.
- A clean generated-site check reported 31 skills, 108 tasks, 232 subtasks, and 5,955 editable map
  blocks across 72 mapped pages. Worker editor-data bundling completed from the same generated map.

## Writer and configuration inventory

- `tools/direct_apply_daemon.py` invokes `tools/apply_suggestions.py`; its deploy step targets
  `deploy/deploy-dev.sh` and cannot call the PROD deployer.
- `deploy/deploy-dev.sh` writes the Hetzner editing/DEV site.
- `deploy/deploy-prod.sh` is the current public Pages writer. It is an imperative direct upload to
  the `sonsteng` Pages project on branch `main`; it is not ledger-gated.
- `app/worker/wrangler.jsonc` defines separate DEV/default and production Worker environments.
  Production has a distinct name and empty routes, but its checked-in `DIRECT_APPLY=true` describes
  suggestion acceptance only; there is no current production executor on `main`.
- A read-only Cloudflare version inspection found production Worker version 17 (uploaded August 8)
  with the expected separate production upstream/origin and `DIRECT_APPLY=true`. Wrangler also
  reported that `EDIT_ACCESS_AUD`, `EDIT_ACCESS_TEAM_DOMAIN`, and `EDIT_ACCESS_HOST` are absent from
  the production environment because environment vars do not inherit. No secret values were
  displayed or recorded. This is current configuration evidence, not publication authority.
- No repository GitHub Actions workflow or scheduled repository job writes production. The dormant
  `feat/prod-editor-promotion` branch contains a promotion daemon and release primitives, but those
  are not present on or executed by current `main`.

## Superseded contract fence

The historical `2026-08-05-001-feat-prod-editor-promotion-plan.md` on the dormant promotion branch
records automatic/confidence-based production publication and a five-minute publication promise.
Those policy decisions are superseded by
`2026-08-09-001-feat-taxonomy-publisher-batches-plan.md`: accepted wording may continue to canonical
DEV automatically, but public production requires an immutable batch explicitly authorized by a
human Publisher. The dormant implementation remains evidence and a selective source of primitives;
it is not a merge target.

## Recheck before migration

1. Aggregate the live queue again and require zero unresolved rows.
2. Require the dedicated daemon checkout to be clean and at the expected canonical SHA.
3. Rebuild the site/editor map and require generated-map parity.
4. Stop rather than migrate if any unresolved row, dirty checkout, or map mismatch appears.

The immediate pre-migration recheck completed at `2026-08-09T17:00:26.223821+00:00` through the
aggregate digest endpoint using the daemon's configured environment: **30 total rows, 0 unresolved**.
No suggestion content or credential value was printed. The migration fence passed; all rows were
terminal.
