from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "tools" / "apply_day_zero_review.py"
PROPOSAL_PATH = REPO / "docs" / "evidence" / "2026-08-17-day-zero-review-proposal.json"
sys.path.insert(0, str(MODULE_PATH.parent))


def load_module():
    spec = importlib.util.spec_from_file_location("apply_day_zero_review", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_inputs(module):
    proposal = json.loads(PROPOSAL_PATH.read_text())
    resolved_holdouts = json.loads(
        (REPO / "data" / "day-zero-holdouts.json").read_text()
    )
    resolved_audit = json.loads(
        (REPO / "data" / "day-zero-anchor-audit.json").read_text()
    )
    holdouts, audit = module._synthetic_pending(
        proposal, resolved_holdouts, resolved_audit
    )
    return proposal, holdouts, audit


def test_apply_review_resolves_every_key_without_using_array_position():
    module = load_module()
    proposal, holdouts, audit = load_inputs(module)
    proposal["proposals"].reverse()

    resolved_holdouts, resolved_audit = module.apply_review(
        REPO, proposal, holdouts, audit
    )

    assert resolved_holdouts["summary"]["count"] == 635
    assert all(
        row["review_status"] == "declared_absolute_holdout"
        for row in resolved_holdouts["entries"]
    )
    assert resolved_audit["summary"]["converted_dates"] == 1236
    assert resolved_audit["summary"]["attention_required"] == 0
    assert resolved_audit["attention_required"] == []
    assert resolved_audit["summary"]["reconciled_category_total"] == 1242


def test_duplicate_raw_locator_is_disambiguated_by_literal_and_gets_durable_locator():
    module = load_module()
    proposal, holdouts, audit = load_inputs(module)

    _, resolved_audit = module.apply_review(REPO, proposal, holdouts, audit)

    rows = [
        row for row in resolved_audit["entries"]
        if row["source"].endswith("exhibit-medical-summary.md")
        and row["literal"] in {"2025-02-24", "2025-07-18"}
    ]
    assert len(rows) == 2
    assert len({row["locator"] for row in rows}) == 2
    assert all(row["locator"].startswith("raw:") for row in rows)
    assert all(row["source_locator"] == "line:9:raw-occurrence:1" for row in rows)


def test_repeated_identical_raw_literals_keep_distinct_durable_identities():
    module = load_module()
    proposal, holdouts, audit = load_inputs(module)

    _, resolved_audit = module.apply_review(REPO, proposal, holdouts, audit)

    repeated = [
        row for row in resolved_audit["entries"]
        if (
            row["source"].endswith("exhibit-medical-summary.md")
            and row["literal"] == "2025-02-12"
        ) or (
            row["source"].endswith("timekeeping-records.md")
            and row["literal"] == "2025-10-14"
            and row.get("source_locator", "").startswith("line:")
        )
    ]
    assert len(repeated) == 4
    assert len({(row["source"], row["locator"], row["literal"]) for row in repeated}) == 4
    identities = [
        (row["source"], row["locator"], row["literal"])
        for row in resolved_audit["entries"]
    ]
    assert len(identities) == len(set(identities))


def test_raw_occurrence_out_of_range_is_rejected():
    module = load_module()
    proposal, _, _ = load_inputs(module)
    row = next(
        row for row in proposal["proposals"]
        if row["source"].endswith("exhibit-medical-summary.md")
        and row["literal"] == "2025-02-12"
    )
    row = copy.deepcopy(row)
    row["locator"] = "line:7:raw-occurrence:999"
    row["key"] = module.proposal_key(row)

    with pytest.raises(ValueError, match="raw locator no longer resolves"):
        module._durable_locator(REPO, row)


def test_apply_review_rejects_missing_unknown_and_replayed_keys():
    module = load_module()
    proposal, holdouts, audit = load_inputs(module)

    missing = copy.deepcopy(proposal)
    missing["proposals"].pop()
    with pytest.raises(ValueError, match="coverage mismatch"):
        module.apply_review(REPO, missing, holdouts, audit)

    unknown = copy.deepcopy(proposal)
    unknown["proposals"][0]["literal"] = "2099-01-01"
    unknown["proposals"][0]["key"] = module.proposal_key(unknown["proposals"][0])
    with pytest.raises(ValueError, match="coverage mismatch"):
        module.apply_review(REPO, unknown, holdouts, audit)

    replayed_holdouts = copy.deepcopy(holdouts)
    replayed_holdouts["entries"][0]["review_status"] = "declared_absolute_holdout"
    with pytest.raises(ValueError, match="not pending"):
        module.apply_review(REPO, proposal, replayed_holdouts, audit)


def test_committed_governed_files_are_fully_resolved_and_approved():
    approval = REPO / "docs" / "decisions" / "2026-08-17-day-zero-review-approval.md"
    holdouts = json.loads((REPO / "data" / "day-zero-holdouts.json").read_text())
    audit = json.loads((REPO / "data" / "day-zero-anchor-audit.json").read_text())

    assert approval.is_file()
    assert "87072b82835eb877c49250b31f20f55689fd4e958d209e4426f25897b5f17293" in approval.read_text()
    assert holdouts["entries"]
    assert not any(
        row["review_status"] == "candidate_pending_human_review"
        for row in holdouts["entries"]
    )
    assert audit["attention_required"] == []
    assert audit["summary"]["attention_required"] == 0


def test_day_zero_dry_run_round_trips_the_approved_governed_files(tmp_path):
    audit = tmp_path / "audit.json"
    holdouts = tmp_path / "holdouts.json"

    subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "day_zero.py"),
            "--repo", str(REPO),
            "--audit-output", str(audit),
            "--holdouts-output", str(holdouts),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert audit.read_bytes() == (REPO / "data" / "day-zero-anchor-audit.json").read_bytes()
    assert holdouts.read_bytes() == (REPO / "data" / "day-zero-holdouts.json").read_bytes()


def test_governed_pair_write_restores_both_originals_when_second_replace_fails(
    tmp_path, monkeypatch
):
    module = load_module()
    holdouts = tmp_path / "holdouts.json"
    audit = tmp_path / "audit.json"
    holdouts.write_text("old holdouts\n")
    audit.write_text("old audit\n")
    real_replace = module.os.replace
    replace_count = 0

    def fail_second_replace(source, target):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("injected second replacement failure")
        return real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="injected second replacement failure"):
        module._write_governed_pair(
            holdouts, "new holdouts\n", audit, "new audit\n"
        )

    assert holdouts.read_text() == "old holdouts\n"
    assert audit.read_text() == "old audit\n"
