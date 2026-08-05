#!/usr/bin/env bash
# Generate or install the isolated PROD promotion user service. This installer
# never enables or starts the timer; rollout requires a separate, attributed
# receipt after the calibration and restoration gates in docs/prod-enable.md.
set -euo pipefail

MODE="${1:---dry-run}"
case "$MODE" in --dry-run|--install) ;; *) echo "usage: $0 [--dry-run|--install]" >&2; exit 2;; esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_ROOT="${PROD_PROMOTION_CONFIG_ROOT:-${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-prod-promotion}"
DATA_ROOT="${PROD_PROMOTION_DATA_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/sonsteng-prod-promotion}"
UNIT_DIR="${PROD_PROMOTION_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
CHECKOUT="${PROD_PROMOTION_CHECKOUT:-$DATA_ROOT/checkout}"
ENV_FILE="$CONFIG_ROOT/env"
CREDENTIAL_FILE="$CONFIG_ROOT/credentials"
STATE_FILE="$DATA_ROOT/state.json"
LOCK_FILE="$DATA_ROOT/promotion.lock"
SERVICE="sonsteng-prod-promotion.service"
TIMER="sonsteng-prod-promotion.timer"
API_BASE="${PROD_PROMOTION_API_BASE:-https://sonsteng-chat-production.damienriehl.workers.dev/edit/v1}"
BRANCH="${PROD_PROMOTION_BRANCH:-main}"

[[ "$API_BASE" == https://* ]] || { echo "REFUSED: PROD API must use https" >&2; exit 2; }
[[ "$BRANCH" == main ]] || { echo "REFUSED: PROD promotion checkout must use main" >&2; exit 2; }
[[ "$CHECKOUT" != "$REPO_ROOT" ]] || { echo "REFUSED: PROD requires an isolated checkout" >&2; exit 2; }

service_text() {
  printf '%s\n' \
    '[Unit]' \
    'Description=Sonsteng PROD promotion coordinator (default-off)' \
    'After=network-online.target' \
    '' \
    '[Service]' \
    'Type=oneshot' \
    "EnvironmentFile=$ENV_FILE" \
    "EnvironmentFile=$CREDENTIAL_FILE" \
    'UMask=0077' \
    'NoNewPrivileges=true' \
    'PrivateTmp=true' \
    'ProtectSystem=strict' \
    'ProtectHome=read-only' \
    "ReadWritePaths=$DATA_ROOT $CHECKOUT" \
    "WorkingDirectory=$CHECKOUT" \
    "ExecStart=/usr/bin/flock -n $LOCK_FILE /usr/bin/python3 $CHECKOUT/tools/prod_promotion_daemon.py"
}

timer_text() {
  printf '%s\n' \
    '[Unit]' \
    'Description=Poll the Sonsteng PROD promotion lane every minute' \
    '' \
    '[Timer]' \
    'OnBootSec=1min' \
    'OnUnitActiveSec=1min' \
    'Persistent=true' \
    '' \
    '[Install]' \
    'WantedBy=timers.target'
}

if [[ "$MODE" == --dry-run ]]; then
  printf 'mode=dry-run\nservice=%s\ntimer=%s\napi_base=%s\nwrangler_environment=production\ncheckout=%s\nbranch=%s\nlock=%s\nstate=%s\ncredentials=configured(path redacted)\nauto_enable=false\n' \
    "$SERVICE" "$TIMER" "$API_BASE" "$CHECKOUT" "$BRANCH" "$LOCK_FILE" "$STATE_FILE"
  printf '%s\n' '--- service contract ---'
  service_text
  printf '%s\n' '--- timer contract ---'
  timer_text
  exit 0
fi

mkdir -p "$CONFIG_ROOT" "$DATA_ROOT" "$UNIT_DIR"
umask 077
if [[ ! -f "$ENV_FILE" ]]; then
  printf 'PROD_PROMOTION_API_BASE=%s\nPROD_PROMOTION_ENVIRONMENT=production\nPROD_PROMOTION_CHECKOUT=%s\nPROD_PROMOTION_BRANCH=main\nPROD_PROMOTION_LOCK=%s\nPROD_PROMOTION_STATE=%s\nPROD_PROMOTION_ENABLED=0\n' \
    "$API_BASE" "$CHECKOUT" "$LOCK_FILE" "$STATE_FILE" >"$ENV_FILE"
fi
if [[ ! -f "$CREDENTIAL_FILE" ]]; then
  printf '%s\n' '# 0600 capability-separated paths/values; fill out of band.' \
    'PROD_LEDGER_TOKEN=' 'PROD_CLOUDFLARE_CREDENTIAL_FILE=' 'PROD_GITHUB_CREDENTIAL_FILE=' >"$CREDENTIAL_FILE"
fi
service_text >"$UNIT_DIR/$SERVICE"
timer_text >"$UNIT_DIR/$TIMER"
chmod 600 "$ENV_FILE" "$CREDENTIAL_FILE" "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
printf 'installed_default_off=true\nservice=%s\ntimer=%s\nenv=%s\ncredentials=configured(path redacted)\nnext=complete rollout receipt, then explicitly enable timer\n' \
  "$SERVICE" "$TIMER" "$ENV_FILE"
