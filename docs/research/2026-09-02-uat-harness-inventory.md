# Persona-driven UAT research dossier

Inventory date: 2026-09-02.

Repository: Legal Practicum worktree.

Purpose: evidence base for a planning agent designing persona-driven UAT.

Scope rule: “proves” below means the named automated assertion actually exercises that property.

It does not mean that a mocked unit test proves a deployed, authenticated browser journey.

All paths are repository-relative.

All citations use `path:line`.

No browser harness or deploy command was run while preparing this dossier.

## 1. HARNESS INVENTORY

### 1.1 Invocation families and evidence strength

- Full repository preflight: `bash tools/preflight.sh`; add `--no-browser` to omit browser gates (`tools/preflight.sh:15-21`).
- Preflight makes headless Chrome the default and `HEADFUL=1` an explicit supervised opt-in (`tools/preflight.sh:11-13`, `tools/preflight.sh:29-33`).
- Worker unit suite: `cd app/worker && node --test test/*.test.js`; Node 20+ and no npm install are claimed (`README.md:65-70`).
- Python unit suite: `python3 -m pytest tools/tests/ -q`, as wired by preflight (`tools/preflight.sh:59`).
- Browser harnesses use the checked-in local build or purpose-built HTML/mock servers unless a row explicitly says live.
- Worker `.test.js` files use source modules, fixtures, mocked fetches, and/or local SQLite; they require no display and no live URL.
- `tools/tests/*` use repository files and throwaway fixtures; they require no display and no live URL unless explicitly noted below.
- Runtime is “not stated” unless a source gives a duration or request timeout.

### 1.2 `tools/preflight.sh` gate list

Headless/non-browser gates, in execution order:

1. `python3 tools/validate_spine.py` — spine integrity (`tools/preflight.sh:53`).
2. `python3 tools/build_site.py --check` — rebuild plus link/leak sweeps (`tools/preflight.sh:54`).
3. Anonymous `curl` to the public GitHub repository (`tools/preflight.sh:55`).
4. `python3 tools/midstate_contract.py` — naming/remedy contract (`tools/preflight.sh:56`).
5. `python3 tools/verify_pitch.py` — hand-authored pitch contract (`tools/preflight.sh:57`).
6. `python3 tools/check_build_parity.py` — generated-bundle parity (`tools/preflight.sh:58`).
7. `python3 -m pytest tools/tests/ -q` (`tools/preflight.sh:59`).
8. One focused Publisher-review migration Node test (`tools/preflight.sh:60`).
9. All Worker `.test.js` unit tests (`tools/preflight.sh:61`).
10. Offline red-team prompt probe, requiring the output to report `0/8` missing countermeasures (`tools/preflight.sh:62`).

Browser gates, in execution order:

1. Editor client local harness (`tools/preflight.sh:77`).
2. Accessibility audit, zero failures required (`tools/preflight.sh:78`).
3. Platform responsive layout matrix (`tools/preflight.sh:79`).
4. Weekly-hours client behavior (`tools/preflight.sh:80`).
5. Matter-catalog client behavior (`tools/preflight.sh:81`).
6. Publisher authorization/review client (`tools/preflight.sh:82`).
7. Platform print matrix (`tools/preflight.sh:83`).
8. Interview plus critique matrix (`tools/preflight.sh:84`).
9. Cost-per-credit interactions (`tools/preflight.sh:85`).
10. Cost-per-credit accessibility (`tools/preflight.sh:86`).
11. Rail placement, always against a local harness unless `TARGET_URL` upgrades it to a live editor page (`tools/preflight.sh:87-100`).

Browser environment handling:

- Names: `HEADFUL`, `HEADLESS`, `EDITOR_HEADLESS`, `DISPLAY`, `XAUTHORITY`, `TARGET_URL` (`tools/preflight.sh:29-33`, `tools/preflight.sh:68-71`, `tools/preflight.sh:96-100`).
- `--no-browser` records all eleven browser categories as skipped (`tools/preflight.sh:114-126`).
- In headful mode, a reachable X display is mandatory; otherwise all browser gates are skipped (`tools/preflight.sh:73-113`).
- Preflight is not purely read-only: build and browser tools generate artifacts under `site/` and `build/`; this inventory did not invoke it.

### 1.3 `tools/platform_browser_matrix.json`

- Seven viewports: desktop 1280×900, breakpoint 960×900, 959×900, breakpoint 672×900, 671×900, compact 480×844, phone 390×844 (`tools/platform_browser_matrix.json:2-10`).
- Two type modes: `baseline`, `large` (`tools/platform_browser_matrix.json:11`).
- Required families: home, module, skills, matter library, catalog print, packet, facts, law, firm, templates, interview, critique (`tools/platform_browser_matrix.json:12`).
- Static exemplars: platform home, module 1, skills, matter library, print catalog, m03 packet/facts/law, firm, templates (`tools/platform_browser_matrix.json:14-23`).
- Interactive exemplars use `chat/test.html` for m05 interview and critique (`tools/platform_browser_matrix.json:24-25`).
- The generated-corpus sweep includes every HTML page except preview, third-party, and the two live chat pages (`tools/platform_browser_matrix.json:27`).
- Named screenshot families are home, module, packet, facts, interview, and critique (`tools/platform_browser_matrix.json:28`).

### 1.4 Direct browser and page harnesses

#### `tools/a11y_audit.js`

- Surface: default curated static platform pages plus `app/editor/test-harness.html`; explicit URL arguments replace that list (`tools/a11y_audit.js:48-52`, `tools/a11y_audit.js:223-227`).
- Assertions: rendered text/UI contrast, accessible names, target size, image alt, headings, landmarks, language, and title (`tools/a11y_audit.js:9-23`).
- Invocation: `DISPLAY=:0 node tools/a11y_audit.js [url ...]` (`tools/a11y_audit.js:25-27`).
- Environment names: `PUP_DIR`, `CHROME_BIN`, `CHROMIUM_PATH`, `HEADFUL`, `HEADLESS` (`tools/a11y_audit.js:32-43`, `tools/a11y_audit.js:228-231`).
- Input: local built files by default; arbitrary local or live URLs when passed.
- Browser: Chromium/Puppeteer; headless unless opted out.
- Output: `build/a11y-audit.json` (`tools/a11y_audit.js:267-270`).
- Runtime: not stated; per-navigation timeout is 30 seconds (`tools/a11y_audit.js:240`).

#### `tools/verify_platform_layout.js`

- Surface: matrix static exemplars; responsive, hierarchy, overflow, overlap, controls, Large Type, and print (`tools/verify_platform_layout.js:53-72`).
- It also sweeps the complete non-chat generated HTML corpus at 390 px in both type modes (`tools/verify_platform_layout.js:75-80`).
- Invocation: `node tools/verify_platform_layout.js`; add `--print` for print-only cases (`tools/verify_platform_layout.js:53-60`).
- Environment names: `PUP_DIR`, `CHROME_BIN`, `CHROMIUM_PATH`, `HEADFUL`, `HEADLESS` (`tools/verify_platform_layout.js:9-13`, `tools/verify_platform_layout.js:57`).
- Input: local `site/platform` build via `file://` (`tools/verify_platform_layout.js:5-17`).
- Browser: Chromium/Puppeteer, headless by default.
- Output: `build/platform-layout-report.json` or `build/platform-print-report.json` (`tools/verify_platform_layout.js:81-82`).
- Runtime: not stated; each navigation has a 30-second timeout (`tools/verify_platform_layout.js:63`, `tools/verify_platform_layout.js:78`).

#### `tools/verify_catalog_client.js`

- Surface: matter-library enhanced search, pagination, history restoration, focus, and a synthetic 60-matter catalog (`tools/verify_catalog_client.js:1`, `tools/verify_catalog_client.js:13-17`, `tools/verify_catalog_client.js:37-61`).
- Invocation: `node tools/verify_catalog_client.js`, wired in preflight (`tools/preflight.sh:81`).
- Environment names: `PUP_DIR`, `CHROME_BIN`, `CHROMIUM_PATH`, `HEADFUL`, `HEADLESS` (`tools/verify_catalog_client.js:8-12`, `tools/verify_catalog_client.js:30-33`).
- Input: local source files served by an ephemeral localhost HTTP server (`tools/verify_catalog_client.js:19-29`).
- Browser: Chromium/Puppeteer, headless by default.
- Runtime: not stated.

#### `tools/verify_publisher_client.mjs`

- Surface: rendered Publisher review decisions, required questioned note, multi-operation submission, and one-shot authorization behavior (`tools/verify_publisher_client.mjs:20-31`, `tools/verify_publisher_client.mjs:42-92`).
- URL: ephemeral localhost `/review` and `/`; fetch is replaced in-page (`tools/verify_publisher_client.mjs:32-36`, `tools/verify_publisher_client.mjs:43-49`, `tools/verify_publisher_client.mjs:66-73`).
- Invocation: `node tools/verify_publisher_client.mjs` (`tools/preflight.sh:82`).
- Environment names: `PUP_DIR`, `CHROME_BIN`, `CHROMIUM_PATH`, `HEADFUL`, `HEADLESS` (`tools/verify_publisher_client.mjs:8-13`, `tools/verify_publisher_client.mjs:37-40`).
- Input: local rendered mock, not a live ledger or Access session.
- Browser: Chromium/Puppeteer, headless by default.
- Runtime: not stated.

#### `tools/verify_chat_critique.js`

- Surface: m05 mock interview and mock critique through `app/chat/test.html` (`tools/verify_chat_critique.js:1`, `tools/verify_chat_critique.js:11-15`).
- Assertions: one mock turn, expected critique score/content, Large Type transition, heading order, target size, and overflow across every viewport/type-mode combination (`tools/verify_chat_critique.js:73-126`, `tools/verify_chat_critique.js:129-169`).
- Invocation: `node tools/verify_chat_critique.js` (`tools/preflight.sh:84`).
- Environment names: `PUP_DIR`, `CHROME_BIN`, `CHROMIUM_PATH`, `HEADFUL`, `HEADLESS` (`tools/verify_chat_critique.js:17-29`, `tools/verify_chat_critique.js:150-156`).
- Input: local `file://` mock harness; no provider, Worker, Turnstile, or live URL.
- Browser: Chromium/Puppeteer, headless by default.
- Runtime: not stated; navigation timeout 30 seconds and state waits 10–15 seconds (`tools/verify_chat_critique.js:73-93`, `tools/verify_chat_critique.js:134-135`).

#### `tools/verify_cost_per_credit.js`

- Surface: `site/cost-per-credit.html`, loaded with `page.setContent` (`tools/verify_cost_per_credit.js:19-20`, `tools/verify_cost_per_credit.js:39-46`).
- Assertions include defaults, accessible validation, stipend/load calculations, model switching, mobile overflow, and no network/console errors (`tools/verify_cost_per_credit.js:53-108`).
- Invocation: `node tools/verify_cost_per_credit.js` (`tools/preflight.sh:85`).
- Environment names: `PUP_DIR`, `HEADLESS` (`tools/verify_cost_per_credit.js:7-17`, `tools/verify_cost_per_credit.js:22-27`).
- Input: local standalone HTML, not a live URL.
- Browser: Puppeteer/Chromium; headless unless `HEADLESS=0`.
- Runtime: not stated.

#### `app/editor/verify-editor.js`

- Surface: `app/editor/test-harness.html`, exercising the real editor client against mocked server behavior (`app/editor/verify-editor.js:1-12`).
- Assertions cover edit/save/autocorrect, selection comments, formatted/scalar restrictions, 401 recovery, dedupe, Large Type, text normalization, scoped changes, and desktop/mobile layouts (`app/editor/verify-editor.js:7-12`).
- Invocation: `DISPLAY=:0 node app/editor/verify-editor.js` (`app/editor/verify-editor.js:5`).
- Environment names: `HOME`, `HARNESS_URL`, `HEADFUL`, `EDITOR_HEADLESS` (`app/editor/verify-editor.js:19-26`, `app/editor/verify-editor.js:115`).
- Input: local `file://`; `HARNESS_URL` permits a localhost static-server copy (`app/editor/verify-editor.js:22-26`).
- Browser: Chromium/Puppeteer; current code is headless unless `HEADFUL=1` or `EDITOR_HEADLESS=0` (`app/editor/verify-editor.js:115`).
- Output: screenshots under `HOME` (`app/editor/verify-editor.js:945-949`, `app/editor/verify-editor.js:1061-1062`).
- Runtime: documented as well over ten minutes; allow 1800 seconds (`docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md:62-64`).

#### `app/editor/verify-rail-placement.js`

- Surface: editor rail geometry at ten widths (`app/editor/verify-rail-placement.js:11-18`, `app/editor/verify-rail-placement.js:34`).
- Invocation: local `DISPLAY=:0 node app/editor/verify-rail-placement.js`; live upgrade with `TARGET_URL` (`app/editor/verify-rail-placement.js:15-18`).
- Environment names: `TARGET_URL`, `HEADFUL`, `HEADLESS`, `DISPLAY` (`app/editor/verify-rail-placement.js:15-18`, `app/editor/verify-rail-placement.js:32-39`).
- Input: live editor URL if supplied; otherwise a hard-coded local path outside this worktree (`app/editor/verify-rail-placement.js:32`).
- Browser: Chromium/Puppeteer, headless by default.
- Runtime: not stated; each width deliberately settles for 1.8 seconds (`app/editor/verify-rail-placement.js:43-50`).
- Important planning constraint: the default path is not derived from the current repository, so worktree UAT should set `TARGET_URL` or correct local serving outside this read-only task.

#### `app/hours/verify-hours.js`

- Surface: built weekly-hours page `site/platform/hours/index.html` (`app/hours/verify-hours.js:1-8`).
- Assertions: local assets, Large Type, future-envelope quarantine, no network, mobile overflow, live region, weekly draft navigation, clear, and storage-failure fallback (`app/hours/verify-hours.js:20-79`).
- Invocation: `EDITOR_HEADLESS=1 node app/hours/verify-hours.js` (`app/hours/verify-hours.js:1-2`).
- Environment names: `EDITOR_HEADLESS` (`app/hours/verify-hours.js:15-18`).
- Input: local built page via `file://`.
- Browser: Chromium/Puppeteer, headless by default.
- Runtime: not stated.

#### `app/editor/spikes/verify-spikes.js`

- Surface: the two local prototype pages for blur/save races and selection commenting (`app/editor/spikes/verify-spikes.js:1-7`, `app/editor/spikes/verify-spikes.js:26-31`, `app/editor/spikes/verify-spikes.js:131-136`).
- Invocation: `DISPLAY=:0 node app/editor/spikes/verify-spikes.js` (`app/editor/spikes/verify-spikes.js:6`).
- Environment names: `HOME`, `HEADFUL`, `HEADLESS` (`app/editor/spikes/verify-spikes.js:11-13`, `app/editor/spikes/verify-spikes.js:20-23`).
- Input: local `file://` spike pages.
- Browser: Chromium/Puppeteer, headless by default in current code.
- Output: screenshots under `HOME` (`app/editor/spikes/verify-spikes.js:126-127`).
- Runtime: not stated.

#### `tools/shot.js`

- Surface: generic screenshot of any caller-supplied URL, with viewport/full-page and scroll-target controls (`tools/shot.js:1-2`).
- Invocation: `node tools/shot.js <url> <out.png> [view|full] [width] [scale] [scrollTarget]` (`tools/shot.js:1-2`).
- Environment names: `PUP_DIR`, `CHROME_BIN`, `CHROMIUM_PATH`, `HEADFUL`, `HEADLESS` (`tools/shot.js:4`, `tools/shot.js:10`).
- Input: local or live URL supplied by caller.
- Browser: Chromium/Puppeteer, headless by default.
- Runtime: not stated; navigation timeout is 45 seconds (`tools/shot.js:14`).
- It captures evidence but contains no product assertions, so it is a utility rather than a release gate.

### 1.5 Static/build verification harnesses in `tools/`

- `tools/verify_pitch.py`: checks every hand-authored top-level `site/*.html` page by default, including links, assets, body names/statistics, size, and the nine-section proof contract (`tools/verify_pitch.py:2-7`, `tools/verify_pitch.py:451-490`). Invocation `python3 tools/verify_pitch.py [pages...]`; no env, live URL, display, or stated runtime (`tools/verify_pitch.py:493-500`).
- `tools/midstate_contract.py`: fail-closed check that the public Midstate/Rogers materials retain the approved arbitration/court caption and remedy postures and contain no legacy arbitration caption (`tools/midstate_contract.py:1-6`, `tools/midstate_contract.py:58-90`). Invocation `python3 tools/midstate_contract.py [--root ...]`; no env, live URL, display, or stated runtime (`tools/midstate_contract.py:93-106`).
- `tools/build_site.py --check`: generates all student pages, data catalog, editor map, stamp, then makes link, instructor-content, durable-marker, history-leak, and page-size failures fatal (`tools/build_site.py:3992-4028`, `tools/build_site.py:4085-4145`). No env/live URL/display; local build; runtime not stated. It modifies generated outputs and was not run here.
- `tools/validate_spine.py`: 31-check corpus integrity gate with schemas, references, money, persona facts, taxonomy, and Day Zero/identifier rules (`tools/validate_spine.py:2-26`, `tools/validate_spine.py:74`). Invocation via `--help`; no display/live URL; offline by default, optional local-crosswalk `--online` (`tools/validate_spine.py:18-26`).
- `tools/audit_nine_parts.py`: report-only audit of every packet against nine required parts (`tools/audit_nine_parts.py:2-6`, `tools/audit_nine_parts.py:20-45`). Invocation `python3 tools/audit_nine_parts.py [--root ...]`; no env/live URL/display; runtime not stated (`tools/audit_nine_parts.py:337-354`).
- `tools/check_build_parity.py`: compares data truth, public-site stamp, persona bundle, instructor bundle, and editor map (`tools/check_build_parity.py:2-19`, `tools/check_build_parity.py:33-38`). No env/live URL/display; runtime not stated.
- `tools/platform_semantic_contract.py`: compares authored visible text, headings, links, editor identities, and reading order while ignoring presentation wrappers (`tools/platform_semantic_contract.py:2-6`, `tools/platform_semantic_contract.py:77-121`). Invocation takes baseline, site, editor-map paths; no env/live URL/display (`tools/platform_semantic_contract.py:188-205`).
- `tools/editor_consistency.py`: flags facts-versus-narrative inconsistency and never edits; deterministic mode is available (`tools/editor_consistency.py:607-618`). Environment names `EDIT_API_BASE`, `EDIT_SERVICE_TOKEN`; without the API base it falls back to dry-run (`tools/editor_consistency.py:621-629`, `tools/editorial_pass.py:62-63`). No browser/display; local corpus plus optional Worker filing; runtime not stated.
- `tools/offline_redteam_probe.mjs`: renders real system prompts and mechanically scans eight jailbreak angles; explicitly not live model behavior (`tools/offline_redteam_probe.mjs:2-18`). Invocation `node tools/offline_redteam_probe.mjs [--dump persona]`; no env/live URL/display/runtime (`tools/offline_redteam_probe.mjs:20-24`).
- `tools/prod_release_readiness.py`: read-only Publisher readiness report over ledger status and local config/timer state (`tools/prod_release_readiness.py:190-210`). Invocation requires `--ledger-url`, `--observer-env-file`, `--prod-env-file`; ambient `SONSTENG_PROD_RELEASE_BEARER` or `EDIT_SERVICE_TOKEN` makes it refuse (`tools/prod_release_readiness.py:190-200`). It needs a live ledger URL, no display; runtime not stated.
- `tools/assessment_calibration.py`: validates a caller-owned, de-identified faculty/panel rating file and emits aggregate threshold evidence; it refuses identity and free-text fields (`tools/assessment_calibration.py:1-6`, `tools/assessment_calibration.py:21-28`). Invocation `python3 tools/assessment_calibration.py INPUT --min-kappa ... --max-abs-signed-difference ... [--human]`; no environment variables, live URL, display, or stated runtime (`tools/assessment_calibration.py:223-259`).
- `tools/day_zero_equivalence.py`: library proof harness used by the governed Day Zero migration to reconstruct every declared touched file byte-for-byte, resolve every date proof, and preserve durable block IDs (`tools/day_zero_equivalence.py:1-8`, `tools/day_zero_equivalence.py:210-241`). It has no standalone CLI; invoke through the Day Zero migration/tests. No environment variables, live URL, display, or stated runtime.
- `tools/validate_day_zero_review_proposal.py`: validates the deterministic agent proposal, human decision sheet, exact-key joins, and—when present—the separate approval/applied state (`tools/validate_day_zero_review_proposal.py:1-24`, `tools/validate_day_zero_review_proposal.py:374-415`). Invocation `python3 tools/validate_day_zero_review_proposal.py [--repo ...]`; no environment variables, live URL, display, or stated runtime.
- `tools/build_history.py` plus `tools/render_diff_lib.py`: generates the authenticated John/Roger/Damien history bundle and HTML redlines, and keeps instructor-only history outside the public site (`tools/build_history.py:2-30`, `tools/render_diff_lib.py:14-23`). These are local build artifacts, not live URLs; no browser/display. The redline library's direct CLI is `python3 tools/render_diff_lib.py OLD NEW OUT.html [title]` (`tools/render_diff_lib.py:116-132`). Runtime is not stated.
- `tools/build_instructor_bundle.py`: build/self-check harness for server-only facts, instructor notes, and answer keys; it verifies the output is outside the public site and absent from the persona bundle (`tools/build_instructor_bundle.py:2-29`, `tools/build_instructor_bundle.py:136-160`). Local build, no env/live URL/display/runtime; it writes `build/instructor-bundle.generated.json` (`tools/build_instructor_bundle.py:16`, `tools/build_instructor_bundle.py:131-134`).
- `tools/build_worker_personas.py` is indirectly checked by parity and focused tests; its product is the server-only persona/rubric/evaluator bundle (`README.md:30`, `tools/check_build_parity.py:6-9`).

Classification boundary: `tools/build_taxonomy_contract.py` is a mutating inventory generator, not a verification harness (`tools/build_taxonomy_contract.py:1-2`, `tools/build_taxonomy_contract.py:109-112`). Likewise, `tools/tests/fresh_site_build.py` and `app/worker/test/editor-sql-helper.mjs` are test support code rather than independently invocable user-surface harnesses. Operational apply/deploy/installer programs are covered by their focused tests below; they are not safe UAT invocations.

### 1.6 Live Worker harnesses

#### `app/worker/test/live-stream-smoke.mjs`

- Surface: deployed DEV `GET /v1/session`, streaming `POST /v1/chat`, and same-turn JSON replay (`app/worker/test/live-stream-smoke.mjs:342-423`).
- Invocation: from `app/worker`, set target/provider and run `node test/live-stream-smoke.mjs`; run once per provider (`app/worker/API-CONTRACTS.md:32-53`).
- Environment names: `WORKER_URL`, `PROVIDER`, `MODEL`, `ORIGIN`, `API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `DEMO_BYPASS_TOKEN`, `CREDENTIALS_FILE`, `CREDENTIALS_STDIN` (`app/worker/API-CONTRACTS.md:45-63`).
- Input: exact approved deployed DEV Worker by default; production is rejected by the CLI (`app/worker/test/live-stream-smoke.mjs:316-332`).
- Browser/display: none; Node fetch.
- Runtime: no typical total stated; per-request timeout is 90 seconds (`app/worker/test/live-stream-smoke.mjs:18`, `app/worker/test/live-stream-smoke.mjs:291-296`).
- It proves transport/API behavior, not chat-page UI, Turnstile widget rendering, or browser CORS integration.

#### `app/worker/test/assessment-live-uat.mjs`

- Surface: creates one fixed, disposable formative memo assessment through session mint and `POST /v1/memo-assessment`, then returns the Access review URL (`app/worker/test/assessment-live-uat.mjs:153-229`).
- Invocation: `PROVIDER=... CREDENTIALS_FILE=... node test/assessment-live-uat.mjs` from `app/worker` (`app/worker/API-CONTRACTS.md:73-89`).
- Environment names: `PROVIDER`, `CREDENTIALS_FILE`, `CREDENTIALS_STDIN`, `WORKER_URL`, `ORIGIN`; direct API-key environment variables are rejected (`app/worker/API-CONTRACTS.md:91-100`, `app/worker/test/assessment-live-uat.mjs:232-241`).
- Credential file contains both provider key and DEV bypass; names are not repeated here beyond the environment-variable inventory (`app/worker/API-CONTRACTS.md:84-96`).
- Input: exact DEV Worker; approved public origin only (`app/worker/test/assessment-live-uat.mjs:171-180`).
- Browser/display: none; the human must separately open the returned Access URL.
- Runtime: no typical total stated; request timeout is bounded at 90 seconds (`app/worker/test/assessment-live-uat.mjs:160-168`).

#### `app/worker/test/redteam.mjs`

- Surface: deployed Worker `/v1/session`, `/v1/chat`, and `/v1/debrief`; concealed-fact, meta/jailbreak, sycophancy, fact-fidelity, premature-debrief, and legitimate-debrief probes (`app/worker/test/redteam.mjs:2-27`, `app/worker/test/redteam.mjs:80-116`).
- Invocation: from `app/worker`, `WORKER_URL=... PROVIDER=... API_KEY=... node test/redteam.mjs` (`app/worker/test/redteam.mjs:5-11`).
- Environment names: `WORKER_URL`, `PROVIDER`, `API_KEY`, `MODEL`, `ORIGIN` (`app/worker/test/redteam.mjs:29-37`).
- Input: live deployed Worker and real provider key.
- Browser/display: none; Node fetch.
- Runtime: not stated.
- Output includes PASS/FAIL/REVIEW; REVIEW requires a human reading (`app/worker/test/redteam.mjs:26-27`, `app/worker/test/redteam.mjs:225-229`).

### 1.7 Worker unit-harness inventory

Shared invocation: `cd app/worker && node --test test/*.test.js` (`README.md:65-70`).

Shared requirements: no environment variables, no live URL, no Chrome/display, local source/fixtures only, runtime not stated.

- `app/worker/test/access-jwt.test.js`: real-signature/mocked-JWKS Access identity and fail-closed verifier (`app/worker/test/access-jwt.test.js:1-8`).
- `app/worker/test/assessment-audit-store.test.js`: signer audit persistence, review scope, credential absence, attribution, expiry (`app/worker/test/assessment-audit-store.test.js:1-3`).
- `app/worker/test/assessment-config.test.js`: instructor/school/default threshold resolution (`app/worker/test/assessment-config.test.js:1-2`).
- `app/worker/test/assessment-live-uat.test.js`: no-side-effect import and safe CLI/network failure contracts for the live preparer (`app/worker/test/assessment-live-uat.test.js:96-144`).
- `app/worker/test/assessment-review.test.js`: signer page, provenance, keyboard-labelled override UI, assets, and Access-only scope (`app/worker/test/assessment-review.test.js:1-2`, `app/worker/test/assessment-review.test.js:139-220`).
- `app/worker/test/budget-core.test.js`: session mint, spend/turn caps, replay, rollback, and assessment cap (`app/worker/test/budget-core.test.js:1-2`).
- `app/worker/test/budget.test.js`: displayed/charged budget cost math (`app/worker/test/budget.test.js:1-4`).
- `app/worker/test/byok.test.js`: provider/model selection, hosted-key absence, budget semantics, key-never-logged scan (`app/worker/test/byok.test.js:1-2`).
- `app/worker/test/chat-stream.test.js`: provider-neutral SSE framing, deltas, usage, and bookkeeping parity using fixtures (`app/worker/test/chat-stream.test.js:1-12`).
- `app/worker/test/cors.test.js`: browser-origin allowlist/preflight/error wrapping (`app/worker/test/cors.test.js:1-10`).
- `app/worker/test/debrief-oracle.test.js`: student-visible missed-fact labels cannot become an answer key (`app/worker/test/debrief-oracle.test.js:1-7`).
- `app/worker/test/editor-access-door.test.js`: Access assertion-to-slot wiring, host gate, and coexistence with token/cookie/Bearer paths (`app/worker/test/editor-access-door.test.js:1-12`).
- `app/worker/test/editor-admin.test.js`: `/edit/admin` landing and “since you last looked” editorial flags (`app/worker/test/editor-admin.test.js:1-12`).
- `app/worker/test/editor-auth.test.js`: `?t=` token exchange, signed cookie, rotation, admin isolation, CSRF (`app/worker/test/editor-auth.test.js:1-9`).
- `app/worker/test/editor-direct-apply.test.js`: direct-apply flag and liveness/error endpoint behavior (`app/worker/test/editor-direct-apply.test.js:1-7`).
- `app/worker/test/editor-facts.test.js`: new-fact and system-suggest Worker surface (`app/worker/test/editor-facts.test.js:1-4`).
- `app/worker/test/editor-inject.test.js`: injected page script stripping, asset rewriting, CSP, link routing, and student-view URL (`app/worker/test/editor-inject.test.js:1-10`).
- `app/worker/test/editor-map.test.js`: page/block allowlists, SSRF/XSS/path rejection, instructor-doc resolution (`app/worker/test/editor-map.test.js:1-16`).
- `app/worker/test/editor-norm-parity.test.js`: JS/Python hash normalization parity required by save/apply (`app/worker/test/editor-norm-parity.test.js:1-3`).
- `app/worker/test/editor-overlay.test.js`: reload reconstruction of pending WYSIWYG overlays (`app/worker/test/editor-overlay.test.js:1-4`).
- `app/worker/test/editor-publisher-release.test.js`: release ledger preparation/authorization/claim/transition boundaries (`app/worker/test/editor-publisher-release.test.js:1-6`).
- `app/worker/test/editor-publisher-review.test.js`: granular review draft/submit/backfill/reconciliation and route authorization (`app/worker/test/editor-publisher-review.test.js:1-6`).
- `app/worker/test/editor-publisher-ui.test.js`: Publisher page/view model, save states, review submission, and Publisher-only route (`app/worker/test/editor-publisher-ui.test.js:1-6`).
- `app/worker/test/editor-release-provenance.test.js`: public release SHA attestation endpoint (`app/worker/test/editor-release-provenance.test.js:1-5`).
- `app/worker/test/editor-revert.test.js`: history-browser revert request lifecycle and endpoints (`app/worker/test/editor-revert.test.js:1-4`).
- `app/worker/test/editor-roger.test.js`: Roger’s independent edit/instructor scope, RSH attribution, and isolation (`app/worker/test/editor-roger.test.js:1-6`).
- `app/worker/test/editor-scope.test.js`: part→matter→module→course scope enumeration and blast radius (`app/worker/test/editor-scope.test.js:1-5`).
- `app/worker/test/editor-scoped-endpoints.test.js`: scoped-request and admin drafter endpoints/ceiling (`app/worker/test/editor-scoped-endpoints.test.js:1-4`).
- `app/worker/test/editor-scoped-requests.test.js`: scoped request/claim/draft/canary/remainder lifecycle (`app/worker/test/editor-scoped-requests.test.js:1-5`).
- `app/worker/test/editor-security.test.js`: editor credential/logging/CSP/uniform-404 security (`app/worker/test/editor-security.test.js:1-4`).
- `app/worker/test/editor-store.test.js`: suggestion state machine, dedupe, ceilings, groups, review/store semantics (`app/worker/test/editor-store.test.js:1-4`).
- `app/worker/test/editor-structural-endpoints.test.js`: structural suggest validation, CSRF, identity, allowlists, sizes (`app/worker/test/editor-structural-endpoints.test.js:1-3`).
- `app/worker/test/editor-structural.test.js`: insert/delete/split/merge/move store semantics (`app/worker/test/editor-structural.test.js:1-5`).
- `app/worker/test/editor-system-suggest.test.js`: admin-only system proposal endpoint (`app/worker/test/editor-system-suggest.test.js:1-4`).
- `app/worker/test/editor-taxonomy-plain-text.test.js`: taxonomy edit normalization (`app/worker/test/editor-taxonomy-plain-text.test.js:1-5`).
- `app/worker/test/errors.test.js`: user-visible typed error envelope and in-character policy (`app/worker/test/errors.test.js:1-4`).
- `app/worker/test/live-stream-smoke.test.js`: credential-source, target, timeout, streaming, replay, and redaction behavior of live smoke without network (`app/worker/test/live-stream-smoke.test.js:1-6`).
- `app/worker/test/memo-scorecard.test.js`: seven-heading memo evaluator output contract (`app/worker/test/memo-scorecard.test.js:1-5`).
- `app/worker/test/offline-redteam-redaction.test.js`: crafted leak/evasion inputs against real bundled secrets and fail-closed free-text scan (`app/worker/test/offline-redteam-redaction.test.js:1-24`, `app/worker/test/offline-redteam-redaction.test.js:246-253`).
- `app/worker/test/panel.test.js`: formative-only multi-provider memo panel orchestration (`app/worker/test/panel.test.js:1-4`).
- `app/worker/test/platform-language-contract.test.js`: formative route and locked learner-facing terminology (`app/worker/test/platform-language-contract.test.js:12-48`).
- `app/worker/test/prompts.test.js`: golden prompt, persona, debrief, and rubric-label rendering (`app/worker/test/prompts.test.js:1-10`, `app/worker/test/prompts.test.js:89-116`).
- `app/worker/test/providers.test.js`: upstream request shape and usage normalization for three providers (`app/worker/test/providers.test.js:1-5`).
- `app/worker/test/session.test.js`: user session HMAC mint/verify/tamper/UTC-day behavior (`app/worker/test/session.test.js:1-6`).
- `app/worker/test/turnstile.test.js`: mocked siteverify, enabled/disabled/bypass session-mint decisions (`app/worker/test/turnstile.test.js:1-4`).

### 1.8 `tools/tests/` surface-oriented harness inventory

Shared invocation: `python3 -m pytest tools/tests/ -q` (`tools/preflight.sh:59`).

Shared requirements: no live URL, no display, no browser, no required environment, runtime not stated; subprocesses and git use throwaway/local fixtures where present.

- Pitch/top-level pages: `tools/tests/test_verify_pitch.py`, `test_cost_per_credit.py`, `test_identity_rights_contract.py`, `test_midstate_contract.py` (`tools/tests/test_verify_pitch.py:1`, `tools/tests/test_cost_per_credit.py:1-14`, `tools/tests/test_identity_rights_contract.py:1`, `tools/tests/test_midstate_contract.py:1`).
- Generated platform: `test_catalog_contract.py`, `test_platform_browser_matrix.py`, `test_platform_language_contract.py`, `test_platform_semantic_contract.py`, `test_platform_visual_contract.py` (`tools/tests/test_catalog_contract.py:103-169`, `tools/tests/test_platform_browser_matrix.py:12-42`, `tools/tests/test_platform_language_contract.py:58-111`, `tools/tests/test_platform_semantic_contract.py:22-101`, `tools/tests/test_platform_visual_contract.py:1-10`).
- Packets/content: `test_validate_spine.py`, `test_audit_nine_parts.py`, `test_facts_surface.py`, `test_law_pages.py`, `test_catalog_contract.py` (`tools/tests/test_validate_spine.py:1-12`, `tools/tests/test_audit_nine_parts.py:68-162`, `tools/tests/test_facts_surface.py:112-236`, `tools/tests/test_law_pages.py:120-177`).
- Student hours/privacy: `test_weekly_hours_log.py`, `test_no_committed_learner_exports.py` (`tools/tests/test_weekly_hours_log.py:41-152`, `tools/tests/test_no_committed_learner_exports.py:34-50`).
- Chat/type/sample language: `test_chat_type_preference.py`, `type_preference_contract.test.js`, `test_platform_language_contract.py` (`tools/tests/test_chat_type_preference.py:14-67`, `tools/tests/type_preference_contract.test.js:1-12`, `tools/tests/test_platform_language_contract.py:99-119`).
- Assessment/instructor: `test_assessment_instrument.py`, `test_assessment_calibration.py`, `test_build_worker_personas.py`, `test_competency_credit_proposal.py` (`tools/tests/test_assessment_instrument.py:79-157`, `tools/tests/test_assessment_calibration.py:35-164`, `tools/tests/test_build_worker_personas.py:23-53`, `tools/tests/test_competency_credit_proposal.py:18-172`).
- Editor reachability/content: `test_editable_coverage.py`, `test_editor_map_reachability.py`, `test_scope_index.py`, `test_taxonomy_editability.py` (`tools/tests/test_editable_coverage.py:127-277`, `tools/tests/test_editor_map_reachability.py:68-107`, `tools/tests/test_scope_index.py:1-8`, `tools/tests/test_taxonomy_editability.py:1-12`).
- Editor authoring/apply: `test_apply_suggestions.py`, `test_json_surgical.py`, `test_span_splice.py`, `test_stamp_block_ids.py`, `test_structural_ops.py`, `test_editor_scoped_drafts.py`, `test_editor_consistency.py`, `test_editorial_pass.py` (each describes its local/throwaway scope at `tools/tests/test_apply_suggestions.py:1-4`, `tools/tests/test_json_surgical.py:1-5`, `tools/tests/test_span_splice.py:1-11`, `tools/tests/test_stamp_block_ids.py:1-11`, `tools/tests/test_structural_ops.py:1-12`, `tools/tests/test_editor_scoped_drafts.py:1-4`, `tools/tests/test_editor_consistency.py:1-5`, `tools/tests/test_editorial_pass.py:1-5`).
- History/revert/direct apply: `test_build_history.py`, `test_direct_apply_daemon.py`, `test_direct_apply_revert.py`, `test_publication_boundary.py` (`tools/tests/test_build_history.py:1-5`, `tools/tests/test_direct_apply_daemon.py:1-5`, `tools/tests/test_direct_apply_revert.py:1-5`, `tools/tests/test_publication_boundary.py:1-6`).
- History redline rendering: `test_render_diff_lib.py` verifies escaped insertion/deletion HTML, collapsed unchanged context, standalone page output, and title escaping (`tools/tests/test_render_diff_lib.py:20-97`).
- Day Zero visible-content migration: `test_day_zero.py`, `test_day_zero_equivalence.py`, `test_day_zero_migration.py`, `test_day_zero_review_proposal.py`, `test_apply_day_zero_review.py` (`tools/tests/test_day_zero_equivalence.py:1-4`, `tools/tests/test_day_zero_migration.py:1-4`).
- Publisher legacy review: `test_build_prod_review_backfill.py`, `test_build_legacy_review_reconciliation.py` (`tools/tests/test_build_prod_review_backfill.py:1-8`, `tools/tests/test_build_legacy_review_reconciliation.py:1-8`).
- Production release: `test_prod_release_bootstrap.py`, `test_prod_release_daemon.py`, `test_prod_release_executor.py`, `test_prod_release_operations.py`, `test_prod_release_readiness.py`, `test_migrate_prod_release_env.py` (`tools/tests/test_prod_release_bootstrap.py:59-202`, `tools/tests/test_prod_release_daemon.py:25-190`, `tools/tests/test_prod_release_executor.py:35-220`, `tools/tests/test_prod_release_operations.py:1-8`, `tools/tests/test_prod_release_readiness.py:48-144`, `tools/tests/test_migrate_prod_release_env.py:1-8`).
- Security/public-source hygiene: `test_no_committed_pii.py` and the learner-export guard (`tools/tests/test_no_committed_pii.py:1-10`, `tools/tests/test_no_committed_learner_exports.py:1-11`).
- Audited but not user-facing persona harnesses: `test_digest_push.py`, `test_todo_report.py`, `test_make_baseline.py`, and `test_repo_rename_inventory.py` exercise operator reminders, tags, or rename inventory rather than a listed user surface (`tools/tests/test_digest_push.py:1-5`, `tools/tests/test_todo_report.py:1-10`, `tools/tests/test_make_baseline.py:1-5`, `tools/tests/test_repo_rename_inventory.py:1-12`).

## 2. SURFACE MAP

### 2.1 Prospective-reader pitch page

`site/index.html` is the single-page pitch and links into the practicum plus cost page (`README.md:23-24`, `site/index.html:254-284`).

Anchors/section IDs, in document order:

1. `#problem` — “The broken promise” (`site/index.html:289-293`).
2. `#practicum` — worked Midstate/Rogers demonstration and 20 matter covers (`site/index.html:308-320`).
3. `#skills` — survey evidence (`site/index.html:355-360`).
4. `#work` — scholarship trilogy and delivery economics (`site/index.html:416-430`).
5. `#opus` — book/curriculum/platform layers (`site/index.html:433-440`).
6. `#centaur` — human+AI model (`site/index.html:445-453`).
7. `#coverage` — all 26 skills mapped; this anchor is not in the top nav (`site/index.html:500-504`).
8. `#where` — open licensing and ask (`site/index.html:540-565`).
9. `#react` — eight reaction prompts and comment-copy workflow (`site/index.html:570-586`).

Top navigation exposes problem, practicum, skills, work, opus, centaur, where, react, and the cost page (`site/index.html:255-267`).

The hero CTA goes to `/platform/` (`site/index.html:281-284`).

“THE PROOF” mechanism:

- Each of exactly nine sections must contain exactly one direct-child `details.proof`, closed by default (`tools/verify_pitch.py:335-370`).
- The approved summary labels are pinned in order (`tools/verify_pitch.py:24-34`).
- `#proofToggle` is initially `aria-expanded=false` (`site/index.html:287`).
- Clicking it opens/closes every `details.proof` and synchronizes label/state (`site/index.html:657-673`).
- Before print all proof disclosures open; after print their prior states are restored (`site/index.html:674-680`).
- Print CSS hides summaries and forces disclosure content visible (`site/index.html:248-250`).

### 2.2 Generated platform

There are 76 HTML files under `site/platform/` in this checkout.

Generation owns this tree except `assets/`, and reads the data spine plus chat/hours sources (`tools/build_site.py:11-19`, `tools/build_site.py:43-50`).

Top-level directories are exactly:

- `about/`
- `assets/`
- `chat/`
- `data/`
- `downloads/`
- `firm/`
- `hours/`
- `matters/`
- `modules/`
- `skills/`
- `templates/`

User-facing index/special pages:

- `site/platform/index.html` — generated home.
- `site/platform/modules/m1.html`
- `site/platform/modules/m2.html`
- `site/platform/modules/m3.html`
- `site/platform/skills/index.html`
- `site/platform/matters/index.html`
- `site/platform/matters/print-all.html`
- `site/platform/firm/index.html`
- `site/platform/hours/index.html`
- `site/platform/templates/index.html`
- `site/platform/chat/index.html`
- `site/platform/chat/critique.html`
- `site/platform/about/content-license.html`
- `site/platform/about/code-license.html`
- `site/platform/about/third-party.html`
- `site/platform/assets/preview.html` is a design preview, excluded from the generated-corpus matrix (`tools/platform_browser_matrix.json:27`).

Matter count: 20; module count: 3. The README states the 20-matter platform (`README.md:14-17`, `README.md:24`).

The 20 matter directories are:

- `site/platform/matters/m01-arbitration-meridian/`
- `site/platform/matters/m02-discipline-meridian/`
- `site/platform/matters/m03-tort-meridian/`
- `site/platform/matters/m04-realestate-meridian/`
- `site/platform/matters/m05-dwi-meridian/`
- `site/platform/matters/m06-noncompete-meridian/`
- `site/platform/matters/m07-ucc-meridian/`
- `site/platform/matters/m08-juvenile-meridian/`
- `site/platform/matters/m09-dissolution-meridian/`
- `site/platform/matters/m10-probate-meridian/`
- `site/platform/matters/m11-arbitration-il/`
- `site/platform/matters/m12-discipline-mn/`
- `site/platform/matters/m13-tort-fl/`
- `site/platform/matters/m14-realestate-tx/`
- `site/platform/matters/m15-dwi-mn/`
- `site/platform/matters/m16-noncompete-ny/`
- `site/platform/matters/m17-ucc-ny/`
- `site/platform/matters/m18-juvenile-ca/`
- `site/platform/matters/m19-dissolution-ca/`
- `site/platform/matters/m20-probate-fl/`

Each currently has three HTML pages: packet `index.html`, `facts/index.html`, and `law/index.html`, for 60 matter HTML pages.

Packet rendering creates interview, critique, rubric, business, and optional side-confidential sections (`tools/build_site.py:2306-2392`, `tools/build_site.py:2394-2404`).

Every matter has a student-material ZIP under `site/platform/downloads/`; the catalog tests require deterministic, exact, instructor-safe archives (`tools/tests/test_catalog_contract.py:15-87`).

The generator may split oversized packet case files to `case-file.html`, but no such file exists in this checkout; the split rule is over 250 KB (`tools/build_site.py:2488-2497`).

### 2.3 `site/cost-per-credit.html`

- Standalone dean/school calculator linked twice from the pitch (`tools/tests/test_cost_per_credit.py:45-50`).
- Shows the 225-hour ABA Standard 310(b) arithmetic (`site/cost-per-credit.html:32-34`).
- Supports stipend and teaching-load models (`site/cost-per-credit.html:37-60`).
- Compares standard class, seminar, clinic, and internship costs (`site/cost-per-credit.html:64-69`).
- Contains no form submission/network/persistence API by contract (`tools/tests/test_cost_per_credit.py:121-125`).

### 2.4 Public Worker routes

- `GET /v1/session` — mints a session (`app/worker/src/index.js:127-165`, `app/worker/src/index.js:649-650`).
- `POST /v1/chat` — persona interview (`app/worker/src/index.js:186-206`, `app/worker/src/index.js:651-652`).
- `POST /v1/debrief` — scorecard after at least six committed persona turns (`app/worker/src/index.js:325-355`, `app/worker/src/index.js:653-654`).
- `POST /v1/memo-assessment` — formative seven-heading assessment (`app/worker/src/index.js:402-403`, `app/worker/src/index.js:655-656`).
- `POST /v1/critique` — deliverable critique (`app/worker/src/index.js:552-553`, `app/worker/src/index.js:657-658`).
- `OPTIONS` is handled centrally; requests with a non-allowlisted Origin get bare 403 (`app/worker/src/index.js:638-645`).
- Other public paths return typed 404 (`app/worker/src/index.js:659-665`).

Public-route requirements:

- `/v1/session` reads optional `?bypass=` and `?cf_ts=` (`app/worker/src/index.js:129-143`).
- A valid demo bypass skips Turnstile; otherwise Turnstile is verified before mint (`app/worker/src/index.js:133-147`).
- Chat/debrief/critique/assessment require a valid session token in their JSON bodies; chat also requires known matter/persona and BYOK or a hosted key (`app/worker/src/index.js:187-209`).
- `?sample=1` is a static UI-only mode: it makes no API/key/session request (`app/chat/chat.js:1151-1156`, `app/chat/chat.js:1310`).
- `?bypass=` is forwarded only to session mint in chat (`app/chat/chat.js:1127-1135`).
- Live interview UI loads Turnstile unless sample or bypass is active (`app/chat/chat.js:1055-1124`).
- Critique can reuse a per-tab session or mint one, but its code supplies only `?bypass=`, not `cf_ts` (`app/chat/critique.js:38-61`).

### 2.5 Host redirects and edit routes

Host routing runs before feature routes (`app/worker/src/index.js:617-626`).

- Exact configured public aliases redirect 308 to the canonical public host (`app/worker/src/host-routing.js:25-36`).
- Exact legacy editor host redirects 308 to the Access editor host (`app/worker/src/host-routing.js:15-23`).
- Bare Access hostname `/` redirects 302 to `/edit/` (`app/worker/src/editor.js:51-65`).
- `/edit/` redirects by scope: admin→`/edit/admin`; edit/instructor→`/edit/index.html`; no identity→uniform 404 (`app/worker/src/editor.js:274-287`).
- `?t=<opaque>` exchanges for an HttpOnly signed scope cookie and is always removed from the URL (`app/worker/src/editor.js:147-159`).
- Cloudflare Access requires the exact Access hostname, a verified `Cf-Access-Jwt-Assertion`, and an email mapped to a configured slot (`app/worker/src/editor-auth.js:262-305`).
- Cookie and service Bearer credentials are checked before Access (`app/worker/src/editor-auth.js:236-267`).

User-facing edit pages:

- `/edit/<allowlisted public-page path>` — proxy-injected author editor, edit scope (`app/worker/src/editor.js:368-390`).
- `/edit/history/` and `/edit/history/<slug>` — history index/detail, edit or instructor scope (`app/worker/src/editor.js:256-271`).
- `/edit/admin` — admin dashboard (`app/worker/src/editor.js:290-323`).
- `/edit/review` — admin review (`app/worker/src/editor.js:325-335`).
- `/edit/publish` — human Access Publisher only, and only when release ledger is enabled (`app/worker/src/editor.js:337-349`).
- `/edit/instructor/<matter>/<doc>` — instructor facts, notes, or answer key (`app/worker/src/editor.js:351-360`, `tools/build_instructor_bundle.py:4-14`).
- `/edit/assessments/<id>` — Access-authenticated signer review (`app/worker/src/editor.js:362-365`).
- `/edit/release-provenance` — public read-only deployment attestation (`app/worker/src/editor.js:125-139`).
- `/edit/assets/*` and `/edit/site-assets/*` — public static assets required by injected pages (`app/worker/src/editor.js:163-177`).

JSON edit routes are enumerated at `app/worker/src/editor.js:180-254`.

They cover suggest/system-suggest, pending, scoped request/claim/resolve/status, review/decide/digest, apply claim/finalize/reconcile/heartbeat, granular Publisher review/backfill, release prepare/frontier/audit/claim/renew/transition/status, revert, and assessment read/override.

Mutations require their endpoint’s scope and same-origin/CSRF rules; assessment review specifically requires the Access-human `damienadmin` identity plus admin and instructor scope (`app/worker/src/assessment-endpoints.js:18-23`, `app/worker/src/assessment-endpoints.js:58-88`).

## 3. PERSONA → HARNESS MAPPING

### Prospective reader

Existing proof:

- `tools/verify_pitch.py` and `tools/tests/test_verify_pitch.py` prove nine sections, proof summaries, section ordering, 20 matter covers, links, size, and content-boundary rules (`tools/verify_pitch.py:335-448`, `tools/tests/test_verify_pitch.py:211-379`).
- `test_identity_rights_contract.py` proves public identity/licensing wording (`tools/tests/test_identity_rights_contract.py:67-176`).
- `test_cost_per_credit.py` proves the pitch-to-cost links (`tools/tests/test_cost_per_credit.py:45-50`).

Gaps:

- No browser harness opens `site/index.html`.
- No browser assertion clicks individual THE PROOF summaries or the expand/collapse-all control.
- No browser assertion exercises pitch navigation, hero CTA, reaction controls, inline pencils, drawer, or “Copy all for Damien.”
- The pitch is absent from the default accessibility and platform-layout matrices (`tools/a11y_audit.js:48-52`, `tools/platform_browser_matrix.json:14-25`).

### Student

Existing proof:

- Layout/print and accessibility gates cover representative generated families (`tools/platform_browser_matrix.json:12-28`, `tools/a11y_audit.js:223-240`).
- Full-corpus mobile overflow is checked for generated non-chat HTML (`tools/verify_platform_layout.js:75-80`).
- Catalog search/paging/history and hours persistence/fallback have real local browser harnesses (`tools/verify_catalog_client.js:37-61`, `app/hours/verify-hours.js:35-79`).
- Mock interview and critique UI are exercised across 7×2 view/type cases (`tools/verify_chat_critique.js:129-169`).
- Live streaming API smoke proves one m00 turn and replay, not UI (`app/worker/test/live-stream-smoke.mjs:342-423`).
- Sample mode is unit/static-contracted and deliberately avoids API/session (`app/chat/chat.js:1151-1156`).
- Packet/facts/law/archive contracts cover all matters (`tools/tests/test_facts_surface.py:178-236`, `tools/tests/test_law_pages.py:120-177`, `tools/tests/test_catalog_contract.py:15-87`).

Gaps:

- No automated browser journey traverses home→module→matter→facts/law→download→firm→hours→templates.
- No browser harness opens and validates every one of the 20 packet pages beyond layout/overflow.
- No harness downloads and opens a ZIP through the served HTTP surface.
- No live browser harness completes Turnstile, enters a BYOK key, conducts six turns, requests debrief, or submits critique.
- No live harness proves provider failures/retry UX in the actual UI.
- No live browser harness covers the `?sample=1` play/pause/skip/export journey.

### Instructor

Existing proof:

- Instructor bundle build renders facts, instructor notes, and answer keys server-only and self-checks leak boundaries (`tools/build_instructor_bundle.py:2-29`, `tools/build_instructor_bundle.py:136-160`).
- Memo instrument, configuration, panel, persistence, and signer page are extensively unit-tested (`tools/tests/test_assessment_instrument.py:79-157`, `app/worker/test/assessment-config.test.js:28-94`, `app/worker/test/assessment-review.test.js:139-280`).
- Live assessment preparer creates one disposable audit and returns its review URL (`app/worker/test/assessment-live-uat.mjs:179-229`).

Gaps:

- The live preparer does not open the review URL or authenticate through Access.
- No browser harness inspects a real signer-review page, changes a heading score, supplies a reason, submits an override, reloads, and verifies attribution.
- No live browser harness navigates all instructor documents or makes an instructor edit.
- Rubric display is layout/static tested, but no instructor UAT compares every displayed rubric to its source while signed in.
- Human-human calibration remains a named blocker rather than an automated journey (`app/worker/test/platform-language-contract.test.js:39-48`).

### Author-editor John and Roger

Existing proof:

- Local real-client editor harness covers save/comment/race/recovery/scoped-change behavior against mocks (`app/editor/verify-editor.js:1-12`).
- Rail geometry is checked across ten widths and can target a live `?t=` page (`app/editor/verify-rail-placement.js:11-18`).
- Access/JWT, token/cookie coexistence, author scope, Roger isolation, and JOS/RSH attribution are unit-tested (`app/worker/test/editor-access-door.test.js:1-12`, `app/worker/test/editor-roger.test.js:1-6`).
- History/revert routes and generated redlines have unit/static tests (`app/worker/test/editor-revert.test.js:1-4`, `tools/tests/test_build_history.py:1-5`).

Gaps:

- No automated browser test completes the real Cloudflare Access login for John or Roger.
- No live browser journey proves landing-door redirect, page edit, save persistence, cross-editor overlay, history view, and revert request as one sequence.
- `verify-editor.js` uses a mock harness, not the injected live page or Durable Object.
- The optional live rail test proves geometry only.
- No browser UAT proves Roger sees RSH and John sees JOS in a shared live history/review flow.

### Admin-Publisher Damien

Existing proof:

- Local Publisher browser harness proves granular decision payloads, required questioned notes, single-flight authorization, and accessible settled status (`tools/verify_publisher_client.mjs:42-93`).
- Worker tests cover `/edit/admin`, `/edit/review`, `/edit/publish`, granular reviews, authorization, release ledger, provenance, and scope separation (`app/worker/test/editor-admin.test.js:1-12`, `app/worker/test/editor-publisher-review.test.js:1-6`, `app/worker/test/editor-publisher-release.test.js:1-6`).
- Python tests cover readiness, executor, daemon, bootstrap, environment migration, and closed publication bypasses (`tools/tests/test_prod_release_operations.py:19-117`, `tools/tests/test_prod_release_readiness.py:48-144`).
- Readiness CLI is deliberately GET-only and rejects ambient mutation credentials (`tools/prod_release_readiness.py:190-210`).

Gaps:

- The Publisher browser harness is localhost with mocked fetch, not Access plus a real ledger.
- No automated browser journey goes admin→review→Publisher review→publish authorization against DEV.
- No harness automates the human judgment of reviewing authored text.
- Live provider deployment, canary, exact-pair restore, and secret handling are intentionally excluded from repository-only tests (`docs/prod-release-operations.md:319-320`).
- Therefore repository tests prove safety contracts, not that a real publication completed.

### Open-source adopter

Existing proof:

- README gives clone/static-serve, Worker test, rebuild, and Wrangler paths (`README.md:44-75`).
- Build/link/leak gates and Worker unit suite prove local artifacts and server logic (`tools/build_site.py:4085-4145`, `README.md:65-75`).
- Identity/license tests verify public adoption terms (`tools/tests/test_identity_rights_contract.py:141-176`).

Gaps:

- No clean-container/fresh-machine test follows README from clone to served pages.
- No UAT verifies missing optional `jsonschema` behavior on a bare clone.
- No automated Wrangler local/self-host deployment journey updates `sonsteng-api` and matching CORS origins.
- No localhost end-to-end chat test exists; the checked-in Worker allowlist does not include localhost (`app/worker/wrangler.jsonc:80`, `app/worker/wrangler.jsonc:259`).
- No browser test proves an adopter’s own Worker, Turnstile domain configuration, or BYOK setup.

### School / ABA reader

Existing proof:

- Proposal contract checks privacy-preserving joins, measurement definitions, missingness, claim limits, synthetic examples, and school authority (`tools/tests/test_competency_credit_proposal.py:18-172`).
- Cost calculator has static math/contracts, browser interaction, and explicit accessibility audit (`tools/tests/test_cost_per_credit.py:35-154`, `tools/preflight.sh:85-86`).
- The page shows checkable Standard 310(b) workload arithmetic (`site/cost-per-credit.html:32-34`).

Gaps:

- `docs/proposals/competency-based-credit.md` is documentation, not a user-reachable HTML route in this repository.
- No harness tests comprehension, decision usefulness, or institutional legal/ABA acceptance.
- No UAT connects the calculator to the proposal or exports a study dataset.
- The proposal explicitly says it does not establish causation or award/recommend credits (`docs/proposals/competency-based-credit.md:187-193`).

### Accessibility user

Existing proof:

- Rendered-DOM audit checks core WCAG-adjacent properties on curated pages (`tools/a11y_audit.js:9-27`).
- Layout matrix checks heading counts/jumps, unnamed controls, overlap, overflow, Large Type, and print (`tools/verify_platform_layout.js:28-50`, `tools/verify_platform_layout.js:61-80`).
- Chat/critique matrix checks heading order, 24 px controls, Large Type, and overflow (`tools/verify_chat_critique.js:38-69`, `tools/verify_chat_critique.js:100-126`).
- Cost page gets a dedicated audit (`tools/preflight.sh:85-86`).
- Assessment review unit tests require native keyboard controls and screen-reader labels (`app/worker/test/assessment-review.test.js:197-218`).

Gaps:

- Default a11y does not include the pitch, live chat pages, hours, admin, Publisher, history, instructor, or signer routes.
- Default a11y uses 1280×900; it does not sweep accessibility at every viewport (`tools/a11y_audit.js:228-235`).
- No screen-reader, speech, zoom/reflow beyond Large Type, forced-colors, or keyboard-only end-to-end run exists.
- No live Turnstile accessibility journey exists.
- The generic rendered audit is not a substitute for assistive-technology UAT.

### Hostile actor

Existing proof:

- Live `redteam.mjs` probes concealed leakage, prompt/meta escape, sycophancy, invented facts, debrief oracle, and legitimate debrief (`app/worker/test/redteam.mjs:13-27`).
- Offline prompt scan covers eight named jailbreak angles but labels itself partial/non-live (`tools/offline_redteam_probe.mjs:2-18`).
- Deterministic redaction tests use real bundled secrets, crafted obfuscations, fail-closed free-text detection, false-positive carve-outs, and cross-matter data (`app/worker/test/offline-redteam-redaction.test.js:12-24`, `app/worker/test/offline-redteam-redaction.test.js:246-335`).
- `validate.js` rebuilds missed fields and scans unreconstructed free-text fields for unelicited concealed/gated facts (`app/worker/src/validate.js:228-315`).
- Access, auth, CORS, Turnstile, SSRF/XSS, path, uniform-404, and logging boundaries have focused Worker tests (`app/worker/test/access-jwt.test.js:1-8`, `app/worker/test/editor-map.test.js:1-16`, `app/worker/test/editor-security.test.js:1-4`).
- Static canaries prove semantic/language/consistency checks can detect perturbations (`tools/tests/test_platform_semantic_contract.py:51-101`, `tools/tests/test_platform_language_contract.py:99-111`, `tools/tests/test_editor_consistency.py:479-509`).

Gaps:

- Live red-team requires a deployed URL and real provider key and is outside the unit glob (`README.md:69-70`).
- No recorded evidence here establishes that it has been run against all three providers in the current deployment.
- No hostile browser harness attacks Turnstile, BYOK UI storage, DOM injection, live editor cookies, or Access login.
- Heuristic `REVIEW` outcomes require human judgment and are not machine passes (`app/worker/test/redteam.mjs:26-27`).

## 4. LEARNINGS FROM EVERY `docs/solutions/**` FILE

### `docs/solutions/editor/2026-07-28-checks-that-cannot-fail.md`

Title: “Checks that cannot fail: four green gates that were measuring nothing” (`docs/solutions/editor/2026-07-28-checks-that-cannot-fail.md:1-8`).

Lesson: absence checks need a positive canary/catch-rate proof; compare actual idempotency values; trust exit codes rather than fixed assertion counts; build the complete production surface (`docs/solutions/editor/2026-07-28-checks-that-cannot-fail.md:43-56`).

UAT constraints: every persona checklist needs at least one deliberate failing perturbation; pitch/layout/leak tests cannot be credited merely because they report zero findings; editor dedupe must compare IDs; corpus claims must include all generated pages.

### `docs/solutions/editor/2026-07-28-durable-block-identity.md`

Title: “Durable block identity: making content addressable when it can move” (`docs/solutions/editor/2026-07-28-durable-block-identity.md:1-8`).

Lesson: generated non-reused source IDs survive content edits and moves; positional index is placement only; JSON scalar paths are already durable (`docs/solutions/editor/2026-07-28-durable-block-identity.md:24-43`).

Migration proof must check page/index/text/kind/file 1:1 and sweep markers from all shipped outputs, including verbatim student exports (`docs/solutions/editor/2026-07-28-durable-block-identity.md:61-84`).

UAT constraints: John/Roger structural edits, history, reload overlays, and student downloads must preserve identity without displaying `{#b:...}` markers.

### `docs/solutions/editor/2026-07-28-generated-artifacts-are-not-tracked-state.md`

Title: “A revert restores tracked files; it does not restore generated ones” (`docs/solutions/editor/2026-07-28-generated-artifacts-are-not-tracked-state.md:1-8`).

Lesson: after data mutation/revert, rebuild editor map, instructor/persona/history bundles and redeploy the Worker; a git revert alone leaves stale runtime allowlists (`docs/solutions/editor/2026-07-28-generated-artifacts-are-not-tracked-state.md:11-34`).

Second lesson: core-only Durable Object tests do not prove wrapper RPC forwarding; live instances may retain an old script briefly (`docs/solutions/editor/2026-07-28-generated-artifacts-are-not-tracked-state.md:36-60`).

UAT constraints: author revert, instructor pages, student pages, and Publisher release must verify parity plus live RPC wiring after deploy, not only source state.

### `docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md`

Title: “The headful harness needs XAUTHORITY, not just DISPLAY — and its absence reads as ‘no browser’” (`docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md:1-8`).

Lesson: headful Xwayland requires both `DISPLAY` and a per-boot `XAUTHORITY`; socket existence is insufficient (`docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md:32-60`).

The editor suite takes well over ten minutes; use 1800 seconds (`docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md:62-64`).

The document says sandboxed delegated workers cannot perform this headful verification (`docs/solutions/editor/2026-07-28-headful-harness-needs-xauthority.md:76-90`).

UAT constraints: any supervised visual John/Roger/admin/accessibility run needs an interactive desktop session, correct Xauthority, a long timeout, and execution by the controlling/orchestrating process.

### `docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md`

Title: “Fleet-building a 20-matter legal-ed platform in one day” (`docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md:1-8`).

Lessons: schema/vocabulary first, per-author self-gates, validator as product spine, code-enforced safety, common design tokens, and an early keyless demo (`docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md:10-19`).

Critical UAT lesson: 56 unit tests and link checks missed a dead critique deep link; a stranger’s real browser journey found it in 30 minutes (`docs/solutions/orchestration/2026-07-17-fleet-built-platform-in-a-day.md:15-16`).

UAT constraints: student critique/session mint, first-time adopter flow, and end-to-end navigation must remain separate required gates from unit coverage.

### `docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md`

Title: “A systemd timer in a path with a space, and the em dash that ate the notification” (`docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md:1-8`).

Lesson: `ExecStart` needs quoting, `WorkingDirectory` must not be quoted, local `Documentation=file://` cannot contain raw spaces, and HTTP headers need Latin-1-safe text (`docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md:21-40`, `docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md:42-67`).

UAT constraints: Publisher/apply/release timer installation is not proved until the service is fired once and the journal read; notification success needs non-ASCII canaries (`docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md:72-84`).

## 5. DEV/PROD DIFFERENCES THAT AFFECT UAT

Configuration is intentionally duplicated because Wrangler `vars` are non-inheritable (`app/worker/wrangler.jsonc:10-23`).

### DEV/default

- Worker name `sonsteng-chat`; bare deploy targets DEV (`app/worker/wrangler.jsonc:10-18`, `app/worker/wrangler.jsonc:24-25`).
- `STREAMING=true` (`app/worker/wrangler.jsonc:93-100`, `app/worker/wrangler.jsonc:205`).
- `TURNSTILE_ENABLED=true` and a public sitekey is configured (`app/worker/wrangler.jsonc:102-114`, `app/worker/wrangler.jsonc:206-207`).
- `EDIT_UPSTREAM` is DEV static site (`app/worker/wrangler.jsonc:117-119`).
- `EDIT_ORIGIN` includes both Access hostname and DEV workers.dev fallback (`app/worker/wrangler.jsonc:120-129`).
- Access audience/team/host and legacy host are configured (`app/worker/wrangler.jsonc:131-148`).
- Public alias redirects and canonical host are configured (`app/worker/wrangler.jsonc:149-151`).
- `damienadmin` has edit+instructor+admin+publisher; release service exists (`app/worker/wrangler.jsonc:153-166`).
- `PROD_RELEASE_LEDGER=true` and `DIRECT_APPLY=true` (`app/worker/wrangler.jsonc:165-177`).

### PROD

- Separate worker `sonsteng-chat-production`; inherited Access custom-domain routes are explicitly cleared (`app/worker/wrangler.jsonc:228-251`).
- `STREAMING=false` (`app/worker/wrangler.jsonc:258-271`).
- Turnstile remains enabled but needs its own production secret (`app/worker/wrangler.jsonc:272-277`).
- `EDIT_UPSTREAM` is `https://legalpracticum.org/platform/` (`app/worker/wrangler.jsonc:278-279`).
- `EDIT_ORIGIN` is the production workers.dev origin (`app/worker/wrangler.jsonc:280-281`).
- Token scopes omit `damienadmin`, publisher, and release service; `PROD_RELEASE_LEDGER=false` (`app/worker/wrangler.jsonc:282-283`).
- `DIRECT_APPLY=false`; production saves require review (`app/worker/wrangler.jsonc:287-289`).

### Deployment consequences

- DEV deploy archives a named branch (default `main`), rsyncs with delete, and restarts a Hetzner Docker Compose project (`deploy/deploy-dev.sh:1-24`).
- README warns to pass the branch explicitly and never use `--remove-orphans` (`README.md:86-90`).
- PROD is Cloudflare Pages through the Publisher release lane; DEV is Hetzner; API is Worker; editor is Access plus default DEV Worker (`README.md:77-84`).
- Approval, DEV application, preparation, Publisher authorization, and publication are distinct states (`docs/prod-release-operations.md:24-31`, `docs/prod-release-operations.md:309-317`).
- Production release executor always targets Wrangler `production`; Pages uses its configured production branch (`docs/prod-release-operations.md:3-11`).
- DEV apply daemon cannot publish PROD; direct prod deploy is disabled; only an already-authorized ledger record reaches the release executor (`docs/prod-release-operations.md:33-43`).
- First production publication is a supervised one-shot canary with the timer stopped and persistent config off (`docs/prod-release-operations.md:149-157`).
- UAT plans must therefore run streaming expectations on DEV only, direct-apply expectations on DEV only, Publisher ledger journeys on DEV, and review-only/nonstreaming expectations on PROD.

## 6. HOW THE SITE IS BUILT AND SERVED LOCALLY

### Build

- `tools/build_site.py` renders the data spine into `site/platform/`; Python standard library only; normal and `--check` invocation are documented at the file top (`tools/build_site.py:1-19`).
- Main cleans output, copies static chat/hours, builds home/modules/templates/skills/library/packets/facts/law/firm/licenses/data, and emits editor map plus parity stamp (`tools/build_site.py:3992-4028`).
- Chat source is copied verbatim except `test.html`; hours source is copied except verifier files (`tools/build_site.py:910-933`).
- Instructor facts, notes, answer keys, and confidential persona text must never render into student pages (`tools/build_site.py:18-19`).
- `--check` makes link, instructor leak, marker leak, history leak, and page-ceiling findings fatal (`tools/build_site.py:4085-4145`).

### Local static serving

README quickstart:

```bash
cd site && python3 -m http.server 8791
```

The platform is then at `http://localhost:8791/platform/` (`README.md:48-51`).

What local static serving can exercise:

- Pitch, cost calculator, platform home, modules, skills, matter library, all packets/facts/law pages, firm dashboard, templates, license pages, and downloads.
- Weekly hours, including browser-local persistence and exports.
- Static sample interview at `chat/index.html?...&sample=1`, because it uses the same-origin sample JSON and no Worker/session/key (`app/chat/chat.js:1151-1187`).
- Static page navigation, responsive/print presentation, Large Type, and downloadable assets.
- README explicitly says curriculum, packets, and dashboard work immediately (`README.md:53-54`).

The `sonsteng-api` meta tag:

- Checked-in interview page points to the deployed DEV Worker (`app/chat/index.html:33-39`).
- Checked-in critique page points to the same Worker (`app/chat/critique.html:9-13`).
- API resolution precedence is `?api=` → localStorage → `sonsteng-api` meta → same-origin `/api` (`app/chat/chat.js:39-49`).
- An adopter self-hosting the Worker is instructed to point the site at it via this meta tag (`README.md:60-63`).

What local static serving alone cannot exercise:

- A working live chat against the checked-in deployed Worker from localhost, because Worker CORS allowlists deployed DEV/PROD origins, not localhost (`app/worker/wrangler.jsonc:80`, `app/worker/src/index.js:638-645`).
- Any `/edit/*` route, Cloudflare Access JWT flow, `?t=` exchange, injected editor, history, instructor, admin, Publisher, assessment signer, or release ledger; those are Worker-served routes (`app/worker/src/index.js:610-636`, `app/worker/src/editor.js:119-139`).
- Durable Object session budgets, editor persistence, review state, assessment audits, or publication state.
- A real Turnstile session mint without a compatible widget/domain/Worker configuration; static HTML only loads the widget/client path (`app/chat/index.html:31-39`, `app/chat/chat.js:1055-1135`).
- Hosted/BYOK model calls, streaming, debrief, memo assessment, or critique without a reachable configured Worker and valid session (`README.md:53-63`).
- Server-only instructor bundle content, which is intentionally outside `site/platform/` (`tools/build_instructor_bundle.py:16-24`).

## Planning bottom line

The repository already has broad deterministic contract coverage and several high-value local browser gates.

The persona program should preserve those as preconditions, then add journey-level UAT where the current evidence stops:

1. pitch interaction/accessibility in a browser;
2. one complete student navigation/download/hours/sample journey;
3. one live Turnstile+BYOK+six-turn+debrief journey and one live critique deep link;
4. one real Access journey each for author, instructor/signer, and admin-Publisher;
5. a John↔Roger shared edit/history/attribution journey;
6. a real Publisher review/authorization readiness journey without deploying;
7. a clean-machine README quickstart/self-host configuration journey;
8. accessibility runs on dynamic authenticated routes with keyboard and assistive technology;
9. current live red-team runs per supported provider.

These are gaps in automated journey evidence, not claims that the product behavior is broken.
