# Local assessment data

Weekly hours records are sensitive educational data. The weekly-hours client keeps them in
the learner's browser and exports them only when the learner asks. This directory contains
schemas and portable configuration only—never learner exports, rosters, or production data.

`data/schemas/weekly-hours-log.schema.json` defines export version 1. It uses pseudonymous
learner and offering identifiers, a declared week, dated time entries, and the August D2
per-deliverable contribution log. D2 supersedes the earlier 50-50 attestation; no attestation,
percentage split, or claim about equal contribution belongs in the schema.

Only synthetic fixtures may be committed. `tools/tests/test_no_committed_learner_exports.py`
is the repository guard for accidental exports.
