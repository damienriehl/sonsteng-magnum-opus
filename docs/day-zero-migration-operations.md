# Day Zero migration rehearsal and supervised boundary

`tools/day_zero_migration.py` closes the executable rehearsal gap in U15 without
creating a new production bypass. Its default mode checks out one exact Git
commit in a temporary standalone local clone, runs all migration gates there,
deletes the clone, and makes no production call.

## Safe default: exact-SHA rehearsal

Run this from the dedicated daemon checkout or another clean trusted checkout:

```bash
python3 tools/day_zero_migration.py --candidate-sha <40-character-lowercase-SHA>
```

Omitting `--candidate-sha` uses the exact current `HEAD`. The command creates a
standalone clone with no local hardlinks or object alternates and checks out
that exact commit detached; uncommitted files and the source checkout are never
rewritten. Retaining Git metadata is intentional because preflight's tracked-file
and history checks must run under their real repository contract. The command
removes credential-like environment variables from every child process, forces
both production controls false, suppresses child output, and runs these bounded
phases in order:

1. governed combined date-offset/JSON-LD verification (dry run and round-trip proof);
2. one atomic date-offset and JSON-LD-base write in the disposable copy;
3. site, Worker-persona, instructor, history, and editor-map builds;
4. generated-bundle parity;
5. strict spine validation with Day Zero and Legal Practicum identifier enforcement,
   including nonzero `checked_dates`, `offset_dates_checked`, and
   `identifier_files_checked` and `identifier_base_values_checked` evidence,
   complete manifest/matters/curriculum/jurisdictions/firm/taxonomy/schemas scope, plus zero
   `old_identifier_base_occurrences`;
6. repository preflight in headless/no-browser mode.

Any failed phase aborts the rehearsal. A passing JSON receipt reports the exact
candidate SHA, the six phase names, and `production_mutations: 0`. The temporary
copy is removed on normal exit and catchable signals. As always, SIGKILL or
machine power loss cannot run process cleanup, but the source checkout was never
the write target.

## Production remains fail closed

`--execute` is intentionally not connected to Cloudflare, systemd, the editor
queue, or the recovery registry. Even with every flag and
`SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true`, it exits `78` before any production
adapter call. `deploy/deploy-prod.sh` remains a disabled tripwire and is never a
fallback.

This boundary is deliberate. The existing low-level release adapters can upload,
activate, restore, and verify SHA provenance, but they do not safely read and bind
the *currently active exact* Pages deployment ID and Worker version ID as one
pair. Trusting ambient CLI output or reusing the normal Publisher-ledger executor
would either weaken U15's compensating control or blur its one-off waiver.

The module includes a dependency-injected production state machine so this
workflow is executable against fakes and ready for a future bounded provider
reader. Its tested contract requires, before mutation:

- an exact lowercase candidate SHA;
- the exact prior Pages ID, Worker ID, and shared live provenance SHA;
- an absolute non-symlink recovery-registry path;
- explicit acknowledgements that John was notified and the queue was empty;
- `SONSTENG_PROD_RELEASE_ENABLED=false`;
- the production release timer already disabled and inactive;
- the apply timer stopped and disabled with readback;
- no relevant service/process/lease; and
- the shared daemon lock held through verification, deployment, recovery-pair
  recording, restoration proof, and return-to-candidate proof.

Provider failures are reduced to bounded categories. Provider stdout/stderr,
credentials, and authored content are never included in a receipt or exception.
The exact prior apply-timer enabled/active policy is restored and read back on
every exception and catchable signal. If candidate activation may have started,
failure compensation occurs while the daemon lock is still held and must prove
the exact prior pair before the lock is released.

## Generate the exact operator sheet

Once a trusted read-only provider inspection has supplied the exact prior pair,
generate the non-secret supervised checklist:

```bash
SONSTENG_DAY_ZERO_MIGRATION_ENABLED=true \
SONSTENG_PROD_RELEASE_ENABLED=false \
python3 tools/day_zero_migration.py \
  --print-operator-plan \
  --candidate-sha <candidate-SHA> \
  --prior-sha <prior-live-SHA> \
  --prior-pages-deployment-id <exact-Pages-deployment-ID> \
  --prior-worker-version-id <exact-Worker-version-ID> \
  --recovery-registry "$HOME/.local/state/sonsteng-prod-release/known-good-pairs.json" \
  --ack-john-notified \
  --ack-queue-empty
```

Do not put credentials in these arguments. The provider IDs are non-secret
recovery coordinates; credentials remain in the protected process environment.
Generating the sheet does not authorize or execute production work.

## Remaining supervised U15 act

Damien must still perform the production window at the keyboard under the
Cloudflare PROD principal described in `docs/prod-release-operations.md`:

1. notify John and independently prove the queue is empty;
2. capture and verify the exact prior provider pair and both live SHA headers;
3. stop the apply timer, prove both services quiescent, and take the daemon lock;
4. run the exact-SHA rehearsal and repeat its single combined date-offset and
   JSON-LD-base write sequence in an isolated candidate checkout;
5. upload only the Pages artifact and named production Worker version;
6. read back the exact new pair and SHA, then atomically record the complete pair;
7. reactivate and prove the exact prior pair;
8. reactivate and prove the exact intended new pair; and
9. release the lock, restore the apply timer's exact prior policy, and read it back.

If any step is ambiguous, stop with production on the exact prior pair. Do not
infer a provider ID, fall forward to `HEAD`, alter DNS or Access, touch DEV, or
substitute normal Publisher authorization for this migration-only KTD6 waiver.
