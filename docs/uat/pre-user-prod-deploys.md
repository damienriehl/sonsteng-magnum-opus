# Pre-user production deploy record

Procedure: `docs/pre-user-prod-deploy.md`. Authority: Q1 in
`docs/decisions/2026-09-02-resume-and-uat-decision-sheet.md`. One block per deploy, newest last.

```
Date (UTC): 2026-09-02 16:10–16:20
Candidate SHA: edd940634fb930bbb0a30c74a50b9d9df8dec38d (main after PR #32)
Previous Worker version / Pages deployment: 2f49415a-db5f-4a31-9d16-fe5f5015a6bd / 12465c64-8c97-4420-87ae-3b222c9f6eef (source 6837ae9)
New Worker version / Pages deployment: c788b114-f643-479c-989b-a65a7f2119a5 / 3fe768ba (https://3fe768ba.sonsteng.pages.dev)
Worker provenance: 204 + sha
Pages provenance: 200 + sha on / and /platform/
DEV/production parity: SAME except build stamp (/, /platform/, /platform/matters/, /platform/skills/)
Operator: orchestrating agent under Damien's 2026-09-02 authority
```

```
Date (UTC): 2026-09-02 17:55–18:00
Candidate SHA: 0643a54924b3fdd082f74944d131753de0dc4c06 (main after PR #34)
Previous Worker version / Pages deployment: c788b114-f643-479c-989b-a65a7f2119a5 / 3fe768ba
New Worker version / Pages deployment: cb2e029d-5e75-4cfe-83f7-62687581ee98 / dbff9a7b (https://dbff9a7b.sonsteng.pages.dev)
Worker provenance: 204 + sha (after propagation)
Pages provenance: 200 + sha on / and /platform/
DEV/production parity: DEV redeployed from origin/main by deploy/deploy-dev.sh in the same window
Operator: orchestrating agent under Damien's 2026-09-02 authority
```

```
Date (UTC): 2026-09-02 20:20–20:24
Candidate SHA: 76064635d57260faec19637f297a017fb8d7f2b1 (main after PR #37; carries PR #35 and PR #36)
Previous Worker version / Pages deployment: cb2e029d-5e75-4cfe-83f7-62687581ee98 / dbff9a7b (0643a54)
New Worker version / Pages deployment: 5cc72a1c-0ad1-45e7-af86-b1c763885c86 / 32bb3b9e (https://32bb3b9e.sonsteng.pages.dev)
Worker provenance: 204 + sha
Pages provenance: 200 + sha on /, /platform/, /platform/matters/
DEV/production parity: SAME spine-build 3c6cab1f6a10220c; DEV redeployed from origin/main by deploy/deploy-dev.sh at 20:16
Operator: Damien (ran the orchestrator's runbook script after the agent's production upload was blocked by the session's permission classifier); preconditions verified by the orchestrating agent; full preflight 21/21 on the candidate
Note: wrangler warned that EDIT_ACCESS_* and PUBLIC_* vars are defined at the top level but not under env.production.vars (pre-existing; the editor is served by the default Worker deploy, not production)
```

```
Date (UTC): 2026-09-02 21:33–21:36
Candidate SHA: 49e24f4f301ea509017d2c4dfa3105adfb7e0b2b (main after PR #40; carries PR #38 aria-live counters, PR #39 pitch contrast Remedy A, PR #40 bot-gate sequencing)
Previous Worker version / Pages deployment: 5cc72a1c-0ad1-45e7-af86-b1c763885c86 / 32bb3b9e (7606463)
New Worker version / Pages deployment: 6932fea4-cf7f-48fe-9d94-679eda21eca2 / 37a2f2f0 (https://37a2f2f0.sonsteng.pages.dev)
Worker provenance: 204 + sha
Pages provenance: 200 + sha on /, /platform/, /platform/matters/
DEV/production parity: SAME spine-build 3c6cab1f6a10220c; DEV redeployed from origin/main by deploy/deploy-dev.sh at 21:22
Operator: orchestrating agent under Damien's 2026-09-02 authority (Wrangler permission granted inline at ~21:05Z); preconditions verified: clean worktree at origin/main, full preflight 21/21 on the candidate, production dry-run clean
```
