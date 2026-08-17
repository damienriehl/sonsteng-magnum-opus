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
    assert "complete `weekly-hours/v1` CSV export" in text
    assert "complete `assessment-attempts/v1` CSV export" in text
    assert "exact-token inner join and reconciliation" in text
    assert "case-sensitive UTF-8 lexical order selects `b1`" in text
    normalized_text = normalized_proposal_text()
    assert (
        "primary final-score analysis requires exactly one remaining attempt whose "
        "`is_final_attempt` value is boolean `true`"
    ) in normalized_text
    assert (
        "with zero or more than one such attempt, exclude that token from primary "
        "final-score and competence analyses"
    ) in normalized_text
    assert (
        "break an equal timestamp tie by the lexicographically smallest case-sensitive "
        "utf-8 `attempt_id`"
    ) in normalized_text
    assert "`2 + 3 = 5`" in text
    assert "`1 + 2 = 3`" in text
    assert "`3 / 5 = 0.60`" in text
    assert "`2 / 4 = 0.50`" in text
    assert "worked hours total `5 + 4 = 9`" in text
    assert "billable hours total\n`3 + 2 = 5`" in text
    assert "final-score points total `4 + 5 = 9`" in text
    assert "attempt count totals `2 + 2 = 4`" in text


def test_proposal_makes_no_causal_claim() -> None:
    text = proposal_text().lower()

    assert "does not establish causation" in text
    assert "no causal claim" in text
