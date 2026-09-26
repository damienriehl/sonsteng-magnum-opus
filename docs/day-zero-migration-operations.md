# Day Zero migration materialization, verification, and supervised boundary

`tools/day_zero_migration.py` prepares U15 without creating a production
bypass. It has two intentionally different paths: a write-bearing
materialization rehearsal and a write-free verification of the exact committed
candidate. The dependency-injected production state machine consumes only the
second path. No full CLI production adapter exists. The separate,
Git-only `tools/canonical_ref_cas.py` supplies the bounded canonical `main`
forward and compensation operations documented below; it does not connect
`--execute` to any other production surface.

This runbook is the single operating authority for Packet D's supervised
window. Where an older procedure, handoff, or decision-sheet step reads
differently, [Settled operator decisions for the Packet D window](#settled-operator-decisions-for-the-packet-d-window)
and the numbered sequence under
[Remaining supervised U15 act](#remaining-supervised-u15-act) govern.

## Operation-frontier integrity migration

The operation frontier deliberately makes a clean break from the former
unkeyed 32-bit FNV-1a values. Newly derived receipt hashes, revision and
decision evidence digests, release membership hashes, and fencing tokens are
SHA-256 over typed, length-prefixed canonical values. Projection identity,
manifest, evidence, request, and authorization bindings are likewise SHA-256
at their producer. The serialization preserves field and type boundaries; an
attacker-controlled string cannot move a delimiter and become another
structure.

### Assumption (a): legacy values fail closed

There is no compatibility path for an old digest. A stored FNV-era value that
is compared with a new derivation raises a bounded `operation_frontier_integrity`
error. The release-service projection throws; the read-only observer returns
zero with `blocked_state: "blocked"`, never a silent clean zero. One incompatible
completed schema-v2 release blocks the proof for the whole store and therefore
every later queue check. Do not update or delete evidence rows to clear it. Keep
the Day Zero window closed and require a separately reviewed migration decision
if such a row is ever found.

The same clean break applies outside the projection proof: a replay against an
FNV-era non-null suggestion `client_fp` returns bounded `id_conflict`, and an
FNV-era saved review draft returns bounded `draft_mismatch`. Neither old value
is silently upgraded or treated as an empty result.

This blast radius is accepted because the window is closed, no release is
prepared or authorized, and pre-change releases already fail closed. The
repository evidence is the `Prepared release ID` and `Authorized release ID`
rows marked **NOT RUN** in
[`docs/uat/editor-publisher-matrix.md`](uat/editor-publisher-matrix.md).

### Assumption (c): append-only remains an operational policy

SQLite does not enforce append-only authority for receipts, normalized
lifecycle rows, release events, or publication rows. Append-only operation is
still a privileged-service policy, and that policy permits appends; the
integrity proof separately rejects duplicate and unknown lifecycle events.
SHA-256 supplies practical collision and second-preimage resistance, but it is
not a secret or an external witness: a privileged writer that can rewrite both
evidence and its digest remains outside this in-process trust boundary.

### Assumption (b): the sentinel is a work bound

The 100,001-row work bound is explicit: the internal summary returns null
eligible and held counts. The public observer maps either non-integer to exactly
`{"pending_operation_count":0,"blocked_state":"blocked"}` rather than report
100,001 held operations as a measured fact. The short circuit limits query and
integrity-validation work; its safety role is redundant because the endpoint
independently blocks any earned count above the same 100,000-operation maximum.

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
diff, and commit the governed source, date-offset sidecars, identifier-base
rewrite, and the *tracked* generated artifacts together. Ignored generator
outputs are never part of that commit; see decision OQ-10 under
[Settled operator decisions](#settled-operator-decisions-for-the-packet-d-window)
for the exact split. The resulting commit—not the pre-write source SHA—is the
only candidate that may proceed.

## Phase 2: verify the committed candidate without rewriting it

The repository-side helper `verify_materialized(repo, candidate_sha)` runs in a
fresh standalone exact-SHA clone. The injected production state machine uses
the same verification-only phase contract. That phase list never contains
`governed-write`. Its exact phases are `candidate-commit`,
`governed-verification`, `generated-build`, `generated-artifact-cleanliness`,
`build-parity`, `strict-day-zero-enforcement`, `preflight`, and
`final-tree-cleanliness`. It performs:

1. exact detached `HEAD` and clean-tree proof;
2. governed dry-run verification;
3. deterministic generated builds followed by generated-artifact cleanliness.
   Every tracked byte must match except that the committed and regenerated
   `.build-stamp.json` objects are compared without `git_base_sha`, which is
   traceability-only. Their `spine_build_id` and every other field must match;
   the committed stamp bytes are then restored before an exact clean-tree proof;
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
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance
```

The Worker provenance URL must be the `/edit/release-provenance` path; the
Worker root carries no `X-Release-SHA` header. That path answers `204 No
Content` with the header, and the inspector accepts `200` or `204` for the
Worker provenance read only. The Pages provenance read stays `200`-only, and
every read has redirects disabled: `https://legalpracticum.org/` must answer
`200` directly, and a `3xx` there is a stop. Provider JSON reads remain
`200`-only. The Pages project is `sonsteng` (the name the pre-user procedure
deploys to); substitute it for `<Pages-project-name>` in every inspector call.

Normal inspection output contains the shared SHA, digests of the two recovery
IDs, and `production_mutations: 0`. Exact provider IDs are non-secret but are
not printed in the ordinary receipt.

To capture exact non-secret recovery coordinates before a candidate exists,
add `--print-recovery-ids`, `--ack-john-notified`, and `--ack-queue-empty`, with
`SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true`:

```bash
credential-helper-that-prints-only-the-token | \
SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true \
python3 tools/day_zero_migration.py \
  --inspect-cloudflare-pair \
  --print-recovery-ids \
  --cloudflare-account-id <32-character-lowercase-account-ID> \
  --pages-project <Pages-project-name> \
  --worker-script sonsteng-chat-production \
  --pages-provenance-url https://legalpracticum.org/ \
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance \
  --ack-john-notified \
  --ack-queue-empty
```

Only after the stable two-read proof, this mode prints the exact Pages
canonical deployment ID, Worker version ID, shared SHA, and
`production_mutations: 0`. It requires no candidate or recovery registry and
cannot be combined with `--print-operator-plan`; it never prints the token or
provider bodies.

To place the inspected exact IDs directly into the explicitly requested
supervised operator sheet, add
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
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance \
  --repo <trusted-repository-path> \
  --candidate-sha <committed-migration-SHA> \
  --recovery-registry "$HOME/.local/state/sonsteng-prod-release/known-good-pairs.json" \
  --ack-john-notified \
  --ack-queue-empty
```

Do not add `--prior-sha`, `--prior-pages-deployment-id`, or
`--prior-worker-version-id` in this combined mode: the stable inspection owns
those values and refuses overrides. With `--repo`, combined mode also requires
the candidate's first parent to equal the *live pair* SHA. When the live
production pair is older than canonical `main` (see
[Two prior SHAs](#two-prior-shas-prior_pair_sha-and-prior_sha)), the
candidate's parent is `PRIOR_SHA`, not `PRIOR_PAIR_SHA`, so combined mode
refuses with `operator-plan candidate first parent did not match prior SHA`.
That refusal is correct, not a fault to work around. In that case do not
generate the combined sheet; this runbook's numbered sequence governs, and the
sheet is only a rendering aid (OQ-15). Redirects, HTTP errors, timeouts, malformed
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
- the apply timer stopped and disabled with readback (this is the injected
  state machine's own contract; the manual Packet D window below instead
  stops the apply timer **without** disabling it, because the queue-proof
  receipt validator requires it to read back enabled; see step 1);
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
all still on the complete prior state (production on `PRIOR_PAIR_SHA`; canonical
`main`, DEV, and editor on `PRIOR_SHA` as established by step 1a). An assumption that "nothing changed" is
not enough. Failed prior-state proof persists the fence and leaves the apply
timer off.

## Mandatory compensation

Any failure after the canonical candidate is proved triggers the complete
compensation sequence while the window remains held:

1. reactivate and read back the exact prior Pages/Worker pair, Pages first and
   then the Worker, using the full canonical Pages deployment ID (decisions
   OQ-8 and OQ-9 below). Use exactly the commands in
   [Pages and Worker rollback commands](#pages-and-worker-rollback-commands):

   ```bash
   credential-helper-that-prints-only-the-Cloudflare-bearer | pages_rollback "$PRIOR_PAGES_DEPLOYMENT_ID"
   echo "pages rollback rc=$?"
   ( cd "$CONTROLLED_REPO/app/worker" &&
     npx wrangler@4 versions deploy "$PRIOR_WORKER_VERSION_ID" --env production --yes )
   echo "worker rollback rc=$?"
   ```

   Then read the pair back with the step-3 inspector helpers:

   ```bash
   RECOVERY_JSON=$(recovery_ids); echo "inspector rc=$? $RECOVERY_JSON"
   test "$(recovery_field sha)" = "$PRIOR_PAIR_SHA" &&
     test "$(recovery_field pages_deployment_id)" = "$PRIOR_PAGES_DEPLOYMENT_ID" &&
     test "$(recovery_field worker_version_id)" = "$PRIOR_WORKER_VERSION_ID" &&
     echo "PRIOR PAIR OK" || echo "STOP: prior pair readback"
   ```

   Require `PRIOR PAIR OK`;
2. atomically compare-and-swap canonical `main` from the exact candidate SHA to
   the exact prior SHA (`PRIOR_SHA`), then read back that exact prior SHA;
3. return DEV/editor to `PRIOR_SHA` in the same fixed OQ-13 order as step 8,
   (a) then (b), with `TARGET_SHA=$PRIOR_SHA` (see OQ-13 for why one order is
   used everywhere): (a) the DEV static site with
   `bash deploy/deploy-dev.sh "$PRIOR_SHA"` from `$CONTROLLED_REPO` and its
   `spine-build` proof against `PRIOR_SHA`'s committed build stamp, then
   (b) the top-level DEV Worker. For (b), reactivate the exact step-1a DEV
   version recorded in step 1a.7, which already carries
   `RELEASE_SHA=PRIOR_SHA` and the observer secret, from `app/worker` of any
   checkout:

   ```bash
   npx wrangler@4 versions deploy "$STEP1A_DEV_VERSION_ID" --env="" --yes
   echo "dev worker rollback rc=$?"
   prove_provenance https://sonsteng-chat.damienriehl.workers.dev/edit/release-provenance 204 "$PRIOR_SHA"
   ```

   Only if that version cannot be activated, rebuild instead: regenerate the
   ignored Worker inputs in a clean `PRIOR_SHA` checkout (OQ-10) and run the
   OQ-13 part (b) upload, view, and deploy with `TARGET_SHA=$PRIOR_SHA`; and
4. prove every surface is back on its prior state: production's two
   `x-release-sha` headers name `PRIOR_PAIR_SHA` with the exact prior provider
   IDs; canonical `main` and the DEV/editor Worker's `x-release-sha` name
   `PRIOR_SHA`; and the DEV static `spine-build` equals `PRIOR_SHA`'s committed
   `spine_build_id`. See
   [Two prior SHAs](#two-prior-shas-prior_pair_sha-and-prior_sha).

`restore_canonical_ref_exact` is not a general-purpose Git writer or a history
rewrite. Its protected-ref authority is bounded to that one candidate-to-prior
compare-and-swap while the six-actor fence is held. A mismatched current ref,
failed atomic update, or non-exact readback fails compensation.

### Pin every Day Zero verifier to the reviewed release

Use one trusted operations checkout and one reviewed release identity for every
Day Zero verifier. Never accept a verifier from the daemon checkout, the current
directory, ambient `PATH`, or receipt replay. Establish these non-secret values
before the window from the reviewed release record; in particular,
`EXPECTED_REMOTE_URL_SHA256` must be the independently recorded digest of the
canonical remote URL, not a digest read from the daemon checkout being verified.

```bash
set -eu
for INJECTION_NAME in ${!LD_@}; do unset "$INJECTION_NAME"; done
unset OPENSSL_CONF OPENSSL_MODULES
unset PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP PYTHONUSERBASE

OPS_REPO=/absolute/path/to/the/reviewed/operations-checkout
CAS="$OPS_REPO/tools/canonical_ref_cas.py"
REVIEWED_OPS_COMMIT=<reviewed-40-character-release-commit>
REVIEWED_CAS_SHA256=<reviewed-64-character-canonical_ref_cas.py-sha256>
EXPECTED_REMOTE_URL_SHA256=<independently-recorded-64-character-remote-url-sha256>
DAEMON_REPO=/absolute/path/to/the/dedicated-daemon-checkout
# One value for both tools; must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}.
# Example shape: packet-d-2026-09-25.w1
WINDOW_OWNER=<opaque-Packet-D-window-id>
RECEIPT_DIR=/absolute/path/to/a/new-mode-0700-window-evidence-directory
HOST_IDENTITY=$(/usr/bin/env -i /usr/bin/uname -n)

trusted_git() {
  /usr/bin/env -i \
    LC_ALL=C \
    GIT_CONFIG_COUNT=0 \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_SYSTEM=/dev/null \
    GIT_NO_REPLACE_OBJECTS=1 \
    GIT_TERMINAL_PROMPT=0 \
    /usr/bin/git "$@"
}

test "${OPS_REPO#/}" != "$OPS_REPO"
test "${DAEMON_REPO#/}" != "$DAEMON_REPO"
test "${RECEIPT_DIR#/}" != "$RECEIPT_DIR"
test -n "$WINDOW_OWNER"
test -n "$HOST_IDENTITY"
test "$(/usr/bin/env -i /usr/bin/readlink -f -- "$OPS_REPO")" = "$OPS_REPO"
test "$(/usr/bin/env -i /usr/bin/readlink -f -- "$CAS")" = "$CAS"
test "$(/usr/bin/env -i /usr/bin/readlink -f -- "$DAEMON_REPO")" = "$DAEMON_REPO"
test "$(trusted_git -C "$OPS_REPO" rev-parse --verify HEAD)" = "$REVIEWED_OPS_COMMIT"
test -z "$(trusted_git -C "$OPS_REPO" status --porcelain --untracked-files=all)"
REVIEWED_CAS_BLOB=$(trusted_git -C "$OPS_REPO" rev-parse \
  "$REVIEWED_OPS_COMMIT:tools/canonical_ref_cas.py")
ACTUAL_CAS_BLOB=$(trusted_git -C "$OPS_REPO" hash-object -- "$CAS")
test "$ACTUAL_CAS_BLOB" = "$REVIEWED_CAS_BLOB"
ACTUAL_CAS_SHA256=$(/usr/bin/env -i /usr/bin/sha256sum -- "$CAS")
ACTUAL_CAS_SHA256=${ACTUAL_CAS_SHA256%% *}
test "$ACTUAL_CAS_SHA256" = "$REVIEWED_CAS_SHA256"
/usr/bin/env -i /usr/bin/test -x /usr/bin/python3
/usr/bin/env -i /usr/bin/python3 -I -c 'import os,pathlib,shutil,sys; p=os.confstr("CS_PATH"); es=p.split(os.pathsep) if p else []; g=shutil.which("git",path=p) if es and all(os.path.isabs(e) for e in es) else None; q=pathlib.Path(g).resolve(strict=True) if g else None; sys.exit(0 if q and q.is_file() and os.access(q,os.X_OK) else 1)'
/usr/bin/env -i /usr/bin/test ! -e "$RECEIPT_DIR"
umask 077
/usr/bin/env -i /usr/bin/mkdir "$RECEIPT_DIR"
echo "PINNING OK"
set +e
```

**Use one persistent interactive shell for the whole window.** Run this block,
every helper definition (`prove_provenance`, `check_version_view`,
`version_secret_names`, and `pages_rollback`), and every numbered step in the
same interactive Bash session, for example one `tmux` pane that an agent
operator drives by sending keys and reading the pane. The window variables
(`PRIOR_SHA`, `CANDIDATE_SHA`, the provider IDs, `CONTROLLED_REPO`, and the
others) and the helper functions exist only in that shell; a fresh shell per
command loses them. The block runs under `set -eu` so that any failed identity
comparison aborts it before `PINNING OK` prints; if `PINNING OK` did not print,
STOP. Its final `set +e` switches fail-fast off again, so that a later failing
check prints its `STOP` line and `rc=` value instead of killing the window
shell. `set -u` stays on deliberately: in an interactive shell a reference to
an unset window variable aborts only that command line with `unbound variable`,
which is a STOP.

`WINDOW_OWNER` is passed to both the queue verifier (`--window-owner` in the
launcher below) and every CAS command, so choose one value that satisfies the
stricter of the two rules. `tools/prove_queues_empty.py` requires the full
pattern `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` (at most 128 characters, starting
with a letter or digit, then only letters, digits, `.`, `_`, `:`, or `-`).
`tools/canonical_ref_cas.py` requires a non-empty printable value of at most
256 characters with no surrounding whitespace, `@`, or `://`. A value such as
`packet-d-2026-09-25.w1` satisfies both. Substitute that same literal for
`<opaque-Packet-D-window-id>` in the queue-proof launcher.

The absolute `/usr/bin/python3 -I` interpreter, absolute `$CAS` path, clean
checkout commit, Git blob ID, and SHA-256 above are one release-identity rule;
apply that same rule to the queue verifier when its reviewed release is pinned.
Rerun all checkout, blob, and SHA-256 comparisons immediately after each
verifier invocation and before accepting its receipt.
Run this complete block in a pre-window rehearsal on the daemon host. The
`set -eu` makes every failed identity comparison abort this block. Run it from a
freshly authenticated login shell whose environment was not supplied by an
untrusted parent. Clearing every ambient `LD_*` name plus the named OpenSSL and
Python variables, then using `/usr/bin/env -i` and Python `-I`, bounds inherited
environment injection; `-I` also disables the user site and `usercustomize`.
It does not prove interpreter integrity. A native loader can pre-empt the shell
or interpreter before either can clear or inspect its environment, so the
in-tool refusal is necessarily a secondary guard. Corroborate receipt identity
with the out-of-band Git blob and SHA-256 comparisons above after every
invocation. The `CS_PATH` probe is the go/no-go check for the verifier's trusted
Git resolver;
if it is nonzero, the CAS tool is inoperable on that host and the production
window must not open.

With the daemon lock and the entire six-actor window still held, first prove
the exact Git compensation preconditions without mutation:

```bash
CAS_RESTORE_DRY_RECEIPT="$RECEIPT_DIR/canonical-ref-restore-dry-run.json"
/usr/bin/env -i /usr/bin/python3 -I "$CAS" restore \
  --repo "$DAEMON_REPO" \
  --remote origin \
  --branch main \
  --from "$CANDIDATE_SHA" \
  --to "$PRIOR_SHA" \
  --expect-remote-url-sha256 "$EXPECTED_REMOTE_URL_SHA256" \
  --window-owner "$WINDOW_OWNER" \
  --receipt-path "$CAS_RESTORE_DRY_RECEIPT" \
  --dry-run
```

Then perform that same exact candidate-to-prior CAS:

```bash
CAS_RESTORE_RECEIPT="$RECEIPT_DIR/canonical-ref-restore.json"
/usr/bin/env -i /usr/bin/python3 -I "$CAS" restore \
  --repo "$DAEMON_REPO" \
  --remote origin \
  --branch main \
  --from "$CANDIDATE_SHA" \
  --to "$PRIOR_SHA" \
  --expect-remote-url-sha256 "$EXPECTED_REMOTE_URL_SHA256" \
  --window-owner "$WINDOW_OWNER" \
  --receipt-path "$CAS_RESTORE_RECEIPT"
```

The command refuses unless checked-out, clean local `main`, worktree `HEAD`,
and remote `main` all equal the exact `--from` candidate, and the candidate is
an exact commit whose sole parent is the `--to` prior SHA. The named remote must
resolve to one identical fetch and push URL, which is pinned for all remote
reads and writes and revalidated before success. Cleanliness rejects hidden
index flags and compares tracked content with `HEAD` through a disposable
index. The command first pushes the prior SHA from an immutable disposable
source with `--force-with-lease=main:<candidate-sha>`, explicit
`refs/heads/main:refs/heads/main`, and tag following disabled. It then
compare-and-swaps local `main` only if it still equals the candidate and aligns
the index and worktree with ref-nonmutating plumbing—never `reset --hard`.
Finally it rechecks symbolic `HEAD`, exact cleanliness, remote configuration,
and local, remote, and worktree SHAs. Its mode-`0600`, create-new JSON receipt
includes the verifier's absolute path and self-hash; the absolute repository;
remote name and branch; UTC timestamp; operation labels; validated SHA values;
the operator-supplied remote expectation; a credential-redacted validated
remote URL; and the SHA-256 fingerprint of the exact validated URL. It also
records the required window owner (a printable value of at most 256
characters, without surrounding whitespace, `@`, or `://`, refused before any
mutation otherwise) and host identity, plus best-effort readback
after a failure so partial state is never silent. It writes and syncs a private
temporary file in the evidence directory, then publishes the complete receipt
without overwrite; the named receipt is therefore complete or absent, never a
partial JSON file. An inability to open, write, flush, publish, or sync the
receipt fails the command and mirrors the complete in-memory payload to standard
error when possible.

An injected production adapter can implement the state machine method by
delegating to
`canonical_ref_cas.CanonicalRefCasAdapter(...,
receipt_path=<new-absolute-path>).restore_canonical_ref_exact`.
That method returns the exact prior SHA only after all three readbacks match,
which satisfies the check in `day_zero_migration._restore_canonical_ref_exact`.
It publishes the same durable receipt on success and before re-raising a
`CasFailure`; every adapter call requires a new, absolute, single-use path.
If adapter receipt publication fails or is interrupted, the named receipt is
complete or absent, the complete in-memory payload is mirrored to standard
error when possible, and the adapter raises a bounded `CasError` instead of a
raw `BaseException`.
It deliberately supplies no adapter for the other `--execute` production
methods.

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
remains held. The generated sheet is strictly post-materialization. Supplying
`--repo` makes generation fail closed unless the candidate exists, a fresh
exact-candidate clone is clean, its first parent is exactly `--prior-sha`, and
`prior..candidate` contains exactly one commit. These checks do not prove that
canonical `main` names the candidate or that materialization was reviewed;
those remain explicit operator checks.

```bash
SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true \
SONSTENG_PROD_RELEASE_ENABLED=false \
python3 tools/day_zero_migration.py \
  --print-operator-plan \
  --repo <trusted-repository-path> \
  --candidate-sha <committed-migration-SHA> \
  --prior-sha <prior-live-SHA> \
  --prior-pages-deployment-id <exact-Pages-deployment-ID> \
  --prior-worker-version-id <exact-Worker-version-ID> \
  --recovery-registry "$HOME/.local/state/sonsteng-prod-release/known-good-pairs.json" \
  --ack-john-notified \
  --ack-queue-empty
```

With `--repo`, `--prior-sha` must be the candidate's parent, which is
`PRIOR_SHA`. The sheet prints that value as "Prior live SHA" and tells the
operator the live headers must equal it. When `PRIOR_PAIR_SHA` differs from
`PRIOR_SHA`, that line is false for production, so do not generate or follow
the sheet for this window; follow this runbook's numbered sequence instead.

If `--repo` is omitted, the sheet explicitly says that candidate existence,
fresh-clone cleanliness, first-parent identity, and the one-commit range were
not checked; it does not assert those facts.

Do not put credentials in these arguments. Provider IDs are non-secret recovery
coordinates; credentials stay in protected process state. Generating the sheet
does not authorize or execute production work.

## Settled operator decisions for the Packet D window

These decisions close the operator-sequence open questions (OQ numbers) that
had more than one plausible reading. They bind every numbered step of the
supervised act below.

**Already settled by merged tooling.** Queue emptiness (OQ-1) is proved only
by `tools/prove_queues_empty.py` through the readonly launcher in step 2, whose
publication proof requires the observer's `operation_frontier`; the older
`tools/prod_release_readiness.py` counts are not a substitute. Exact prior
recovery IDs before a candidate exists (OQ-4) come from
`--inspect-cloudflare-pair --print-recovery-ids`. The deterministic rebuild
compares build stamps without the traceability-only `git_base_sha` (OQ-6,
Phase 2 step 3). The generated operator sheet is a rendering and binding aid,
not proof of canonical state (OQ-15); with `--repo` it checks only the facts
listed under [Generate the exact operator sheet](#generate-the-exact-operator-sheet).
Canonical `main` moves only through the `tools/canonical_ref_cas.py` `forward`
and `restore` commands (OQ-7); never `merge --no-ff`, a manual push, or
`reset --hard`.

**Length-check every comparison.** Every ID or SHA comparison in the window,
whether scripted or read by eye, first confirms that both sides are present and
have the expected exact length (40 lowercase hex characters for a Git SHA, 64
for a SHA-256 digest, the full canonical form for a provider ID). Two empty
strings compare equal; a prior false PASS came from exactly that. An absent or
short value is a stop, never a match.

**OQ-8: deploy order and the compatibility proof.** Deploy the production
Worker first, then Pages. Restore in the reverse order: Pages first, then the
Worker. Both transients therefore pair the *candidate* production Worker with
the *prior* Pages deployment, so the window requires an explicit
new-Worker-serves-old-Pages proof, recorded in the window evidence directory
before Pages deploys. It is not inherited from the September 7 pre-user release
or any other earlier release: this migration rewrites dates and the JSON-LD
identifier base, which changes the generated editor map and persona bundle the
Worker embeds. The proof records `PRIOR_PAIR_SHA`, `PRIOR_SHA`, the candidate
SHA, and the result of each check:

1. **Editor map against old pages.** The production Worker's `/edit` injector
   fetches pages from its `EDIT_UPSTREAM` (`https://legalpracticum.org/platform/`,
   the production Pages origin) and overlays its bundled
   `editor-data/editor-map.generated.json`, stamping that map's
   `spine_build_id` into the page as `editor-map-version`. Compare the page-key
   set of `EDITOR_MAP.pages` built from `PRIOR_PAIR_SHA` (the Pages deployment
   actually live during both transients) with the one built from the
   candidate; they must be identical, because the migration rewrites content,
   not page structure. Record both `spine_build_id` values. They differ by
   design, and the editor client's `map_version` and `base_hash` stale guards
   absorb that difference. A missing or added page key is a stop. The editor
   map is an ignored output, so build the prior one in a disposable clone
   outside every checkout, from the controlled worktree (`$CONTROLLED_REPO`)
   after the candidate's ignored inputs have been regenerated (OQ-10):

   ```bash
   PRIOR_MAP_DIR=$(mktemp -d)
   git clone --quiet --no-hardlinks --no-checkout "$CONTROLLED_REPO" "$PRIOR_MAP_DIR/tree"
   git -C "$PRIOR_MAP_DIR/tree" checkout --quiet --detach "$PRIOR_PAIR_SHA"
   test "$(git -C "$PRIOR_MAP_DIR/tree" rev-parse HEAD)" = "$PRIOR_PAIR_SHA"
   ( cd "$PRIOR_MAP_DIR/tree" &&
     python3 tools/build_site.py --check &&
     python3 tools/build_worker_personas.py &&
     python3 tools/build_instructor_bundle.py &&
     python3 tools/build_history.py &&
     node app/worker/scripts/bundle-editor-data.mjs )
   echo "prior build rc=$?"
   python3 - "$PRIOR_MAP_DIR/tree/build/editor-map.generated.json" \
     "$CONTROLLED_REPO/build/editor-map.generated.json" <<'PY'
   import json, sys
   prior, candidate = (json.load(open(path, encoding="utf-8")) for path in sys.argv[1:3])
   a, b = set(prior["pages"]), set(candidate["pages"])
   print(json.dumps({
       "prior_page_keys": len(a), "candidate_page_keys": len(b),
       "identical": a == b, "missing": sorted(a - b), "added": sorted(b - a),
       "prior_spine_build_id": prior.get("spine_build_id"),
       "candidate_spine_build_id": candidate.get("spine_build_id"),
   }, sort_keys=True))
   sys.exit(0 if a == b and a else 1)
   PY
   echo "check-1 rc=$?"
   ```

   Require `prior build rc=0`, `check-1 rc=0`, nonzero key counts, and two
   64-character `spine_build_id` values. Then rerun only the Python comparison
   (from `python3 - ...` through `echo "check-1 rc=$?"`) with
   `"$PRIOR_DIR/tree/build/editor-map.generated.json"` as its first argument
   in place of the `$PRIOR_MAP_DIR` path. That is the `PRIOR_SHA` map that
   step 1a.3 built, and it covers the DEV transients (OQ-13). Require
   `check-1 rc=0` again. Keep both JSON lines in the window evidence
   directory, then delete `$PRIOR_MAP_DIR`.
2. **Chat contract.** The Worker resolves `persona_id` and `matter_id` against
   its bundled `app/worker/personas/personas.generated.json` (`personas`,
   `fact_map`, `rubrics`). Compare the key sets of those three maps between
   the candidate and **both** `git show "$PRIOR_SHA":app/worker/personas/personas.generated.json`
   and `git show "$PRIOR_PAIR_SHA":app/worker/personas/personas.generated.json`;
   they must be identical. From `$CONTROLLED_REPO` at the committed
   candidate:

   ```bash
   python3 - "$PRIOR_SHA" "$PRIOR_PAIR_SHA" "$CANDIDATE_SHA" <<'PY'
   import json, re, subprocess, sys
   KEYS = ("personas", "fact_map", "rubrics")
   def key_sets(sha):
       if not re.fullmatch("[0-9a-f]{40}", sha):
           sys.exit("STOP: malformed SHA %r" % sha)
       blob = subprocess.run(
           ["git", "show", sha + ":app/worker/personas/personas.generated.json"],
           check=True, capture_output=True).stdout
       data = json.loads(blob)
       return {key: sorted(data[key]) for key in KEYS}
   prior, pair, candidate = (key_sets(sha) for sha in sys.argv[1:4])
   result = {key: {"prior": len(prior[key]), "pair": len(pair[key]),
                   "candidate": len(candidate[key]),
                   "identical": prior[key] == pair[key] == candidate[key]}
             for key in KEYS}
   print(json.dumps(result, sort_keys=True))
   ok = all(r["identical"] and r["candidate"] > 0 for r in result.values())
   sys.exit(0 if ok else 1)
   PY
   echo "check-2 rc=$?"
   ```

   Require `check-2 rc=0` and keep the JSON line in the window evidence
   directory. Require identity, not only inclusion: the
   checked-in chat pages' `sonsteng-api` meta tag names the top-level DEV
   Worker (`sonsteng-chat.damienriehl.workers.dev`), not the production Worker,
   so between the Pages deploy (step 6) and the DEV deploy (step 8) new Pages
   chat talks to the DEV Worker that step 1a left on `PRIOR_SHA`, and during
   step 9's prior-pair drill the `PRIOR_PAIR_SHA` pages talk to the candidate
   DEV Worker.
3. **Provenance during the transient.** After the Worker deploy and before
   Pages, a `GET` (not `HEAD`) of
   `https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance`
   must answer `204` with `x-release-sha` equal to the candidate SHA, while a
   `GET` of `https://legalpracticum.org/` still answers `200` with
   `x-release-sha` equal to `PRIOR_PAIR_SHA`. Use `prove_provenance` from
   [Two prior SHAs](#two-prior-shas-prior_pair_sha-and-prior_sha):

   ```bash
   prove_provenance https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance 204 "$CANDIDATE_SHA"
   prove_provenance https://legalpracticum.org/ 200 "$PRIOR_PAIR_SHA"
   ```

   The inspector's shared-SHA proof fails by design in that interval; do not
   run it as acceptance until both targets are deployed.

The production executor's `CompatibilityGate` yields the same
`("worker", "pages")` order only when `new_worker_accepts_old_pages` is the
sole proved direction. The installed daemon template sets both
`SONSTENG_NEW_WORKER_ACCEPTS_OLD_PAGES` and
`SONSTENG_OLD_WORKER_ACCEPTS_NEW_PAGES` to `false`, so this manual window never
relies on a daemon default.

**OQ-9: the Pages recovery coordinate.** The Pages recovery coordinate is the
full `canonical_deployment.id` reported by the stable inspector
(`--print-recovery-ids`, or `--print-operator-plan` in combined mode), both for
the prior pair captured before any mutation and for the new pair read back in
step 7. A non-production proof on September 7, 2026 showed that the Pages
rollback endpoint accepts that full canonical ID and rejects the short
preview-subdomain ID. The subdomain that `WranglerPagesAdapter.deploy()`
parses from Wrangler output (`deployable_id`), and that the pre-user procedure
calls "the deployment ID", is therefore **not** a recovery coordinate for this
window; never record it in the recovery registry or the D2 paste-back. The
executor's rollback handler treats Cloudflare error code `8000039` as "already
active". That code has **not** been verified against the live API. If a
rollback to the deployment that is already active returns any error, prove the
state with an inspector readback rather than trusting the error code.

#### Pages and Worker rollback commands

These are the only rollback commands for the window: step 9's drill and every
compensation path use them. Record the four recovery coordinates as shell
variables when they are read (never retype them later): step 3 sets
`PRIOR_PAGES_DEPLOYMENT_ID` and `PRIOR_WORKER_VERSION_ID` from the
`--print-recovery-ids` JSON fields `pages_deployment_id` and
`worker_version_id`; step 7 sets `NEW_PAGES_DEPLOYMENT_ID` and
`NEW_WORKER_VERSION_ID` the same way. Print `${#…}` for each; an empty value,
or a Pages value as short as the 8-character preview subdomain, is a stop.

**Pages.** Pages has no version-deploy CLI; rollback is one `POST` to the
Cloudflare Pages rollback API, issued by the executor's own
`WranglerPagesAdapter.restore` (`tools/prod_release_executor.py`). It posts
to `/accounts/<account>/pages/projects/sonsteng/deployments/<id>/rollback`
and succeeds only when the response binds the exact requested ID. The bearer
comes from the credential helper that prints only the Cloudflare bearer (see
the private operator notes). The bearer arrives only on stdin; it never
appears in argv, the environment, or any output. The helper below disables
redirects, so the bearer can never be forwarded to another host. Define it
once in the window shell, after the pinning block has set `OPS_REPO`:

```bash
CF_ACCOUNT_ID=<32-character-lowercase-account-ID>
echo "account id len=${#CF_ACCOUNT_ID}"   # must print 32
pages_rollback() {
  /usr/bin/python3 -I -c '
import re, sys, urllib.request
sys.path.insert(0, sys.argv[1])
import prod_release_executor as e
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None
account, target = sys.argv[2], sys.argv[3]
if not re.fullmatch("[0-9a-f]{32}", account) or len(target) <= 8:
    sys.exit("STOP: account or deployment ID malformed")
token = sys.stdin.readline().strip()
if not token:
    sys.exit("STOP: no Cloudflare bearer on stdin")
adapter = e.WranglerPagesAdapter(
    "sonsteng", "site", "https://legalpracticum.org/",
    opener=urllib.request.build_opener(NoRedirect).open,
    account_id=account, api_token=token)
try:
    adapter.restore(target)
except e.ReleaseError as exc:
    sys.exit("STOP: " + str(exc))
except Exception:
    sys.exit("STOP: Pages rollback request failed")
print("pages rollback accepted:", target)' "$OPS_REPO/tools" "$CF_ACCOUNT_ID" "$1"
}
```

Use it with the bearer piped in:

```bash
credential-helper-that-prints-only-the-Cloudflare-bearer | pages_rollback "$PRIOR_PAGES_DEPLOYMENT_ID"
echo "pages rollback rc=$?"
```

It must print `pages rollback accepted: <ID>` and `pages rollback rc=0`. Any
`STOP` line, including the adapter's `Pages rollback API rejected the exact
deployment`, is a failure. Rolling back to the deployment that is already
active may be accepted silently (error code `8000039`, not live-verified);
either way, prove the result with the inspector, never with this output alone.

**Worker.** From `app/worker` of any checkout of this repository (the
controlled worktree is the default), under the Cloudflare PROD principal:

```bash
( cd "$CONTROLLED_REPO/app/worker" &&
  npx wrangler@4 versions deploy "$PRIOR_WORKER_VERSION_ID" --env production --yes )
echo "worker rollback rc=$?"
```

Substitute `NEW_WORKER_VERSION_ID` (and `NEW_PAGES_DEPLOYMENT_ID` above) to
return to the new pair. The DEV equivalent is
`npx wrangler@4 versions deploy "$STEP1A_DEV_VERSION_ID" --env="" --yes`
(compensation step 3) or `"$PRE1A_DEV_VERSION_ID"` (a step-1a failure).

**Readback.** After each pair change, run the `--print-recovery-ids`
inspector. Its `sha` must equal the pair's SHA (`PRIOR_PAIR_SHA` or
`CANDIDATE_SHA`), and its `pages_deployment_id` and `worker_version_id` must
equal the two variables just used, compared with `test`, not by eye.

**OQ-10: contents of the one migration commit.** The single migration commit
contains the governed `data/**` source changes, including the per-matter
`data/matters/*/date-offsets.json` sidecars and the identifier-base rewrite
across `identifier_base.authoritative_paths()`, plus the tracked generator
outputs: `site/platform/**` (including `site/platform/data/.build-stamp.json`)
and `app/worker/personas/personas.generated.json`. The ignored outputs,
`build/**` (editor map, instructor bundle, history bundle) and
`app/worker/editor-data/**` (copied in by `app/worker/scripts/bundle-editor-data.mjs`),
are never force-added. Before **each** Worker upload in the window, including
DEV and any compensating redeploy, regenerate them from the exact checkout
being uploaded (`python3 tools/build_site.py --check`,
`python3 tools/build_worker_personas.py`,
`python3 tools/build_instructor_bundle.py`, `python3 tools/build_history.py`,
then `node app/worker/scripts/bundle-editor-data.mjs`). Then verify that the
`spine_build_id` in `app/worker/editor-data/editor-map.generated.json`,
`app/worker/editor-data/instructor-bundle.generated.json`, and
`app/worker/personas/personas.generated.json` equals the committed
`site/platform/data/.build-stamp.json` value, each a 64-character hex string.
Restore the tracked build stamp afterwards
(`git checkout -- site/platform/data/.build-stamp.json`) so the upload tree
stays exactly the committed tree. Run this from the root of the checkout being
uploaded, after the five generator commands:

```bash
python3 - <<'PY'
import json, re, subprocess, sys
head = subprocess.run(["git", "rev-parse", "HEAD"], check=True,
                      capture_output=True, text=True).stdout.strip()
stamp = json.loads(subprocess.run(
    ["git", "show", "HEAD:site/platform/data/.build-stamp.json"],
    check=True, capture_output=True).stdout)["spine_build_id"]
paths = ("app/worker/editor-data/editor-map.generated.json",
         "app/worker/editor-data/instructor-bundle.generated.json",
         "app/worker/personas/personas.generated.json")
values = {path: json.load(open(path, encoding="utf-8")).get("spine_build_id")
          for path in paths}
print("HEAD", head, "committed spine_build_id", stamp)
for path, value in values.items():
    print(path, value)
ok = (re.fullmatch("[0-9a-f]{64}", stamp or "") is not None
      and all(value == stamp for value in values.values()))
print("SPINE OK" if ok else "STOP: spine_build_id mismatch")
sys.exit(0 if ok else 1)
PY
echo "spine rc=$?"
git checkout -- site/platform/data/.build-stamp.json
PORCELAIN=$(git status --porcelain) && test -z "$PORCELAIN" && echo "TREE CLEAN" || echo "STOP: tree dirty or git failed"
```

Require `HEAD` to equal the SHA being uploaded, `SPINE OK`, `spine rc=0`,
and `TREE CLEAN`.

Stage the migration commit explicitly. `day_zero.py --write` creates the
per-matter `data/matters/*/date-offsets.json` sidecars as **new untracked**
files, so `git commit -a` would silently leave them out. From the controlled
worktree, after reviewing the full diff:

```bash
git add data site/platform app/worker/personas/personas.generated.json
git status --short --untracked-files=all   # nothing may remain unstaged or untracked
git commit -m "feat(day-zero): materialize Day Zero dates and legalpracticum.org identifiers"
CANDIDATE_SHA=$(git rev-parse HEAD)
test "${#CANDIDATE_SHA}" -eq 40
test "$(git rev-parse HEAD^)" = "$PRIOR_SHA"
test "$(git rev-list --count "$PRIOR_SHA..$CANDIDATE_SHA")" = 1
```

Never use `git commit -a` or `git add -A` here, and never force-add `build/`
or `app/worker/editor-data/`. Any path outside those three that `git status`
still lists is a stop: it means a generator wrote somewhere unexpected.

The committed stamp's `git_base_sha` is `PRIOR_SHA`, not the candidate SHA.
That is expected: the stamp is generated before the commit exists, and
`git_base_sha` is traceability metadata, not a provenance claim. Phase 2
compares stamps without that field. Provenance comes from `x-release-sha` and
from `spine_build_id`.

**OQ-11: one candidate commit, then separate evidence.** "Merge only that
commit" is the rule for the live corpus candidate. The window forwards
canonical `main` by exactly that one commit, and every provider, DEV, and
editor surface names that SHA. The UAT and evidence record (the D2 paste-back
values and the rows in `docs/uat/`) lands afterwards as a separate docs-only
commit, after the window closes and the apply timer's prior policy is
restored. It never amends, rebases, or replaces the candidate, so the
candidate SHA stays the deployed provenance SHA, and it is not deployed as
part of this window. Strict enforcement
(`python3 tools/validate_spine.py --strict --enforce-day-zero-offsets --enforce-legal-practicum-identifiers`)
runs twice: before any deploy, inside the Phase 2 verification of the exact
candidate, and again after the window closes against that same candidate SHA,
with nonzero scope counts and zero old-base occurrences. The second run is the
durable U16b evidence Packet D requests.

**OQ-12: which procedure is authoritative.** The September 7 pre-user
production procedure (`docs/pre-user-prod-deploy.md`) is **not** Packet D's
authority. Its lane expires at the first real user, and repository state cannot
prove that none exists. Packet D runs under this migration-specific supervised
protocol: the KTD6 waiver, the Cloudflare PROD principal, the six-actor
freeze, the queue proofs, the CAS-bounded canonical moves, and the restoration
and evidence steps below. Reuse only that document's provider command
mechanics (`wrangler versions upload`/`versions deploy` with
`--var "RELEASE_SHA:<sha>"`, the staged Pages `_headers` provenance, and the
GET provenance checks), and only where they agree with this runbook.

**OQ-13: DEV/editor commands.** "DEV/editor from one SHA" means two
surfaces, always deployed in this order, (a) then (b), from the same clean
exact-SHA checkout, with the OQ-10 ignored inputs regenerated first. One fixed
order is used for every use (step 1a, step 8, and compensation step 3), so the
operator runs one pattern. Either order leaves a brief mixed DEV state: going
forward (step 8), the prior DEV Worker overlays candidate static pages; going
back (compensation), the candidate DEV Worker overlays prior static pages, the
same direction production uses. Both mixed states are covered, because the
OQ-8 checks are identity checks and therefore symmetric: the editor page-key
set is identical between `PRIOR_SHA` and the candidate (check 1, second run),
and the persona, fact-map, and rubric key sets are identical (check 2). Under
pencils-down no editor traffic flows during either transient.


(a) **DEV static site.** `edit.legalpracticum.org` is served by the
`sonsteng-chat` Worker, whose top-level `EDIT_UPSTREAM` is the Hetzner DEV
static origin `https://sonsteng-dev.damienriehl.com/platform/`. Publish it with
`deploy/deploy-dev.sh`, which takes one positional Git ref (default `main`),
stages `git archive <ref>` from the repository that contains the script,
rsyncs it to the `hetzner-dev` SSH host, and restarts the `sonsteng` compose
project. Pass the exact 40-character SHA, never a branch name, and run it from
a checkout that contains that commit on a host with the `hetzner-dev` SSH
alias (the daemon host):

```bash
bash deploy/deploy-dev.sh "$TARGET_SHA"
echo "deploy-dev rc=$?"
EXPECTED_SPINE=$(git show "$TARGET_SHA:site/platform/data/.build-stamp.json" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["spine_build_id"])')
LIVE_SPINE=$(curl -fsS --max-time 20 --proto '=https' https://sonsteng-dev.damienriehl.com/platform/ |
  python3 -c 'import re,sys; m=re.findall(r"<meta name=\"spine-build\" content=\"([0-9a-f]*)\">", sys.stdin.read()); print(m[0] if len(m)==1 else "")')
echo "expected=${#EXPECTED_SPINE}:$EXPECTED_SPINE live=${#LIVE_SPINE}:$LIVE_SPINE"
test "${#EXPECTED_SPINE}" -eq 64 && test "$LIVE_SPINE" = "$EXPECTED_SPINE" && echo "DEV static OK" || echo "STOP: DEV static mismatch"
```

The DEV static site carries a `spine_build_id`, not a Git SHA, so this
comparison is the DEV static half of every "names the SHA" proof. Use the
committed `.build-stamp.json` of `$TARGET_SHA`; its `git_base_sha` is that
commit's parent by design (OQ-10) and is not compared.

(b) **DEV/editor Worker.** Deploy the top-level `sonsteng-chat` Worker with
the same version upload and activation pattern as production, from
`app/worker/` of the same checkout:

```bash
npx wrangler@4 versions upload --env="" --message "day-zero:$TARGET_SHA" --var "RELEASE_SHA:$TARGET_SHA"
echo "upload rc=$?"
DEV_VERSION_ID=<paste the full "Worker Version ID:" UUID printed above>
echo "dev version id len=${#DEV_VERSION_ID}"   # must print 36
npx wrangler@4 versions view "$DEV_VERSION_ID" --env="" --json | check_version_view dev "$TARGET_SHA"
echo "view rc=$?"
npx wrangler@4 versions deploy "$DEV_VERSION_ID" --env="" --yes
```

Wrangler 4 accepts `--env=""` as the explicit top-level (DEV) target for these
commands; do not use `--env dev` or omit the flag during the window. Copy the
full `Worker Version ID:` UUID printed by the upload into the window log
before activating it. Run `versions deploy` only after `check_version_view`
prints `VIEW OK` and `view rc=0`. The helper checks the exact 40-character
`RELEASE_SHA` and the exact DEV `EDIT_UPSTREAM`, `EDIT_ORIGIN`, and
`PROD_RELEASE_LEDGER` values from `wrangler.jsonc`; any other result is a stop.
Do not read the plain `versions view` table instead: Wrangler 4.141 truncates
values of 40 or more characters to 37 plus `...`, so a SHA can never be
confirmed there. Then prove the Worker:

```bash
prove_provenance https://sonsteng-chat.damienriehl.workers.dev/edit/release-provenance 204 "$TARGET_SHA"
```

The apply daemon's DEV deploys (`wrangler@latest deploy`) do not set
`RELEASE_SHA`. DEV provenance is therefore only trustworthy when the last
deploy set `RELEASE_SHA`; after any daemon apply or revert it answers `503`,
and before that it may still name whatever SHA an earlier manual upload set,
which proves nothing about the code currently deployed. The `--var` above is
what makes the DEV part of step 10's proof possible.

The same two parts, (a) then (b), are used three times with different SHAs:
step 1a with `TARGET_SHA=$PRIOR_SHA` (its own part (b) block adds the observer
secrets file), step 8 with `TARGET_SHA=$CANDIDATE_SHA`, and compensation
step 3 with `TARGET_SHA=$PRIOR_SHA`, where part (b) first tries to reactivate
the recorded step-1a DEV version. Set `TARGET_SHA`, and check
`${#TARGET_SHA}` is 40, before each use; no command line carries a
hand-substituted SHA.

### Two prior SHAs: PRIOR_PAIR_SHA and PRIOR_SHA

The window has two different "prior" values. Record both, length-checked, and
never substitute one for the other.

- **`PRIOR_PAIR_SHA`** is the SHA the live production Pages/Worker pair
  reports in both `x-release-sha` headers, as read by the stable inspector in
  step 3 together with the exact prior Pages canonical deployment ID and Worker
  version ID. It is what step 9's prior-pair drill and compensation step 1
  restore production to. It can be, and on 2026-09-25 was, an older commit
  than canonical `main`: production had last been deployed from an earlier
  release.
- **`PRIOR_SHA`** is canonical `main`'s tip at window open, which is the
  candidate's parent. The controlled worktree is created at it, the CAS
  `forward --from` and `restore --to` use it, and step 1a and compensation
  step 3 deploy DEV/editor from it. It must contain the operation-frontier
  merge: `git merge-base --is-ancestor 93b80a7 "$PRIOR_SHA"` must exit `0`.

`git merge-base --is-ancestor "$PRIOR_PAIR_SHA" "$PRIOR_SHA"` must also exit
`0`; production running a commit that `main` does not contain is a stop.

The other window variables are `CANDIDATE_SHA` (set by the OQ-10 commit
block), `CONTROLLED_REPO` (the absolute path of the controlled migration
worktree, a detached linked worktree of `$DAEMON_REPO` at `PRIOR_SHA`, created
by the command in step 4), `TARGET_SHA` (the SHA of the current OQ-13 use),
and the recovery coordinates listed under
[Pages and Worker rollback commands](#pages-and-worker-rollback-commands).
Commands that name a relative path such as `deploy/deploy-dev.sh` or
`app/worker` run from the checkout that step names. Step 1a leaves the shell
inside `$PRIOR_DIR/tree/app/worker`; step 3 therefore begins with
`cd "$OPS_REPO"`, and step 4 creates `$CONTROLLED_REPO` and changes into it.

Define this helper once in the window shell. It issues one redirect-free
`GET`, prints what it saw, and succeeds only on the exact status, exactly one
`x-release-sha` header, and an exact 40-character match:

```bash
prove_provenance() {
  local url=$1 want_status=$2 want_sha=$3 headers status count sha
  case "$want_sha" in ''|*[!0-9a-f]*) echo "STOP: expected SHA malformed"; return 1 ;; esac
  test "${#want_sha}" -eq 40 || { echo "STOP: expected SHA length ${#want_sha}"; return 1; }
  headers=$(curl -sS --max-time 20 --proto '=https' -o /dev/null -D - "$url") ||
    { echo "STOP: request failed for $url"; return 1; }
  headers=$(printf '%s\n' "$headers" | tr -d '\r')
  status=$(printf '%s\n' "$headers" | awk 'NR==1 {print $2}')
  count=$(printf '%s\n' "$headers" | grep -ci '^x-release-sha:')
  sha=$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^x-release-sha:/ {sub(/^[^:]*:[ \t]*/, ""); print}')
  echo "url=$url status=$status headers=$count sha_len=${#sha} sha=$sha"
  if test "$status" = "$want_status" && test "$count" = 1 &&
     test "${#sha}" -eq 40 && test "$sha" = "$want_sha"; then
    echo "OK"
  else
    echo "STOP: provenance mismatch"; return 1
  fi
}
```

Define these two helpers once in the window shell as well. Wrangler 4.141's
human-readable `versions view` table truncates every value of 40 or more
characters to its first 37 characters plus `...`, so a 40-character
`RELEASE_SHA` (and the 47-character DEV `EDIT_UPSTREAM`) can never be
confirmed from it. Always read a version with `versions view <ID> --json` and
pipe it to `check_version_view`. The JSON is the Cloudflare API version
object; its `resources.bindings` list gives each plain-text var's full `text`
and each secret's `name` only. The API never returns secret values, so the
helper cannot print one. Its first argument selects the expected values from
`app/worker/wrangler.jsonc` (`dev` for the top-level `sonsteng-chat` Worker,
`production` for `env.production`), its second is the exact expected SHA, and
any further arguments are secret names that must be present:

```bash
check_version_view() {
  python3 -c '
import json, re, sys
env, want_sha, required = sys.argv[1], sys.argv[2], sys.argv[3:]
expected = {
    "dev": {
        "EDIT_UPSTREAM": "https://sonsteng-dev.damienriehl.com/platform/",
        "EDIT_ORIGIN": "https://edit.legalpracticum.org,https://sonsteng-chat.damienriehl.workers.dev",
        "PROD_RELEASE_LEDGER": "true",
    },
    "production": {
        "EDIT_UPSTREAM": "https://legalpracticum.org/platform/",
        "EDIT_ORIGIN": "https://sonsteng-chat-production.damienriehl.workers.dev",
        "PROD_RELEASE_LEDGER": "false",
    },
}.get(env)
try:
    bindings = json.load(sys.stdin)["resources"]["bindings"]
    plain = {b["name"]: b.get("text") for b in bindings if b.get("type") == "plain_text"}
    secrets = sorted(b["name"] for b in bindings if b.get("type") == "secret_text")
except (ValueError, KeyError, TypeError):
    print("STOP: version view JSON unreadable")
    sys.exit(1)
sha = plain.get("RELEASE_SHA") or ""
checks = {
    "known env": expected is not None,
    "expected SHA is 40 hex": re.fullmatch("[0-9a-f]{40}", want_sha) is not None,
    "RELEASE_SHA exact": len(sha) == 40 and sha == want_sha,
    "required secret names": all(re.fullmatch("[A-Z0-9_]+", n) and n in secrets for n in required),
}
for key, value in (expected or {}).items():
    checks[key] = plain.get(key) == value
print("RELEASE_SHA len=%d value=%s" % (len(sha), sha))
for key in ("EDIT_UPSTREAM", "EDIT_ORIGIN", "PROD_RELEASE_LEDGER"):
    print("%s=%s" % (key, plain.get(key)))
print("secret names:", " ".join(secrets))
missing = [n for n in required if n not in secrets]
if missing:
    print("missing secret names:", " ".join(missing))
failed = [name for name, ok in checks.items() if not ok]
print("VIEW OK" if not failed else "STOP: version view mismatch: " + ", ".join(failed))
sys.exit(1 if failed else 0)' "$@"
}

version_secret_names() {
  python3 -c '
import json, re, sys
try:
    bindings = json.load(sys.stdin)["resources"]["bindings"]
    names = sorted(b["name"] for b in bindings if b.get("type") == "secret_text")
except (ValueError, KeyError, TypeError):
    sys.exit("STOP: version view JSON unreadable")
if not names or not all(re.fullmatch("[A-Z0-9_]+", n) for n in names):
    sys.exit("STOP: no or unexpected secret names")
print(" ".join(names))'
}
```

`check_version_view` prints `VIEW OK` and returns `0` only when every check
passes; otherwise it prints `STOP: version view mismatch:` with the failed
checks and returns `1`. An empty or failed `wrangler` read reaches it as
unreadable JSON, which is also a stop. `version_secret_names` prints only the
sorted secret names, space-separated, and fails on an empty list.

The dependency-injected state machine and the generated operator sheet model a
single prior SHA. When the two values differ, neither may be used for this
window; the numbered sequence below is the authority.

## Remaining supervised U15 act

Damien must perform the production window at the keyboard under the Cloudflare
PROD principal described in `docs/prod-release-operations.md`:

Before pencils-down or either timer is changed, run this network preflight from
the reviewed values recorded outside the checkout. It performs an authenticated
TLS handshake to the allowlisted ledger origin but makes no HTTP/API request.
It must exit `0` with `preflight.ready:true`, the reviewed measured
`verifier_blob`, `/usr/bin/systemctl`, and the expected system-CA-bundle path,
SHA-256, nonzero root count, and negotiated TLS protocol/cipher. A
missing/unreadable bundle fails as
`https-client-unavailable`; a missing trusted-path systemctl fails as
`systemctl-unavailable`; a failed handshake fails as
`https-handshake-unavailable`. Compare the reported bundle SHA-256 and root count to
the approved baseline for this production host before opening the window:

```bash
builtin test "$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C /home/damienriehl/.local/share/sonsteng-daemon/checkout rev-parse --verify 'HEAD^{commit}')" = \
  '<reviewed-release-commit-SHA>' || exit 72
builtin test "$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C /home/damienriehl/.local/share/sonsteng-daemon/checkout rev-parse '<reviewed-release-commit-SHA>:tools/prove_queues_empty.py')" = \
  '<reviewed-verifier-Git-blob-OID>' || exit 73
builtin test "$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C /home/damienriehl/.local/share/sonsteng-daemon/checkout hash-object -- /home/damienriehl/.local/share/sonsteng-daemon/checkout/tools/prove_queues_empty.py)" = \
  '<reviewed-verifier-Git-blob-OID>' || exit 74
(
  builtin exec -c /usr/bin/env -i LC_ALL=C \
    /usr/bin/python3 -I -B --check-hash-based-pycs always \
    /proc/self/fd/9 \
    --preflight \
    --release-commit '<reviewed-release-commit-SHA>' \
    --verifier-blob '<reviewed-verifier-Git-blob-OID>' \
    --ledger-origin https://sonsteng-chat.damienriehl.workers.dev \
    --receipt-path '<absolute-preflight-receipt-path>.json'
) 9</home/damienriehl/.local/share/sonsteng-daemon/checkout/tools/prove_queues_empty.py
queue_proof_preflight_rc=$?
```

This preflight intentionally reports `all_queues_empty:false`: it checks that
the proof machinery is ready, not the live queues. Treat any skipped comparison
or nonzero result as a stop before the window.

1. notify John, establish pencils-down, stop the apply timer, prove both
   services quiescent, take the daemon lock, and establish the six-actor
   exclusive change window. **Stop the apply timer; do not disable it.** The
   step-2 receipt validator requires `fence.apply_timer` to read back exactly
   `{"active":false,"available":true,"enabled":true}`, so a disabled apply
   timer fails the queue proof (validator exit `75`). The production release
   timer is the opposite: it must already be disabled and inactive
   (`timer` must read `{"active":false,"available":true,"enabled":false}`).

   ```bash
   systemctl --user stop sonsteng-apply.timer; echo "stop rc=$?"
   systemctl --user is-active sonsteng-apply.timer    # must print: inactive
   systemctl --user is-enabled sonsteng-apply.timer   # must print: enabled
   systemctl --user is-active sonsteng-prod-release.timer    # must print: inactive
   systemctl --user is-enabled sonsteng-prod-release.timer   # must print: disabled
   ```

   **1a. Re-establish DEV/editor at `PRIOR_SHA`** (after the window is
   established, before the opening queue proof). The opening proof reads the
   operation frontier from the ledger on the `sonsteng-chat` Worker, so that
   Worker must run code at or after the observer-frontier merge (PR #63,
   `93b80a7`). Nothing proves that today: the apply daemon deploys it without
   `RELEASE_SHA`, so its provenance is only trustworthy when the last deploy
   set `RELEASE_SHA`, and a live read on 2026-09-25 showed it naming a
   pre-#63 SHA left by an earlier manual upload. Step 1a puts the ledger/editor
   Worker and the DEV static site on `PRIOR_SHA`, which is canonical `main`'s
   tip at window open (the candidate's parent), already contains #63, and is
   the state compensation returns DEV to.

   1. Fix `PRIOR_SHA` from canonical `main` and prove it contains #63:

      ```bash
      PRIOR_SHA=$(git -C "$DAEMON_REPO" rev-parse --verify 'refs/heads/main^{commit}')
      test "${#PRIOR_SHA}" -eq 40
      git -C "$DAEMON_REPO" ls-remote origin refs/heads/main   # first field must equal $PRIOR_SHA
      git -C "$DAEMON_REPO" merge-base --is-ancestor 93b80a7 "$PRIOR_SHA" && echo "post-#63 OK" || echo "STOP: pre-#63"
      git -C "$DAEMON_REPO" show "$PRIOR_SHA:app/worker/wrangler.jsonc" |
        grep -c '\\"observer\\":{\\"release_observer\\":1}'   # must print 2 (top-level and env.dev)
      ```

      The last check proves that `PRIOR_SHA` carries the reviewed observer
      slot, which grants only `release_observer`, in the top-level and
      `env.dev` `EDIT_TOKEN_SCOPES`. Production does not carry it.
      Sub-step 6 uploads the Worker with `PRIOR_SHA`'s vars, so a count other
      than `2` is a STOP before any deploy.

   2. From `app/worker/` of any checkout, run
      `npx wrangler@4 deployments list --env=""` and record the active DEV
      Worker version ID (the last entry, at 100 percent; full 36-character
      UUID). This **pre-1a DEV version ID** is the rollback coordinate if step
      1a fails. Record it and capture its secret names (names only; the API
      never returns secret values):

      ```bash
      PRE1A_DEV_VERSION_ID=<the full 36-character version ID at 100 percent>
      echo "pre-1a id len=${#PRE1A_DEV_VERSION_ID}"   # must print 36
      PRE1A_SECRET_NAMES=$(npx wrangler@4 versions view "$PRE1A_DEV_VERSION_ID" --env="" --json | version_secret_names)
      echo "pre-1a names rc=$? names=$PRE1A_SECRET_NAMES"
      ```

      Require `pre-1a names rc=0` and a non-empty list; keep the line in the
      window log. Sub-step 6 requires every one of these names, plus
      `EDIT_TOKEN_OBSERVER`, in the step-1a version.
   3. Create a clean `PRIOR_SHA` checkout outside every existing checkout and
      regenerate the ignored Worker inputs there (OQ-10):

      ```bash
      PRIOR_DIR=$(mktemp -d)
      git clone --quiet --no-hardlinks --no-checkout "$DAEMON_REPO" "$PRIOR_DIR/tree"
      git -C "$PRIOR_DIR/tree" checkout --quiet --detach "$PRIOR_SHA"
      test "$(git -C "$PRIOR_DIR/tree" rev-parse HEAD)" = "$PRIOR_SHA"
      cd "$PRIOR_DIR/tree"
      python3 tools/build_site.py --check && python3 tools/build_worker_personas.py &&
        python3 tools/build_instructor_bundle.py && python3 tools/build_history.py &&
        node app/worker/scripts/bundle-editor-data.mjs; echo "prior regen rc=$?"
      ```

      Then, still in `$PRIOR_DIR/tree`, run the OQ-10 `spine_build_id`
      equality block (it restores the stamp and checks the tree); require its
      `HEAD` to equal `PRIOR_SHA`, `SPINE OK`, `spine rc=0`, and `TREE CLEAN`.
   4. **DEV static prior-state check.** With `TARGET_SHA=$PRIOR_SHA`, run the
      OQ-13 part (a) `spine-build` comparison without deploying first. If it
      prints `DEV static OK`, DEV static is already on `PRIOR_SHA`. Otherwise
      run `bash deploy/deploy-dev.sh "$PRIOR_SHA"` from `$PRIOR_DIR/tree` and
      repeat the comparison until it prints `DEV static OK`.
   5. **Mint the read-only observer credential** (Damien approved this
      2026-09-25). The opening queue proof reads the operation frontier with a
      bearer that has only `release_observer` authority. The agent generates
      that bearer locally, stores it in the observer environment file, and
      sets it as the `sonsteng-chat` secret `EDIT_TOKEN_OBSERVER` in the same
      Worker version that sub-step 6 uploads and deploys. The value is never
      displayed, typed, pasted, or passed as a command-line argument. Worker
      bearer auth compares the whole presented token with each slot's secret
      (`Authorization: Bearer <token>`, no slot prefix), so the file holds only
      the raw token.

      ```bash
      OBS_ENV="$HOME/.config/sonsteng-release-observer/env"
      python3 - "$OBS_ENV" <<'PY'
      import os, re, secrets, stat, sys
      path = sys.argv[1]
      directory = os.path.dirname(path)
      os.makedirs(directory, mode=0o700, exist_ok=True)
      os.chmod(directory, 0o700)
      TEMPLATE = ["SONSTENG_PROD_OBSERVER_BEARER="]
      def mint(target):
          line = "SONSTENG_PROD_OBSERVER_BEARER=" + secrets.token_urlsafe(32) + "\n"
          fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
          with os.fdopen(fd, "w", encoding="utf-8") as handle:
              handle.write(line)
              handle.flush()
              os.fsync(handle.fileno())
      try:
          info = os.lstat(path)
      except FileNotFoundError:
          mint(path)
          print("observer env minted")
          sys.exit(0)
      if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
              or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
          sys.exit("STOP: observer env is not an owned regular mode-0600 file; left untouched")
      fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
      with os.fdopen(fd, encoding="utf-8") as handle:
          text = handle.read()
      assignments = [line for line in text.splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
      if assignments == TEMPLATE:
          mint(path + ".mint")
          os.replace(path + ".mint", path)
          print("observer env was the empty installer template; minted")
      elif (len(text.splitlines()) == 1 and re.fullmatch(
              "SONSTENG_PROD_OBSERVER_BEARER=[A-Za-z0-9_-]{43}", text.rstrip("\n"))):
          print("observer env exists and is populated; reusing it")
      else:
          sys.exit("STOP: observer env has unexpected content; left untouched")
      PY
      echo "observer mint rc=$?"
      stat -c '%a %U %F' "$OBS_ENV"    # must print: 600 <operator> regular file
      grep -c '^SONSTENG_PROD_OBSERVER_BEARER=[A-Za-z0-9_-]\{43\}$' "$OBS_ENV"   # must print 1
      grep -c . "$OBS_ENV"             # must print 1 (the one line; no other keys)
      ```

      The script handles exactly three states and prints which one it found.
      An **absent** file is minted. An **empty installer template** is
      replaced: `tools/install-apply-daemon.sh` writes a template of comment
      lines plus an empty `SONSTENG_PROD_OBSERVER_BEARER=` when the file is
      absent, and the script replaces it only if that is its sole assignment
      and the file is a regular, operator-owned, mode-`0600` file. A
      **populated** file (one line, a 43-character value) is reused, which is
      what a retry of step 1a finds. Anything else, including a symlink, the
      wrong owner or mode, or any other content, prints `STOP` and leaves the
      file untouched: do not overwrite it inside the window. Require
      `observer mint rc=0` and all three check lines. The Worker secret is
      always derived from the file, so the two cannot diverge.

      Derive the one-key secrets file that sub-step 6 passes to Wrangler. It
      goes in the mode-0700 `$PRIOR_DIR`, outside the `PRIOR_SHA` checkout:

      ```bash
      OBS_SECRETS="$PRIOR_DIR/observer-secret.env"
      ( umask 077; set -o noclobber
        python3 - "$OBS_ENV" > "$OBS_SECRETS" <<'PY'
      import sys
      key, value = open(sys.argv[1], encoding="utf-8").read().strip().split("=", 1)
      if key != "SONSTENG_PROD_OBSERVER_BEARER" or not value:
          sys.exit("observer env malformed")
      print("EDIT_TOKEN_OBSERVER=" + value)
      PY
      ) && echo "observer secrets file ready" || echo "STOP: observer secrets file"
      stat -c '%a %F' "$OBS_SECRETS"                 # must print: 600 regular file
      grep -c '^EDIT_TOKEN_OBSERVER=.' "$OBS_SECRETS"   # must print 1
      grep -c . "$OBS_SECRETS"                       # must print 1
      npx wrangler@4 versions upload --help | grep -c -- '--secrets-file'   # must print 1 or more
      ```

      **Why `versions upload --secrets-file`.** Wrangler 4's `versions upload`
      help (4.141.0) documents `--secrets-file` as a "Path to a file
      containing secrets to upload with the version (JSON or .env format).
      Applies additively with secrets from previous deployments - omitted
      secrets will not be deleted." The secret therefore lands only in the new
      version that sub-step 6 inspects and then deploys. Every existing
      `EDIT_TOKEN_*` and other secret carries forward. The live editor stays on
      the pre-1a version until `versions deploy`. The value reaches Wrangler
      only from a file, never from argv or a terminal.

      The alternatives are rejected. `wrangler secret put` deploys a new
      version immediately, so the live editor would move before the reviewed
      `versions view` check. `wrangler versions secret put` creates a separate
      version outside the reviewed upload-view-deploy sequence. If the last `grep` prints `0`, the
      installed Wrangler lacks the flag: STOP and remove `$OBS_SECRETS`.
   6. **DEV/editor Worker at `PRIOR_SHA`.** From `$PRIOR_DIR/tree/app/worker`:

      ```bash
      cd "$PRIOR_DIR/tree/app/worker"
      TARGET_SHA=$PRIOR_SHA
      ( trap 'rm -f "$OBS_SECRETS"' EXIT
        npx wrangler@4 versions upload --env="" --message "day-zero-prior:$TARGET_SHA" \
          --var "RELEASE_SHA:$TARGET_SHA" --secrets-file "$OBS_SECRETS" )
      echo "upload rc=$?"
      rm -f "$OBS_SECRETS"
      test ! -e "$OBS_SECRETS" && echo "observer secrets file removed" || echo "STOP: observer secrets file still present"
      STEP1A_DEV_VERSION_ID=<paste the full "Worker Version ID:" UUID printed above>
      echo "step-1a id len=${#STEP1A_DEV_VERSION_ID}"   # must print 36
      npx wrangler@4 versions view "$STEP1A_DEV_VERSION_ID" --env="" --json |
        check_version_view dev "$PRIOR_SHA" EDIT_TOKEN_OBSERVER $PRE1A_SECRET_NAMES
      echo "view rc=$?"
      npx wrangler@4 versions deploy "$STEP1A_DEV_VERSION_ID" --env="" --yes
      prove_provenance https://sonsteng-chat.damienriehl.workers.dev/edit/release-provenance 204 "$PRIOR_SHA"
      ```

      The upload runs in a subshell whose `EXIT` trap removes `$OBS_SECRETS`
      whether the upload succeeds, fails, or is interrupted; the next line
      removes it again and proves it is gone. `$PRE1A_SECRET_NAMES` is left
      unquoted on purpose so that each captured name becomes one argument
      (`version_secret_names` only emits `[A-Z0-9_]` names). Run
      `versions deploy` only after `check_version_view` prints `VIEW OK` and
      `view rc=0`: that proves the exact 40-character `RELEASE_SHA`, the exact
      DEV `EDIT_UPSTREAM`, `EDIT_ORIGIN`, and `PROD_RELEASE_LEDGER` values,
      and that the `secret names:` line includes `EDIT_TOKEN_OBSERVER` and
      every secret name the pre-1a version carried (such as
      `EDIT_TOKEN_RELEASE` and `EDIT_TOKEN_ADMIN`). A `STOP` there comes
      before `versions deploy`, so the live editor is still on the pre-1a
      version. The API returns secret names, never values. `prove_provenance`
      must print `OK` (`204`, one header, 40 characters, equal to
      `PRIOR_SHA`).

      Then run one read-only observer `GET` of the operation frontier. It
      prints only the HTTP status and two fields, never the token:

      ```bash
      python3 - "$OBS_ENV" <<'PY'
      import json, sys, urllib.error, urllib.request
      class NoRedirect(urllib.request.HTTPRedirectHandler):
          def redirect_request(self, *args):
              return None
      token = open(sys.argv[1], encoding="utf-8").read().strip().split("=", 1)[1]
      request = urllib.request.Request(
          "https://sonsteng-chat.damienriehl.workers.dev/edit/v1/prod/releases/frontier",
          headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                   "User-Agent": "sonsteng-queue-proof/1.0"})
      try:
          with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
              body = json.load(response)
              frontier = (body.get("context") or {}).get("operation_frontier") or {}
              print("status", response.status, "ok", body.get("ok"),
                    "blocked_state", frontier.get("blocked_state"))
      except urllib.error.HTTPError as error:
          print("status", error.code, "STOP: observer refused")
      except (urllib.error.URLError, OSError, ValueError):
          print("STOP: observer request failed")
      PY
      ```

      Redirects are disabled, as in the verifier, so the bearer is never
      forwarded to another host; a `3xx` prints its status and `STOP`. It must
      print `status 200 ok True` and a `blocked_state`. This is a
      fail-fast credential check, not the proof: step 2's receipt is the
      authoritative queue evidence. A `401` or `403` means the secret or slot
      did not land. Treat it as a step-1a failure.
   7. Record both DEV Worker coordinates in the window log: the **pre-1a DEV
      version ID** (from sub-step 2) and the **step-1a DEV version ID**. The
      step-1a version is the DEV state that compensation must reproduce.

   If any part of step 1a fails, reactivate the pre-1a version with
   `npx wrangler@4 versions deploy "$PRE1A_DEV_VERSION_ID" --env="" --yes`
   (from any `app/worker` directory),
   perform no migration step, and close the window. The pre-1a version has no
   `EDIT_TOKEN_OBSERVER` secret, because secrets set with `--secrets-file`
   attach only to the new version. Whether or not its vars list the
   `observer` slot, that slot then authenticates nothing: a slot without its
   secret is skipped, which a Worker test proves. The observer environment
   file may stay. It is read-only, and a retry reuses it.

   **Observer credential lifetime and compensation.** Later uploads inherit
   the observer secret: step 8's DEV upload, compensation's step-3(b) DEV
   upload, and the apply daemon's DEV `wrangler deploy` after the window.
   Wrangler applies secrets additively, and deployments never delete them. It
   is a standing read-only principal, and `docs/prod-release-operations.md`
   uses the same principal for its readiness check. Compensation does not
   remove it. Revoking it is a separate, out-of-window credential change:
   delete the `EDIT_TOKEN_OBSERVER` secret, or remove the slot from
   `EDIT_TOKEN_SCOPES`.

   **Before step 2: observer credential confirmation.** The opening proof
   cannot pass without a working observer credential. Step 1a sub-steps 5 and
   6 mint and install it. Confirm all three before running step 2:

   - **The file.** `~/.config/sonsteng-release-observer/env` must exist,
     owned by the operator account, as a regular non-symlink file of mode
     `0600` and at most 65,536 bytes, with exactly one non-empty
     `SONSTENG_PROD_OBSERVER_BEARER=` line. Check without printing the value:
     `stat -c '%a %U %F' ~/.config/sonsteng-release-observer/env` must show
     `600`, the operator, and `regular file`, and
     `grep -c '^SONSTENG_PROD_OBSERVER_BEARER=.' ~/.config/sonsteng-release-observer/env`
     must print `1`.
   - **What happens without it.** Absence is not a fallback path to success.
     If the file does not exist, the verifier still performs the admin review
     `GET`, skips the observer-frontier `GET`, and writes a receipt with
     `"publication":"observer-env-absent"`, a diagnostic-only
     `publication_fallback` naming the production timer state,
     `"proof_error":"environment-unavailable"`, and
     `"all_queues_empty":false`, and it exits nonzero. The complete-JSON
     validator also rejects it, because it requires
     `"publication":"observer-frontier"` and `"publication_fallback":null`. A
     file that exists but has the wrong owner, mode, or type, or an empty
     value, fails with `environment-unavailable` without that fallback
     receipt field.
   - **The Worker grant.** The bearer must equal the `sonsteng-chat` secret
     `EDIT_TOKEN_OBSERVER`, and the committed `observer` slot in
     `EDIT_TOKEN_SCOPES` must grant only `{"release_observer":1}`
     (`docs/prod-release-operations.md`). Step 1a sub-step 1 proves that
     `PRIOR_SHA` carries the slot. Sub-step 6's `check_version_view`
     `secret names:` line and observer `GET` prove the deployed version carries the secret.
     If either is missing, the frontier `GET` is refused with `401` or `403`,
     and the proof fails with `http-status-invalid`.

2. at window open, independently prove all three queues empty with the one
   text-free, read-only receipt. The reviewed queue-proof release identity is
   the exact release commit plus the Git blob object ID at
   `tools/prove_queues_empty.py`. After review, the reviewer records those two
   values with these absolute commands; the operator copies the recorded
   values into the two quoted placeholders below rather than deriving a new
   expected value from the working verifier during the production window. The
   expected blob therefore comes from the review record, independently of the
   bytes the verifier will measure:

   ```bash
   builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C /home/damienriehl/.local/share/sonsteng-daemon/checkout \
     rev-parse --verify 'HEAD^{commit}'
   (
     builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C /home/damienriehl/.local/share/sonsteng-daemon/checkout \
       rev-parse '<reviewed-release-commit-SHA>:tools/prove_queues_empty.py'
   )
   ```

   Before opening, the supervisor also records one unpredictable 64-character
   lowercase-hex window nonce outside the checkout in an operator-owned,
   regular, mode-`0600` file whose sole assignment is named
   `QUEUE_PROOF_WINDOW_NONCE`. Rehearsals use a newly provisioned file containing
   a different nonce. The nonce itself never appears in a command argument,
   receipt, checked-in fixture, documentation example, or transcript; receipts carry only its domain-separated
   SHA-256, which the supervisor compares with the separately recorded digest.
   The checkout, Git, env, Python, verifier, nonce-file, and receipt paths are
   absolute.

   The following machine-readable block is the normative contract for every
   executable operational guarantee used by this procedure. Each named test
   measures its mechanism, compares every field with that observation, and
   requires the statement to encode those same observed propositions.
   Surrounding prose is explanatory only and does not widen these guarantees.

   <!-- queue-proof-controlled-claims:start -->
   <!-- queue-proof-executable-claims:start -->
   ```json
   {
     "schema": "queue-proof-executable-claims/v1",
     "claims": {
       "apply_timer_fence": {
         "statement": "Apply-timer fence measurements: `required_active=false`; `active_state_refused=true`; `inactive_state_accepted=true`.",
         "required_active": false,
         "active_state_refused": true,
         "inactive_state_accepted": true
       },
       "bootstrap_signal_mask": {
         "statement": "Receipt bootstrap measured `blocked_signals=[\"SIGINT\",\"SIGTERM\"]`, `mask_installed_before_receipt_reservation=true`, and `mask_restored_after_reservation_attempt=true`.",
         "blocked_signals": ["SIGINT", "SIGTERM"],
         "mask_installed_before_receipt_reservation": true,
         "mask_restored_after_reservation_attempt": true
       },
       "clock_skew_bound": {
         "statement": "Server-date validation measured `max_abs_skew_seconds=300`, `boundary_accepted=true`, and `one_second_outside_refused=true`.",
         "max_abs_skew_seconds": 300,
         "boundary_accepted": true,
         "one_second_outside_refused": true
       },
       "dev_full": {
         "statement": "A /dev/full mirror measured `returncode=1`, `durable_receipt=true`, and `diagnostic=queue proof stdout mirror failed; receipt is at the required path`.",
         "returncode": 1,
         "durable_receipt": true,
         "diagnostic": "queue proof stdout mirror failed; receipt is at the required path"
       },
       "environment_file": {
         "statement": "Protected environment parsing measured `max_bytes=65536`, `oversized_refused=true`, `duplicate_required_key_refused=true`, and `empty_required_value_refused=true`.",
         "max_bytes": 65536,
         "oversized_refused": true,
         "duplicate_required_key_refused": true,
         "empty_required_value_refused": true
       },
       "epipe": {
         "statement": "An EPIPE mirror measured `returncode=1`, `durable_receipt=true`, and `diagnostic=queue proof stdout mirror failed; receipt is at the required path`.",
         "returncode": 1,
         "durable_receipt": true,
         "diagnostic": "queue proof stdout mirror failed; receipt is at the required path"
       },
       "evidence_and_mirror": {
         "statement": "Evidence and mirror measurements: `stdout_role=convenience_mirror`; `authoritative_evidence=[\"durable_receipt\",\"supervised_returncode\"]`; `mirror_failure_returncode=1`; `durable_receipt_preserved_on_mirror_failure=true`.",
         "stdout_role": "convenience_mirror",
         "authoritative_evidence": ["durable_receipt", "supervised_returncode"],
         "mirror_failure_returncode": 1,
         "durable_receipt_preserved_on_mirror_failure": true
       },
       "frontier_envelope": {
         "statement": "The frontier response measured `exact_keys=[\"context\",\"ok\"]` and `unexpected_key_refused=true`.",
         "exact_keys": ["context", "ok"],
         "unexpected_key_refused": true
       },
       "host_identity_input": {
         "statement": "Host-identity input measured `max_bytes=256` and `oversized_refused=true` even under a permissive value pattern.",
         "max_bytes": 256,
         "oversized_refused": true
       },
       "http_redirects": {
         "statement": "The production HTTP handler measured `redirect_followed=false`.",
         "redirect_followed": false
       },
       "loader_boundary": {
         "statement": "The loader experiment measured `preload_constructor_ran_with_exec_c=false`, `preload_constructor_ran_without_exec_c=true`, and `verifier_detected_missing_exec_c=false`.",
         "preload_constructor_ran_with_exec_c": false,
         "preload_constructor_ran_without_exec_c": true,
         "verifier_detected_missing_exec_c": false
       },
       "launcher_closed_stdout": {
         "statement": "Shell-level launcher closed-stdout measurements: `returncode=68`; `receipt_path_reserved=false`.",
         "returncode": 68,
         "receipt_path_reserved": false
       },
       "launcher_script_route": {
         "statement": "The documented launcher measured `script_path=/proc/self/fd/9` and `path_swap_executes_open_inode=true`.",
         "script_path": "/proc/self/fd/9",
         "path_swap_executes_open_inode": true
       },
       "nonce_format": {
         "statement": "The protected window nonce measured `exact_length=64`, `lowercase_only=true`, `short_refused=true`, and `uppercase_refused=true`.",
         "exact_length": 64,
         "lowercase_only": true,
         "short_refused": true,
         "uppercase_refused": true
       },
       "verifier_closed_stdout": {
         "statement": "Verifier closed-stdout invocation measurements: `bare_interpreter_returncode=1`; `bare_interpreter_durable_receipt=true`; `bare_interpreter_diagnostic=queue proof stdout mirror was unavailable; receipt is at the required path`; `bare_interpreter_stdout_target=closed`; `bare_interpreter_guard_reached=true`; `documented_chain_returncode=0`; `documented_chain_durable_receipt=true`; `documented_chain_diagnostic=`; `documented_chain_stdout_target=/dev/null`; `documented_chain_env_implementation=uutils coreutils 0.8.0`; `documented_chain_env_reopens_stdout_to_devnull=true`; `documented_chain_guard_reached=false`; `in_verifier_guard_retained=true`.",
         "bare_interpreter_returncode": 1,
         "bare_interpreter_durable_receipt": true,
         "bare_interpreter_diagnostic": "queue proof stdout mirror was unavailable; receipt is at the required path",
         "bare_interpreter_stdout_target": "closed",
         "bare_interpreter_guard_reached": true,
         "documented_chain_returncode": 0,
         "documented_chain_durable_receipt": true,
         "documented_chain_diagnostic": "",
         "documented_chain_stdout_target": "/dev/null",
         "documented_chain_env_implementation": "uutils coreutils 0.8.0",
         "documented_chain_env_reopens_stdout_to_devnull": true,
         "documented_chain_guard_reached": false,
         "in_verifier_guard_retained": true
       },
       "sigint": {
         "statement": "The SIGINT experiment measured `returncode=130`, `bounded_receipt=true`, `all_queues_empty=false`, `proof_error=verifier-interrupted`, `proof_state_preserved=true`, and `receipt_path_reusable=true`.",
         "returncode": 130,
         "bounded_receipt": true,
         "all_queues_empty": false,
         "proof_error": "verifier-interrupted",
         "proof_state_preserved": true,
         "receipt_path_reusable": true
       },
       "sigterm": {
         "statement": "The SIGTERM experiment measured `returncode=143`, `bounded_receipt=true`, `all_queues_empty=false`, `proof_error=verifier-interrupted`, `proof_state_preserved=true`, and `receipt_path_reusable=true`.",
         "returncode": 143,
         "bounded_receipt": true,
         "all_queues_empty": false,
         "proof_error": "verifier-interrupted",
         "proof_state_preserved": true,
         "receipt_path_reusable": true
       },
       "post_finalization_signal": {
         "statement": "The post-finalization signal experiment measured `returncode=0`, `durable_receipt=true`, and `receipt_matches_stdout=true`.",
         "returncode": 0,
         "durable_receipt": true,
         "receipt_matches_stdout": true
       },
       "receipt_parent": {
         "statement": "Receipt creation measured `symlink_parent_followed=false`.",
         "symlink_parent_followed": false
       },
       "sha1_attribution": {
         "statement": "SHA-1 mechanism measurements: `self_hash_algorithm=sha1`; `launcher_git_hashes_open_descriptor=true`; `deployment_git_capability=SHA-1: SHA1_DC`; `host_capability_present=true`.",
         "self_hash_algorithm": "sha1",
         "launcher_git_hashes_open_descriptor": true,
         "deployment_git_capability": "SHA-1: SHA1_DC",
         "host_capability_present": true
       },
       "standard_stream_reservation": {
         "statement": "With fd 1 and fd 2 closed, bootstrap measured `closed_descriptors_reserved_to=/dev/null`, `diagnostic_appended_to_receipt=false`, and `failed_write_receipt_remains_single_json=true`.",
         "closed_descriptors_reserved_to": "/dev/null",
         "diagnostic_appended_to_receipt": false,
         "failed_write_receipt_remains_single_json": true
       }
     }
   }
   ```
   <!-- queue-proof-executable-claims:end -->
   <!-- queue-proof-controlled-claims:end -->

   Claim `loader_boundary` is the complete operational guarantee for dynamic
   loader isolation; do not infer a wider loader guarantee from explanatory
   prose. The nested `/usr/bin/env -i` removes the launcher's non-secret scrub
   canary and gives Python the exact sole allowlisted entry `LC_ALL=C`; its
   removal is a tested verifier failure (`environment-hostile`).
   Invoking the verifier outside this documented readonly launcher is unsupported,
   and no receipt from such an invocation is acceptable. Python isolated mode
   ignores user-site and Python environment path injection. The Bash builtin
   command boundary prevents PATH entries or slash-named shell functions from
   intercepting Git or Python, and a pre-existing readonly launcher makes setup
   exit `69`. Every
   invocation independently requires the named checkout to be
   at the reviewed commit, requires that commit to contain the reviewed blob,
   and hashes the working verifier bytes to reject a dirty or substituted copy.
   The launcher opens the working verifier once on file descriptor 9, feeds
   that descriptor to `git hash-object --stdin`, and invokes Python through
   that same descriptor. Python then reads its own `/proc/self/fd/9` source path, hashes those bytes as
   a Git blob (`SHA-1("blob " + ASCII byte length + NUL + file bytes)`), and
   compares that measurement with the independently recorded expected blob.
   Claim `sha1_attribution` is limited to the measurable digest mechanism,
   open-descriptor Git gate, and host capability. The host capability is
   deployment-specific, not a portable property of Git or Python; a mismatch
   with that measured claim is a stop.
   The shell rejects a mismatch before Python starts; Python rejects a mismatch
   or unreadable self path with a bounded receipt.

   Precisely stated, the self-hash proves that the file reachable at `__file__`
   had the reviewed blob at run time. Through `/proc/self/fd/9` that measurement
   is inode-bound, so a dirty or substituted copy is refused even if the shell
   gates were skipped. It does **not** prove that the executing bytes equal the
   hashed bytes: the measurement is a later re-read. It proves nothing when the
   invoked program is not this program, does not cover interpreter caches, and
   echoes `release_commit` without verifying it. The real binding is the
   runbook's Git gates plus the supervised transcript. The
   `/usr/bin/python3 -I -B --check-hash-based-pycs always /proc/self/fd/9` script
   route is therefore load-bearing: it executes the opened source as `__main__`
   without consulting `__pycache__`. A `-m` invocation, import, wrapper, or
   ordinary path substitution is unsupported. The in-tool `__cached__ is None`
   check is another post-start guard, not proof about code that could already
   have executed from a cache. Keep the function readonly for the whole window:

   <!-- queue-proof-launcher:start -->
   ```bash
   builtin readonly QUEUE_PROOF_CHECKOUT=/home/damienriehl/.local/share/sonsteng-daemon/checkout || exit 68
   builtin readonly QUEUE_PROOF_VERIFIER="$QUEUE_PROOF_CHECKOUT/tools/prove_queues_empty.py" || exit 68
   builtin readonly QUEUE_PROOF_RELEASE_COMMIT='<reviewed-release-commit-SHA>' || exit 68
   builtin readonly QUEUE_PROOF_VERIFIER_BLOB='<reviewed-verifier-Git-blob-OID>' || exit 68
   builtin readonly QUEUE_PROOF_NONCE_FILE='<absolute-mode-0600-window-nonce-file>' || exit 68
   builtin unset -f run_queue_proof 2>/dev/null || exit 69

   run_queue_proof() {
     builtin local observed_checkout observed_commit committed_blob working_blob window_phase receipt_path
     case "$#" in
       2) ;;
       *) return 68 ;;
     esac
     window_phase=$1
     receipt_path=$2
     case "$window_phase" in
       opening|closing) ;;
       *) return 68 ;;
     esac
     case "$receipt_path" in
       /*) ;;
       *) return 68 ;;
     esac
     builtin test -e /proc/self/fd/1 || return 68
     observed_checkout=$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C "$QUEUE_PROOF_CHECKOUT" rev-parse --show-toplevel) || return 70
     case "$observed_checkout" in
       "$QUEUE_PROOF_CHECKOUT") ;;
       *) return 71 ;;
     esac
     observed_commit=$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C "$QUEUE_PROOF_CHECKOUT" rev-parse --verify 'HEAD^{commit}') || return 70
     case "$observed_commit" in
       "$QUEUE_PROOF_RELEASE_COMMIT") ;;
       *) return 72 ;;
     esac
     committed_blob=$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C "$QUEUE_PROOF_CHECKOUT" rev-parse "${QUEUE_PROOF_RELEASE_COMMIT}:tools/prove_queues_empty.py") || return 70
     case "$committed_blob" in
       "$QUEUE_PROOF_VERIFIER_BLOB") ;;
       *) return 73 ;;
     esac
     (
       working_blob=$(builtin exec -c /usr/bin/env -i LC_ALL=C /usr/bin/git -C "$QUEUE_PROOF_CHECKOUT" hash-object --stdin <&9) || return 70
       case "$working_blob" in
         "$QUEUE_PROOF_VERIFIER_BLOB") ;;
         *) return 74 ;;
       esac
       builtin exec -c /usr/bin/env QUEUE_PROOF_ENV_SCRUB_REQUIRED=1 \
         /usr/bin/env -i LC_ALL=C \
         /usr/bin/python3 -I -B --check-hash-based-pycs always /proc/self/fd/9 \
         --release-commit "$QUEUE_PROOF_RELEASE_COMMIT" \
         --verifier-blob "$QUEUE_PROOF_VERIFIER_BLOB" \
         --ledger-origin https://sonsteng-chat.damienriehl.workers.dev \
         --apply-env-file /home/damienriehl/.config/sonsteng-apply/env \
         --observer-env-file /home/damienriehl/.config/sonsteng-release-observer/env \
         --apply-timer-stopped \
         --window-owner '<opaque-Packet-D-window-id>' \
         --window-nonce-file "$QUEUE_PROOF_NONCE_FILE" \
         --window-phase "$window_phase" \
         --receipt-path "$receipt_path"
     ) 9<"$QUEUE_PROOF_VERIFIER"
   }
   builtin readonly -f run_queue_proof

   run_queue_proof opening '<absolute-opening-receipt-path>.json'
   opening_queue_proof_rc=$?
   ```
   <!-- queue-proof-launcher:end -->

   Validate the opening receipt as complete JSON with this fail-closed check;
   a substring search is not acceptance:

   <!-- queue-proof-receipt-validator:start -->
   ```bash
   validate_queue_proof_receipt() {
     /usr/bin/env -i LC_ALL=C /usr/bin/python3 -I - "$1" <<'PY'
   import json
   import sys

   def exact_dict(value, keys):
       return isinstance(value, dict) and set(value) == set(keys)

   def integer(value):
       return isinstance(value, int) and not isinstance(value, bool)

   def digest(value, length=64):
       return (
           isinstance(value, str)
           and len(value) == length
           and all(character in "0123456789abcdef" for character in value)
       )

   required = {
       "all_queues_empty", "apply", "editor_review", "fence",
       "first_get_utc", "host_identity", "last_get_utc", "ledger_host",
       "ledger_state_hash", "preflight", "publication",
       "publication_fallback", "publication_frontier",
       "server_date_skew_seconds", "server_dates", "timer",
       "verifier_identity",
   }
   try:
       with open(sys.argv[1], encoding="utf-8") as source:
           receipt = json.load(source)
   except (OSError, UnicodeError, json.JSONDecodeError):
       raise SystemExit(75)
   valid = exact_dict(receipt, required)
   if valid:
       apply = receipt["apply"]
       review = receipt["editor_review"]
       fence = receipt["fence"]
       frontier = receipt["publication_frontier"]
       host = receipt["host_identity"]
       hashes = receipt["ledger_state_hash"]
       server_dates = receipt["server_dates"]
       skews = receipt["server_date_skew_seconds"]
       timer = receipt["timer"]
       identity = receipt["verifier_identity"]
       valid = all((
           receipt["all_queues_empty"] is True,
           exact_dict(apply, {"accepted"}) and apply["accepted"] == 0,
           exact_dict(review, {"accepted", "other_non_terminal", "pending"})
               and all(value == 0 for value in review.values()),
           exact_dict(fence, {
               "apply_timer", "apply_timer_stopped", "proved",
               "window_nonce_sha256", "window_owner", "window_phase",
           }),
           exact_dict(frontier, {
               "operation_frontier", "queue_count", "reason", "releases",
           }),
           exact_dict(host, {"boot_id_sha256", "machine_id_sha256"})
               and all(digest(value) for value in host.values()),
           exact_dict(hashes, {
               "algorithm", "combined", "publication_frontier", "review",
           }) and hashes.get("algorithm") == "sha256"
               and all(digest(hashes[key]) for key in (
                   "combined", "publication_frontier", "review",
               )),
           exact_dict(server_dates, {"publication_frontier", "review"})
               and all(isinstance(value, str) and value for value in server_dates.values()),
           exact_dict(skews, {"publication_frontier", "review"})
               and all(integer(value) for value in skews.values()),
           exact_dict(timer, {"active", "available", "enabled"})
               and timer == {"active": False, "available": True, "enabled": False},
           exact_dict(identity, {"release_commit", "verifier_blob"})
               and digest(identity["release_commit"], 40)
               and digest(identity["verifier_blob"], 40),
       ))
   if valid:
       apply_timer = fence["apply_timer"]
       operation = frontier["operation_frontier"]
       valid = all((
           exact_dict(apply_timer, {"active", "available", "enabled"})
               and apply_timer == {"active": False, "available": True, "enabled": True},
           fence["apply_timer_stopped"] is True,
           fence["proved"] is True,
           digest(fence["window_nonce_sha256"]),
           isinstance(fence["window_owner"], str) and bool(fence["window_owner"]),
           fence["window_phase"] in {"opening", "closing"},
           exact_dict(operation, {"blocked_state", "pending_operation_count"})
               and operation == {"blocked_state": "unblocked", "pending_operation_count": 0},
           frontier["queue_count"] == 0,
           isinstance(frontier["reason"], str) and bool(frontier["reason"]),
           frontier["releases"] == [],
           receipt["publication"] == "observer-frontier",
           receipt["publication_fallback"] is None,
           receipt["preflight"] is None,
           isinstance(receipt["first_get_utc"], str) and bool(receipt["first_get_utc"]),
           isinstance(receipt["last_get_utc"], str) and bool(receipt["last_get_utc"]),
           receipt["ledger_host"] == "sonsteng-chat.damienriehl.workers.dev",
       ))
   raise SystemExit(
       0 if valid else 75
   )
   PY
   }

   validate_queue_proof_receipt '<absolute-opening-receipt-path>.json'
   opening_receipt_validation_rc=$?
   ```
   <!-- queue-proof-receipt-validator:end -->

   Require `opening_receipt_validation_rc` and `opening_queue_proof_rc` to both
   be exactly `0`. Require the measured `verifier_identity` values to equal
   the two reviewed constants, `fence.window_phase:"opening"`, and its
   `fence.window_nonce_sha256` to equal the digest recorded before the window.
   Receipt contents alone are insufficient. Acceptance also requires the
   supervised shell transcript to show this exact readonly `run_queue_proof`
   function was invoked with the opening phase and required receipt path and to
   record its return code. A bare hand invocation is unsupported and is never
   accepted, regardless of its receipt contents. The verifier command executed by the
   function is:

   ```bash
   (
     builtin exec -c /usr/bin/env QUEUE_PROOF_ENV_SCRUB_REQUIRED=1 \
       /usr/bin/env -i LC_ALL=C \
       /usr/bin/python3 -I -B --check-hash-based-pycs always /proc/self/fd/9 \
       --release-commit "$QUEUE_PROOF_RELEASE_COMMIT" \
       --verifier-blob "$QUEUE_PROOF_VERIFIER_BLOB" \
       --ledger-origin https://sonsteng-chat.damienriehl.workers.dev \
       --apply-env-file /home/damienriehl/.config/sonsteng-apply/env \
       --observer-env-file /home/damienriehl/.config/sonsteng-release-observer/env \
       --apply-timer-stopped \
       --window-owner '<opaque-Packet-D-window-id>' \
       --window-nonce-file "$QUEUE_PROOF_NONCE_FILE" \
       --window-phase "$window_phase" \
       --receipt-path "$receipt_path"
   ) 9<"$QUEUE_PROOF_VERIFIER"
   ```

   The tool performs only the
   daemon's admin review GET and the observer's readiness-frontier GET, emits
   counts rather than authored rows or IDs, and names only the ledger host. It
   checks `sonsteng-apply.timer` against controlled claim `apply_timer_fence`,
   records its recognized enabled/disabled state, records the named window,
   phase, and nonce digest,
   the local UTC times and server-authenticated HTTP `Date` values of both GETs,
   and the signed skew in seconds (`server Date - local clock`). The exclusive
   quantitative acceptance bound is controlled claim `clock_skew_bound`; the
   frontier server date must not precede the review server date. It also records SHA-256s of
   the canonical JSON form of both validated response bodies plus a
   domain-separated combined ledger-state hash, and SHA-256s of this host's
   machine ID and boot ID. It
   fails with `"fence":"unproven"` when any fence assertion is absent. The
   receipt is valid only inside the exact window named by `window_owner`; use
   the same opaque ID and protected nonce file for the opening and closing proof.

   The receipt path is required, absolute, create-new, and mode `0600`. The
   verifier retries partial writes, syncs the receipt file, verifies the opened
   inode still names that regular file, and syncs the parent directory before
   mirroring the same bytes to stdout. Failure to open, write, flush, or sync the
   durable receipt returns nonzero with a bounded diagnostic. Claims
   `dev_full`, `epipe`, `verifier_closed_stdout`, and
   `launcher_closed_stdout` are the complete operational guarantees for those
   stdout conditions, including their return codes, receipt disposition, and
   diagnostics. Claim `evidence_and_mirror` exclusively assigns the evidence
   and mirror roles. Preserve any existing or partial
   receipt and use a new path for a supervised retry.
   Claims `sigint`, `sigterm`, and `post_finalization_signal` are the complete
   operational guarantees for those signal timings, including return code,
   receipt verdict and disposition, preserved proof state, and retryability.
   Receipt finalization is the linearization point used by those claims. If
   reservation release reports failure, stop, preserve the path for
   inspection, and select a fresh absolute create-new path for any retry.

   The verifier deliberately uses the host's full distribution-managed CA
   store rather than a private issuer pin. The allowlisted origin is a
   Cloudflare edge whose served issuer chain may rotate independently of this
   migration window; narrowing today to one observed root could turn routine
   edge certificate rotation into an in-window outage. The compensating check
   is explicit: preflight records the bundle path, SHA-256, and loaded root
   count, and the operator compares them with the approved production-host
   baseline and confirms no unreviewed local CA/update changed that baseline.
   The name `SYSTEM_CA_BUNDLE` is intentional; this is not represented as a
   one-origin trust set. Production permits TLS 1.2 or TLS 1.3 (minimum TLS 1.2,
   no TLS-1.2 maximum pin); the preflight handshake records which protocol and
   cipher the edge actually negotiated before the window.

   Publication emptiness additionally requires the observer context to expose
   exactly `operation_frontier:{pending_operation_count,blocked_state}`, with a
   zero count and `blocked_state:"unblocked"`. The observer frontier endpoint
   exposes that field from the observer-frontier merge (PR #63) onward. A
   response without `operation_frontier`, for example from an older deployed
   Worker, fails closed with `operation-frontier-missing`. The ledger Worker
   behind the allowlisted origin `sonsteng-chat.damienriehl.workers.dev` (the
   `sonsteng-chat` script: its top-level and `env.dev` configurations set
   `PROD_RELEASE_LEDGER` to `"true"`, while `env.production` sets `"false"`)
   must therefore be deployed at or after that merge before the opening proof
   can pass. If the observer environment file does not
   exist, the receipt reports `"publication":"observer-env-absent"` and fails
   closed with `environment-unavailable`; production-timer state is retained
   only as diagnostic metadata and cannot substitute for the observer proof.
   Step 1a and the observer credential precondition above are what make this
   proof passable. Never put environment-file values on the command line;
3. capture and verify the exact prior pair and both live SHA headers with the
   stable inspector's `--print-recovery-ids` form. Record its `sha` as
   `PRIOR_PAIR_SHA`, plus the full `pages_deployment_id` and
   `worker_version_id` as `PRIOR_PAGES_DEPLOYMENT_ID` and
   `PRIOR_WORKER_VERSION_ID`, each length-checked. Step 1a left the shell in
   `$PRIOR_DIR/tree/app/worker`, so change to the reviewed operations checkout
   first:

   ```bash
   cd "$OPS_REPO"
   recovery_ids() {
     credential-helper-that-prints-only-the-Cloudflare-bearer |
     SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true python3 "$OPS_REPO/tools/day_zero_migration.py" \
       --inspect-cloudflare-pair --print-recovery-ids \
       --cloudflare-account-id "$CF_ACCOUNT_ID" --pages-project sonsteng \
       --worker-script sonsteng-chat-production \
       --pages-provenance-url https://legalpracticum.org/ \
       --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance \
       --ack-john-notified --ack-queue-empty
   }
   recovery_field() {
     printf '%s' "$RECOVERY_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
   }
   RECOVERY_JSON=$(recovery_ids); echo "inspector rc=$? $RECOVERY_JSON"
   PRIOR_PAIR_SHA=$(recovery_field sha)
   PRIOR_PAGES_DEPLOYMENT_ID=$(recovery_field pages_deployment_id)
   PRIOR_WORKER_VERSION_ID=$(recovery_field worker_version_id)
   echo "lens sha=${#PRIOR_PAIR_SHA} pages=${#PRIOR_PAGES_DEPLOYMENT_ID} worker=${#PRIOR_WORKER_VERSION_ID}"
   git -C "$DAEMON_REPO" merge-base --is-ancestor "$PRIOR_PAIR_SHA" "$PRIOR_SHA" && echo "pair ancestry OK" || echo "STOP: production runs a commit main does not contain"
   ```

   `recovery_ids` and `recovery_field` stay defined in the window shell;
   step 7, step 9, and compensation step 1 reuse them. Require
   `inspector rc=0`, `sha=40`, `worker=36`, `pages=36` (the canonical
   Pages deployment ID is a 36-character UUID, as in every recorded pair; the
   8-character preview subdomain is never a recovery coordinate, OQ-9), and
   `pair ancestry OK`. When
   `PRIOR_PAIR_SHA` differs from `PRIOR_SHA`, which is the expected case on
   2026-09-25, keep both. See
   [Two prior SHAs](#two-prior-shas-prior_pair_sha-and-prior_sha);
4. rehearse, then materialize and commit the combined rewrite plus generated
   artifacts exactly once in a controlled worktree created at `PRIOR_SHA`
   (OQ-10, including its explicit `git add`). Create the controlled worktree
   as a detached linked worktree of the daemon repository, so that the CAS
   `forward` finds the candidate commit object in `$DAEMON_REPO`; a separate
   clone fails the CAS dry run. Choose a new absolute path outside every
   existing checkout:

   ```bash
   CONTROLLED_REPO=/absolute/path/for/the/new/controlled-worktree
   test "${CONTROLLED_REPO#/}" != "$CONTROLLED_REPO" && test ! -e "$CONTROLLED_REPO" && echo "path OK" || echo "STOP: path relative or exists"
   git -C "$DAEMON_REPO" worktree add --detach "$CONTROLLED_REPO" "$PRIOR_SHA"
   test "$(git -C "$CONTROLLED_REPO" rev-parse HEAD)" = "$PRIOR_SHA" && echo "controlled worktree at PRIOR_SHA" || echo "STOP: controlled worktree HEAD"
   cd "$CONTROLLED_REPO"
   ```

   `--detach` leaves `main` checked out only in the daemon checkout, which
   `forward` requires. Before moving canonical `main`,
   run the Phase 2 verification from item 5 against the committed candidate;
   it runs in its own exact-SHA clone and does not need `main`, so a failure
   here needs no canonical restore. Only after it passes, advance canonical
   `main` by running the exact one-commit compare-and-swap below
   (`--from "$PRIOR_SHA" --to "$CANDIDATE_SHA"`);
5. verify the exact committed tree with the write-free phase list. There is no
   CLI flag for this, so call the function directly from the controlled
   worktree (any trusted checkout that contains the candidate commit):

   ```bash
   python3 - "$CANDIDATE_SHA" <<'PY'
   import json, pathlib, sys
   sys.path.insert(0, "tools")
   import day_zero_migration as m
   try:
       with m.signal_unwind_guard():
           receipt = m.verify_materialized(pathlib.Path("."), sys.argv[1])
   except m.MigrationError as exc:
       print(f"error: {exc}", file=sys.stderr)
       sys.exit(1)
   print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
   PY
   echo "phase2 rc=$?"
   ```

   Require `phase2 rc=0` and a receipt whose `mode` is `"verify-only"`,
   whose `candidate_sha` equals `CANDIDATE_SHA`, whose `phases` list is
   exactly the Phase 2 list, and whose `production_mutations` is `0`. Keep the
   JSON in the window evidence directory. Run it before the forward CAS (item
   4). Its `preflight` phase reruns `tools/preflight.sh`, which reruns
   `build_site.py --check` and so rewrites the stamp's traceability-only
   `git_base_sha` to the candidate; `verify_materialized` compares that stamp
   without `git_base_sha`, restores the committed bytes, and requires the
   exact clean tree before `final-tree-cleanliness`. Any other stamp or tracked
   change still fails as `verify-only phase failed: preflight`. After the
   forward CAS, confirm the CAS receipt's three readbacks equal
   `CANDIDATE_SHA`; the tree is unchanged, so a second Phase 2 run is
   optional;
6. regenerate and verify the ignored Worker inputs (OQ-10), record the
   new-Worker-serves-old-Pages proof (OQ-8), then upload and activate the named
   production Worker version, prove its provenance, and only then deploy the
   Pages artifact. From the controlled worktree at `CANDIDATE_SHA`, after the
   OQ-10 regeneration and stamp restore, with `git status --porcelain` empty,
   under the Cloudflare PROD principal:

   ```bash
   cd "$CONTROLLED_REPO/app/worker"
   npx wrangler@4 versions upload --env production --message "day-zero:$CANDIDATE_SHA" --var "RELEASE_SHA:$CANDIDATE_SHA"
   echo "upload rc=$?"
   NEW_WORKER_VERSION_ID=<paste the full "Worker Version ID:" UUID printed above>
   echo "new worker id len=${#NEW_WORKER_VERSION_ID}"   # must print 36
   npx wrangler@4 versions view "$NEW_WORKER_VERSION_ID" --env production --json | check_version_view production "$CANDIDATE_SHA"
   echo "view rc=$?"
   npx wrangler@4 versions deploy "$NEW_WORKER_VERSION_ID" --env production --yes
   prove_provenance https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance 204 "$CANDIDATE_SHA"
   prove_provenance https://legalpracticum.org/ 200 "$PRIOR_PAIR_SHA"
   cd "$CONTROLLED_REPO"
   ```

   `check_version_view` must print `VIEW OK` and `view rc=0` before
   `versions deploy`: the exact 40-character `RELEASE_SHA` equal to the
   candidate and the exact production `EDIT_UPSTREAM`
   (`https://legalpracticum.org/platform/`), `EDIT_ORIGIN`, and
   `PROD_RELEASE_LEDGER` (`false`). Any `STOP` is a stop before activation,
   while production still serves the prior pair. The only expected upload warning concerns top-level vars that
   are absent from `env.production`. Those two `prove_provenance` lines are
   OQ-8 check 3. Only after both print `OK`, stage and deploy Pages from the
   repository root:

   ```bash
   PAGES_STAGE=$(mktemp -d)
   cp -a site "$PAGES_STAGE/site"
   test ! -e "$PAGES_STAGE/site/_headers" || { echo "STOP: unexpected site/_headers"; false; }
   printf '/*\n  X-Release-SHA: %s\n' "$CANDIDATE_SHA" > "$PAGES_STAGE/site/_headers"
   cat "$PAGES_STAGE/site/_headers"
   npx wrangler@4 pages deploy "$PAGES_STAGE/site" --project-name sonsteng --branch main \
     --commit-hash "$CANDIDATE_SHA" --commit-message "day-zero:$CANDIDATE_SHA"
   prove_provenance https://legalpracticum.org/ 200 "$CANDIDATE_SHA"
   ```

   This is the executor's `WranglerPagesAdapter.deploy` staging: a copy of
   `site/` plus a deployment-only `_headers` file. The repository has no
   `site/_headers` of its own at this revision; if one appears, stop and
   append rather than overwrite. The deployment URL's subdomain that Wrangler
   prints is **not** the recovery coordinate (OQ-9). Delete `$PAGES_STAGE`
   afterwards;
7. read back and atomically record the exact new provider pair, using the
   inspector's full canonical Pages deployment ID (OQ-9):

   ```bash
   RECOVERY_JSON=$(recovery_ids); echo "inspector rc=$? $RECOVERY_JSON"
   test "$(recovery_field sha)" = "$CANDIDATE_SHA" && echo "new pair sha OK" || echo "STOP: new pair sha"
   test "$(recovery_field worker_version_id)" = "$NEW_WORKER_VERSION_ID" && echo "new worker id OK" || echo "STOP: new worker id"
   NEW_PAGES_DEPLOYMENT_ID=$(recovery_field pages_deployment_id)
   test "${#NEW_PAGES_DEPLOYMENT_ID}" -eq 36 && echo "new pages id len OK" || echo "STOP: new pages id is not a 36-character canonical id"
   ```

   Record `NEW_PAGES_DEPLOYMENT_ID` and `NEW_WORKER_VERSION_ID` as the new
   pair;
8. deploy/rebuild DEV/editor from the same SHA with OQ-13 and
   `TARGET_SHA=$CANDIDATE_SHA`, in the fixed OQ-13 order: (a)
   `bash deploy/deploy-dev.sh "$CANDIDATE_SHA"` from the controlled worktree,
   then the `spine-build` proof against the candidate's committed
   `.build-stamp.json` `spine_build_id` (64 hex, length-checked); then (b) the
   `--env=""` Worker upload, `check_version_view dev "$CANDIDATE_SHA"`, and
   deploy, and `prove_provenance` on
   `https://sonsteng-chat.damienriehl.workers.dev/edit/release-provenance`
   with `204` and the candidate SHA. Between (a) and (b) the prior DEV Worker
   briefly overlays candidate static pages; OQ-13 explains why that mixed
   state, and compensation's opposite one, are both covered by the OQ-8
   identity checks. Run (b) promptly after (a) prints `DEV static OK`, and
   never skip (b);
9. reactivate and prove the prior pair (Pages first, then the Worker), then the
   intended new pair (Worker first, then Pages), with the
   [Pages and Worker rollback commands](#pages-and-worker-rollback-commands)
   and an inspector readback after each pair. The prior pair is the one
   recorded in step 3, so its proof is the inspector's `sha` equal to
   `PRIOR_PAIR_SHA` with the exact prior IDs, not `PRIOR_SHA`:

   ```bash
   credential-helper-that-prints-only-the-Cloudflare-bearer | pages_rollback "$PRIOR_PAGES_DEPLOYMENT_ID"
   echo "pages rollback rc=$?"
   ( cd "$CONTROLLED_REPO/app/worker" &&
     npx wrangler@4 versions deploy "$PRIOR_WORKER_VERSION_ID" --env production --yes )
   echo "worker rollback rc=$?"
   RECOVERY_JSON=$(recovery_ids); echo "inspector rc=$? $RECOVERY_JSON"
   test "$(recovery_field sha)" = "$PRIOR_PAIR_SHA" &&
     test "$(recovery_field pages_deployment_id)" = "$PRIOR_PAGES_DEPLOYMENT_ID" &&
     test "$(recovery_field worker_version_id)" = "$PRIOR_WORKER_VERSION_ID" &&
     echo "PRIOR PAIR OK" || echo "STOP: prior pair readback"

   ( cd "$CONTROLLED_REPO/app/worker" &&
     npx wrangler@4 versions deploy "$NEW_WORKER_VERSION_ID" --env production --yes )
   echo "worker forward rc=$?"
   credential-helper-that-prints-only-the-Cloudflare-bearer | pages_rollback "$NEW_PAGES_DEPLOYMENT_ID"
   echo "pages forward rc=$?"
   RECOVERY_JSON=$(recovery_ids); echo "inspector rc=$? $RECOVERY_JSON"
   test "$(recovery_field sha)" = "$CANDIDATE_SHA" &&
     test "$(recovery_field pages_deployment_id)" = "$NEW_PAGES_DEPLOYMENT_ID" &&
     test "$(recovery_field worker_version_id)" = "$NEW_WORKER_VERSION_ID" &&
     echo "NEW PAIR OK" || echo "STOP: new pair readback"
   ```

   Require every `rc=0`, `PRIOR PAIR OK`, and `NEW PAIR OK`. Do not run the
   inspector between the two halves of a pair change: its shared-SHA proof
   fails by design while Pages and the Worker name different SHAs. Any `STOP`
   means complete compensation, which starts by restoring and re-proving the
   prior pair;
10. prove canonical `main`, production, DEV, and editor all name the candidate:
    canonical `main` (CAS readback), both production `x-release-sha` headers
    (inspector), and the DEV/editor Worker `x-release-sha` equal
    `CANDIDATE_SHA`. The DEV static `spine-build` equals the candidate's
    committed `spine_build_id`. The editor surface
    (`edit.legalpracticum.org`) is the same `sonsteng-chat` Worker overlaying
    that DEV static origin, so those two proofs cover it;
11. at window close, rerun the readonly function from item 2 with the same
    `--window-owner`. The function re-verifies the checkout commit, committed
    blob, and working verifier blob before this second proof:

    ```bash
    run_queue_proof closing '<absolute-closing-receipt-path>.json'
    closing_queue_proof_rc=$?
    ```

    Require `closing_queue_proof_rc` and the same complete-JSON validator run
    against the closing receipt to both return exactly `0`. Require the same
    two `verifier_identity` values and the
    same pre-recorded fence nonce digest. Require
    `fence.window_phase:"closing"` and require both closing server dates to
    strictly postdate their opening-receipt counterparts. The phase is the
    categorical discriminator and the server-date pairs are the temporal
    discriminator. Do not use `ledger_state_hash` to distinguish the receipts:
    for two successful empty-ledger proofs it is necessarily identical.
    Preserve this fresh identity-bearing receipt as the durable queue evidence.
    Only then release the window and restore the apply timer's prior policy.

The opening receipt is a go/no-go check for entering Packet D work. It does not
remain authoritative after later actions in the window. The closing rerun,
still inside the fence, is the durable evidence for that named window.

These fields make an accidental stale, rehearsal, other-host, or other-ledger
receipt detectable when compared with the pre-recorded nonce digest and supervised
window log. They are not a signature. Someone able to forge arbitrary receipt
bytes can still forge these fields; a valid receipt can also be replayed inside
the same window if an auditor ignores its server dates and ordering, and later
work after the closing proof still invalidates it. Preventing those cases needs
the supervised fence/log discipline (or a future signing scheme), not another
self-declared receipt field.

If any step is ambiguous, run complete compensation and keep the window fenced
until the prior state is proved. Do not infer a provider ID, fall forward to
`HEAD`, alter DNS or Access, or substitute normal Publisher authorization for
this migration-only KTD6 waiver.

For act 4, after the candidate commit, its review, and the item 5 Phase 2
verification have passed and while the daemon lock and six-actor window remain
held, rehearse the Git-only transition:

```bash
CAS_FORWARD_DRY_RECEIPT="$RECEIPT_DIR/canonical-ref-forward-dry-run.json"
/usr/bin/env -i /usr/bin/python3 -I "$CAS" forward \
  --repo "$DAEMON_REPO" \
  --remote origin \
  --branch main \
  --from "$PRIOR_SHA" \
  --to "$CANDIDATE_SHA" \
  --expect-remote-url-sha256 "$EXPECTED_REMOTE_URL_SHA256" \
  --window-owner "$WINDOW_OWNER" \
  --receipt-path "$CAS_FORWARD_DRY_RECEIPT" \
  --dry-run
```

Then perform the exact same transition without `--dry-run`:

```bash
CAS_FORWARD_RECEIPT="$RECEIPT_DIR/canonical-ref-forward.json"
/usr/bin/env -i /usr/bin/python3 -I "$CAS" forward \
  --repo "$DAEMON_REPO" \
  --remote origin \
  --branch main \
  --from "$PRIOR_SHA" \
  --to "$CANDIDATE_SHA" \
  --expect-remote-url-sha256 "$EXPECTED_REMOTE_URL_SHA256" \
  --window-owner "$WINDOW_OWNER" \
  --receipt-path "$CAS_FORWARD_RECEIPT"
```

`forward` requires a non-shallow daemon repository with no other worktree
holding `main`; clean checked-out local `main`, worktree `HEAD`, remote-tracking
`origin/main`, and remote `main` all at the exact prior SHA; the candidate's
sole parent to be that prior SHA in the raw commit object; and the named remote
to resolve to one non-empty, identical fetch and push URL. It refuses Git
configuration injected through the environment, invokes Git from an explicit
environment with no inherited variables, `LC_ALL=C`, global and system Git
configuration disabled, replacement objects disabled, prompts disabled, and
SSH transport disabled. It does not use client-side `-c` values that purport to
override serving-repository `uploadpack` or `transfer` settings: those values
cannot neutralize the server's own configuration. Instead, required remote
`main` and symbolic-`HEAD` advertisements fail closed when the server hides
them. Only the tool-created temporary-index and optional-lock controls are added
for the calls that need them. It independently refuses any
`LD_*`, `OPENSSL_CONF`, `OPENSSL_MODULES`, `PYTHONHOME`, `PYTHONINSPECT`,
`PYTHONPATH`, `PYTHONSTARTUP`, or `PYTHONUSERBASE` variable in its own
environment. This is the same family and named-variable set cleared in the
launcher block above. It records a
credential-redacted version, including redaction of URL and scp-like userinfo,
and SHA-256 fingerprint of the validated remote URL in its receipt. It checks
the candidate in a fresh standalone exact
clone, rejects hidden index flags, and proves tracked content and file types
against `HEAD` with a disposable index. It moves local `main` with the
three-argument
`update-ref refs/heads/main <candidate> <prior>` CAS, checks local `main` and
worktree `HEAD` at the candidate before and after aligning the index and
worktree with `read-tree -m -u <candidate>`, and pins the validated remote URL.
Before mutation it snapshots the full local ref map, the advertised non-hidden
remote ref map, and the remote symbolic `HEAD` from one `ls-remote --symref`
advertisement. The advertised `HEAD` must resolve to `refs/heads/main`; it then
pushes a verified non-shallow immutable source with
`--force-with-lease=main:<prior-sha>`, explicit
`refs/heads/main:refs/heads/main`, and tag following disabled. It succeeds only
after CAS-updating `refs/remotes/origin/main`, a final local symbolic-HEAD,
exact-cleanliness, remote-configuration, local-ref-map, remote symbolic-HEAD,
and local/remote/worktree SHA proof. The after-state `ls-remote --symref`
comparison proves that the only change among the remote's advertised
non-hidden `refs/*` was the exact `refs/heads/main` transition; the separate
symref comparison proves remote `HEAD` did not change. This migration-specific
command replaces the generic merge example in `docs/direct-apply-daemon.md`,
which must not be used for Day Zero.

Exit `0` alone never accepts any of the four CAS commands. Open the named
receipt file only after rerunning the release-identity comparisons above, and
require all of the following: `result` is `"success"`;
`verb` equals the command verb; `dry_run` is `true` exactly for a rehearsal and
`false` for a live command;
`tool.path` is `$CAS`;
`tool.sha256` is `$REVIEWED_CAS_SHA256`; `repo`, `remote`, and `branch` are the
absolute `$DAEMON_REPO`, `"origin"`, and `"main"`; `timestamp_utc` falls inside
the current named window; `window_owner` equals `$WINDOW_OWNER` and
`host_identity` equals `$HOST_IDENTITY`; both remote URL digest fields equal
`$EXPECTED_REMOTE_URL_SHA256`; `expected.from` and `expected.to` equal the
command coordinates; and all three `readback` values equal `--from` for a dry
run or `--to` for a live run. A successful dry run has
`transition_outcome: "not-attempted"`; a successful live run has
`transition_outcome: "succeeded"`. A dry-run mutation list must be empty. A live
forward list must be exactly `local-main-cas`, `worktree-alignment`,
`remote-main-cas`, `remote-tracking-main-cas`; a live restore list must be
exactly `remote-main-cas`, `local-main-cas`, `worktree-alignment`,
`remote-tracking-main-cas`. Reject an absent, reused, malformed, stale, or
identity-mismatched receipt and keep the window fenced. A failed live command
uses its receipt and direct state readback to decide compensation; never
compensate merely because terminal output was lost when the durable receipt is
present and valid.

Every possible `transition_outcome` has an operator rule:

- `succeeded`: the live transition has the complete verb-specific ledger and
  all three final readbacks equal `--to`. Continue only after every other
  acceptance field also matches.
- `landed-verification-incomplete`: the canonical transition landed, but some
  later bookkeeping or verification was incomplete. This requires either all
  three available readbacks at `--to`, or a complete ledger with no available
  readback contradicting `--to`. Keep the window fenced, re-observe missing
  values out of band, and **do not run candidate-to-prior compensation** against
  this state.
- `target-already-present`: no mutation was performed and all three readbacks
  equal `--to`. Treat it as retry evidence, keep the window fenced, investigate
  the earlier consumed receipt, and do not compensate from the retry result.
- `not-attempted`: the command was a dry run and performed no owned mutation.
  A successful rehearsal additionally requires an empty mutation list and all
  three readbacks equal to `--from`; a failed rehearsal means the production
  window must not open. Either result authorizes no live state change.
- `incomplete`: at least one mutation was confirmed, or a remote CAS was
  attempted and its post-failure remote observation is unavailable, but the
  complete target state was not certified. Keep the window fenced and read all
  three surfaces directly. If the remote readback equals `--to`, the production
  ref CAS landed: do not repeat or reverse it merely from this label; repair
  remaining local bookkeeping only under a new exact plan. If the remote
  readback is absent, re-read it out of band before deciding any production
  move. If the remote readback is neither `--from` nor `--to`, a third party
  moved production after this command's CAS; keep the window fenced and
  escalate, and do not treat even a complete mutation ledger as authority to
  compensate.
- `not-landed`: no mutation was confirmed and the target was not fully
  observed. This is not proof that an unreadable remote stayed at `--from`,
  especially when validation failed before readback. Keep the window fenced,
  directly re-read the remote, and do not treat the label alone as authority to
  move production.
- `undetermined`: a bounded fallback could not establish a transition state.
  Keep the window fenced, directly read all three surfaces, and escalate; this
  label never authorizes retry, reversal, compensation, or any other production
  move. A `failure-handler-fallback` source retains any mutations already
  recorded, while an `outermost-fallback` source reports mutation evidence as
  unavailable rather than asserting an empty ledger. On an
  `outermost-fallback` receipt, `expected.from`, `expected.to`, `repo`,
  `remote`, `branch`, `expected_remote_url_sha256`, and `window_owner` are
  unvalidated echoes of the command input so the receipt can still be tied to
  its requested transition and migration window; they are not proof that any
  repository state or identity was validated. The echoes are still
  credential-redacted: `remote` is echoed only when it is a plain remote name
  and is otherwise `[redacted]`, and any echo carrying URL or scp-like userinfo
  is redacted.

On any normally handled failure after operation validation,
`transition_outcome_source` is `"post-failure-readback"`. If the failure handler
itself cannot finish, the source is `"failure-handler-fallback"`; if an
exception escapes the operation entrypoint entirely, it is
`"outermost-fallback"`. A push reported failed by the client is reconciled
against the fresh remote observation when available; when it equals `--to`, the
receipt adds `remote-main-cas` to the confirmed mutation ledger and records
`mutation_reconciliation.remote-main-cas` as
`"confirmed-by-post-failure-readback"`. Observation failures appear separately
in `readback_errors`; an unavailable observation is not a contradictory SHA.
The receipt's transition evidence, not `result` or process exit alone, decides
whether any compensation may move production.

Receipt paths are single-use. The current writer publishes only complete JSON
and removes its private temporary file. After a write failure, preserve the
mirrored payload and any complete named receipt that was published, choose a
new unique path in the same mode-`0700` evidence directory, and rerun the exact
original command. Never delete, overwrite, or reuse a named receipt. The CAS
makes the retry non-mutating if the transition already landed.

`host_identity` is the UTS-namespace nodename, not proof of a physical host.
Corroborate it with the independently controlled login/session and reviewed
checkout identity; do not rely on `uname -n` from the same namespace alone.
`timestamp_utc` is a single wall-clock observation with no monotonic or trusted
clock corroboration. A timestamp outside the named window rejects the receipt
but is not evidence that the transition failed; keep the window fenced and
reconcile the live refs before any state change.

`unsafe-process-environment` is a pre-mutation refusal. Keep the refusal for
both CLI and adapter paths, including benign `LD_LIBRARY_PATH`: security takes
precedence during compensation. Do not unset it inside an already-started
adapter process, because loader effects may already have occurred. Keep
production fenced, restart the adapter-hosting process from the clean launcher
environment above, use a new receipt path, and retry only after direct ref
readback. The accompanying `not-landed` value is not proof about an unreadable
live remote.
