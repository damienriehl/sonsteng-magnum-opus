#!/usr/bin/env bash
# install-digest-timer.sh — install (or refresh) the Sonsteng digest-push systemd
# USER timer on the home box. Run MANUALLY by Damien; nothing here runs on its own
# and this script is NOT invoked by the build. Idempotent.
#
#   Usage:  bash tools/install-digest-timer.sh   # install + enable + start
#           bash tools/install-digest-timer.sh --uninstall
#
# It writes a oneshot service that runs tools/digest_push.py plus a timer that
# fires a few times a day (09:00, 13:00, 17:00, 21:00 America/Chicago), matching
# the home box's systemd-user-timer convention (see rc-wip.timer /
# coding-projects-sync.timer).
#
# PREREQS (env for the service — put them in the EnvironmentFile below, 0600):
#   EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1
#   EDIT_SERVICE_TOKEN=<admin bookmark token>     # from ~/.secrets/sonsteng-editor-tokens
#   EDIT_REVIEW_URL=https://sonsteng-chat.damienriehl.workers.dev/edit/review  # optional
# The ntfy topic is read from ~/.config/claude-rc/ntfy-topic (the rc-notify topic).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-digest/env"
SERVICE="sonsteng-digest.service"
TIMER="sonsteng-digest.timer"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "$TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
  systemctl --user daemon-reload
  echo "[digest] uninstalled $TIMER / $SERVICE (env file left at $ENV_FILE)."
  exit 0
fi

mkdir -p "$UNIT_DIR" "$(dirname "$ENV_FILE")"

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<EOF
# Sonsteng digest-push environment (0600). Fill EDIT_SERVICE_TOKEN from
# ~/.secrets/sonsteng-editor-tokens — NEVER commit this file.
EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1
EDIT_SERVICE_TOKEN=
EDIT_REVIEW_URL=https://sonsteng-chat.damienriehl.workers.dev/edit/review
EOF
  echo "[digest] wrote a template env file: $ENV_FILE"
  echo "[digest] -> fill EDIT_SERVICE_TOKEN (from ~/.secrets/sonsteng-editor-tokens), then re-run."
fi

cat > "$UNIT_DIR/$SERVICE" <<EOF
[Unit]
Description=Sonsteng editor pending-suggestion digest push (ntfy)

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 "$REPO_ROOT/tools/digest_push.py"
EOF

cat > "$UNIT_DIR/$TIMER" <<EOF
[Unit]
Description=Fire the Sonsteng digest push a few times a day

[Timer]
OnCalendar=*-*-* 09,13,17,21:00 America/Chicago
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER"
echo "[digest] installed + enabled $TIMER"
systemctl --user list-timers "$TIMER" --all || true
