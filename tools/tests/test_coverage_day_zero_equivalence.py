"""Adversarial snapshots and disk-backed Day Zero proof integration."""
import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero_equivalence as eq


def snapshots(document, date_proof=None):
    payload = json.dumps(document).encode()
    return (["offsets.json"], {"offsets.json": b""}, {"offsets.json": payload},
            [eq.FileProof("offsets.json", (eq.ReverseEdit(0, len(payload), b""),))],
            [] if date_proof is None else [date_proof])


def sidecar():
    proof = eq.DateProof("facts.md", "b123:0", "2026-02-16", "2026-02-01", 15,
                         "prose_sidecar", "offsets.json", 0)
    document = {"anchor": proof.anchor, "entries": [{
        "source": proof.path, "locator": 0, "literal": proof.literal,
        "day_zero_offset": 15, "block_id": "b123"}]}
    return document, proof


def test_disk_snapshots_restore_multiple_files_and_nested_json_dates(tmp_path):
    before = {"facts.json": b'{"items":[{"date":"Feb 16, 2026"}]}',
              "notes.md": "Café: original\n".encode()}
    after = {"facts.json": b'{"items":[{"date":"Feb 16, 2026","offset":15}]}',
             "notes.md": "Café: revised\n".encode()}
    for name, value in after.items():
        (tmp_path / name).write_bytes(value)
    insert = before["facts.json"].index(b"}")
    note_start = after["notes.md"].index(b"revised")
    proofs = [eq.FileProof("facts.json", (eq.ReverseEdit(insert, insert + 12, b""),)),
              eq.FileProof("notes.md", (eq.ReverseEdit(note_start, note_start + 7, b"original"),))]
    dates = [eq.DateProof("facts.json", "items.0.date", "Feb 16, 2026", "2026-02-01",
                          15, "json_sibling", "facts.json", "items.0.offset")]
    result = eq.file_round_trip(list(before), before,
                               {name: (tmp_path / name).read_bytes() for name in before},
                               proofs, dates)
    assert result == eq.RoundTripResult(2, 1, 1)


def test_empty_authoritative_set_is_a_valid_zero_conversion():
    assert eq.file_round_trip([], {}, {}, [], []) == eq.RoundTripResult(0, 0, 0)


@pytest.mark.parametrize("span", [(-1, 1), (2, 1), (0, 4)])
def test_reverse_spans_must_be_valid_in_original_after_bytes(span):
    with pytest.raises(eq.EquivalenceError, match="invalid reverse-edit span"):
        eq._restore(b"abc", [eq.ReverseEdit(*span, b"")], "facts.md")


def test_overlapping_reverse_edits_are_rejected():
    with pytest.raises(eq.EquivalenceError, match="overlapping"):
        eq._restore(b"abcdef", [eq.ReverseEdit(1, 4, b""), eq.ReverseEdit(3, 5, b"")], "f")


def test_reverse_edits_are_order_independent_and_allow_adjacent_changes():
    assert eq._restore(b"ABCDEF", [eq.ReverseEdit(0, 3, b"x"),
                                     eq.ReverseEdit(3, 6, b"yz")], "f") == b"xyz"


@pytest.mark.parametrize("change,message", [
    ("duplicate-proof", "duplicate file proof"),
    ("duplicate-touched", "contains duplicates"),
    ("extra-after", "snapshots differ"),
    ("missing-after", "snapshots differ"),
    ("extra-proof", "outside touched set"),
])
def test_authoritative_set_rejects_inconsistent_inputs(change, message):
    touched, before, after, files, dates = snapshots({})
    if change == "duplicate-proof":
        files.append(files[0])
    elif change == "duplicate-touched":
        touched.append(touched[0])
    elif change == "extra-after":
        after["extra"] = b""
    elif change == "missing-after":
        after.clear()
    else:
        files.append(eq.FileProof("extra"))
    with pytest.raises(eq.EquivalenceError, match=message):
        eq.file_round_trip(touched, before, after, files, dates)


@pytest.mark.parametrize("changes,message", [
    ({"literal": "not a date"}, "unsupported date literal"),
    ({"anchor": "2026-99-01"}, "unsupported date literal"),
    ({"storage_kind": "unsupported"}, "unknown storage kind"),
    ({"storage_path": "missing.json"}, "cannot read emitted proof"),
    ({"storage_locator": 99}, "cannot read emitted proof"),
    ({"storage_locator": "bad-index"}, "cannot read emitted proof"),
])
def test_bad_date_proofs_report_context_instead_of_succeeding(changes, message):
    document, proof = sidecar()
    with pytest.raises(eq.EquivalenceError, match=message):
        eq.file_round_trip(*snapshots(document, dataclasses.replace(proof, **changes)))


@pytest.mark.parametrize("field,value,message", [
    ("anchor", "2026-01-01", "emitted anchor"),
    ("source", "other.md", "emitted source"),
    ("locator", 7, "emitted locator"),
    ("literal", "2026-02-17", "emitted literal"),
    ("day_zero_offset", 16, "emitted day_zero_offset"),
    ("block_id", "bother", "emitted block_id"),
    ("durable_locator", "raw:wrong:0", "emitted durable_locator"),
])
def test_sidecar_storage_is_checked_independently_of_reverse_byte_proof(field, value, message):
    document, proof = sidecar()
    if field == "anchor":
        document[field] = value
    else:
        document["entries"][0][field] = value
        if field == "durable_locator":
            del document["entries"][0]["block_id"]
    with pytest.raises(eq.EquivalenceError, match=message):
        eq.file_round_trip(*snapshots(document, proof))


def test_json_literal_mismatch_is_detected_even_when_offset_is_correct():
    proof = eq.DateProof("offsets.json", "date", "2026-02-16", "2026-02-01", 15,
                         "json_sibling", "offsets.json", "offset")
    with pytest.raises(eq.EquivalenceError, match="emitted literal"):
        eq.file_round_trip(*snapshots({"date": "2026-02-17", "offset": 15}, proof))


@pytest.mark.parametrize("payload", [b"\xff", b"{broken"])
def test_unreadable_storage_fails_with_a_bounded_proof_error(payload):
    _, proof = sidecar()
    with pytest.raises(eq.EquivalenceError, match="cannot read emitted proof"):
        eq.file_round_trip(["offsets.json"], {"offsets.json": b""},
                          {"offsets.json": payload},
                          [eq.FileProof("offsets.json", (eq.ReverseEdit(0, len(payload), b""),))],
                          [proof])
