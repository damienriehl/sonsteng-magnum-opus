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
