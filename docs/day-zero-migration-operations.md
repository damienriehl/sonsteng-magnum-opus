# Day Zero migration materialization, verification, and supervised boundary

`tools/day_zero_migration.py` prepares U15 without creating a production
bypass. It has two intentionally different paths: a write-bearing
materialization rehearsal and a write-free verification of the exact committed
candidate. The dependency-injected production state machine consumes only the
second path. No full CLI production adapter exists. The separate,
Git-only `tools/canonical_ref_cas.py` supplies the bounded canonical `main`
forward and compensation operations documented below; it does not connect
`--execute` to any other production surface.

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
diff, and commit source, date-offset sidecars, identifier base, and generated
artifacts together. The resulting commit—not the pre-write source SHA—is the
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
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/
```

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
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/ \
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
  --worker-provenance-url https://sonsteng-chat-production.damienriehl.workers.dev/ \
  --repo <trusted-repository-path> \
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
for INJECTION_NAME in ${!LD_@}; do unset "$INJECTION_NAME"; done
unset OPENSSL_CONF OPENSSL_MODULES
unset PYTHONHOME PYTHONINSPECT PYTHONPATH PYTHONSTARTUP PYTHONUSERBASE

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
characters, without surrounding whitespace or `@`, refused before any mutation
otherwise) and host identity, plus best-effort readback
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

If `--repo` is omitted, the sheet explicitly says that candidate existence,
fresh-clone cleanliness, first-parent identity, and the one-commit range were
not checked; it does not assert those facts.

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
   zero count and `blocked_state:"unblocked"`. Until the observer endpoint
   exposes that field, a source-faithful response fails closed with
   `operation-frontier-missing`. If the observer environment file does not
   exist, the receipt reports `"publication":"observer-env-absent"` and fails
   closed with `environment-unavailable`; production-timer state is retained
   only as diagnostic metadata and cannot substitute for the observer proof.
   Never put environment-file values on the command line;
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
