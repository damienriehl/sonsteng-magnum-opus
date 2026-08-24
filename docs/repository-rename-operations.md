# Repository rename operations

The planned GitHub repository name is `legal-practicum`. The current repository and every active
reference remain unchanged until Packet D migration evidence has passed and an operator schedules a
quiet rename window. Historical plans, decisions, handoffs, and evidence remain unchanged forever.

## Prepare the inventory

Run the read-only scanner from the repository root:

    python3 tools/repo_rename_inventory.py \
      --owner damienriehl \
      --current sonsteng-magnum-opus \
      --target legal-practicum > /protected/path/repository-rename-inventory.json

The scanner reads tracked text files plus local Git remote and worktree metadata. It does not edit a
file, contact GitHub, rename a directory, change a remote, or restart a service. Its manifest contains
paths, line numbers, classifications, remote names, and digests of worktree paths; it never includes
source-line text. Any unclassified active occurrence fails the scan instead of guessing whether the
reference is operational or historical.

`historical_evidence_preserve` entries must not be patched. Active documentation, generated URLs,
contract tests, installers, and operational references are patched in the controlled window. Hosted
Actions `uses:` references are a separate class because GitHub repository redirects do not keep those
consumers working reliably; update each consumer explicitly.

## Controlled cutover

Do not begin until Packet D evidence is accepted, no Publisher release is active, the apply and
production-release daemons are stopped under their documented controls, and the human operator has
confirmed the quiet window and target name.

1. Save a fresh passing inventory in the protected operations record and confirm there are no
   unclassified references.
2. Record the current GitHub repository ID, default branch, branch protections, deploy keys, webhooks,
   Actions consumers, Pages/build source, and every remote named by the inventory.
3. Rename the external GitHub repository through the authenticated owner workflow. This is the first
   activating step and is deliberately outside the preparer.
4. Patch only entries classified for change. Do not replace occurrences under historical plans,
   decisions, handoffs, or evidence.
5. Repair each clone remote with the exact new repository URL. Re-read every remote after writing it.
6. Repair worktree and local checkout paths only where the operator wants the directory name changed;
   Git worktree metadata must be verified after any filesystem move.
7. Regenerate user-level systemd units from the repository installers so `WorkingDirectory`, `ExecStart`,
   and `Documentation` resolve to the intended checkout and URL. Reload the user daemon, but leave
   timers stopped until verification passes.
8. Verify the apply daemon, production-release daemon, TODO timer, build source, and public source links
   against the new canonical repository. Confirm each service uses the intended credentials and scope.
9. Verify a new clone and ordinary web/blob redirects from the old URL. Separately verify every hosted
   Actions `uses:` consumer against the new name; do not accept a redirect as proof for that class.
10. Re-run the inventory with `--current sonsteng-magnum-opus`. Only preserved historical entries may
    remain. Start timers only after the new clone, daemons, build source, links, and Actions consumers
    all pass.

If any verification fails, keep timers stopped and repair the active reference. The GitHub rename can
be reversed through the owner workflow, but do not rewrite historical evidence or use a global search
and replace as compensation.
