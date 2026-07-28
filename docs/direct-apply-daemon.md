# Direct-Apply Daemon + Editorial Pass (home box)

The home-box automation that makes John/Roger's `/edit` changes **direct-apply**:
accepted suggestions converge to canonical (git) + DEV within ~2 min with zero
Damien action, and quality is guarded **post-hoc** by an editorial pass. Part of
`docs/plans/2026-07-19-001-feat-canonical-direct-apply-plan.md`.

Two systemd **user** timers (never PROD):

| unit | cadence | runs |
|------|---------|------|
| `sonsteng-apply.timer` | every 2 min (`OnUnitActiveSec=2min`, `Persistent`) | `tools/direct_apply_daemon.py` |
| `sonsteng-editorial.timer` | daily 21:30 America/Chicago (`Persistent`) | `tools/editorial_pass.py --daily` |

Install / refresh / remove (mirrors `install-digest-timer.sh`):

```
bash tools/install-apply-daemon.sh              # install + enable + start both
bash tools/install-apply-daemon.sh --uninstall
```

Secrets live in the 0600 env file `~/.config/sonsteng-apply/env` (`EDIT_API_BASE`,
`EDIT_SERVICE_TOKEN` = the `EDIT_TOKEN_ADMIN` value from
`~/.secrets/sonsteng-editor-tokens`, `APPLY_DEPLOY_BRANCH`). The ntfy topic is read
by path from `~/.config/claude-rc/ntfy-topic`. Nothing is inlined in git.

## Deploy topology (since 2026-07-24)

| | path | branch | who writes it |
|---|---|---|---|
| **Daemon checkout** | `~/.local/share/sonsteng-daemon/checkout` | `main` | the daemon only |
| **Interactive checkout** | `~/Coding Projects/sonsteng-magnum-opus` | feature branches | you |

The daemon runs **from its own git worktree**, not from an interactive checkout.
`ExecStart` in both user units points at `<daemon checkout>/tools/…`, and
`APPLY_DEPLOY_BRANCH=main` — `main` carries the direct-apply/History work as of the
`merge: canonical direct-apply + redline History + docs` merge.

**Amended 2026-07-28 — a revert must also rebuild and redeploy the Worker.**
`git revert` restores tracked trees (`data/`, `site/`) but cannot restore
*generated* artifacts — above all `build/editor-map.generated.json`, the
server-side allowlist the Worker bundles. A revert that only deployed the site
left the Worker serving a pre-revert map, so every block the revert restored
answered "That block is not editable." The revert success path is now
rebuild → deploy site → deploy Worker, and any failure among the three marks
the revert failed. Detail:
`docs/solutions/editor/2026-07-28-generated-artifacts-are-not-tracked-state.md`.

**Why.** The apply engine (`assert_clean_tree`) and the History revert both refuse
to run on a dirty tree. While the daemon lived in the interactive checkout, any
session that parked an uncommitted edit — or merely ran `build_site.py` — silently
blocked auto-apply. Separating the trees makes that impossible.

**Why a worktree and not a clone.** A worktree shares the object store and refs
with the interactive checkout, so an `apply:` or `revert(history):` commit the
daemon makes is instantly visible to `git log` there and pushable from there — the
exact behavior of the old shared-tree setup, minus the shared working tree. A clone
would strand the daemon's commits behind a fetch and could diverge from `origin/main`.

**Consequence — `main` is checked out in the daemon worktree**, so an interactive
checkout can't check it out (git allows a branch in one worktree at a time). Merge
into `main` from the daemon worktree, under the daemon flock so it can't race a tick:

```bash
D=~/.local/share/sonsteng-daemon/checkout
flock "$D/.locks/daemon.lock" git -C "$D" merge --no-ff feat/your-branch
git -C "$D" push origin main
```

Note the flock now lives at `<daemon checkout>/.locks/daemon.lock` — that is the file
to take for anything that races the 2-min timer.

Provisioning is idempotent and lives in the installer: `tools/install-apply-daemon.sh`
creates the worktree if it is missing, builds the gitignored generated bundles
(`app/worker/editor-data/`, `build/`) that a fresh worktree lacks, warns if the tree
is on a branch other than `APPLY_DEPLOY_BRANCH`, and writes the units. Override the
location with `SONSTENG_DAEMON_ROOT=…`.

**Regenerable-site guard.** `build_site.py` stamps the current HEAD sha into
`site/platform/data/.build-stamp.json` (traceability only — deliberately not part of
the parity hash), so the tick's post-apply rebuild always leaves that one tracked
file dirty. The daemon therefore calls `restore_regenerable_site()` (`git checkout --
site`) both **before** invoking the engine and **after** the deploy; without it the
tick after any successful apply would abort with "canonical tree is dirty" — i.e.
auto-apply would stall on the second edit of a session. DEV is published from the
**committed** tree (`git archive <branch>`), so restoring the working copy never
changes what ships. Source dirtiness (`data/`, `app/`, `tools/`) is untouched and
still stops the engine, which is the point.

## Daemon flow (`direct_apply_daemon.py`)

Each tick, under a host-local **daemon flock** (`.locks/daemon.lock`, distinct
from and cooperating with the engine's `.locks/apply.lock`):

1. `GET {EDIT_API_BASE}/review` (admin) → filter `status == "accepted"`. This is
   the auto-accept output the worker lane emits; `/review` already surfaces
   `accepted`, so the daemon works with **today's** API shape.
2. **No accepted** → best-effort heartbeat `{ok:true, applied:0, ts}` and stop.
   The 2-min cadence **is** the flush — no withholding logic (SL3). If a batch has
   been idle ≥30 min and is still unreviewed, this quiet tick also dispatches the
   **session-end** editorial pass and marks the batch reviewed.
3. **Any accepted** → subprocess `tools/apply_suggestions.py --batch-id … --base-url …`
   with `APPLY_DEPLOY=1`. The existing engine (unmodified) patches canonical, runs
   the validator + parity gates, marks `applied` / `needs_human` / `accepted_blocked`
   via `/finalize`, and fast-forward-merges into canonical.
4. **Authoritative publish**: `build_site.py` rebuild, then
   `deploy/deploy-dev.sh <branch>` with the branch passed **explicitly** (default
   `feat/canonical-docs`). **DEV only** — `deploy-dev.sh` targets the Hetzner DEV
   box and can never reach PROD.
5. `POST {EDIT_API_BASE}/heartbeat` (admin Bearer) `{ok:true, applied:N, ts}`.
   The endpoint is being added by the worker lane; the daemon sends **best-effort
   and tolerates 404** until that merges (a 404/unreachable heartbeat never gates
   the apply).
6. On **any** apply/rebuild/deploy failure: heartbeat `{ok:false}` + an **ntfy
   alert** naming the failed suggestion **IDs only** (never content), so a stalled
   home box is never silent (SL1/SL6).

`APPLY_DEPLOY=1` is required because the engine only merges canonical on a
successful deploy (merge is gated behind deploy). The engine therefore performs a
DEV deploy of its worktree as the pre-merge gate; the daemon's step-4 deploy is
the authoritative publish of the merged canonical **branch** (identical content,
correctly named). Both are DEV-only.

## Crash-safety (inherited from the apply engine)

Rerunning after a crash mid-sequence **never double-applies**. The daemon adds a
flock so two ticks never overlap, but the real guarantees come from the engine:

- **Reconcile FIRST.** `run_apply` calls `client.reconcile()` before any new
  claim. Expired-lease batches that crashed **pre-merge** roll `in_flight →
  accepted` (re-queued) + journal `rolled_back`; batches that crashed
  **post-merge** complete `in_flight → applied`. No limbo.
- **Claim only takes `accepted` rows, whole groups only.** A row already
  `in_flight`/`applied` is never re-claimed. So a batch that merged before the
  crash leaves its rows terminal `applied`; the next daemon tick fetches
  `/review`, sees **zero** `accepted`, and no-ops (no second apply). A batch that
  crashed before merge is re-queued to `accepted` and flushed **exactly once** —
  canonical was byte-clean the whole time (git-worktree isolation; the fast-forward
  merge is the only canonical write, and it is the last step).
- **Append-only `apply_batches` journal + the DO `in_flight` lease** are the true
  cross-host mutex and the crash-recovery record; the flock is only intra-host.

These are covered by `TestCrashIdempotency` in the daemon test (a reasoning test
that models the post-crash `/review` truth for both the pre- and post-merge cases).

## Editorial pass (`editorial_pass.py`)

Post-hoc review of applied edits (detection-lag by design, SL5). Two triggers:

- **Session-end** — the daemon records `last_applied_ts` + a `batch_reviewed`
  flag; when ≥30 min have elapsed since the last apply and the batch is still
  unreviewed, the daemon dispatches `editorial_pass.py --batch-id <id>` (subprocess,
  so a hung reviewer never blocks the apply cadence).
- **Daily sweep** — `sonsteng-editorial.timer` runs `--daily` at 21:30.

The pass: (1) `git log` selects the apply-engine commits (author
`apply@sonsteng.local`, subject `apply: batch …`) in the window and `git show`
collects their `data/` + `site/` diffs; (2) it invokes the Claude CLI **headless**
with a strict timeout and graceful degradation; (3) it parses the model's JSON
`{flags:[{source_ref, severity, message}]}`; (4) it files each flag as a
**comment** on the block via `POST /edit/v1/system-suggest` (`origin=ai_rewrite`,
comment only — `source_ref` re-validated against the map server-side, unknown refs
skipped best-effort); (5) it sends a content-light ntfy digest ping (counts by
severity).

Exact CLI invocation (no secrets — the prompt is public canonical diffs only):

```
claude -p <PROMPT> --model opus --output-format json
```

**Graceful degradation (SL7 accepted):** a missing `claude` binary
(`FileNotFoundError`) or a timeout (`TimeoutExpired`) returns a degraded reason,
logs, and pings an ntfy note — it **never crashes**. `--dry-run` files nothing and
pings nothing; it returns the planned flags + payloads.

## Tests

`tools/tests/test_direct_apply_daemon.py` + `tools/tests/test_editorial_pass.py`
(pure-logic; mock HTTP/subprocess/model). Covered: no-op path, apply→rebuild→
deploy→heartbeat ordering, failure→`ok:false`+alert (IDs only), crash-idempotency
reasoning, session-end windowing math + dispatch, heartbeat-404 tolerance, flag
parsing (envelope/bare/embedded/malformed), flag payload shape, filing best-effort,
graceful degradation, dry-run. Run `python3 -m pytest tools/tests/ -q`.
