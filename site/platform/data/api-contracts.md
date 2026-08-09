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
- **Non-streaming.** Each response is a single JSON body.

---

## BYOK — bring your own key (provider-agnostic)

`POST /v1/chat`, `/v1/debrief`, and `/v1/critique` MAY include:

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
`MAX_TURNS` (20), `MAX_SESSIONS_PER_DAY` (200), plus the per-provider model
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
- `claim` `{ batch_id, base_sha?, ids? }` → `accepted → in_flight` for **whole
  groups only** (never partially), stamps a **lease** + `apply_batch_id`, opens
  the `apply_batches` journal at phase `claimed`.
- `finalize` `{ batch_id, phase, applied?, accepted_blocked?, needs_human?,
  drift? }` → journals the phase and resolves the batch's `in_flight` rows.
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
CSRF guarded. `POST /prepare`, `/claim`, and `/transition` require the trusted
release service channel: a bearer credential with admin/service scope. The
service may freeze evidence and execute an already-authorized release, but it
cannot authorize one.

`POST /prepare` binds the next complete contiguous DEV apply frontier through a
target batch to base/candidate SHA, generator ID, evidence/manifest hashes,
exact batch/group/suggestion membership, target, and service actor. It is
idempotent only for the identical binding. The browser has no service credential
and its preparation control remains disabled; see
`docs/prod-release-operations.md`.

`POST /authorize` requires a current human Access identity with independent
`publisher` scope. Approver, admin-only, bearer, cookie, and AI/service paths
fail closed. It authorizes only the already-prepared immutable binding.

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
