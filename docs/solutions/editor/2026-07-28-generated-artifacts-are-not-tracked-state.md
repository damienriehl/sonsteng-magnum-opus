---
title: "A revert restores tracked files; it does not restore generated ones"
category: editor
tags: [apply-daemon, revert, editor-map, worker-deploy, durable-object, generated-artifacts]
module: editor
symptom: "After an undo, the restored paragraph answered 'That block is not editable.' — and a newly added store method returned 'Not found.' in production while every unit test passed"
root_cause: "Two variants of the same mistake: state that must be regenerated or explicitly forwarded was assumed to follow the thing it derives from"
related: [docs/direct-apply-daemon.md, docs/research/editor-apply-spec.md]
---

# Case 1 — the revert path shipped a stale allowlist

**Found live**, during the U4 structural-editing cycle on DEV: an undo
correctly restored a deleted paragraph to `data/`, but every subsequent edit
against it was refused with `validation_error` — *"That block is not
editable."*

The revert path did: `git revert` → deploy site. Both correct, both
insufficient. `build/editor-map.generated.json` — the **server-side allowlist**
the Worker bundles — is a *generated* artifact. `git revert` restores tracked
trees (`data/`, `site/`); it cannot restore a build product. So the Worker kept
serving a map describing the pre-revert corpus, in which the restored block
does not exist.

**Fix:** the revert success path is now `rebuild → deploy site → deploy
worker`, and any failure among the three marks the revert failed. Tests assert
both the rebuild and the worker deploy happen on success, and neither happens
on failure.

**The general rule:** after any operation that changes `data/`, ask *what is
derived from this that git does not carry?* In this repo that is at minimum:
the editor map, the instructor bundle, the persona bundle, the history bundle
— and the Worker deploy that embeds them. `check_build_parity.py` exists to
catch exactly this drift; run it after reverts, not only after applies.

# Case 2 — the Durable Object shell forwards RPCs explicitly

**Found live** immediately after deploying the U7 scoped-request endpoints: a
valid request died with a bare `Not found.` while 341 worker unit tests passed.

`editor-store-core.js` holds all logic (so it can run under `node:sqlite` in
tests); `editor-store.js` is a **thin DO wrapper that delegates each RPC by
hand**. Five new core methods were added; none were forwarded. The unit tests
exercise the *core* directly, so they cannot see the gap — the wrapper is the
one seam with no test coverage.

**Two follow-on traps in the same incident:**

- The Worker's `/edit/*` catch-all converts an unhandled throw into a 500
  whose body is the literal text `Not found.` — which reads exactly like a 404
  routing miss and sent the first diagnosis in the wrong direction. Check
  observability for the real error before trusting that string.
- **A live DO instance keeps running the script version it started with.**
  After deploying the fix, the first calls still failed; they succeeded once
  the instance was evicted. When testing a DO-touching deploy, a failure in
  the first minute may be a stale instance, not your code.

**Rule:** when adding a method to `editor-store-core.js`, add the forwarding
line to `editor-store.js` in the same commit. A test that only calls the core
proves the logic, never the wiring.
