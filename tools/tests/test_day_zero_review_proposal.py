from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools" / "validate_day_zero_review_proposal.py"
PROPOSAL_PATH = REPO / "docs" / "evidence" / "2026-08-17-day-zero-review-proposal.json"


def load_module():
    spec = importlib.util.spec_from_file_location("day_zero_review_proposal", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_committed_proposal_is_current_complete_and_deterministic():
    module = load_module()
    proposal = json.loads(PROPOSAL_PATH.read_text())

    module.validate_proposal(REPO, proposal)
    assert module.render_proposal(proposal) == PROPOSAL_PATH.read_text()
    sheet = REPO / "docs" / "decisions" / "2026-08-17-day-zero-review-decision-sheet.md"
    assert module.render_sheet(proposal) == sheet.read_text()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda proposal: proposal["proposals"].append(copy.deepcopy(proposal["proposals"][0])),
         "duplicate proposal key"),
        (lambda proposal: proposal["proposals"][0].__setitem__("source", "data/unknown.json"),
         "key does not match"),
        (lambda proposal: proposal["proposals"][0].__setitem__("proposed_disposition", "approved"),
         "invalid disposition"),
        (lambda proposal: proposal["proposals"][0].__setitem__("rationale", ""),
         "empty reason"),
        (lambda proposal: proposal["proposals"].__setitem__(
            slice(0, 2), reversed(proposal["proposals"][0:2])
        ), "ordering"),
    ],
)
def test_validator_rejects_invalid_or_unknown_proposals(mutate, message):
    module = load_module()
    proposal = json.loads(PROPOSAL_PATH.read_text())
    mutate(proposal)

    with pytest.raises(ValueError, match=message):
        module.validate_proposal(REPO, proposal)


def test_validator_rejects_stale_decision_sheet():
    module = load_module()
    proposal_text = PROPOSAL_PATH.read_text()
    proposal = json.loads(proposal_text)
    sheet = module.render_sheet(proposal)

    with pytest.raises(ValueError, match="decision sheet is stale"):
        module.validate_rendered_artifacts(proposal, proposal_text, sheet + "stale\n")


def test_validator_rejects_unknown_and_missing_governed_keys():
    module = load_module()
    proposal = json.loads(PROPOSAL_PATH.read_text())
    row = proposal["proposals"][0]
    row["source"] = "data/unknown.json"
    row["key"] = module._key(row["category"], row)

    with pytest.raises(ValueError, match="referenced source does not exist"):
        module.validate_proposal(REPO, proposal)

    proposal = json.loads(PROPOSAL_PATH.read_text())
    proposal["proposals"].pop()
    with pytest.raises(ValueError, match="proposal coverage mismatch"):
        module.validate_proposal(REPO, proposal)


def test_governed_artifacts_remain_pending_until_human_confirmation():
    holdouts = json.loads((REPO / "data" / "day-zero-holdouts.json").read_text())
    audit = json.loads((REPO / "data" / "day-zero-anchor-audit.json").read_text())

    assert holdouts["entries"]
    assert all(
        row["review_status"] == "candidate_pending_human_review"
        for row in holdouts["entries"]
    )
    assert audit["attention_required"]
    assert audit["summary"]["attention_required"] == len(audit["attention_required"])
