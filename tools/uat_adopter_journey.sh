#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="https://github.com/damienriehl/sonsteng-magnum-opus.git"
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REVIEW_SHA="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
MINIMAL_PATH="/usr/local/bin:/usr/bin:/bin"
TEMP_ROOT="$(mktemp -d)"
TEMP_HOME="$TEMP_ROOT/home"
CLONE_ROOT="$TEMP_ROOT/sonsteng-magnum-opus"
SERVER_PID=""

mkdir -p "$TEMP_HOME"

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf -- "$TEMP_ROOT"
}
trap cleanup EXIT INT TERM

usage() {
  printf '%s\n' "Usage: bash tools/uat_adopter_journey.sh <clone-serve|worker-tests|byok-boundary|worker-dry-run|validate-build>"
}

clone_reviewed_commit() {
  env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" \
    git clone --filter=blob:none --no-checkout "$REPOSITORY_URL" "$CLONE_ROOT"
  env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" \
    git -C "$CLONE_ROOT" checkout --detach "$REVIEW_SHA"
  local checked_out
  checked_out="$(env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" git -C "$CLONE_ROOT" rev-parse HEAD)"
  if [[ "$checked_out" != "$REVIEW_SHA" ]]; then
    printf 'checkout mismatch: expected %s, got %s\n' "$REVIEW_SHA" "$checked_out" >&2
    return 1
  fi
  printf 'reviewed checkout verified: %s\n' "$checked_out"
}

choose_port() {
  env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" python3 - <<'PY'
import random
import socket

for port in random.SystemRandom().sample(range(8700, 10000), 1300):
    try:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", port))
        print(port)
        raise SystemExit(0)
    except OSError:
        pass
raise SystemExit("no free port in 8700-9999")
PY
}

start_static_server() {
  local port="$1"
  env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" \
    python3 -m http.server "$port" --bind 127.0.0.1 --directory "$CLONE_ROOT/site" \
    >"$TEMP_ROOT/http-server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 50); do
    if env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" \
      curl --fail --silent --show-error --max-time 2 "http://127.0.0.1:$port/platform/" >/dev/null; then
      return 0
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  printf 'static server did not become ready on port %s\n' "$port" >&2
  sed -n '1,80p' "$TEMP_ROOT/http-server.log" >&2 || true
  return 1
}

stop_static_server() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}

run_clone_serve() {
  local port page
  port="$(choose_port)"
  start_static_server "$port"
  page="$(env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" \
    curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/platform/")"
  grep -Fq '<main' <<<"$page"
  stop_static_server
  printf 'static site served successfully on disposable port %s\n' "$port"
}

run_worker_tests() {
  local major
  major="$(env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" node -p 'Number(process.versions.node.split(".")[0])')"
  if (( major < 20 )); then
    printf 'Node 20 or newer is required; found major version %s\n' "$major" >&2
    return 1
  fi
  (
    cd "$CLONE_ROOT/app/worker"
    env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" node --test test/*.test.js
  )
}

run_byok_boundary() {
  local port page script
  port="$(choose_port)"
  start_static_server "$port"
  page="$(env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" curl --fail --silent --show-error --max-time 5 \
    "http://127.0.0.1:$port/platform/chat/index.html?matter=m05&persona=m05.per.halvard")"
  script="$(env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" curl --fail --silent --show-error --max-time 5 \
    "http://127.0.0.1:$port/platform/chat/byok.js")"
  stop_static_server
  grep -Fq 'byok.js' <<<"$page"
  grep -Fq 'ADD YOUR KEY' <<<"$script"
  grep -Fq 'Provider' <<<"$script"
  grep -Fq 'API key' <<<"$script"
  grep -Fq 'Model override (optional)' <<<"$script"
  grep -Fq 'never stores it' <<<"$script"
  printf '%s\n' 'BYOK controls and no-server-storage boundary verified without entering a credential'
}

run_worker_dry_run() {
  (
    cd "$CLONE_ROOT/app/worker"
    env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" npx wrangler@4 deploy --dry-run
  )
  if [[ -e "$TEMP_HOME/.wrangler" || -e "$TEMP_HOME/.config/.wrangler" ]]; then
    printf '%s\n' 'Wrangler wrote account configuration into the temporary HOME' >&2
    return 1
  fi
  printf '%s\n' 'Worker dry-run completed without login or deployment'
}

run_validate_build() {
  (
    cd "$CLONE_ROOT"
    env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" python3 tools/validate_spine.py
    env -i HOME="$TEMP_HOME" PATH="$MINIMAL_PATH" python3 tools/build_site.py --check
  )
}

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

case "$1" in
  clone-serve|worker-tests|byok-boundary|worker-dry-run|validate-build) ;;
  *) usage >&2; exit 2 ;;
esac

clone_reviewed_commit
case "$1" in
  clone-serve) run_clone_serve ;;
  worker-tests) run_worker_tests ;;
  byok-boundary) run_byok_boundary ;;
  worker-dry-run) run_worker_dry_run ;;
  validate-build) run_validate_build ;;
esac
