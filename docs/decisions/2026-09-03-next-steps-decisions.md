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

## Status at record time

- **D1 pending:** The DEV Worker redeploy remains unexecuted pending notice to John and Roger.
