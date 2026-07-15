#!/usr/bin/env bash
# Deploy the walkthrough site to the Hetzner DEV box → https://sonsteng-dev.damienriehl.com
# Pattern mirrors woodshed/deploy: git-archive -> rsync -> docker compose up.
set -euo pipefail
HOST=hetzner-dev
REMOTE_DIR=/opt/sonsteng
PROJECT=sonsteng          # explicit Compose project name — MUST be unique on the box.
                          # (The box hosts other stacks whose compose file also sits in a
                          #  dir named "deploy"; without -p they'd share the default project
                          #  name and could remove each other's containers. Never add
                          #  --remove-orphans here.)
BRANCH="${1:-main}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT

echo "→ staging '$BRANCH' from $ROOT"
git -C "$ROOT" archive "$BRANCH" | tar -x -C "$STAGE"

echo "→ shipping to $HOST:$REMOTE_DIR"
ssh "$HOST" "[ -d $REMOTE_DIR ] || { sudo mkdir -p $REMOTE_DIR && sudo chown \$(whoami): $REMOTE_DIR; }"
rsync -az --delete "$STAGE/" "$HOST:$REMOTE_DIR/"

echo "→ (re)starting container (project=$PROJECT)"
ssh "$HOST" "cd $REMOTE_DIR && docker compose -p $PROJECT -f deploy/docker-compose.yml up -d"

echo "✓ deployed → https://sonsteng-dev.damienriehl.com/"
