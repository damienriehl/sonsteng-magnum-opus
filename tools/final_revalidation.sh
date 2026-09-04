#!/usr/bin/env bash
# ============================================================================
# final_revalidation.sh — run the complete persona-UAT revalidation at one SHA.
#
# WHY: the final browser, binding, and accessibility legs originally lived in a
# session scratchpad. Keeping their exact orchestration in the repository makes
# the release record repeatable and prevents an environment or leg from being
# omitted during a manual rerun.
#
# Usage:
#   bash tools/final_revalidation.sh
#   LOCAL_PORT=8792 DEV_BASE=https://dev.example.test \
#     PROD_BASE=https://example.test bash tools/final_revalidation.sh
#
# Environment variables:
#   LOCAL_PORT  local static-server port (default: 8791)
#   DEV_BASE    DEV site origin (default: https://sonsteng-dev.damienriehl.com)
#   PROD_BASE   production origin (default: https://legalpracticum.org)
#
# Runtime is approximately 15 minutes. Run from a clean worktree at the exact
# SHA under test; tracked and non-ignored untracked changes are rejected so
# every run file describes one repository revision. The script writes logs and
# run evidence under build/uat/.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
ROOT=$(pwd -P)
PORT=${LOCAL_PORT:-8791}
DEV_BASE=${DEV_BASE:-https://sonsteng-dev.damienriehl.com}
PROD_BASE=${PROD_BASE:-https://legalpracticum.org}
DEV_BASE=${DEV_BASE%/}
PROD_BASE=${PROD_BASE%/}
BUILD_UAT="$ROOT/build/uat/"
SERVER_PID=""
MARKER_PATH=""
REVALIDATION_STATUS=0

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

# build_site.py refreshes this tracked stamp as a side effect. Keep the freshly
# generated stamp through the local legs, then restore the committed copy.
restore_build_stamp() {
  if git checkout -q -- site/platform/data/.build-stamp.json 2>/dev/null; then
    return 0
  fi
  printf '%s\n' \
    'ERROR: could not restore generated build stamp; site/platform/data/.build-stamp.json was left installed:' \
    >&2
  git status --short -- site/platform/data/.build-stamp.json >&2 || true
  return 1
}

# SIGKILL and host crashes bypass the EXIT trap. Remove only regular markers
# created by this script whose recorded owner is no longer running; every other
# untracked path remains visible to the clean-worktree gate below.
remove_stale_server_markers() {
  local marker marker_name marker_pid marker_token resolved_site
  resolved_site=$(realpath -m -- "$ROOT/site") || \
    die "could not resolve site/ before stale-marker cleanup"
  if [ "$resolved_site" != "$ROOT/site" ]; then
    die "site/ must resolve to its repository-local path before stale-marker cleanup (got $resolved_site)"
  fi

  for marker in "$ROOT"/site/.final-revalidation-server.??????; do
    if [ ! -f "$marker" ] || [ -L "$marker" ]; then
      continue
    fi
    marker_name=${marker##*/}
    if ! [[ "$marker_name" =~ ^\.final-revalidation-server\.[A-Za-z0-9]{6}$ ]]; then
      continue
    fi
    marker_token=$(<"$marker")
    if ! [[ "$marker_token" =~ ^final-revalidation:([[:xdigit:]]{40}|[[:xdigit:]]{64}):([0-9]+):[0-9]+$ ]]; then
      continue
    fi
    marker_pid=${BASH_REMATCH[2]}
    if kill -0 "$marker_pid" 2>/dev/null; then
      continue
    fi
    rm -f -- "$marker" || die "could not remove stale local-server marker: $marker"
    printf 'removed stale local-server marker: %s\n' "${marker#"$ROOT"/}"
  done
}

require_clean_worktree() {
  local context=$1
  local changes
  local -a pathspecs=()
  shift
  if [ "$#" -gt 0 ]; then
    pathspecs=(-- . "$@")
  fi
  if ! changes=$(git status --short --untracked-files=normal "${pathspecs[@]}"); then
    die "could not inspect worktree status"
  fi
  if [ -n "$changes" ]; then
    printf '%s\n' "$changes" >&2
    die "$context"
  fi
}

clear_prior_evidence() {
  rm -rf -- "${BUILD_UAT}runs" "${BUILD_UAT}shots" || \
    die "could not clear prior UAT evidence"
}

record_status() {
  if [ "$1" -ne 0 ]; then
    REVALIDATION_STATUS=1
  fi
}

cleanup() {
  local status=${1:-0}
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ -n "$MARKER_PATH" ]; then
    rm -f -- "$MARKER_PATH"
  fi
  if ! restore_build_stamp; then
    status=1
  fi
  return "$status"
}

cleanup_on_exit() {
  local status=$?
  trap - EXIT
  cleanup "$status"
  exit $?
}

remove_stale_server_markers
require_clean_worktree "tracked or untracked changes found; run from a clean worktree at the SHA under test"

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  die "LOCAL_PORT must be an integer from 1 through 65535"
fi
PORT=$((10#$PORT))
if ((PORT < 1 || PORT > 65535)); then
  die "LOCAL_PORT must be an integer from 1 through 65535"
fi

# Refuse symlink escapes before creating or deleting anything. Removal targets
# are fixed children of this canonical repository-local directory.
resolved_uat=$(realpath -m "$BUILD_UAT") || die "could not resolve build/uat/"
if [ "$resolved_uat/" != "$BUILD_UAT" ]; then
  die "build/uat/ must resolve to its repository-local path (got $resolved_uat)"
fi
mkdir -p "$BUILD_UAT" || die "could not create build/uat/"
clear_prior_evidence

SHA=$(git rev-parse HEAD) || die "could not resolve HEAD"
printf 'revalidation @ %s in %s\n' "$SHA" "$ROOT"

trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

MARKER_PATH=$(mktemp "$ROOT/site/.final-revalidation-server.XXXXXX") || \
  die "could not create local-server identity marker"
MARKER_NAME=$(basename "$MARKER_PATH")
MARKER_TOKEN="final-revalidation:$SHA:$$:$RANDOM"
printf '%s\n' "$MARKER_TOKEN" > "$MARKER_PATH"
MARKER_URL="http://127.0.0.1:$PORT/$MARKER_NAME"

server_matches_worktree() {
  [ "$(curl -fsS --max-time 2 "$MARKER_URL" 2>/dev/null)" = "$MARKER_TOKEN" ]
}

command -v curl >/dev/null 2>&1 || die "curl is required to verify the local server"
if server_matches_worktree; then
  printf 'reusing this worktree\047s site/ server on port %s\n' "$PORT"
else
  (cd "$ROOT/site" && exec python3 -m http.server "$PORT" --bind 127.0.0.1) \
    > "${BUILD_UAT}local-server.log" 2>&1 &
  SERVER_PID=$!
  server_ready=0
  for _ in {1..20}; do
    if server_matches_worktree; then
      server_ready=1
      break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      wait "$SERVER_PID" 2>/dev/null || true
      die "LOCAL_PORT $PORT is occupied or the local server failed; see build/uat/local-server.log"
    fi
    sleep 0.25
  done
  if [ "$server_ready" -ne 1 ]; then
    die "local static server did not become ready; see build/uat/local-server.log"
  fi
fi

run_generator() {
  local label=$1
  shift
  if ! "$@"; then
    die "generator failed: $label"
  fi
}

run_generator "site build" python3 tools/build_site.py --check
run_generator "instructor bundle" python3 tools/build_instructor_bundle.py
run_generator "editor data bundle" node app/worker/scripts/bundle-editor-data.mjs
require_clean_worktree "generators changed tracked or untracked files; revalidation would no longer describe one SHA" \
  ":(top,exclude,literal)site/$MARKER_NAME" \
  ":(top,exclude,literal)site/platform/data/.build-stamp.json"

run() {
  local label=$1
  local status
  shift
  printf '===== %s =====\n' "$label"
  node tools/verify_persona_journeys.js "$@" > "${BUILD_UAT}final-$label.log" 2>&1
  status=$?
  record_status "$status"
  printf 'exit=%s\n' "$status"
  grep -E '^(JOURNEY SUMMARY|RUN FILE)' "${BUILD_UAT}final-$label.log" || true
  # Deliberate canaries prove that the runner can fail; omit only their expected
  # noise from this excerpt. See docs/solutions/uat/2026-09-02-browser-journeys-measure-the-wrong-thing.md.
  grep -E '^(FAIL|ERROR|BLOCKED)' "${BUILD_UAT}final-$label.log" \
    | grep -v deliberate-canary \
    | cut -c1-180 || true
}

run browser-local --base "http://127.0.0.1:$PORT" --env-label local
run browser-dev --base "$DEV_BASE" --env-label dev
run browser-prod --base "$PROD_BASE" --env-label prod
run bindings-local --bindings --env-label local
run bindings-dev --bindings --env-label dev --only hostile-bot-gate,student-live-provider-dev,hostile-live-redteam-dev
run bindings-prod --bindings --env-label prod --only hostile-bot-gate

printf '===== a11y audit (explicit pages, both envs) =====\n'
node tools/a11y_audit.js \
  "${DEV_BASE}/" \
  "${DEV_BASE}/platform/" \
  "${DEV_BASE}/platform/matters/" \
  "${DEV_BASE}/platform/matters/m05-dwi-meridian/" \
  "${DEV_BASE}/platform/hours/" \
  "${DEV_BASE}/cost-per-credit.html" \
  "${PROD_BASE}/" \
  "${PROD_BASE}/platform/" \
  "${PROD_BASE}/platform/matters/" \
  "${PROD_BASE}/platform/matters/m05-dwi-meridian/" \
  "${PROD_BASE}/platform/hours/" \
  "${PROD_BASE}/cost-per-credit.html" \
  > "${BUILD_UAT}final-a11y.log" 2>&1
a11y_status=$?
record_status "$a11y_status"
printf 'a11y exit=%s\n' "$a11y_status"
grep -E '^=== |A11Y AUDIT' "${BUILD_UAT}final-a11y.log" || true

cleanup "$REVALIDATION_STATUS"
cleanup_status=$?
trap - EXIT
if [ "$cleanup_status" -ne 0 ]; then
  REVALIDATION_STATUS=1
fi
git status --short | head -5
printf 'revalidation done @ %s\n' "$SHA"
exit "$REVALIDATION_STATUS"
