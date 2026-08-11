import json
import pathlib
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
from build_prod_review_backfill import BackfillError, build_payload


def evidence():
    return {"schema_version":1,"batches":[
      {"batch_id":"b1","base_sha":"a"*40,"commit_sha":"b"*40,"phase":"done"},
      {"batch_id":"b2","base_sha":"b"*40,"commit_sha":"c"*40,"phase":"done"}],
      "suggestions":[
       {"id":"s1","editor":"JOS","kind":"prose","source_ref":"data/copy/home.json#lead",
        "original_text":"Old copy.","new_text":"New copy.","apply_batch_id":"b1","created_at":1},
       {"id":"s2","editor":"JOS","kind":"prose","source_ref":"data/copy/skills.json#lead",
        "original_text":"One", "new_text":"Two!","apply_batch_id":"b2","created_at":2}]}


def test_builds_deterministic_source_scoped_payload():
    first = build_payload(evidence(),"migration-1","a"*40)
    second = build_payload(evidence(),"migration-1","a"*40)
    assert first == second
    assert [item["source_ref"] for item in first["revisions"]] == [
      "data/copy/home.json#lead","data/copy/skills.json#lead"]
    assert len(first["revisions"][0]["batch_chain"]) == 1
    assert len(first["revisions"][1]["batch_chain"]) == 2
    assert first["revisions"][1]["suggestion_evidence"] == [{
      "suggestion_id":"s2","batch_id":"b2","commit_sha":"c"*40}]


def test_rejects_ambiguous_chain_and_missing_durable_identity():
    bad = evidence(); bad["batches"][1]["base_sha"] = "a"*40
    with pytest.raises(BackfillError,match="unambiguous"): build_payload(bad,"m","a"*40)
    bad = evidence(); bad["suggestions"][0]["source_ref"] = "not-durable"
    with pytest.raises(BackfillError,match="durable"): build_payload(bad,"m","a"*40)
