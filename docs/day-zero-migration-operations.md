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
   exclusive change window;
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

   The launcher's Bash `builtin exec -c` is the loader-isolation boundary: it
   gives the dynamic loader of the first `/usr/bin/env` an empty environment.
   The nested `/usr/bin/env -i` then removes the launcher's non-secret scrub
   canary and gives Python the exact sole allowlisted entry `LC_ALL=C`; removing
   either isolation mechanism is a tested failure. The verifier's exact
   environment allowlist and isolated-mode check are secondary, post-start
   guards. They cannot fire if a dynamic loader pre-empts the interpreter.
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

   Require `opening_queue_proof_rc` to be exactly `0`, the receipt to contain
   `"all_queues_empty":true`, its measured `verifier_identity` values to equal
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
   verifies `sonsteng-apply.timer` is inactive, records its recognized
   enabled/disabled state, records the named window, phase, and nonce digest,
   the local UTC times and server-authenticated HTTP `Date` values of both GETs,
   and the signed skew in seconds (`server Date - local clock`). Each skew must
   be within 300 seconds, and the frontier server date must not precede the
   review server date. It also records SHA-256s of
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
   durable receipt returns nonzero with a bounded diagnostic. `/dev/full`, a
   closed stdout, or a broken stdout pipe also returns nonzero; when the durable
   write completed, the receipt remains at the required path and the diagnostic
   says the stdout mirror failed. Preserve any existing or partial receipt and
   use a new path for a supervised retry.

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
   zero count and `blocked_state:"unblocked"`. Until the observer endpoint
   exposes that field, a source-faithful response fails closed with
   `operation-frontier-missing`. If the observer environment file does not
   exist, the receipt reports `"publication":"observer-env-absent"` and fails
   closed with `environment-unavailable`; production-timer state is retained
   only as diagnostic metadata and cannot substitute for the observer proof.
   Never put environment-file values on the command line;
3. capture and verify the exact prior pair and both live SHA headers;
4. rehearse, then materialize and commit the combined rewrite plus generated
   artifacts exactly once; merge only that commit;
5. verify the exact committed tree with the write-free phase list;
6. upload only the Pages artifact and named production Worker version;
7. read back and atomically record the exact new provider pair;
8. deploy/rebuild DEV/editor from the same SHA;
9. reactivate and prove the prior pair, then the intended new pair;
10. prove canonical `main`, production, DEV, and editor all name the candidate;
11. at window close, rerun the readonly function from item 2 with the same
    `--window-owner`. The function re-verifies the checkout commit, committed
    blob, and working verifier blob before this second proof:

    ```bash
    run_queue_proof closing '<absolute-closing-receipt-path>.json'
    closing_queue_proof_rc=$?
    ```

    Require `closing_queue_proof_rc` to be exactly `0`,
    `"all_queues_empty":true`, the same two `verifier_identity` values, and the
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
