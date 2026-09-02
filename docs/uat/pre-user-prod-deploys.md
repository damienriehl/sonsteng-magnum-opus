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
