# Worker API — Contract v1

Authoritative request/response contract for the Sonsteng client-interview
Worker (`app/worker/`). The chat/critique UI is built against this exact
contract. **Do not deviate without versioning.** Base path: `/v1`.

- Model, system prompt, and `max_tokens` are **always server-built**; the client
  can never supply `model`, `system`, `max_tokens`, or `tools`. Client input is
  restricted to the whitelisted fields below.
- **CORS:** every response — success *and* error — carries
  `Access-Control-Allow-Origin` echoing the matched allowlisted origin (plus
  `Vary: Origin`). A request from a non-allowlisted origin gets a bare `403`
  with **no** ACAO (the browser blocks it — the allowlist working).
- **Non-streaming.** Each response is a single JSON body.

---

## GET /v1/session

Mint a signed, stateless session token.

**Query:** `?bypass=<token>` (optional) — the demo bypass. Compared with
`crypto.subtle.timingSafeEqual`; a valid bypass yields pool `"demo"` and skips
the per-IP mint ceiling (for the two professors). Scrubbed from all logs.

**200:**
```json
{ "session_token": "<hmac-signed>", "sid": "<uuid>", "pool": "public", "max_turns": 20 }
```
- `pool`: `"public"` | `"demo"` (signed into the token; the client cannot upgrade
  itself to the demo reserve).
- Mint is throttled: global `MAX_SESSIONS_PER_DAY` (DO `mints` table) **and** a
  per-IP issuance ceiling (~20/day, HMAC-of-IP counter). Over either →
  `429 rate_limited` (bypass is exempt from the per-IP ceiling).

The token payload is `{sid, d, p}` signed HMAC-SHA256. The **turn count is never
in the token** — the authoritative counter lives in the BudgetCounter DO keyed by
`sid`. Signed token = identity; DO = enforcement. This is the NAT-safe primary
limit: caps key off `sid`, so students behind one classroom NAT each get their
own budget.

---

## POST /v1/chat

One interview turn (user message + client reply).

**Request (whitelisted fields only):**
```json
{
  "session_token": "<token>",
  "matter_id": "m03",
  "persona_id": "m03.per.petimeyer",
  "turn_id": "<client idempotency key>",
  "messages": [ { "role": "user", "content": "..." }, { "role": "assistant", "content": "..." } ]
}
```
- `messages`: `role` ∈ `user|assistant`, `content` a string. Server caps: per
  message ≤4000 chars, ≤60 messages, ≤24000 total chars (else `validation_error`).
- `turn_id`: replaying the **same** `turn_id` returns the stored result **without
  re-billing** (DO dedupe; retries never double-count).

**200:**
```json
{
  "reply": "…",
  "turn": 7,
  "remaining": 13,
  "state": "active",
  "usage": { "input_tokens": 92, "output_tokens": 143, "cache_read_input_tokens": 5148 }
}
```
- `state`: `"active"` (turn < 15) · `"warning"` (turn ≥ 15) · `"ended"` (turn ≥ 20,
  server-confirmed). The client drives the cap banner/lockout only from server
  `state` — never client-side prediction. `cache_read_input_tokens > 0` from turn 2.

**Errors:** `cap_exceeded` (429), `turn_limit` (429), `upstream_unavailable`
(503; retry-once then in-character "bad phone connection", **no turn burned**),
`session_invalid` (401), `validation_error` (400).

---

## POST /v1/debrief

Two-axis debrief scorecard for a completed interview.

**Request:**
```json
{ "session_token": "<token>", "matter_id": "m03", "persona_id": "m03.per.petimeyer",
  "transcript": [ { "role": "user", "content": "…" }, { "role": "assistant", "content": "…" } ] }
```

**Debrief-oracle guard:** the `sid` must have **≥6 committed turns for this
persona** (DO check) — else `session_invalid` (403). Missed items are named by
**topic label only** (never the fact text of un-elicited concealed/rapport-gated
facts). The out-of-band `fact_map` supplies the labels.

**200:** `{ "scorecard": <debrief scorecard JSON> }` — validates against
`data/schemas/debrief.scorecard.schema.json`; a malformed model response →
`validation_error` (502), never raw text.

---

## POST /v1/critique

Rubric-based first-pass critique of a pasted deliverable.

**Request:**
```json
{ "session_token": "<token>", "matter_id": "m03", "deliverable_text": "…memo text…" }
```
- Server-side size cap **~18,000 chars** → `413 validation_error` above it.
- The matter's rubric is loaded server-side (bundled), never sent by the client.

**200:**
```json
{
  "scorecard": { "…": "critique scorecard JSON…" },
  "criteria_labels": { "m03.rub.c01": "Case theory and liability analysis", "m03.rub.c03.s01": "Rapport and opening" }
}
```
- `scorecard` validates against `data/schemas/critique.scorecard.schema.json`.
- `criteria_labels` (backward-compatible addition): `{criterion_id: name}` for
  every criterion **and** subcriterion in the matter's bundled rubric, so the UI
  can render real criterion names instead of "Criterion NN". Ids match the
  scorecard's `criteria[].criterion_id` values.

---

## Error envelope (every non-200)

Always JSON, always with CORS headers (for allowlisted origins):
```json
{ "error": { "code": "cap_exceeded", "message": "…", "in_character": "…optional…" } }
```
- `code` ∈ `cap_exceeded` | `turn_limit` | `rate_limited` | `validation_error` |
  `upstream_unavailable` | `origin_forbidden` | `session_invalid`.
- `in_character` is supplied for `cap_exceeded` / `turn_limit` /
  `upstream_unavailable` (the UI renders the in-character line, e.g. the
  bad-phone-connection message, instead of a raw error).

---

## Config (wrangler.jsonc `vars`) + secrets

`vars`: `ALLOWED_ORIGINS`, `ANTHROPIC_MODEL` (`claude-haiku-4-5`),
`PUBLIC_BUDGET_USD` (7), `DEMO_RESERVE_USD` (3), `MAX_TURNS` (20),
`MAX_SESSIONS_PER_DAY` (200).

Secrets (set with `wrangler secret put`, never in source): `ANTHROPIC_API_KEY`
(gated on Damien), `SESSION_SIGNING_KEY`, `DEMO_BYPASS_TOKEN`.

## Privacy / logging

Transcripts are never stored server-side (browser-only). The Worker logs
**metadata only** — HMAC-of-IP (server-salted), token counts, timestamps, event
names — never message content and never query strings (the bypass token).
Conversations transit Anthropic's API (disclosed to users).

## Build artifact

`personas/personas.generated.json` is produced by
`tools/build_worker_personas.py` from `data/matters/*/personas/*.json` +
`facts.md` (+ the m00 fixture). It holds Segment A (verbatim), the
debrief/critique templates, injection-only persona fields, the out-of-band
`fact_map` (topic labels), and matter rubrics. **Server-only** — never shipped as
a public/static asset (it contains concealed facts). Re-run it after matter
content changes.
