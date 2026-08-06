#!/usr/bin/env bash
# install-todo-timer.sh — install (or refresh) the Legal Practicum TODO reminder
# as a systemd USER timer on the home box. Run MANUALLY by Damien; nothing here
# runs on its own and the build never invokes it. Idempotent.
#
#   Usage:  bash tools/install-todo-timer.sh              # install + enable + start
#           bash tools/install-todo-timer.sh --uninstall
#
# It writes a oneshot service running tools/todo_report.py plus a timer that fires
# once each morning at 09:00 America/Chicago, matching the home box's
# systemd-user-timer convention (see install-digest-timer.sh, rc-wip.timer).
#
# No secrets are needed. The ntfy topic is read by path from
# ~/.config/claude-rc/ntfy-topic — the same rotatable topic the editor digest uses.
# Override the click-through target with SONSTENG_TODO_URL in the env file below.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-todo/env"
SERVICE="sonsteng-todo.service"
TIMER="sonsteng-todo.timer"
ONCALENDAR="${SONSTENG_TODO_ONCALENDAR:-*-*-* 09:00:00}"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "$TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
  systemctl --user daemon-reload
  echo "[todo] uninstalled $TIMER / $SERVICE (env file left at $ENV_FILE)."
  exit 0
fi

command -v python3 >/dev/null || { echo "[todo] python3 not found" >&2; exit 1; }
[[ -f "$REPO_ROOT/docs/TODO.md" ]] || { echo "[todo] docs/TODO.md missing" >&2; exit 1; }

mkdir -p "$UNIT_DIR" "$(dirname "$ENV_FILE")"

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<'EOF'
# Legal Practicum TODO reminder environment (0600). All optional.
# SONSTENG_TODO_URL=https://github.com/damienriehl/sonsteng-magnum-opus/blob/main/docs/TODO.md
# SONSTENG_NTFY_TOPIC=   # normally read from ~/.config/claude-rc/ntfy-topic
# SONSTENG_NTFY_SERVER=https://ntfy.sh
EOF
  echo "[todo] wrote $ENV_FILE (all values optional)."
fi

# NOTE: the repo path contains a space ("Coding Projects"), and the three settings
# below each want it handled differently. Getting any of them wrong fails loudly
# only on the second one — the first fails at RUN time, days later, from a timer.
#   ExecStart=        is a command line, split on whitespace -> MUST be quoted.
#   WorkingDirectory= is a path taken literally -> MUST NOT be quoted
#                     ("path is not absolute" if you do).
#   Documentation=    is a URL list; file:// with a raw space is rejected and
#                     dropped -> point at the remote URL instead.
cat > "$UNIT_DIR/$SERVICE" <<EOF
[Unit]
Description=Legal Practicum — TODO reminder (docs/TODO.md -> ntfy)
Documentation=https://github.com/damienriehl/sonsteng-magnum-opus/blob/main/docs/TODO.md

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
EnvironmentFile=-$ENV_FILE
ExecStart=/usr/bin/env python3 "$REPO_ROOT/tools/todo_report.py"
Nice=10
EOF

cat > "$UNIT_DIR/$TIMER" <<EOF
[Unit]
Description=Legal Practicum TODO reminder timer

[Timer]
OnCalendar=$ONCALENDAR
Persistent=true
AccuracySec=1m
Unit=$SERVICE

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER"

echo "[todo] installed. Schedule: $ONCALENDAR (system timezone)."
systemctl --user list-timers "$TIMER" --no-pager || true
echo
echo "Check it now without notifying:  python3 $REPO_ROOT/tools/todo_report.py --dry-run"
