# Worker / LLM Implementation Facts

*Produced 2026-07-17 by the deepen-plan pass (claude-api skill applied). Numbers-first build contract for the Phase 4 Worker. Companion to the Cloudflare guidance embedded in the plan.*

## 1. Model + pricing (Haiku)

- **Model ID:** `claude-haiku-4-5` (alias); full pinned ID `claude-haiku-4-5-20251001`.
- **Context:** 200K in / **64K max output**.
- **Per-MTok pricing:** input $1.00 · output $5.00 · cache read $0.10 (0.1×) · cache write 5-min TTL $1.25 (1.25×) · cache write 1-h TTL $2.00 (don't use 1h here).

## 2. Prompt caching — the load-bearing correction

- **⚠️ Haiku 4.5's minimum cacheable prefix is 4096 tokens.** A ~3k persona system prompt **silently never caches** (`cache_creation_input_tokens: 0`, no error).
- **Fix:** structure the system prompt as **shared boilerplate first** (disclosure-tier rules, deflection/knowledge-boundary instructions, anti-sycophancy clause, Rule 4.2 handling, format rules — identical across personas), then persona-specific content; pad the stable prefix to **≥4096 tokens**. The shared block caches once and reuses across every persona/session.
- **Second cache breakpoint on the conversation:** add `cache_control` on the last content block of the most-recent turn — cuts cumulative history cost ~10× (~$0.063 → ~$0.006/session). 5-min TTL survives interview pacing; refreshed on each read.
- Cache stays valid as `messages` grows (prefix match). Breakers: editing the system prompt mid-session; any timestamp/UUID/unsorted-JSON in the prompt. Keep persona injection **byte-stable**.
- **Assert in the red-team harness:** `usage.cache_read_input_tokens > 0` from turn 2 on; if 0, the prefix is under the floor.

## 3. Session cost model (3k→4k+ persona prompt, 20 turns, ~80-tok user msgs, max_tokens 300)

| Scenario | Per session |
|---|---|
| Uncached (prompt under the floor) | ≈ $0.164 |
| Cached (prefix ≥4096 + history breakpoint) | ≈ $0.055 |

- Sessions per $7: ≈43 uncached / **≈127 cached**.
- Two-professor 30-min demo (4–10 sessions): $0.22–$0.55 cached — the $3 reserve is ample.
- `/critique` (4k memo + 2k rubric in, 1.5k out): **≈ $0.014** each (don't bother caching — one-shot, memo varies).
- `/debrief` (20-turn transcript ~7.6k + tiers ~3k + scoring ~1k in, ~1.75k out): **≈ $0.020** each.
- **Full session + debrief + one critique: ~$0.09 cached / ~$0.20 uncached.** All draw the same daily counter.

## 4. Streaming — ship NON-streaming tonight

- 300-token Haiku replies complete in ~2–4s; no timeout risk at `max_tokens: 300`. Non-streaming keeps the Worker simple and lets the spend counter read `usage` from one JSON response. Client shows a typing indicator.
- Streaming is a clean fast-follow (TransformStream sniffing the terminal `message_delta` for `usage`), promoted only if the D7 rehearsal feels slow.

## 5. Direct-browser BYOK header (documented-future, not built tonight)

- Header `anthropic-dangerous-direct-browser-access: true` (SDK: `dangerouslyAllowBrowser: true`) permits browser→Anthropic calls. Key + prompts are client-visible; no server caps possible. Documented as a future convenience tier only — the shipped BYOK story is deploy-your-own-Worker.

## 6. Error handling (Worker-side; raw fetch does NOT auto-retry)

1. **429** — read `retry-after`, wait (cap ~1–2s), retry once.
2. **529/500** — one retry, 500ms–1s backoff + jitter.
3. Retry fails → in-character "bad phone connection"; **no turn burned, no spend recorded**; metadata-only log.
4. **400/401/403** — never retry, never in-character; these are config bugs — log and alert.
