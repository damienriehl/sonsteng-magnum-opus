#!/usr/bin/env python3
"""Word-level HTML diff for long-lived docs — importable library form.

Ported verbatim from the fence-litigation build
(``fence-litigation/tools/render_diff_lib.py``), which in turn was ported from
``~/Coding Projects/tools/render-diff.py`` (stdlib-only, word-level). Same author
(Damien Riehl), so no THIRD-PARTY attribution is required — this is first-party
code reused across projects. The algorithm is unchanged: tokenize on
``\\S+|\\s+`` (words and whitespace runs), ``difflib.SequenceMatcher`` opcodes,
insertions rendered ``<ins>`` / deletions ``<del>``, and equal runs longer than
``2*CTX+20`` tokens collapse into an expandable ``<details>`` block keeping
``CTX`` words of context on each side.

Library entry points:

* ``diff_html(old_text, new_text)`` -> the ``<pre>``-ready HTML *fragment* plus
  insertion/deletion region counts (returns ``DiffResult``).
* ``diff_page(old_text, new_text, title=...)`` -> a complete standalone HTML page,
  byte-equivalent in structure to the original CLI's output.

sonsteng's ``tools/build_history.py`` wraps ``diff_html`` to pre-render every
revision/baseline redline into the editor-gated history bundle; ``diff_page``
preserves the original file-writing CLI behaviour for ad-hoc redline pages.
"""
from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass

CTX = 40  # words of context kept visible around each change (unchanged from origin)


def toks(text: str) -> list[str]:
    """Split into words and whitespace runs (the original tokenization)."""
    return re.findall(r"\S+|\s+", text)


@dataclass(frozen=True)
class DiffResult:
    """Result of a word-level diff."""

    html: str  # inner fragment (goes inside <pre>)
    n_ins: int  # number of insertion regions
    n_del: int  # number of deletion regions


def diff_html(old_text: str, new_text: str, ctx: int = CTX) -> DiffResult:
    """Word-level diff of two strings -> ``DiffResult`` with the inner HTML fragment."""
    ot = toks(old_text)
    nt = toks(new_text)

    sm = difflib.SequenceMatcher(None, ot, nt, autojunk=False)
    parts: list[str] = []
    n_ins = n_del = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        o, n = "".join(ot[i1:i2]), "".join(nt[j1:j2])
        if op == "equal":
            if i2 - i1 > 2 * ctx + 20:
                head = html.escape("".join(nt[j1 : j1 + ctx]))
                mid = html.escape("".join(nt[j1 + ctx : j2 - ctx]))
                tail = html.escape("".join(nt[j2 - ctx : j2]))
                parts.append(
                    head
                    + f"<details><summary>… {i2 - i1 - 2 * ctx} unchanged words …"
                    + f"</summary>{mid}</details>"
                    + tail
                )
            else:
                parts.append(html.escape(n))
            continue
        if op in ("delete", "replace") and o:
            parts.append(f"<del>{html.escape(o)}</del>")
            n_del += 1
        if op in ("insert", "replace") and n:
            parts.append(f"<ins>{html.escape(n)}</ins>")
            n_ins += 1

    return DiffResult(html="".join(parts), n_ins=n_ins, n_del=n_del)


# The standalone page chrome, kept structurally identical to the origin CLI.
_PAGE_TEMPLATE = """<!doctype html><meta charset="utf-8">
<title>{title}</title>
<style>
body{{max-width:62rem;margin:2rem auto;padding:0 1rem;color:#1f2430;
     font:15px/1.6 Georgia,'Times New Roman',serif;background:#fbfaf7}}
h1{{font-size:1.15rem;letter-spacing:.02em}}
p.legend{{color:#6b7180;font-size:.85rem}}
pre{{white-space:pre-wrap;overflow-wrap:break-word;
    font:13px/1.6 ui-monospace,Menlo,Consolas,monospace}}
ins{{background:#d9f2d9;color:#155724;text-decoration:none;border-radius:2px;padding:0 1px}}
del{{background:#fbdcdf;color:#8a1420;border-radius:2px;padding:0 1px}}
details{{color:#8a8f9c;margin:.25em 0;border-left:3px solid #e4e1d8;padding-left:.6em}}
summary{{cursor:pointer;font:12px ui-monospace,monospace}}
</style>
<h1>{title}</h1>
<p class="legend">{n_ins} insertion region(s) · {n_del} deletion region(s) ·
click "unchanged words" to expand collapsed context</p>
<pre>{body}</pre>
"""


def diff_page(old_text: str, new_text: str, title: str = "diff", ctx: int = CTX) -> str:
    """Full standalone HTML page for a redline (original CLI's output shape)."""
    result = diff_html(old_text, new_text, ctx=ctx)
    return _PAGE_TEMPLATE.format(
        title=html.escape(title),
        n_ins=result.n_ins,
        n_del=result.n_del,
        body=result.html,
    )


def main() -> None:
    """CLI parity with the origin: ``render_diff_lib.py OLD NEW OUT.html [title]``."""
    import sys

    if len(sys.argv) < 4:
        sys.exit(__doc__)
    old_path, new_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    title = sys.argv[4] if len(sys.argv) > 4 else f"{old_path} → {new_path}"
    with open(old_path, encoding="utf-8") as f:
        old_text = f.read()
    with open(new_path, encoding="utf-8") as f:
        new_text = f.read()
    page = diff_page(old_text, new_text, title=title)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    result = diff_html(old_text, new_text)
    print(f"wrote {out_path} ({result.n_ins} insertions, {result.n_del} deletions)")


if __name__ == "__main__":
    main()
