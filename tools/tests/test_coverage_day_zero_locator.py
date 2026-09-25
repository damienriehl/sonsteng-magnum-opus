"""Durable locators bind dates to content across real source-file edits."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero_locator as dl


def raw_row(locator="line:1:raw-occurrence:1", literal="2026-09-19"):
    return {"key": "reviewed-date", "source": "source.md", "literal": literal, "locator": locator}


def test_raw_locator_survives_line_movement_whitespace_and_date_conversion(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("Deadline 2026-09-19; hearing September 21, 2026.\n")
    row = raw_row()
    locator = dl.durable_locator(tmp_path, row)
    assert dl.resolve_durable_locator(source, locator, row["literal"]) == 0
    source.write_text("New introductory paragraph\n\nDeadline   2027-01-01; hearing January 3, 2027.\n")
    assert dl.resolve_durable_locator(source, locator, "2027-01-01") == 0
    assert dl.durable_locator(tmp_path, raw_row(literal="2027-01-01")) == locator


def test_repeated_literal_on_different_lines_binds_selected_context(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("First 2026-09-19\nSecond 2026-09-19\n")
    row = raw_row("line:999:raw-occurrence:2")
    assert dl.resolve_raw_occurrence(tmp_path, row) == ("Second 2026-09-19", 0, 2)
    locator = dl.durable_locator(tmp_path, row)
    assert dl.resolve_durable_locator(source, locator, row["literal"]) == 0
    source.write_text("Second 2026-09-19\nFirst 2026-09-19\n")
    with pytest.raises(ValueError, match="no longer resolves"):
        dl.resolve_durable_locator(source, locator, row["literal"])


@pytest.mark.parametrize("locator", ["bad", "line:x:raw-occurrence:1", "line:1:raw-occurrence:0", "line:1:raw-occurrence:2"])
def test_raw_review_locator_rejects_unsupported_or_absent_occurrence(tmp_path, locator):
    (tmp_path / "source.md").write_text("Deadline 2026-09-19\n")
    with pytest.raises(ValueError, match="unsupported|no longer resolves"):
        dl.resolve_raw_occurrence(tmp_path, raw_row(locator))


@pytest.mark.parametrize("replacement", ["", "Deadline 2026-09-20", "Changed 2026-09-19"])
def test_durable_raw_locator_rejects_deleted_changed_or_recontextualized_date(tmp_path, replacement):
    source = tmp_path / "source.md"
    source.write_text("Deadline 2026-09-19\n")
    locator = dl.durable_locator(tmp_path, raw_row())
    source.write_text(replacement)
    with pytest.raises(ValueError, match="no longer resolves"):
        dl.resolve_durable_locator(source, locator, "2026-09-19")


def test_raw_locator_rejects_wrong_date_ordinal_even_with_matching_fingerprint(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("Dates 2026-09-19 and 2026-09-20")
    locator = dl.durable_locator(tmp_path, raw_row())
    for ordinal in (1, 2):
        with pytest.raises(ValueError, match="no longer resolves"):
            dl.resolve_durable_locator(source, locator.rsplit(":", 1)[0] + f":{ordinal}", "2026-09-19")


def test_json_locator_round_trip_nested_list_and_revised_dates(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"sections": [{"text": "From 2026-09-19 through September 21, 2026"}]}))
    row = {"locator": "sections.0.text:date:1"}
    locator = dl.durable_locator(tmp_path, row)
    assert locator == "json:sections.0.text:date:1"
    assert dl.resolve_durable_locator(source, locator, "September 21, 2026") == 1
    source.write_text(json.dumps({"sections": [{"text": "From 2027-01-01 through January 3, 2027"}]}))
    assert dl.resolve_durable_locator(source, locator, "January 3, 2027") == 1


@pytest.mark.parametrize("value,ordinal,literal", [
    (None, 0, "2026-09-19"), (42, 0, "2026-09-19"),
    ("", 0, "2026-09-19"), ("2026-09-19", 1, "2026-09-19"),
    ("2026-09-19", 0, "2026-09-20"),
])
def test_json_locator_rejects_nonstring_absent_or_changed_dates(tmp_path, value, ordinal, literal):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"field": value}))
    with pytest.raises(ValueError, match="JSON locator no longer resolves"):
        dl.resolve_durable_locator(source, f"json:field:date:{ordinal}", literal)


@pytest.mark.parametrize("locator", ["", "line:1", "raw:0123456789abcdef:occurrence:0:date:0", "json:field:date:-1"])
def test_unsupported_durable_locators_are_rejected_without_reading_files(tmp_path, locator):
    with pytest.raises(ValueError, match="unsupported durable locator"):
        dl.resolve_durable_locator(tmp_path / "absent.md", locator, "2026-09-19")


def test_already_stable_nondate_identifier_is_preserved(tmp_path):
    assert dl.durable_locator(tmp_path, {"locator": "block:deadbeef"}) == "block:deadbeef"


def test_date_boundaries_do_not_match_digits_embedded_in_longer_numbers(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("12026-09-19 2026-09-190; actual (2026-09-19)")
    assert dl.resolve_raw_occurrence(tmp_path, raw_row()) == (source.read_text(), 0, 1)


@pytest.mark.parametrize("document,locator,error", [
    ('{broken', 'json:field:date:0', json.JSONDecodeError),
    ('{}', 'json:field:date:0', KeyError),
    ('{"rows":[]}', 'json:rows.0:date:0', IndexError),
    ('{"rows":[]}', 'json:rows.invalid:date:0', ValueError),
])
def test_invalid_json_source_or_path_does_not_silently_resolve(tmp_path, document, locator, error):
    source = tmp_path / "source.json"
    source.write_text(document)
    with pytest.raises(error):
        dl.resolve_durable_locator(source, locator, "2026-09-19")


def test_missing_source_is_reported_by_raw_review_and_json_durable_resolution(tmp_path):
    with pytest.raises(FileNotFoundError):
        dl.resolve_raw_occurrence(tmp_path, raw_row())
    with pytest.raises(FileNotFoundError):
        dl.resolve_durable_locator(tmp_path / "source.json", "json:field:date:0", "2026-09-19")
