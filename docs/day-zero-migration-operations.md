# Day Zero migration materialization, verification, and supervised boundary

`tools/day_zero_migration.py` prepares U15 without creating a production
bypass. It has two intentionally different paths: a write-bearing
materialization rehearsal and a write-free verification of the exact committed
candidate. The dependency-injected production state machine consumes only the
second path. No CLI production adapter exists.

## Phase 1: rehearse the one-time materialization

Run this from the dedicated daemon checkout or another clean trusted checkout:

```bash
python3 tools/day_zero_migration.py --candidate-sha <40-character-lowercase-SHA>
```

Omitting `--candidate-sha` uses exact current `HEAD`. The command creates a
standalone clone with no local hardlinks or object alternates and checks out
that exact commit detached; uncommitted source files are never copied. It
removes credential-like environment variables, forces both production controls
false, suppresses child output, and runs these write-bearing phases:

1. governed combined date-offset/JSON-LD verification;
2. one atomic date-offset and JSON-LD-base write in the disposable copy;
3. site, Worker-persona, instructor, history, and editor-map builds;
4. generated-bundle parity;
5. strict Day Zero and Legal Practicum identifier enforcement with nonzero
   scope evidence and zero old-base occurrences; and
6. headless repository preflight.

Any failure aborts. The receipt reports the exact source SHA, phase names, and
`production_mutations: 0`. The temporary copy is removed on normal exit and
catchable signals. SIGKILL and power loss cannot run process cleanup, but the
source checkout was never the write target.

This receipt does **not** identify deployable migrated artifacts. Under the
exclusive production change window, repeat the governed write exactly once in
the controlled migration worktree, run the generators, review the complete
diff, and commit source, date-offset sidecars, identifier base, and generated
artifacts together. The resulting commit—not the pre-write source SHA—is the
only candidate that may proceed.

## Phase 2: verify the committed candidate without rewriting it

The repository-side helper `verify_materialized(repo, candidate_sha)` runs in a
fresh standalone exact-SHA clone. The injected production state machine uses
the same verification-only phase contract. That phase list never contains
`governed-write`. It performs:

1. exact detached `HEAD` and clean-tree proof;
2. governed dry-run verification;
3. deterministic generated builds followed by clean-tree proof, proving the
   committed artifacts match their generators;
4. generated-bundle parity;
5. strict Day Zero and `legalpracticum.org` identifier enforcement;
6. full headless preflight; and
7. a final exact-HEAD/clean-tree proof.

Thus a disposable rewrite cannot be deployed while claiming the unchanged
source commit, and verifying a materialized commit cannot repeat the governed
corpus write.

## Inspect the exact active Cloudflare pair without mutation

The `--inspect-cloudflare-pair` mode is a read-only U15 prerequisite. It issues
only redirect-disabled `GET` requests, with a 20-second timeout, to these fixed
Cloudflare API shapes:

- `https://api.cloudflare.com/client/v4/accounts/<account>/pages/projects/<project>`
- `https://api.cloudflare.com/client/v4/accounts/<account>/workers/scripts/<script>/deployments`

It selects `result.canonical_deployment` for Pages and never substitutes
`latest_deployment`, which may be a newer preview. The canonical deployment
must have a bounded non-null ID, `environment: production`, `is_skipped: false`,
and `latest_stage.status: success`. It selects `result.deployments[0]` for the
Worker and accepts only one version allocation at exactly 100 percent. A
50/50 split, a 0/100 override, multiple entries of any percentages, or a
malformed allocation is intentionally unrepresentable.

The inspector reads both provider records, fetches the Pages and Worker live
URLs without the Cloudflare bearer, and requires both `x-release-sha` headers
to be the same exact lowercase 40-character SHA. It then reads both provider
records again and requires the Pages ID, Worker deployment ID, and complete
Worker allocation to be unchanged. This is a stable two-read proof, not a
claim that Cloudflare offers an atomic cross-product snapshot. Capture recovery
coordinates only while the six-actor exclusive change window remains proved.

Supply the least-privilege Cloudflare read token through stdin. There is no
token command-line option and the tool does not consult an environment variable
for it. A regular stdin credential file must be owned by the current user and
mode `0600`; a password-manager or credential-helper pipe is also accepted.
The tool never writes the token or provider response bodies.

```bash
credential-helper-that-prints-only-the-token | \
python3 tools/day_zero_migration.py \
  --inspect-cloudflare-pair \
  --cloudflare-account-id <32-character-lowercase-account-ID> \
  --pages-project <Pages-project-name> \
  --worker-script sonsteng-chat-production \
  --pages-provenance-url https://legalpracticum.org/ \
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/
```

Normal inspection output contains the shared SHA, digests of the two recovery
IDs, and `production_mutations: 0`. Exact provider IDs are non-secret but are
not printed in the ordinary receipt. To place the inspected exact IDs directly
into the explicitly requested supervised operator sheet, add
`--print-operator-plan` and all of its candidate, registry, enablement, and
acknowledgement inputs:

```bash
credential-helper-that-prints-only-the-token | \
SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true \
SONSTENG_PROD_RELEASE_ENABLED=false \
python3 tools/day_zero_migration.py \
  --inspect-cloudflare-pair \
  --print-operator-plan \
  --cloudflare-account-id <32-character-lowercase-account-ID> \
  --pages-project <Pages-project-name> \
  --worker-script sonsteng-chat-production \
  --pages-provenance-url https://legalpracticum.org/ \
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/ \
  --candidate-sha <committed-migration-SHA> \
  --recovery-registry "$HOME/.local/state/sonsteng-prod-release/known-good-pairs.json" \
  --ack-john-notified \
  --ack-queue-empty
```

Do not add `--prior-sha`, `--prior-pages-deployment-id`, or
`--prior-worker-version-id` in this combined mode: the stable inspection owns
those values and refuses overrides. Redirects, HTTP errors, timeouts, malformed
JSON, `success: false`, ambiguous provider state, and invalid provenance all
produce bounded errors without including raw Cloudflare details.

## Production remains fail closed

`--execute` is intentionally not connected to Cloudflare, systemd, Git, the
editor queue, or the recovery registry. Even with every flag and
`SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true`, it exits `78` before a production
adapter call. `deploy/deploy-prod.sh` remains a disabled tripwire.

The dependency-injected production contract requires, before provider mutation:

- an exact lowercase candidate SHA;
- an exact prior Pages ID, Worker ID, and shared live provenance SHA;
- an absolute non-symlink recovery-registry path;
- explicit acknowledgements that John was notified and the queue was empty;
- `SONSTENG_PROD_RELEASE_ENABLED=false`;
- the production release timer disabled and inactive;
- the apply timer stopped and disabled with readback;
- no relevant service, process, or lease;
- the candidate commit clean, canonical, and based on the declared prior SHA;
  and
- one exclusive window excluding canonical writers and merges, apply and
  production-release daemons, direct deployments, and every provider deployment
  actor before the prior pair is captured.

The daemon lock and exclusive window remain held through exact-candidate
verification, production deployment, recovery-pair recording, DEV/editor
synchronization, prior-pair restoration proof, return-to-candidate proof, and
final all-surface proof. The prior apply-timer policy is restored and read back
only after that proof. Provider failures are reduced to bounded categories;
provider output, credentials, and authored text never enter receipts or errors.

If an error occurs after window entry but before the candidate is proved, the
adapter must explicitly prove production, canonical `main`, DEV, and editor are
all still on the complete prior state. An assumption that "nothing changed" is
not enough. Failed prior-state proof persists the fence and leaves the apply
timer off.

## Mandatory compensation

Any failure after the canonical candidate is proved triggers the complete
compensation sequence while the window remains held:

1. reactivate and read back the exact prior Pages/Worker pair;
2. atomically compare-and-swap canonical `main` from the exact candidate SHA to
   the exact prior SHA, then read back that exact prior SHA;
3. rebuild and redeploy DEV/editor from that prior tree; and
4. prove production, canonical `main`, DEV, and editor all name the prior SHA.

`restore_canonical_ref_exact` is not a general-purpose Git writer or a history
rewrite. Its protected-ref authority is bounded to that one candidate-to-prior
compare-and-swap while the six-actor fence is held. A mismatched current ref,
failed atomic update, or non-exact readback fails compensation.

The adapter attempts every compensation surface even if an earlier step fails.
If the complete prior state cannot be proved, it requires the persistent-freeze
hook to return an affirmative proof, leaves the apply timer stopped, leaves the
production timer off, and exits with a bounded fenced result. If that hook
raises or returns anything other than exactly `true`, the result instead states
that persistent fencing could not be proved; it never claims the fence exists.
Partial compensation is never reported as success.

An exclusive-window close failure is handled before releasing the daemon lock.
If the candidate had been proved and compensation has not already run, full
prior compensation runs there. The close/control-boundary failure takes
precedence over any earlier body error, and timers remain off even when that
compensation succeeds.

## Generate the exact operator sheet

After the controlled worktree has produced and merged the exact migration
commit, generate the non-secret checklist while the same exclusive window
remains held. The generated sheet is strictly post-materialization: its supplied
candidate must already be canonical, clean, and based on the prior SHA.

```bash
SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true \
SONSTENG_PROD_RELEASE_ENABLED=false \
python3 tools/day_zero_migration.py \
  --print-operator-plan \
  --candidate-sha <committed-migration-SHA> \
  --prior-sha <prior-live-SHA> \
  --prior-pages-deployment-id <exact-Pages-deployment-ID> \
  --prior-worker-version-id <exact-Worker-version-ID> \
  --recovery-registry "$HOME/.local/state/sonsteng-prod-release/known-good-pairs.json" \
  --ack-john-notified \
  --ack-queue-empty
```

Do not put credentials in these arguments. Provider IDs are non-secret recovery
coordinates; credentials stay in protected process state. Generating the sheet
does not authorize or execute production work.

## Remaining supervised U15 act

Damien must perform the production window at the keyboard under the Cloudflare
PROD principal described in `docs/prod-release-operations.md`:

1. notify John and independently prove the queue empty;
2. stop the apply timer, prove both services quiescent, take the daemon lock,
   and establish the six-actor exclusive change window;
3. capture and verify the exact prior pair and both live SHA headers;
4. rehearse, then materialize and commit the combined rewrite plus generated
   artifacts exactly once; merge only that commit;
5. verify the exact committed tree with the write-free phase list;
6. upload only the Pages artifact and named production Worker version;
7. read back and atomically record the exact new provider pair;
8. deploy/rebuild DEV/editor from the same SHA;
9. reactivate and prove the prior pair, then the intended new pair;
10. prove canonical `main`, production, DEV, and editor all name the candidate;
    only then release the window and restore the apply timer's prior policy.

If any step is ambiguous, run complete compensation and keep the window fenced
until the prior state is proved. Do not infer a provider ID, fall forward to
`HEAD`, alter DNS or Access, or substitute normal Publisher authorization for
this migration-only KTD6 waiver.
