// text-norm.js — the JS mirror of tools/text_norm.py, BYTE-FOR-BYTE.
//
// Both sides of the editor round-trip hash the SAME canonical form:
//   * the Python generator (tools/build_site.py / build_instructor_bundle.py)
//     writes original_hash = sha256(normalize(rendered_text)) into the maps;
//   * this module lets the Worker recompute that hash server-side to detect
//     drift and to verify what the editor saw.
// If the two disagree by a single code point every hash mismatches and the apply
// loop stalls, so the rules below are frozen and mirror text_norm.py exactly.
// A cross-language parity test (test/editor-norm-parity.test.js) proves it.
// EVERY non-ASCII code point is written as a \u escape so the source is
// unambiguous — U+2028 and U+2029 are JS line terminators and MUST be escaped.
//
// CANONICAL SPEC (see tools/text_norm.py docstring — the frozen source of truth):
//   1. Unicode NFC.
//   2. contenteditable artifact strip: CRLF/CR -> LF, U+2028/U+2029 -> LF, then
//      every LF -> a single space (an editable BLOCK is single-logical-line prose).
//   3. smart-quote / dash / space fold to ASCII (table below).
//   4. collapse every run of whitespace to one space, then trim.

// Step-3 fold table (code point -> replacement), applied after NFC + the newline
// fold. Mirrors _FOLD in text_norm.py exactly.
const FOLD = {
  "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
  "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
  "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
  "―": "-", "−": "-",
  " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
  "​": "", "‌": "", "‍": "", "﻿": "",
  "…": "...",
};
// Character class of every fold key, for a single-pass replace.
const FOLD_RE = new RegExp(
  "[" + Object.keys(FOLD).map((c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0")).join("") + "]",
  "g"
);

// Return the canonical normalized form of `s` per the frozen spec. Deterministic
// and idempotent (normalize(normalize(x)) === normalize(x)). null -> "".
export function normalize(s) {
  if (s === null || s === undefined) return "";
  if (typeof s !== "string") s = String(s);
  // 1. NFC
  s = s.normalize("NFC");
  // 2. contenteditable artifact strip: all newline variants -> LF -> space
  s = s.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  s = s.replace(/\u2028/g, "\n").replace(/\u2029/g, "\n");
  s = s.replace(/\n/g, " ");
  // 3. smart-quote / dash / space fold
  s = s.replace(FOLD_RE, (ch) => FOLD[ch]);
  // 4. whitespace collapse + trim
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

// SHA-256 (lowercase hex) of the UTF-8 bytes of `str`. Web Crypto (Workers) and
// node:crypto webcrypto both expose crypto.subtle.digest.
export async function sha256Hex(str) {
  const bytes = new TextEncoder().encode(str);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let out = "";
  for (let i = 0; i < digest.length; i++) out += digest[i].toString(16).padStart(2, "0");
  return out;
}

// original_hash: sha256(hex) of normalize(s). Async (subtle.digest). This is the
// exact value the Python generator stored (norm_hash in text_norm.py).
export async function normHash(s) {
  return sha256Hex(normalize(s));
}
