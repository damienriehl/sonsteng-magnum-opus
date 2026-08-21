#!/usr/bin/env python3
"""Proof-harness tests for the Day Zero migration (U6)."""

from __future__ import annotations

import os
import json
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import day_zero_equivalence as dze  # noqa: E402
import stamp_block_ids as sb  # noqa: E402


def proof(path="data/matters/m01/facts.json", literal="2026-02-16", offset=15):
    before = b'{"hearing_date": "2026-02-16"}\n'
    addition = b', "hearing_date_day_zero_offset": 15'
    insert = before.index(b"}")
    after = before[:insert] + addition + before[insert:]
    touched = [path]
    return (touched, {path: before}, {path: after},
            [dze.FileProof(path, (dze.ReverseEdit(insert, insert + len(addition), b""),))],
            [dze.DateProof(path, "hearing_date", literal, "2026-02-01", offset,
                           "json_sibling", path, "hearing_date_day_zero_offset")])


def test_file_round_trip_passes_and_reports_counts():
    result = dze.file_round_trip(*proof())
    assert result.converted_date_count == 1
    assert result.proof_covered_date_count == 1
    assert result.files_checked == 1


def test_shifted_offset_fails_naming_file_and_date():
    args = proof(offset=16)
    with pytest.raises(dze.EquivalenceError, match=r"facts\.json.*2026-02-16"):
        dze.file_round_trip(*args)


def test_count_mismatch_fails_and_reports_both_counts():
    touched, before, after, files, dates = proof()
    with pytest.raises(
        dze.EquivalenceError, match=r"converted-date count 2.*proof-covered-date count 1"
    ):
        dze.file_round_trip(touched, before, after, files, dates, converted_date_count=2)


def test_whole_touched_set_includes_business_file_outside_editor_map():
    touched, before, after, files, dates = proof()
    business = "data/matters/m01/business/business.json"
    before[business] = b'{"formed": "February 16, 2026"}\n'
    addition = b', "formed_day_zero_offset": 15'
    insert = before[business].index(b"}")
    after[business] = before[business][:insert] + addition + before[business][insert:]
    files.append(dze.FileProof(
        business, (dze.ReverseEdit(insert, insert + len(addition), b""),)
    ))
    dates.append(dze.DateProof(business, "formed", "February 16, 2026",
                               "2026-02-01", 15, "json_sibling", business,
                               "formed_day_zero_offset"))
    touched.append(business)
    result = dze.file_round_trip(touched, before, after, files, dates)
    assert result.files_checked == 2
    assert result.proof_covered_date_count == 2


def test_missing_touched_file_fails_even_when_all_dates_have_proofs():
    touched, before, after, files, dates = proof()
    before["data/matters/m01/extra.json"] = b"{}\n"
    after["data/matters/m01/extra.json"] = b"{}\n"
    touched.append("data/matters/m01/extra.json")
    with pytest.raises(dze.EquivalenceError, match="extra.json.*no file proof"):
        dze.file_round_trip(touched, before, after, files, dates)


def test_mutation_canary_proves_byte_comparison_can_fail():
    touched, before, after, files, dates = proof()
    after[files[0].path] = after[files[0].path].replace(b"hearing_date", b"hearing_DATA", 1)
    with pytest.raises(dze.EquivalenceError, match=r"facts\.json.*byte mismatch"):
        dze.file_round_trip(touched, before, after, files, dates)


def test_emitted_offset_mutation_fails_even_when_proof_is_still_correct():
    touched, before, after, files, dates = proof()
    path = dates[0].storage_path
    after[path] = after[path].replace(b'hearing_date_day_zero_offset": 15',
                                      b'hearing_date_day_zero_offset": 16')
    addition = b', "hearing_date_day_zero_offset": 16'
    insert = before[path].index(b"}")
    files[0] = dze.FileProof(path, (dze.ReverseEdit(insert, insert + len(addition), b""),))
    with pytest.raises(dze.EquivalenceError, match="emitted day_zero_offset"):
        dze.file_round_trip(touched, before, after, files, dates)


def test_authoritative_touched_set_must_equal_snapshots_and_proofs():
    touched, before, after, files, dates = proof()
    with pytest.raises(dze.EquivalenceError, match="before snapshots differ"):
        dze.file_round_trip(touched + ["data/matters/m01/undeclared.json"],
                            before, after, files, dates)


def test_existing_identity_checker_rejects_changed_bid_and_text():
    before = {"pages": {"p": [{
        "index": 0, "source_ref": "data/x.md#b11111111",
        "original_text": "February 16, 2026", "kind": "prose", "json_path": None,
    }]}}
    changed_bid = {"pages": {"p": [{
        **before["pages"]["p"][0], "source_ref": "data/x.md#b22222222",
    }]}}
    changed_text = {"pages": {"p": [{
        **before["pages"]["p"][0], "original_text": "February 17, 2026",
    }]}}
    # equivalence_check intentionally compares source files, not locator IDs;
    # U6 adds an explicit durable-ID guard without changing that established API.
    assert sb.equivalence_check(before, changed_bid) == []
    assert dze.block_identity_check(before, changed_bid)
    assert sb.equivalence_check(before, changed_text)
    assert dze.block_identity_check(before, changed_text)


def _durable_sidecar_proof(identity_fields):
    sidecar = "data/matters/m01/date-offsets.json"
    durable = "raw:0123456789abcdef:occurrence:1:date:0"
    entry = {
        "source": "data/matters/m01/facts.md", "locator": 0,
        "literal": "2026-02-16", "day_zero_offset": 15,
        **identity_fields,
    }
    payload = json.dumps({
        "anchor": "2026-02-01", "entries": [entry],
    }).encode()
    return ([sidecar], {sidecar: b""}, {sidecar: payload},
            [dze.FileProof(sidecar, (dze.ReverseEdit(0, len(payload), b""),))],
            [dze.DateProof(entry["source"], durable, entry["literal"],
                           "2026-02-01", 15, "prose_sidecar", sidecar, 0)])


def test_durable_locator_proof_passes_without_pretending_to_be_a_block_id():
    durable = "raw:0123456789abcdef:occurrence:1:date:0"
    result = dze.file_round_trip(*_durable_sidecar_proof({"durable_locator": durable}))
    assert result.proof_covered_date_count == 1


@pytest.mark.parametrize("identity", [
    {},
    {"block_id": "b:deadbeef",
     "durable_locator": "raw:0123456789abcdef:occurrence:1:date:0"},
])
def test_prose_sidecar_requires_exactly_one_identity_contract(identity):
    with pytest.raises(dze.EquivalenceError, match="exactly one"):
        dze.file_round_trip(*_durable_sidecar_proof(identity))
