# Publisher-authorized production releases

The executor uses a dedicated `EDIT_TOKEN_RELEASE` bearer granted only the
`release_service` scope. Never reuse the DEV apply daemon's admin bearer. Each
authorized candidate is materialized in a detached temporary worktree, so DEV
may continue advancing without changing the frozen release. Worker upload and
activation always target Wrangler's explicit `production` environment.
Pages deploys explicitly target the configured production branch (`main` by
default). Each provider's bounded deployable ID is atomically recorded in the
0600 `SONSTENG_PROD_RECOVERY_REGISTRY`; unbounded CLI output and credentials
never enter either that registry or the release ledger.

Read-only preparation checks use a third principal, `EDIT_TOKEN_OBSERVER`,
granted only `release_observer`. That scope is accepted exclusively by the GET
status, frontier, and text-free audit routes. It is rejected by every prepare,
claim, renew, transition, restore, review-decision, authorization, DEV-apply,
and provider operation.

For a fenced release, an operator may invoke the disabled-by-default service
manually with `--restore-release-id RELEASE_ID`. The daemon loads only that
release's recorded base pair and reactivates the exact Pages deployment and
Worker version; it never restores from ambient `HEAD` or current build output.

**Approval is not publication.** A wording edit may be saved, accepted, applied to canonical
content, and visible on DEV without changing public production. Production changes only when a
human Publisher authorizes an immutable prepared batch and the separate release executor verifies
that exact batch on both Pages and the production Worker/editor map.

The release ledger lives in the canonical editor Durable Object on the Access-protected editing
Worker, because that store owns the completed DEV apply batches. The production Worker is a release
target and provenance source; its separate editor store is never publication authority.

## Writer inventory and closed bypasses

| Possible writer | Repository contract |
|---|---|
| `tools/direct_apply_daemon.py` | DEV only. It may apply accepted rows to canonical content and deploy DEV; it never invokes a PROD deployer. |
| `deploy/deploy-prod.sh` | Disabled tripwire. It exits before reading credentials or invoking a provider. There is no override. |
| Production release executor | `tools/prod_release_daemon.py` claims only a ledger record already authorized by a human Publisher. It validates the frozen manifest and coordinates Pages + Worker. |
| automatic Pages builds | None are configured in this repository. Keep the Pages project in direct-upload mode; a branch integration must not be added outside the ledger executor. |
| direct branch writers | `main` may advance for DEV, but it is never production authority. The candidate/base SHAs in the authorized manifest are authority. Releases run only from the dedicated daemon checkout under `.locks/daemon.lock`. |
| cron/systemd | `sonsteng-apply.*` remains DEV only. `sonsteng-prod-release.*` is separate, config-off, and installed disabled. No other timer may call Wrangler or the PROD release endpoints. |
| production `DIRECT_APPLY` | Checked in as `false`. PROD browser saves require review; neither a save nor approval creates production authority. |

Repository checks enforce the inventory in `tools/tests/test_prod_release_operations.py` and
`tools/tests/test_publication_boundary.py`. Re-run the inventory after any CI, Pages, Worker,
systemd, deploy-script, or branch-policy change.

## Config-off installation

Run `bash tools/install-prod-release-daemon.sh` manually from a trusted checkout. It writes a 0600
template at `~/.config/sonsteng-prod-release/env` and systemd user units, but does **not** enable or
start them. The unit runs from `~/.local/share/sonsteng-daemon/checkout` and shares that checkout's
`.locks/daemon.lock`; it may not run from an interactive tree.

Rerunning the installer also migrates an existing legacy environment while it remains config-off.
The migration accepts only a non-symlink regular file with mode 0600 and exactly one active
`SONSTENG_PROD_RELEASE_ENABLED=false` assignment. It preserves every existing line and value,
atomically appends only missing required settings, and is idempotent once the file is current. Any
unsafe file or ambiguous/true enabled flag aborts the installer without changing the environment or
units. Newly added blank non-secret controls still require an explicit operator choice. The release
bearer and Cloudflare API token are labeled separately and remain blank pending independent
least-privilege provisioning; the migration never evaluates, prints, hashes, or replaces their
values.

The template begins with `SONSTENG_PROD_RELEASE_ENABLED=false`. With the variable missing or false,
the executor exits successfully before parsing release arguments, reading a bearer, opening the
ledger, inspecting git, or contacting Cloudflare. Keep it false until all of these are recorded:

1. store migrations and the last verified production frontier are proven;
2. an exact known-good Pages deployment and Worker version are bootstrapped for recovery;
3. the candidate manifest reproduces from a clean isolated worktree;
4. transient Pages/Worker compatibility and a deployment order are proven;
5. credential separation, redaction, rotation, and revocation checks pass;
6. supervised canary and exact-pair restore drills pass.

For the existing legacy production pair, this is a hard evidence gate—not an
operator judgment call. Record the exact Pages deployment ID, Worker version ID,
matching source/candidate SHA and live provenance response; prove both provider
IDs can still be reactivated; set that SHA as the verified bootstrap base; then
restore and re-verify that exact pair in a drill. A current-looking web page, a
git commit alone, or a legacy Worker without the provenance endpoint is not
sufficient evidence. Until every item exists, keep
`SONSTENG_PROD_RELEASE_ENABLED=false`; do not prepare a selective candidate,
activate the timer, or use a direct deploy as a substitute canary.

### Audited legacy-pair bootstrap

Use `tools/prod_release_bootstrap.py` only from the privileged local operator
environment. It is intentionally separate from `prod_release_daemon.py`: it
has no release-ledger client, cannot prepare or authorize a candidate, and
refuses to run when `SONSTENG_PROD_RELEASE_BEARER` is present. Keep the normal
publication flag false throughout this procedure.

Supply the exact source/candidate SHA, Pages deployment ID, Worker version ID,
both SHA-bound provenance observations, trusted repository/artifact paths, and
state paths through the existing `SONSTENG_PROD_*` environment-file convention.
Also set `SONSTENG_PROD_BOOTSTRAP_AUTHORITY=local-operator`, a bounded
`SONSTENG_PROD_BOOTSTRAP_OPERATOR_ID`, and a bounded
`SONSTENG_PROD_BOOTSTRAP_AUTHORITY_CHANNEL`. Do not place credentials, edited
text, or secret values in arguments. Provider credentials remain injected by
the provider's existing protected environment.

The command checks out the exact SHA in a detached temporary worktree, rejects
outside-repository and symlinked artifact paths, proves both live provenance
endpoints, reactivates the supplied pair in compatibility order, and repeats
the pair in reverse order as a restoration drill. Only after every check passes
does one locked compare-and-set write make the complete pair visible in the
0600 recovery registry. An exact replay is idempotent; a changed pair or legacy
partial entry fails closed. The append-only 0600 receipt binds operator,
authority channel, SHA, time, redacted provider-ID digests, and the two proof
results. Provider stdout/stderr, credentials, and edited text are discarded.

Only after those gates may an operator set the flag to true and explicitly enable the timer. A
config flip is operational authority, so it must be attributed in the release evidence. Do not use
the legacy deploy script as a canary.

### Editing Worker migration and rollback gate

Before deploying the editing Worker migration, record its prior editing Worker version ID, the
deployed source SHA, and a redacted live provenance response. Prove the new schema is compatible
with that version before changing code: the old Worker can read the migrated Durable Object state,
its read-only Publisher/status projections remain valid, and it does not interpret new rows as
publication authority. Then activate the prior version, read back its exact version and provenance,
run a config-off status smoke, reactivate the new version, and read that identity back too.

This is a hard rollback gate. If the old Worker cannot read the migrated Durable Object state, stop
before legacy backfill. Do not treat a successful forward migration as rollback proof, and do not
backfill review revisions until the exact prior-version activation/readback and config-off smoke are
recorded. Rolling back the editing Worker never changes the production Pages/Worker pair.

### Routine activation ordering

Routine activation is fail closed and ordered. First stop and disable the timer, prove no release
service process is running and no live lease is owned, and retain `SONSTENG_PROD_RELEASE_ENABLED=false`.
While stopped, set the intended non-secret configuration, compute and record its
first-tick configuration digest, set `SONSTENG_PROD_EXPECTED_CONFIG_DIGEST`, then set the enabled flag and read
both values back without printing credentials. Record the operator, time, reason, exact code SHA,
configuration digest, and timer intent. Only then enable the timer and read back its enabled state.
Compute the value with `python3 tools/print_prod_release_config_digest.py --env-file <protected-env-file>`;
the parser allowlists non-secret release controls and neither evaluates nor hashes credential fields.

The first tick recomputes the non-secret configuration digest before importing the executor, parsing
arguments, reading credentials, opening the ledger, inspecting git, or contacting a provider. A
mismatch stops the tick. If any stop/process/lease, environment readback, timer readback, or first-tick
check fails, compensate by stopping and disabling the timer, restoring
`SONSTENG_PROD_RELEASE_ENABLED=false`, reading both states back, and recording the failed intent.

### Supervised one-shot canary

The first publication uses a process-scoped one-shot canary, not routine enablement. The timer remains stopped and disabled,
and the persisted environment remains config-off. In one supervised process,
override `SONSTENG_PROD_RELEASE_ENABLED=true`, set `SONSTENG_PROD_RELEASE_MODE=canary`, bind
`SONSTENG_PROD_CANARY_RELEASE_ID` to exactly one already human-authorized release, and bind the matching
configuration digest. Canary mode never prepares a frontier and claims only that exact release. Return
the process environment to config-off before the recovery drill. Routine timer activation is a later,
separately attributed event after the canary and exact-pair recovery evidence pass.

## Credentials, rotation, and emergency revocation

Use distinct environment-scoped principals:

- the DEV apply bearer may claim/finalize DEV apply batches and deploy DEV only;
- the PROD release bearer may prepare, claim, transition, and read release status, but cannot
  authorize a release;
- the PROD observer bearer may read release status, preparation frontier, and
  text-free audit only; it has no mutation method or provider credential;
- the human Publisher signs in through Access and is the only actor that may authorize;
- the Cloudflare PROD principal may upload the `sonsteng` Pages artifact and create/activate the
  named production Worker version only. It must have no DNS, Access-policy, account-admin, or DEV
  mutation rights.

Every machine endpoint is TLS-only. The PROD release bearer has independent production scope and is
not a renamed or shared DEV/admin credential. Human Access sessions require Publisher scope plus the
existing same-origin/CSRF marker for every review submission and authorization mutation; the service
bearer cannot submit or authorize. Conversely, Access sessions cannot prepare, claim, renew,
transition, fence, restore, or bootstrap. Bootstrap is a separate local-operator authority and accepts
neither the browser session nor the release-service bearer.

### Text-free readiness check

Provision the observer as its own Worker token slot (for example,
`observer: {"release_observer":1}` in `EDIT_TOKEN_SCOPES`) and inject its opaque
value only into a separate owned regular 0600 file:

```text
~/.config/sonsteng-release-observer/env
SONSTENG_PROD_OBSERVER_BEARER=<observer-only token>
```

Do not add `admin`, `publisher`, or `release_service` to that slot. Do not put
`EDIT_SERVICE_TOKEN` or `SONSTENG_PROD_RELEASE_BEARER` in this file. Verify the
file without printing its contents:

```bash
stat -c '%a %U %F' ~/.config/sonsteng-release-observer/env
```

The result must show mode `600`, the service account owner, and a regular file.
With the production executor still config-off and its timer stopped and
disabled, run:

```bash
python3 tools/prod_release_readiness.py \
  --ledger-url https://sonsteng-chat.damienriehl.workers.dev \
  --observer-env-file ~/.config/sonsteng-release-observer/env \
  --prod-env-file ~/.config/sonsteng-prod-release/env
```

The command performs only three allowlisted GET shapes. It reads only the
explicit config-off flag from the protected production environment (never its
bearer or provider values), reads systemd status
without changing it and emits only invariant counts, queue counts, bounded IDs,
commit/evidence hashes, the active/prepared/authorized state, timer state, and
config-off state. It never emits authored operations. `ready_to_prepare` means
the text-free queue and invariants are safe for the separate release service to
prepare; this observer does not perform that action and the result does not mean
authorized or published. A `prepared` record is reported by ID and hashes as an
`active_release`, not as permission to advance it. `unprepared`, `active_release`, `config_on`,
`timer_not_proved_off`, invariant failures, malformed responses, authentication
failures, and timeouts are bounded non-ready results. The command refuses to run
when a DEV-admin or production-mutation bearer is present in its process.

Secrets live only in 0600 environment files or the provider's secret store. **Never copy credentials**
into source, manifests, command arguments, journals, receipts, notifications, screenshots, or UAT
notes. Evidence records opaque provider IDs and hashes only. Do not inspect or print secret values
while verifying configuration.

Never pass secrets in CLI arguments. They must not appear in the ledger, configuration digest,
manifest, journal, receipt, logs, process listing, exception text, or provider-output capture. Canary
tests use a sentinel secret and fail if it reaches any of those seams. Redact authorization headers and
discard provider stdout/stderr before recording bounded error categories.

For routine rotation: create the replacement with equal or narrower scope, inject it out of band,
run a non-publishing authentication/status canary, switch the service environment, verify the old
principal can no longer authenticate, then revoke it. Record actor, time, principal identifier, and
canary result—not the value. For emergency revocation: set
`SONSTENG_PROD_RELEASE_ENABLED=false`, stop/disable the timer, fence the active ledger release,
revoke the affected provider or service principal, and reconcile strictly toward the recorded
manifest. Revocation never authorizes a different target.

## Legacy applied-change backfill

Fetch `GET /edit/v1/publisher/review/backfill-evidence` only with the protected
bearer-admin migration credential. Run `tools/build_prod_review_backfill.py` once
to create the payload and again with `--check`; do not submit unless the second
run is byte-identical. Both files contain authored text and belong only in a 0600
operator state directory, never Git or logs.

Applied DEV suggestions created before granular review have no release authority.
The trusted apply/migration bearer may submit a named, immutable bulk backfill to
`POST /edit/v1/publisher/review/backfill`. Each per-source cumulative revision
must bind the verified PROD base, applied suggestion IDs, their completed apply
batch/commit evidence, the complete ordered apply-batch base-to-commit chain,
source hashes, and deterministic atomic operations. The store validates that
the chain ends at the revision and includes every applied suggestion for that
source in its named batches, validates the entire payload transactionally,
writes an audit receipt, and treats an exact
retry as an idempotent replay. Any pending row, mismatched source/commit/base, or
changed retry rolls back or fails closed. Backfill creates no draft, decision,
review receipt, release member, or implicit acceptance: every operation appears
as unreviewed and requires a fresh human review.

If the captured legacy rows do not form one contiguous chain because test/UAT
edits were later reverted, stop: the bulk backfill would make obsolete copy
reviewable again. Create a 0600 operator classification that places every legacy
suggestion in exactly one class: still effective, or excluded as
`reverted_legacy_uat`. Run `tools/build_legacy_review_reconciliation.py` and its
`--check` mode against the canonical repository, protected evidence, exact
verified PROD SHA, and that classification. For every exclusion the generator
requires the exact Git apply commit and either a `proof_base_sha` whose complete
source file equals current canonical bytes or restored original/new-text proof.
For every effective edit it requires exact PROD and current source values plus
proof that the completed apply commit changed the exact durable locator from the
recorded original value to the recorded new value. The payload carries the exact
batch/base/commit evidence that the Worker independently binds to its ledger.
Submit the resulting protected payload once to
`POST /edit/v1/publisher/review/reconcile-legacy` with the bearer-admin migration
credential and CSRF header. The first receipt permanently closes new migration
identities; only a byte-identical same-ID replay remains valid. Then require the text-free release audit to report
the expected exclusion/revision counts and
`unreconciled_applied_suggestions: 0`. Exclusions remain immutable historical
attribution only; they never become operations, decisions, or release members.

## Preparation, authorization, execution

The Publisher page may show eligible submitted-accepted operations, but its “Prepare immutable preview”
control stays disabled. On each config-enabled service run, the trusted candidate builder reads the
text-free contiguous frontier, proves the clean checkout, ancestry, exact membership, generator and
candidate tree, writes the canonical manifest under the service state directory, and submits the
immutable preparation. The first run also requires `SONSTENG_PROD_BOOTSTRAP_BASE_SHA`, recorded from
the exact SHA already verified on both production targets; later bases come from the completed
ledger frontier. No service bearer enters browser code. Once a prepared ledger record exists, the
human Publisher page becomes the read-only preview plus one explicit authorization action.

The executor may claim only `authorized` records. On ambiguity or partial failure it records a
bounded error, fences later releases, and reconciles or restores the recorded pair; it never falls
forward to ambient `HEAD`.

Accepted-only candidates are synthetic commits and therefore do not move a branch. Before the
temporary candidate worktree is removed, the builder creates an immutable
`refs/sonsteng/releases/<manifest-hash>` ref in the dedicated daemon repository. These refs share
the release ledger's durable retention: they are not deleted after completion, because the same
candidate identity remains audit and retry evidence. A repeated preparation may reuse the exact
ref, but a ref that already names another commit fails closed. Consequently routine or aggressive
Git garbage collection cannot prune a candidate while it awaits human authorization or later
recovery inspection.

## Status vocabulary

- **Saved / waiting for review:** stored, not approved.
- **Approved / queued for DEV:** accepted, not yet applied.
- **Available on DEV — waiting for Publisher:** canonical and DEV-applied, absent from PROD.
- **Prepared:** immutable evidence exists for human review; still absent from PROD.
- **Authorized / publishing:** a human Publisher authorized that exact batch; executor work is in progress.
- **Published:** both public Pages and the authenticated production editor map match the recorded SHA.
- **Failed and fenced / restoring / restored:** production is not claimed current until exact-pair verification succeeds.

The live acceptance matrix is in `docs/uat/editor-publisher-matrix.md`. Live deployment and secret
handling are intentionally not part of repository-only testing.

The one-off U15 corpus rewrite is rehearsed by `tools/day_zero_migration.py` under the separate
fail-closed procedure in `docs/day-zero-migration-operations.md`. That command does not add a direct
production writer or replace this Publisher-ledger lane.
