#!/usr/bin/env python3
r"""spine_stamp.py — the deterministic `spine_build_id` and its traceability sha.

`spine_build_id` is the machine-checkable "which version of the data spine did
this build come from" fingerprint. It is emitted by ALL THREE builds:

  * tools/build_site.py               -> site/platform/data/.build-stamp.json
                                         + <meta name="spine-build"> on every page
  * tools/build_worker_personas.py    -> personas.generated.json  (spine_build_id)
  * tools/build_instructor_bundle.py  -> instructor-bundle.generated.json

and asserted equal by tools/check_build_parity.py. If any two disagree, one
bundle is stale relative to another and the apply loop must abort (a suggestion
resolved against a stale map could corrupt the wrong source span).

Definition (frozen):

    spine_build_id = sha256(
        spine_version + "\n"
        + "\n".join(sorted(  f"{relpath}:{sha256(file_bytes)}"
                             for every regular file under data/ ))
    )

  * `spine_version` comes from data/spine-manifest.json ("spine_version").
  * `relpath` is POSIX-style, relative to the data/ directory, so the id is
    stable across checkouts and OSes.
  * Content only — mtimes, ownership, and path outside data/ are irrelevant.
  * `git_base_sha()` is provided for traceability in .build-stamp.json but is
    DELIBERATELY NOT part of the equality hash (a clean rebuild from an identical
    tree on a different commit must still parity-match).

Python 3, stdlib only. Deterministic and idempotent.
"""

import hashlib
import json
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DATA = os.path.join(_REPO_ROOT, "data")
_MANIFEST_NAME = "spine-manifest.json"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_data_files(data_dir):
    """Yield (posix_relpath, abspath) for every regular file under data_dir,
    deterministically. Skips nothing content-bearing; ignores VCS/OS cruft that
    could vary between machines."""
    skip_dirs = {".git", "__pycache__", ".ipynb_checkpoints"}
    skip_names = {".DS_Store"}
    for root, dirs, files in os.walk(data_dir):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
        for fn in sorted(files):
            if fn in skip_names or fn.endswith(".pyc"):
                continue
            abspath = os.path.join(root, fn)
            if not os.path.isfile(abspath):
                continue
            rel = os.path.relpath(abspath, data_dir).replace(os.sep, "/")
            yield rel, abspath


def _spine_version(data_dir):
    manifest = os.path.join(data_dir, _MANIFEST_NAME)
    try:
        with open(manifest, "r", encoding="utf-8") as fh:
            return json.load(fh).get("spine_version", "")
    except (OSError, json.JSONDecodeError):
        return ""


def compute(data_dir=_DEFAULT_DATA):
    """Return the `spine_build_id` (hex sha256) for the data spine at `data_dir`.

    Equality across bundles is the parity gate. Depends ONLY on spine_version +
    the content of every file under data/. Not on git, mtime, or path prefix.
    """
    version = _spine_version(data_dir)
    lines = [
        "%s:%s" % (rel, _sha256_file(abspath))
        for rel, abspath in _iter_data_files(data_dir)
    ]
    lines.sort()
    blob = version + "\n" + "\n".join(lines)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Back-compat / intent-revealing alias: the importable "spine_build_id" function.
def spine_build_id(data_dir=_DEFAULT_DATA):
    """Alias for compute() — the canonical name callers import."""
    return compute(data_dir)


def git_base_sha(repo_dir=_REPO_ROOT):
    """Current HEAD commit sha for TRACEABILITY only (never hashed into the id).
    Returns the 40-char sha, or None if git is unavailable / not a repo."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        sha = out.stdout.strip()
        return sha or None
    except (subprocess.SubprocessError, OSError):
        return None


if __name__ == "__main__":
    print("spine_build_id : %s" % compute())
    print("git_base_sha   : %s" % (git_base_sha() or "(unavailable)"))
