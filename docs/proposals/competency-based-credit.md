# Competency-Based Credit: A Privacy-Preserving Evidence Proposal

## Proposal status

This document proposes an evidence design for schools and the ABA. It does not report
findings from learners. Evidence claims remain deferred until the consent, sample, and
analysis conditions below have been met.

The question is deliberately limited: among consenting learners, are locally recorded
hours associated with demonstrated memo-heading competence strongly and consistently
enough to support further study of competency-based credit? An association does not
establish causation. This proposal makes no causal claim that time spent causes competence,
that the practicum causes competence, or that credit should be awarded because of either
measure alone.

## Learner-controlled data boundary

Weekly worked hours, billable hours, contribution entries, and the pseudonymous join token
remain in the learner's browser. Actual learner data does not leave the browser unless the
learner chooses an explicit export. Nothing in this proposal adds an automatic upload,
analytics beacon, background synchronization, or server-side collection path.

Before a learner's first consented study export, a cryptographically random per-student
token is generated in the browser from exactly 128 random bits, encoded as exactly 32 lowercase
hexadecimal characters matching `^[0-9a-f]{32}$`. The token is never derived from name,
email, student ID, login, device identifier, or another identifying field. For a consented
study copy, that opaque value occupies the current weekly-hours document's `learner_id`
field and the future assessment projection's `join_token`. No token-to-identity map is
persisted server-side, and schools must not request a separate identity lookup table.

The token is a pseudonym, not anonymity. A learner can rotate it to end future linkage;
rotation creates a new analysis record and does not rewrite earlier exports. Each export
must preview the exact fields, explain the linkage purpose, and require a fresh learner
action. The study copy retains only the token and approved study fields; original local
records remain under learner control.

## Measurement/data-contract appendix — version 2

### Bind to the current products; do not invent another one

The assessment side is a **versioned future study projection** bound to the existing
`POST /v1/memo-assessment` response and protected `assessment-audit/v1` record. This
proposal does not claim that the runtime currently exports an assessment CSV or JSON study
file. It creates no parallel production schema and authorizes no custom analyzer or export
subsystem. A later consented export may project only these current response/audit fields:

- `assessment_audit_id` = `record.id`, using the identifier returned by the successful
  memo-assessment response;
- `attempted_at` = `record.created_at`, converted from server epoch milliseconds to a UTC
  ISO-8601 timestamp without changing its ordering;
- the assessment result from `record.result.headings`, containing exactly seven unique
  `heading_id` and integer `score` pairs on the 1–7 scale;
- the resolved threshold claim from `record.provenance.config`;
- instrument ID, version, and content hash from `record.provenance.instrument`; and
- grader provider, model, and mode from `record.provenance.providers`.

Each projected attempt also needs the stable task/deliverable ID used by the hours log.
The assessment caller and audit record must retain that identifier before collection begins;
this proposal does not silently infer it from memo text or an audit ID. Legacy audit records
without that identifier are missing for task-level analysis and are excluded, with their
count reported. That prerequisite is a small extension to the existing audit evidence,
not permission to create a second assessment record format.

Human overrides remain append-only audit evidence. The primary study measure uses the
original deterministic `record.result.headings` values; a pre-registered sensitivity
analysis may apply the last authorized override per heading ordered by server `created_at`
and override ID. Never mix original and overridden results without reporting which path was
used.

### Reproducible pseudonymous join

The exact pseudonymous learner join is current weekly-hours `learner_id` = projected
assessment `join_token`, compared case-sensitively after both pass the 32-lowercase-hex
constraint. `offering_id` must also match the pre-declared offering. Blank, malformed,
rotated, or unmatched tokens remain in reconciliation counts and do not enter analysis.

Within a matched learner and offering, the task-hours bridge is:

1. `contribution_log[].deliverable_id` = the assessment task/deliverable ID;
2. `contribution_log[].related_entry_ids` -> `entries[].id`; and
3. each referenced entry supplies its `date`, `worked_hours`, and `billable_hours`.

Collect every valid weekly document whose week intersects the observation window. Entry and
contribution IDs must remain unique within a learner/offering across those documents; a
repeated ID with different content makes the affected learner-task missing.

Count each linked entry ID once per task even when more than one contribution for that task
references it. An entry ID linked to more than one deliverable is ambiguous: exclude its
hours from every affected task measure, report the learner-task record as missing for time
measures, and retain it in reconciliation. A contribution with a missing `deliverable_id`,
an unknown `related_entry_id`, or no related entry cannot donate hours. Unlinked weekly
entries remain valid for weekly totals but not for task-level time measures.

The weekly schema records dates, not times. Include a linked entry only when its `date` is
strictly earlier than the UTC calendar date of the attempt. If a linked entry and attempt
share a UTC date, temporal order is unknowable; that learner-task is missing for time
measures rather than assigning possibly post-assessment hours to the attempt. Order eligible
attempts by `record.created_at`; break equal `record.created_at` values by the
lexicographically smallest case-sensitive `assessment_audit_id`. These rules are applied
without looking at scores.

Every eligible attempt contains all seven unique heading IDs and seven integer scores.
An attempt missing a heading, containing a duplicate heading, or carrying a score outside
1–7 is missing, not a failure. Its config, instrument, and provider provenance must be
present and internally consistent. Reject unknown audit or instrument versions. Report
exported, valid, matched, unmatched, ambiguous, excluded, and analyzed counts.

### Exact learner-task measures

For learner `i`, task `t`, and eligible attempt `j`, let `H_itj` be the sum of distinct,
unambiguous linked `worked_hours` dated before attempt `j`; let `B_itj` be the analogous
billable-hours sum. Let `q_itjh` be heading `h`'s effective integer score and `c_itj` the
attempt's resolved competence threshold.

For each eligible point, worked hours are `H_itj`, billable hours are `B_itj`, and the
billable ratio is `B_itj / H_itj` (missing, not zero, when `H_itj = 0`). The assessment
score is the seven-heading score vector, never an average or letter grade. Attempt count is
the number of distinct eligible assessment attempts ordered under the rule above.

- **Time-to-first-competence.** The event is the earliest eligible attempt `j*` for which
  all seven heading scores satisfy `score >= the attempt's resolved competence threshold`:
  `min_h(q_it,j*,h) >= c_it,j*`. The worked-hours result is `H_itj*`; report `B_itj*`
  separately. It is missing, not zero, when time linkage is invalid.
- **Time-to-six.** The event is the earliest eligible attempt `j6` for which all seven
  heading scores satisfy `score >= 6`: `min_h(q_itj6h) >= 6`. The result is `H_itj6`, with
  `B_itj6` reported separately. Six is the fixed scale milestone, not a letter grade and not
  a substitute for the configured competence threshold.
- **Attempts-to-competence.** For an observed first-competence event at ordered attempt
  `j*`, the result is the 1-indexed number of eligible attempts through `j*`:
  `A_it = 1 + count(eligible attempts earlier than j*)`. Invalid attempts do not enter the
  count; their exclusion count is reported.
- **Right-censoring.** If a learner-task has valid linked hours and eligible attempts but no
  endpoint event by the pre-declared observation-window close, it is right-censored at its
  last cumulative worked hours and eligible-attempt count. A learner-task with no valid
  pseudonymous match, no task/deliverable ID, ambiguous time links, no eligible attempt, or
  invalid provenance is missing, not zero and not censored.

Event times and attempts are learner-task observations, never grades or credit awards.
Task summaries must show event counts and censoring. A median computed only among event
records may be shown as descriptive but must be labelled survivor-selected; a future
pre-registered analysis should use a censoring-aware estimator when the sample supports it.

### Task-level uncertainty

For each pre-declared task and endpoint, choose a worked-hours horizon `H` before inspecting
outcomes. Define `k` as eligible learner-task records with the endpoint observed by `H`,
`f` as eligible records observed event-free through at least `H`, `c` as otherwise-valid
records right-censored before `H`, and `m` as missing/excluded records. The estimable
denominator is `n = k + f`; neither `c` nor `m` is silently counted as failure. When `n > 0`,
`p-hat = k / n`.

The required **task-level uncertainty** is the two-sided Wilson 95% interval with `z = 1.96`:

```text
center = (p-hat + z^2/(2n)) / (1 + z^2/n)
half_width = z * sqrt(p-hat*(1-p-hat)/n + z^2/(4n^2)) / (1 + z^2/n)
interval = [max(0, center-half_width), min(1, center+half_width)]
```

Publish `k`, `f`, `c`, `m`, `n`, `p-hat`, and the interval together, plus censoring rate
`c / (k + f + c)` and missingness rate `m / (n + c + m)` when their denominators are
nonzero. If `n = 0`, the estimate and interval are missing. This interval quantifies sampling
uncertainty in the descriptive task endpoint; it does not repair selection bias, measurement
error, provider disagreement, or confounding.

## Consent, minimum sample, and claim conditions

Participation must be voluntary and based on plain-language informed consent obtained
before study exports are collected. Consent must identify the fields, purpose, recipients,
retention period, withdrawal deadline, risks of pseudonymous linkage, and the fact that
participation or refusal will not affect assessment, course access, standing, or credit.
A school must provide appropriate ethics or institutional review and a documented deletion
process for withdrawn exports.

No public claim may be made unless all of these conditions are satisfied:

1. the protocol, observation window, eligible tasks, horizons, thresholds, and analysis
   choices were fixed before inspecting outcomes;
2. at least 30 consenting learners have complete, valid, matched exports in the declared
   cohort, independent of how many exports were invited or received;
3. consent rate, retention/deletion outcomes, missingness, censoring, exclusions, and
   unmatched counts are disclosed;
4. no subgroup result is published with fewer than 10 consenting learners, and small cells
   are suppressed rather than recombined in a way that invites re-identification;
5. uncertainty is reported, the sample is described as self-selected where applicable, and
   claims are phrased only as associations in that cohort; and
6. the assessment instrument and competence threshold were settled and versioned before
   any competence-rate claim.

A cohort below 30 may be used only for private workflow validation. It cannot support a
public effectiveness, equivalence, competency, or credit claim. Meeting the minimum sample
does not by itself make a claim representative or causal. Schools set credits until evidence
supports any different policy; no formula in this proposal awards or recommends credits.

### Future-data checklist before any public outcome statement

- **Consent:** voluntary, prospective, comprehensible, revocable, and unrelated to standing.
- **Retention:** pre-declared duration, access controls, withdrawal deadline, and verified
  deletion procedure.
- **Missingness:** counts and reasons at every join, validation, task, and endpoint step.
- **Selection bias:** invitation, consent, export, and completion rates, with no claim that
  volunteers represent all learners.
- **Instructor effects:** instructor, section, feedback opportunity, and threshold differences
  described or modeled only under a pre-registered, sufficiently powered design.
- **Provider/model effects:** provider, model, mode, panel composition, and version provenance
  retained; reduced-assurance and configuration changes analyzed separately.
- **Task difficulty:** task identity, sequence, prerequisites, and material version retained;
  faster performance on an easier task is not generalized to harder tasks.
- **Causal limits:** prior preparation, accessibility, work obligations, feedback, exercise
  order, and assessment conditions can affect both hours and outcomes; observational
  association does not establish causation.

## Hand-checkable synthetic example

Every input, intermediate value, and output in this section is explicitly **SYNTHETIC —
ILLUSTRATIVE**. None describes an actual learner, and the two illustrative learners are
far below the public-claim minimum. The pre-declared task is `deliverable-t1`, the competence
threshold is 4, the endpoint horizon is `H = 6.0` worked hours, and the window is 2026-01-01
through 2026-01-31 UTC.

**SYNTHETIC — ILLUSTRATIVE input: current `weekly-hours-log` shape**

The following are current-shape synthetic weekly-document excerpts, compacted into a table
for hand checking. Every entry also has a synthetic `project`, `matter`, `activity`, boolean
`class_time`, and `narrative`; each document has `schema_version: 1`, the shown 32-hex
`learner_id`, `offering_id: offering-demo`, and the seven-day week containing its rows.

| Illustrative token | Entry ID | Date | Worked | Billable | Contribution ID | Deliverable ID | Related entry IDs |
|---|---|---|---:|---:|---|---|---|
| `0000000000000000000000000000000a` | `entry-a1` | 2026-01-05 | 2.0 | 1.0 | `contrib-a-w1` | `deliverable-t1` | `entry-a1`, `entry-a2` |
| same | `entry-a2` | 2026-01-09 | 3.0 | 2.0 | `contrib-a-w1` | same | `entry-a1`, `entry-a2` |
| same | `entry-a3` | 2026-01-13 | 1.0 | 0.5 | `contrib-a-w2` | same | `entry-a3` |
| `0000000000000000000000000000000b` | `entry-b1` | 2026-01-05 | 4.0 | 2.0 | `contrib-b-w1` | `deliverable-t1` | `entry-b1` |
| same | `entry-b2` | 2026-01-12 | 2.0 | 1.0 | `contrib-b-w2` | same | `entry-b2` |

**SYNTHETIC — ILLUSTRATIVE input: future study projection from current audit fields**

Each compact score vector is ordered by the canonical seven heading IDs and therefore
contains all seven scores. All rows carry `instrument.id = memo-seven-heading-1-7`, the same
valid version/content hash, resolved config threshold 4, and synthetic provider/model/mode
provenance. Those are projected from the current audit locations specified above.

| Illustrative token | Task/deliverable | `assessment_audit_id` | `record.created_at` as UTC | Seven scores |
|---|---|---|---|---|
| `...000a` | `deliverable-t1` | `assessment-a1` | 2026-01-06T12:00:00Z | `[3,3,3,3,3,3,3]` |
| `...000a` | `deliverable-t1` | `assessment-a2` | 2026-01-10T12:00:00Z | `[4,4,4,4,4,4,4]` |
| `...000a` | `deliverable-t1` | `assessment-a3` | 2026-01-14T12:00:00Z | `[6,6,6,6,6,6,6]` |
| `...000b` | `deliverable-t1` | `assessment-b1` | 2026-01-06T13:00:00Z | `[3,3,3,3,3,3,3]` |
| `...000b` | `deliverable-t1` | `assessment-b2` | 2026-01-13T13:00:00Z | `[3,3,3,3,3,3,3]` |

**SYNTHETIC — ILLUSTRATIVE intermediate output: deliverable/entry join and reconciliation**

- Illustrative tokens: `2` exported, `2` valid, `2` exactly matched, `0` unmatched.
- Illustrative tasks: `2` learner-task records, `0` ambiguous links, `0` missing records.
- Illustrative attempts: `5` exported, `5` valid; every row has seven unique heading scores
  and valid config/instrument/provider provenance.
- Illustrative ordering uses server time, then case-sensitive `assessment_audit_id`; no tie
  changes this example.

**SYNTHETIC — ILLUSTRATIVE output: learner-task measures**

| Illustrative learner | Illustrative time-to-first-competence | Illustrative time-to-six | Illustrative attempts-to-competence | Illustrative status at 6.0 hours |
|---|---:|---:|---:|---|
| `...000a` | `2 + 3 = 5.0` worked hours | `2 + 3 + 1 = 6.0` worked hours | `1 + 1 = 2` attempts | both events observed |
| `...000b` | right-censored at `4 + 2 = 6.0` | right-censored at `6.0` | right-censored at `2` attempts | event-free through horizon |

**SYNTHETIC — ILLUSTRATIVE output: task-level uncertainty**

For first competence by `H = 6.0`, illustrative `k = 1`, `f = 1`, `c = 0`, `m = 0`,
so `n = 1 + 1 = 2` and `p-hat = 1 / 2 = 0.50`. Substituting `n = 2`, `p-hat = 0.5`,
and `z = 1.96` into the Wilson formula gives the illustrative 95% interval
`[0.0945, 0.9055]`, with lower bound `0.0945` and upper bound `0.9055`, rounded to four
decimals. The illustrative censoring and missingness
rates are both `0 / 2 = 0.00`.

**SYNTHETIC — ILLUSTRATIVE hand recomputation:** the first illustrative learner has only
`entry-a1` before `assessment-a1`, so the first attempt occurs after 2.0 linked hours and
does not meet competence. `entry-a2` precedes `assessment-a2`; all seven scores are 4, so
Illustrative time-to-first-competence is `2 + 3 = 5.0` and Illustrative
attempts-to-competence is `1 + 1 = 2`. `entry-a3` precedes `assessment-a3`; all seven
scores are 6, so Illustrative time-to-six is `2 + 3 + 1 = 6.0`. The second learner has
6.0 linked hours and two attempts but no heading score at 4, so both endpoints are
right-censored. One of two estimable records reaches competence by six hours, which
recomputes the Illustrative task-level uncertainty inputs as `1 / 2 = 0.50`. These are
illustrative workflow checks, not inferential evidence, a causal effect, or a credit rule.
