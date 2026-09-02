# Persona Journey Schema

`tools/persona_journeys.json` is the coverage authority for persona UAT. Browser journeys, existing harnesses, and adopter commands all use the same catalog; only browser journeys are executed by `tools/verify_persona_journeys.js`.

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
| `expectDownload` | `pattern`, optionally `selector` or `name` | Optionally clicks, then records the downloaded filename and byte size; the downloaded file stays temporary. |
| `assert` | `kind`, `check`, and kind-specific data | Proves the named 1-based acceptance-check index from the story. |

Assertion kinds are `selector`, `text`, `attr`, `url`, `consoleClean`, `focusOn`, `a11yName`, `a11yRole`, `a11yState`, `readingOrder`, and `liveRegion`. Accessibility assertions use Puppeteer’s accessibility snapshot, so name, role, value, and state come from the browser accessibility tree rather than DOM text alone.

## Harness and command bindings

A `harness` binding records `command` and `story_checks`. A `command` binding records those fields and may also name its `local_target` and `account_boundary`. The browser runner records these bindings as `NOT RUN`; their owning UAT unit writes the actual harness or command result into a run file.

## Evidence and retries

Each invocation writes `build/uat/runs/<UTC>-<environment>.json`. Every browser attempt contains its journey, story, persona, viewport, verdict, first failure, SHA-256 screenshot digest, duration, canary flag, and retry number. PASS screenshots are deleted after hashing. FAIL and ERROR screenshots remain below `build/uat/shots/<run>/` until triage. Infrastructure errors are `ERROR`, retried once, and both attempts remain in history; an HTTP error response is `FAIL` because the browser reached the surface.

The runner reads `/platform/data/.build-stamp.json` and the `x-release-sha` header on `/platform/` once per invocation. The renderer selects the latest attempt per story, environment, and viewport on the latest observed build for each environment, retains all attempts in history, and excludes canaries from persona counts.
