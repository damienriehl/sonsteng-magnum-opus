"""Safety contract for upgrading an existing config-off PROD environment."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


migration = _load("migrate_prod_release_env", TOOLS / "migrate_prod_release_env.py")
daemon = _load("prod_release_daemon_for_env_migration", TOOLS / "prod_release_daemon.py")


def _legacy_env(path: Path, extra: str = "") -> bytes:
    original = (
        "# legacy operator comment must survive byte-for-byte\n"
        "SONSTENG_PROD_RELEASE_ENABLED=false\n"
        "SONSTENG_PROD_LEDGER_URL=https://legacy.example.invalid\n"
        "SONSTENG_PROD_RELEASE_BEARER=release-secret-sentinel\n"
        f"{extra}"
    ).encode()
    path.write_bytes(original)
    path.chmod(0o600)
    return original


def test_migration_appends_every_required_key_and_preserves_existing_bytes(tmp_path, capsys):
    env_file = tmp_path / "env"
    original = _legacy_env(env_file)

    result = migration.migrate_env_file(
        env_file,
        daemon_root=tmp_path / "daemon root",
        state_root=tmp_path / "state root",
    )

    output = capsys.readouterr()
    migrated = env_file.read_bytes()
    assert result.added_count > 0
    assert migrated.startswith(original)
    assert "SONSTENG_PROD_LEDGER_URL=https://legacy.example.invalid\n" in migrated.decode()
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert set(migration.REQUIRED_DEFAULTS) == (
        set(daemon.CONFIG_DIGEST_KEYS)
        | {
            "SONSTENG_PROD_RELEASE_ENABLED",
            "SONSTENG_PROD_EXPECTED_CONFIG_DIGEST",
            "SONSTENG_PROD_RELEASE_BEARER",
            "SONSTENG_PROD_CLOUDFLARE_API_TOKEN",
        }
    )
    installer_keys = {
        line.split("=", 1)[0]
        for line in (TOOLS / "install-prod-release-daemon.sh").read_text(encoding="utf-8").splitlines()
        if line.startswith("SONSTENG_") and "=" in line
    }
    assert set(migration.REQUIRED_DEFAULTS) == installer_keys
    assignments = migration.active_assignments(migrated.decode())
    assert set(migration.REQUIRED_DEFAULTS).issubset(assignments)
    combined_output = output.out + output.err
    assert "release-secret-sentinel" not in combined_output
    assert "https://legacy.example.invalid" not in combined_output


def test_migration_is_idempotent_and_does_not_replace_a_current_file(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    _legacy_env(env_file)
    migration.migrate_env_file(env_file, daemon_root=tmp_path / "daemon", state_root=tmp_path / "state")
    migrated = env_file.read_bytes()
    inode = env_file.stat().st_ino

    def unexpected_replace(*_args):
        raise AssertionError("an already-current file must not be replaced")

    monkeypatch.setattr(migration.os, "replace", unexpected_replace)
    result = migration.migrate_env_file(
        env_file, daemon_root=tmp_path / "daemon", state_root=tmp_path / "state"
    )

    assert result.added_count == 0
    assert env_file.read_bytes() == migrated
    assert env_file.stat().st_ino == inode


def test_migration_uses_same_directory_atomic_replace_with_mode_0600(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    _legacy_env(env_file)
    real_replace = migration.os.replace
    observed = {}

    def checked_replace(source, target):
        source_path = Path(source)
        observed["source"] = source_path
        observed["target"] = Path(target)
        observed["mode"] = stat.S_IMODE(source_path.stat().st_mode)
        real_replace(source, target)

    monkeypatch.setattr(migration.os, "replace", checked_replace)
    migration.migrate_env_file(env_file, daemon_root=tmp_path / "daemon", state_root=tmp_path / "state")

    assert observed == {
        "source": observed["source"],
        "target": env_file,
        "mode": 0o600,
    }
    assert observed["source"].parent == env_file.parent
    assert env_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("enabled_line", [
    "SONSTENG_PROD_RELEASE_ENABLED=true\n",
    "SONSTENG_PROD_RELEASE_ENABLED=\"false\"\n",
    "# SONSTENG_PROD_RELEASE_ENABLED=false\n",
    "",
])
def test_migration_fails_closed_unless_enabled_is_one_literal_false(
    tmp_path, capsys, enabled_line
):
    env_file = tmp_path / "env"
    secret = "do-not-disclose-this-secret"
    original = (enabled_line + f"SONSTENG_PROD_RELEASE_BEARER={secret}\n").encode()
    env_file.write_bytes(original)
    env_file.chmod(0o600)

    with pytest.raises(migration.MigrationError) as raised:
        migration.migrate_env_file(
            env_file, daemon_root=tmp_path / "daemon", state_root=tmp_path / "state"
        )

    output = capsys.readouterr()
    assert env_file.read_bytes() == original
    assert secret not in str(raised.value)
    assert secret not in output.out + output.err


def test_migration_rejects_duplicate_enabled_assignments(tmp_path):
    env_file = tmp_path / "env"
    original = _legacy_env(env_file, "SONSTENG_PROD_RELEASE_ENABLED=false\n")

    with pytest.raises(migration.MigrationError, match="config-off"):
        migration.migrate_env_file(
            env_file, daemon_root=tmp_path / "daemon", state_root=tmp_path / "state"
        )

    assert env_file.read_bytes() == original


def test_cli_failure_is_generic_and_does_not_leak_existing_values(tmp_path, capsys):
    env_file = tmp_path / "env"
    secret = "cli-error-secret-sentinel"
    original = (
        "SONSTENG_PROD_RELEASE_ENABLED=true\n"
        f"SONSTENG_PROD_RELEASE_BEARER={secret}\n"
    ).encode()
    env_file.write_bytes(original)
    env_file.chmod(0o600)

    exit_code = migration.main([
        "--env-file", str(env_file),
        "--daemon-root", str(tmp_path / "daemon"),
        "--state-root", str(tmp_path / "state"),
    ])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "migration refused" in output.err
    assert secret not in output.out + output.err
    assert env_file.read_bytes() == original


@pytest.mark.parametrize("unsafe_kind", ["mode", "symlink", "directory"])
def test_migration_rejects_unsafe_existing_files_without_secret_leakage(
    tmp_path, capsys, unsafe_kind
):
    env_file = tmp_path / "env"
    secret = "unsafe-file-secret-sentinel"
    if unsafe_kind == "directory":
        env_file.mkdir()
    else:
        target = tmp_path / "target"
        _legacy_env(target, f"# {secret}\n")
        if unsafe_kind == "symlink":
            env_file.symlink_to(target)
        else:
            target.rename(env_file)
            env_file.chmod(0o640)

    with pytest.raises(migration.MigrationError) as raised:
        migration.migrate_env_file(
            env_file, daemon_root=tmp_path / "daemon", state_root=tmp_path / "state"
        )

    output = capsys.readouterr()
    assert secret not in str(raised.value)
    assert secret not in output.out + output.err


def test_installer_rerun_migrates_legacy_env_but_only_reloads_systemd(tmp_path):
    home = tmp_path / "home"
    config = tmp_path / "config"
    state = tmp_path / "state"
    daemon_root = tmp_path / "daemon"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "systemctl-calls"
    (daemon_root / ".git").mkdir(parents=True)
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$SYSTEMCTL_CALLS\"\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    env_file = config / "sonsteng-prod-release" / "env"
    env_file.parent.mkdir(parents=True)
    original = _legacy_env(env_file)
    environ = os.environ.copy()
    environ.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_STATE_HOME": str(state),
        "SONSTENG_DAEMON_ROOT": str(daemon_root),
        "SYSTEMCTL_CALLS": str(calls),
        "PATH": f"{fake_bin}:{environ['PATH']}",
    })

    completed = subprocess.run(
        ["bash", str(TOOLS / "install-prod-release-daemon.sh")],
        cwd=ROOT,
        env=environ,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert env_file.read_bytes().startswith(original)
    assert "release-secret-sentinel" not in completed.stdout + completed.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["--user daemon-reload"]
    assert "units; they remain disabled" in completed.stdout


def test_installer_rejects_dangling_env_symlink_before_writing_target(tmp_path):
    home = tmp_path / "home"
    config = tmp_path / "config"
    state = tmp_path / "state"
    daemon_root = tmp_path / "daemon"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "systemctl-calls"
    (daemon_root / ".git").mkdir(parents=True)
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$SYSTEMCTL_CALLS\"\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    env_file = config / "sonsteng-prod-release" / "env"
    env_file.parent.mkdir(parents=True)
    outside = tmp_path / "must-not-be-created"
    env_file.symlink_to(outside)
    environ = os.environ.copy()
    environ.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_STATE_HOME": str(state),
        "SONSTENG_DAEMON_ROOT": str(daemon_root),
        "SYSTEMCTL_CALLS": str(calls),
        "PATH": f"{fake_bin}:{environ['PATH']}",
    })

    completed = subprocess.run(
        ["bash", str(TOOLS / "install-prod-release-daemon.sh")],
        cwd=ROOT,
        env=environ,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not outside.exists()
    assert not calls.exists()
