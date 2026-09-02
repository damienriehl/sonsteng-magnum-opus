# Persona Journey Schema

`tools/persona_journeys.json` is the coverage authority for persona UAT. Browser journeys, existing harnesses, and adopter commands all use the same catalog. `tools/verify_persona_journeys.js` runs browser steps in its default mode and harness/command bindings in `--bindings` mode; the two modes never execute each other's entries.

## Journey

Every journey has:

- `id`: unique lowercase kebab-case journey ID.
- `story`: stable `US-<persona>-<number>` ID from `user-stories.md`, or that persona's reserved `US-<persona>-CANARY` failure ID.
- `persona`: `A1` through `A10`.
- `canary`: optional boolean. A canary deliberately fails and is excluded from persona verdict counts.
- `viewports`: one or more of `desktop`, `phone`, or `zoom200`.
- `binding`: exactly one of `steps`, `harness`, or `command`, accompanied by the field with the same name.

The fixed viewports are desktop at 1280×900 CSS pixels and device scale factor 1; phone at 390×844 with mobile and touch emulation at device scale factor 2; and 200% zoom at 640×450 CSS pixels and device scale factor 2. The last reproduces a 1280-pixel browser window at 200% browser zoom.

## Browser steps

A `steps` binding is an ordered list. Supported operations are:

| Operation | Required data | Effect |
|---|---|---|
| `goto` | `path` | Opens a path relative to `--base`; HTTP 4xx and 5xx are journey failures. |
| `click`, `focus` | `selector` or accessible `name` | Finds and activates or focuses one control. |
| `press` | `key` | Sends a Puppeteer keyboard key such as `Tab` or `Enter`. |
| `type` | `selector` or `name`, plus `text` | Types into a control. |
| `waitFor` | one of `selector`, `text`, or `url` | Waits for visible content or a URL substring. |
| `expectDownload` | `pattern`, optionally `selector` or `name` | Enables the browser download before the optional click, waits for a completed filename matching the glob pattern, and records its filename and byte size; the downloaded file stays temporary. |
| `assert` | `kind`, `check`, and kind-specific data | Proves the named 1-based acceptance-check index from the story. |

Assertion kinds are `selector`, `text`, `attr`, `url`, `consoleClean`, `focusOn`, `a11yName`, `a11yRole`, `a11yState`, `readingOrder`, and `liveRegion`. A `selector` assertion may set `visible` to `true` or `false`; visibility uses `checkVisibility()` when the browser supports it. An `a11yName` assertion matches exactly by default and may set `contains: true` for a whitespace-collapsed substring match. Accessibility assertions use Puppeteer’s accessibility snapshot, so name, role, value, and state come from the browser accessibility tree rather than DOM text alone.

Before a `text`, selector-visibility, `focusOn`, or accessibility assertion is evaluated, the runner scrolls its target to the center of the viewport and polls for as long as 2,500 ms for the target’s resting visibility. Text matching uses whitespace-collapsed `textContent` on an attached element with a non-zero bounding rectangle. Each browser page also emulates `prefers-reduced-motion: reduce`, making reveal and transition effects immediate. These are deliberate resting-state measurements: persona UAT evaluates the usable content after motion settles, not a transient animation frame or an element that has not yet crossed an intersection threshold.

Name-based control lookup ignores non-interactive containers whose only candidacy is `tabindex="-1"`, such as skip-link focus targets. Exact normalized names rank above substring matches; visible controls rank above hidden controls within each tier; and substring-only matches prefer the shortest normalized name. This keeps broad containers from shadowing the specific link or button named by a journey.

Downloads use the Chrome DevTools Protocol Browser domain with `allowAndName` and completion events. The event’s suggested filename is matched against `pattern`; after completion the runner records that filename and the stored file’s byte size. If Browser-domain events are unavailable, the runner falls back to Page-domain download allowance and polls the temporary download directory.

Browser profiles and per-attempt downloads live under `build/uat/profiles/<run-id>/` and `build/uat/downloads/<run-id>/`. They deliberately do not use `/tmp`: snap-packaged Chromium sees a private, confinement-specific `/tmp` namespace that the host-side runner cannot inspect or remove. The repository-local `build/` tree is gitignored, available through snap's home interface, and each run's profile and download directories are removed after the browser closes.

## Harness and command bindings

A `harness` binding records `command` and `story_checks`. A `command` binding records those fields and may also name its `local_target` and `account_boundary`. Either binding may add:

- `environments`: a non-empty list of environment labels where the binding may run. A mismatch records `NOT RUN` and names the allowed labels.
- `credential_gate`: the name of an environment variable that must be set before execution. An unavailable credential records `BLOCKED` without starting the command.

Run bindings with `node tools/verify_persona_journeys.js --bindings --env-label <label>`. `--bindings` and the browser mode's `--base` are mutually exclusive. `--only` filters journey IDs within the selected mode, and `--binding-timeout <ms>` sets the per-command timeout (default 1,800,000 ms). A binding command runs from the repository root through `sh -c` with `HEADLESS=1` and `EDITOR_HEADLESS=1`.

Commands must begin with `node`, `python3`, `python`, `npx`, `bash`, `sh`, `cd`, `git`, `pytest`, or `curl`. A command containing an angle-bracket placeholder such as `<repository-url>`, or one that begins with descriptive prose, records `NOT RUN` as non-executable and is never passed to the shell. `{{WORKER_URL}}` is a supported runner substitution: `dev` selects `https://sonsteng-chat.damienriehl.workers.dev`, `prod` selects `https://sonsteng-chat-production.damienriehl.workers.dev`, and `local` records `NOT RUN`.

Exit zero records `PASS`; a non-zero exit records `FAIL`; and a timeout or spawn failure records `ERROR` and is retried once. Every process attempt uses viewport `n/a`. The complete combined stdout/stderr is retained as `build/uat/shots/<run>/<journey>-binding.log`, its SHA-256 is the attempt digest, and only the last 40 output lines appear in `first_failure` for `FAIL` or `ERROR`. The runner does not print or prepend its environment to command logs.

## Evidence and retries

Each invocation writes `build/uat/runs/<UTC>-<environment>.json`. Verdicts are `PASS`, `FAIL`, `OPEN`, `BLOCKED`, `NOT RUN`, and `ERROR`. Every browser attempt contains its journey, story, persona, viewport, verdict, first failure, SHA-256 screenshot digest, duration, canary flag, and retry number. PASS screenshots are deleted after hashing. FAIL and ERROR screenshots remain below `build/uat/shots/<run>/` until triage. Infrastructure errors are `ERROR`, retried once, and both attempts remain in history; an HTTP error response is `FAIL` because the browser reached the surface.

The runner reads `/platform/data/.build-stamp.json` and the `x-release-sha` header on `/platform/` once per invocation. The renderer selects the latest attempt per story, environment, and viewport on the latest observed build for each environment, retains all attempts in history, and excludes canaries from persona counts.
