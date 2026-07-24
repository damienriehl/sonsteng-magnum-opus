#!/usr/bin/env bash
# install-apply-daemon.sh — install (or refresh) the Sonsteng direct-apply DAEMON
# + the EDITORIAL-PASS systemd USER timers on the home box. Run MANUALLY by Damien;
# nothing here runs on its own and this script is NOT invoked by the build.
# Idempotent. Mirrors tools/install-digest-timer.sh (the house timer pattern).
#
#   Usage:  bash tools/install-apply-daemon.sh              # install + enable + start
#           bash tools/install-apply-daemon.sh --uninstall
#
# INSTALLS:
#   * sonsteng-apply.service   (oneshot) -> tools/direct_apply_daemon.py
#   * sonsteng-apply.timer     -> every 2 min (OnUnitActiveSec=2min), Persistent
#         The 2-min cadence IS the flush (SL3): no withholding. A quiet tick past
#         the 30-min idle window also fires the session-end editorial pass.
#   * sonsteng-editorial.service (oneshot) -> tools/editorial_pass.py --daily
#   * sonsteng-editorial.timer -> daily 21:30 America/Chicago, Persistent
#
# DEV-ONLY: the daemon deploys the canonical branch to the Hetzner DEV box; it can
# NEVER deploy PROD (deploy/deploy-dev.sh targets DEV exclusively).
#
# PREREQS (env for the services — put them in the EnvironmentFile below, 0600):
#   EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1
#   EDIT_SERVICE_TOKEN=<admin bookmark token>     # from ~/.secrets/sonsteng-editor-tokens (EDIT_TOKEN_ADMIN)
#   APPLY_DEPLOY_BRANCH=feat/canonical-docs        # canonical branch published to DEV
# The ntfy topic is read from ~/.config/claude-rc/ntfy-topic (the rc-notify topic).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-apply/env"
APPLY_SERVICE="sonsteng-apply.service"
APPLY_TIMER="sonsteng-apply.timer"
EDIT_SERVICE="sonsteng-editorial.service"
EDIT_TIMER="sonsteng-editorial.timer"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "$APPLY_TIMER" 2>/dev/null || true
  systemctl --user disable --now "$EDIT_TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$APPLY_SERVICE" "$UNIT_DIR/$APPLY_TIMER" \
        "$UNIT_DIR/$EDIT_SERVICE" "$UNIT_DIR/$EDIT_TIMER"
  systemctl --user daemon-reload
  echo "[apply-daemon] uninstalled timers/services (env file left at $ENV_FILE)."
  exit 0
fi

mkdir -p "$UNIT_DIR" "$(dirname "$ENV_FILE")"

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<EOF
# Sonsteng apply-daemon environment (0600). Fill EDIT_SERVICE_TOKEN from
# ~/.secrets/sonsteng-editor-tokens (the EDIT_TOKEN_ADMIN value) — NEVER commit this file.
EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1
EDIT_SERVICE_TOKEN=
APPLY_DEPLOY_BRANCH=feat/canonical-docs
EOF
  echo "[apply-daemon] wrote a template env file: $ENV_FILE"
  echo "[apply-daemon] -> fill EDIT_SERVICE_TOKEN (EDIT_TOKEN_ADMIN from ~/.secrets/sonsteng-editor-tokens), then re-run."
fi

# ---- apply daemon (oneshot) + 2-min timer ---------------------------------- #
cat > "$UNIT_DIR/$APPLY_SERVICE" <<EOF
[Unit]
Description=Sonsteng direct-apply daemon (flush accepted editor edits -> canonical + DEV)

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 "$REPO_ROOT/tools/direct_apply_daemon.py"
EOF

cat > "$UNIT_DIR/$APPLY_TIMER" <<EOF
[Unit]
Description=Fire the Sonsteng apply daemon every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ---- editorial pass (oneshot) + daily 21:30 timer -------------------------- #
cat > "$UNIT_DIR/$EDIT_SERVICE" <<EOF
[Unit]
Description=Sonsteng editorial pass (daily sweep of applied editor edits)

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 "$REPO_ROOT/tools/editorial_pass.py" --daily
EOF

cat > "$UNIT_DIR/$EDIT_TIMER" <<EOF
[Unit]
Description=Fire the Sonsteng editorial daily sweep at 21:30

[Timer]
OnCalendar=*-*-* 21:30 America/Chicago
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$APPLY_TIMER"
systemctl --user enable --now "$EDIT_TIMER"
echo "[apply-daemon] installed + enabled $APPLY_TIMER and $EDIT_TIMER"
systemctl --user list-timers "$APPLY_TIMER" "$EDIT_TIMER" --all || true
