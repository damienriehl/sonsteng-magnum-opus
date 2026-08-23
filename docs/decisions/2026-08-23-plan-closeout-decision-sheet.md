# Decision Sheet — Remaining 21-day plan gates

Date: August 23, 2026  
Supersedes the residual-action sections of the August 20 and August 21 handoffs  
Purpose: one ordered, copy-friendly sheet for every remaining human or external gate

The Cloudflare cutover, Google live-provider check, and combined Day Zero date/JSON-LD implementation
are complete. PR #26 merged the atomic rewrite, permanent validator, and copied-corpus rehearsal.
Packet D may be scheduled once its human coordination and empty-queue conditions are satisfied.
Do not send credentials, one-time codes, edited private text, or personal roster data in the result.
For any failed step, stop that packet and paste back only the visible error and non-secret IDs.

## Packet A — Authenticated editor and assessment UAT

**Why you are needed:** Cloudflare Access must bind actions to an allowlisted human identity. A
service token cannot honestly substitute for the signer or Publisher.

### A1. Reversible editor round-trip

1. Open `https://edit.legalpracticum.org/` in your normal browser.
2. Complete Cloudflare Access with your allowlisted email and one-time code.
3. Confirm the bare host lands under `/edit/` and shows the editor/review option for your role.
4. Open a low-risk explanatory page and copy the exact original sentence into a temporary note.
5. Change one punctuation character only, such as adding or removing one comma.
6. Submit and wait for the normal completed/applied state.
7. Record the page name and displayed suggestion or batch ID. Do not record the login code.
8. Restore the sentence to the exact original text through the same workflow.
9. Wait for completion, refresh once, and confirm the original sentence is visible.

### A2. Assessment signer exercise

There is no audit index or create button. Before starting A2, reply `A2 READY`; the agent will create
one harmless formative audit through the existing API and return its exact
`https://edit.legalpracticum.org/edit/assessments/<audit-id>` URL. The page requires the
Access-authenticated `damienadmin` instructor/admin role and is titled **Assessment signer review**.

1. While still authenticated, open the exact protected assessment URL supplied by the agent.
2. At a desktop width near 1280px, confirm the disposable formative audit loads. A uniform 404 means
   the identity lacks the required role or the audit is unavailable; stop and report it.
3. Confirm all seven memo headings are independently scored on the 1–7 scale.
4. Confirm the audit shows evidence spans and instrument/config provenance rather than an opaque
   total alone.
5. Resize the browser to 390px wide and confirm every heading, score, evidence control, and signer
   control remains readable and keyboard reachable.
6. Submit one harmless attributed override as yourself. Record the audit ID, but do not paste the
   assessment content. The browser does not expose the override receipt ID; after you report that the
   override persisted, the agent will retrieve that non-secret ID separately from the stored audit.
7. Reopen the audit and confirm the attributed override persisted.

### Paste back Packet A

```text
Packet A result: PASS | FAIL
Editor page:
First suggestion/batch ID:
Restoration suggestion/batch ID:
Original text restored: YES | NO
Assessment audit ID:
Override persisted: YES | NO
Desktop and 390px both usable: YES | NO
Error shown (if any):
```

## Packet B — Resolve the three-provider validation disposition

Google is already **PASS**. OpenAI's key is valid but its account has no available credit. No current
Anthropic key was authorized. These are account/authority gates, not repository defects.

Provider outcomes remain distinct: **PASS** means a live smoke met every contract; **BLOCKED** means
an external prerequisite prevented the smoke; **WAIVED** means you deliberately chose not to test.
A waived provider remains unvalidated in every closeout artifact.

1. Add enough OpenAI API credit for one minimal live smoke request, or waive that provider. The
   recommendation is to add the smallest practical credit.
2. For Anthropic, choose exactly one path:
   - **Recommended:** create or identify a current active API key in protected machine storage; or
   - explicitly authorize a credential-safe status-only test of the legacy Anthropic-format keys
     already discovered on this machine. No values will be displayed or copied.
3. Reply with the compact authorization below. For a current key, the agent will run the generation
   smoke harness and record only status, normalized event counts, usage, replay parity, and non-secret
   provider errors. For legacy status-only authorization, the agent will perform only the authorized
   credential-status check, report that result, and keep Anthropic **BLOCKED** until generation is
   separately authorized with a current key.
4. Treat every discovered legacy credential as compromised regardless of whether a status-only test
   succeeds. Revoke it at the provider, create a replacement only if needed, and store the replacement
   only in the established protected credential location. After revocation, explicitly authorize the
   agent to remove the known plaintext legacy copies from ordinary files, backups, and Trash; deletion
   will be reported separately because it is destructive.

### Paste back Packet B

```text
OpenAI: CREDIT ADDED | WAIVE
Anthropic: CURRENT KEY AVAILABLE | AUTHORIZE LEGACY STATUS-ONLY TEST | WAIVE
Current Anthropic key location, if applicable: [path only; do not paste the key]
After legacy-key revocation: AUTHORIZE PLAINTEXT-COPY CLEANUP | NOT YET
```

## Packet C — Publisher review, exact candidate, canary, and restoration

**Human judgment required:** accepting, rejecting, questioning, or leaving a proposed edit unanswered
changes authored content. The agent can operate the machinery after those judgments are recorded.

### C1. Review and authorize one exact candidate

1. Open `https://edit.legalpracticum.org/edit/publish` as the Access-authenticated
   `damienadmin` Publisher. The page title is **Production Publisher**. It is also linked from
   `/edit/review` as **Open Production Publisher**.
2. Review the reconciled revision item by item. Choose Accept, Reject, Ask question, or leave
   unanswered. Enter question text where required.
3. Reload before submission and confirm the draft survives.
4. Submit once and confirm the immutable receipt attributes every answer without treating unanswered
   items as accepted.
5. Review the prepared preview and verify the accepted operations, held exclusions, base SHA,
   candidate SHA, manifest hash, and evidence hash.
6. Authorize that exact immutable candidate with the explicit authorization control.
7. Paste back C1 below. The agent will then operate the process-scoped canary and exact-pair
   restoration drill while you observe and verify. Do not activate or restore provider artifacts in
   the browser yourself.

### Paste back C1

```text
Publisher review: COMPLETE | NOT COMPLETE
Review receipt ID:
Prepared release ID:
Authorized release ID:
Base SHA:
Candidate SHA:
Manifest hash:
Canary observation window: READY NOW | [proposed date/time]
```

### C2. Decide routine publication only after evidence

The agent will report the anonymous/editor provenance comparison, exact-pair restoration result, and
U18 consistency-daemon result after C1. Only if all three pass is routine enablement a live decision.
The recommendation remains **keep routine publication off** until those results are recorded.

```text
Public/editor provenance agreement: PASS | FAIL
Exact-pair restoration: PASS | FAIL
U18 consistency-daemon verification: PASS | FAIL
Routine publication: KEEP OFF | ENABLE
```

## Packet D — Combined Day Zero and identifier migration window

**External coordination required:** this changes the live corpus and must occur only when John has
been notified and the editing/release queues are empty. The governing U8 plan requires the date-offset
rewrite and JSON-LD base rewrite to run in one freeze window. The settled new base is
`https://legalpracticum.org/spine/`; the old base is `https://sonsteng.damienriehl.com/spine/`.

**Agent-owned prerequisite complete:** PR #26 merged the combined rewrite path, permanent old-base
validator, copied-corpus rehearsal, and rollback proof. The final exact-head rehearsal at
`b54c8b3854d0dabde038530293b1e567df5f61be` passed all six phases with zero production mutations.
Packet D remains supervised because it changes the live corpus and provider pair.

### D1. Schedule and authorize the supervised window

1. Choose a supervised window when Damien can stay at the keyboard for the full migration and
   restoration proof.
2. Notify John of the window and ask him not to edit during it.
3. At the start of the window, confirm the editor, review, and publication queues are empty, then paste
   back D1. The agent will perform the production operations; Damien supervises and verifies.

```text
Day Zero window: [date, start time, timezone]
John notified: YES | NO
Queues confirmed empty: YES | NO
```

### D2. Agent-operated migration and human verification

4. The agent captures and verifies the exact live Pages deployment ID, Worker version ID, and both
   live SHA headers through the trusted read-only provider procedure.
5. From clean merged `main`, the agent generates the fully parameterized operator sheet documented
   in `docs/day-zero-migration-operations.md`. `--print-operator-plan` emits a checklist, not executable
   migration commands, and direct `--execute` intentionally fails closed because no production adapter
   is installed.
6. The agent stops the apply timer, proves both services quiescent, and holds the daemon lock as the
   runbook directs.
7. The agent runs the exact-SHA rehearsal, then performs the supervised manual provider procedure from
   `docs/day-zero-migration-operations.md`. In the isolated candidate checkout, execute the single
   corpus pass: date offsets plus substitution of the old JSON-LD base with the settled new base.
8. The agent recomputes and records the exact identifier inventory at the freeze boundary; a historical
   file count is not an acceptance target. The agent verifies every old-base occurrence is gone, all
   block IDs remain unchanged, strict validation and generated parity pass, and only intended lines
   changed.
9. The agent deploys the intended new pair and records its IDs and provenance.
10. The agent restores the exact prior pair while Damien verifies that it serves correctly.
11. The agent returns to the intended new pair while Damien verifies Pages, Worker, provenance, and
    public behavior agree.
12. The agent releases the lock, restores the apply timer's exact prior policy, records the non-secret
    evidence in the UAT matrix, and executes U16b offset enforcement after accepted sidecars exist.

### Paste back D2 after the supervised run

```text
Prior Pages deployment ID:
Prior Worker version ID:
New Pages deployment ID:
New Worker version ID:
Restoration proof: PASS | FAIL
Returned to intended pair: YES | NO
Old JSON-LD base occurrences after migration: 0 | [count]
```

## Packet E — Source, rights, and calibration inputs

These items depend on people or materials outside the repository. Complete them independently; no
technical ordering is required except that publication waits for the relevant permission.

1. Obtain John's one-sentence written confirmation that he bought the Midstate materials from Anita.
2. Identify the other lawyers whose disc briefings may be published and obtain permission or mark
   each briefing excluded.
3. Deliver John's original print/video files and the two named syllabi for governed ingestion. Attach
   them to the active private session or place them in a protected local staging directory and return
   only that absolute directory path; never place uncleared source material in Git.
4. Deliver Roger's non-collaborative books and permission/provenance statement through the same
   protected channel.
5. Have John complete his editor pass.
6. Arrange human-human assessment calibration and complete provider-terms review before any
   summative use.
7. Have John and Roger each replace the temporary-token path independently: complete one Access
   sign-in and one saved/restored harmless edit. After each person succeeds, the agent may retire only
   that person's temporary token under `docs/prod-enable.md`. Retain Damien's break-glass token.

### Paste back Packet E

```text
Chain-of-title confirmation: RECEIVED | PENDING
Other-lawyer permissions/exclusions: COMPLETE | PENDING
John originals and two syllabi: DELIVERED | PENDING
Protected delivery path or attachment names:
Roger materials and provenance: DELIVERED | PENDING
John editor pass: COMPLETE | PENDING
Faculty calibration: COMPLETE | PENDING
Provider-terms review: COMPLETE | PENDING
John Access sign-in and restored edit: COMPLETE | PENDING
Roger Access sign-in and restored edit: COMPLETE | PENDING
```

## Packet F — Legal Practicum repository rename

The public domain cutover is complete. Packet D owns the combined identifier/date migration. The
GitHub repository rename waits until that migration evidence passes because it can disrupt clone URLs,
automation, integrations, and durable references.

1. After Packet D passes, choose a quiet rename window while no release is active.
2. Confirm the target GitHub repository name. The recommendation is `legal-practicum`.
3. Before the rename, let the agent inventory and update repository badges, CI references, deployment
   automation, Cockpit links, clone URLs, worktree/daemon metadata, and rollback instructions.
4. Review the inventory and migration evidence before the external GitHub rename is executed.

### Paste back Packet F

```text
Rename window: [date, start time, timezone]
Target repository name: legal-practicum | [different name]
```

## What the agent will do after each packet

- Packet A: record U10 and assessment UAT, then close those human gates.
- Packet B: run authorized credential-safe provider smokes, record PASS/BLOCKED/WAIVED distinctly,
  and perform separately authorized post-revocation plaintext cleanup.
- Packet C1: operate the supervised canary/restoration workflow; after a real published revision,
  execute U18 consistency-daemon verification and return the evidence needed for C2.
- Packet C2: apply the routine-publication decision only after the evidence exists.
- Packet D: operate the combined date/identifier migration and then execute U16b strict offset
  enforcement after accepted sidecars exist.
- Packet E: ingest only material with recorded provenance/permission, update the source ledger, and
  retire named-user temporary tokens only after each replacement Access path passes.
- Packet F: prepare and execute the repository rename after Packet D evidence is accepted.

No packet asks for a secret. If a requested choice differs from the recommendation, add one sentence
explaining the desired outcome; the agent will adapt the execution plan without re-asking settled
questions.
