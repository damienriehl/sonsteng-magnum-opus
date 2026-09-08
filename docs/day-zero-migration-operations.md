# Day Zero migration materialization, verification, and supervised boundary

`tools/day_zero_migration.py` prepares U15 without creating a production
bypass. It has two intentionally different paths: a write-bearing
materialization rehearsal and a write-free verification of the exact committed
candidate. The dependency-injected production state machine consumes only the
second path. No full CLI production adapter exists. The separate,
Git-only `tools/canonical_ref_cas.py` supplies the bounded canonical `main`
forward and compensation operations documented below; it does not connect
`--execute` to any other production surface.

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

### Pin every Day Zero verifier to the reviewed release

Use one trusted operations checkout and one reviewed release identity for every
Day Zero verifier. Never accept a verifier from the daemon checkout, the current
directory, ambient `PATH`, or receipt replay. Establish these non-secret values
before the window from the reviewed release record; in particular,
`EXPECTED_REMOTE_URL_SHA256` must be the independently recorded digest of the
canonical remote URL, not a digest read from the daemon checkout being verified.

```bash
set -eu
unset LD_AUDIT LD_LIBRARY_PATH LD_PRELOAD
unset PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP

OPS_REPO=/absolute/path/to/the/reviewed/operations-checkout
CAS="$OPS_REPO/tools/canonical_ref_cas.py"
REVIEWED_OPS_COMMIT=<reviewed-40-character-release-commit>
REVIEWED_CAS_SHA256=<reviewed-64-character-canonical_ref_cas.py-sha256>
EXPECTED_REMOTE_URL_SHA256=<independently-recorded-64-character-remote-url-sha256>
DAEMON_REPO=/absolute/path/to/the/dedicated-daemon-checkout
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
```

The absolute `/usr/bin/python3 -I` interpreter, absolute `$CAS` path, clean
checkout commit, Git blob ID, and SHA-256 above are one release-identity rule;
apply that same rule to the queue verifier when its reviewed release is pinned.
Rerun all checkout, blob, and SHA-256 comparisons immediately after each
verifier invocation and before accepting its receipt.
Run this complete block in a pre-window rehearsal on the daemon host. The
`set -eu` makes every failed identity comparison abort this block. Clearing the
native-loader and Python injection variables before its first external command,
then launching Python with `/usr/bin/env -i`, prevents ambient code injection
from substituting the pinned interpreter. The `CS_PATH` probe is the go/no-go
check for the verifier's trusted Git resolver;
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
records the required window owner and host identity, plus best-effort readback
after a failure so partial state is never silent.
An inability to open, write, flush, or sync the receipt fails the command.

An injected production adapter can implement the state machine method by
delegating to
`canonical_ref_cas.CanonicalRefCasAdapter(...,
window_owner=WINDOW_OWNER).restore_canonical_ref_exact`.
That method returns the exact prior SHA only after all three readbacks match,
which satisfies the check in `day_zero_migration._restore_canonical_ref_exact`.
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
   artifacts exactly once; advance canonical `main` by running the exact
   one-commit compare-and-swap below;
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

For act 4, after the candidate commit and review have passed and while the
daemon lock and six-actor window remain held, rehearse the Git-only transition:

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
SSH transport disabled. Only the tool-created temporary-index and optional-lock
controls are added for the calls that need them. It independently refuses any
`LD_*`, `OPENSSL_CONF`, `OPENSSL_MODULES`, `PYTHONHOME`, `PYTHONPATH`, or
`PYTHONSTARTUP` variable in its own environment. It records a
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
require all of the following: `result` is `"success"`; `tool.path` is `$CAS`;
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

A nonzero exit with `result: "warning"` and
`transition_outcome: "succeeded-with-unexpected-observation"` means the
canonical transition succeeded but a later observation prevented clean
certification. Accept that classification only when the live mutation list is
the complete verb-specific list above and all three readbacks equal `--to`.
Keep the window fenced and investigate the named `warning`, but **do not run
candidate-to-prior compensation** against that state. The receipt's transition
evidence, not `result` or the process exit alone, decides whether compensation
may move production.

Receipt paths are single-use. If a process consumed a path but left an empty,
partial, or malformed file, preserve that file; never delete, overwrite, or
reuse it. Choose a new unique path in the same mode-`0700` window evidence
directory and rerun the exact original command. The CAS makes the retry
non-mutating if the transition already landed. A retry receipt with
`transition_outcome: "target-already-present"`, no mutations, and all three
readbacks equal to `--to` proves the retry found the target already present;
keep the window fenced and investigate the consumed receipt, and do not order
compensation from the retry's `result` alone.
