from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "tools" / "render_persona_uat_record.py"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def attempt(
    *,
    verdict: str,
    journey: str = "pitch-home",
    story: str = "US-1-01",
    persona: str = "A1",
    viewport: str = "desktop",
    digest: str = "a" * 64,
    canary: bool = False,
    shot_path: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "journey": journey,
        "story": story,
        "persona": persona,
        "viewport": viewport,
        "verdict": verdict,
        "first_failure": None if verdict == "PASS" else "assert text (check 1): missing",
        "digest": digest,
        "duration_ms": 125,
        "canary": canary,
    }
    if shot_path:
        value["shot_path"] = shot_path
    return value


def run_file(run_id: str, started: str, attempts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "run_id": run_id,
        "env": "local",
        "base": "http://127.0.0.1:8765",
        "started": started,
        "build": {
            "spine_build_id": "spine-one",
            "git_base_sha": "base-one",
            "release_sha": None,
        },
        "attempts": attempts,
    }


def invoke(tmp_path: Path, *, stories: str | None, journeys: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    runs = tmp_path / "runs"
    out = tmp_path / "record.md"
    journey_path = tmp_path / "journeys.json"
    write_json(journey_path, {"journeys": journeys})
    command = [
        sys.executable,
        str(RENDERER),
        "--runs",
        str(runs),
        "--out",
        str(out),
        "--journeys",
        str(journey_path),
    ]
    if stories is not None:
        story_path = tmp_path / "stories.md"
        story_path.write_text(stories, encoding="utf-8")
        command.extend(["--stories", str(story_path)])
    else:
        command.extend(["--stories", str(tmp_path / "absent-stories.md")])
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    result.record_path = out  # type: ignore[attr-defined]
    return result


def test_latest_attempt_is_current_and_every_attempt_stays_in_history(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    write_json(
        runs / "20260902T120000Z-local.json",
        run_file("first", "2026-09-02T12:00:00Z", [attempt(verdict="FAIL", digest="1" * 64)]),
    )
    write_json(
        runs / "20260902T120100Z-local.json",
        run_file("second", "2026-09-02T12:01:00Z", [attempt(verdict="PASS", digest="2" * 64)]),
    )

    result = invoke(
        tmp_path,
        stories="## US-1-01 — Read the pitch\n\n1. See the proposition.\n",
        journeys=[{"id": "pitch-home", "story": "US-1-01", "persona": "A1", "binding": "steps"}],
    )

    assert result.returncode == 0, result.stderr
    record = result.record_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    current = record.split("## Current verdicts", 1)[1].split("## Per-persona counts", 1)[0]
    history = record.split("## Attempt history", 1)[1]
    assert "| US-1-01 | A1 | local | desktop | PASS |" in current
    assert "2" * 64 in current
    assert "| first |" in history and "| FAIL |" in history
    assert "| second |" in history and "| PASS |" in history


def test_latest_build_replaces_older_build_for_an_environment(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    older = run_file("older", "2026-09-02T12:00:00Z", [attempt(verdict="PASS")])
    newer = run_file("newer", "2026-09-02T12:05:00Z", [attempt(verdict="FAIL", digest="3" * 64)])
    newer["build"] = {"spine_build_id": "spine-two", "git_base_sha": "base-two", "release_sha": "release-two"}
    write_json(runs / "older.json", older)
    write_json(runs / "newer.json", newer)

    result = invoke(
        tmp_path,
        stories="## US-1-01 — Read the pitch\n\n1. See the proposition.\n",
        journeys=[{"id": "pitch-home", "story": "US-1-01", "persona": "A1", "binding": "steps"}],
    )

    assert result.returncode == 0, result.stderr
    record = result.record_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    current = record.split("## Current verdicts", 1)[1].split("## Per-persona counts", 1)[0]
    assert "| US-1-01 | A1 | local | desktop | FAIL |" in current
    assert "spine-two" in current
    assert "spine-one" not in current


def test_canaries_are_excluded_from_persona_counts_and_retained_shots_are_listed(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    write_json(
        runs / "run.json",
        run_file(
            "run",
            "2026-09-02T12:00:00Z",
            [
                attempt(verdict="PASS"),
                attempt(
                    verdict="FAIL",
                    journey="a1-canary",
                    story="US-1-99",
                    digest="4" * 64,
                    canary=True,
                    shot_path="build/uat/shots/run/a1-canary-desktop.png",
                ),
            ],
        ),
    )

    result = invoke(
        tmp_path,
        stories=None,
        journeys=[
            {"id": "pitch-home", "story": "US-1-01", "persona": "A1", "binding": "steps"},
            {"id": "a1-canary", "story": "US-1-99", "persona": "A1", "binding": "steps", "canary": True},
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "story coverage check skipped" in result.stdout.lower()
    record = result.record_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    counts = record.split("## Per-persona counts", 1)[1].split("## Retained screenshots", 1)[0]
    assert "| A1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 |" in counts
    assert "a1-canary-desktop.png" in record
    assert "4" * 64 in record


def test_orphaned_story_is_reported_and_exits_nonzero(tmp_path: Path) -> None:
    write_json(
        tmp_path / "runs" / "run.json",
        run_file("run", "2026-09-02T12:00:00Z", [attempt(verdict="PASS")]),
    )

    result = invoke(
        tmp_path,
        stories=(
            "## US-1-01 — Read the pitch\n\n1. See the proposition.\n\n"
            "## US-2-01 — Open a matter\n\n1. See the packet.\n"
        ),
        journeys=[{"id": "pitch-home", "story": "US-1-01", "persona": "A1", "binding": "steps"}],
    )

    assert result.returncode != 0
    assert "US-2-01" in result.stderr


def test_unrun_catalog_stories_are_rendered_as_not_run(tmp_path: Path) -> None:
    result = invoke(
        tmp_path,
        stories="## US-3-01 — Review a score\n\n1. See the rubric.\n",
        journeys=[
            {
                "id": "instructor-rubric",
                "story": "US-3-01",
                "persona": "A3",
                "viewports": ["desktop", "phone"],
                "binding": "harness",
                "harness": {"command": "node test.js", "story_checks": [1]},
            }
        ],
    )

    assert result.returncode == 0, result.stderr
    record = result.record_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "| US-3-01 | A3 | unrun | n/a | NOT RUN |" in record
    assert "node test.js" in record


def test_blocked_is_an_accepted_verdict_and_has_its_own_persona_count(tmp_path: Path) -> None:
    blocked = attempt(verdict="BLOCKED")
    blocked["first_failure"] = "credential TEST_UAT_CREDENTIAL unavailable"
    write_json(
        tmp_path / "runs" / "run.json",
        run_file("run", "2026-09-02T12:00:00Z", [blocked]),
    )

    result = invoke(
        tmp_path,
        stories="## US-1-01 — Read the pitch\n\n1. See the proposition.\n",
        journeys=[{"id": "pitch-home", "story": "US-1-01", "persona": "A1", "binding": "harness"}],
    )

    assert result.returncode == 0, result.stderr
    record = result.record_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "| US-1-01 | A1 | local | desktop | BLOCKED |" in record
    counts = record.split("## Per-persona counts", 1)[1].split("## Retained screenshots", 1)[0]
    assert "| A1 | 1 | 0 | 0 | 0 | 1 | 0 | 0 |" in counts
