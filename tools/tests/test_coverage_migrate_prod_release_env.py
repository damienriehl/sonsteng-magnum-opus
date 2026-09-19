"""Offline config migration integration and atomic-write failure boundaries."""
from pathlib import Path
import os
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_prod_release_env as migration


def config_file(tmp_path, payload=b'SONSTENG_PROD_RELEASE_ENABLED=false\n'):
    path = tmp_path / 'config.txt'
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def migrate(path):
    return migration.migrate_env_file(
        path, daemon_root=path.parent / 'daemon', state_root=path.parent / 'state'
    )


def test_cli_migrates_real_config_and_second_run_preserves_inode(tmp_path, capsys):
    path = config_file(tmp_path, b'# retained\r\nSONSTENG_PROD_RELEASE_ENABLED=false')
    argv = ['--env-file', str(path), '--daemon-root', str(tmp_path / 'daemon'),
            '--state-root', str(tmp_path / 'state')]
    assert migration.main(argv) == 0
    assert '20 added, 0 updated' in capsys.readouterr().out
    content = path.read_bytes()
    assert content.startswith(b'# retained\r\nSONSTENG_PROD_RELEASE_ENABLED=false\n')
    assert set(migration.REQUIRED_DEFAULTS) <= migration.active_assignments(content.decode())
    assert f'SONSTENG_PROD_REPO={tmp_path / "daemon"}\n'.encode() in content
    assert f'SONSTENG_PROD_MANIFEST={tmp_path / "state/authorized-manifest.json"}\n'.encode() in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    inode = path.stat().st_ino
    assert migration.main(argv) == 0
    assert 'environment is current' in capsys.readouterr().out
    assert path.read_bytes() == content
    assert path.stat().st_ino == inode


def test_retired_provenance_without_digest_preserves_crlf_and_appends_defaults(tmp_path):
    path = config_file(tmp_path, (
        'SONSTENG_PROD_RELEASE_ENABLED=false\r\n'
        '  SONSTENG_PROD_PAGES_PROVENANCE_URL =https://sonsteng.damienriehl.com/platform/\r\n'
    ).encode())
    result = migrate(path)
    assert result == migration.MigrationResult(19, 1)
    assert b'  SONSTENG_PROD_PAGES_PROVENANCE_URL =https://legalpracticum.org/platform/\r\n' in path.read_bytes()
    assert b'SONSTENG_PROD_EXPECTED_CONFIG_DIGEST=\n' in path.read_bytes()


def test_duplicate_digest_refuses_retired_default_rewrite_without_changing_file(tmp_path):
    path = config_file(tmp_path, (
        'SONSTENG_PROD_RELEASE_ENABLED=false\n'
        'SONSTENG_PROD_PAGES_PROVENANCE_URL=https://sonsteng.damienriehl.com/platform/\n'
        'SONSTENG_PROD_EXPECTED_CONFIG_DIGEST=first\n'
        'SONSTENG_PROD_EXPECTED_CONFIG_DIGEST=second\n'
    ).encode())
    before = path.read_bytes()
    with pytest.raises(migration.MigrationError, match='duplicate config digest'):
        migrate(path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('payload', [b'\xff', b'SONSTENG_PROD_RELEASE_ENABLED=false\n\x80'])
def test_invalid_utf8_is_rejected_before_replacement(tmp_path, payload):
    path = config_file(tmp_path, payload)
    with pytest.raises(migration.MigrationError, match='valid UTF-8'):
        migrate(path)
    assert path.read_bytes() == payload


def test_missing_config_is_generic_cli_failure(tmp_path, capsys):
    path = tmp_path / 'missing.txt'
    assert migration.main(['--env-file', str(path), '--daemon-root', str(tmp_path),
                           '--state-root', str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert 'migration refused' in output.err
    assert not path.exists()


def test_open_failure_leaves_existing_file_untouched(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    before = path.read_bytes()
    real_open = os.open
    def denied(target, *args, **kwargs):
        if Path(target) == path:
            raise PermissionError('synthetic access denied')
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(migration.os, 'open', denied)
    with pytest.raises(migration.MigrationError, match='opened safely'):
        migrate(path)
    assert path.read_bytes() == before


def test_inode_swap_between_lstat_and_open_is_rejected(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    replacement = tmp_path / 'replacement.txt'
    replacement.write_bytes(b'operator update\n')
    replacement.chmod(0o600)
    real_open = os.open
    def swapped(target, *args, **kwargs):
        if Path(target) == path:
            os.replace(replacement, path)
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(migration.os, 'open', swapped)
    with pytest.raises(migration.MigrationError, match='changed during validation'):
        migrate(path)
    assert path.read_bytes() == b'operator update\n'


@pytest.mark.parametrize('operation,exception,message', [
    ('fchmod', OSError, 'failed safely'),
    ('fchown', PermissionError, 'ownership could not be preserved'),
    ('fsync', OSError, 'failed safely'),
    ('replace', OSError, 'failed safely'),
])
def test_staging_failures_preserve_original_and_remove_temporary_file(
    tmp_path, monkeypatch, operation, exception, message
):
    path = config_file(tmp_path)
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise exception('synthetic staging failure')
    monkeypatch.setattr(migration.os, operation, fail)
    with pytest.raises(migration.MigrationError, match=message):
        migrate(path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_operator_change_during_staging_is_preserved(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    real_fsync = os.fsync
    update = b'SONSTENG_PROD_RELEASE_ENABLED=false\n# operator update\n'
    def change_before_replace(fd):
        path.write_bytes(update)
        return real_fsync(fd)
    monkeypatch.setattr(migration.os, 'fsync', change_before_replace)
    with pytest.raises(migration.MigrationError, match='changed during migration'):
        migrate(path)
    assert path.read_bytes() == update
    assert list(tmp_path.iterdir()) == [path]


def test_disappearing_staged_file_does_not_mask_original_failure(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    before = path.read_bytes()
    def removed_before_replace(source, target):
        Path(source).unlink()
        raise OSError('synthetic staged file disappearance')
    monkeypatch.setattr(migration.os, 'replace', removed_before_replace)
    with pytest.raises(migration.MigrationError, match='failed safely'):
        migrate(path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_safe_reader_collects_multiple_chunks(tmp_path):
    payload = b'SONSTENG_PROD_RELEASE_ENABLED=false\n#' + b'x' * (1024 * 1024 + 100)
    path = config_file(tmp_path, payload)
    content, observed = migration._read_safe_existing(path)
    assert content == payload
    assert observed.st_size == len(payload)


def test_only_credentials_missing_are_appended_blank(tmp_path):
    defaults = migration._defaults(tmp_path / 'daemon', tmp_path / 'state')
    payload = '\n'.join(f'{key}={value}' for key, value in defaults.items()
                        if key not in migration._CREDENTIAL_KEYS).encode()
    path = config_file(tmp_path, payload)
    assert migrate(path) == migration.MigrationResult(2, 0)
    appended = path.read_bytes()[len(payload):]
    assert b'# Non-secret controls.' not in appended
    for key in migration._CREDENTIAL_KEYS:
        assert f'{key}=\n'.encode() in appended
