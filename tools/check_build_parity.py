#!/usr/bin/env python3
r"""check_build_parity.py — the two-bundle staleness gate (CI / manual backstop).

Asserts that the four independently-computed spine_build_id values agree:

    spine_stamp.compute(data/)                       (ground truth)
    site/platform/data/.build-stamp.json             (the public site build)
    app/worker/personas/personas.generated.json      (the persona/chat bundle)
    build/instructor-bundle.generated.json           (the instructor bundle)

If any disagree, a bundle is stale relative to the live data spine (or to another
bundle) — the apply loop must NOT run against a mismatched map. Exits non-zero on
ANY mismatch or missing artifact; exits 0 only when all four match.

Run AFTER a full build of all three generators:
    python3 tools/build_site.py --check
    python3 tools/build_worker_personas.py
    python3 tools/build_instructor_bundle.py
    python3 tools/check_build_parity.py

Python 3, stdlib only.
"""

import json
import os
import sys

import spine_stamp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

ARTIFACTS = [
    ("site .build-stamp", os.path.join(REPO_ROOT, "site", "platform", "data", ".build-stamp.json")),
    ("persona bundle", os.path.join(REPO_ROOT, "app", "worker", "personas", "personas.generated.json")),
    ("instructor bundle", os.path.join(REPO_ROOT, "build", "instructor-bundle.generated.json")),
    ("editor map", os.path.join(REPO_ROOT, "build", "editor-map.generated.json")),
]


def _read_build_id(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("spine_build_id"), None
    except FileNotFoundError:
        return None, "missing (run its generator)"
    except (OSError, json.JSONDecodeError) as exc:
        return None, "unreadable: %s" % exc


def main():
    truth = spine_stamp.compute(DATA_DIR)
    print("spine_stamp.compute(data/) : %s" % truth)

    ok = True
    for label, path in ARTIFACTS:
        build_id, err = _read_build_id(path)
        rel = os.path.relpath(path, REPO_ROOT)
        if err:
            print("  MISMATCH  %-18s %s (%s)" % (label, err, rel))
            ok = False
            continue
        status = "OK      " if build_id == truth else "MISMATCH"
        if build_id != truth:
            ok = False
        shown = (build_id[:16] if build_id else "(none)")
        print("  %s  %-18s %s  %s" % (status, label, shown, rel))

    if ok:
        print("PARITY: PASS — all bundles share spine_build_id %s" % truth[:16])
        return 0
    print("PARITY: FAIL — a bundle is stale; rebuild all three before apply.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
