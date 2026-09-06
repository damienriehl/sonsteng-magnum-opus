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
#
# The script never creates, writes, deletes, or serves outside the physical site/
# and build/uat/ directories it validated. Child tools it invokes (site generator,
# instructor bundle, journey runner, and accessibility audit) resolve repository
# paths themselves and are outside that guarantee, so a same-user directory swap
# during a child's run is out of scope.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
ROOT=$(pwd -P)
PORT=${LOCAL_PORT:-8791}
DEV_BASE=${DEV_BASE:-https://sonsteng-dev.damienriehl.com}
PROD_BASE=${PROD_BASE:-https://legalpracticum.org}
DEV_BASE=${DEV_BASE%/}
PROD_BASE=${PROD_BASE%/}
BUILD_UAT="$ROOT/build/uat"
ROOT_IDENTITY=""
BUILD_IDENTITY=""
BUILD_UAT_IDENTITY=""
BUILD_STAMP_DIR="$ROOT/site/platform/data"
BUILD_STAMP_DIR_IDENTITY=""
BUILD_STAMP_DIRTY=0
SERVER_PID=""
MARKER_PATH=""
MARKER_NAME=""
MARKER_FILE_IDENTITY=""
SITE_IDENTITY=""
LOCK_NAME=.final-revalidation.lock
LOCK_IDENTITY=""
LOCK_TOKEN=""
LOCK_HELD=0
LOCK_INITIALIZING=0
LOCK_GUARD_FD=""
REVALIDATION_STATUS=0

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

# build_site.py refreshes this tracked stamp as a side effect. Keep the freshly
# generated stamp through the local legs, then restore the committed copy.
restore_build_stamp() {
  [ "${BUILD_STAMP_DIRTY:-0}" -eq 1 ] || return 0
  if with_pinned_dir "$BUILD_STAMP_DIR" "$BUILD_STAMP_DIR_IDENTITY" \
      restore_build_stamp_in_pinned_dir .build-stamp.json; then
    BUILD_STAMP_DIRTY=0
    return 0
  fi
  printf '%s\n' \
    'ERROR: could not restore generated build stamp; site/platform/data/.build-stamp.json was left installed:' \
    >&2
  git status --short -- site/platform/data/.build-stamp.json >&2 || true
  return 1
}

restore_build_stamp_in_pinned_dir() {
  local relative_name=$1 restore_fd restore_name status=0
  case "$relative_name" in
    ""|.|..|/*|*/*) return 1 ;;
  esac
  restore_name=".final-revalidation-build-stamp.$$.$RANDOM.$RANDOM"
  open_new_relative_file_fd "$restore_name" restore_fd || return 1
  git show "HEAD:site/platform/data/.build-stamp.json" >&"$restore_fd" || status=$?
  exec {restore_fd}>&- || status=1
  if [ "$status" -ne 0 ]; then
    remove_in_pinned_dir "$BUILD_STAMP_DIR" "$BUILD_STAMP_DIR_IDENTITY" \
      file "$restore_name" || return 1
    return "$status"
  fi
  if ! mv -T -- "$restore_name" "$relative_name"; then
    remove_in_pinned_dir "$BUILD_STAMP_DIR" "$BUILD_STAMP_DIR_IDENTITY" \
      file "$restore_name" || return 1
    return 1
  fi
}

# Return the device:inode identity only after physically entering the expected
# repository-local directory and proving that no symlink redirected the entry.
pinned_directory_identity() {
  local expected_physical_dir=$1 resolved_dir
  (
    trap - EXIT
    cd -P -- "$expected_physical_dir" || return 1
    resolved_dir=$(pwd -P) || return 1
    [ "$resolved_dir" = "$expected_physical_dir" ] || return 1
    stat -Lc '%d:%i' -- .
  )
}

# Enter an already-captured physical directory, revalidate its device:inode,
# and run a command from that cwd. The command name itself must be a simple
# relative name; callers pass only relative filesystem operands.
with_pinned_dir() {
  local expected_physical_dir=$1 expected_identity=$2 command_name resolved_dir
  local pinned_return_fd previous_identity restore_status=0 status=1
  shift 2
  [ "$#" -gt 0 ] || return 1
  command_name=$1
  case "$command_name" in
    ""|.|..|/*|*/*) return 1 ;;
  esac
  exec {pinned_return_fd}< . || return 1
  previous_identity=$(stat -Lc '%d:%i' -- "/proc/self/fd/$pinned_return_fd") || {
    exec {pinned_return_fd}<&-
    return 1
  }
  if [ "$(stat -Lc '%F' -- "/proc/self/fd/$pinned_return_fd")" != directory ] || \
      ! cd -P -- "$expected_physical_dir" || \
      ! resolved_dir=$(pwd -P) || \
      [ "$resolved_dir" != "$expected_physical_dir" ] || \
      [ -z "$expected_identity" ] || \
      [ "$(stat -Lc '%d:%i' -- .)" != "$expected_identity" ]; then
    status=1
  else
    "$@"
    status=$?
  fi
  cd -P -- "/proc/self/fd/$pinned_return_fd" || restore_status=1
  if [ "$restore_status" -eq 0 ] && \
      [ "$(stat -Lc '%d:%i' -- .)" != "$previous_identity" ]; then
    restore_status=1
  fi
  exec {pinned_return_fd}<&- || restore_status=1
  [ "$restore_status" -eq 0 ] || return 1
  return "$status"
}

write_server_marker() {
  local relative_name=$1 expected_file_identity=$2 token=$3 marker_fd
  local actual_file_identity link_count status=0
  case "$relative_name" in
    ""|.|..|/*|*/*) return 1 ;;
  esac
  [ -n "$expected_file_identity" ] || return 1
  exec {marker_fd}<> "$relative_name" || return 1
  actual_file_identity=$(stat -Lc '%d:%i' -- "/proc/self/fd/$marker_fd") || status=1
  link_count=$(stat -Lc '%h' -- "/proc/self/fd/$marker_fd") || status=1
  [ "$status" -eq 0 ] && [ "$actual_file_identity" = "$expected_file_identity" ] && \
    [ "$link_count" = 1 ] || status=1
  if [ "$status" -eq 0 ]; then
    printf '%s\n' "$token" >&"$marker_fd" || status=$?
  fi
  exec {marker_fd}>&- || status=1
  return "$status"
}

relative_regular_file_identity() {
  local relative_name=$1
  case "$relative_name" in
    ""|.|..|/*|*/*) return 1 ;;
  esac
  [ -f "$relative_name" ] && [ ! -L "$relative_name" ] && \
    [ "$(stat -Lc '%h' -- "$relative_name")" = 1 ] || return 1
  stat -Lc '%d:%i' -- "$relative_name"
}

# Noclobber makes the relative log creation atomic: an existing regular file,
# symlink, or hard link is never opened. The caller writes only through the
# descriptor returned to its caller-selected variable.
open_new_relative_file_fd() {
  local relative_name=$1 output_variable=$2 new_fd noclobber_was_set=0
  case "$relative_name" in
    ""|.|..|/*|*/*) return 1 ;;
  esac
  [[ "$output_variable" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
  case $- in *C*) noclobber_was_set=1 ;; esac
  set -C
  if ! exec {new_fd}> "$relative_name"; then
    [ "$noclobber_was_set" -eq 1 ] || set +C
    return 1
  fi
  [ "$noclobber_was_set" -eq 1 ] || set +C
  printf -v "$output_variable" '%s' "$new_fd"
}

enter_pinned_dir() {
  local expected_physical_dir=$1 expected_identity=$2 resolved_dir
  cd -P -- "$expected_physical_dir" || return 1
  resolved_dir=$(pwd -P) || return 1
  [ "$resolved_dir" = "$expected_physical_dir" ] || return 1
  [ -n "$expected_identity" ] || return 1
  [ "$(stat -Lc '%d:%i' -- .)" = "$expected_identity" ] || return 1
}

close_inherited_pinning_fds() {
  if [ -n "${pinned_return_fd:-}" ]; then
    exec {pinned_return_fd}<&- || return 1
    pinned_return_fd=""
  fi
  if [ -n "${LOCK_GUARD_FD:-}" ]; then
    exec {LOCK_GUARD_FD}<&- || return 1
    LOCK_GUARD_FD=""
  fi
}

run_with_new_log() {
  local relative_name=$1 command_dir=$2 command_identity=$3 log_fd status close_status=0
  shift 3
  [ "$#" -gt 0 ] || return 125
  case "$1" in
    ""|.|..|/*|*/*) return 125 ;;
  esac
  open_new_relative_file_fd "$relative_name" log_fd || return 125
  enter_pinned_dir "$command_dir" "$command_identity" || status=125
  if [ "${status:-0}" -eq 0 ]; then
    (close_inherited_pinning_fds && exec "$@") >&"$log_fd" 2>&1
    status=$?
  fi
  exec {log_fd}>&- || close_status=$?
  [ "$status" -ne 0 ] || status=$close_status
  return "$status"
}

exec_with_new_log() {
  local relative_name=$1 command_dir=$2 command_identity=$3 log_fd
  shift 3
  [ "$#" -gt 0 ] || return 125
  case "$1" in
    ""|.|..|/*|*/*) return 125 ;;
  esac
  open_new_relative_file_fd "$relative_name" log_fd || return 125
  enter_pinned_dir "$command_dir" "$command_identity" || return 125
  close_inherited_pinning_fds || return 125
  exec "$@" >&"$log_fd" 2>&1
}

new_log_name() {
  local label=$1
  [[ "$label" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || return 1
  printf 'final-%s.%s.%s.%s.log\n' "$label" "$$" "$RANDOM" "$RANDOM"
}

# Every deletion is pinned to a physically entered, path- and identity-validated
# directory. Callers pass a file/tree mode and only relative entry names, so rm
# never resolves a path assembled from ROOT.
remove_in_pinned_dir() {
  local expected_physical_dir=$1 expected_identity=$2 delete_mode=$3
  local actual_identity relative_name resolved_dir
  shift 3
  [ "$#" -gt 0 ] || return 0
  (
    trap - EXIT
    cd -P -- "$expected_physical_dir" || return 1
    resolved_dir=$(pwd -P) || return 1
    [ "$resolved_dir" = "$expected_physical_dir" ] || return 1
    actual_identity=$(stat -Lc '%d:%i' -- .) || return 1
    [ -n "$expected_identity" ] && \
      [ "$actual_identity" = "$expected_identity" ] || return 1
    for relative_name in "$@"; do
      case "$relative_name" in
        ""|.|..|/*|*/*) return 1 ;;
      esac
    done
    case "$delete_mode" in
      file)
        for relative_name in "$@"; do
          [ ! -d "$relative_name" ] || return 1
        done
        rm -f -- "$@"
        ;;
      tree)
        rm -rf -- "$@"
        ;;
      *) return 1 ;;
    esac
  )
}

process_identity() {
  local pid=$1 stat_line stat_tail
  local -a stat_fields
  if ! [[ "$pid" =~ ^[1-9][0-9]*$ ]] || \
      ! IFS= read -r stat_line 2>&- < "/proc/$pid/stat"; then
    return 1
  fi
  stat_tail=${stat_line##*) }
  if [ "$stat_tail" = "$stat_line" ]; then
    return 1
  fi
  read -r -a stat_fields <<< "$stat_tail"
  if [ "${#stat_fields[@]}" -lt 20 ] || \
      ! [[ "${stat_fields[0]}" =~ ^[[:alpha:]]$ ]] || \
      ! [[ "${stat_fields[19]}" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  printf '%s %s\n' "${stat_fields[0]}" "${stat_fields[19]}"
}

# SIGKILL and host crashes bypass the EXIT trap. Remove only regular markers
# created by this script whose recorded owner is no longer running; every other
# untracked path remains visible to the clean-worktree gate below.
remove_stale_server_markers() {
  local current_identity current_start_ticks marker_name marker_pid
  local marker_start_ticks marker_token process_state resolved_site scanner_identity

  if ! (
    cd -P -- "$ROOT/site" || die "could not enter site/ before stale-marker cleanup"
    resolved_site=$(pwd -P) || \
      die "could not resolve site/ before stale-marker cleanup"
    [ "$resolved_site" = "$ROOT/site" ] || \
      die "site/ must resolve to its repository-local path before stale-marker cleanup (got $resolved_site)"
    scanner_identity=$(stat -Lc '%d:%i' -- .) || \
      die "could not identify site/ before stale-marker cleanup"
    if [ -n "${SITE_IDENTITY:-}" ] && \
        [ "$scanner_identity" != "$SITE_IDENTITY" ]; then
      die "site/ identity changed before stale-marker cleanup"
    fi
    for marker_name in .final-revalidation-server.??????; do
      if [ ! -f "$marker_name" ] || [ -L "$marker_name" ]; then
        continue
      fi
      if ! [[ "$marker_name" =~ ^\.final-revalidation-server\.[A-Za-z0-9]{6}$ ]]; then
        continue
      fi
      marker_token=$(<"$marker_name")
      marker_start_ticks=""
      if [[ "$marker_token" =~ ^final-revalidation:([[:xdigit:]]{40}|[[:xdigit:]]{64}):([0-9]+):([0-9]+):[0-9]+$ ]]; then
        marker_pid=${BASH_REMATCH[2]}
        marker_start_ticks=${BASH_REMATCH[3]}
      elif [[ "$marker_token" =~ ^final-revalidation:([[:xdigit:]]{40}|[[:xdigit:]]{64}):([0-9]+):[0-9]+$ ]]; then
        marker_pid=${BASH_REMATCH[2]}
      else
        continue
      fi
      if ! [[ "$marker_pid" =~ ^[1-9][0-9]*$ ]]; then
        continue
      fi
      if current_identity=$(process_identity "$marker_pid"); then
        read -r process_state current_start_ticks <<< "$current_identity"
        if [ "$process_state" != Z ] && [ "$process_state" != X ] && \
            [ "$process_state" != x ] && \
            { [ -z "$marker_start_ticks" ] || \
              [ "$current_start_ticks" = "$marker_start_ticks" ]; }; then
          continue
        fi
      elif kill -0 "$marker_pid" 2>&- || [ -d "/proc/$marker_pid" ]; then
        continue
      fi
      remove_in_pinned_dir "$ROOT/site" "$scanner_identity" file "$marker_name" || \
        die "could not remove stale local-server marker: site/$marker_name"
      printf 'removed stale local-server marker: site/%s\n' "$marker_name"
    done
  ); then
    die "stale local-server marker cleanup failed"
  fi
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
  remove_in_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" tree runs shots || \
    die "could not clear prior UAT evidence"
}

read_regular_relative_file() {
  local relative_name=$1 file_size
  case "$relative_name" in
    ""|.|..|/*|*/*) return 1 ;;
  esac
  [ -f "$relative_name" ] && [ ! -L "$relative_name" ] || return 1
  file_size=$(stat -Lc '%s' -- "$relative_name") || return 1
  [ "$file_size" -le 256 ] || return 1
  cat -- "$relative_name"
}

write_lock_owner() {
  local relative_name=$1 token=$2 owner_fd status=0
  open_new_relative_file_fd "$relative_name" owner_fd || return 1
  printf '%s\n' "$token" >&"$owner_fd" || status=$?
  exec {owner_fd}>&- || status=1
  return "$status"
}

open_pinned_directory_fd() {
  local expected_physical_dir=$1 expected_identity=$2 output_variable=$3
  local actual_identity directory_fd
  [[ "$output_variable" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
  [ -n "$expected_identity" ] || return 1
  exec {directory_fd}< "$expected_physical_dir" || return 1
  actual_identity=$(stat -Lc '%d:%i' -- "/proc/self/fd/$directory_fd") || {
    exec {directory_fd}<&-
    return 1
  }
  if [ "$(stat -Lc '%F' -- "/proc/self/fd/$directory_fd")" != directory ] || \
      [ "$actual_identity" != "$expected_identity" ]; then
    exec {directory_fd}<&-
    return 1
  fi
  printf -v "$output_variable" '%s' "$directory_fd"
}

acquire_revalidation_lock() {
  local attempt current_identity current_start_ticks existing_identity
  local existing_token flock_command lock_owner_pid lock_owner_start_ticks process_state

  current_identity=$(process_identity "$$") || \
    die "could not read this revalidation process identity for the run lock"
  read -r _ current_start_ticks <<< "$current_identity"
  LOCK_TOKEN="final-revalidation:$SHA:$$:$current_start_ticks:$RANDOM"

  flock_command=$(command -v flock) || die "flock is required for the run lock"
  [ -n "$flock_command" ] || die "flock is required for the run lock"
  open_pinned_directory_fd "$ROOT" "$ROOT_IDENTITY" LOCK_GUARD_FD || \
    die "could not pin the repository root for the final-revalidation run lock"
  if ! flock -n "$LOCK_GUARD_FD"; then
    exec {LOCK_GUARD_FD}<&-
    LOCK_GUARD_FD=""
    die "another final revalidation run owns build/uat/"
  fi

  for attempt in 1 2; do
    if with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" mkdir -- "$LOCK_NAME"; then
      LOCK_IDENTITY=$(pinned_directory_identity "$BUILD_UAT/$LOCK_NAME") || \
        die "could not identify the newly acquired final-revalidation lock"
      LOCK_HELD=1
      LOCK_INITIALIZING=1
      if ! with_pinned_dir "$BUILD_UAT/$LOCK_NAME" "$LOCK_IDENTITY" \
          write_lock_owner owner "$LOCK_TOKEN"; then
        die "could not initialize the final-revalidation run lock"
      fi
      LOCK_INITIALIZING=0
      return 0
    fi

    existing_identity=$(pinned_directory_identity "$BUILD_UAT/$LOCK_NAME") || \
      die "another final revalidation run owns build/uat/ (lock is unsafe or initializing)"
    existing_token=$(with_pinned_dir "$BUILD_UAT/$LOCK_NAME" "$existing_identity" \
      read_regular_relative_file owner) || \
      die "another final revalidation run owns build/uat/ (lock owner is missing or malformed)"
    if ! [[ "$existing_token" =~ ^final-revalidation:([[:xdigit:]]{40}|[[:xdigit:]]{64}):([0-9]+):([0-9]+):[0-9]+$ ]]; then
      die "another final revalidation run owns build/uat/ (lock owner is malformed)"
    fi
    lock_owner_pid=${BASH_REMATCH[2]}
    lock_owner_start_ticks=${BASH_REMATCH[3]}
    if ! [[ "$lock_owner_pid" =~ ^[1-9][0-9]*$ ]]; then
      die "another final revalidation run owns build/uat/ (lock owner is malformed)"
    fi
    if current_identity=$(process_identity "$lock_owner_pid"); then
      read -r process_state current_start_ticks <<< "$current_identity"
      if [ "$process_state" != Z ] && [ "$process_state" != X ] && \
          [ "$process_state" != x ] && \
          [ "$current_start_ticks" = "$lock_owner_start_ticks" ]; then
        die "another final revalidation run owns build/uat/ (pid $lock_owner_pid)"
      fi
    elif kill -0 "$lock_owner_pid" 2>&- || [ -d "/proc/$lock_owner_pid" ]; then
      die "another final revalidation run owns build/uat/ (pid $lock_owner_pid identity is unreadable)"
    fi
    remove_in_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" tree "$LOCK_NAME" || \
      die "could not recover the stale final-revalidation lock"
    printf 'removed stale final-revalidation lock from build/uat/\n'
  done
  die "could not acquire the final-revalidation run lock"
}

release_revalidation_lock() {
  local current_identity current_token
  [ "${LOCK_HELD:-0}" -eq 1 ] || return 0
  current_identity=$(pinned_directory_identity "$BUILD_UAT/$LOCK_NAME") || return 1
  [ -n "$LOCK_IDENTITY" ] && [ "$current_identity" = "$LOCK_IDENTITY" ] || return 1
  if [ "${LOCK_INITIALIZING:-0}" -ne 1 ]; then
    current_token=$(with_pinned_dir "$BUILD_UAT/$LOCK_NAME" "$LOCK_IDENTITY" \
      read_regular_relative_file owner) || return 1
    [ -n "$LOCK_TOKEN" ] && [ "$current_token" = "$LOCK_TOKEN" ] || return 1
  fi
  remove_in_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" tree "$LOCK_NAME" || return 1
  LOCK_HELD=0
  LOCK_INITIALIZING=0
  if [ -n "${LOCK_GUARD_FD:-}" ]; then
    flock -u "$LOCK_GUARD_FD" || return 1
    exec {LOCK_GUARD_FD}<&- || return 1
    LOCK_GUARD_FD=""
  fi
}

record_status() {
  if [ "$1" -ne 0 ]; then
    REVALIDATION_STATUS=1
  fi
}

cleanup() {
  local marker_name=${MARKER_NAME:-} status=${1:-0}
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>&- || true
    wait "$SERVER_PID" 2>&- || true
  fi
  if [ -z "$marker_name" ] && [ -n "${MARKER_PATH:-}" ]; then
    marker_name=${MARKER_PATH##*/}
  fi
  if [ -n "$marker_name" ] && \
      { ! [[ "$marker_name" =~ ^\.final-revalidation-server\.[A-Za-z0-9]{6}$ ]] || \
        { [ -n "${MARKER_PATH:-}" ] && \
          [ "$MARKER_PATH" != "$ROOT/site/$marker_name" ]; }; }; then
    printf 'ERROR: active local-server marker path is invalid\n' >&2
    status=1
  elif [ -n "$marker_name" ] && \
      ! remove_in_pinned_dir "$ROOT/site" "${SITE_IDENTITY:-}" file "$marker_name"; then
    printf 'ERROR: could not remove active local-server marker: site/%s\n' \
      "$marker_name" >&2
    status=1
  fi
  if ! restore_build_stamp; then
    status=1
  fi
  if [ "${LOCK_HELD:-0}" -eq 1 ] && ! release_revalidation_lock; then
    printf 'ERROR: could not release final-revalidation run lock\n' >&2
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

finalize_revalidation() {
  local cleanup_status
  cleanup "$REVALIDATION_STATUS"
  cleanup_status=$?
  trap - EXIT
  record_status "$cleanup_status"
  git status --short | head -5
  printf 'revalidation done @ %s\n' "$SHA"
  return "$REVALIDATION_STATUS"
}

SITE_IDENTITY=$(pinned_directory_identity "$ROOT/site") || \
  die "site/ must be a physically entered repository-local directory"
remove_stale_server_markers
require_clean_worktree "tracked or untracked changes found; run from a clean worktree at the SHA under test"

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  die "LOCAL_PORT must be an integer from 1 through 65535"
fi
PORT=$((10#$PORT))
if ((PORT < 1 || 65535 < PORT)); then
  die "LOCAL_PORT must be an integer from 1 through 65535"
fi

# Create each directory from its pinned physical parent, then capture the child
# identity that protects every later write and deletion.
ROOT_IDENTITY=$(pinned_directory_identity "$ROOT") || die "could not identify repository root"
with_pinned_dir "$ROOT" "$ROOT_IDENTITY" mkdir -p -- build || die "could not create build directory"
BUILD_IDENTITY=$(pinned_directory_identity "$ROOT/build") || \
  die "the build directory must be physically entered and repository-local"
with_pinned_dir "$ROOT/build" "$BUILD_IDENTITY" mkdir -p -- uat || \
  die "could not create build/uat/"
BUILD_UAT_IDENTITY=$(pinned_directory_identity "$BUILD_UAT") || \
  die "could not identify build/uat/"
BUILD_STAMP_DIR_IDENTITY=$(pinned_directory_identity "$BUILD_STAMP_DIR") || \
  die "could not identify site/platform/data/"

SHA=$(git rev-parse HEAD) || die "could not resolve HEAD"

trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

acquire_revalidation_lock
clear_prior_evidence
printf 'revalidation @ %s in %s\n' "$SHA" "$ROOT"

MARKER_NAME=$(with_pinned_dir "$ROOT/site" "$SITE_IDENTITY" mktemp .final-revalidation-server.XXXXXX) || \
  die "could not create local-server identity marker"
MARKER_PATH="$ROOT/site/$MARKER_NAME"
if ! [[ "$MARKER_NAME" =~ ^\.final-revalidation-server\.[A-Za-z0-9]{6}$ ]]; then
  die "local-server identity marker has an invalid basename"
fi
MARKER_IDENTITY=$(process_identity "$$") || \
  die "could not read this revalidation process identity"
read -r _ MARKER_START_TICKS <<< "$MARKER_IDENTITY"
MARKER_TOKEN="final-revalidation:$SHA:$$:$MARKER_START_TICKS:$RANDOM"
MARKER_FILE_IDENTITY=$(with_pinned_dir "$ROOT/site" "$SITE_IDENTITY" \
  relative_regular_file_identity "$MARKER_NAME") || \
  die "could not identify local-server identity marker"
with_pinned_dir "$ROOT/site" "$SITE_IDENTITY" write_server_marker \
  "$MARKER_NAME" "$MARKER_FILE_IDENTITY" "$MARKER_TOKEN" || \
  die "could not write local-server identity marker"
MARKER_URL="http://127.0.0.1:$PORT/$MARKER_NAME"

server_matches_worktree() {
  [ "$(curl -fsS --max-time 2 "$MARKER_URL" 2>&-)" = "$MARKER_TOKEN" ]
}

CURL_COMMAND=$(command -v curl) || die "curl is required to verify the local server"
[ -n "$CURL_COMMAND" ] || die "curl is required to verify the local server"
if server_matches_worktree; then
  printf 'reusing this worktree\047s site/ server on port %s\n' "$PORT"
else
  SERVER_LOG_NAME=$(new_log_name local-server) || die "could not choose local-server log name"
  with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" exec_with_new_log \
    "$SERVER_LOG_NAME" "$ROOT/site" "$SITE_IDENTITY" \
    python3 -m http.server "$PORT" --bind 127.0.0.1 --directory . &
  SERVER_PID=$!
  printf 'local-server log: build/uat/%s\n' "$SERVER_LOG_NAME"
  server_ready=0
  for _ in {1..20}; do
    if server_matches_worktree; then
      server_ready=1
      break
    fi
    if ! kill -0 "$SERVER_PID" 2>&-; then
      wait "$SERVER_PID" 2>&- || true
      die "LOCAL_PORT $PORT is occupied or the local server failed; see build/uat/$SERVER_LOG_NAME"
    fi
    sleep 0.25
  done
  if [ "$server_ready" -ne 1 ]; then
    die "local static server did not become ready; see build/uat/$SERVER_LOG_NAME"
  fi
fi

run_generator() {
  local label=$1
  shift
  if ! (
    enter_pinned_dir "$ROOT" "$ROOT_IDENTITY" &&
      close_inherited_pinning_fds &&
      exec "$@"
  ); then
    die "generator failed: $label"
  fi
}

BUILD_STAMP_DIRTY=1
run_generator "site build" python3 tools/build_site.py --check
run_generator "instructor bundle" python3 tools/build_instructor_bundle.py
run_generator "editor data bundle" node app/worker/scripts/bundle-editor-data.mjs
require_clean_worktree "generators changed tracked or untracked files; revalidation would no longer describe one SHA" \
  ":(top,exclude,literal)site/$MARKER_NAME" \
  ":(top,exclude,literal)site/platform/data/.build-stamp.json"

run() {
  local label=$1
  local log_name status
  shift
  printf '===== %s =====\n' "$label"
  log_name=$(new_log_name "$label") || die "could not choose log name for $label"
  with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" run_with_new_log \
    "$log_name" "$ROOT" "$ROOT_IDENTITY" node tools/verify_persona_journeys.js "$@"
  status=$?
  if [ "$status" -eq 125 ]; then
    die "could not create fresh log for $label"
  fi
  record_status "$status"
  printf 'exit=%s\n' "$status"
  printf 'log=build/uat/%s\n' "$log_name"
  with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" \
    grep -E '^(JOURNEY SUMMARY|RUN FILE)' "$log_name" || true
  # Deliberate canaries prove that the runner can fail; omit only their expected
  # noise from this excerpt. See docs/solutions/uat/2026-09-02-browser-journeys-measure-the-wrong-thing.md.
  with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" \
    grep -E '^(FAIL|ERROR|BLOCKED)' "$log_name" \
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
A11Y_LOG_NAME=$(new_log_name a11y) || die "could not choose accessibility log name"
with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" run_with_new_log \
  "$A11Y_LOG_NAME" "$ROOT" "$ROOT_IDENTITY" node tools/a11y_audit.js \
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
  "${PROD_BASE}/cost-per-credit.html"
a11y_status=$?
if [ "$a11y_status" -eq 125 ]; then
  die "could not create fresh accessibility log"
fi
record_status "$a11y_status"
printf 'a11y exit=%s\n' "$a11y_status"
printf 'a11y log=build/uat/%s\n' "$A11Y_LOG_NAME"
with_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" \
  grep -E '^=== |A11Y AUDIT' "$A11Y_LOG_NAME" || true

finalize_revalidation
exit $?
