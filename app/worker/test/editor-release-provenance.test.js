import { test } from "node:test";
import assert from "node:assert/strict";
import { editorFetch } from "../src/editor.js";

const URL = "https://worker.example/edit/release-provenance";

test("release provenance exposes only the candidate-bound deployment SHA", async () => {
  const sha = "b".repeat(40);
  const response = await editorFetch(new Request(URL), { RELEASE_SHA: sha }, {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("X-Release-SHA"), sha);
  assert.match(response.headers.get("Cache-Control"), /no-store/);
});

test("release provenance fails closed without a valid deployment SHA", async () => {
  for (const RELEASE_SHA of [undefined, "ambient-head", "b".repeat(39)]) {
    const response = await editorFetch(new Request(URL), { RELEASE_SHA }, {});
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("X-Release-SHA"), null);
  }
});
