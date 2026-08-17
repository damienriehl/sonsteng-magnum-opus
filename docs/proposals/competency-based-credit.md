# Competency-Based Credit: A Privacy-Preserving Evidence Proposal

## Proposal status

This document proposes an evidence design for schools and the ABA. It does not report
findings from learners. Evidence claims remain deferred until the consent, sample, and
analysis conditions below have been met.

The question is deliberately limited: among consenting learners, are locally recorded
hours associated with assessment performance strongly and consistently enough to support
further study of competency-based credit? An association does not establish causation.
This proposal makes no causal claim that time spent causes competence, that the practicum
causes competence, or that credit should be awarded because of either measure alone.

## Learner-controlled data boundary

Weekly worked hours, billable hours, contribution entries, and the join token remain in
the learner's browser. Actual learner data does not leave the browser unless the learner
chooses an explicit export. Nothing in this proposal adds an automatic upload, analytics
beacon, background synchronization, or server-side collection path.

Before first export, a cryptographically random per-student token is generated in the browser
from exactly 128 random bits, encoded as exactly 32 lowercase hexadecimal characters matching
`^[0-9a-f]{32}$`, and stored with the
local hours log. The token is never derived from name, email, student ID, login, device
identifier, or another identifying field. The learner copies the same token into the
assessment-attempt export settings, or imports a learner-controlled join-token file, so
both explicit exports carry the same opaque value. No token-to-identity map is persisted
server-side. Schools must not ask learners to submit a separate identity lookup table.

The token is a pseudonym, not anonymity. A learner can rotate it to end future linkage;
rotation creates a new analysis record and does not rewrite earlier exports. Export
screens must preview the exact fields, explain the linkage purpose, and require a fresh
learner action for each export.

## Reproducible join contract

The two learner-chosen exports use UTF-8 CSV or JSON with the following versioned fields:

- `weekly-hours/v1`: `join_token`, `week_start` (ISO date), `worked_hours`,
  `billable_hours`, and optional non-identifying `deliverable_code`.
- `assessment-attempts/v1`: `join_token`, `attempt_id`, `attempted_at` (ISO-8601),
  `exercise_code`, `assessment_score`, `instrument_version`, and `is_final_attempt`.

The reproducible join is an inner join on the exact, case-sensitive `join_token`. Reject
blank or malformed tokens, duplicate `attempt_id` values, negative hours, billable hours
greater than worked hours, scores outside the instrument's declared range, and records
with unknown schema or instrument versions. Keep unmatched rows in a reconciliation
report but exclude them from analysis. Report counts of exported, valid, matched, and
unmatched records so another analyst can reproduce the analysis population.

For a pre-declared observation window, aggregate valid weekly rows by `join_token`, then
join that one-row-per-token table to valid assessment attempts. The analysis script,
schema versions, instrument versions, observation-window dates, exclusion counts, and a
digest of each input export must accompany any result. The analysis copy should retain
only the random token and study fields; the learner's original local records remain under
learner control.

Apply filtering and attempt selection in this fixed order. First, validate the declared
schema and instrument versions and field constraints. Second, retain only weekly rows whose
`week_start` and attempts whose `attempted_at` fall within the inclusive, pre-declared UTC
observation window, and retain only pre-declared eligible `exercise_code` values. Third,
aggregate the remaining weekly rows by exact token and inner-join them to the remaining
attempts. For each matched token, `attempt_count` is the number of remaining distinct
attempts. Primary final-score analysis requires exactly one remaining attempt whose
`is_final_attempt` value is boolean `true`: with zero or more than one such attempt, exclude
that token from primary final-score and competence analyses and report it in a separate
zero-final or multiple-final reconciliation count. The sensitivity first-attempt score is
the remaining attempt with the earliest normalized UTC `attempted_at`; break an equal
timestamp tie by the lexicographically smallest case-sensitive UTF-8 `attempt_id`. These
rules are applied without consulting scores.

## Measures and analysis contract

### Learner-level measures

- **Worked hours:** sum of valid `worked_hours` in the declared observation window.
- **Billable hours:** sum of valid `billable_hours` in that window.
- **Billable ratio:** billable hours divided by worked hours; missing, not zero, when
  worked hours is zero.
- **Assessment score:** the score from the declared instrument version. Primary analysis
  uses the final valid attempt; sensitivity analysis uses the first valid attempt.
- **Attempt count:** count of valid, distinct assessment attempts in the window.
- **Competence indicator:** whether the final score meets a competence threshold declared
  before analysis. Until the assessment instrument and threshold are settled, this
  measure is specified but must not be computed or reported.

### Cohort analysis

Before seeing results, register the observation window, eligible exercises, assessment
instrument version, competence threshold if settled, exclusion rules, and the primary
association statistic. The primary descriptive outputs are the cohort count, medians and
interquartile ranges for worked hours, billable hours, billable ratio, assessment score,
and attempt count. The primary association is Spearman's rank correlation between worked
hours and final assessment score, with a confidence interval. Secondary, explicitly
exploratory associations may examine billable hours, billable ratio, attempt count, and
first-attempt score. Report missingness and unmatched-export rates alongside every table.

Do not convert this observational analysis into earned-credit rules. Prior preparation,
exercise difficulty, feedback, accessibility, work obligations, and assessment conditions
may affect both time and score. Results can describe association, support instrument
review, and motivate a prospectively designed study; they cannot show causation or an
individual learner's entitlement to credit.

## Consent and minimum sample before any public claim

Participation must be voluntary and based on plain-language, informed consent obtained
before exports are collected for analysis. Consent must identify the fields, purpose,
recipients, retention period, withdrawal deadline, risks of pseudonymous linkage, and the
fact that participation or refusal will not affect assessment, course access, standing,
or credit. A school must provide an appropriate ethics or institutional review before
research use and a documented deletion process for withdrawn exports.

No public claim may be made unless all of these conditions are satisfied:

1. the protocol and analysis choices above were fixed before inspecting outcomes;
2. at least 30 consenting learners have complete, valid, matched exports in the declared
   cohort, independent of how many exports were invited or received;
3. the consent rate, missingness, exclusions, and unmatched counts are disclosed;
4. no subgroup result is published with fewer than 10 consenting learners, and small
   cells are suppressed rather than combined in a way that invites re-identification;
5. uncertainty is reported, the sample is described as self-selected where applicable,
   and the claim is phrased as an association in that cohort; and
6. the assessment instrument and its competence threshold have been settled and versioned
   before any competence-rate claim.

A cohort below 30 may be used only for private workflow validation. It cannot support a
public effectiveness, equivalence, competency, or credit claim. Meeting the minimum sample
does not by itself make a claim representative or causal.

## Hand-checkable synthetic example

Every input, intermediate value, and output in this section is explicitly **SYNTHETIC —
ILLUSTRATIVE**; none describes an actual learner. The example declares the inclusive UTC
window `2026-01-05T00:00:00Z` through `2026-01-18T23:59:59Z`, eligible exercise `E1`,
schema versions `weekly-hours/v1` and `assessment-attempts/v1`, and instrument version
`demo-v1`. Its two matched learners are intentionally below the 30-learner minimum and
cannot support a public claim.

**SYNTHETIC — ILLUSTRATIVE input: complete `weekly-hours/v1` CSV export**

```csv
join_token,week_start,worked_hours,billable_hours,deliverable_code
0000000000000000000000000000000a,2026-01-05,2,1,D1
0000000000000000000000000000000a,2026-01-12,3,2,D2
0000000000000000000000000000000b,2026-01-05,4,2,D1
0000000000000000000000000000000c,2026-01-05,7,4,D1
```

**SYNTHETIC — ILLUSTRATIVE input: complete `assessment-attempts/v1` CSV export**

```csv
join_token,attempt_id,attempted_at,exercise_code,assessment_score,instrument_version,is_final_attempt
0000000000000000000000000000000a,a2,2026-01-12T10:00:00Z,E1,4,demo-v1,true
0000000000000000000000000000000a,a1,2026-01-06T10:00:00Z,E1,3,demo-v1,false
0000000000000000000000000000000b,b2,2026-01-07T09:00:00Z,E1,5,demo-v1,true
0000000000000000000000000000000b,b1,2026-01-07T09:00:00Z,E1,2,demo-v1,false
0000000000000000000000000000000d,d1,2026-01-08T10:00:00Z,E1,6,demo-v1,true
```

All rows in both illustrative inputs pass validation and the declared window, exercise,
and version filters. The equal timestamps for `b1` and `b2` intentionally exercise the
first-attempt tie-break: case-sensitive UTF-8 lexical order selects `b1`.

**SYNTHETIC — ILLUSTRATIVE intermediate output: weekly aggregation**

| Illustrative token | Illustrative worked-hours sum | Illustrative billable-hours sum | Illustrative billable ratio |
|---|---:|---:|---:|
| `0000000000000000000000000000000a` | `2 + 3 = 5` | `1 + 2 = 3` | `3 / 5 = 0.60` |
| `0000000000000000000000000000000b` | `4` | `2` | `2 / 4 = 0.50` |
| `0000000000000000000000000000000c` | `7` | `4` | `4 / 7 ≈ 0.5714` |

**SYNTHETIC — ILLUSTRATIVE intermediate output: exact-token inner join and reconciliation**

- Illustrative weekly export: `4` rows, `4` valid rows, `3` distinct valid tokens.
- Illustrative assessment export: `5` rows, `5` valid rows, `3` distinct valid tokens.
- Illustrative matched population: `2` exact tokens (`...000a` and `...000b`), comprising
  `3` weekly rows and `4` assessment rows.
- Illustrative unmatched population: `1` weekly token (`...000c`, one row) and `1`
  assessment token (`...000d`, one row); both are excluded from analysis.
- Illustrative final-flag reconciliation among matched tokens: `2` with exactly one final,
  `0` with zero finals, and `0` with multiple finals.

**SYNTHETIC — ILLUSTRATIVE intermediate output: selected attempts**

| Illustrative token | Illustrative first attempt | Illustrative final attempt | Illustrative attempt count |
|---|---|---|---:|
| `0000000000000000000000000000000a` | `a1` (score `3`) | `a2` (score `4`) | `2` |
| `0000000000000000000000000000000b` | `b1` (score `2`) | `b2` (score `5`) | `2` |

**SYNTHETIC — ILLUSTRATIVE output: learner-level measures**

| Illustrative token | Illustrative worked hours | Illustrative billable hours | Illustrative billable ratio | Illustrative first score | Illustrative final score | Illustrative attempt count |
|---|---:|---:|---:|---:|---:|---:|
| `0000000000000000000000000000000a` | `5` | `3` | `0.60` | `3` | `4` | `2` |
| `0000000000000000000000000000000b` | `4` | `2` | `0.50` | `2` | `5` | `2` |

**SYNTHETIC — ILLUSTRATIVE hand recomputation:** token `...000a` has worked hours
`2 + 3 = 5`, billable hours `1 + 2 = 3`, and billable ratio `3 / 5 = 0.60`; token
`...000b` has `4` worked hours, `2` billable hours, and ratio `2 / 4 = 0.50`. The exact
token intersection is `{...000a, ...000b}`, so there are `2` matched learners. Each has
two distinct valid attempts, giving attempt counts `2` and `2`; the final flags select
scores `4` and `5`, while timestamp order and the stated tie-break select first scores
`3` and `2`. Thus matched worked hours total `5 + 4 = 9`, billable hours total
`3 + 2 = 5`, final-score points total `4 + 5 = 9`, and attempt count totals `2 + 2 = 4`.
These are illustrative workflow checks, not inferential evidence or a causal effect.
