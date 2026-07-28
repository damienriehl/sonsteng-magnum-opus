// editor-structural-endpoints.test.js — /edit/v1/suggest carrying structural
// operations (U4). Same validation posture as every other mutation: CSRF header,
// server-resolved editor, map-allowlisted refs (source_ref AND op_arg), sizes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { suggestEndpoint } from "../src/editor-endpoints.js";
import { resolveAuth } from "../src/editor-auth.js";
import { EDITOR_MAP } from "../src/editor-map.js";

const ENV_BASE = {
  SESSION_SIGNING_KEY: "test-signing-key-abc",
  EDIT_ORIGIN: "https://worker.example.com",
  EDIT_TOKEN_SCOPES: JSON.stringify({ john: { edit: 1 }, admin: { admin: 1 } }),
  EDIT_TOKEN_JOHN: "john-opaque-token-value-123",
  EDIT_TOKEN_ADMIN: "admin-opaque-token-value-999",
};

// Two prose blocks from the SAME .md source file (merge/move need same-file).
function twoProseSameFile() {
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    const byFile = new Map();
    for (const b of blocks) {
      if (b.kind !== "prose") continue;
      const f = b.source_ref.split("#", 1)[0];
      if (!f.endsWith(".md")) continue;
      if (!byFile.has(f)) byFile.set(f, []);
      byFile.get(f).push(b);
      if (byFile.get(f).length >= 2) return byFile.get(f);
    }
  }
  throw new Error("need two prose blocks in one md file");
}
const [P1, P2] = twoProseSameFile();

function proseFromOtherFile() {
  const f1 = P1.source_ref.split("#", 1)[0];
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) {
      if (b.kind === "prose" && b.source_ref.split("#", 1)[0] !== f1) return b;
    }
  }
  throw new Error("no prose block in another file");
}
const OTHER = proseFromOtherFile();

function scalarBlock() {
  for (const blocks of Object.values(EDITOR_MAP.pages || {})) {
    for (const b of blocks) if (b.kind === "json_scalar") return b;
  }
  throw new Error("no scalar block");
}
const SCALAR = scalarBlock();

function envWith(cap = {}) {
  return {
    ...ENV_BASE,
    EDITOR: {
      getByName() {
        return {
          async suggest(input, _ceil, opts) {
            cap.input = input;
            cap.opts = opts;
            return { ok: true, suggestion: { status: "pending" } };
          },
        };
      },
    },
  };
}

let n = 0;
async function post(env, body) {
  const auth = await resolveAuth(env, new Request("https://worker.example.com/x", {
    headers: { Authorization: `Bearer ${ENV_BASE.EDIT_TOKEN_JOHN}` },
  }));
  const req = new Request("https://worker.example.com/edit/v1/suggest", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-edit-request": "1",
      origin: "https://worker.example.com",
    },
    body: JSON.stringify({ id: `structural-${++n}-abcdef`, ...body }),
  });
  return suggestEndpoint(req, env, auth);
}

test("insert_after: valid payload stores kind + anchor and stays queued", async () => {
  const cap = {};
  const res = await post(envWith(cap), {
    source_ref: P1.source_ref, op: "insert_after", new_text: "A new paragraph.",
  });
  assert.equal(res.status, 200);
  assert.equal(cap.input.kind, "insert_after");
  assert.equal(cap.input.source_ref, P1.source_ref);
  assert.equal(cap.input.new_text, "A new paragraph.");
  assert.equal(cap.input.original_text, P1.original_text); // server-resolved anchor
});

test("delete: no payload required; kind stored", async () => {
  const cap = {};
  const res = await post(envWith(cap), { source_ref: P1.source_ref, op: "delete" });
  assert.equal(res.status, 200);
  assert.equal(cap.input.kind, "delete");
  assert.equal(cap.input.new_text, null);
});

test("split: both parts required, stored as one two-part payload", async () => {
  const cap = {};
  const res = await post(envWith(cap), {
    source_ref: P1.source_ref, op: "split",
    new_text: "First half.", new_text2: "Second half.",
  });
  assert.equal(res.status, 200);
  assert.equal(cap.input.kind, "split");
  assert.equal(cap.input.new_text, "First half.\n\nSecond half.");
  const missing = await post(envWith(), {
    source_ref: P1.source_ref, op: "split", new_text: "Only one half.",
  });
  assert.equal(missing.status, 400);
});

test("merge/move: op_arg is allowlisted and must share the source file", async () => {
  const cap = {};
  const ok = await post(envWith(cap), {
    source_ref: P1.source_ref, op: "move", op_arg: P2.source_ref,
  });
  assert.equal(ok.status, 200);
  assert.equal(cap.input.kind, "move");
  assert.equal(cap.input.op_arg, P2.source_ref);

  const crossFile = await post(envWith(), {
    source_ref: P1.source_ref, op: "move", op_arg: OTHER.source_ref,
  });
  assert.equal(crossFile.status, 400);

  const forged = await post(envWith(), {
    source_ref: P1.source_ref, op: "merge", op_arg: "data/evil.md#bdeadbeef",
  });
  assert.equal(forged.status, 400);

  const missingArg = await post(envWith(), { source_ref: P1.source_ref, op: "merge" });
  assert.equal(missingArg.status, 400);
});

test("structural ops are prose-only: a json_scalar anchor is rejected", async () => {
  const res = await post(envWith(), {
    source_ref: SCALAR.source_ref, op: "insert_after", new_text: "Nope.",
  });
  assert.equal(res.status, 400);
});

test("unknown op and reserved-marker payloads are rejected", async () => {
  const bad = await post(envWith(), {
    source_ref: P1.source_ref, op: "transmogrify", new_text: "x",
  });
  assert.equal(bad.status, 400);
  const marker = await post(envWith(), {
    source_ref: P1.source_ref, op: "insert_after", new_text: "sneaky {#b:00000000}",
  });
  assert.equal(marker.status, 400);
  const multiline = await post(envWith(), {
    source_ref: P1.source_ref, op: "insert_after", new_text: "two\n\nblocks",
  });
  assert.equal(multiline.status, 400);
});

test("a plain prose edit still flows exactly as before (no regression)", async () => {
  const cap = {};
  const res = await post(envWith(cap), {
    source_ref: P1.source_ref, new_text: "Just an ordinary rewrite.",
  });
  assert.equal(res.status, 200);
  assert.equal(cap.input.kind, "prose");
  assert.equal(cap.input.op_arg ?? null, null);
});
