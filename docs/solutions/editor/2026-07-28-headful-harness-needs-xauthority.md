---
title: "The headful harness needs XAUTHORITY, not just DISPLAY — and its absence reads as 'no browser'"
category: editor
tags: [verification, headful, puppeteer, xwayland, environment, delegation, subagent]
module: editor
symptom: "`node app/editor/verify-editor.js` dies with 'Failed to launch the browser process' or 'Missing X server to start the headful browser', and a delegated worker concludes the box has no browser"
root_cause: "A non-login shell inherits neither DISPLAY nor XAUTHORITY; mutter's Xwayland auth cookie lives at a per-boot random path, so DISPLAY=:0 alone still fails the X handshake"
related: [docs/solutions/editor/2026-07-28-checks-that-cannot-fail.md, docs/handoffs/2026-07-28-word-like-editing-handoff.md]
---

# The symptom

`app/editor/verify-editor.js` is the headful suite — the one that drives the
real client in a real browser and produced the 56/56 count quoted throughout
the word-like-editing handoff. Run from an agent shell it fails immediately:

```
HARNESS ERROR Error: Failed to launch the browser process: Code: 1
```

and, once `DISPLAY` is set but nothing else, with the more honest:

```
HARNESS ERROR Error: Missing X server to start the headful browser.
Either set headless to true or use xvfb-run to run your Puppeteer script.
```

Both messages invite the wrong conclusion. There is no xvfb on this box
(`which xvfb-run` → nothing), and switching to headless is explicitly against
the house rule. The browser is fine; the shell just cannot reach the display.

# The cause

The file header says `DISPLAY=:0 puppeteer (headful) on the home box's
Xwayland`, and that is true but incomplete. Two variables are required, and an
agent shell inherits **neither**:

```
DISPLAY=:0
XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.XXXXXX
```

`/tmp/.X11-unix/X0` existing is not evidence the display is usable — the socket
is there, the handshake is what fails. `DISPLAY=:0 xdpyinfo` reports *not
reachable* until the auth cookie is named.

**The cookie path is randomised per boot.** Do not hardcode the suffix. Derive
it from the running Xwayland process, which is the only authority:

```bash
XW=$(pgrep -x Xwayland | head -1)
export XDG_RUNTIME_DIR=/run/user/1000
export DISPLAY=$(tr '\0' '\n' < /proc/$XW/environ | sed -n 's/^DISPLAY=//p')
export XAUTHORITY=$(tr '\0' '\n' < /proc/$XW/environ | sed -n 's/^XAUTHORITY=//p')
xdpyinfo >/dev/null && node app/editor/verify-editor.js
```

Confirm with `xdpyinfo` before blaming the harness. If Xwayland is not running
at all — a headless reboot, no desktop session — that is a genuinely different
problem and the suite cannot run until someone logs in.

The suite is also **slow**: it drives several viewports and takes well over ten
minutes. A 600-second timeout kills it mid-run and looks like a hang. Give it
1800s and run it in the background.

# Why this one is worth writing down

It did not merely cost time — it produced a **false negative about the
environment**. A delegated worker, correctly refusing to fabricate results,
reported that the browser could not launch and returned its implementation
*unverified*: no red-before-green, and no perturbation proof for a change whose
entire subject was a test harness that could not fail. Under this repo's own
documented lesson, unverified harness work is the exact thing that must not be
committed.

Two rules follow:

1. **The headful suite is NOT delegable to a sandboxed worker. The orchestrator
   must run it.** Handing a Codex worker the recipe above is not enough, and
   this was tested: a second worker, given the exact commands, still could not
   run it. The Codex sandbox has a **private PID namespace** — `pgrep -x
   Xwayland` returns nothing, `/proc/<pid>/environ` is unreachable, and both
   `:0` and `:1` refuse the connection even after the cookie is copied
   somewhere writable. The X sockets are visible in `/tmp/.X11-unix/`, which
   makes the failure look like an auth problem it is not.

   So: delegate the *editing* of `verify-editor.js` freely, but run the suite,
   the perturbation, and the final count yourself. Tell the worker up front
   that you will do the verifying, so it does not burn a turn discovering the
   sandbox boundary and does not report a static assertion count as a result.

2. **A blocked worker that reports honestly is behaving correctly.** Both
   workers here refused to claim unobserved results — one said plainly "0 PASS
   / 0 FAIL, the browser failed before the first assertion," and declined to
   carry forward an earlier partial run as proof of its own change. That is the
   behaviour you want. The fix is to run the verification yourself, not to
   accept the work on the strength of a clean `node --check`. Syntax checks
   prove the file parses; they prove nothing about the behaviour the suite
   exists to pin.
