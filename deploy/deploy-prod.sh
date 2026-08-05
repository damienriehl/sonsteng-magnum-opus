#!/usr/bin/env bash
# PROD publication is owned by the crash-safe promotion coordinator.  This
# historical direct-upload entrypoint is intentionally config-off: invoking it
# must never bypass paired Pages + Worker verification or advance only one half.
set -euo pipefail
PROJECT=sonsteng
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "PROD deployment is coordinator-only (config-off)."
  echo "project=$PROJECT artifact=$ROOT/site environment=production"
  echo "required=preview-id,production-pages-id,inactive-worker-version,exact-pair-live-check,main-cas"
  exit 0
fi

echo "ERROR: direct PROD deploy is disabled; use the promotion coordinator." >&2
echo "Run '$0 --dry-run' to inspect the non-mutating contract." >&2
exit 64

# Domain wiring is retained as documentation only; no command below is reachable.
# One-time custom-domain wiring (already done; kept for reference):
#   curl -sX POST -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H 'Content-Type: application/json' \
#     "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PROJECT/domains" \
#     -d '{"name":"sonsteng.damienriehl.com"}'
#   # + proxied CNAME sonsteng → sonsteng.pages.dev in zone 45539317ebc2598f913b867756fa58ea
