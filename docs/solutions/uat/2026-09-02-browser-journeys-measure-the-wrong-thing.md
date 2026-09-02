---
title: "Browser journeys that measured the wrong thing: eight runner and process defects from the persona UAT program"
category: uat
tags: [uat, puppeteer, snap, accessibility, flaky-tests, preflight, merge-gate, verification]
module: tools/verify_persona_journeys.js
symptom: "Journeys failed on real pages that were fine, passed on pages that were broken, or flipped verdict by environment; two merges went through on gates that had not passed"
root_cause: "Each check measured a proxy for the behaviour instead of the behaviour: DOM order instead of the control, the host's /tmp instead of the browser's, navigation commit instead of document completion, CSS-transformed text instead of DOM text, a fixed request order instead of the probe's sequencing, and a grep that matched both outcomes instead of the gate's exit"
related: [docs/plans/2026-09-02-1108-test-persona-uat-program-plan.md, docs/uat/journey-schema.md, docs/uat/persona-uat-record.md, docs/handoffs/2026-09-02-persona-uat-closeout.md, docs/solutions/editor/2026-07-28-checks-that-cannot-fail.md]
---

# The pattern

The persona UAT program ran fifty-odd browser journeys against local, DEV, and
production builds on 2026-09-02. Eight distinct failures came out of the day, and
none of them was the product being wrong in the way the failing assertion said.
They share one shape:

> The check measured something adjacent to the behaviour, and the adjacent thing
> diverged from the behaviour exactly when the environment changed.

Every fix below replaced the proxy with the thing itself. Every one carries a
regression test that fails on the old code.

## The runner defects

1. **A skip-link `<main tabindex="-1">` hijacked every name-based click.** The
   control lookup collected `a[href],button,…,[tabindex]`, matched names by
   substring, and returned the first visible match in DOM order. PR #34 added a
   focusable `<main>` to every platform page so skip links could land on it. From
   then on, `<main>` came first, was visible, and its text contained every name
   on the page, so "click Fact gathering" clicked the page body and the
   following `waitFor url` timed out. Fix: exclude non-interactive `tabindex=-1`
   containers, rank exact names above substring matches, prefer the shortest
   substring match. The debug preload that exposed it logged
   `click MAIN | M1 Module 1 — …` where a link was expected.

2. **The snap Chromium's private `/tmp` hid completed downloads.** The runner
   launched `/snap/bin/chromium` and pointed `Browser.setDownloadBehavior` at a
   directory under `/tmp`. Snap confinement gives the browser its own `/tmp`
   namespace; the CDP events fired (`downloadWillBegin`, `downloadProgress`
   completed, 21,728 bytes) and the file landed where the host could never see
   it. The same code with an unconfined Chromium worked first try, which is what
   made the diagnosis land. Fix: download and profile directories live under
   `build/uat/` inside the repository, which the snap's home interface can see.

3. **`waitFor url` resolved on navigation commit, before deferred scripts ran.**
   The large-type journey clicked into the skills page and asserted
   `aria-pressed="true"` on the toggle, which `platform.js` (loaded with `defer`)
   sets. Locally and on Cloudflare Pages the document was complete by the time
   the URL changed; on the slower nginx DEV origin the URL changed at ~200 ms with
   `readyState` still `loading` and the attribute flipped at ~500 ms. Assets were
   byte-identical across environments. Fix: URL waits also require
   `document.readyState === "complete"`, and attribute assertions and control
   lookups poll for up to 2.5 s.

4. **`innerText` applies CSS `text-transform`.** The live-region assertion joined
   `innerText`, so a `.card__meta { text-transform: uppercase }` status read
   "20 MATTERS · PAGE 1 OF 1" and the check for "20 matters" failed. Assistive
   technology announces the DOM text. Fix: compare collapsed `textContent`.

5. **A click inside a closed `<details>` does nothing and reports nothing.** The
   reactions journey clicked a vote button that lives inside a collapsed proof
   disclosure. Puppeteer delivered the click to an element with a bounding box
   but no rendering; no handler ran; the counter stayed at 0; the assertion
   blamed the counter. Fix: the journey opens the disclosure and asserts it is
   open before voting, and a contract test pins the order.

6. **Scroll-reveal opacity made resting content "invisible".** `.reveal` elements
   start at `opacity: 0` until an IntersectionObserver sees them, so
   `checkVisibility({checkOpacity: true})` on unscrolled content was false. Fix:
   emulate `prefers-reduced-motion: reduce` so the audit and the journeys
   measure the resting state, and scroll assertion targets into view first.

7. **The bot-gate probe built `https://v1/session`.** Joining a base whose
   pathname was `/` with `/v1/session` produced `//v1/session`, which the URL
   parser reads as protocol-relative. Every probe reported "fetch failed" and
   looked like a network outage; a plain `fetch` of the real URL answered 403.
   A second defect hid behind it once fixed: the three probes ran concurrently,
   and the integration test asserted their arrival order at a threaded server,
   so the test flaked. Fix: a unit-tested URL helper and sequential probes.

## The process defects

8. **A merge gate that matched both outcomes.** The merge command chained
   `grep -E 'passed, [0-9]+ failed' && gh pr merge`. The pattern matches
   "17 passed, 4 failed" as happily as "21 passed, 0 failed", so PR #37 merged on
   a red preflight. The failures were environmental (below), and a clean
   preflight on the merged main passed 21/21, but the gate was wrong on its own
   terms. Gate on the exit code or on the literal `, 0 failed`; never on a
   pattern that describes the report's shape.

9. **Fresh worktrees fail three Worker gates until the generators run.** The
   Worker's `editor-data/` bundles are gitignored and produced by
   `build_site.py --check`, `build_instructor_bundle.py`, and
   `bundle-editor-data.mjs`. A worktree cut from main and taken straight to
   preflight fails bundle parity, the Worker unit tests, the review-migration
   contract, and the Publisher client gate, none of which is the change under
   test. The adopter persona hit the same wall from a fresh clone, which is why
   the README now documents the three commands and the adopter journey runs them
   (PR #35). Run the generators before every preflight in a fresh worktree.

# How to apply

- When a journey fails, reproduce the single step in an unconfined browser with
  the runner's own helpers before touching the product. Six of the seven runner
  defects were diagnosed that way in minutes; the product was innocent each time.
- A verdict that changes with the origin (local vs DEV vs production) and not
  with the assets is a timing race in the runner until proven otherwise.
- Treat "fetch failed" from a probe as a URL bug first and a network problem
  second.
- Preflight in a fresh worktree: generators first, then the gate, then merge on
  `, 0 failed`.
