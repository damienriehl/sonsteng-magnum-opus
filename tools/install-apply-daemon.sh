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
# DEDICATED DAEMON CHECKOUT (2026-07-24). The services do NOT run from an
# interactive checkout. They run from a **daemon-owned git worktree** —
#
#     $HOME/.local/share/sonsteng-daemon/checkout   (override: SONSTENG_DAEMON_ROOT)
#
# — which has the canonical branch (`main`) checked out and is touched by nothing
# but the daemon. Reason: the apply engine and the History revert both refuse to
# run on a dirty tree, so sharing a tree with interactive sessions let any parked
# edit (or even a stray `build_site.py` run) block auto-apply. A *worktree* rather
# than a clone is deliberate: it shares the object store and refs with the main
# checkout, so an apply/revert commit the daemon makes is visible to interactive
# sessions immediately (and pushable from there) — exactly the old behavior, minus
# the shared working tree. Consequence: `main` is checked out HERE, so interactive
# checkouts cannot check out `main` — merge into it from the daemon worktree under
# the daemon flock (see docs/direct-apply-daemon.md "Deploy topology").
#
# PREREQS (env for the services — put them in the EnvironmentFile below, 0600):
#   EDIT_API_BASE=https://sonsteng-chat.damienriehl.workers.dev/edit/v1
#   EDIT_SERVICE_TOKEN=<admin bookmark token>     # from ~/.secrets/sonsteng-editor-tokens (EDIT_TOKEN_ADMIN)
#   APPLY_DEPLOY_BRANCH=main                       # canonical branch published to DEV
# The ntfy topic is read from ~/.config/claude-rc/ntfy-topic (the rc-notify topic).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DAEMON_ROOT="${SONSTENG_DAEMON_ROOT:-$HOME/.local/share/sonsteng-daemon/checkout}"
CANONICAL_BRANCH="${APPLY_DEPLOY_BRANCH:-main}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-apply/env"
OBSERVER_ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/sonsteng-release-observer/env"
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
  echo "[apply-daemon] uninstalled timers/services (env files left at $ENV_FILE and $OBSERVER_ENV_FILE)."
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
APPLY_DEPLOY_BRANCH=main
EOF
  echo "[apply-daemon] wrote a template env file: $ENV_FILE"
  echo "[apply-daemon] -> fill EDIT_SERVICE_TOKEN (EDIT_TOKEN_ADMIN from ~/.secrets/sonsteng-editor-tokens), then re-run."
fi

# The PROD observer is a different least-privilege principal in its own 0600
# file. It may call only release status/frontier/audit GETs. Never put the DEV
# admin token or the production release-service token here.
mkdir -p "$(dirname "$OBSERVER_ENV_FILE")"
if [[ ! -f "$OBSERVER_ENV_FILE" ]]; then
  umask 077
  cat > "$OBSERVER_ENV_FILE" <<EOF
# Dedicated read-only production-ledger observer. Never reuse EDIT_SERVICE_TOKEN
# or SONSTENG_PROD_RELEASE_BEARER.
SONSTENG_PROD_OBSERVER_BEARER=
EOF
  echo "[apply-daemon] wrote observer template: $OBSERVER_ENV_FILE"
fi
if [[ ! -f "$OBSERVER_ENV_FILE" || -L "$OBSERVER_ENV_FILE" \
      || "$(stat -c '%a' "$OBSERVER_ENV_FILE")" != "600" \
      || "$(stat -c '%u' "$OBSERVER_ENV_FILE")" != "$(id -u)" ]]; then
  echo "[apply-daemon] observer env must be an owned, regular, non-symlink mode-0600 file: $OBSERVER_ENV_FILE" >&2
  exit 1
fi

# ---- dedicated daemon checkout (git worktree on the canonical branch) ------- #
# Idempotent: created once, reused forever. Never created inside an interactive
# checkout; refuses to install if the two would be the same tree.
if [[ "$(cd "$REPO_ROOT" && pwd -P)" == "$(cd "$DAEMON_ROOT" 2>/dev/null && pwd -P || echo "__none__")" ]]; then
  : # already running from the daemon checkout — nothing to provision
elif [[ -d "$DAEMON_ROOT/.git" || -f "$DAEMON_ROOT/.git" ]]; then
  echo "[apply-daemon] daemon checkout present: $DAEMON_ROOT"
else
  echo "[apply-daemon] creating the dedicated daemon checkout: $DAEMON_ROOT"
  mkdir -p "$(dirname "$DAEMON_ROOT")"
  git -C "$REPO_ROOT" worktree add "$DAEMON_ROOT" "$CANONICAL_BRANCH"
fi

# Branch sanity: the daemon's tree MUST hold the branch it publishes (the apply
# engine fast-forward-merges into whatever is checked out there).
if [[ -e "$DAEMON_ROOT/.git" ]]; then
  have="$(git -C "$DAEMON_ROOT" rev-parse --abbrev-ref HEAD)"
  if [[ "$have" != "$CANONICAL_BRANCH" ]]; then
    echo "[apply-daemon] WARNING: $DAEMON_ROOT is on '$have' but APPLY_DEPLOY_BRANCH is '$CANONICAL_BRANCH'."
    echo "[apply-daemon]          Fix with: git -C \"$DAEMON_ROOT\" checkout $CANONICAL_BRANCH"
  fi
  # Generated import trees are gitignored, so a fresh worktree has none. Build them
  # once or the Worker tests / history bundle will be missing in the daemon tree.
  if [[ ! -f "$DAEMON_ROOT/app/worker/editor-data/editor-map.generated.json" ]]; then
    echo "[apply-daemon] bootstrapping generated bundles in the daemon checkout…"
    ( cd "$DAEMON_ROOT" \
      && python3 tools/build_site.py --check \
      && python3 tools/build_worker_personas.py \
      && python3 tools/build_instructor_bundle.py \
      && python3 tools/build_history.py \
      && node app/worker/scripts/bundle-editor-data.mjs ) >/dev/null
    # build_site stamps the *current* HEAD into site/platform/data/.build-stamp.json,
    # so a rebuild at a newer commit leaves that one file dirty. site/ is fully
    # regenerable — restore it so the daemon starts from a clean tree.
    git -C "$DAEMON_ROOT" checkout -- site
    echo "[apply-daemon] bundles built; daemon tree clean."
  fi
fi

# ---- apply daemon (oneshot) + 2-min timer ---------------------------------- #
cat > "$UNIT_DIR/$APPLY_SERVICE" <<EOF
[Unit]
Description=Sonsteng direct-apply daemon (flush accepted editor edits -> canonical + DEV)

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
EnvironmentFile=$OBSERVER_ENV_FILE
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 "$DAEMON_ROOT/tools/direct_apply_daemon.py"
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
ExecStart=/usr/bin/python3 "$DAEMON_ROOT/tools/editorial_pass.py" --daily
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
echo "[apply-daemon] daemon checkout: $DAEMON_ROOT (branch $CANONICAL_BRANCH)"
systemctl --user list-timers "$APPLY_TIMER" "$EDIT_TIMER" --all || true
