# Supersession evidence for unmerged local branches

Audit date: 2026-09-02

Scope: the nine local branches named in the request, compared with the local `main` ref.

Method:

- Commit inventory is from `git log --oneline main..<branch>`.
- File inventory is from `git diff --stat main...<branch>`; the three-dot form uses each branch's merge base with `main`.
- `site/platform/**` generated mirrors and `build/**` are counted in diff totals but excluded from substantive-intent judgments.
- Mainline evidence is from `git show main:<path>`, line-numbered at the audited `main` snapshot, plus `git log main --oneline -S'<string>'`.
- Where stated, an aggregate stable patch ID was computed for the branch merge-base-to-tip diff and the single-parent mainline integration commit. Matching IDs prove identical aggregate textual changes even though commit SHAs differ.
- Recommendations are evidence dispositions only. No branch or stash was changed.

## codex/dev-streaming-u19

Merge base: `5ebfed54a265`.

Commits (1):

- `86d0f1c` Revert "docs(worker): describe dev streaming state"

Files touched (1; 2 insertions, 2 deletions):

```text
app/worker/wrangler.jsonc | 4 ++--
1 file changed, 2 insertions(+), 2 deletions(-)
```

Classification: **SUPERSEDED**

Evidence:

- The branch changes comments only. It changes “Enabled on DEV for U19 validation; production remains explicitly OFF” back to an older “Default OFF in every env” description while leaving top-level `"STREAMING": "true"` unchanged.
- Main intentionally enabled DEV streaming in `f1a147990befe2f967b117b1ff9ecc7f13aa7464` and merged that result in `719688c9631ec66a1dabb4fb418a5e8bc059c8fa`.
- Main subsequently expanded the implementation in `177f9ed90965f0c1f3945e8f084af95a0c2f73c6`, centralized it in `adf39615daea7297c026cfac601465cc2ae2d020`, and hardened failed streams in `80603bff2e4d350df9a1ebbbcf15946c33004e82`.
- Current `main:app/worker/wrangler.jsonc:93`-`100` describes Anthropic, OpenAI, and Google streaming, says DEV is enabled and production is off, and sets top-level `STREAMING` to `true`.
- Current `main:app/worker/wrangler.jsonc:271` separately sets production `STREAMING` to `false`.
- `git log main -S'Enabled on DEV for U19 validation' -- app/worker/wrangler.jsonc` traces the phrase through `5ebfed5` and later stream-hardening work. The branch's reverted comment is therefore stale documentation, not unlanded behavior.
- Matching stash: none named `workspace cleanup: dev-streaming-u19 2026-08-20`.

Recommendation: **delete** — its only delta is an obsolete comment reversal that contradicts current mainline behavior.

## codex/pitch-proof-u3

Merge base: `bd2d95795049`.

Commits (2):

- `053b32a` fix(pitch): restore concise visible narrative
- `8aade0d` feat(pitch): condense evidence behind proof disclosures

Files touched (2; 240 insertions, 82 deletions):

```text
site/index.html                  | 166 ++++++++++++++++++++-------------------
tools/tests/test_verify_pitch.py | 156 ++++++++++++++++++++++++++++++++++++
2 files changed, 240 insertions(+), 82 deletions(-)
```

Classification: **SUPERSEDED**

Evidence:

- The branch's intent is nine closed `THE PROOF` disclosures, concise visible narrative, an expand/collapse-all control, print expansion, and contract tests for disclosure placement and retained prose.
- Main landed the approved U3 implementation as `562ec707d41c083b84f854ab68294f1b58d39307`, merged by `b19d49f8d45e9976c8081d4c933e1d72fe8f63d4`, then hardened it in `63303985c6e6ac65c0664726c3489552ede42fb3` and `165fde5ee0642ca58101fed13b00b74055bf18ba`.
- Current `main:site/index.html:247`-`250` defines the proof toggle, disclosure styling, focus treatment, and forced print visibility.
- Current `main:site/index.html:287` exposes the accessible “Expand all sections” control.
- Current `main:site/index.html:293`, `312`, `359`, `419`, `436`, `449`, `503`, `544`, and `574` contains exactly nine `THE PROOF` summaries.
- Current `main:site/index.html:666` updates “Expand all sections” / “Collapse all sections”; `main:site/index.html:674`-`678` preserves disclosure state across print.
- Current `main:tools/tests/test_verify_pitch.py:206`-`212` verifies nine closed, unique, direct-child disclosures and the approved 55%-65% prose-retention band.
- Current `main:tools/tests/test_verify_pitch.py:215`-`267` verifies proof containment and mutation canaries for missing disclosures, print rules, and the expand-all control.
- `git log main -S'proof-toggle' -- site/index.html tools/tests/test_verify_pitch.py` identifies `562ec70` and later `165fde5`; `git log main -S'THE PROOF · 19,077 attorneys surveyed'` identifies `562ec70` and `6330398`.
- The branch aggregate patch ID (`fe17dcc1a65b545c4e83693252e4dbb05c1bde39`) differs from the approved U3 aggregate (`594c9592eec94bddc3a05a06733403cd40bf8ae9`), showing this was not the exact landed draft; the cited current files prove the same intent landed in the approved form.
- Matching stash: `stash@{1}` / `d6c9de5ee7c4f1def277e8a4330490f877691fd0`; it changes only `site/platform/data/.build-stamp.json` `git_base_sha` from `ed914e33b2934f7739631d0ad0b204d4299d9474` to branch tip `053b32a47a0ccb0a93c039e7b377365e4d55fe5e`.

Recommendation: **delete** — the approved, subsequently hardened version is on `main`; the exact matching stash is disposable provenance churn.

## codex/student-view-u20

Merge base: `67cdbd672a21`.

Commits (1):

- `869c225` fix(editor): protect student view injection

Files touched (2; 66 insertions, 10 deletions):

```text
app/worker/src/editor-inject.js       |  8 +++++
app/worker/test/editor-inject.test.js | 68 +++++++++++++++++++++++++++++------
2 files changed, 66 insertions(+), 10 deletions(-)
```

Classification: **SUPERSEDED**

Evidence:

- The branch strips upstream JSON `<script>` nodes whose IDs collide with Worker-owned `editor-map-data` or `edits-data`, and tests that forged values cannot replace the safe student URL.
- Main first landed the student-view capability in `67cdbd672a218c678877218534e8294c0e468d66`, merged by `6369cd3710694d9ef45d2c998fc1579e86a355e0`.
- Main then replaced the branch's script-only deletion with the broader fix `acb0b3c25617c1c80a6302d91b927aec9fcaae76`.
- Current `main:app/worker/src/editor-inject.js:178`-`185` defines `ReservedStateStripper`, which removes reserved IDs from any upstream element without deleting its content.
- Current `main:app/worker/test/editor-inject.test.js:69`-`80` proves collisions are neutralized case-insensitively, content survives, and unrelated IDs remain intact.
- Current `main:app/worker/test/editor-inject.test.js:175`-`255` exercises forged JSON scripts plus non-script head/body collisions and proves the Worker emits exactly one trusted state island of each kind without leaking query secrets.
- The main fix is strictly broader and less destructive than the branch: it covers all `[id]` collisions rather than only JSON scripts and preserves upstream structure.
- The differing stable patch IDs (`0aeca62be746dc21585456b0fc715213593478b4` for the branch; `31876d5b2f0aa27c97d180f3d27e363ff2f6581c` for `acb0b3c`) confirm replacement rather than a hidden cherry-pick.
- Matching stash: none named `workspace cleanup: student-view-u20 2026-08-20`.

Recommendation: **delete** — main contains the same security intent in a broader, reviewed implementation.

## feat/identity-rights

Merge base: `8553703ca7e2`.

Commits (5):

- `846e17f` fix(accessibility): add license page headings (#8)
- `78e6a5a` fix(review): harden identity and rights contracts
- `1c4fdd6` refactor(rights): share identity and about-page sources
- `182ba59` feat(rights): layer CC BY content beside MIT code
- `df02846` feat(identity): adopt Legal Practicum identity contract

Files touched (91; 1,157 insertions, 488 deletions):

- Substantive files (16; 381 insertions, 57 deletions):
  - `CONTENT-LICENSE.md`, `LICENSE`, `README.md`, `THIRD-PARTY.md`
  - `app/chat/critique.html`, `app/chat/index.html`, `app/worker/personas/personas.generated.json`
  - `data/copy/home.json`, `data/jurisdictions/meridian.json`, `data/schemas/page-copy.schema.json`
  - `docs/content-style-guide.md`, `site/index.html`, `tools/build_site.py`
  - `tools/tests/fixtures/platform-semantic-baseline.json`
  - `tools/tests/test_identity_rights_contract.py`, `tools/tests/test_platform_browser_matrix.py`
- Generated mirrors: 75 files under `site/platform/**`.
- Full three-dot summary: `91 files changed, 1157 insertions(+), 488 deletions(-)`.

Classification: **SUPERSEDED**

Evidence:

- Main commit `5aaf7ef8bf27e2c934991574972efe7bde35b3e4` has parent `8553703ca7e2acaab366abd74e1015e80c80fd3f`, the same merge base used by this branch.
- The aggregate stable patch ID for `8553703ca7e2acaab366abd74e1015e80c80fd3f..846e17fb70ee7eb4dfc9bd0e77ddb005371e46f8` and for `5aaf7ef^..5aaf7ef` is identical: `3e88f709c6609b34ea0c96c30cf11c3fdea2b49b`.
- The main commit's 91-file stat is also identical to the branch three-dot stat. This is direct evidence that the entire branch result was squash-landed as `5aaf7ef` (“feat(identity): publish Legal Practicum identity and rights (#8)”).
- Current `main:CONTENT-LICENSE.md:1`-`12` names the Legal Practicum content license and places CC BY 4.0 beside, not in place of, MIT.
- Current `main:LICENSE:23`-`26` limits MIT to software and points educational content to `CONTENT-LICENSE.md`.
- Current `main:data/copy/home.json:5`-`8` contains the canonical title, byline, and host.
- Current `main:tools/build_site.py:553`-`570` reads the shared identity source; `main:tools/build_site.py:613`-`619` emits both content and code license links.
- Current `main:tools/tests/test_identity_rights_contract.py:67`-`85` enforces the canonical title/byline across fresh generated pages.
- `git log main -S'Legal Practicum'` and `git log main -S'CONTENT-LICENSE.md'` both identify `5aaf7ef` as the landing commit.
- Matching stash: none named `workspace cleanup: identity-rights 2026-08-20`.

Recommendation: **delete** — the branch's complete aggregate patch is already represented by one mainline squash commit.

## feat/platform-contract

Merge base: `e4a36130503d`.

Commits (16):

- `21bcf5b` chore(build): refresh platform provenance
- `933cea3` fix(todo): honor schedule and reminder state contracts
- `c84cad5` chore(build): refresh platform provenance
- `c3d357b` fix(review): harden catalog and archive release gates
- `f899531` fix(catalog): sanction public source link
- `7a50b20` refactor(catalog): unify state and history projections
- `a198dc5` fix(catalog): close real-box accessibility gaps
- `2f4454a` fix(catalog): preserve editor and parity contracts
- `6eda4c1` feat(catalog): ship scalable histories and downloads
- `e9e20a4` feat(platform): lock teaching and routing contracts
- `8b281df` fix(todo): make the reminder actually survive its own environment
- `3c49a05` docs(decisions): reconcile the Aug 6 record against the call recording
- `ec1bec7` feat(todo): repo-tracked task list with a scheduled reminder
- `34ad1dc` fix(site): hero reads "next generation of lawyers"
- `9d66023` docs(decisions): record the Aug 6 outcomes with John
- `8bfe6b7` docs(decisions): decision sheet for the Aug 6 meeting with John

Files touched (146; 4,277 insertions, 1,862 deletions):

- Substantive files (42; 2,472 insertions, 156 deletions):
  - `app/worker/personas/personas.generated.json`, `app/worker/src/index.js`, `app/worker/src/validate.js`
  - `app/worker/test/editor-direct-apply.test.js`, `app/worker/test/editor-map.test.js`, `app/worker/test/platform-language-contract.test.js`
  - `data/copy/home.json`; `data/curriculum/m1.md`, `m2.md`, `m3.md`
  - Six curriculum templates: `client-interview-plan.md`, `engagement-letter-checklist.md`, `learning-portfolio.md`, `reflective-report.md`, `ssnp.md`, `time-sheet.md`
  - Seven exercise records: `m01`, `m02`, `m03`, `m05`, `m11`, `m12`, and `m15` `exercise/exercise.json`
  - `docs/TODO.md`, `docs/content-style-guide.md`
  - `docs/decisions/2026-08-06-john-meeting-decision-sheet.html`, `docs/decisions/2026-08-06-john-meeting-outcomes.md`
  - `docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md`
  - `site/index.html`, `tools/build_site.py`, `tools/install-todo-timer.sh`, `tools/platform_browser_matrix.json`, `tools/preflight.sh`, `tools/student_archives.py`
  - `tools/tests/fixtures/platform-semantic-baseline.json`, `tools/tests/test_catalog_contract.py`, `tools/tests/test_platform_browser_matrix.py`, `tools/tests/test_platform_language_contract.py`, `tools/tests/test_todo_report.py`
  - `tools/todo_report.py`, `tools/verify_catalog_client.js`, `tools/verify_platform_layout.js`
- Generated mirrors: 104 files under `site/platform/**`, including catalog data/pages and student ZIPs.
- Full three-dot summary: `146 files changed, 4277 insertions(+), 1862 deletions(-)`.

Classification: **SUPERSEDED**

Evidence:

- Main commit `a869c61c90a19a63c79bc3390e5764b3d851da22` has parent `e4a36130503d67b0dee61f5231f3ae409fcef39a`, exactly the branch merge base.
- The aggregate stable patch ID for the branch and `a869c61^..a869c61` is identical: `0ca08217dc900abb7e417c2f60940cdc71d92935`.
- The main commit's 146-file stat is identical to the branch three-dot stat. The full branch result was squash-landed as `a869c61` (“feat(platform): lock teaching contracts and scale matter catalog (#9)”).
- Current `main:tools/student_archives.py:10`-`38` defines a fail-closed student-safe allowlist and rejects instructor files, symlinks, and escaping members.
- Current `main:tools/tests/test_catalog_contract.py:103`-`119` requires histories, pagination, one free student ZIP action, and the sanctioned public source link.
- Current `main:tools/verify_catalog_client.js:13`-`61` exercises a 60-item paginated history catalog, query preservation, focus, and back navigation.
- Current `main:app/worker/test/platform-language-contract.test.js:12`-`27` rejects alumni routing while keeping ordinary learner-result requests valid.
- Current `main:docs/TODO.md:260`-`275` documents the repo-tracked reminder and dedupe contract.
- Current `main:docs/decisions/2026-08-06-john-meeting-outcomes.md:14`-`24` preserves the recording-backed corrections that governed the branch.
- Some copy has continued to evolve after landing; for example `d57da2599403e64a6b6c62dcc671c907169f657e` adopted trusted-advisor language. That evolution does not restore uniqueness to the already-landed branch.
- Matching stash: none named `workspace cleanup: platform-contract 2026-08-20`.

Recommendation: **delete** — the complete aggregate patch is already on `main` as a squash commit.

## feat/seven-point-assessment

Merge base: `7d22ad64c861`.

Commits (1):

- `d76c268` feat(assessment): add portable seven-point evaluation (U6)

Files touched (150; 3,902 insertions, 359 deletions):

- Substantive files (35; 2,524 insertions, 157 deletions):
  - `app/chat/critique.js`, `app/worker/API-CONTRACTS.md`, `app/worker/personas/personas.generated.json`, `app/worker/prompts/critique-template.md`
  - `app/worker/src/assessment.js`, `app/worker/src/index.js`, `app/worker/src/prompts.js`, `app/worker/src/validate.js`
  - `app/worker/test/assessment.test.js`, `app/worker/test/prompts.test.js`
  - `data/assessment/README.md`, `data/assessment/default.json`
  - All 20 `data/matters/*/rubric.json` files
  - `data/schemas/assessment-config.schema.json`, `data/schemas/rubric.schema.json`
  - `tools/validate_spine.py`
- Generated mirrors: 115 files under `site/platform/**`, including mirrored rubrics and rebuilt ZIPs.
- Full three-dot summary: `150 files changed, 3902 insertions(+), 359 deletions(-)`.

Classification: **PARTIALLY UNIQUE**

Evidence already on main:

- Main implements a 1-7, seven-heading memo instrument in `f43f168286e09a95ce39ae51e971744e5dde01e0`, score bands in `9db483b6356ece0088f195fc1871794dc23d9495`, evaluator validation in `569c0884607ad575f8e793c968e14f4e7b3fca26`, and local threshold precedence in `2076e685bfcb8da686d2ca9dd54fb577ea70afa8`.
- Current `main:data/schemas/assessment-instrument.schema.json:33`-`47` fixes the seven memo headings; `:49`-`71` fixes integer bands 1-7.
- Current `main:data/schemas/assessment-instrument.schema.json:74`-`88` sets competence 4, redo below 6, and instructor-over-school-over-default precedence.
- Current `main:data/schemas/assessment-instrument.schema.json:99`-`133` routes memo work to the seven-heading contract and preserves weighted rubrics only for non-memo/legacy work.
- Current `main:app/worker/src/assessment-config.js:93`-`113` resolves the closed request configuration fail-closed with instructor precedence.
- Current `main:app/worker/src/assessment-view.js:105`-`138` explains canonical/local thresholds, distinguishes competence from redo eligibility, and labels local authority as unverified.

Work not on main, and validity:

- `app/worker/src/assessment.js`, `app/worker/test/assessment.test.js`, `data/assessment/default.json`, and `data/schemas/assessment-config.schema.json` do not exist on `main`.
- The `assessment` objects added to all 20 `data/matters/*/rubric.json` files and the corresponding `data/schemas/rubric.schema.json` hunk are absent on `main`; current representative `main:data/matters/m01-arbitration-meridian/rubric.json:90`-`97` ends with `letter_grade_map`, not branch-authored seven-band/critical-failure mappings.
- The aggregate-score/config-import integration hunks in `app/chat/critique.js`, `app/worker/API-CONTRACTS.md`, `app/worker/personas/personas.generated.json`, `app/worker/prompts/critique-template.md`, `app/worker/src/index.js`, `app/worker/src/prompts.js`, `app/worker/src/validate.js`, `app/worker/test/prompts.test.js`, and `tools/validate_spine.py` are likewise not the current mainline design.
- These unmatched hunks are obsolete, not viable unique work. `main:docs/decisions/2026-08-17-seven-point-recording-evidence.md:48`-`53` says not to merge this branch as written because it converts each matter's aggregate weighted total into one result and loses section-level scoring/redo semantics.
- `main:docs/decisions/2026-08-20-assessment-map-and-promotion-retirement.md:9`-`27` authoritatively chooses seven memo headings, section-level 1-7 scores, competence 4, redo below 6, and preserves weighted rubrics only for non-memo/legacy assessment.
- That same decision at `main:docs/decisions/2026-08-20-assessment-map-and-promotion-retirement.md:29`-`32` calls the old branch rejected implementation evidence and explicitly says it is not a merge source.
- Matching stash: none named `workspace cleanup: seven-point-assessment 2026-08-20`.

Recommendation: **delete** — the salvageable configuration/presentation intent landed after the assessment mapping was corrected; the remaining unique aggregate-score implementation is explicitly rejected.

## fix/editor-uat

Merge base: `a869c61c90a1`.

Commits (5):

- `964c29b` docs(editor): record restored-draft review fix
- `7cf97ab` fix(editor): keep restored draft status authoritative (#10)
- `b795e83` docs(editor): record autonomous UAT outcomes
- `5706ae5` fix(editor): gate accessible editing surfaces
- `6048ae5` fix(editor): recover drafts and rejected edits safely

Files touched (9; 329 insertions, 31 deletions):

- Substantive files (8; 328 insertions, 30 deletions):
  - `app/editor/editor.css`, `app/editor/editor.js`, `app/editor/test-harness.html`, `app/editor/verify-editor.js`
  - `docs/decisions/2026-08-07-editor-uat-decision-sheet.md`
  - `docs/dogfood-reports/2026-08-07-fix-editor-uat-dogfood.md`
  - `docs/editor-guide-for-john.md`, `tools/a11y_audit.js`
- Generated mirror/provenance: `site/platform/data/.build-stamp.json`.
- Full three-dot summary: `9 files changed, 329 insertions(+), 31 deletions(-)`.

Classification: **SUPERSEDED**

Evidence:

- Main commit `c84bda881e3397a58796b109f6c231975cc89eb8` has parent `a869c61c90a19a63c79bc3390e5764b3d851da22`, exactly this branch's merge base.
- The aggregate stable patch ID for the branch and `c84bda8^..c84bda8` is identical: `1c110b0366ec8551dc243689f2f30ab5e6529b8e`.
- The main commit's nine-file stat is identical. The complete branch was squash-landed as `c84bda8` (“fix(editor): make copy editing recoverable and accessible (#10)”).
- Current `main:app/editor/editor.js:332`-`343` restores a valid unsent draft with its original id and an explicit “Draft restored — not sent yet” status.
- Current `main:app/editor/editor.js:1389`-`1401` removes rejected stale wording while preserving inline markup.
- Current `main:app/editor/editor.js:1968`-`1979` prevents an older pending overlay from replacing the restored-draft warning.
- Current `main:app/editor/verify-editor.js:389`-`424` verifies refresh recovery, local-mirror recovery, id preservation, and restored-draft status authority.
- Current `main:docs/editor-guide-for-john.md:68` documents the restored-draft user contract.
- `git log main -S'restored draft' -- app/editor docs/editor-guide-for-john.md` identifies `c84bda8`.
- Matching stash: none named `workspace cleanup: editor-uat 2026-08-20`.

Recommendation: **delete** — the branch's complete aggregate patch is already on `main`.

## fix/hero-copy-lawyers

Merge base: `8553703ca7e2`.

Commits (1):

- `10ecd35` fix(site): hero reads "next generation of lawyers"

Files touched (1; 1 insertion, 1 deletion):

```text
site/index.html | 2 +-
1 file changed, 1 insertion(+), 1 deletion(-)
```

Classification: **SUPERSEDED**

Evidence:

- The sole hunk changes the hero noun from “advocates” to “lawyers.”
- The same textual hunk exists in branch commit `34ad1dc2dd011770b24b295d3e421e43b53b9c38` and was included in mainline squash commit `a869c61c90a19a63c79bc3390e5764b3d851da22`.
- Current `main:site/index.html:281` still reads `Training the next generation` / `lawyers.`; the branch's exact user-visible intent remains present.
- Later commit `d57da2599403e64a6b6c62dcc671c907169f657e` evolved the lede to “Training the next generation of lawyers as trusted advisors” at `main:site/index.html:282` without undoing the heading.
- A `-S` search for the exact HTML fragment `of <span class="em">lawyers.</span>` traces the noun change on mainline; current-file evidence independently confirms it.
- Matching stash: none named `workspace cleanup: hero-copy-lawyers 2026-08-20`.

Recommendation: **delete** — the one-line copy correction is on `main` and has since been extended consistently.

## plan/aug6-implementation-wave

Merge base: `8553703ca7e2`.

Commits (23):

- `684669e` docs(plan): map August decision implementation waves
- `49492a8` docs(handoff): session handoff for the Codex transition
- `e327fb1` chore: ignore .worktrees/, land the promotion-summary plan doc
- `f6dbafe` fix(todo): make the reminder actually survive its own environment
- `adee256` docs(decisions): reconcile the Aug 6 record against the call recording
- `4023aff` feat(todo): repo-tracked task list with a scheduled reminder
- `4842f6b` fix(site): hero reads "next generation of lawyers"
- `8390dd1` docs(decisions): record the Aug 6 outcomes with John
- `e66c19c` docs(decisions): decision sheet for the Aug 6 meeting with John
- `c96cfbb` fix(review): apply review findings
- `1e05388` refactor(promotion): simplify verified lifecycle paths
- `731e719` test(worker): stabilize cross-language parity gate
- `97f8e64` test(editor): parallelize parity digests
- `c7b015b` test(editor): decouple map contracts from corpus shape
- `67725fe` feat(promotion): enforce measured rollout authority
- `7142494` feat(promotion): add config-off PROD operations
- `343a671` feat(editor): expose promotion lifecycle experience
- `72f6337` feat(promotion): add crash-safe publication saga
- `edc722a` feat(editor): secure bound promotion previews
- `4395e6b` feat(promotion): add bounded hybrid risk policy
- `49df089` feat(promotion): prepare isolated release candidates
- `a5526b3` feat(editor): add durable PROD promotion ledger
- `f4f779d` docs(plan): define safe PROD editor promotion

Files touched (58; 7,570 insertions, 83 deletions):

- Substantive files (56; 7,536 insertions, 82 deletions):
  - `.gitignore`; `app/editor/editor.css`; `app/editor/editor.js`; `app/worker/API-CONTRACTS.md`
  - Worker source: `editor-assets.js`, `editor-endpoints.js`, `editor-http.js`, `editor-inject.js`, `editor-review.js`, `editor-status.js`, `editor-store-core.js`, `editor-store.js`, `editor.js`
  - Worker tests: `editor-direct-apply.test.js`, `editor-map.test.js`, `editor-norm-parity.test.js`, `editor-promotion-endpoints.test.js`, `editor-promotion-preview.test.js`, `editor-promotion-store.test.js`, `editor-promotion-ui.test.js`, `editor-sql-helper.mjs`
  - `app/worker/wrangler.jsonc`, `deploy/deploy-prod.sh`, `docs/TODO.md`
  - `docs/decisions/2026-08-06-john-meeting-decision-sheet.html`, `docs/decisions/2026-08-06-john-meeting-outcomes.md`
  - `docs/direct-apply-daemon.md`, `docs/editor-guide-for-john.md`, `docs/handoffs/2026-08-06-john-decisions-and-todo-system.md`
  - Plans: `2026-08-05-001-feat-prod-editor-promotion-plan.md`, `2026-08-05-002-feat-cockpit-sonsteng-promotion-summary-plan.md`, `2026-08-06-001-feat-august-decision-wave-plan.md`
  - `docs/prod-enable.md`, `docs/solutions/orchestration/2026-08-06-systemd-timers-in-a-path-with-a-space.md`, `site/index.html`
  - Tools: `apply_suggestions.py`, `direct_apply_daemon.py`, `editorial_pass.py`, `install-apply-daemon.sh`, `install-prod-promotion-daemon.sh`, `install-todo-timer.sh`, `prod_promotion.py`, `prod_promotion_daemon.py`
  - Tool tests: `test_editorial_pass.py`, `test_prod_candidate_builder.py`, `test_prod_promotion_ai.py`, `test_prod_promotion_daemon.py`, `test_prod_promotion_install.py`, `test_prod_promotion_live.py`, `test_prod_promotion_policy.py`, `test_prod_promotion_reconcile.py`, `test_prod_promotion_release.py`, `test_prod_promotion_revert.py`, `test_prod_promotion_rollout.py`, `test_todo_report.py`
  - `tools/todo_report.py`
- Ignored generated mirrors: two files under `site/platform/**` (`data/.build-stamp.json` and `data/api-contracts.md`).
- Full three-dot summary: `58 files changed, 7570 insertions(+), 83 deletions(-)`.

Classification: **PARTIALLY UNIQUE**

Evidence already on main:

- The Aug 6 decision sheet/outcomes, TODO/reminder system, catalog/platform changes, and hero correction were included in mainline squash `a869c61c90a19a63c79bc3390e5764b3d851da22`; the two decision documents are byte-identical at current `main`.
- Current `main:docs/TODO.md:260`-`275` contains the reminder contract, and `main:tools/todo_report.py:1`-`24` contains its parser/notifier contract.
- Main rescued the three orphaned plan documents in `ef0d5f3c89dcf8dfcbd9fdc268749a65f185ea57` instead of merging the implementation branch.
- Current `main:docs/plans/2026-08-05-001-feat-prod-editor-promotion-plan.md:13` explicitly marks that plan superseded and retained only as history.
- Current `main:docs/plans/2026-08-06-001-feat-august-decision-wave-plan.md:13` records its provenance and the then-unbuilt wave without treating this branch as live implementation authority.
- Current `main:.gitignore:11` contains the `.worktrees/` exclusion; the associated housekeeping intent is not unique.

Work not on main, and validity:

- The following branch-added implementation files are absent on `main`: `app/worker/test/editor-promotion-endpoints.test.js`, `app/worker/test/editor-promotion-preview.test.js`, `app/worker/test/editor-promotion-store.test.js`, `app/worker/test/editor-promotion-ui.test.js`, `tools/install-prod-promotion-daemon.sh`, `tools/prod_promotion.py`, `tools/prod_promotion_daemon.py`, and the ten `tools/tests/test_prod_*` / `test_prod_candidate_builder.py` files listed above.
- `docs/handoffs/2026-08-06-john-decisions-and-todo-system.md` is also absent on `main`; its transition instructions are historical and predate the settled replacement.
- Promotion-specific hunks are not present as written in `app/editor/editor.css`, `app/editor/editor.js`, `app/worker/API-CONTRACTS.md`, all nine changed `app/worker/src/editor-*.js` / `editor.js` files, the four pre-existing worker tests (`editor-direct-apply`, `editor-map`, `editor-norm-parity`, `editor-sql-helper`), `app/worker/wrangler.jsonc`, `deploy/deploy-prod.sh`, `docs/direct-apply-daemon.md`, `docs/editor-guide-for-john.md`, `docs/prod-enable.md`, `tools/apply_suggestions.py`, `tools/direct_apply_daemon.py`, `tools/editorial_pass.py`, `tools/install-apply-daemon.sh`, and `tools/tests/test_editorial_pass.py`.
- Those unmatched hunks implement automatic/confidence-based promotion, bounded AI risk adjustment, timed rollout, and an autonomous promotion daemon. Branch `tools/prod_promotion.py:60`-`177` defines automatic thresholds, AI caps, and rollout requirements including zero false automatic promotions and a five-minute target.
- Repository authority explicitly retires that work: `main:docs/decisions/2026-08-20-assessment-map-and-promotion-retirement.md:34`-`40` says the automatic confidence/eligibility contract is retired and porting it into the current ledger is unauthorized.
- The same decision at `main:docs/decisions/2026-08-20-assessment-map-and-promotion-retirement.md:42`-`52` records final tip `49492a8e1c1d2bded8ab4b0fdfe0bf3f666fb18`, says the refs were deleted, and preserves lineage without making it current product authority.
- Main instead implements a human Publisher ledger: `main:app/worker/src/editor-endpoints.js:927`-`981` requires a human Publisher using Access for review and authorization.
- `main:docs/plans/2026-08-09-001-feat-taxonomy-publisher-batches-plan.md:21`-`34` separates approval from human-authorized release, selectively ports only durable primitives, and explicitly removes confidence-based/timed automatic publication.
- `main:docs/evidence/2026-08-09-editor-publication-baseline.md:38`-`48` characterizes this branch as dormant evidence and limits reuse to selective primitives under the Publisher contract.
- Matching stash: none named `workspace cleanup: aug6-implementation-wave 2026-08-20`.

Recommendation: **delete** — its live decision/TODO/plan material is preserved on `main`, while its unmatched promotion machinery is explicitly retired and unauthorized for porting.

## Summary

| Branch | Classification | Recommendation |
|---|---|---|
| `codex/dev-streaming-u19` | SUPERSEDED | delete |
| `codex/pitch-proof-u3` | SUPERSEDED | delete |
| `codex/student-view-u20` | SUPERSEDED | delete |
| `feat/identity-rights` | SUPERSEDED | delete |
| `feat/platform-contract` | SUPERSEDED | delete |
| `feat/seven-point-assessment` | PARTIALLY UNIQUE — unmatched work explicitly rejected/obsolete | delete |
| `fix/editor-uat` | SUPERSEDED | delete |
| `fix/hero-copy-lawyers` | SUPERSEDED | delete |
| `plan/aug6-implementation-wave` | PARTIALLY UNIQUE — unmatched work explicitly retired/obsolete | delete |

## Stash evidence

Exact matching was performed against `workspace cleanup: <branch-suffix> 2026-08-20` in the current `git stash list`.

| Stash | Branch | Content | Recommendation |
|---|---|---|---|
| none found | `codex/dev-streaming-u19` | No exact `dev-streaming-u19` cleanup stash exists. | no action |
| `stash@{1}` (`d6c9de5ee7c4f1def277e8a4330490f877691fd0`) | `codex/pitch-proof-u3` | Only `site/platform/data/.build-stamp.json`: one provenance SHA replacement to `053b32a47a0ccb0a93c039e7b377365e4d55fe5e`; nothing beyond build-stamp/provenance churn. | drop |
| none found | `codex/student-view-u20` | No exact `student-view-u20` cleanup stash exists. | no action |
| none found | `feat/identity-rights` | No exact `identity-rights` cleanup stash exists. | no action |
| none found | `feat/platform-contract` | No exact `platform-contract` cleanup stash exists. | no action |
| none found | `feat/seven-point-assessment` | No exact `seven-point-assessment` cleanup stash exists. | no action |
| none found | `fix/editor-uat` | No exact `editor-uat` cleanup stash exists. | no action |
| none found | `fix/hero-copy-lawyers` | No exact `hero-copy-lawyers` cleanup stash exists. | no action |
| none found | `plan/aug6-implementation-wave` | No exact `aug6-implementation-wave` cleanup stash exists. | no action |

Nearby but non-matching entry: `stash@{0}` is named for `codex/pitch-proof-u3-final`, a different branch not in this audit. It contains substantive pitch/test edits and was not treated as the matching stash for `codex/pitch-proof-u3`.
