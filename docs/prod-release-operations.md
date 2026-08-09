# Publisher-authorized production releases

The executor uses a dedicated `EDIT_TOKEN_RELEASE` bearer granted only the
`release_service` scope. Never reuse the DEV apply daemon's admin bearer. Each
authorized candidate is materialized in a detached temporary worktree, so DEV
may continue advancing without changing the frozen release. Worker upload and
activation always target Wrangler's explicit `production` environment.

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

The template begins with `SONSTENG_PROD_RELEASE_ENABLED=false`. With the variable missing or false,
the executor exits successfully before parsing release arguments, reading a bearer, opening the
ledger, inspecting git, or contacting Cloudflare. Keep it false until all of these are recorded:

1. store migrations and the last verified production frontier are proven;
2. an exact known-good Pages deployment and Worker version are bootstrapped for recovery;
3. the candidate manifest reproduces from a clean isolated worktree;
4. transient Pages/Worker compatibility and a deployment order are proven;
5. credential separation, redaction, rotation, and revocation checks pass;
6. supervised canary and exact-pair restore drills pass.

Only after those gates may an operator set the flag to true and explicitly enable the timer. A
config flip is operational authority, so it must be attributed in the release evidence. Do not use
the legacy deploy script as a canary.

## Credentials, rotation, and emergency revocation

Use distinct environment-scoped principals:

- the DEV apply bearer may claim/finalize DEV apply batches and deploy DEV only;
- the PROD release bearer may prepare, claim, transition, and read release status, but cannot
  authorize a release;
- the human Publisher signs in through Access and is the only actor that may authorize;
- the Cloudflare PROD principal may upload the `sonsteng` Pages artifact and create/activate the
  named production Worker version only. It must have no DNS, Access-policy, account-admin, or DEV
  mutation rights.

Secrets live only in 0600 environment files or the provider's secret store. **Never copy credentials**
into source, manifests, command arguments, journals, receipts, notifications, screenshots, or UAT
notes. Evidence records opaque provider IDs and hashes only. Do not inspect or print secret values
while verifying configuration.

For routine rotation: create the replacement with equal or narrower scope, inject it out of band,
run a non-publishing authentication/status canary, switch the service environment, verify the old
principal can no longer authenticate, then revoke it. Record actor, time, principal identifier, and
canary result—not the value. For emergency revocation: set
`SONSTENG_PROD_RELEASE_ENABLED=false`, stop/disable the timer, fence the active ledger release,
revoke the affected provider or service principal, and reconcile strictly toward the recorded
manifest. Revocation never authorizes a different target.

## Preparation, authorization, execution

The Publisher page may show eligible contiguous DEV batches, but its “Prepare immutable preview”
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
