from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "tools" / "persona_journeys.json"
RUNNER_PATH = ROOT / "tools" / "verify_persona_journeys.js"
STORIES_PATH = ROOT / "docs" / "uat" / "user-stories.md"
VALID_VIEWPORTS = {"desktop", "phone", "zoom200"}
VALID_OPS = {"goto", "click", "focus", "press", "type", "waitFor", "expectDownload", "assert"}
VALID_ASSERTIONS = {
    "selector",
    "text",
    "attr",
    "url",
    "consoleClean",
    "focusOn",
    "a11yName",
    "a11yRole",
    "a11yState",
    "readingOrder",
    "liveRegion",
}
EXECUTABLES = {"node", "python3", "python", "npx", "bash", "sh", "cd", "git", "pytest", "curl"}
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
STORY_HEADING_RE = re.compile(r"^#{1,6}\s+(US-\d+-(?:\d+|CANARY))\b", re.MULTILINE)
CHECK_RE = re.compile(r"^\s*(\d+)\.\s+", re.MULTILINE)


def catalog() -> list[dict[str, object]]:
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert value["schema_version"] == 1
    return value["journeys"]


def story_checks(text: str) -> dict[str, set[int]]:
    matches = list(STORY_HEADING_RE.finditer(text))
    result: dict[str, set[int]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1)] = {int(value) for value in CHECK_RE.findall(text[match.end():end])}
    return result


def site_path(url_path: str) -> Path:
    parsed = urlsplit(url_path)
    assert not parsed.scheme and not parsed.netloc, url_path
    relative = parsed.path.lstrip("/")
    if not relative or parsed.path.endswith("/"):
        relative += "index.html"
    target = (ROOT / "site" / relative).resolve()
    target.relative_to((ROOT / "site").resolve())
    return target


def test_catalog_has_unique_ids_valid_bindings_and_valid_steps() -> None:
    journeys = catalog()
    ids = [item["id"] for item in journeys]
    assert len(ids) == len(set(ids))
    for journey in journeys:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", journey["id"])
        assert re.fullmatch(r"US-\d+-(?:\d+|CANARY)", journey["story"])
        assert re.fullmatch(r"A(?:10|[1-9])", journey["persona"])
        assert journey["binding"] in {"steps", "harness", "command"}
        assert set(journey["viewports"]) <= VALID_VIEWPORTS
        assert journey["viewports"]
        bindings = [name for name in ("steps", "harness", "command") if name in journey]
        assert bindings == [journey["binding"]]
        if journey["binding"] != "steps":
            binding = journey[journey["binding"]]
            command = binding["command"]
            assert command
            assert binding["story_checks"]
            assert not re.search(r"<[^>]+>", command), f"{journey['id']}: placeholder in binding command"
            assert command.split(maxsplit=1)[0] in EXECUTABLES, f"{journey['id']}: binding command starts with prose"
            if "environments" in binding:
                assert isinstance(binding["environments"], list) and binding["environments"]
                assert all(isinstance(value, str) and value for value in binding["environments"])
            if "credential_gate" in binding:
                assert isinstance(binding["credential_gate"], str)
                assert ENV_NAME_RE.fullmatch(binding["credential_gate"])
            continue
        assert journey["steps"]
        for step in journey["steps"]:
            assert step["op"] in VALID_OPS
            if step["op"] == "assert":
                assert step["kind"] in VALID_ASSERTIONS
                assert isinstance(step.get("check"), int) and step["check"] > 0


def test_every_goto_path_exists_under_site() -> None:
    for journey in catalog():
        for step in journey.get("steps", []):
            if step["op"] == "goto":
                target = site_path(step["path"])
                assert target.is_file(), f"{journey['id']}: {step['path']} -> {target}"


def test_every_represented_persona_has_a_deliberate_failing_canary() -> None:
    journeys = catalog()
    personas = {item["persona"] for item in journeys}
    canary_personas = {item["persona"] for item in journeys if item.get("canary")}
    assert canary_personas == personas
    for journey in (item for item in journeys if item.get("canary")):
        assertions = [step for step in journey["steps"] if step["op"] == "assert"]
        assert assertions
        assert any("data-uat-canary" in step.get("selector", "") for step in assertions)


def test_viewport_contract_and_portable_puppeteer_resolution_are_pinned() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "desktop: {width: 1280, height: 900, deviceScaleFactor: 1}" in source
    assert "phone: {width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true}" in source
    assert "zoom200: {width: 640, height: 450, deviceScaleFactor: 2}" in source
    assert source.index("process.env.PUP_DIR") < source.index("'puppeteer'")
    assert "process.env.CHROME_BIN || process.env.CHROMIUM_PATH || '/snap/bin/chromium'" in source
    assert "process.env.HEADFUL !== '1' && process.env.HEADLESS !== '0'" in source
    assert "page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}])" in source
    assert "client.send('Browser.setDownloadBehavior'" in source
    assert "behavior: 'allowAndName'" in source
    assert "eventsEnabled: true" in source


def test_repaired_browser_journey_contracts_are_pinned() -> None:
    journeys = {journey["id"]: journey for journey in catalog()}
    download_selector = (
        '[data-catalog-id="m05"] '
        'a[href="../downloads/m05-dwi-meridian-student-materials.zip"]'
    )

    assert journeys["pitch-public-navigation"]["viewports"] == ["desktop"]
    phone_nav = journeys["pitch-phone-nav-hidden"]
    assert phone_nav["story"] == journeys["pitch-public-navigation"]["story"]
    assert phone_nav["viewports"] == ["phone", "zoom200"]
    assert {step["selector"]: step.get("visible") for step in phone_nav["steps"] if step["op"] == "assert"} == {
        "nav .navlinks": False,
        ".hero-cta": True,
    }

    for journey_id in ("student-matter-packet", "student-download-packet"):
        steps = journeys[journey_id]["steps"]
        assert steps[0] == {"op": "goto", "path": "/platform/matters/"}
        assert any(step.get("selector") == download_selector and step.get("visible") is True for step in steps)
        assert any(step["op"] == "expectDownload" and step.get("selector") == download_selector for step in steps)
    assert any(
        step == {"op": "goto", "path": "/platform/matters/m05-dwi-meridian/"}
        for step in journeys["student-matter-packet"]["steps"]
    )

    assert {"op": "click", "selector": "#SK-LP-07 > summary"} in journeys["instructor-skill-to-rubric"]["steps"]
    reaction_steps = journeys["pitch-empty-reactions"]["steps"]
    vote_index = reaction_steps.index(
        {"op": "click", "selector": "#fb .fbrow:first-child .opts button:first-child"}
    )
    assert reaction_steps[vote_index + 1] == {"op": "waitFor", "text": "1"}
    a11y_name = next(
        step for step in journeys["accessibility-keyboard-packet"]["steps"]
        if step.get("kind") == "a11yName"
    )
    assert a11y_name["expected"] == "MATTER LIBRARY 20 simulated matters"
    assert a11y_name["contains"] is True


def test_story_and_journey_coverage_is_two_way_when_u1_exists() -> None:
    if not STORIES_PATH.exists():
        pytest.skip("U1 docs/uat/user-stories.md has not landed; two-way story coverage is deferred explicitly")
    defined = set(story_checks(STORIES_PATH.read_text(encoding="utf-8")))
    mapped = {item["story"] for item in catalog()}
    assert mapped == defined, f"journey-only={sorted(mapped - defined)} story-only={sorted(defined - mapped)}"


def test_assertion_check_indices_exist_on_their_story_when_u1_exists() -> None:
    if not STORIES_PATH.exists():
        pytest.skip("U1 docs/uat/user-stories.md has not landed; acceptance-check validation is deferred explicitly")
    checks = story_checks(STORIES_PATH.read_text(encoding="utf-8"))
    for journey in catalog():
        assert journey["story"] in checks
        cited = set()
        if journey["binding"] == "steps":
            cited = {step["check"] for step in journey["steps"] if step["op"] == "assert"}
        else:
            cited = set(journey[journey["binding"]]["story_checks"])
        assert cited <= checks[journey["story"]], (
            f"{journey['id']} cites nonexistent checks {sorted(cited - checks[journey['story']])} "
            f"on {journey['story']}"
        )


def invoke_bindings(
    tmp_path: Path,
    journeys: list[dict[str, object]],
    *,
    only: str | None = None,
    env_label: str = "dev",
    timeout_ms: int = 1000,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object], Path]:
    catalog_path = tmp_path / "journeys.json"
    run_dir = tmp_path / "runs"
    shots_dir = tmp_path / "shots"
    catalog_path.write_text(json.dumps({"journeys": journeys}), encoding="utf-8")
    command = [
        "node",
        str(RUNNER_PATH),
        "--bindings",
        "--env-label",
        env_label,
        "--binding-timeout",
        str(timeout_ms),
        "--run-dir",
        str(run_dir),
        "--shots-dir",
        str(shots_dir),
        "--journeys",
        str(catalog_path),
    ]
    if only:
        command.extend(["--only", only])
    process_env = os.environ.copy()
    process_env.pop("TEST_UAT_CREDENTIAL", None)
    process_env.update(extra_env or {})
    result = subprocess.run(command, cwd=ROOT, env=process_env, text=True, capture_output=True, check=False)
    run_files = list(run_dir.glob("*.json"))
    assert len(run_files) == 1, result.stderr
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    return result, run, shots_dir


def bound_journey(journey_id: str, command: str, **binding_fields: object) -> dict[str, object]:
    return {
        "id": journey_id,
        "story": "US-1-01",
        "persona": "A1",
        "viewports": ["desktop"],
        "binding": "harness",
        "harness": {"command": command, "story_checks": [1], **binding_fields},
    }


def test_bindings_mode_executes_only_selected_bindings_and_records_log_digest(tmp_path: Path) -> None:
    journeys = [
        {
            "id": "browser-step",
            "story": "US-1-01",
            "persona": "A1",
            "viewports": ["desktop"],
            "binding": "steps",
            "steps": [{"op": "goto", "path": "/"}],
        },
        bound_journey("binding-pass", "sh -c 'printf \"binding ok\\n\"'"),
        bound_journey("binding-filtered", "node -e \"process.exit(9)\""),
    ]

    result, run, shots_dir = invoke_bindings(tmp_path, journeys, only="binding-pass")

    assert result.returncode == 0, result.stderr
    assert len(run["attempts"]) == 1
    attempt = run["attempts"][0]
    assert attempt["journey"] == "binding-pass"
    assert attempt["viewport"] == "n/a"
    assert attempt["verdict"] == "PASS"
    assert re.fullmatch(r"[0-9a-f]{64}", attempt["digest"])
    log_path = next(shots_dir.glob("*/binding-pass-binding.log"))
    assert log_path.read_text(encoding="utf-8") == "binding ok\n"


def test_binding_failures_keep_only_the_last_40_output_lines_in_first_failure(tmp_path: Path) -> None:
    command = "sh -c 'i=1; while [ $i -le 45 ]; do echo line-$i; i=$((i+1)); done; exit 7'"

    result, run, shots_dir = invoke_bindings(tmp_path, [bound_journey("binding-fail", command)])

    assert result.returncode == 1
    attempt = run["attempts"][0]
    assert attempt["verdict"] == "FAIL"
    assert "line-6" in attempt["first_failure"]
    assert "line-45" in attempt["first_failure"]
    assert "line-5\n" not in attempt["first_failure"]
    assert len((attempt["first_failure"] or "").splitlines()) == 40
    assert len(next(shots_dir.glob("*/binding-fail-binding.log")).read_text(encoding="utf-8").splitlines()) == 45


def test_binding_timeout_is_error_and_retries_once(tmp_path: Path) -> None:
    result, run, _ = invoke_bindings(
        tmp_path,
        [bound_journey("binding-timeout", "sh -c 'sleep 1'")],
        timeout_ms=20,
    )

    assert result.returncode == 1
    assert [attempt["verdict"] for attempt in run["attempts"]] == ["ERROR", "ERROR"]
    assert [attempt["retry"] for attempt in run["attempts"]] == [0, 1]
    assert all("timed out after 20ms" in attempt["first_failure"] for attempt in run["attempts"])


def test_non_executable_restricted_and_credential_gated_bindings_do_not_run(tmp_path: Path) -> None:
    journeys = [
        bound_journey("placeholder", "node script.js <repository-url>"),
        bound_journey("prose", "credential-free probe against the target"),
        bound_journey("restricted", "node -e \"process.exit(9)\"", environments=["prod"]),
        bound_journey("credential", "node -e \"process.exit(9)\"", credential_gate="TEST_UAT_CREDENTIAL"),
    ]

    result, run, _ = invoke_bindings(tmp_path, journeys)

    assert result.returncode == 0, result.stderr
    attempts = {attempt["journey"]: attempt for attempt in run["attempts"]}
    assert attempts["placeholder"]["verdict"] == "NOT RUN"
    assert attempts["placeholder"]["first_failure"].startswith("binding is not executable:")
    assert attempts["prose"]["verdict"] == "NOT RUN"
    assert attempts["prose"]["first_failure"].startswith("binding is not executable:")
    assert attempts["restricted"]["first_failure"] == "binding restricted to prod"
    assert attempts["credential"]["verdict"] == "BLOCKED"
    assert attempts["credential"]["first_failure"] == "credential TEST_UAT_CREDENTIAL unavailable"


def test_cli_rejects_mixed_modes_and_only_ids_from_the_other_mode(tmp_path: Path) -> None:
    catalog_path = tmp_path / "journeys.json"
    catalog_path.write_text(
        json.dumps(
            {
                "journeys": [
                    {
                        "id": "browser-step",
                        "story": "US-1-01",
                        "persona": "A1",
                        "viewports": ["desktop"],
                        "binding": "steps",
                        "steps": [{"op": "goto", "path": "/"}],
                    },
                    bound_journey("binding-pass", "sh -c 'exit 0'"),
                ]
            }
        ),
        encoding="utf-8",
    )
    common = ["--env-label", "local", "--journeys", str(catalog_path)]

    mixed = subprocess.run(
        ["node", str(RUNNER_PATH), "--bindings", "--base", "http://127.0.0.1:9999", *common],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    wrong_mode = subprocess.run(
        ["node", str(RUNNER_PATH), "--bindings", "--only", "browser-step", *common],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert mixed.returncode == 2
    assert "mutually exclusive" in mixed.stderr
    assert wrong_mode.returncode == 2
    assert "unavailable in bindings mode" in wrong_mode.stderr
