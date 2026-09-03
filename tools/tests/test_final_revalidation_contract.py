from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "tools" / "final_revalidation.sh"
CATALOG_PATH = ROOT / "tools" / "persona_journeys.json"
EXPECTED_LEGS = [
    ("browser-local", "local", "browser"),
    ("browser-dev", "dev", "browser"),
    ("browser-prod", "prod", "browser"),
    ("bindings-local", "local", "bindings"),
    ("bindings-dev", "dev", "bindings"),
    ("bindings-prod", "prod", "bindings"),
]
EXPECTED_ONLY_IDS = {
    "bindings-dev": [
        "hostile-bot-gate",
        "student-live-provider-dev",
        "hostile-live-redteam-dev",
    ],
    "bindings-prod": ["hostile-bot-gate"],
}
EXPECTED_A11Y_PATHS = {
    "/",
    "/platform/",
    "/platform/matters/",
    "/platform/matters/m05-dwi-meridian/",
    "/platform/hours/",
    "/cost-per-credit.html",
}


def script_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def journey_ids() -> set[str]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 1
    return {journey["id"] for journey in catalog["journeys"]}


def run_legs(source: str) -> list[tuple[str, list[str]]]:
    legs: list[tuple[str, list[str]]] = []
    for match in re.finditer(r"^run\s+(\S+)\s+(.+)$", source, re.MULTILINE):
        legs.append((match.group(1), shlex.split(match.group(2), comments=True)))
    return legs


def test_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_journey_legs_use_known_ids_and_their_required_modes() -> None:
    legs = run_legs(script_source())
    assert [name for name, _ in legs] == [name for name, _, _ in EXPECTED_LEGS]

    known_ids = journey_ids()
    for (name, arguments), (_, expected_label, expected_mode) in zip(legs, EXPECTED_LEGS):
        label_index = arguments.index("--env-label")
        assert arguments[label_index + 1] == expected_label

        if expected_mode == "bindings":
            assert "--bindings" in arguments, name
            assert "--base" not in arguments, name
        else:
            assert "--base" in arguments, name
            assert "--bindings" not in arguments, name

        if "--only" in arguments:
            only_index = arguments.index("--only")
            requested_ids = arguments[only_index + 1].split(",")
            assert requested_ids
            assert set(requested_ids) <= known_ids, set(requested_ids) - known_ids
            assert requested_ids == EXPECTED_ONLY_IDS[name]
        else:
            assert name not in EXPECTED_ONLY_IDS

    assert {label for _, label, _ in EXPECTED_LEGS} == {"local", "dev", "prod"}


def test_a11y_uses_the_same_expected_paths_for_dev_and_production() -> None:
    urls = re.findall(r'"\$\{(DEV_BASE|PROD_BASE)\}(/[^"\n]*)"', script_source())
    paths_by_environment = {
        variable: {path for found_variable, path in urls if found_variable == variable}
        for variable in ("DEV_BASE", "PROD_BASE")
    }
    assert paths_by_environment["DEV_BASE"] == EXPECTED_A11Y_PATHS
    assert paths_by_environment["PROD_BASE"] == EXPECTED_A11Y_PATHS
    assert len(urls) == 2 * len(EXPECTED_A11Y_PATHS)


def test_script_contains_no_credentials_or_non_uat_build_paths() -> None:
    source = script_source()
    assert not re.search(r"api_key|AIza|sk-|Bearer", source, re.IGNORECASE)

    build_references = list(re.finditer(r"build/", source))
    assert build_references
    for reference in build_references:
        assert source.startswith("build/uat/", reference.start())


def test_every_leg_contributes_to_the_final_exit_status() -> None:
    source = script_source()
    assert 'record_status "$status"' in source
    assert 'record_status "$a11y_status"' in source
    assert 'exit "$REVALIDATION_STATUS"' in source
