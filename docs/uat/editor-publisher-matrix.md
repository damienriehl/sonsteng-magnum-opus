# Real-browser Editor and Publisher UAT matrix

Run this matrix on the real box with trusted browser exit codes. Use disposable, non-sensitive
wording. Record release IDs, hashes, timestamps, and screenshots only; never tokens or edited
content in operational logs. Repository tests and a mocked browser are preparation, not a pass.

## John-like editing journey

For each row, sign in through a clean Chrome/Edge profile as John, open the real Edit door, change
one harmless value, save, observe its truthful status, and verify DEV. Then restore the wording
through the normal audited workflow.

| Leaf / canary | Expected Edit behavior | Locked neighbor canary |
|---|---|---|
| skill name | Edit is available; saved plain text reaches DEV | skill ID / `@id` offers no Edit |
| alternate name | Edit is available when present | canonical crosswalk IRI offers no Edit |
| task name and description | Each authored scalar edits independently | task ID, module, category, Bloom value stay locked |
| subtask name | The authored name is its own edit leaf | subtask ID stays locked |
| subtask description | The authored description is its own edit leaf | survey/crosswalk structure stays locked |
| taxonomy introduction / note | Human-authored page wording edits normally | generated counts and structural labels stay locked |

Positive canary: a valid wording edit must save. Negative canary: a forged request against one
locked ID must fail, proving the absence check can catch a bypass. Paste hostile-looking plain text
(`<script>`, quotes, bidi controls, and markup punctuation) into a disposable leaf and confirm it is
either rejected by normalization or displayed inertly on every preview/history surface.

After DEV apply, both John and the review surface must say **Available on DEV — waiting for Publisher**
(or an equivalently explicit two-stage explanation). Confirm the anonymous public page
is unchanged. A save, Approver decision, page reload, timer tick, and preview preparation must each
leave PROD unchanged.

Repeat the core save/status journey at phone width, 200% zoom/large type, keyboard-only, and with a
screen-reader semantics inspection. Confirm focus returns after cancel/error and status changes are
announced without color dependence.

## Damien Publisher journey

1. Sign in through Access with human Publisher scope and open **Production Publisher** from review.
2. Confirm each cumulative per-source change renders as atomic semantic redlines: red struck
   deletion, blue underlined addition, and only conservatively detected green moved-from/moved-to.
   With color disabled, textual labels must retain the same meaning. Decide siblings Accept,
   Reject, Ask question, and unanswered; a question requires text and holds only itself.
3. Reload before submission and confirm actor-bound drafts survive. Cause one autosave failure,
   verify Submit remains blocked and Retry is explicit, then recover. Submit once and confirm the
   attributed immutable receipt records every answered decision without inferring unanswered as
   accepted. The browser must never possess or call the trusted preparation bearer.
4. Review the immutable prepared preview: every accepted operation/group, held exclusion,
   review receipt, attribution, count, target,
   base/candidate SHA, manifest/evidence/membership hashes, and active-lane state.
5. Confirm Approver-only, Admin-only, Editor, AI, cookie, and service-bearer identities cannot perform
   the human authorization action. Confirm replay of the identical request is idempotent and a stale
   or mutated binding fails closed.
6. Use the single explicit checkbox/button gesture to authorize the exact candidate. Confirm a later DEV
   edit is not added to it.
7. Under supervision, enable and run the executor only after the operations checklist passes. Verify
   its phase journal and provider receipts contain identifiers/hashes, not edited text or secrets.
8. Verify the **anonymous public** Pages copy and the **authenticated editor map** report the same
   candidate provenance before the release is called Published.
9. Exercise one partial-failure/restart canary and one recorded-pair restoration drill. Later releases
   must remain fenced until both targets match the recorded SHA.

Positive leak canary: put distinctive disposable text in an accepted operation and different
distinctive text in rejected, questioned, and unanswered siblings. PROD must contain the accepted
canary and none of the three held canaries. Then make a later same-source DEV edit and prove the
draft/submitted decision and unexecuted preview become stale rather than silently retargeting.

Run desktop, 480px, keyboard-only, color-disabled, punctuation-only, exact-move, ambiguous-move,
question-validation, stale-review, and failed-release recovery journeys in background/headless
Chrome. `HEADFUL=1` is human opt-in only; browser UAT must not take desktop focus by default.

## Evidence record

Record browser/version, viewport, Access actor, DEV apply batch IDs, prepared/authorized release ID,
base/candidate SHA, manifest hash, Pages deployment ID digest, Worker version ID digest, live
provenance results, restoration result, and clean-git proof. Mark every skipped live step **NOT RUN**,
never pass. Any new product choice goes to a new Decision Sheet; implementation defects belong in
code/tests and are fixed without re-asking Damien.
