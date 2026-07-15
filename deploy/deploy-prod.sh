#!/usr/bin/env bash
# Deploy the walkthrough site to PROD → https://sonsteng.damienriehl.com
# Cloudflare Pages direct-upload (no build; the site is a single static index.html).
# Re-run after edits: same command → same URL.
set -euo pipefail
PROJECT=sonsteng
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Cloudflare creds (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID) — same source the
# other portfolio sites use.
set -a; source "$HOME/.config/cloudflare/creds.env"; set +a
export WRANGLER_SEND_METRICS=false

echo "→ deploying $ROOT/site to Cloudflare Pages project '$PROJECT'"
npx --yes wrangler@latest pages deploy "$ROOT/site" \
  --project-name "$PROJECT" --branch main --commit-dirty=true

echo "✓ deployed → https://sonsteng.damienriehl.com/  (alias: https://$PROJECT.pages.dev/)"
# One-time custom-domain wiring (already done; kept for reference):
#   curl -sX POST -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H 'Content-Type: application/json' \
#     "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PROJECT/domains" \
#     -d '{"name":"sonsteng.damienriehl.com"}'
#   # + proxied CNAME sonsteng → sonsteng.pages.dev in zone 45539317ebc2598f913b867756fa58ea
