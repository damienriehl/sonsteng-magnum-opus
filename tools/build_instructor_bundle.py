#!/usr/bin/env python3
r"""build_instructor_bundle.py — the SERVER-ONLY instructor materials bundle.

Pre-renders the back-of-house instructor documents that the Worker's
`/edit/instructor/<matter>/<doc>` route serves for editing:

    data/matters/<slug>/facts.md                 (doc_type "facts")
    data/matters/<slug>/exercise/instructor-notes.md  (doc_type "instructor_notes")
    data/matters/<slug>/exercise/answer-key.md   (doc_type "answer_key")

Each is rendered to HTML with build_site.py's own markdown renderer (one shared
markdown implementation) AND carries its own editor-map blocks (same shape as
editor-map.generated.json), so the instructor view is editable through the very
same round-trip contract as the public prose.

Output: build/instructor-bundle.generated.json  (stamped with spine_build_id).

CRITICAL — this bundle contains ANSWER KEYS and concealed instructor content. It
is SERVER-ONLY and must NEVER land in:
    * site/platform/            (the public static build), or
    * personas.generated.json   (the persona/chat bundle).
A self-check at the end asserts the output path is outside site/platform/ and
refuses to run if it isn't. The public leak-sweep (build_site.py) independently
proves none of this text reaches the static site.

Python 3, stdlib only. Idempotent.

Usage:  python3 tools/build_instructor_bundle.py
"""

import glob
import json
import os
import sys

import build_site as bs        # reuse markdown renderer + editor-map walker
import spine_stamp
import text_norm

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
MATTERS_DIR = os.path.join(DATA_DIR, "matters")
SITE_PLATFORM = os.path.join(REPO_ROOT, "site", "platform")
OUT_PATH = os.path.join(REPO_ROOT, "build", "instructor-bundle.generated.json")

# (relative path under the matter dir, doc_type). The Worker keys on doc_type.
INSTRUCTOR_DOCS = [
    ("facts.md", "facts"),
    (os.path.join("exercise", "instructor-notes.md"), "instructor_notes"),
    (os.path.join("exercise", "answer-key.md"), "answer_key"),
]


def _matter_id(slug_dir):
    """Prefer matter.json's id (e.g. 'm07'); fall back to the slug prefix."""
    mpath = os.path.join(slug_dir, "matter.json")
    try:
        with open(mpath, "r", encoding="utf-8") as fh:
            mid = json.load(fh).get("id")
            if mid:
                return mid
    except (OSError, json.JSONDecodeError):
        pass
    return os.path.basename(slug_dir).split("-", 1)[0]


def _render_doc(matter_dir, relfile):
    """Render one instructor markdown doc -> (html, blocks). Uses build_site's
    EDMAP recorder + block walker, so blocks match the public editor-map shape
    (index within THIS doc's document order)."""
    path = os.path.join(matter_dir, relfile)
    if not os.path.isfile(path):
        return None, []
    with open(path, "r", encoding="utf-8") as fh:
        md = fh.read()

    src = bs.data_relpath(matter_dir, relfile)
    # Render with recording on; walk the produced fragment for indices.
    bs.EDMAP.enabled = True
    html = bs.markdown(md, src=src)
    entries, _ = bs._extract_page_blocks("<main>" + html + "</main>")
    # Build final block records (join rendered_hash already computed by walker).
    return html, entries


def main():
    # Fail-closed guard: this bundle must never be written under the public site.
    out_abs = os.path.abspath(OUT_PATH)
    if os.path.commonpath([out_abs, os.path.abspath(SITE_PLATFORM)]) == os.path.abspath(SITE_PLATFORM):
        sys.stderr.write(
            "FATAL: instructor bundle output path is inside site/platform/ — refusing "
            "to write answer-key content into the public build.\n")
        return 2

    bs.EDMAP.reset()
    bs.EDMAP.enabled = True

    docs = []
    counts = {}
    missing = []
    for slug_dir in sorted(glob.glob(os.path.join(MATTERS_DIR, "m*-*"))):
        if not os.path.isdir(slug_dir):
            continue
        mid = _matter_id(slug_dir)
        for relfile, doc_type in INSTRUCTOR_DOCS:
            html, blocks = _render_doc(slug_dir, relfile)
            if html is None:
                missing.append((mid, doc_type))
                continue
            docs.append({
                "matter_id": mid,
                "doc_type": doc_type,
                "source_ref": bs.data_relpath(slug_dir, relfile),
                "html": html,
                "blocks": blocks,
            })
            counts[doc_type] = counts.get(doc_type, 0) + 1

    bundle = {
        "schema_version": "1.0.0",
        "generated_by": "tools/build_instructor_bundle.py",
        "note": ("SERVER-ONLY instructor materials (facts, teaching notes, ANSWER "
                 "KEYS). Bundled by the Worker for /edit/instructor. NEVER ship in "
                 "site/platform/ or personas.generated.json."),
        "spine_build_id": spine_stamp.compute(DATA_DIR),
        "git_base_sha": spine_stamp.git_base_sha(),
        "normalization": "original_hash per tools/text_norm.py (shared spec).",
        "docs": docs,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # ---- self-checks (fail loud) ----
    assert os.path.abspath(OUT_PATH).startswith(os.path.abspath(os.path.join(REPO_ROOT, "build"))), \
        "instructor bundle must be written under build/"
    persona_bundle = os.path.join(REPO_ROOT, "app", "worker", "personas", "personas.generated.json")
    if os.path.isfile(persona_bundle):
        with open(persona_bundle, "r", encoding="utf-8") as fh:
            pb = fh.read()
        # No instructor HTML doc should appear inside the persona/chat bundle.
        for d in docs:
            snippet = text_norm.normalize(d["html"])[:80]
            if snippet and snippet in pb:
                sys.stderr.write("FATAL: instructor content leaked into personas bundle (%s/%s)\n"
                                 % (d["matter_id"], d["doc_type"]))
                return 2

    total_blocks = sum(len(d["blocks"]) for d in docs)
    print("instructor bundle written: %s" % os.path.relpath(OUT_PATH, REPO_ROOT))
    print("  spine_build: %s" % bundle["spine_build_id"][:16])
    print("  docs       : %d (%s)" % (len(docs), ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items()))))
    print("  blocks     : %d editable across all instructor docs" % total_blocks)
    if missing:
        print("  missing    : %d" % len(missing))
        for mid, dt in missing:
            print("    - %s/%s" % (mid, dt))
    print("  self-check : output outside site/platform/ ✓; not in personas bundle ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
