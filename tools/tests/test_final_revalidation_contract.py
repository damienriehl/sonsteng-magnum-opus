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


def shell_function(source: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", source, re.MULTILINE | re.DOTALL)
    assert match is not None, f"missing shell function: {name}"
    return match.group(0)


def initialize_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "contract@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


def run_bash(path: Path, program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash"],
        cwd=path,
        input=program,
        capture_output=True,
        text=True,
        check=False,
    )


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


def test_initial_gate_rejects_non_ignored_untracked_generator_input(tmp_path: Path) -> None:
    source = script_source()
    (tmp_path / ".gitignore").write_text("ignored-input.json\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    (tmp_path / "ignored-input.json").write_text("ignored\n", encoding="utf-8")

    functions = "\n\n".join(
        [shell_function(source, "die"), shell_function(source, "require_clean_worktree")]
    )
    ignored_only = run_bash(
        tmp_path,
        f"set -uo pipefail\n{functions}\nrequire_clean_worktree 'initial gate failed'\n",
    )
    assert ignored_only.returncode == 0, ignored_only.stderr

    (tmp_path / "untracked-generator-input.json").write_text("untracked\n", encoding="utf-8")
    untracked_input = run_bash(
        tmp_path,
        f"set -uo pipefail\n{functions}\nrequire_clean_worktree 'initial gate failed'\n",
    )
    assert untracked_input.returncode == 1
    assert "?? untracked-generator-input.json" in untracked_input.stderr
    assert "ignored-input.json" not in untracked_input.stderr
    assert "ERROR: initial gate failed" in untracked_input.stderr


def test_stale_server_marker_is_removed_without_weakening_initial_gate(
    tmp_path: Path,
) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    stale_marker = site / ".final-revalidation-server.ABC123"
    stale_marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:12345\n", encoding="utf-8"
    )
    stale_only = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert stale_only.returncode == 0, stale_only.stderr
    assert not stale_marker.exists()

    stale_marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:12345\n", encoding="utf-8"
    )
    unrelated = site / "unexpected-source.json"
    unrelated.write_text("unexpected\n", encoding="utf-8")
    unrelated_change = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert unrelated_change.returncode == 1
    assert not stale_marker.exists()
    assert "?? site/unexpected-source.json" in unrelated_change.stderr
    assert ".final-revalidation-server" not in unrelated_change.stderr
    assert "ERROR: initial gate failed" in unrelated_change.stderr


def test_malformed_server_marker_is_preserved_for_initial_gate(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text("not-a-final-revalidation-token\n", encoding="utf-8")
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "?? site/.final-revalidation-server.ABC123" in result.stderr
    assert "ERROR: initial gate failed" in result.stderr


def test_live_owner_server_marker_is_preserved_for_initial_gate(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    marker = site / ".final-revalidation-server.ABC123"
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            f"printf 'final-revalidation:{'deadbeef' * 5}:%s:12345\\n' \"$$\" > "
            f"{shlex.quote(str(marker))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "?? site/.final-revalidation-server.ABC123" in result.stderr
    assert "ERROR: initial gate failed" in result.stderr


def test_symlinked_site_cannot_delete_external_server_marker(tmp_path: Path) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    external_site = tmp_path / "external-site"
    repository.mkdir()
    external_site.mkdir()
    (repository / "site").symlink_to(external_site, target_is_directory=True)
    (repository / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(repository)

    marker = external_site / ".final-revalidation-server.ABC123"
    marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:12345\n", encoding="utf-8"
    )
    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "remove_stale_server_markers"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "remove_stale_server_markers\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "site/ must resolve to its repository-local path" in result.stderr


def test_post_generator_gate_excludes_only_marker_and_build_stamp(tmp_path: Path) -> None:
    source = script_source()
    stamp = tmp_path / "site" / "platform" / "data" / ".build-stamp.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("committed\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    stamp.write_text("generated\n", encoding="utf-8")
    marker = tmp_path / "site" / ".final-revalidation-server.fixture"
    marker.write_text("marker\n", encoding="utf-8")

    functions = "\n\n".join(
        [shell_function(source, "die"), shell_function(source, "require_clean_worktree")]
    )
    post_generator_gate = (
        "require_clean_worktree 'post-generator gate failed' "
        "':(top,exclude,literal)site/.final-revalidation-server.fixture' "
        "':(top,exclude,literal)site/platform/data/.build-stamp.json'"
    )
    expected_changes = run_bash(
        tmp_path,
        f"set -uo pipefail\n{functions}\n{post_generator_gate}\n",
    )
    assert expected_changes.returncode == 0, expected_changes.stderr

    (tmp_path / "site" / "unexpected-source.json").write_text("unexpected\n", encoding="utf-8")
    unexpected_change = run_bash(
        tmp_path,
        f"set -uo pipefail\n{functions}\n{post_generator_gate}\n",
    )
    assert unexpected_change.returncode == 1
    assert "?? site/unexpected-source.json" in unexpected_change.stderr

    assert (
        'require_clean_worktree "generators changed tracked or untracked files; '
        'revalidation would no longer describe one SHA"'
    ) in source
    assert '":(top,exclude,literal)site/$MARKER_NAME"' in source
    assert '":(top,exclude,literal)site/platform/data/.build-stamp.json"' in source


def test_generated_build_stamp_remains_installed_until_cleanup(tmp_path: Path) -> None:
    source = script_source()
    after_generators = source.index('run_generator "editor data bundle"')
    browser_local = source.index("run browser-local")
    bindings_local = source.index("run bindings-local")
    bindings_dev = source.index("run bindings-dev")
    final_cleanup = source.rindex('\ncleanup "$REVALIDATION_STATUS"\n')

    assert "restore_build_stamp" not in source[after_generators:bindings_dev]
    assert after_generators < browser_local < bindings_local < final_cleanup
    assert "restore_build_stamp" in shell_function(source, "cleanup")

    stamp = tmp_path / "site" / "platform" / "data" / ".build-stamp.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("committed\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    stamp.write_text("generated\n", encoding="utf-8")
    functions = "\n\n".join(
        [
            shell_function(source, "restore_build_stamp"),
            shell_function(source, "cleanup"),
        ]
    )
    cleanup_result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "SERVER_PID=''\nMARKER_PATH=''\n"
            "test \"$(cat site/platform/data/.build-stamp.json)\" = generated\n"
            "cleanup\n"
            "test \"$(cat site/platform/data/.build-stamp.json)\" = committed\n"
        ),
    )
    assert cleanup_result.returncode == 0, cleanup_result.stderr


def test_failed_build_stamp_restoration_is_reported_and_fails_cleanup(
    tmp_path: Path,
) -> None:
    source = script_source()
    stamp = tmp_path / "site" / "platform" / "data" / ".build-stamp.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("committed\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    stamp.write_text("generated\n", encoding="utf-8")
    marker = tmp_path / "site" / ".final-revalidation-server.ABC123"
    marker.write_text("marker\n", encoding="utf-8")

    functions = "\n\n".join(
        [
            shell_function(source, "restore_build_stamp"),
            shell_function(source, "cleanup"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "git() {\n"
            "  if [ \"$1\" = checkout ]; then return 1; fi\n"
            "  command git \"$@\"\n"
            "}\n"
            "sleep 30 &\n"
            "server_pid=$!\n"
            "SERVER_PID=$server_pid\n"
            f"MARKER_PATH={shlex.quote(str(marker))}\n"
            "REVALIDATION_STATUS=0\n"
            "cleanup\n"
            "cleanup_status=$?\n"
            "test \"$cleanup_status\" -eq 1 || exit 80\n"
            "if [ \"$cleanup_status\" -ne 0 ]; then REVALIDATION_STATUS=1; fi\n"
            "test \"$REVALIDATION_STATUS\" -eq 1 || exit 81\n"
            "test ! -e \"$MARKER_PATH\" || exit 82\n"
            "if kill -0 \"$server_pid\" 2>/dev/null; then exit 83; fi\n"
            "test \"$(cat site/platform/data/.build-stamp.json)\" = generated || exit 84\n"
            "exit \"$REVALIDATION_STATUS\"\n"
        ),
    )
    assert result.returncode == 1
    assert "could not restore generated build stamp" in result.stderr
    assert "site/platform/data/.build-stamp.json" in result.stderr
    assert " M site/platform/data/.build-stamp.json" in result.stderr


def test_evidence_clear_failure_aborts(tmp_path: Path) -> None:
    source = script_source()
    functions = "\n\n".join(
        [shell_function(source, "die"), shell_function(source, "clear_prior_evidence")]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"BUILD_UAT={shlex.quote(str(tmp_path))}/\n"
            "rm() { return 1; }\n"
            "clear_prior_evidence\n"
        ),
    )
    assert result.returncode == 1
    assert result.stderr == "ERROR: could not clear prior UAT evidence\n"
