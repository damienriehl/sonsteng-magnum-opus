// Budget cost math — pure cents calc incl. all cache token types.
import { test } from "node:test";
import assert from "node:assert/strict";
import { centsForUsage, worstCaseReserveCents, CENTS_PER_MTOK } from "../src/cost.js";

test("rates match Haiku 4.5 pricing (cents per MTok)", () => {
  assert.deepEqual(CENTS_PER_MTOK, { input: 100, output: 500, cache_read: 10, cache_write: 125 });
});

test("plain input+output priced and rounded up", () => {
  // 1,000,000 input @100 + 1,000,000 output @500 = 600 cents exactly.
  assert.equal(centsForUsage({ input_tokens: 1_000_000, output_tokens: 1_000_000 }), 600);
  // Sub-cent usage rounds UP to 1 cent (never under-bill).
  assert.equal(centsForUsage({ input_tokens: 1 }), 1);
  assert.equal(centsForUsage({}), 0);
});

test("cache_creation billed at 125 and cache_read at 10 cents/MTok", () => {
  // Only cache write: 1,000,000 @125 = 125 cents.
  assert.equal(centsForUsage({ cache_creation_input_tokens: 1_000_000 }), 125);
  // Only cache read: 1,000,000 @10 = 10 cents.
  assert.equal(centsForUsage({ cache_read_input_tokens: 1_000_000 }), 10);
  // Mixed: 500k in(50) + 300 out(0.15) + 400k cwrite(50) + 2M cread(20) = 120.15 -> ceil 121.
  const cents = centsForUsage({
    input_tokens: 500_000,
    output_tokens: 300,
    cache_creation_input_tokens: 400_000,
    cache_read_input_tokens: 2_000_000,
  });
  assert.equal(cents, 121);
});

test("a cached interview turn is far cheaper than an uncached one", () => {
  // Scaled so cent-rounding doesn't collapse the two; a 5100-token prefix served
  // from cache (@10) instead of full input (@100) is the ~10x saving.
  const uncached = centsForUsage({ input_tokens: 520_000, output_tokens: 25_000 });
  const cached = centsForUsage({ input_tokens: 9_000, output_tokens: 25_000, cache_read_input_tokens: 510_000 });
  assert.ok(cached < uncached, "cache_read should reduce cost");
});

test("worst-case reserve assumes uncached input + full output", () => {
  // 5000 input @100 + 300 output @500 = 0.5 + 0.15 = 0.65 cents -> ceil 1.
  assert.equal(worstCaseReserveCents(5000, 300), 1);
  // Bigger: 50,000 input @100 + 1500 out @500 = 5 + 0.75 = 5.75 -> ceil 6.
  assert.equal(worstCaseReserveCents(50_000, 1500), 6);
});
