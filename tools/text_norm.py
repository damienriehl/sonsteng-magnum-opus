#!/usr/bin/env python3
r"""text_norm.py — the ONE canonical text-normalization contract for the editor.

Both sides of the round-trip depend on this being byte-for-byte identical:

  * The GENERATOR (tools/build_site.py, tools/build_instructor_bundle.py) hashes
    the normalized *rendered* text of every editable block into
    `original_hash` in editor-map.generated.json / instructor-bundle.generated.json.
  * The WORKER / EDITOR CLIENT (app/worker, app/editor — later phases) will hash
    the normalized text a `contenteditable` block shows John, and compare it to
    `original_hash` to detect drift and to key drafts.

If the two implementations disagree by a single code point, every hash mismatches
and the whole apply loop stalls. So the rules below are frozen and the JS side
MUST mirror them exactly. This module is Python-3 stdlib only.

===========================================================================
CANONICAL NORMALIZATION SPEC  (mirror this in JS byte-for-byte)
===========================================================================
Apply these steps, in this order, to a Unicode string:

  1. UNICODE NORMAL FORM C (NFC).
       py: unicodedata.normalize("NFC", s)
       js: s.normalize("NFC")

  2. CONTENTEDITABLE ARTIFACT STRIP — turn the block/line structure a browser
     leaves in `contenteditable` into plain "\n" line breaks, THEN drop the
     line breaks entirely (an editable BLOCK is single-logical-line prose):
       - Replace CRLF ("\r\n") and CR ("\r") with LF ("\n").
       - Replace the Unicode line/paragraph separators U+2028 and U+2029 with LF.
       - Replace every remaining control/format newline-ish artifact (a bare
         <br>/<div> boundary shows up as "\n" once the DOM text is read) with " ".
       (In text form we never see literal tags — we operate on `.textContent`,
       so this step is: turn every newline into a space.)

  3. SMART-QUOTE / DASH / SPACE FOLD — fold typographic variants to ASCII so a
     value typed on Windows, iPad, or pasted from Word all hash the same:
       - Curly single quotes  U+2018 U+2019 U+201A U+201B  -> "'"  (straight apostrophe)
       - Prime                U+2032                        -> "'"
       - Curly double quotes  U+201C U+201D U+201E U+201F  -> '"'  (straight quote)
       - Double prime         U+2033                        -> '"'
       - Dashes  U+2010 U+2011 U+2012 U+2013 U+2014 U+2015 and U+2212 (minus) -> "-"
       - Non-breaking / thin / hair spaces  U+00A0 U+2007 U+202F U+2009 U+200A -> " "
       - Zero-width chars  U+200B U+200C U+200D U+FEFF  -> "" (delete)
       - Ellipsis  U+2026  -> "..."

  4. WHITESPACE COLLAPSE — collapse every run of ASCII whitespace to a single
     space and strip leading/trailing whitespace:
       py: re.sub(r"\s+", " ", s).strip()
       js: s.replace(/\s+/g, " ").trim()

The output is a single-line, NFC, ASCII-punctuation-folded string. Hash it as
UTF-8 bytes with SHA-256 (see norm_hash).
===========================================================================
"""

import hashlib
import re
import unicodedata

# Step-3 fold table (single-char -> replacement). Applied after NFC + newline fold.
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "﻿": "",
    "…": "...",
}
_FOLD_TABLE = {ord(k): v for k, v in _FOLD.items()}

_WS_RE = re.compile(r"\s+")


def normalize(s):
    """Return the canonical normalized form of `s` per the spec above.

    Deterministic, idempotent (normalize(normalize(x)) == normalize(x)),
    stdlib only. `None` normalizes to "".
    """
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    # 1. NFC
    s = unicodedata.normalize("NFC", s)
    # 2. contenteditable artifact strip: all newline variants -> LF -> space
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace(" ", "\n").replace(" ", "\n")
    s = s.replace("\n", " ")
    # 3. smart-quote / dash / space fold
    s = s.translate(_FOLD_TABLE)
    # 4. whitespace collapse + trim
    s = _WS_RE.sub(" ", s).strip()
    return s


def norm_hash(s):
    """SHA-256 (hex) of the UTF-8 bytes of normalize(s). This is `original_hash`."""
    return hashlib.sha256(normalize(s).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    # Tiny self-check / demonstration of idempotency and folding.
    samples = [
        "  The  “quick”—brown fox’s tale…  ",
        "line one\r\nline two line three",
        "non breaking​space",
    ]
    for x in samples:
        n = normalize(x)
        assert normalize(n) == n, "not idempotent: %r" % x
        print("%-45r -> %r  (%s)" % (x, n, norm_hash(x)[:12]))
