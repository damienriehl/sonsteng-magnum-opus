# Worker API — Contract v1

Authoritative request/response contract for the Sonsteng client-interview
Worker (`app/worker/`). The chat/critique UI is built against this exact
contract. **Do not deviate without versioning.** Base path: `/v1`.

- System prompt and `max_tokens` are **always server-built**; the client can
  never supply `system`, `max_tokens`, or `tools`. Client input is restricted to
  the whitelisted fields below. The one client-controlled upstream input is the
  optional **`byok`** block (below): their provider, **their** key, and a model
  restricted to a per-provider allowlist.
- **CORS:** every response — success *and* error — carries
  `Access-Control-Allow-Origin` echoing the matched allowlisted origin (plus
  `Vary: Origin`). A request from a non-allowlisted origin gets a bare `403`
  with **no** ACAO (the browser blocks it — the allowlist working).
- **Response framing.** Responses are a single JSON body except for
  `POST /v1/chat` when the deployment-only `STREAMING` flag is enabled. In
  that case the same v1 client request may receive normalized SSE for any
  supported provider. The response declares `text/event-stream` and
  `x-sonsteng-stream: 1`; clients must branch on either header. Streaming is
  disabled by default and is enabled on DEV only while provider validation is
  incomplete.

The normalized SSE event contract is provider-independent:

- `delta` carries `{ "text": "..." }`.
- `done` carries the same successful reply payload as the JSON path, after
  terminal provider usage and server-side budget settlement complete.
- `error` carries the ordinary typed error envelope. A transport, terminal
  frame, or settlement failure emits no `done` and stores no partial replay.

### Live DEV streaming verification

`test/live-stream-smoke.mjs` verifies one real DEV turn without printing reply
text or credentials. Run it separately for `anthropic`, `openai`, and `google`.
For each provider it requires normalized SSE (`x-sonsteng-stream: 1`), one or
more `delta` events, exactly one `done`, canonical token usage, nonempty output,
and a structurally identical settled JSON replay from the same `turn_id`. It
also requires a delta to arrive in an earlier body read than `done`, so a fully
buffered SSE transcript does not count as live streaming. Provider errors,
early EOF, malformed frames, redirects, or credential reflection fail closed.
The command-line harness accepts only the approved DEV Worker origin shown
below; non-DEV and localhost origins are available only to the unit-test API.

Load a protected environment file (mode 0600) before invoking the harness:

```bash
cd app/worker
set -a
. ~/.secrets/sonsteng-stream-smoke.env
set +a
WORKER_URL=https://sonsteng-chat.damienriehl.workers.dev PROVIDER=anthropic node test/live-stream-smoke.mjs
```

The protected environment may set `API_KEY` or the selected provider's
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`; it may also set the
optional `DEMO_BYPASS_TOKEN`. Set only one applicable API-key variable. The
bypass is sent through the documented `GET /v1/session?bypass=…` contract and
is never included in harness output.

Alternatively, set `CREDENTIALS_FILE` to a regular mode-0600 JSON file, or set
`CREDENTIALS_STDIN=1` and supply the same JSON object on standard input from a
secret manager:

```json
{ "api_key": "<provider key>", "demo_bypass_token": "<optional bypass>" }
```

Do not put credentials directly in command arguments. The harness emits only a
bounded JSON receipt (provider, event/output counts, normalized usage, and
replay result); it never emits response text, session tokens, or secret values.

---

## BYOK — bring your own key (provider-agnostic)

`POST /v1/chat`, `/v1/debrief`, `/v1/critique`, and `/v1/memo-assessment` MAY include:

```json
{ "byok": { "provider": "anthropic", "api_key": "sk-…", "model": "claude-haiku-4-5" } }
```

- `provider` ∈ `"anthropic"` | `"openai"` | `"google"`.
- `model` (optional) must be in that provider's **allowlist**; omitted → the
  provider's default. Defaults & allowlists are deploy config (`wrangler.jsonc`
  vars), currently:

  | provider  | default            | allowlist                                              |
  |-----------|--------------------|--------------------------------------------------------|
  | anthropic | `claude-haiku-4-5` | `claude-haiku-4-5`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-5` |
  | openai    | `gpt-4o-mini`      | `gpt-4o-mini`, `gpt-4o`                                |
  | google    | `gemini-2.0-flash` | `gemini-2.0-flash`, `gemini-2.5-flash`                 |

- **When `byok` is present:** the Worker calls that provider with the user's
  key and **skips the hosted spend counter entirely** (their money). Everything
  else still applies: session validity, turn caps + `turn_id` dedupe, input-size
  caps, server-side persona injection, and the ≥6-turn debrief guard.
- **The key is never stored and never logged.** It exists only for the lifetime
  of the request (forwarded in the provider's auth **header** — never in a URL);
  no log line, metric, or error body contains it (enforced by a unit test that
  scans every logging call site).
- A provider 4xx on a BYOK call (bad key, unavailable model) returns a plain
  `validation_error` naming the provider + HTTP status — never in-character.
- **When `byok` is absent:** the hosted demo pool is used as documented — but if
  the deployment has no `ANTHROPIC_API_KEY` secret (the hosted pool is dormant),
  every such request returns:

```json
{ "error": { "code": "no_hosted_key", "message": "This deployment has no hosted demo key. Add your own API key to interview the client." } }
```

- Prompt caching (Segment-A prefix + history breakpoint) applies **only** on the
  anthropic path; OpenAI/Gemini requests send the same server-built prompt
  without cache directives (any provider-side automatic caching is surfaced in
  the normalized `usage`).
- Evaluator calls (`/v1/debrief`, `/v1/critique`) request native JSON output
  where the provider supports it: OpenAI `response_format: json_object`, Gemini
  `responseMimeType: application/json`; Anthropic relies on the prompt contract.
  All providers' outputs are still schema-validated before reaching the client.

---

## GET /v1/session

Mint a signed, stateless session token.

**Query:** `?bypass=<token>` (optional) — the demo bypass. Compared with
`crypto.subtle.timingSafeEqual`; a valid bypass yields pool `"demo"` and skips
the per-IP mint ceiling (for the two professors). Scrubbed from all logs.

**Query:** `?cf_ts=<turnstile-token>` — the Cloudflare Turnstile token from the
managed widget rendered client-side (WP6 bot-gate). The Worker calls Turnstile
`siteverify` **before** minting; a missing/invalid token → `403 turnstile_failed`
(retryable — the client reloads to re-run the widget). **Skipped** (no
`siteverify` call) when (a) a valid `?bypass` is present — keyless demo/professor
flows never see a challenge — or (b) the gate is disabled via
`TURNSTILE_ENABLED="false"`. Like `bypass`, the token is query-only and scrubbed
from logs. A widget that fails to load client-side degrades to the retryable
`turnstile_failed` prompt, never a hard brick.

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
`session_invalid` (401), `validation_error` (400), `no_hosted_key` (503; no
`byok` supplied and the deployment has no hosted key).

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

## POST /v1/memo-assessment

Formative-only panel assessment of the seven memo headings. A request includes
`session_token`, `deliverable_text`, and either the ordinary `byok` record or a
`byok_panel` of supported provider records. The server always supplies the
canonical assessment instrument.

A true multi-provider panel is explicit and closed:

```json
{
  "session_token": "<session>",
  "deliverable_text": "<memo>",
  "byok_panel": [
    { "provider": "anthropic", "api_key": "<key>" },
    { "provider": "openai", "api_key": "<key>" },
    { "provider": "google", "api_key": "<key>" }
  ]
}
```

- `byok_panel` contains exactly three explicit credentials: one each for
  Anthropic, OpenAI, and Google. Duplicate providers fail.
- `byok_panel` and the ordinary single `byok` record are mutually exclusive.
- Hosted and ordinary single-BYOK requests run exactly one grader and return
  `reduced_assurance`; one credential is never fanned out into a synthetic panel.
- Hosted and BYOK requests share an atomic per-session/day request cap. The
  default is 20; exhaustion returns `429 rate_limited` before provider work.

The request MAY include a locally supplied threshold envelope:

```json
{
  "assessment_config": {
    "schema_version": "memo-assessment-threshold-config/v1",
    "school": {
      "id": "school:midstate-local-2026",
      "competence_score": 4,
      "redo_eligible_below": 6
    },
    "instructor": {
      "id": "instructor:john-local-2026",
      "competence_score": 5,
      "redo_eligible_below": 7
    }
  }
}
```

- The envelope and each supplied record are closed to unknown fields.
- Record IDs are stable local claim IDs. Both thresholds are integers 1–7 and
  `competence_score` cannot exceed `redo_eligible_below`.
- Resolution is deterministic: instructor > school > canonical default. A
  present invalid envelope returns `400 validation_error`; it never falls back.
- A selected school or instructor record is explicitly labelled locally
  supplied and unverified. It is not evidence of institutional authorization.

**200:**

```json
{
  "assessment": {
    "threshold_configuration": {
      "schema_version": "memo-assessment-threshold-resolution/v1",
      "source": "instructor",
      "source_id": "instructor:john-local-2026",
      "competence_score": 5,
      "redo_eligible_below": 7,
      "resolution": "instructor>school>default",
      "locally_supplied": true,
      "authority_status": "claimed_locally_supplied",
      "verified_institutional_authority": false,
      "version": "1.1.0",
      "content_hash": "sha256:…"
    }
  },
  "assessment_audit_id": "memo-assessment-…"
}
```

The complete resolved configuration is returned with the result and persisted
byte-equivalently in audit provenance. Under canonical defaults, score 4 is
competent and score 5 remains redo-eligible.

---

## Error envelope (every non-200)

Always JSON, always with CORS headers (for allowlisted origins):
```json
{ "error": { "code": "cap_exceeded", "message": "…", "in_character": "…optional…" } }
```
- `code` ∈ `cap_exceeded` | `turn_limit` | `rate_limited` | `validation_error` |
  `upstream_unavailable` | `origin_forbidden` | `session_invalid` |
  `no_hosted_key` | `turnstile_failed`.
- `turnstile_failed` (`GET /v1/session` only): the bot-gate could not verify the
  request — missing/invalid Turnstile token (`403`) or the gate is enabled but
  its secret is unset (`503`, deploy error). Always **retryable**: the client
  reloads to re-run the widget.
- `in_character` is supplied for `cap_exceeded` / `turn_limit` /
  `upstream_unavailable` (the UI renders the in-character line, e.g. the
  bad-phone-connection message, instead of a raw error).

---

## Config (wrangler.jsonc `vars`) + secrets

`vars`: `ALLOWED_ORIGINS`, `PUBLIC_BUDGET_USD` (7), `DEMO_RESERVE_USD` (3),
`MAX_TURNS` (20), `MAX_SESSIONS_PER_DAY` (200),
`MAX_ASSESSMENTS_PER_SESSION_DAY` (20), plus the per-provider model
config `MODEL_DEFAULT_ANTHROPIC` / `MODEL_DEFAULT_OPENAI` /
`MODEL_DEFAULT_GOOGLE` and allowlists `MODEL_ALLOW_ANTHROPIC` /
`MODEL_ALLOW_OPENAI` / `MODEL_ALLOW_GOOGLE` (comma-separated; see the BYOK
section table). **Turnstile (WP6):** `TURNSTILE_ENABLED` (`"true"` default;
`"false"` disables the session-mint bot-gate) and `TURNSTILE_SITEKEY` (public;
mirrored in the chat page's `<meta name="turnstile-sitekey">`, which is what the
client actually reads).

Secrets (set with `wrangler secret put`, never in source): `SESSION_SIGNING_KEY`,
`DEMO_BYPASS_TOKEN`, `TURNSTILE_SECRET` (the Turnstile verification secret — the
gate rejects with `turnstile_failed` when enabled but unset), and **optionally**
`ANTHROPIC_API_KEY` (gated on Damien; while unset the hosted demo pool is dormant
and non-BYOK requests get `no_hosted_key`).

## Privacy / logging

Transcripts are never stored server-side (browser-only). The Worker logs
**metadata only** — HMAC-of-IP (server-salted), token counts, timestamps, event
names — never message content and never query strings (the bypass token).
Conversations transit Anthropic's API (disclosed to users).

---

# Editor API — `/edit/*` (Sonsteng Editor Experience)

A self-contained surface for the Worker-injected edit mode, the instructor view,
and the suggestion review/apply loop. It shares NOTHING with the chat path: its
own auth (opaque bookmark tokens → signed cookie), its own CORS allowlist (the
worker's edit origin ONLY), its own CSRF guard, and strict per-response security
headers. All state lives in the **EditorStore** Durable Object (SQLite, migration
tag **v2**, appended — v1/BudgetCounter is never altered).

## The map is the universal allowlist (P0 invariant)

Every client-influenced reference — the `/edit/<path>` proxy path, every
`source_ref`, every `json_path` — is validated **server-side** against the
generator-emitted `editor-map.generated.json` (public pages) or
`instructor-bundle.generated.json` (instructor docs), at **suggest AND apply
time**. Unknown → uniform `404` / `validation_error`, no upstream fetch, no file
path, no JSON write. Both bundles are copied into the Worker at build time
(`scripts/bundle-editor-data.mjs`, wired as wrangler's `build.command`) and are
**server-only** (the instructor bundle carries answer keys — never shipped to the
static site or the persona bundle).

## Auth, scopes, cookie (Decision 2)

- **Opaque bookmark token → scope record** `{ edit:{granted,ver}, instructor:{granted,ver}, admin:{granted,ver} }`.
  Tokens are deploy **secrets** (`EDIT_TOKEN_<SLOT>`, e.g. `EDIT_TOKEN_JOHN`,
  `EDIT_TOKEN_ROGER`, `EDIT_TOKEN_ADMIN`); the var `EDIT_TOKEN_SCOPES` (JSON) maps
  each slot to its granted scopes + per-scope versions:
  `{"john":{"edit":1,"instructor":1},"roger":{"edit":1,"instructor":1},"admin":{"admin":1}}`.
  Comparison is constant-time (digest-then-XOR, no short-circuit). **admin is
  reachable ONLY via the admin token** — never from an edit/instructor token.
- **Attribution labels:** the server-resolved identity is `slot:<name>`
  (`slot:john`, `slot:roger`). The admin review surface stamps a short human label
  onto each row via `attributionLabel()` (`editor-auth.js`): `john → "JOS"`,
  `roger → "RSH"`, unknown slots → the upper-cased slot name (never mis-attributed).
- **`?t=<opaque>` one-time exchange:** resolves the token, sets an HttpOnly
  cookie, then **302** to the clean URL (the `?t` is stripped so it never lands
  in logs/history). Cookie: `edit_scope=<hmac-signed slot+stamp>; HttpOnly;
  Secure; SameSite=Strict; Path=/edit` (Path=/edit so it reaches BOTH the pages
  and `/edit/v1/*`). **The raw token is never in the cookie** — only the slot name
  + a version stamp, HMAC-signed with `SESSION_SIGNING_KEY`.
- **Independent rotation:** scopes resolve per-request from `EDIT_TOKEN_SCOPES`;
  bumping a scope's version changes the slot stamp and invalidates every
  already-issued cookie for that slot (John re-clicks his magic link).

## CSRF + headers (every `/edit` response)

- **Mutations** (`POST suggest|decide|claim|finalize|reconcile`) require the
  custom header **`X-Edit-Request: 1`** AND a same-origin/absent `Origin` AND a
  `Sec-Fetch-Site` of `same-origin` when present. (A cross-site POST cannot set
  the custom header without a preflight our CORS never grants a foreign origin.)
- **CORS allowlist = the worker's edit origin ONLY** (`EDIT_ORIGIN`); credentials
  allowed; any other Origin gets no ACAO.
- Every response carries: `Content-Security-Policy: default-src 'none'; script-src
  'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src
  'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`,
  `Cache-Control: private, no-store`, `Vary: Cookie`, `Referrer-Policy:
  no-referrer`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.

## Proxy-injector — `GET /edit/<path>` (edit scope)

Resolves `<path>` against the page allowlist (unknown → uniform 404, no upstream).
Fetches `EDIT_UPSTREAM + page` as a **clean subrequest** (no cookie/authorization
forwarded, no `?t`, `redirect: "manual"`, `cf:{cacheEverything:false,cacheTtl:0}`;
upstream Set-Cookie/CSP/cache headers dropped). Injects at serve time: a
server-constant `<base href>` into `/edit` space, the worker-served
`editor.css`/`editor.js` (`<script src>`, never inline), this page's block map,
and John's pending items for the page — the last two as **escaped JSON islands**
(`<script type="application/json" id="editor-map-data">` /`id="edits-data"`;
`<`,`>`,`&`,`U+2028`,`U+2029` escaped so a `</script>`/XSS payload is inert).
Same-origin `<a href>`s are rewritten into `/edit` space (HTMLRewriter).

## Instructor view — `GET /edit/instructor/<matter>/<doc>` (instructor scope)

`<matter>` = `mNN`; `<doc>` ∈ `facts | instructor_notes | answer_key` (aliases:
`notes`, `answer-key`, `key`). Serves the pre-rendered HTML from the instructor
bundle (already carries `data-ebsrc` anchors), same injection + headers as the
proxy. **Uniform 404 for BOTH a missing doc AND insufficient scope** (no oracle).

## Assessment signer review (Access-authenticated `damienadmin`)

Every successful `POST /v1/memo-assessment` writes a reconstructable audit record
before returning. The response includes `assessment_audit_id`; persistence keeps
the submitted evidence, derived 1–7 section result, provider/config/instrument
provenance, summative blockers, and a declared retention period (30 days by
default, bounded to 1–365 by the store). Provider keys and the learner session
token exist only in the write's request-lifetime `credential_values` redaction
list and never in stored evidence, provenance, results, responses, or logs.

Assessment reads and overrides are deliberately narrower than general editor or
admin authority. Only the existing human `damienadmin` context, authenticated on
the Access hostname and currently holding both `admin` and `instructor`, is
mapped at the endpoint to the store's literal `assessment-review` capability.
Cookie, bearer, service, admin-token, John, and Roger contexts do not receive
that capability. Under-scoped and unknown-id probes return the same uniform 404.

- `GET /edit/assessments/<assessment_audit_id>` renders the section results,
  provider/config/instrument provenance, raw evidence, and attributed override
  history together. It states at the point of reading that 4 is competent and 5
  remains redo-eligible under the default below-6 rule. The view never performs
  a letter translation.
- `GET /edit/v1/assessment?id=<assessment_audit_id>` returns the same protected
  audit record as JSON.
- `POST /edit/v1/assessment-override` is CSRF-guarded and accepts
  `{id, assessment_id, heading_id, score, note}`. The heading must be one of the
  canonical seven, score must be an integer 1–7, and the reason is required.
  `author` is ignored; the endpoint stamps the current server-resolved identity
  and the store stamps server time. Overrides are append-only and idempotent by
  `id`.

## `POST /edit/v1/suggest` (edit OR instructor scope)

CSRF-guarded. Body (whitelisted):

```json
{ "id": "<client uuid>", "source_ref": "data/…#locator", "json_path": "…?",
  "new_text": "plain-text intent", "comment": "…?", "original_hash": "…?" }
```

- `id`: client uuid (idempotency — a replay returns the stored row, never a second
  insert). `[A-Za-z0-9_-]{8,64}`.
- `source_ref` MUST resolve in the caller's scope map (else `validation_error`).
  `json_scalar` blocks: `json_path` must equal the map's `json_path` (no forgery).
- **`editor` and `original_text` are resolved SERVER-side** (from the auth slot +
  the map) — never read from the client. `original_hash`, `kind`, `page`,
  `json_path`, `context`, `map_version` are taken from the map.
- If the client sends `original_hash` and it disagrees with the map → `409
  stale_page` ("reload"). Size cap `EDIT_MAX_BYTES` (16 KB) → graceful `413`.
- Ceilings: per-editor pending (`EDIT_MAX_PENDING_PER_EDITOR`, 200), daily
  (`EDIT_MAX_DAILY_PER_EDITOR`, 500), global pending, all → `429`.
- **200:** `{ "ok": true, "id": "…", "status": "pending", "replay": false }`.

## `GET /edit/v1/pending?page=` (edit/instructor scope)

This editor's own items (non-superseded), for inline closure. `{ ok, items: [...] }`.

## `GET /edit/v1/review` (admin) · `GET /edit/review` (admin, HTML)

All outstanding suggestions (`pending|drift|needs_human|accepted_blocked|accepted|
in_flight`) grouped by `source_ref` — the cumulative "all-pending" digest. The
HTML page renders word-level diffs **text-node-only** (never `innerHTML`) from an
escaped island, with per-item / per-group / bulk Accept-Decline, a drift
re-anchor action, and a decline-note field.

## `POST /edit/v1/decide` (admin)

CSRF-guarded. `{ "action": "accept|decline|reanchor", "id": "…" | "group_id": "…",
"note": "…?" }`. **decide is the SOLE writer of `accepted`.** Group accept = one
atomic txn (pass `group_id`); a **lone-member-of-group accept is rejected**
(`409 group_accept_required`). `reanchor` moves `drift → pending` (forces
re-review — never straight to accepted).

## `GET /edit/v1/digest` (admin)

`{ ok, digest: { by_status, pending_by_source, generated_at } }`. Admin-only.

## Apply-engine RPCs — `POST /edit/v1/{claim,finalize,reconcile}` (admin/service)

The `tools/apply_suggestions.py` loop drives these (admin token = service scope).
- `claim` `{ batch_id, base_sha?, ids? }` → `{ ..., prod_base }` and `accepted → in_flight` for **whole
  groups only** (never partially), stamps a **lease** + `apply_batch_id`, opens
  the `apply_batches` journal at phase `claimed`. `prod_base` is the candidate
  SHA of the latest completed production release, selected by the Worker; it is
  `null` before a production frontier exists and is never inferred from DEV
  `base_sha`. In that bootstrap state normal apply omits review revisions and
  the Publisher remains fail-closed pending the explicit trusted backfill.
- `finalize` `{ batch_id, phase, applied?, accepted_blocked?, needs_human?,
  drift?, commit_sha?, generator_id?, review_revisions? }` → journals the phase and resolves the
  batch's `in_flight` rows. The terminal `done` call records the exact canonical
  commit and content identity of the authoritative generator entrypoints plus
  their complete transitive local-Python dependency closure. The normal apply
  client attaches deterministic per-source atomic review evidence in the same
  transaction, so successfully applied DEV copy is immediately available to
  the Publisher without a migration backfill.
- `reconcile` → startup crash recovery: expired-lease batches pre-`merged` roll
  `in_flight → accepted` (re-queue) + phase `rolled_back`; post-`merged` complete
  `in_flight → applied`. Orphan `in_flight` (expired lease, no live batch) → back
  to `accepted`. No limbo. (Also run automatically in the DO constructor.)

## Status machine

`pending → superseded⛔` (same editor re-edits `source_ref`) · `pending →
declined⛔ / accepted` (decide, the sole `accepted` writer) · `pending/accepted →
drift` · `accepted → in_flight` (claim) · `in_flight → applied⛔ | accepted_blocked
| drift | needs_human | accepted` · `accepted_blocked → accepted / declined⛔` ·
`drift → pending` (re-anchor) · `needs_human → applied⛔ / accepted / declined⛔`.
Terminal (⛔): `superseded`, `declined`, `applied`.

`applied` is terminal only for the DEV apply lifecycle. It means canonical + DEV
application, surfaced as **Available on DEV — waiting for Publisher** until it is
included in a verified production release. Production state is a separate
immutable ledger; approval and `DIRECT_APPLY` never authorize PROD.

## Production release API (`/edit/v1/prod/releases/*`)

These routes exist only with `EDIT_ENVIRONMENT=production` and are same-origin
CSRF guarded. `GET /frontier` and `POST /prepare`, `/claim`, `/renew`, and `/transition` require the trusted
release service channel: a bearer credential with the dedicated `release_service`
scope and a separate `EDIT_TOKEN_RELEASE` secret. An admin or DEV apply-daemon
bearer is insufficient. The
service may freeze evidence and execute an already-authorized release, but it
cannot authorize one.

`POST /renew` extends an unexpired execution lease only for its current fencing
token. The executor heartbeats around every bounded provider operation; a lost
renewal, expired lease, or stale token blocks later provider work and ledger
transitions so a failover cannot overlap the original executor.
Before a provider call, that lease covers the adapter's complete worst-case
command sequence plus margin. Production leases are capped at 15 minutes, so
heartbeat loss cannot create overlapping mutation while failover remains bounded.

`GET /frontier` returns the text-free complete contiguous DEV apply frontier for the isolated
candidate builder. `POST /prepare` binds that frontier through a
target batch to base/candidate SHA, generator ID, evidence/manifest hashes,
exact batch/group/suggestion membership, target, and service actor. It is
idempotent only for the identical binding. The browser has no service credential
and its preparation control remains disabled; see
`docs/prod-release-operations.md`.

`POST /authorize` requires a current human Access identity with independent
`publisher` scope. Approver, admin-only, bearer, cookie, and AI/service paths
fail closed. It authorizes only the already-prepared immutable binding.

### Granular Publisher review and legacy backfill

`GET /edit/v1/publisher/review` requires an authenticated human Publisher and
returns `{ ok:true, review:{ revisions, counts, blocked_reason? } }`. Each
revision includes immutable source evidence, classified operations, an
actor-owned draft when present, and any submitted review. `403` means the
Publisher scope is absent.

`POST /edit/v1/publisher/review/draft` requires the same Access identity plus
CSRF proof. Its JSON body binds `review_revision_id`, `source_revision`,
`prod_base`, and the complete draft `decisions`. Success returns
`{ ok:true, draft }`; invalid or stale evidence returns `400`, an ownership or
scope failure returns `403`, and an already-submitted revision returns `409`.

`POST /edit/v1/publisher/review/submit` requires the same human Publisher and
CSRF proof. Its body contains one idempotency key and a `sources` array whose
entries bind every submitted revision and decision. The first atomic submit
returns `201` with one shared immutable review receipt; an exact replay returns
`200`. Changed replay, stale evidence, partial groups, or incomplete source
bindings return `409`; malformed input returns `400`. A service bearer cannot
read, draft, or submit Publisher judgments.

The review source is the cumulative value from the **verified PROD base** to the
current DEV value for one durable `source_ref`. Sequential DEV suggestions are
immutable attribution evidence; they are not overlapping review decisions.
Review display text is a normalized, Unicode-aware prose projection used only
to calculate and render redlines. Immutable source-patch evidence (source value,
hashes, source revision, source location, topology operation/arguments, and
surrounding anchors) remains authoritative for applying a reviewed change.

An **operation** is the smallest independently reviewable edit and binds a
deterministic `operation_id`, durable `source_ref`, contributing suggestion and
group IDs, original and proposed values/hashes, verified PROD base, DEV source
revision, base range, replacement text, and context anchors. Adjacent delete and
insert spans forming one replacement share one operation. A structural group or
move pair is indivisible and receives one operation ID and one decision.

A Publisher **draft decision** is actor-bound, mutable, revision-bound, and has
exactly one value: `accepted`, `rejected`, or `questioned`; `questioned` requires
text. A draft has no release authority. **Submit review** atomically freezes an
immutable decision for each answered operation; absence is `unanswered`, never
accepted. Any relevant PROD-base or DEV-source advance makes affected drafts,
submitted decisions, and unexecuted previews `stale` and ineligible.

A **review receipt** hash-binds its reviewer and timestamp, verified PROD base
manifest, DEV frontier, immutable operation payloads, group identities, and the
complete submitted decision/note set. An **accepted-only manifest** starts from
that verified PROD base and names only submitted-accepted operation IDs plus the
review receipt. Rejected, questioned, unanswered, stale, ambiguous, or partially
grouped operations are held. The candidate is projected from source-patch
evidence and regenerated; filtering legacy suggestion rows or contiguous DEV
commits is not selective publication and must fail closed.

The first production-capable granular lane is prose-only. An operation whose
`op` is `insert_after`, `delete`, `split`, `merge`, or `move` remains visible on
DEV but is classified `held` with reason `structural_prod_deferred`; prose in
the same structural group or an affected source is held as
`depends_on_structural_prod_deferred`. These cards are counted and filterable as
**Held / Not publishable**, not unanswered or rejected, and expose no decision
or authorization control. Markdown prose, human-readable `json_scalar` text,
punctuation, and prose move pairs without a structural `op` remain eligible.
The Worker projection/preparation and Python materializer/manifest validator
enforce this boundary independently.

Applied rows that predate operation evidence remain unreviewable until an explicit
backfill. `POST /edit/v1/publisher/review/backfill` is restricted to the trusted
bearer/admin migration channel; human Access sessions cannot call it. One named
transaction binds a verified PROD base and per-source cumulative revision
snapshots to applied suggestion IDs, per-suggestion batch/commit evidence, and
the complete ordered apply-batch base-to-commit chain ending at the revision.
The store matches every chain entry to a completed batch and requires the
snapshot to include every applied suggestion for that source in those batches. Exact
replay is idempotent and audited; changed replay, pending rows, and mismatched
source/commit/base evidence fail atomically. Backfill creates operations only—no
drafts, decisions, reviews, release membership, or implicit acceptance.

`GET /edit/v1/publisher/review/backfill-evidence` uses the same bearer-admin-only
migration channel and returns the applied suggestion text and completed batch
evidence consumed by `tools/build_prod_review_backfill.py`. Human Access sessions
cannot call it. The generator fails closed on incomplete or ambiguous chains and
its `--check` mode requires byte-identical deterministic output before submission.

If legacy applied rows include demonstrably reverted UAT edits, the ordinary
contiguous backfill must not be used because it would resurrect copy that is no
longer canonical. `tools/build_legacy_review_reconciliation.py` instead verifies
each exclusion against its exact Git apply commit and either an exact restored
file snapshot or restored original text. It independently verifies each still-
effective edit against the exact PROD source value, current canonical value, and
the target-locator transition introduced by its apply commit. Each effective
revision carries per-suggestion batch/base/commit evidence; the store binds that
evidence to its completed apply batch before recording the revision.
`POST /edit/v1/publisher/review/reconcile-legacy` accepts that
deterministic payload only from the bearer-admin migration channel and, in one
transaction, records append-only exclusion evidence plus review revisions for
still-effective edits. The classification must cover every applied suggestion
exactly once. The endpoint reads at most 1 MiB of actual request bytes. An exact
same-ID replay is idempotent; any other migration identity is permanently closed
after the first receipt. That receipt also closes the earlier contiguous-backfill
endpoint, and excluded suggestion IDs are rejected there defensively. It creates no decisions or release authority; effective
operations remain unreviewed until a human Publisher submits a review.

`GET /edit/v1/prod/releases/audit` is a text-free, read-only rollout audit. It
is available only when `PROD_RELEASE_LEDGER=true` and only to a bearer holding
`release_service`; Access/Publisher sessions and other bearer scopes receive
`403 forbidden`. Its versioned `audit` object contains row counts, immutable
review-migration receipts, release-state counts, bounded active-release
identities, and zero-expected relationship-invariant counts. It returns no
authored text, decision notes, credentials, or provider output. Any nonzero
invariant count is a rollout stop. Migration IDs and production bases are
limited to 256 UTF-8 bytes at both the HTTP and store boundaries; defensive
audit projection omits and flags any oversized legacy value. Any nonzero
invariant count, including `unreconciled_applied_suggestions`, is a rollout stop.
The response is
bounded to the newest 100 migration receipts and 20 active releases and reports
truncation explicitly. A disabled ledger returns `404`.

`POST /claim` returns only authorized releases plus a fencing token.
`POST /transition` journals bounded identifiers/hashes and rejects stale fences
or illegal/incomplete phases. `GET /status?id=…` exposes machine-readable state
to Publisher or service scope. Completion occurs only after Pages and the
Worker/editor-map provenance match the frozen candidate.

## EditorStore config (wrangler.jsonc)

`vars`: `EDIT_UPSTREAM` (the static origin the proxy fetches, with trailing
slash), `EDIT_ORIGIN` (the worker's edit origin = the sole /edit CORS allowlist),
`EDIT_TOKEN_SCOPES` (slot→scopes JSON), `EDIT_MAX_PENDING_PER_EDITOR` (200),
`EDIT_MAX_DAILY_PER_EDITOR` (500), `EDIT_MAX_BYTES` (16384).
**Environment-scoped (WP1):** `EDIT_UPSTREAM`/`EDIT_ORIGIN` differ per env — the
top-level/default (and `env.dev`) point at DEV (`sonsteng-dev…/platform/`,
worker `sonsteng-chat`); `env.production` points at the PROD CF Pages origin
(`sonsteng.damienriehl.com/platform/`, separate worker `sonsteng-chat-production`).
A bare `wrangler deploy` still targets DEV. PROD enable = `docs/prod-enable.md`
(held). `vars`/`durable_objects` are non-inheritable, so each env re-declares them.
Secrets (never in source, **per-environment**): `EDIT_TOKEN_JOHN`,
`EDIT_TOKEN_ROGER`, `EDIT_TOKEN_ADMIN` (opaque bookmark tokens), plus the shared
`SESSION_SIGNING_KEY` (signs the edit cookie).

## Privacy / logging / retention

- Suggestions are the only new server state (DO SQLite; terminal at
  `applied/declined/superseded`; `EditorStore.purge(days)` deletes terminal rows
  after a retention window). Drafts are client-side only.
- The opaque token, the cookie value, and every secret are **never logged**
  (enforced by the editor source-scan test, parity with the BYOK key-never-logged
  guarantee). A "don't paste confidential client info" warning belongs at the
  input (client-side).

---

## Build artifact

`personas/personas.generated.json` is produced by
`tools/build_worker_personas.py` from `data/matters/*/personas/*.json` +
`facts.md` (+ the m00 fixture). It holds Segment A (verbatim), the
debrief/critique templates, injection-only persona fields, the out-of-band
`fact_map` (topic labels), and matter rubrics. **Server-only** — never shipped as
a public/static asset (it contains concealed facts). Re-run it after matter
content changes.
