// Error-envelope shape + in_character policy.
import { test } from "node:test";
import assert from "node:assert/strict";
import { errorEnvelope, ERROR_CODES, IN_CHARACTER } from "../src/errors.js";

async function bodyOf(res) {
  return JSON.parse(await res.text());
}

test("every envelope has {error:{code,message}} and the given status", async () => {
  const res = errorEnvelope("validation_error", "bad input", 400);
  assert.equal(res.status, 400);
  const b = await bodyOf(res);
  assert.equal(b.error.code, "validation_error");
  assert.equal(b.error.message, "bad input");
  assert.equal(b.error.in_character, undefined); // validation_error has no in_character
});

test("cap_exceeded / turn_limit / upstream_unavailable carry an in_character line", async () => {
  for (const code of ["cap_exceeded", "turn_limit", "upstream_unavailable"]) {
    const b = await bodyOf(errorEnvelope(code, "x", 429));
    assert.equal(typeof b.error.in_character, "string");
    assert.ok(b.error.in_character.length > 0);
    assert.equal(b.error.in_character, IN_CHARACTER[code]);
  }
});

test("all codes used by the Worker are in the documented set", () => {
  for (const code of [
    "cap_exceeded", "turn_limit", "rate_limited", "validation_error",
    "upstream_unavailable", "origin_forbidden", "session_invalid", "no_hosted_key",
  ]) {
    assert.ok(ERROR_CODES.has(code), code + " must be a documented code");
  }
});

test("an explicit extra.in_character overrides the default", async () => {
  const b = await bodyOf(errorEnvelope("upstream_unavailable", "x", 503, { in_character: "custom line" }));
  assert.equal(b.error.in_character, "custom line");
});

test("no_hosted_key envelope has the exact required message shape", async () => {
  const res = errorEnvelope(
    "no_hosted_key",
    "This deployment has no hosted demo key. Add your own API key to interview the client.",
    503
  );
  assert.equal(res.status, 503);
  const b = JSON.parse(await res.text());
  assert.equal(b.error.code, "no_hosted_key");
  assert.match(b.error.message, /no hosted demo key/i);
});
