"""Documentation contract for the U14 competency-credit proposal."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = ROOT / "docs/proposals/competency-based-credit.md"


def proposal_text() -> str:
    return PROPOSAL.read_text(encoding="utf-8")


def normalized_proposal_text() -> str:
    return " ".join(proposal_text().lower().split())


def test_proposal_defines_privacy_preserving_reproducible_join() -> None:
    text = normalized_proposal_text()

    assert "random per-student token" in text
    assert "generated in the browser" in text
    assert "exactly 32 lowercase hexadecimal characters" in text
    assert "^[0-9a-f]{32}$" in text
    assert "never derived from" in text
    assert "name, email, student id" in text
    assert "no token-to-identity map" in text
    assert "assessment attempt" in text
    assert (
        "actual learner data does not leave the browser unless the learner chooses an "
        "explicit export"
    ) in text
    assert (
        "nothing in this proposal adds an automatic upload, analytics beacon, background "
        "synchronization, or server-side collection path"
    ) in text


def test_proposal_states_measures_and_claim_limits() -> None:
    text = normalized_proposal_text()

    for required in (
        "worked hours",
        "billable hours",
        "billable ratio",
        "assessment score",
        "attempt count",
        "consent",
        "minimum sample",
        "association",
        "causation",
    ):
        assert required in text

    assert (
        "at least 30 consenting learners have complete, valid, matched exports"
    ) in text
    assert "no subgroup result is published with fewer than 10 consenting learners" in text
    assert "a cohort below 30 may be used only for private workflow validation" in text
    assert (
        "it cannot support a public effectiveness, equivalence, competency, or credit claim"
    ) in text


def test_every_synthetic_output_is_explicitly_illustrative() -> None:
    text = proposal_text()
    synthetic_result_headings = [
        line
        for line in text.splitlines()
        if line.startswith("**SYNTHETIC")
        and ("output:" in line or "recomputation:" in line)
    ]

    assert synthetic_result_headings
    assert all("ILLUSTRATIVE" in line for line in synthetic_result_headings)
    assert "current `weekly-hours-log` shape" in text
    assert "future study projection from current audit fields" in text
    assert "deliverable/entry join and reconciliation" in text
    assert "case-sensitive `assessment_audit_id`" in text
    normalized_text = normalized_proposal_text()
    assert (
        "every eligible attempt contains all seven unique heading ids and seven integer "
        "scores"
    ) in normalized_text
    assert (
        "an attempt missing a heading, containing a duplicate heading, or carrying a "
        "score outside 1–7 is missing"
    ) in normalized_text
    assert (
        "break equal `record.created_at` values by the lexicographically smallest "
        "case-sensitive `assessment_audit_id`"
    ) in normalized_text
    assert "`entry-a1`" in text
    assert "`deliverable-t1`" in text
    assert "`assessment-a2`" in text


def test_proposal_makes_no_causal_claim() -> None:
    text = proposal_text().lower()

    assert "does not establish causation" in text
    assert "no causal claim" in text


def test_future_attempt_projection_binds_to_current_assessment_audit() -> None:
    text = normalized_proposal_text()

    for required in (
        "versioned future study projection",
        "does not claim that the runtime currently exports",
        "no parallel production schema",
        "`assessment_audit_id` = `record.id`",
        "`attempted_at` = `record.created_at`",
        "`record.result.headings`",
        "exactly seven unique `heading_id` and integer `score` pairs",
        "`record.provenance.config`",
        "`record.provenance.instrument`",
        "`record.provenance.providers`",
        "task/deliverable id",
    ):
        assert required in text

    assert "legacy audit records without that identifier are missing" in text


def test_proposal_defines_all_task_progress_measures_and_missingness() -> None:
    text = normalized_proposal_text()

    for required in (
        "time-to-first-competence",
        "time-to-six",
        "attempts-to-competence",
        "task-level uncertainty",
        "all seven heading scores",
        "score >= the attempt's resolved competence threshold",
        "score >= 6",
        "right-censored",
        "missing, not zero",
        "n = k + f",
        "p-hat = k / n",
        "wilson 95% interval",
    ):
        assert required in text

    assert (
        "`contribution_log[].deliverable_id` = the assessment task/deliverable id"
    ) in text
    assert (
        "`contribution_log[].related_entry_ids` -> `entries[].id`"
    ) in text
    assert "count each linked entry id once per task" in text
    assert "linked to more than one deliverable" in text


def test_synthetic_example_recomputes_progress_measures_and_uncertainty() -> None:
    text = proposal_text()

    for required in (
        "Illustrative time-to-first-competence",
        "Illustrative time-to-six",
        "Illustrative attempts-to-competence",
        "Illustrative task-level uncertainty",
        "`2 + 3 = 5.0`",
        "`1 + 1 = 2`",
        "`1 / 2 = 0.50`",
        "`0.0945`",
        "`0.9055`",
    ):
        assert required in text


def test_future_data_checklist_and_school_credit_authority_are_explicit() -> None:
    text = normalized_proposal_text()

    for required in (
        "consent",
        "retention",
        "missingness",
        "selection bias",
        "instructor effects",
        "provider/model effects",
        "task difficulty",
        "causal limits",
        "schools set credits until evidence supports any different policy",
    ):
        assert required in text
