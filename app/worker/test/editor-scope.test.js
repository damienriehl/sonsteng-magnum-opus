// editor-scope.test.js — the scope ladder's deterministic enumeration (U6):
// part -> matter -> module -> course resolve to exact block sets from the map,
// with a blast-radius summary (matters/files/blocks) BEFORE any model runs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { EDITOR_MAP, enumerateScope } from "../src/editor-map.js";
import { scopeEndpoint } from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";

const TOTAL = EDITOR_MAP.counts._total;
const SLUG = "m03-tort-meridian";

function blocksUnder(prefix) {
  let n = 0;
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) if (b.source_ref.startsWith(prefix)) n++;
  }
  return n;
}

test("course scope covers exactly the whole map", () => {
  const r = enumerateScope({ level: "course" });
  assert.equal(r.ok, true);
  assert.equal(r.blocks, TOTAL);
  assert.ok(r.files > 200);
  assert.equal(r.matters.length, 20);
});

test("matter scope counts reconcile against the map", () => {
  const r = enumerateScope({ level: "matter", matter: SLUG });
  assert.equal(r.ok, true);
  assert.equal(r.blocks, blocksUnder(`data/matters/${SLUG}/`));
  assert.deepEqual(r.matters, [SLUG]);
  assert.ok(r.blocks > 50);
});

test("part scope partitions its matter", () => {
  const parts = ["case-file", "exercise", "business", "matter"];
  let sum = 0;
  for (const p of parts) {
    const r = enumerateScope({ level: "part", matter: SLUG, part: p });
    assert.equal(r.ok, true, p);
    sum += r.blocks;
  }
  assert.equal(sum, enumerateScope({ level: "matter", matter: SLUG }).blocks);
  const cf = enumerateScope({ level: "part", matter: SLUG, part: "case-file" });
  assert.equal(cf.blocks, blocksUnder(`data/matters/${SLUG}/case-file/`));
});

test("module scope spans its curriculum page plus member matters' exercises", () => {
  const r = enumerateScope({ level: "module", module: "M2" });
  assert.equal(r.ok, true);
  assert.ok(r.matters.length > 10, "M2 cuts across most matters: " + r.matters.length);
  assert.ok(r.matters.includes(SLUG));
  const curriculum = blocksUnder("data/curriculum/m2.md");
  const exercises = r.matters.reduce(
    (n, s) => n + blocksUnder(`data/matters/${s}/exercise/`), 0);
  assert.equal(r.blocks, curriculum + exercises);
});

test("enumeration is deterministic and returns refs capped with honesty about it", () => {
  const a = enumerateScope({ level: "module", module: "M1" });
  const b = enumerateScope({ level: "module", module: "M1" });
  assert.deepEqual(a, b);
  assert.ok(Array.isArray(a.refs));
  assert.ok(a.refs.length <= 200);
  assert.equal(a.refs_truncated, a.blocks > a.refs.length);
});

test("invalid scopes are rejected, never guessed", () => {
  for (const bad of [
    { level: "matter" },                          // matter missing
    { level: "matter", matter: "m99-nope" },      // unknown matter
    { level: "part", matter: SLUG, part: "personas" }, // not an editable part
    { level: "module", module: "M9" },
    { level: "galaxy" },
  ]) {
    const r = enumerateScope(bad);
    assert.equal(r.ok, false, JSON.stringify(bad));
    assert.equal(r.reason, "validation_error");
  }
});

// ---- endpoint: GET /edit/v1/scope ------------------------------------------
const ENV = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};

async function get(url, token) {
  const auth = await resolveAuth(ENV, new Request("https://worker.example.com/x", {
    headers: { Authorization: `Bearer ${token}` },
  }));
  return scopeEndpoint(new Request(url), ENV, auth);
}

test("scope endpoint serves editors and admin; anonymous is refused", async () => {
  const ok = await get(
    `https://worker.example.com/edit/v1/scope?level=matter&matter=${SLUG}`,
    ENV.EDIT_TOKEN_JOHN);
  assert.equal(ok.status, 200);
  const data = await ok.json();
  assert.equal(data.ok, true);
  assert.equal(data.blocks, blocksUnder(`data/matters/${SLUG}/`));

  const anon = await scopeEndpoint(
    new Request("https://worker.example.com/edit/v1/scope?level=course"),
    ENV, await resolveAuth(ENV, new Request("https://worker.example.com/x")));
  assert.equal(anon.status, 403);
});

test("scope endpoint rejects an invalid level with 400", async () => {
  const r = await get("https://worker.example.com/edit/v1/scope?level=galaxy",
    ENV.EDIT_TOKEN_JOHN);
  assert.equal(r.status, 400);
});
