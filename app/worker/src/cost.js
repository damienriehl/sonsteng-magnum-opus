// cost.js — PURE token-cost math (no cloudflare imports), so it is unit-testable
// under node and shared by the BudgetCounter DO.
//
// Haiku 4.5 pricing, in CENTS per million tokens (docs/research/worker-llm-facts.md §1):
//   input             $1.00/MTok  = 100 cents/MTok
//   output            $5.00/MTok  = 500 cents/MTok
//   cache read        $0.10/MTok  =  10 cents/MTok   (cache_read_input_tokens)
//   cache write 5-min $1.25/MTok  = 125 cents/MTok   (cache_creation_input_tokens)
//
// Anthropic's `usage.input_tokens` already EXCLUDES cached tokens; cache creation
// and cache read are reported separately and billed at their own rates.

export const CENTS_PER_MTOK = {
  input: 100,
  output: 500,
  cache_read: 10,
  cache_write: 125,
};

// Actual cost of one call, in whole cents (rounded up), from normalized usage.
// Google reports billable thinking separately as thought_tokens; it uses the
// output rate. Missing fields count as 0, so non-cached calls still price correctly.
export function centsForUsage(usage) {
  const u = usage || {};
  const input = u.input_tokens || 0;
  const output = u.output_tokens || 0;
  const thoughts = u.thought_tokens || 0;
  const cacheWrite = u.cache_creation_input_tokens || 0;
  const cacheRead = u.cache_read_input_tokens || 0;
  const micro =
    input * CENTS_PER_MTOK.input +
    (output + thoughts) * CENTS_PER_MTOK.output +
    cacheWrite * CENTS_PER_MTOK.cache_write +
    cacheRead * CENTS_PER_MTOK.cache_read;
  return Math.ceil(micro / 1_000_000);
}

// Worst-case reservation for a call BEFORE it runs: assume the whole input is
// uncached (the priciest input rate) and the model emits its full max_tokens.
// preflight adds this to the pool so concurrent requests can't over-serve; settle
// reconciles to the real cost. inputTokensEstimate is a server-side upper bound.
export function worstCaseReserveCents(inputTokensEstimate, maxOutputTokens) {
  const micro =
    (inputTokensEstimate || 0) * CENTS_PER_MTOK.input +
    (maxOutputTokens || 0) * CENTS_PER_MTOK.output;
  return Math.ceil(micro / 1_000_000);
}
