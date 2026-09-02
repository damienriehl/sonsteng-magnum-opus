from __future__ import annotations

import json
import re
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
            assert journey[journey["binding"]]["command"]
            assert journey[journey["binding"]]["story_checks"]
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
