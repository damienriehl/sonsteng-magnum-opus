#!/usr/bin/env bash
# Deploy the walkthrough site to the Hetzner DEV box → https://sonsteng-dev.damienriehl.com
# Pattern mirrors woodshed/deploy: git-archive -> rsync -> docker compose up.
set -euo pipefail
HOST=hetzner-dev
REMOTE_DIR=/opt/sonsteng
BRANCH="${1:-main}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT

echo "→ staging '$BRANCH' from $ROOT"
git -C "$ROOT" archive "$BRANCH" | tar -x -C "$STAGE"

echo "→ shipping to $HOST:$REMOTE_DIR"
ssh "$HOST" "mkdir -p $REMOTE_DIR"
rsync -az --delete "$STAGE/" "$HOST:$REMOTE_DIR/"

echo "→ (re)starting container"
ssh "$HOST" "cd $REMOTE_DIR && docker compose -f deploy/docker-compose.yml up -d --remove-orphans"

echo "✓ deployed → https://sonsteng-dev.damienriehl.com/"
