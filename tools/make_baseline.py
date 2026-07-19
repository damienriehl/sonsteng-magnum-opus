#!/usr/bin/env python3
r"""make_baseline.py — create a named baseline for the redline History browser.

A baseline is an ANNOTATED git tag named ``baseline-<name>`` pointing at the
current HEAD (e.g. ``baseline-walkthrough-2026-07-23`` cut just before the
John/Roger walkthrough). tools/build_history.py surfaces every ``baseline-*`` tag
as a comparison anchor: each revision can be redlined against any baseline, and
the cumulative "baseline → current" redline shows everything that changed since.

This tool ONLY creates the tag and regenerates the history bundle. It does NOT
push (the daemon lane owns publish) and it never rewrites history.

Usage:
  python3 tools/make_baseline.py <name> [-m "message"] [--at <ref>] [--no-regen]
  python3 tools/make_baseline.py --list

Examples:
  python3 tools/make_baseline.py walkthrough-2026-07-23 \
      -m "State before John & Roger walkthrough"
  python3 tools/make_baseline.py --list

NOTE (do NOT run at build time in CI): creating a tag is a deliberate editorial
act. The plan documents cutting ``baseline-walkthrough-2026-07-23`` before the
walkthrough; this session does not create any tag.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# baseline-<name>: name is [a-z0-9-] (a slug + optional ISO date), 1..64 chars.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True,
                          text=True, check=check)


def list_baselines() -> int:
    cp = _git("for-each-ref", "--sort=-creatordate",
              "--format=%(refname:short)  %(creatordate:short)  %(contents:subject)",
              "refs/tags/baseline-*", check=False)
    out = cp.stdout.strip()
    print(out if out else "no baselines yet (create one with make_baseline.py <name>)")
    return 0


def make_baseline(name: str, message: str, at: str, regen: bool) -> int:
    if not _NAME_RE.match(name):
        print(f"make_baseline: invalid name {name!r} — use lower-case [a-z0-9-].",
              file=sys.stderr)
        return 2
    tag = f"baseline-{name}"

    exists = _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}",
                  check=False).returncode == 0
    if exists:
        print(f"make_baseline: {tag} already exists — refusing to move it.",
              file=sys.stderr)
        return 2

    target = _git("rev-parse", "--verify", "--quiet", at, check=False).stdout.strip()
    if not target:
        print(f"make_baseline: ref {at!r} not found.", file=sys.stderr)
        return 2

    msg = message or f"Baseline {name}"
    cp = _git("tag", "-a", tag, target, "-m", msg, check=False)
    if cp.returncode != 0:
        print("make_baseline: git tag failed:\n" + cp.stderr, file=sys.stderr)
        return 1
    print(f"created annotated tag {tag} -> {target[:8]}  ({msg})")
    print("NOTE: not pushed. The daemon lane owns publish; push with your normal flow.")

    if regen:
        import build_history
        sys.path.insert(0, HERE)
        rc = build_history.main([])
        if rc != 0:
            print("make_baseline: history regen reported a problem "
                  "(see above).", file=sys.stderr)
            return rc
    return 0


def main(argv: list) -> int:
    if "--list" in argv:
        return list_baselines()
    positional = [a for a in argv if not a.startswith("-")]
    if not positional:
        print(__doc__)
        return 2
    name = positional[0]
    message = ""
    if "-m" in argv:
        i = argv.index("-m")
        if i + 1 < len(argv):
            message = argv[i + 1]
    at = "HEAD"
    if "--at" in argv:
        i = argv.index("--at")
        if i + 1 < len(argv):
            at = argv[i + 1]
    regen = "--no-regen" not in argv
    return make_baseline(name, message, at, regen)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
