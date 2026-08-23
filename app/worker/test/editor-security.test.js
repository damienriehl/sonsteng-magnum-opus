// editor-security.test.js — source-scan parity with the byok key-never-logged
// guarantee, extended to the editor surface: no logging path in the editor
// modules references the opaque token, the cookie value, or a secret; the strict
// CSP + uniform-404 + instructor uniform-404 shape are asserted.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  EDIT_CSP,
  editSecurityHeaders,
  uniform404,
  csrfOk,
  editOrigin,
  editCorsHeaders,
} from "../src/editor-http.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const srcDir = join(__dirname, "..", "src");

function editorSources() {
  const files = [];
  (function walk(dir) {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      // `access-jwt` is in the glob deliberately: the Cf-Access-Jwt-Assertion is a
      // bearer credential exactly like the ?t= token, so the never-log rule has to
      // cover the Access door too. Without it the newest auth path was the only
      // one this scan could not see.
      else if (name.endsWith(".js") && /editor|text-norm|access-jwt/.test(name)) files.push(p);
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

// ---------------------------------------------------------------------------
// EDIT_ORIGIN is a LIST (KTD6). One deployment answers on two browser origins at
// once — the Cloudflare Access hostname and the older workers.dev fallback that
// every already-sent link points at. csrfOk gates all eleven mutation endpoints
// on the Origin header, so a single swapped value would let every old bookmark
// keep LOADING pages (navigations send no Origin) while every SAVE from those
// pages returned 403 csrf_failed. That failure is invisible to fixtures that set
// EDIT_ORIGIN to whatever origin they simulate, which is every other test in this
// suite — these are the tests that actually hold the property.
// ---------------------------------------------------------------------------

const NEW_DOOR = "https://edit.legalpracticum.org";
const OLD_DOOR = "https://sonsteng-chat.damienriehl.workers.dev";
const STRANGER = "https://evil.example.com";

// An XHR the editor page itself would send: custom header + Origin + same-origin
// fetch metadata.
function editXhr(origin, extra = {}) {
  return new Request(`${origin}/edit/v1/suggest`, {
    method: "POST",
    headers: { "X-Edit-Request": "1", Origin: origin, "Sec-Fetch-Site": "same-origin", ...extra },
  });
}

test("EDIT_ORIGIN with a single value behaves exactly as before (regression guard)", () => {
  const env = { EDIT_ORIGIN: OLD_DOOR };
  assert.deepEqual([...editOrigin(env)], [OLD_DOOR]);
  assert.equal(csrfOk(editXhr(OLD_DOOR), env), true);
  assert.equal(csrfOk(editXhr(STRANGER), env), false);
  assert.equal(editCorsHeaders(env, OLD_DOOR)["Access-Control-Allow-Origin"], OLD_DOOR);
  assert.deepEqual(editCorsHeaders(env, STRANGER), {});
});

test("EDIT_ORIGIN unset leaves the allowlist empty and unenforced (dev)", () => {
  const env = {};
  assert.equal(editOrigin(env).size, 0);
  // No allowlist configured => the Origin check is skipped, not failed. Several
  // fixtures elsewhere in the suite rely on this; do not tighten it.
  assert.equal(csrfOk(editXhr(STRANGER), env), true);
  // ...but with nothing allowlisted there is still no ACAO to hand out.
  assert.deepEqual(editCorsHeaders(env, STRANGER), {});
});

test("both listed origins pass csrfOk and get CORS headers", () => {
  const env = { EDIT_ORIGIN: `${NEW_DOOR},${OLD_DOOR}` };
  assert.deepEqual([...editOrigin(env)], [NEW_DOOR, OLD_DOOR]);
  for (const origin of [NEW_DOOR, OLD_DOOR]) {
    assert.equal(csrfOk(editXhr(origin), env), true, `${origin} must pass csrfOk`);
    const h = editCorsHeaders(env, origin);
    assert.equal(h["Access-Control-Allow-Origin"], origin);
    assert.equal(h["Access-Control-Allow-Credentials"], "true");
    assert.equal(h["Vary"], "Origin, Cookie");
  }
});

test("the echoed ACAO is the single matched origin — never the list, never '*'", () => {
  const env = { EDIT_ORIGIN: `${NEW_DOOR},${OLD_DOOR}` };
  for (const origin of [NEW_DOOR, OLD_DOOR]) {
    const acao = editCorsHeaders(env, origin)["Access-Control-Allow-Origin"];
    assert.equal(acao, origin);
    assert.doesNotMatch(acao, /,/, "ACAO must never be the comma-separated list");
    assert.notEqual(acao, "*", "credentialed responses may never wildcard");
  }
});

test("an unlisted third origin is refused by BOTH csrfOk and editCorsHeaders", () => {
  const env = { EDIT_ORIGIN: `${NEW_DOOR},${OLD_DOOR}` };
  assert.equal(csrfOk(editXhr(STRANGER), env), false);
  assert.deepEqual(editCorsHeaders(env, STRANGER), {});
  // A near-miss (right suffix, wrong host) must not slip through a substring bug.
  const lookalike = "https://evil-edit.legalpracticum.org.attacker.test";
  assert.equal(csrfOk(editXhr(lookalike), env), false);
  assert.deepEqual(editCorsHeaders(env, lookalike), {});
  // No Origin at all still gets no CORS headers (and needs none: same-origin).
  assert.deepEqual(editCorsHeaders(env, null), {});
});

test("X-Edit-Request is still required, even from a listed origin", () => {
  const env = { EDIT_ORIGIN: `${NEW_DOOR},${OLD_DOOR}` };
  for (const origin of [NEW_DOOR, OLD_DOOR]) {
    const req = new Request(`${origin}/edit/v1/suggest`, {
      method: "POST",
      headers: { Origin: origin, "Sec-Fetch-Site": "same-origin" },
    });
    assert.equal(csrfOk(req, env), false, `${origin} must still need the custom header`);
  }
});

test("Sec-Fetch-Site: cross-site is still rejected, even from a listed origin", () => {
  const env = { EDIT_ORIGIN: `${NEW_DOOR},${OLD_DOOR}` };
  for (const origin of [NEW_DOOR, OLD_DOOR]) {
    assert.equal(
      csrfOk(editXhr(origin, { "Sec-Fetch-Site": "cross-site" }), env),
      false,
      `${origin} must still fail the fetch-metadata check`
    );
  }
});

test("whitespace and empty entries around the commas are tolerated", () => {
  const env = { EDIT_ORIGIN: `  ${NEW_DOOR} ,, ${OLD_DOOR}  ,  ` };
  assert.deepEqual([...editOrigin(env)], [NEW_DOOR, OLD_DOOR]);
  assert.equal(csrfOk(editXhr(NEW_DOOR), env), true);
  assert.equal(csrfOk(editXhr(OLD_DOOR), env), true);
  assert.equal(editCorsHeaders(env, NEW_DOOR)["Access-Control-Allow-Origin"], NEW_DOOR);
  assert.equal(csrfOk(editXhr(STRANGER), env), false);
});

// The 404 now has to serve BOTH doors: someone denied at the Access check who
// never held a personal link, and someone whose old bookmark lapsed. It still
// may not vary by request — the byte-identity IS the no-oracle property.
test("uniform404 is byte-identical across unknown, under-scoped and hostile paths", async () => {
  // uniform404() takes no request by design; simulate the call sites (unknown
  // path, known path without the right scope, traversal probe) and prove the
  // bytes and status never diverge.
  const bodies = [];
  for (const _path of [
    "/edit/nope",
    "/edit/admin",
    "/edit/practicum/m1/doc",
    "/edit/../../etc/passwd",
    "/edit/v1/pending",
  ]) {
    const r = uniform404();
    assert.equal(r.status, 404);
    assert.equal(r.headers.get("content-type"), "text/html; charset=utf-8");
    bodies.push(await r.text());
  }
  for (const b of bodies) assert.equal(b, bodies[0], "the 404 body must not vary by path");
});

test("uniform404 names the Access address and drops the token-only phrasing", async () => {
  const body = await uniform404().text();
  assert.match(body, /edit\.legalpracticum\.org/, "must name the durable way back in");
  assert.doesNotMatch(
    body,
    /only opens through the\s+personal link|personal link Damien sent you/i,
    "must not tell an Access visitor to reopen a link they never had"
  );
  // Still helps the person holding an old link, and still reassures.
  assert.match(body, /reopen your editing link/i);
  assert.match(body, /nothing you wrote has been lost/i);
  // Design is unchanged: serif stack, cream ground, crimson rule.
  assert.match(body, /Iowan Old Style/);
  assert.match(body, /#f4efe4/);
  assert.match(body, /#7c1e2b/);
});
