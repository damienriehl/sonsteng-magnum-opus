from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
JOURNEY = ROOT / "tools" / "uat_adopter_journey.sh"


def shell_function(name: str) -> str:
    source = JOURNEY.read_text(encoding="utf-8")
    match = re.search(
        rf"^{name}\(\) \{{\n.*?^\}}\n",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match, f"missing {name} shell function"
    return match.group(0)


def invoke_check(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail\nTEMP_HOME="$1"\n' + shell_function("check_wrangler_home") + "\ncheck_wrangler_home",
            "bash",
            str(home),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_worker_dry_run_disables_wrangler_metrics_in_the_clean_environment(tmp_path: Path) -> None:
    worker = tmp_path / "clone" / "app" / "worker"
    bin_dir = tmp_path / "bin"
    worker.mkdir(parents=True)
    bin_dir.mkdir()
    fake_npx = bin_dir / "npx"
    fake_npx.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        '[[ "${WRANGLER_SEND_METRICS:-}" == "false" ]]\n'
        '[[ "$*" == "wrangler@4 deploy --dry-run" ]]\n',
        encoding="utf-8",
    )
    fake_npx.chmod(0o755)
    command = "\n".join(
        [
            "set -euo pipefail",
            'CLONE_ROOT="$1/clone"',
            'TEMP_HOME="$1/home"',
            'MINIMAL_PATH="$1/bin:/usr/bin:/bin"',
            "prepare_worker_data() { :; }",
            "check_wrangler_home() { :; }",
            shell_function("run_worker_dry_run"),
            "run_worker_dry_run",
        ]
    )

    result = subprocess.run(
        ["bash", "-c", command, "bash", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Worker dry-run completed without login or deployment" in result.stdout


def test_metrics_and_logs_are_listed_but_not_treated_as_auth(tmp_path: Path) -> None:
    wrangler = tmp_path / ".config" / ".wrangler"
    (wrangler / "logs").mkdir(parents=True)
    (wrangler / "metrics.json").write_text('{"permission": false}\n', encoding="utf-8")
    (wrangler / "logs" / "wrangler-test.log").write_text("dry run\n", encoding="utf-8")

    result = invoke_check(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "metrics.json" in result.stdout
    assert "wrangler-test.log" in result.stdout


@pytest.mark.parametrize(
    "relative",
    [
        Path(".config/.wrangler/config/default.toml"),
        Path(".wrangler/config/default.toml"),
    ],
)
def test_wrangler_default_auth_files_fail_the_check(tmp_path: Path, relative: Path) -> None:
    auth_file = tmp_path / relative
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text("[user]\n", encoding="utf-8")

    result = invoke_check(tmp_path)

    assert result.returncode == 1
    assert str(auth_file) in result.stdout
    assert str(auth_file) in result.stderr


@pytest.mark.parametrize("token_name", ["oauth_token", "refresh_token", "api_token"])
def test_token_material_anywhere_in_wrangler_trees_fails(tmp_path: Path, token_name: str) -> None:
    state_file = tmp_path / ".config" / ".wrangler" / "state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(f'{{"{token_name}": "fixture-value"}}\n', encoding="utf-8")

    result = invoke_check(tmp_path)

    assert result.returncode == 1
    assert str(state_file) in result.stdout
    assert str(state_file) in result.stderr
