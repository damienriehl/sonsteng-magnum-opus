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


def shell_tokens(source: str) -> list[str]:
    lexer = shlex.shlex(source, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def without_shell_functions(source: str, *names: str) -> str:
    for name in names:
        source = source.replace(shell_function(source, name), "")
    return source


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
        assert source.startswith("build/uat/", reference.start()) or source.startswith(
            'build/uat"', reference.start()
        )


def test_every_leg_contributes_to_the_final_exit_status() -> None:
    source = script_source()
    assert 'record_status "$status"' in source
    assert 'record_status "$a11y_status"' in source
    assert "finalize_revalidation\nexit $?" in source


def test_all_filesystem_writes_use_pinned_directory_helpers() -> None:
    source = script_source()
    inspected = without_shell_functions(source, "remove_in_pinned_dir", "with_pinned_dir")
    assert "rm" not in shell_tokens(inspected)

    mktemp_lines = [
        line
        for line in inspected.splitlines()
        if re.search(r"(?:^|[\s;|(&])mktemp(?:\s|$)", line)
    ]
    assert mktemp_lines
    assert all(re.search(r"\bwith_pinned_dir\b.*\bmktemp\b", line) for line in mktemp_lines)
    direct_mktemp_mutant = 'marker=$(mktemp "$ROOT/site/marker.XXXXXX")'
    assert not re.search(r"\bwith_pinned_dir\b.*\bmktemp\b", direct_mktemp_mutant)
    pinned_path_redirect = r">{1,2}\s*[\"']?\$\{?(?:ROOT|BUILD_UAT)\}?(?:[/\"]|$)"
    absolute_directory = r"--directory\s+[\"']?\$\{?ROOT\}?"
    assert not re.search(pinned_path_redirect, inspected)
    assert not re.search(absolute_directory, inspected)
    assert re.search(pinned_path_redirect, 'printf data > "$ROOT/site/marker"')
    assert re.search(pinned_path_redirect, 'printf data >> "${BUILD_UAT}/final-a11y.log"')
    assert re.search(absolute_directory, 'python3 -m http.server --directory "$ROOT/site"')

    fixed_log_mutants = [
        'python3 -m http.server > "$BUILD_UAT/local-server.log"',
        'node tools/verify_persona_journeys.js >> "${BUILD_UAT}/final-browser-local.log"',
        'node tools/a11y_audit.js > "$ROOT/build/uat/final-a11y.log"',
    ]
    for mutant in fixed_log_mutants:
        assert re.search(pinned_path_redirect, mutant)

    rm_mutants = [
        'if rm -rf -- "$ROOT/build/uat/runs"; then :; fi',
        'value=$(rm -rf -- "$ROOT/build/uat/runs")',
        '(rm -rf -- "$ROOT/build/uat/runs")',
        'rm>"$ROOT/build/uat/deletion.log"',
    ]
    for mutant in rm_mutants:
        assert "rm" in shell_tokens(inspected + "\n" + mutant + "\n")

    assert 'remove_in_pinned_dir "$ROOT/site" "$scanner_identity" file' in shell_function(
        source, "remove_stale_server_markers"
    )
    assert 'remove_in_pinned_dir "$ROOT/site" "${SITE_IDENTITY:-}" file' in shell_function(
        source, "cleanup"
    )
    assert 'remove_in_pinned_dir "$BUILD_UAT" "$BUILD_UAT_IDENTITY" tree' in shell_function(
        source, "clear_prior_evidence"
    )


def test_pinned_helpers_reject_unsafe_names_directory_file_mode_and_empty_identity(
    tmp_path: Path,
) -> None:
    source = script_source()
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    (pinned / "directory-entry").mkdir()
    identity = f"{pinned.stat().st_dev}:{pinned.stat().st_ino}"
    functions = "\n\n".join(
        [
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "remove_in_pinned_dir"),
        ]
    )
    rejected_names = ["", ".", "..", "/tmp/outside", "nested/name"]
    cases = "\n".join(
        f"remove_in_pinned_dir \"$PINNED\" \"$IDENTITY\" file {shlex.quote(name)} && exit 21"
        for name in rejected_names
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"PINNED={shlex.quote(str(pinned))}\n"
            f"IDENTITY={shlex.quote(identity)}\n"
            "rm() { printf 'RM_INVOKED\\n'; return 0; }\n"
            f"{cases}\n"
            "remove_in_pinned_dir \"$PINNED\" \"$IDENTITY\" file directory-entry && exit 22\n"
            "remove_in_pinned_dir \"$PINNED\" '' tree safe-entry && exit 23\n"
            "with_pinned_dir \"$PINNED\" '' true && exit 24\n"
            "with_pinned_dir \"$PINNED\" \"$IDENTITY\" /bin/true && exit 25\n"
            "with_pinned_dir \"$PINNED\" \"$IDENTITY\" nested/command && exit 26\n"
            "printf 'all-rejected\\n'\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "all-rejected\n"

    inherited_exit_trap = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"PINNED={shlex.quote(str(pinned))}\n"
            f"IDENTITY={shlex.quote(identity)}\n"
            "trap 'true' EXIT\n"
            "with_pinned_dir \"$PINNED\" \"$IDENTITY\" false && exit 31\n"
            "remove_in_pinned_dir \"$PINNED\" '' tree safe-entry && exit 32\n"
            "trap - EXIT\n"
            "exit 0\n"
        ),
    )
    assert inherited_exit_trap.returncode == 0, inherited_exit_trap.stderr

    helper = shell_function(source, "remove_in_pinned_dir")
    assert '""|.|..|/*|*/*) return 1' in helper
    assert '[ ! -d "$relative_name" ] || return 1' in helper
    assert '""|.|..|/*|*/*) return 1' in shell_function(source, "with_pinned_dir")


def test_with_pinned_dir_restores_caller_by_open_directory_identity(tmp_path: Path) -> None:
    source = script_source()
    caller = tmp_path / "caller"
    pinned = tmp_path / "pinned"
    caller.mkdir()
    pinned.mkdir()
    caller_identity = f"{caller.stat().st_dev}:{caller.stat().st_ino}"
    pinned_identity = f"{pinned.stat().st_dev}:{pinned.stat().st_ino}"
    result = run_bash(
        caller,
        (
            f"set -uo pipefail\n{shell_function(source, 'with_pinned_dir')}\n"
            f"CALLER={shlex.quote(str(caller))}\n"
            f"PINNED={shlex.quote(str(pinned))}\n"
            f"CALLER_IDENTITY={shlex.quote(caller_identity)}\n"
            f"PINNED_IDENTITY={shlex.quote(pinned_identity)}\n"
            "swap_caller() {\n"
            "  mv -- \"$CALLER\" \"${CALLER}-original\" || return 1\n"
            "  mkdir -- \"$CALLER\" || return 1\n"
            "}\n"
            "with_pinned_dir \"$PINNED\" \"$PINNED_IDENTITY\" swap_caller\n"
            "test \"$(stat -Lc '%d:%i' -- .)\" = \"$CALLER_IDENTITY\"\n"
            "test \"$(pwd -P)\" = \"${CALLER}-original\"\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert (caller.stat().st_dev, caller.stat().st_ino) != (
        int(caller_identity.split(":")[0]),
        int(caller_identity.split(":")[1]),
    )


def test_build_uat_identity_is_captured_before_lock_and_evidence_clear() -> None:
    source = script_source()
    mkdir_position = source.index('with_pinned_dir "$ROOT/build" "$BUILD_IDENTITY" mkdir -p -- uat')
    capture = 'BUILD_UAT_IDENTITY=$(pinned_directory_identity "$BUILD_UAT")'
    capture_position = source.index(capture)
    lock_position = source.index("\nacquire_revalidation_lock\n", capture_position)
    clear_position = source.index("\nclear_prior_evidence\n", lock_position)
    assert mkdir_position < capture_position < lock_position < clear_position
    main_calls = [match.start() for match in re.finditer(r"^clear_prior_evidence$", source, re.MULTILINE)]
    assert main_calls == [clear_position + 1]
    early_clear_mutant = source[:lock_position] + "\nclear_prior_evidence\n" + source[lock_position:]
    assert len(re.findall(r"^clear_prior_evidence$", early_clear_mutant, re.MULTILINE)) == 2


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
    marker_creation = source.index(
        'MARKER_NAME=$(with_pinned_dir "$ROOT/site" "$SITE_IDENTITY" mktemp ',
        stale_cleanup,
    )
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


def test_site_parent_swap_blocks_marker_create_token_write_and_server_start(
    tmp_path: Path,
) -> None:
    source = script_source()
    repository = tmp_path / "repository"
    site = repository / "site"
    replacement = tmp_path / "replacement-site"
    site.mkdir(parents=True)
    replacement.mkdir()
    marker_name = ".final-revalidation-server.ABC123"
    (site / marker_name).write_text("original\n", encoding="utf-8")
    (replacement / marker_name).write_text("replacement\n", encoding="utf-8")

    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "write_server_marker"),
        ]
    )
    result = run_bash(
        repository,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(repository))}\n"
            f"REPLACEMENT={shlex.quote(str(replacement))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            f"MARKER_FILE_IDENTITY=$(stat -Lc '%d:%i' -- \"$ROOT/site/{marker_name}\")\n"
            "mv -- \"$ROOT/site\" \"$ROOT/original-site\"\n"
            "ln -s -- \"$REPLACEMENT\" \"$ROOT/site\"\n"
            "if with_pinned_dir \"$ROOT/site\" \"$SITE_IDENTITY\" "
            "mktemp .final-revalidation-server.XXXXXX; then exit 21; fi\n"
            f"if with_pinned_dir \"$ROOT/site\" \"$SITE_IDENTITY\" write_server_marker "
            f"{marker_name} \"$MARKER_FILE_IDENTITY\" changed; then exit 22; fi\n"
            "python3() { touch \"$REPLACEMENT/server-started\"; }\n"
            "if with_pinned_dir \"$ROOT/site\" \"$SITE_IDENTITY\" python3 -m http.server "
            "8791 --bind 127.0.0.1 --directory .; then exit 23; fi\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert (repository / "original-site" / marker_name).read_text(encoding="utf-8") == "original\n"
    assert (replacement / marker_name).read_text(encoding="utf-8") == "replacement\n"
    assert not (replacement / "server-started").exists()
    assert 'with_pinned_dir "$ROOT/site" "$SITE_IDENTITY" mktemp ' in source
    assert 'with_pinned_dir "$ROOT/site" "$SITE_IDENTITY" write_server_marker ' in source
    assert '--directory .' in source
    assert '--directory "$ROOT' not in source


def test_marker_token_write_uses_validated_open_descriptor(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    site.mkdir()
    marker = site / ".final-revalidation-server.ABC123"
    marker.write_text("original\n", encoding="utf-8")
    expected_identity = f"{marker.stat().st_dev}:{marker.stat().st_ino}"
    external = tmp_path / "external-marker"
    external.write_text("KEEP\n", encoding="utf-8")
    marker.unlink()
    marker.symlink_to(external)

    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "write_server_marker"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"SITE={shlex.quote(str(site))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$SITE\")\n"
            f"if with_pinned_dir \"$SITE\" \"$SITE_IDENTITY\" write_server_marker "
            f"{marker.name} {shlex.quote(expected_identity)} changed; then exit 31; fi\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert marker.is_symlink()
    assert external.read_text(encoding="utf-8") == "KEEP\n"
    marker_writer = shell_function(source, "write_server_marker")
    assert 'stat -Lc \'%d:%i\' -- "/proc/self/fd/$marker_fd"' in marker_writer
    assert 'printf \'%s\\n\' "$token" >&"$marker_fd"' in marker_writer


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
            "BUILD_UAT=\"$ROOT/build/uat\"\n"
            f"EXTERNAL_UAT={shlex.quote(str(external_uat))}\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
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
            shell_function(source, "restore_build_stamp_in_pinned_dir"),
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "open_new_relative_file_fd"),
            shell_function(source, "remove_in_pinned_dir"),
            shell_function(source, "cleanup"),
        ]
    )
    cleanup_result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "BUILD_STAMP_DIR=\"$ROOT/site/platform/data\"\n"
            "BUILD_STAMP_DIR_IDENTITY=$(pinned_directory_identity \"$BUILD_STAMP_DIR\")\n"
            "BUILD_STAMP_DIRTY=1\n"
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
            shell_function(source, "restore_build_stamp_in_pinned_dir"),
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "open_new_relative_file_fd"),
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
            "  if [ \"$1\" = show ]; then return 1; fi\n"
            "  command git \"$@\"\n"
            "}\n"
            "sleep 30 &\n"
            "server_pid=$!\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "BUILD_STAMP_DIR=\"$ROOT/site/platform/data\"\n"
            "BUILD_STAMP_DIR_IDENTITY=$(pinned_directory_identity \"$BUILD_STAMP_DIR\")\n"
            "BUILD_STAMP_DIRTY=1\n"
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
    assert not list(stamp.parent.glob(".final-revalidation-build-stamp.*"))


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
            shell_function(source, "restore_build_stamp_in_pinned_dir"),
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "open_new_relative_file_fd"),
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
            "  if [ \"$1\" = show ]; then return 1; fi\n"
            "  command git \"$@\"\n"
            "}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "BUILD_STAMP_DIR=\"$ROOT/site/platform/data\"\n"
            "BUILD_STAMP_DIR_IDENTITY=$(pinned_directory_identity \"$BUILD_STAMP_DIR\")\n"
            "BUILD_STAMP_DIRTY=1\n"
            "SERVER_PID=''\n"
            "MARKER_NAME=''\n"
            "trap cleanup_on_exit EXIT\n"
            "exit 0\n"
        ),
    )
    assert result.returncode == 1
    assert "generated" == stamp.read_text(encoding="utf-8").strip()
    assert "could not restore generated build stamp" in result.stderr
    assert not list(stamp.parent.glob(".final-revalidation-build-stamp.*"))


def test_build_stamp_restore_rejects_parent_swap(tmp_path: Path) -> None:
    source = script_source()
    data_dir = tmp_path / "site" / "platform" / "data"
    replacement = tmp_path / "replacement-data"
    data_dir.mkdir(parents=True)
    replacement.mkdir()
    stamp = data_dir / ".build-stamp.json"
    replacement_stamp = replacement / ".build-stamp.json"
    stamp.write_text("committed\n", encoding="utf-8")
    initialize_git_repo(tmp_path)
    stamp.write_text("original-generated\n", encoding="utf-8")
    replacement_stamp.write_text("replacement-generated\n", encoding="utf-8")
    functions = "\n\n".join(
        shell_function(source, name)
        for name in (
            "restore_build_stamp",
            "restore_build_stamp_in_pinned_dir",
            "pinned_directory_identity",
            "with_pinned_dir",
            "open_new_relative_file_fd",
        )
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "BUILD_STAMP_DIR=\"$ROOT/site/platform/data\"\n"
            "BUILD_STAMP_DIR_IDENTITY=$(pinned_directory_identity \"$BUILD_STAMP_DIR\")\n"
            "BUILD_STAMP_DIRTY=1\n"
            "mv -- \"$BUILD_STAMP_DIR\" \"$ROOT/site/platform/original-data\"\n"
            f"ln -s -- {shlex.quote(str(replacement))} \"$BUILD_STAMP_DIR\"\n"
            "restore_build_stamp && exit 31\n"
            "exit 0\n"
        ),
    )
    assert result.returncode == 0
    assert (tmp_path / "site/platform/original-data/.build-stamp.json").read_text(
        encoding="utf-8"
    ) == "original-generated\n"
    assert replacement_stamp.read_text(encoding="utf-8") == "replacement-generated\n"


def test_rejected_contender_cleanup_does_not_restore_build_stamp(tmp_path: Path) -> None:
    source = script_source()
    called = tmp_path / "git-called"
    functions = "\n\n".join(
        [shell_function(source, "restore_build_stamp"), shell_function(source, "cleanup")]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"CALLED={shlex.quote(str(called))}\n"
            "git() { touch \"$CALLED\"; return 1; }\n"
            "SERVER_PID=''\nMARKER_NAME=''\nMARKER_PATH=''\n"
            "BUILD_STAMP_DIRTY=0\nLOCK_HELD=0\n"
            "cleanup 0\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert not called.exists()
    capture = source.index("BUILD_STAMP_DIRTY=1")
    lock = source.index("\nacquire_revalidation_lock\n")
    assert lock < capture


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
            "BUILD_UAT=\"$ROOT/build/uat\"\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            "rm() { return 1; }\n"
            "clear_prior_evidence\n"
            "printf 'SENTINEL: evidence cleanup returned\\n'\n"
        ),
    )
    assert result.returncode == 1
    assert "SENTINEL" not in result.stdout
    assert result.stderr == "ERROR: could not clear prior UAT evidence\n"


def exercise_log_alias_preservation(tmp_path: Path, alias_kind: str) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    build_uat.mkdir(parents=True)
    fixed_names = ["local-server.log", "final-browser-local.log", "final-a11y.log"]
    targets: list[Path] = []
    aliases: list[Path] = []
    for index, fixed_name in enumerate(fixed_names):
        target = tmp_path / f"external-{index}.log"
        target.write_text(f"KEEP-{index}\n", encoding="utf-8")
        alias = build_uat / fixed_name
        if alias_kind == "symlink":
            alias.symlink_to(target)
        else:
            os.link(target, alias)
        targets.append(target)
        aliases.append(alias)

    functions = "\n\n".join(
        [
            shell_function(source, "pinned_directory_identity"),
            shell_function(source, "with_pinned_dir"),
            shell_function(source, "open_new_relative_file_fd"),
            shell_function(source, "enter_pinned_dir"),
            shell_function(source, "close_inherited_pinning_fds"),
            shell_function(source, "run_with_new_log"),
        ]
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"BUILD_UAT={shlex.quote(str(build_uat))}\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            f"COMMAND_DIR={shlex.quote(str(tmp_path))}\n"
            "COMMAND_IDENTITY=$(pinned_directory_identity \"$COMMAND_DIR\")\n"
            "for name in server-unique.log journey-unique.log a11y-unique.log; do\n"
            "  with_pinned_dir \"$BUILD_UAT\" \"$BUILD_UAT_IDENTITY\" "
            "run_with_new_log \"$name\" \"$COMMAND_DIR\" \"$COMMAND_IDENTITY\" "
            "printf 'NEW-DATA\\n' || exit 31\n"
            "done\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    for index, (target, alias) in enumerate(zip(targets, aliases)):
        assert target.read_text(encoding="utf-8") == f"KEEP-{index}\n"
        assert alias.read_text(encoding="utf-8") == f"KEEP-{index}\n"
        if alias_kind == "symlink":
            assert alias.is_symlink()
        else:
            assert alias.stat().st_ino == target.stat().st_ino
    for unique_name in ("server-unique.log", "journey-unique.log", "a11y-unique.log"):
        assert (build_uat / unique_name).read_text(encoding="utf-8") == "NEW-DATA\n"

    assert 'SERVER_LOG_NAME=$(new_log_name local-server)' in source
    assert 'log_name=$(new_log_name "$label")' in source
    assert 'A11Y_LOG_NAME=$(new_log_name a11y)' in source


def test_server_journey_and_a11y_logs_preserve_symlink_targets(tmp_path: Path) -> None:
    exercise_log_alias_preservation(tmp_path, "symlink")


def test_server_journey_and_a11y_logs_preserve_hardlink_targets(tmp_path: Path) -> None:
    exercise_log_alias_preservation(tmp_path, "hardlink")


def test_logged_commands_run_from_pinned_repository_root(tmp_path: Path) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    build_uat.mkdir(parents=True)
    functions = "\n\n".join(
        shell_function(source, name)
        for name in (
            "pinned_directory_identity",
            "with_pinned_dir",
            "open_new_relative_file_fd",
            "enter_pinned_dir",
            "close_inherited_pinning_fds",
            "run_with_new_log",
        )
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "ROOT_IDENTITY=$(pinned_directory_identity \"$ROOT\")\n"
            "BUILD_UAT=\"$ROOT/build/uat\"\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            "with_pinned_dir \"$BUILD_UAT\" \"$BUILD_UAT_IDENTITY\" run_with_new_log "
            "cwd.log \"$ROOT\" \"$ROOT_IDENTITY\" pwd\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert (build_uat / "cwd.log").read_text(encoding="utf-8").strip() == str(tmp_path)


def test_server_pid_is_execed_process_and_cleanup_terminates_it(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    build_uat = tmp_path / "build" / "uat"
    site.mkdir()
    build_uat.mkdir(parents=True)
    functions = "\n\n".join(
        shell_function(source, name)
        for name in (
            "pinned_directory_identity",
            "with_pinned_dir",
            "open_new_relative_file_fd",
            "enter_pinned_dir",
            "close_inherited_pinning_fds",
            "exec_with_new_log",
            "remove_in_pinned_dir",
            "cleanup",
        )
    )
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "restore_build_stamp() { return 0; }\n"
            f"ROOT={shlex.quote(str(tmp_path))}\n"
            "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
            "BUILD_UAT=\"$ROOT/build/uat\"\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            "MARKER_NAME=''\nMARKER_PATH=''\nBUILD_STAMP_DIRTY=0\nLOCK_HELD=0\n"
            "with_pinned_dir \"$BUILD_UAT\" \"$BUILD_UAT_IDENTITY\" exec_with_new_log "
            "server.log \"$ROOT/site\" \"$SITE_IDENTITY\" sleep 30 &\n"
            "SERVER_PID=$!\n"
            "for _ in {1..100}; do [ -e \"/proc/$SERVER_PID/exe\" ] && break; sleep 0.01; done\n"
            "test \"$(basename \"$(readlink \"/proc/$SERVER_PID/exe\")\")\" = sleep\n"
            "test \"$(readlink \"/proc/$SERVER_PID/cwd\")\" = \"$ROOT/site\"\n"
            "cleanup 0\n"
            "! kill -0 \"$SERVER_PID\" 2>/dev/null\n"
        ),
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_server_child_does_not_inherit_run_lock_after_owner_crash(tmp_path: Path) -> None:
    source = script_source()
    site = tmp_path / "site"
    build_uat = tmp_path / "build" / "uat"
    site.mkdir()
    build_uat.mkdir(parents=True)
    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
        "release_revalidation_lock",
        "enter_pinned_dir",
        "close_inherited_pinning_fds",
        "exec_with_new_log",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    pid_file = tmp_path / "owner-and-child-pids"
    setup = (
        f"set -uo pipefail\n{functions}\n"
        f"ROOT={shlex.quote(str(tmp_path))}\n"
        "BUILD_UAT=\"$ROOT/build/uat\"\n"
        "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
        "SITE_IDENTITY=$(pinned_directory_identity \"$ROOT/site\")\n"
        "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
        "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
        f"SHA={'deadbeef' * 5}\n"
    )
    owner_program = (
        setup
        + "acquire_revalidation_lock\n"
        + "with_pinned_dir \"$BUILD_UAT\" \"$BUILD_UAT_IDENTITY\" exec_with_new_log "
        + "server.log \"$ROOT/site\" \"$SITE_IDENTITY\" sleep 30 &\n"
        + "SERVER_PID=$!\n"
        + f"printf '%s %s\\n' \"$$\" \"$SERVER_PID\" > {shlex.quote(str(pid_file))}\n"
        + "wait \"$SERVER_PID\"\n"
    )
    owner = subprocess.Popen(
        ["bash"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert owner.stdin is not None
    owner.stdin.write(owner_program)
    owner.stdin.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.01)
    assert pid_file.exists(), "lock owner did not launch its server child"
    owner_pid, server_pid = map(int, pid_file.read_text(encoding="utf-8").split())
    assert owner_pid == owner.pid
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            executable = Path(f"/proc/{server_pid}/exe")
            if executable.exists() and Path(os.readlink(executable)).name == "sleep":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("server child did not reach its exec boundary")
        owner.kill()
        owner.wait(timeout=3)
        assert Path(f"/proc/{server_pid}").exists(), "server child exited before recovery probe"
        contender = run_bash(
            tmp_path,
            setup + "acquire_revalidation_lock\nrelease_revalidation_lock\n",
            timeout=3,
        )
        assert contender.returncode == 0, contender.stderr
        assert "removed stale final-revalidation lock" in contender.stdout
    finally:
        try:
            os.kill(server_pid, 15)
        except ProcessLookupError:
            pass


def test_run_lock_allows_only_one_contender_to_clear_and_enter_legs(
    tmp_path: Path,
) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    for evidence_name in ("runs", "shots"):
        evidence = build_uat / evidence_name
        evidence.mkdir(parents=True)
        (evidence / "old.txt").write_text("old\n", encoding="utf-8")

    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
        "release_revalidation_lock",
        "clear_prior_evidence",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    entrants = tmp_path / "entrants"
    clearers = tmp_path / "clearers"
    release = tmp_path / "release"
    program = (
        f"set -uo pipefail\n{functions}\n"
        f"ROOT={shlex.quote(str(tmp_path))}\n"
        "BUILD_UAT=\"$ROOT/build/uat\"\n"
        "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
        "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
        "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
        f"SHA={'deadbeef' * 5}\n"
        "trap 'release_revalidation_lock >/dev/null 2>&1 || true' EXIT\n"
        "acquire_revalidation_lock\n"
        f"printf '%s\\n' \"$$\" >> {shlex.quote(str(clearers))}\n"
        "clear_prior_evidence\n"
        f"printf '%s\\n' \"$$\" >> {shlex.quote(str(entrants))}\n"
        f"for _ in {{1..200}}; do [ -e {shlex.quote(str(release))} ] && exit 0; sleep 0.01; done\n"
        "exit 41\n"
    )

    processes: list[subprocess.Popen[str]] = []
    for _ in range(2):
        process = subprocess.Popen(
            ["bash"],
            cwd=tmp_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        process.stdin.write(program)
        process.stdin.close()
        processes.append(process)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not entrants.exists():
        time.sleep(0.01)
    assert entrants.exists(), "neither contender acquired the lock"

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and all(process.poll() is None for process in processes):
        time.sleep(0.01)
    release.write_text("release\n", encoding="utf-8")
    for process in processes:
        process.wait(timeout=3)

    results = []
    for process in processes:
        assert process.stdout is not None
        assert process.stderr is not None
        results.append((process.returncode, process.stdout.read(), process.stderr.read()))
    assert sorted(result[0] == 0 for result in results) == [False, True]
    assert len(entrants.read_text(encoding="utf-8").splitlines()) == 1
    assert len(clearers.read_text(encoding="utf-8").splitlines()) == 1
    loser = next(result for result in results if result[0] != 0)
    assert "another final revalidation run owns build/uat/" in loser[2]
    assert not (build_uat / "runs").exists()
    assert not (build_uat / "shots").exists()
    assert not (build_uat / ".final-revalidation.lock").exists()


def test_dead_run_lock_is_recovered_before_acquisition(tmp_path: Path) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    lock = build_uat / ".final-revalidation.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n",
        encoding="utf-8",
    )
    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
        "release_revalidation_lock",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            f"BUILD_UAT={shlex.quote(str(build_uat))}\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
            "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
            f"SHA={'deadbeef' * 5}\n"
            "acquire_revalidation_lock\n"
            "test \"$LOCK_HELD\" -eq 1\n"
            "release_revalidation_lock\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "removed stale final-revalidation lock from build/uat/\n"
    assert not lock.exists()


def test_live_lock_is_preserved_but_reused_pid_lock_is_recovered(tmp_path: Path) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    lock = build_uat / ".final-revalidation.lock"
    lock.mkdir(parents=True)
    _, live_start_ticks = read_process_identity(os.getpid())
    sha = "deadbeef" * 5
    owner = lock / "owner"
    owner.write_text(
        f"final-revalidation:{sha}:{os.getpid()}:{live_start_ticks}:12345\n",
        encoding="utf-8",
    )
    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
        "release_revalidation_lock",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    setup = (
        f"set -uo pipefail\n{functions}\n"
        f"BUILD_UAT={shlex.quote(str(build_uat))}\n"
        "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
        "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
        "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
        f"SHA={sha}\n"
    )
    live_result = run_bash(tmp_path, setup + "acquire_revalidation_lock\n")
    assert live_result.returncode == 1
    assert f"(pid {os.getpid()})" in live_result.stderr
    assert lock.exists()

    owner.write_text(
        f"final-revalidation:{sha}:{os.getpid()}:{int(live_start_ticks) + 1}:12345\n",
        encoding="utf-8",
    )
    reused_result = run_bash(
        tmp_path,
        setup + "acquire_revalidation_lock\nrelease_revalidation_lock\n",
    )
    assert reused_result.returncode == 0, reused_result.stderr
    assert "removed stale final-revalidation lock" in reused_result.stdout
    assert not lock.exists()


def test_malformed_and_uncertain_lock_owners_are_preserved(tmp_path: Path) -> None:
    source = script_source()
    sha = "deadbeef" * 5
    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    fixtures: list[tuple[str, str | None]] = [
        ("missing", None),
        ("malformed", "not-a-lock-token\n"),
        ("oversized", "x" * 257),
    ]
    for label, owner_contents in fixtures:
        build_uat = tmp_path / label / "build" / "uat"
        lock = build_uat / ".final-revalidation.lock"
        lock.mkdir(parents=True)
        if owner_contents is not None:
            (lock / "owner").write_text(owner_contents, encoding="utf-8")
        setup = (
            f"set -uo pipefail\n{functions}\n"
            f"BUILD_UAT={shlex.quote(str(build_uat))}\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
            "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
            f"SHA={sha}\n"
            "acquire_revalidation_lock\n"
        )
        result = run_bash(tmp_path, setup)
        assert result.returncode == 1, label
        assert "lock owner" in result.stderr and "malformed" in result.stderr, label
        assert lock.exists(), label

    uncertain_uat = tmp_path / "uncertain" / "build" / "uat"
    uncertain_lock = uncertain_uat / ".final-revalidation.lock"
    uncertain_lock.mkdir(parents=True)
    uncertain_program = (
        f"set -uo pipefail\n{functions}\n"
        "process_identity() {\n"
        "  if [ \"$1\" = \"$$\" ]; then printf 'S 1\\n'; else return 1; fi\n"
        "}\n"
        f"BUILD_UAT={shlex.quote(str(uncertain_uat))}\n"
        "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
        "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
        "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
        f"SHA={sha}\n"
        f"printf 'final-revalidation:%s:{os.getpid()}:1:12345\\n' \"$SHA\" > "
        '"$BUILD_UAT/$LOCK_NAME/owner"\n'
        "acquire_revalidation_lock\n"
    )
    uncertain_result = run_bash(tmp_path, uncertain_program)
    assert uncertain_result.returncode == 1
    assert "identity is unreadable" in uncertain_result.stderr
    assert uncertain_lock.exists()


def test_lock_initialization_failure_removes_partial_owned_lock(tmp_path: Path) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    build_uat.mkdir(parents=True)
    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
        "release_revalidation_lock",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    result = run_bash(
        tmp_path,
        (
            f"set -uo pipefail\n{functions}\n"
            "write_lock_owner() { return 1; }\n"
            f"BUILD_UAT={shlex.quote(str(build_uat))}\n"
            "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
            "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
            "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
            f"SHA={'deadbeef' * 5}\n"
            "trap 'release_revalidation_lock >/dev/null 2>&1 || true' EXIT\n"
            "acquire_revalidation_lock\n"
        ),
    )
    assert result.returncode == 1
    assert "could not initialize the final-revalidation run lock" in result.stderr
    assert not (build_uat / ".final-revalidation.lock").exists()


def test_two_contenders_serialize_stale_lock_recovery(tmp_path: Path) -> None:
    source = script_source()
    build_uat = tmp_path / "build" / "uat"
    lock = build_uat / ".final-revalidation.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(
        f"final-revalidation:{'deadbeef' * 5}:99999999:1:12345\n",
        encoding="utf-8",
    )
    function_names = [
        "die",
        "pinned_directory_identity",
        "with_pinned_dir",
        "remove_in_pinned_dir",
        "process_identity",
        "read_regular_relative_file",
        "open_new_relative_file_fd",
        "write_lock_owner",
        "open_pinned_directory_fd",
        "acquire_revalidation_lock",
        "release_revalidation_lock",
    ]
    functions = "\n\n".join(shell_function(source, name) for name in function_names)
    entrants = tmp_path / "stale-entrants"
    release = tmp_path / "stale-release"
    program = (
        f"set -uo pipefail\n{functions}\n"
        f"BUILD_UAT={shlex.quote(str(build_uat))}\n"
        "BUILD_UAT_IDENTITY=$(pinned_directory_identity \"$BUILD_UAT\")\n"
        "LOCK_NAME=.final-revalidation.lock\nLOCK_HELD=0\nLOCK_INITIALIZING=0\n"
        "LOCK_IDENTITY=''\nLOCK_TOKEN=''\nLOCK_GUARD_FD=''\n"
        f"SHA={'deadbeef' * 5}\n"
        "trap 'release_revalidation_lock >/dev/null 2>&1 || true' EXIT\n"
        "acquire_revalidation_lock\n"
        f"printf '%s\\n' \"$$\" >> {shlex.quote(str(entrants))}\n"
        f"for _ in {{1..200}}; do [ -e {shlex.quote(str(release))} ] && exit 0; sleep 0.01; done\n"
        "exit 41\n"
    )
    processes: list[subprocess.Popen[str]] = []
    for _ in range(2):
        process = subprocess.Popen(
            ["bash"],
            cwd=tmp_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        process.stdin.write(program)
        process.stdin.close()
        processes.append(process)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not entrants.exists():
        time.sleep(0.01)
    assert entrants.exists(), "neither contender recovered the stale lock"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and all(process.poll() is None for process in processes):
        time.sleep(0.01)
    release.write_text("release\n", encoding="utf-8")
    for process in processes:
        process.wait(timeout=3)

    results = []
    for process in processes:
        assert process.stdout is not None
        assert process.stderr is not None
        results.append((process.returncode, process.stdout.read(), process.stderr.read()))
    assert sorted(result[0] == 0 for result in results) == [False, True]
    assert len(entrants.read_text(encoding="utf-8").splitlines()) == 1
    assert sum("removed stale final-revalidation lock" in result[1] for result in results) == 1
    loser = next(result for result in results if result[0] != 0)
    assert "another final revalidation run owns build/uat/" in loser[2]
    assert not lock.exists()
