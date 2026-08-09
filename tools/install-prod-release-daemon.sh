#!/usr/bin/env bash
# Install the config-off PROD release executor as a systemd user timer.
# Run manually. This installer never deploys, reads a secret, or enables units.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DAEMON_ROOT="${SONSTENG_DAEMON_ROOT:-$HOME/.local/share/sonsteng-daemon/checkout}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-prod-release/env"
SERVICE="sonsteng-prod-release.service"
TIMER="sonsteng-prod-release.timer"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "$TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
  systemctl --user daemon-reload
  echo "[prod-release] units removed; environment file preserved at $ENV_FILE"
  exit 0
fi

[[ -e "$DAEMON_ROOT/.git" ]] || {
  echo "[prod-release] dedicated daemon checkout missing: $DAEMON_ROOT" >&2
  echo "Install the DEV apply daemon/worktree first; no alternate checkout is allowed." >&2
  exit 1
}

mkdir -p "$UNIT_DIR" "$(dirname "$ENV_FILE")" "$DAEMON_ROOT/.locks"
if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<EOF
# Config-off by design. Keep false until the migration, known-good manifest,
# credential separation, live canary, and recovery drills are recorded.
SONSTENG_PROD_RELEASE_ENABLED=false
SONSTENG_PROD_LEDGER_URL=https://sonsteng-chat-production.damienriehl.workers.dev
SONSTENG_PROD_RELEASE_BEARER=
SONSTENG_PROD_PAGES_PROJECT=sonsteng
SONSTENG_PROD_PAGES_ARTIFACT=$DAEMON_ROOT/site
SONSTENG_PROD_PAGES_PROVENANCE_URL=https://sonsteng.damienriehl.com/platform/
SONSTENG_PROD_WORKER_CONFIG=$DAEMON_ROOT/app/worker/wrangler.jsonc
SONSTENG_PROD_WORKER_PROVENANCE_URL=https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance
SONSTENG_PROD_REPO=$DAEMON_ROOT
SONSTENG_PROD_MANIFEST=$DAEMON_ROOT/.release/authorized-manifest.json
SONSTENG_PROD_LOCK=$DAEMON_ROOT/.locks/daemon.lock
# Set exactly one to true only after the transient pairing has been proven.
SONSTENG_NEW_WORKER_ACCEPTS_OLD_PAGES=false
SONSTENG_OLD_WORKER_ACCEPTS_NEW_PAGES=false
EOF
  echo "[prod-release] wrote config-off 0600 template: $ENV_FILE"
fi

# ExecStart is quoted because the repository path may contain spaces;
# WorkingDirectory is deliberately unquoted because systemd treats it literally.
cat > "$UNIT_DIR/$SERVICE" <<EOF
[Unit]
Description=Sonsteng Publisher-authorized production release executor
Documentation=https://github.com/damienriehl/sonsteng-magnum-opus/blob/main/docs/prod-release-operations.md

[Service]
Type=oneshot
WorkingDirectory=$DAEMON_ROOT
EnvironmentFile=$ENV_FILE
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 "$DAEMON_ROOT/tools/prod_release_daemon.py"
EOF

cat > "$UNIT_DIR/$TIMER" <<EOF
[Unit]
Description=Check for a Publisher-authorized Sonsteng production release

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min
Persistent=true
Unit=$SERVICE

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
echo "[prod-release] installed config-off units; they remain disabled."
echo "[prod-release] inspect with: systemctl --user cat $SERVICE $TIMER"
echo "[prod-release] enablement requires the checklist in docs/prod-release-operations.md"
