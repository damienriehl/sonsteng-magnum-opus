# Decision Record: Next steps decisions

Date: September 3, 2026
Purpose: record Damien's answers to the seven next-steps questions collected after the persona UAT
program. Damien chose the recommended option for each decision.

## Provenance

- **D1, D2, D3, D6:** Asked inline and filed as cockpit asks
  `sonsteng-magnum-opus-2026-09-03-1052-next-steps-hard-blocks`, using the ask stem and question id
  as the durable identifiers. Answered September 3, 2026.
- **D4, D5, D7:** Asked inline and filed as cockpit asks
  `sonsteng-magnum-opus-2026-09-03-1053-next-steps-hygiene`, using the ask stem and question id as
  the durable identifiers. Answered September 3, 2026.

## Decisions

- **D1 `d1-dev-worker-redeploy`.**
  - **Question:** Redeploy the default DEV Worker from `main` with `RELEASE_SHA`, at a quiet moment
    with John and Roger told?
  - **Chosen option:** "Yes, after diff review and telling John and Roger"
  - **Authorized:** Redeploy the default DEV Worker only after reviewing the diff and telling John
    and Roger.
- **D2 `d2-production-vars`.**
  - **Question:** Add the six missing vars to `env.production.vars` (or record why production does
    not need them)?
  - **Chosen option:** "Investigate first, add only what production reads"
  - **Authorized:** Investigate the production code paths and add only the variables those paths
    read.
- **D3 `d3-dev-clean-urls`.**
  - **Question:** Give DEV a clean-URL nginx config for parity with Pages?
  - **Chosen option:** "Yes, add the nginx config"
  - **Authorized:** Add the DEV nginx configuration needed for clean-URL parity with Pages.
- **D4 `d4-preflight-journey-gate`.**
  - **Question:** Add the persona journeys' local browser leg to `tools/preflight.sh` as a gate?
  - **Chosen option:** "Yes, local leg only, Chromium requirement documented"
  - **Authorized:** Add only the local browser leg to preflight and document its Chromium
    requirement.
- **D5 `d5-aaa-target-sizes`.**
  - **Question:** Pursue AAA target sizes on the warned controls, or accept AA and record the WARNs
    as accepted?
  - **Chosen option:** "Accept AA now; revisit with the platform redesign"
  - **Authorized:** Accept the AA result and its target-size warnings now, then revisit target sizes
    with the platform redesign.
- **D6 `d6-google-credential-path`.**
  - **Question:** Supply the protected Google credential path for the two live legs?
  - **Chosen option:** "Yes, I will place the file; agent runs the legs after"
  - **Authorized:** Damien will place the protected credential file, after which the agent may run
    the two blocked live legs.
- **D7 `d7-promote-revalidation-driver`.**
  - **Question:** Promote the revalidation driver into `tools/` with a contract test?
  - **Chosen option:** "Yes, promote with a contract test"
  - **Authorized:** Promote the revalidation driver into `tools/` with a contract test.

## Current status

**D1 executed.** Both authorization conditions were satisfied before the default DEV Worker
deploy.

| Evidence | Result |
|---|---|
| Diff review | Completed `2026-09-03` against the undeployed Worker surface. |
| Corrected baseline | The previous DEV Worker deploy was `2026-08-23`, not `2026-08-11` as finding 1 in the September 2 handoff stated. |
| Undeployed surface | Four files, approximately seventy lines: `app/worker/src/byok.js`, `app/worker/src/editor-auth.js`, `app/worker/src/editor-endpoints.js`, and `app/worker/wrangler.jsonc`. |
| Migration check | No new Durable Object migration. |
| Notice | Damien confirmed `2026-09-04` that John and Roger had been told and authorized the deploy. |
| Activation | Completed `2026-09-04` at commit `20129c07666306a0b9ee614f6c021b6353f1b349`, with `RELEASE_SHA` set. |
| New version | `d0bfd1c5-7034-4b2a-8f87-107d897ae8dc` |
| Rollback target | `c4d21de6-ea7d-454f-905a-d0203d800af0`, activated `2026-08-23T15:59:30Z` |

**Post-deploy checks.**

| Check | Result |
|---|---|
| Release provenance | HTTP `204` with the deployed SHA. Before deployment it answered HTTP `503` with a null build key. |
| Access door | Still redirects to Cloudflare Access. |
| Legacy editor host | Still forwards to the Access door. |
| Public aliases | Both still redirect to the canonical host. |
| Session gate | Still answers HTTP `403` to a headless client. |
| Tokenless routes | `/edit/index.html` and `/edit/pending` still answer HTTP `404`. |
| DEV browser journey | Passed every attempt, `44` of `44`. |
| DEV binding legs | Recorded the release SHA where they previously recorded null. |

**D6 executed.** Both previously blocked live legs ran.

| Live leg | Result |
|---|---|
| Student live provider | Pass |
| Hostile live red-team | `13` of `14`; one known defect is tracked separately |

**Other decisions.** This update makes no execution claim for the remaining decisions.

| Decisions | Status |
|---|---|
| `D2` through `D5`; `D7` | Not established by this update |
