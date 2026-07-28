#!/usr/bin/env bash
# ============================================================================
# preflight.sh — every gate this project has, in one command.
#
# WHY: the gates existed but lived in a session's head. On 2026-07-27 the
# STANDARD / LARGE TYPE toggle was found sitting at 1.06:1 contrast — cream on
# cream, invisible, shipped and unnoticed — and the accessibility audit written
# in response was itself left unwired, which is the same mistake one level up.
# A check nobody runs is a check that does not exist.
#
# Two of these gates need a real browser (Chromium via Xwayland), so they cannot
# live inside build_site.py --check. They live here instead, and this script is
# what a session runs before shipping or handing off.
#
# Usage:
#   bash tools/preflight.sh              # everything (browser gates included)
#   bash tools/preflight.sh --no-browser # skip the browser gates (CI, no display)
#   TARGET_URL=… bash tools/preflight.sh # also check rail placement on a live page
#
# Exit 0 only if every gate that ran passed.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/.."

WANT_BROWSER=1
[ "${1:-}" = "--no-browser" ] && WANT_BROWSER=0

pass=0; fail=0; skipped=0
results=()

run() {  # run <name> <command…>
  local name="$1"; shift
  printf '\n\033[1m── %s\033[0m\n' "$name"
  if "$@"; then
    results+=("PASS  $name"); pass=$((pass+1))
  else
    results+=("FAIL  $name"); fail=$((fail+1))
  fi
}
skip() { results+=("SKIP  $1 — $2"); skipped=$((skipped+1)); printf '\n\033[2m── %s (skipped: %s)\033[0m\n' "$1" "$2"; }

# ---- headless gates --------------------------------------------------------
run "spine integrity (validate_spine)"      python3 tools/validate_spine.py
run "site build + link/leak sweeps"         python3 tools/build_site.py --check
run "bundle parity"                         python3 tools/check_build_parity.py
run "python unit tests"                     python3 -m pytest tools/tests/ -q
run "worker unit tests"                     bash -c 'cd app/worker && node --test test/*.test.js >/dev/null 2>&1'
run "offline red-team probe"                bash -c 'node tools/offline_redteam_probe.mjs | grep -q "0/8" || node tools/offline_redteam_probe.mjs | tail -3'

# ---- browser gates ---------------------------------------------------------
# Chromium here needs Xwayland, which is auth-gated by a mutter cookie whose
# filename regenerates on every login — DISPLAY alone is not enough, and that is
# why screenshot verification was quietly broken for weeks.
if [ "$WANT_BROWSER" = "1" ]; then
  export DISPLAY="${DISPLAY:-:0}"
  if [ -z "${XAUTHORITY:-}" ] || [ ! -r "${XAUTHORITY:-}" ]; then
    cookie=$(ls -t "/run/user/$(id -u)"/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
    [ -n "$cookie" ] && export XAUTHORITY="$cookie"
  fi
  if xdpyinfo >/dev/null 2>&1; then
    # verify-editor exits nonzero on any failed assertion and prints an
    # "N/N PASS" summary — trust the exit code, never a hardcoded count (the
    # literal "43/43" grep silently turned every added assertion into a FAIL).
    run "editor client (headful)" bash -c 'node app/editor/verify-editor.js | grep -E "ASSERTION SUMMARY|FAIL " ; exit "${PIPESTATUS[0]}"'
    run "accessibility audit (0 FAIL required)"  node tools/a11y_audit.js
    # ALWAYS runs. It used to be skipped unless TARGET_URL named an /edit URL with
    # a ?t= token — which meant that once the Access door retires those tokens
    # (plan KD1) the gate could never run again and would sit permanently
    # "SKIP", a gate that had quietly stopped being a gate. It needs no
    # credential: verify-rail-placement.js defaults to the same local
    # test-harness.html the 43-assertion editor-client gate above drives, and the
    # property it proves is geometric (the rail's box never intersects its
    # block's box at ten widths), which the harness reproduces faithfully.
    # Setting TARGET_URL still upgrades it to a real editor page.
    if [ -n "${TARGET_URL:-}" ]; then
      run "rail placement (live page)"           node app/editor/verify-rail-placement.js
    else
      run "rail placement (harness)"             node app/editor/verify-rail-placement.js
    fi
  else
    skip "editor client"        "no reachable X display"
    skip "accessibility audit"  "no reachable X display"
    skip "rail placement"       "no reachable X display"
  fi
else
  skip "editor client"        "--no-browser"
  skip "accessibility audit"  "--no-browser"
  skip "rail placement"       "--no-browser"
fi

# ---- summary ---------------------------------------------------------------
printf '\n\033[1m══ PREFLIGHT ══\033[0m\n'
for r in "${results[@]}"; do
  case "$r" in
    PASS*) printf '  \033[32m%s\033[0m\n' "$r" ;;
    FAIL*) printf '  \033[31m%s\033[0m\n' "$r" ;;
    *)     printf '  \033[2m%s\033[0m\n' "$r" ;;
  esac
done
printf '\n  %d passed, %d failed, %d skipped\n\n' "$pass" "$fail" "$skipped"
[ "$fail" -eq 0 ]
