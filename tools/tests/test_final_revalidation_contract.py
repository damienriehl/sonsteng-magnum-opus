from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
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


def run_bash(
    path: Path, program: str, *, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash"],
        cwd=path,
        input=program,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def read_process_identity(pid: int) -> tuple[str, str]:
    stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    stat_fields = stat_line.rsplit(") ", 1)[1].split()
    return stat_fields[0], stat_fields[19]


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
    assert "finalize_revalidation\nexit $?" in source


def test_all_deletions_use_the_shared_pinned_directory_helper() -> None:
    source = script_source()
    rm_commands = [
        line.strip()
        for line in source.splitlines()
        if re.match(r"^\s*rm(?:\s|$)", line)
    ]
    assert rm_commands == ['rm -f -- "$@"', 'rm -rf -- "$@"']
    assert 'remove_in_pinned_dir "$ROOT/site" "$scanner_identity" file' in shell_function(
        source, "remove_stale_server_markers"
    )
    assert 'remove_in_pinned_dir "$ROOT/site" "${SITE_IDENTITY:-}" file' in shell_function(
        source, "cleanup"
    )
    assert 'remove_in_pinned_dir "${BUILD_UAT%/}" "$BUILD_UAT_IDENTITY" tree' in shell_function(
        source, "clear_prior_evidence"
    )


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
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    stale_marker = site / ".final-revalidation-server.ABC123"
    stale_marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n", encoding="utf-8"
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
    assert stale_only.stdout == (
        "removed stale local-server marker: site/.final-revalidation-server.ABC123\n"
    )
    assert stale_only.stderr == ""

    stale_marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n", encoding="utf-8"
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
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
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


def test_dead_legacy_server_marker_is_removed(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:12345\n", encoding="utf-8"
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_live_legacy_server_marker_is_preserved_for_initial_gate(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
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


def test_pid_zero_server_marker_is_malformed_and_preserved(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:0:1:12345\n", encoding="utf-8"
    )
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
    assert "/proc/0/stat" not in result.stderr
    assert "?? site/.final-revalidation-server.ABC123" in result.stderr


def test_live_matching_owner_server_marker_is_preserved_for_initial_gate(
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
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
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
            "read -r _ owner_start_ticks <<< \"$(process_identity \"$$\")\"\n"
            f"printf 'final-revalidation:{'deadbeef' * 5}:%s:%s:12345\\n' "
            '"$$" "$owner_start_ticks" > '
            f"{shlex.quote(str(marker))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "?? site/.final-revalidation-server.ABC123" in result.stderr
    assert "ERROR: initial gate failed" in result.stderr


def test_reused_pid_with_different_start_ticks_is_removed(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
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
            "read -r _ owner_start_ticks <<< \"$(process_identity \"$$\")\"\n"
            "stale_start_ticks=$((owner_start_ticks + 1))\n"
            f"printf 'final-revalidation:{'deadbeef' * 5}:%s:%s:12345\\n' "
            '"$$" "$stale_start_ticks" > '
            f"{shlex.quote(str(marker))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_matching_zombie_owner_server_marker_is_removed(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    marker = site / ".final-revalidation-server.ABC123"

    zombie_pid = os.fork()
    if zombie_pid == 0:
        os._exit(0)
    try:
        os.waitid(os.P_PID, zombie_pid, os.WEXITED | os.WNOWAIT)
        state, start_ticks = read_process_identity(zombie_pid)
        assert state == "Z"

        marker.write_text(
            f"final-revalidation:{'deadbeef' * 5}:{zombie_pid}:{start_ticks}:12345\n",
            encoding="utf-8",
        )
        functions = "\n\n".join(
            [
                shell_function(source, "die"),
                shell_function(source, "process_identity"),
                shell_function(source, "remove_in_pinned_dir"),
                shell_function(source, "remove_stale_server_markers"),
                shell_function(source, "require_clean_worktree"),
            ]
        )
        result = run_bash(
            tmp_path,
            (
                f"set -uo pipefail\n{functions}\n"
                f"ROOT={shlex.quote(str(tmp_path))}\n"
                "remove_stale_server_markers\n"
                "require_clean_worktree 'initial gate failed'\n"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert not marker.exists()
    finally:
        os.waitpid(zombie_pid, 0)


def test_process_stat_parser_handles_comm_with_close_paren_and_spaces(
    tmp_path: Path,
) -> None:
    source = script_source()
    ready_read, ready_write = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(ready_read)
        try:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(15, b"a) b c) d", 0, 0, 0) != 0:
                os._exit(2)
            os.write(ready_write, b"1")
            time.sleep(30)
        finally:
            os._exit(0)

    os.close(ready_write)
    try:
        assert os.read(ready_read, 1) == b"1"
        _, expected_ticks = read_process_identity(child_pid)
        functions = shell_function(source, "process_identity")
        result = run_bash(
            tmp_path,
            (
                f"set -uo pipefail\n{functions}\n"
                f"process_identity {child_pid}\n"
            ),
        )
        assert result.returncode == 0, result.stderr
        output_state, output_ticks = result.stdout.split()
        assert re.fullmatch(r"[A-Za-z]", output_state)
        assert output_ticks == expected_ticks
    finally:
        os.close(ready_read)
        os.kill(child_pid, 15)
        os.waitpid(child_pid, 0)


def test_live_owner_with_unreadable_start_ticks_is_preserved_for_initial_gate(
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
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
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
            f"printf 'final-revalidation:{'deadbeef' * 5}:%s:1:12345\\n' \"$$\" > "
            f"{shlex.quote(str(marker))}\n"
            "process_identity() { return 1; }\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "?? site/.final-revalidation-server.ABC123" in result.stderr
    assert "ERROR: initial gate failed" in result.stderr


def test_marker_entry_symlink_and_external_target_are_preserved(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    external_marker = tmp_path / "external-marker"
    external_marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n", encoding="utf-8"
    )
    marker = site / ".final-revalidation-server.ABC123"
    marker.symlink_to(external_marker)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
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
    assert marker.is_symlink()
    assert external_marker.exists()
    assert "?? site/.final-revalidation-server.ABC123" in result.stderr


def test_stale_marker_cleanup_handles_root_spaces_and_glob_characters(
    tmp_path: Path,
) -> None:
    source = script_source()
    repository = tmp_path / "repo [x]* space"
    site = repository / "site"
    site.mkdir(parents=True)
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(repository)
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n", encoding="utf-8"
    )

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
            shell_function(source, "require_clean_worktree"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "remove_stale_server_markers\n"
            "require_clean_worktree 'initial gate failed'\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


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
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n", encoding="utf-8"
    )
    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "remove_stale_server_markers\n"
            "printf 'SENTINEL: stale cleanup returned\\n'\n"
        ),
    )
    assert result.returncode == 1
    assert "SENTINEL" not in result.stdout
    assert marker.exists()
    assert "site/ must resolve to its repository-local path" in result.stderr


def test_clean_symlinked_site_is_rejected_before_marker_creation(tmp_path: Path) -> None:
    source = script_source()
    site_validation = source.index(
        'SITE_IDENTITY=$(pinned_directory_identity "$ROOT/site")'
    )
    stale_cleanup = source.index("\nremove_stale_server_markers\n", site_validation)
    marker_creation = source.index('MARKER_PATH=$(mktemp "$ROOT/site/', stale_cleanup)
    assert site_validation < stale_cleanup < marker_creation

    repository = tmp_path / "repository"
    external_site = tmp_path / "external-site"
    repository.mkdir()
    external_site.mkdir()
    (repository / "site").symlink_to(external_site, target_is_directory=True)

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "pinned_directory_identity"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\") || "
            "die 'site identity validation failed'\n"
            "touch \"$ROOT/site/.final-revalidation-server.ABC123\"\n"
        ),
    )
    assert result.returncode == 1
    assert not (external_site / ".final-revalidation-server.ABC123").exists()
    assert result.stderr == "ERROR: site identity validation failed\n"


def test_real_site_replacement_before_helper_entry_deletes_neither_marker(
    tmp_path: Path,
) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    repository.mkdir()
    site = repository / "site"
    site.mkdir()
    (site / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(repository)

    marker_name = ".final-revalidation-server.ABC123"
    token = f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n"
    original_marker = site / marker_name
    original_marker.write_text(token, encoding="utf-8")
    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "process_identity() {\n"
            "  mv -- \"$ROOT/site\" \"$ROOT/original-site\" || return 1\n"
            "  mkdir -- \"$ROOT/site\" || return 1\n"
            f"  printf '%s' {shlex.quote(token)} > \"$ROOT/site/{marker_name}\" || return 1\n"
            "  return 1\n"
            "}\n"
            "remove_stale_server_markers\n"
        ),
    )
    assert result.returncode == 1
    assert (repository / "original-site" / marker_name).exists()
    assert (repository / "site" / marker_name).exists()
    assert "could not remove stale local-server marker" in result.stderr


def test_stale_marker_directory_substitution_is_not_recursively_deleted(
    tmp_path: Path,
) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    site = repository / "site"
    site.mkdir(parents=True)
    marker_name = ".final-revalidation-server.ABC123"
    marker = site / marker_name
    marker.write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n",
        encoding="utf-8",
    )

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "process_identity() {\n"
            f"  rm -f -- \"$ROOT/site/{marker_name}\" || return 1\n"
            f"  mkdir -- \"$ROOT/site/{marker_name}\" || return 1\n"
            f"  printf 'keep\\n' > \"$ROOT/site/{marker_name}/evidence.txt\" || return 1\n"
            "  return 1\n"
            "}\n"
            "remove_stale_server_markers\n"
        ),
    )
    assert result.returncode == 1
    assert (marker / "evidence.txt").read_text(encoding="utf-8") == "keep\n"
    assert "could not remove stale local-server marker" in result.stderr


def test_non_directory_site_fails_without_blocking(tmp_path: Path) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    initialize_git_repo(repository)
    os.mkfifo(repository / "site")

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "process_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "remove_stale_server_markers"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "remove_stale_server_markers\n"
            "printf 'SENTINEL: stale cleanup returned\\n'\n"
        ),
        timeout=2,
    )
    assert result.returncode == 1
    assert "SENTINEL" not in result.stdout
    assert "could not enter site/ before stale-marker cleanup" in result.stderr


def test_cleanup_real_site_replacement_cannot_delete_either_active_marker(
    tmp_path: Path,
) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    replacement_site = tmp_path / "replacement-site"
    site = repository / "site"
    site.mkdir(parents=True)
    replacement_site.mkdir()
    marker_name = ".final-revalidation-server.ABC123"
    original_marker = site / marker_name
    replacement_marker = replacement_site / marker_name
    original_marker.write_text("original\n", encoding="utf-8")
    replacement_marker.write_text("replacement\n", encoding="utf-8")

    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            "restore_build_stamp() { return 0; }\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            f"REPLACEMENT_SITE={shlex.quote(str(replacement_site))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            f"MARKER_NAME={marker_name}\n"
            "MARKER_PATH=\"$ROOT/site/$MARKER_NAME\"\n"
            "SERVER_PID=''\n"
            "mv -- \"$ROOT/site\" \"$ROOT/original-site\"\n"
            "mv -- \"$REPLACEMENT_SITE\" \"$ROOT/site\"\n"
            "cleanup 0\n"
            "status=$?\n"
            "printf 'cleanup_status=%s\\n' \"$status\"\n"
            "exit \"$status\"\n"
        ),
    )
    assert result.returncode == 1
    assert result.stdout == "cleanup_status=1\n"
    assert (repository / "original-site" / marker_name).exists()
    assert (repository / "site" / marker_name).exists()
    assert "could not remove active local-server marker" in result.stderr


def test_interrupted_marker_name_initialization_is_cleaned_from_path(
    tmp_path: Path,
) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text("marker\n", encoding="utf-8")
    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "restore_build_stamp() { return 0; }\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            "SERVER_PID=''\n"
            "MARKER_NAME=''\n"
            "MARKER_PATH=\"$ROOT/site/.final-revalidation-server.ABC123\"\n"
            "cleanup 0\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_active_marker_directory_substitution_is_not_recursively_deleted(
    tmp_path: Path,
) -> None:
    source = script_source()
    site = tmp_path / "site"
    marker = site / ".final-revalidation-server.ABC123"
    marker.mkdir(parents=True)
    (marker / "evidence.txt").write_text("keep\n", encoding="utf-8")
    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "restore_build_stamp() { return 0; }\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            "SERVER_PID=''\n"
            "MARKER_NAME=.final-revalidation-server.ABC123\n"
            "MARKER_PATH=\"$ROOT/site/$MARKER_NAME\"\n"
            "cleanup 0\n"
        ),
    )
    assert result.returncode == 1
    assert (marker / "evidence.txt").read_text(encoding="utf-8") == "keep\n"
    assert "could not remove active local-server marker" in result.stderr


def test_build_uat_swap_after_validation_cannot_clear_replacement(
    tmp_path: Path,
) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    build_uat = repository / "build" / "uat"
    external_uat = tmp_path / "external-uat"
    for path in (build_uat / "runs", build_uat / "shots", external_uat / "runs", external_uat / "shots"):
        path.mkdir(parents=True)
        (path / "evidence.txt").write_text("fixture\n", encoding="utf-8")

    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "clear_prior_evidence"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            "BUILD_UAT=\"$ROOT/build/uat/\"\n"
            f"EXTERNAL_UAT={shlex.quote(str(external_uat))}\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"${BUILD_UAT%/}\")\n"
            "mv -- \"$ROOT/build/uat\" \"$ROOT/build/original-uat\"\n"
            "ln -s -- \"$EXTERNAL_UAT\" \"$ROOT/build/uat\"\n"
            "clear_prior_evidence\n"
        ),
    )
    assert result.returncode == 1
    assert (repository / "build" / "original-uat" / "runs" / "evidence.txt").exists()
    assert (repository / "build" / "original-uat" / "shots" / "evidence.txt").exists()
    assert (external_uat / "runs" / "evidence.txt").exists()
    assert (external_uat / "shots" / "evidence.txt").exists()
    assert result.stderr == "ERROR: could not clear prior UAT evidence\n"


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
    final_cleanup = source.rindex("\nfinalize_revalidation\n")

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
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
        ]
    )
    cleanup_result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SERVER_PID=''\nMARKER_NAME=''\n"
            "test \"$(cat site/platform/data/.build-stamp.json)\" = generated\n"
            "cleanup\n"
            "test \"$(cat site/platform/data/.build-stamp.json)\" = committed\n"
        ),
    )
    assert cleanup_result.returncode == 0, cleanup_result.stderr


def test_failed_build_stamp_restoration_fails_normal_final_adapter(
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
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
            shell_function(source, "record_status"),
            shell_function(source, "finalize_revalidation"),
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
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            "SERVER_PID=$server_pid\n"
            "MARKER_NAME=.final-revalidation-server.ABC123\n"
            "MARKER_PATH=\"$ROOT/site/$MARKER_NAME\"\n"
            "REVALIDATION_STATUS=0\n"
            "SHA=fixture\n"
            "finalize_revalidation\n"
            "exit $?\n"
        ),
    )
    assert result.returncode == 1
    assert not marker.exists()
    assert "generated" == stamp.read_text(encoding="utf-8").strip()
    assert "could not restore generated build stamp" in result.stderr
    assert "site/platform/data/.build-stamp.json" in result.stderr
    assert " M site/platform/data/.build-stamp.json" in result.stderr


def test_failed_build_stamp_restoration_fails_exit_trap(tmp_path: Path) -> None:
    source = script_source()
    stamp = tmp_path / "site" / "platform" / "data" / ".build-stamp.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("committed\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    stamp.write_text("generated\n", encoding="utf-8")

    functions = "\n\n".join(
        [
            shell_function(source, "restore_build_stamp"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
            shell_function(source, "cleanup_on_exit"),
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
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SERVER_PID=''\n"
            "MARKER_NAME=''\n"
            "trap cleanup_on_exit EXIT\n"
            "exit 0\n"
        ),
    )
    assert result.returncode == 1
    assert "generated" == stamp.read_text(encoding="utf-8").strip()
    assert "could not restore generated build stamp" in result.stderr


def test_active_marker_unlink_failure_fails_normal_final_adapter(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text("marker\n", encoding="utf-8")
    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
            shell_function(source, "record_status"),
            shell_function(source, "finalize_revalidation"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "restore_build_stamp() { return 0; }\n"
            "rm() { return 1; }\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            "SERVER_PID=''\n"
            "MARKER_NAME=.final-revalidation-server.ABC123\n"
            "MARKER_PATH=\"$ROOT/site/$MARKER_NAME\"\n"
            "REVALIDATION_STATUS=0\n"
            "SHA=fixture\n"
            "finalize_revalidation\n"
            "exit $?\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "could not remove active local-server marker" in result.stderr


def test_active_marker_unlink_failure_fails_exit_trap(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text("marker\n", encoding="utf-8")
    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
            shell_function(source, "cleanup_on_exit"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "restore_build_stamp() { return 0; }\n"
            "rm() { return 1; }\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            "SERVER_PID=''\n"
            "MARKER_NAME=.final-revalidation-server.ABC123\n"
            "MARKER_PATH=\"$ROOT/site/$MARKER_NAME\"\n"
            "trap cleanup_on_exit EXIT\n"
            "exit 0\n"
        ),
    )
    assert result.returncode == 1
    assert marker.exists()
    assert "could not remove active local-server marker" in result.stderr


def test_evidence_clear_failure_aborts(tmp_path: Path) -> None:
    source = script_source()
    (tmp_path / "build" / "uat").mkdir(parents=True)
    functions = "\n\n".join(
        [
            shell_function(source, "die"),
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "clear_prior_evidence"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "BUILD_UAT=\"$ROOT/build/uat/\"\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"${BUILD_UAT%/}\")\n"
            "rm() { return 1; }\n"
            "clear_prior_evidence\n"
            "printf 'SENTINEL: evidence cleanup returned\\n'\n"
        ),
    )
    assert result.returncode == 1
    assert "SENTINEL" not in result.stdout
    assert result.stderr == "ERROR: could not clear prior UAT evidence\n"
