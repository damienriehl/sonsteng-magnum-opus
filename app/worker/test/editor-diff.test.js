import { test } from "node:test";
import assert from "node:assert/strict";

import {
  atomicProseDiff,
  renderAtomicSegments,
} from "../src/editor-diff.js";

const meta = {
  source_ref: "data/taxonomy/tasks.json#description",
  source_revision: "dev-abc",
  prod_base: "prod-123",
};

test("separated word and punctuation edits stay separate and preserve the middle", async () => {
  const diff = await atomicProseDiff(
    "Weigh both sides' strong points, and weak points.",
    "Weigh both sides' strongest points, and weak points!",
    meta,
  );

  assert.equal(diff.operations.length, 2);
  assert.deepEqual(diff.operations.map((op) => [op.old_text, op.new_text]), [
    ["strong", "strongest"],
    [".", "!"],
  ]);
  assert.equal(diff.operations[0].kind, "replace");
  assert.deepEqual(diff.operations[0].refinement, [
    { type: "equal", text: "strong" },
    { type: "insert", text: "est" },
  ]);
  assert.match(diff.segments.map((segment) => segment.text).join(""), /points, and weak points/);
});

test("punctuation, capitalization, and whitespace are independently reviewable", async () => {
  const punctuation = await atomicProseDiff("Wait, John.", "Wait; John!", meta);
  assert.deepEqual(punctuation.operations.map((op) => [op.old_text, op.new_text]), [[",", ";"], [".", "!"]]);

  const capitalization = await atomicProseDiff("Strong point", "strong point", meta);
  assert.deepEqual(capitalization.operations[0].refinement, [
    { type: "delete", text: "S" },
    { type: "insert", text: "s" },
    { type: "equal", text: "trong" },
  ]);

  const whitespace = await atomicProseDiff("one two", "one  two", meta);
  assert.deepEqual(whitespace.operations.map((op) => [op.old_text, op.new_text]), [[" ", "  "]]);
});

test("phrase, sentence, insertion, and deletion changes form meaningful operations", async () => {
  const phrase = await atomicProseDiff("The quick fox rests.", "The very swift fox rests.", meta);
  assert.deepEqual(phrase.operations.map((op) => [op.kind, op.old_text, op.new_text]), [
    ["replace", "quick", "very swift"],
  ]);

  const sentence = await atomicProseDiff("Keep this. Remove this sentence.", "Keep this. Add this sentence!", meta);
  assert.equal(sentence.operations.length, 2);
  assert.deepEqual(sentence.operations.map((op) => op.kind), ["replace", "replace"]);

  assert.equal((await atomicProseDiff("alpha", "alpha beta", meta)).operations[0].kind, "insert");
  assert.equal((await atomicProseDiff("alpha beta", "alpha", meta)).operations[0].kind, "delete");
});

test("operation identity binds immutable source metadata and is byte deterministic", async () => {
  const first = await atomicProseDiff("old text", "new text", meta);
  const second = await atomicProseDiff("old text", "new text", { ...meta });
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.match(first.operations[0].id, /^op_[0-9a-f]{64}$/);

  const changedRevision = await atomicProseDiff("old text", "new text", { ...meta, source_revision: "dev-def" });
  assert.notEqual(first.operations[0].id, changedRevision.operations[0].id);
});

test("context is bounded while anchors retain exact placement evidence", async () => {
  const left = Array.from({ length: 60 }, (_, index) => `left${index}`).join(" ");
  const right = Array.from({ length: 60 }, (_, index) => `right${index}`).join(" ");
  const diff = await atomicProseDiff(`${left} old ${right}`, `${left} new ${right}`, meta, { context_tokens: 4 });
  const [operation] = diff.operations;
  assert.ok(operation.context_before.length <= 4);
  assert.ok(operation.context_after.length <= 4);
  assert.equal(operation.base_range[0] < operation.base_range[1], true);
});

test("exact distinctive moves pair conservatively; short, repeated, and edited text do not", async () => {
  const moved = "distinctive amber phrase travels";
  const exact = await atomicProseDiff(
    `alpha beta gamma delta epsilon zeta. ${moved}`,
    `${moved} alpha beta gamma delta epsilon zeta.`,
    meta,
  );
  const pair = exact.operations.filter((op) => op.move_pair_id);
  assert.equal(pair.length, 2);
  assert.equal(pair[0].move_pair_id, pair[1].move_pair_id);
  assert.deepEqual(new Set(pair.map((op) => op.move_role)), new Set(["from", "to"]));

  assert.equal((await atomicProseDiff("A short phrase ends.", "Ends. A short phrase", meta)).operations.some((op) => op.move_pair_id), false);
  assert.equal((await atomicProseDiff("rare amber phrase rare amber phrase X", "X rare amber phrase rare amber phrase", meta)).operations.some((op) => op.move_pair_id), false);
  assert.equal((await atomicProseDiff("distinctive amber phrase travels X", "X distinctive amber wording travels", meta)).operations.some((op) => op.move_pair_id), false);
});

test("hostile Unicode stays structured and renderer escapes all text", async () => {
  const hostile = "<img src=x onerror=alert(1)> \u202E e\u0301 👩🏽‍⚖️";
  const diff = await atomicProseDiff("safe", hostile, meta);
  const html = renderAtomicSegments(diff.segments);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /<del aria-label="Deleted text">/);
  assert.match(html, /<ins aria-label="Added text">/);
  assert.match(diff.operations[0].new_text, /\u202E/);
  assert.match(diff.operations[0].new_text.normalize("NFC"), /é/);
});

test("pathological input fails closed before unbounded work", async () => {
  await assert.rejects(
    () => atomicProseDiff("a", "x".repeat(40_000), meta),
    { name: "RangeError", message: /maximum supported length/ },
  );
});
