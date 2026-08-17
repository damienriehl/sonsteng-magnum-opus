import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import audit_nine_parts as audit  # noqa: E402


SECTION_TEXT = " ".join(["substantive"] * 160)
SCHEMA_SECTION_KEYS = (
    "intro",
    "objectives",
    "activities",
    "instructions",
    "case_file",
    "history",
    "considerations",
    "substantive_info",
)


def _write_fixture(tmp_path: Path, *, complete: bool = True) -> Path:
    root = tmp_path / "repo"
    matter = root / "data" / "matters" / "m01-example"
    exercise = matter / "exercise"
    exercise.mkdir(parents=True)
    (matter / "case-file").mkdir()
    (matter / "personas").mkdir()
    (matter / "facts.md").write_text(SECTION_TEXT, encoding="utf-8")
    (matter / "personas" / "witness.json").write_text("{}\n", encoding="utf-8")
    (matter / "case-file" / "witness-statement.md").write_text(
        "witness statement\n", encoding="utf-8"
    )
    for name in ("syllabus.md", "dates-method.md", "assessment-feedback-form.md"):
        (exercise / name).write_text(SECTION_TEXT, encoding="utf-8")

    sections = {
        key: {"title": key.replace("_", " ").title(), "body_md": SECTION_TEXT}
        for key in SCHEMA_SECTION_KEYS
    }
    sections["case_file"] = {
        "title": "Case File",
        "files": ["case-file/witness-statement.md"],
    }
    packet = {"matter_id": "m01", "sections": sections}
    (exercise / "exercise.json").write_text(
        json.dumps(packet, indent=2) + "\n", encoding="utf-8"
    )
    if not complete:
        (exercise / "syllabus.md").unlink()
    return root


def _issue_classes(report: dict) -> set[str]:
    return {
        issue["class"]
        for matter in report["matters"]
        for issue in matter["issues"]
    }


def test_conforming_matter_reports_all_nine_parts(tmp_path):
    report = audit.audit_repository(_write_fixture(tmp_path))

    assert report["summary"] == {
        "matters_audited": 1,
        "conforming_matters": 1,
        "nonconforming_matters": 0,
        "mechanical_gaps": 0,
        "structural_gaps": 0,
    }
    assert len(report["matters"][0]["parts"]) == 9
    assert report["matters"][0]["conforms"] is True


@pytest.mark.parametrize(
    ("mutate", "expected_class"),
    [
        (lambda root: (root / "data/matters/m01-example/exercise/syllabus.md").unlink(), "structural_gap"),
        (
            lambda root: _edit_packet(root, lambda packet: packet["sections"].pop("intro")),
            "missing_section",
        ),
        (
            lambda root: _edit_packet(
                root, lambda packet: packet["sections"]["objectives"].update(body_md="too short")
            ),
            "trivial_section",
        ),
        (
            lambda root: _edit_packet(
                root,
                lambda packet: packet["sections"]["case_file"].update(
                    files=["case-file/absent.md"]
                ),
            ),
            "broken_file_reference",
        ),
        (
            lambda root: (root / "data/matters/m01-example/exercise/exercise.json").write_text(
                "{not json", encoding="utf-8"
            ),
            "invalid_json",
        ),
        (
            lambda root: (root / "data/matters/m01-example/facts.md").unlink(),
            "missing_corpus_evidence",
        ),
    ],
)
def test_each_nonconformance_class_is_reported(tmp_path, mutate, expected_class):
    root = _write_fixture(tmp_path)
    mutate(root)

    report = audit.audit_repository(root)

    assert report["matters"][0]["conforms"] is False
    assert expected_class in _issue_classes(report)


def _edit_packet(root: Path, edit) -> None:
    path = root / "data/matters/m01-example/exercise/exercise.json"
    packet = json.loads(path.read_text(encoding="utf-8"))
    edit(packet)
    path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")


def test_report_is_deterministic_and_contains_human_summary(tmp_path):
    root = _write_fixture(tmp_path)

    first = audit.audit_repository(root)
    second = audit.audit_repository(root)

    assert first == second
    assert audit.human_summary(first) == (
        "Nine-part audit: 1 matter; 1 conforming; 0 nonconforming; "
        "0 mechanical gaps; 0 structural gaps."
    )


def test_canary_broken_fixture_makes_cli_fail(tmp_path):
    root = _write_fixture(tmp_path, complete=False)

    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audit_nine_parts.py"), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["summary"]["nonconforming_matters"] == 1
    assert "1 nonconforming" in completed.stderr


def test_report_only_audit_leaves_schema_byte_identical(tmp_path):
    root = _write_fixture(tmp_path)
    schema = root / "data" / "schemas" / "exercise.schema.json"
    schema.parent.mkdir(parents=True)
    original = b'{"title":"sentinel exercise schema"}\n'
    schema.write_bytes(original)

    audit.audit_repository(root)

    assert schema.read_bytes() == original
