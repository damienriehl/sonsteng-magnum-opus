// editor-security.test.js — source-scan parity with the byok key-never-logged
// guarantee, extended to the editor surface: no logging path in the editor
// modules references the opaque token, the cookie value, or a secret; the strict
// CSP + uniform-404 + instructor uniform-404 shape are asserted.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { EDIT_CSP, editSecurityHeaders, uniform404, csrfOk } from "../src/editor-http.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const srcDir = join(__dirname, "..", "src");

function editorSources() {
  const files = [];
  (function walk(dir) {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (name.endsWith(".js") && /editor|text-norm/.test(name)) files.push(p);
    }
  })(srcDir);
  return files;
}

test("no editor logging path references a token, cookie, or secret", () => {
  const files = editorSources();
  assert.ok(files.length >= 8, "source scan must find the editor modules");
  const forbidden = /(EDIT_TOKEN|api_?key|SESSION_SIGNING_KEY|presented|setCookie|cookie\s*value)/i;
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    for (const line of src.split("\n")) {
      // Any structured-log call site (console.log / logMeta / *log(JSON) ) must
      // not carry a token/cookie/secret reference.
      if (/console\.(log|error|warn|info)\(|logMeta\(/.test(line)) {
        assert.ok(!forbidden.test(line), `${f}: log line references a secret/token/cookie: ${line.trim()}`);
      }
    }
  }
});

test("editor-auth never console.logs at all (holds the token + cookie)", () => {
  const src = readFileSync(join(srcDir, "editor-auth.js"), "utf8");
  assert.ok(!src.includes("console."), "editor-auth.js must not log");
});

test("strict CSP is present on every edit response", () => {
  const h = editSecurityHeaders();
  assert.equal(h["Content-Security-Policy"], EDIT_CSP);
  assert.match(EDIT_CSP, /default-src 'none'/);
  assert.match(EDIT_CSP, /base-uri 'self'/);
  assert.match(EDIT_CSP, /frame-ancestors 'none'/);
  assert.match(EDIT_CSP, /script-src 'self'/);
  assert.equal(h["Cache-Control"], "private, no-store");
  assert.equal(h["Vary"], "Cookie");
  assert.equal(h["Referrer-Policy"], "no-referrer");
});

test("uniform404 is identical bytes/status for every not-found reason", async () => {
  const a = uniform404();
  const b = uniform404();
  assert.equal(a.status, 404);
  assert.equal(b.status, 404);
  assert.equal(await a.text(), await b.text()); // no oracle
});

// The uniform 404 is the LAST thing a locked-out reviewer sees, and for a long
// time it said only "Not found." — correct for a probe, useless for Prof.
// Sonsteng, who reaches it whenever the ?t= token is missing from the address
// (the redirect strips it, so a bookmark taken after arrival lacks it). It now
// tells that person how to get back in WITHOUT telling a prober anything: the
// bytes are still identical for every reason, so there is no oracle.
test("uniform404 tells a locked-out reviewer how to get back in", async () => {
  const body = await uniform404().text();
  assert.match(body, /reopen your editing link/i, "must name the recovery action");
  assert.match(body, /bookmark/i, "must explain the bookmark trap that causes this");
  assert.match(body, /nothing you wrote has been lost/i, "must reassure — no work is lost");
  assert.equal(uniform404().headers.get("content-type"), "text/html; charset=utf-8");
  // Still no oracle: nothing in the page hints at whether the path was real, and
  // it names no page, matter, scope or token.
  assert.doesNotMatch(body, /scope|token|instructor|admin|matters\/m\d/i);
});

test("csrfOk rejects a missing custom header even with same-origin", () => {
  const env = { EDIT_ORIGIN: "https://w.example.com" };
  const req = new Request("https://w.example.com/edit/v1/decide", {
    method: "POST",
    headers: { Origin: "https://w.example.com", "Sec-Fetch-Site": "same-origin" },
  });
  assert.equal(csrfOk(req, env), false);
});
