# Real-browser Editor and Publisher UAT matrix

All browser checks run headless or on an isolated display. They never fall back to a foreground
browser. Before backfill, record the editing Worker's prior version, prove old-version reads of the
migrated store, activate/read back the old and new versions, and smoke the Publisher/status API while
the release executor remains config-off. A failed rollback proof blocks backfill.

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

## Evidence record — 2026-08-17 background pass

This record covers the autonomous, non-credentialed preparation leg only. It does not claim that a
human Publisher reviewed or authorized a candidate, that production changed, or that the recovery
drill ran.

| Field | Evidence |
|---|---|
| Observed at | `2026-08-17T15:33:29-05:00` |
| Browser | Chromium `151.0.7922.108` (snap), headless |
| Editor viewports | Desktop; Large Type / 200%; mobile `390x844` |
| Publisher harness viewport | Puppeteer default `800x600`; keyboard submission path included |
| Editor browser matrix | `89/89 PASS` via `EDITOR_HEADLESS=1 HEADLESS=1 node app/editor/verify-editor.js` |
| Publisher browser contract | `PUBLISHER CLIENT PASS` via `node tools/verify_publisher_client.mjs` |
| Publisher Worker contracts | `editor-publisher-ui`, `editor-publisher-review`, and `editor-publisher-release`: `3/3 PASS` |
| Screenshot digest — desktop | `sha256:d6ec99d57e1df04bf359279c6ce16edab174bedc09c8fa6856936eb65056e826` |
| Screenshot digest — Large Type | `sha256:d6ec99d57e1df04bf359279c6ce16edab174bedc09c8fa6856936eb65056e826` |
| Screenshot digest — mobile | `sha256:aba7946f10ee772463ba0a57f3e000ac3a0c7e4f2be78e340fc43e743f295033` |
| Screenshot retention | Disposable local files removed after digest capture; no edited content retained |
| Clean-git proof | canonical `main` clean at `0a193f6bbbefb4045b4b50551c1c7de7acae78e6` |
| Access actor | **NOT RUN** — requires Damien's authenticated Publisher session |
| DEV apply batch IDs | **NOT RUN** — no live edit was made in this background pass |
| Prepared release ID | **NOT RUN** |
| Authorized release ID | **NOT RUN** |
| Base/candidate SHA | **NOT RUN** |
| Manifest/evidence/membership hashes | **NOT RUN** |
| Pages deployment ID digest | **NOT RUN** |
| Worker version ID digest | **NOT RUN** |
| Live provenance result | **NOT RUN** |
| Exact-pair restoration result | **NOT RUN** |

The next step is the human leg: Damien reviews the one reconciled backfilled revision, submits the
decision, and authorizes the resulting immutable candidate. The supervised process-scoped canary
and exact-pair recovery drill remain required afterward; production stays config-off until both are
recorded.

## Evidence record — 2026-08-21 post-merge autonomous recheck

This record rechecks the merged implementation and DEV deployment. It does not upgrade any human or
production field from **NOT RUN**.

| Field | Evidence |
|---|---|
| Observed at | `2026-08-21T12:17:13-05:00` |
| Merged source exercised | `d18b657ba010cf800f1cd5faafad50d20bc5ed04` |
| DEV Worker version | `5ae43990-84b2-4772-9cde-73bde49246f7` |
| DEV generated build ID | `25fe75a7b465b205` |
| Editor browser matrix | `89/89 PASS` via `EDITOR_HEADLESS=1 HEADLESS=1 node app/editor/verify-editor.js` |
| Editor viewports | Desktop; Large Type / 200%; mobile `390x844` |
| Publisher browser contract | `PUBLISHER CLIENT PASS` via `node tools/verify_publisher_client.mjs` |
| Screenshot digest — desktop | `sha256:45a1135861b9cc05acfc0853479c234e831a49a2df285d0840a328742a0bb8ec` |
| Screenshot digest — Large Type | `sha256:45a1135861b9cc05acfc0853479c234e831a49a2df285d0840a328742a0bb8ec` |
| Screenshot digest — mobile | `sha256:772a507136ba2f8724f55e57349f7d82d920daedbba9ceeb14edee9c94edf52b` |
| Screenshot retention | Disposable local files moved to Trash after digest capture; no edited content retained in Git |
| Access door | Unauthenticated request redirects to Cloudflare Access |
| Access actor | **NOT RUN** — authenticated Damien session still required |
| Live edit and DEV apply batch IDs | **NOT RUN** — no live authored edit was made |
| Prepared / authorized release ID | **NOT RUN** |
| Production provider IDs and provenance | **NOT RUN** |
| Production canary and exact-pair restoration | **NOT RUN** |

Production remains configuration-off. The new repository-only streaming smoke, Day Zero migration
rehearsal, and legacy-environment migrator are preparation evidence only; they do not substitute for
the credentialed or supervised rows above.

## Evidence record — 2026-08-23 domain cutover

This record covers the autonomous U10 infrastructure leg. It does not claim the
human-authenticated edit that still requires an allowlisted identity session.

| Field | Evidence |
|---|---|
| Public property | `https://legalpracticum.org/platform/` returned HTTP 200 from Cloudflare Pages |
| DEV Worker version | `cc86efd2-636d-4823-be8e-a07810487bbf` |
| New Access door | Unauthenticated `/edit/v1/status` returned 302 to the Access team login |
| Access policy parity | Same IdP, one allow policy, three email selectors, `730h` session |
| Old Access application | Retired; API returns unknown application |
| Legacy editor host | HTTP 308 to `edit.legalpracticum.org`, path and query preserved |
| Public aliases | `www.legalpracticum.org` and `sonsteng.damienriehl.com` return path/query-preserving HTTP 308s to the apex |
| Production release config | Provenance URL points to `legalpracticum.org`; release remains config-off |
| Repository verification | Full preflight: 21 passed, 0 failed, 0 skipped |
| Access actor | **NOT RUN** — requires an allowlisted human identity session |
| Authenticated suggestion round-trip | **NOT RUN** — queued in the domain-cutover human gate sheet |
