// editor-norm-parity.test.js — the JS text-norm mirror MUST match tools/text_norm.py
// byte-for-byte, or every original_hash mismatches and the apply loop stalls.
// This hashes a set of strings in BOTH languages and asserts equality.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { normalize } from "../src/text-norm.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..", "..", "..");

// Edge cases spanning every rule: NFC, contenteditable newlines, smart quotes,
// dashes, non-breaking/zero-width spaces, ellipsis, whitespace collapse, money.
const SAMPLES = [
  "  The  “quick”—brown fox’s tale…  ",
  "line one\r\nline two line three",
  "para\u2028sep\u2029break",
  "non​breaking space",
  "$1,000.00 on Jan 5",
  "‘single’ and “double” and ′prime″",
  "em—dash en–dash minus−sign hyphen‐",
  "zero‌width﻿chars",
  "tab\tand thin spaces",
  "Café résumé naïve", // combining marks -> NFC
  "MOOTLOOP v. Duckler, No. 62-CV-26-2379",
  "",
];

test("JS normalize/hash contract matches Python text_norm byte-for-byte", () => {
  // JS side
  // Use Node's synchronous SHA-256 in this cross-process parity test. The
  // browser harness covers the Worker's Web Crypto wrapper; keeping it out of
  // this child-process test avoids libuv/WebCrypto starvation in constrained CI.
  const jsRows = SAMPLES.map((s) => {
    const normalized = normalize(s);
    return [normalized, createHash("sha256").update(normalized, "utf8").digest("hex")];
  });

  // Python side (stdlib only; uses the frozen tools/text_norm.py).
  const py = spawnSync("python3", ["-c", `
import sys, json
sys.path.insert(0, ${JSON.stringify(join(REPO_ROOT, "tools"))})
import text_norm
S = json.loads(sys.argv[1])
print(json.dumps([[text_norm.normalize(s), text_norm.norm_hash(s)] for s in S]))
`, JSON.stringify(SAMPLES)], { encoding: "utf8" });

  assert.equal(py.status, 0, `python failed: ${py.stderr}`);
  const pyRows = JSON.parse(py.stdout);
  assert.deepEqual(jsRows, pyRows);
});
