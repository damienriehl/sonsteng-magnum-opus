#!/usr/bin/env python3
"""Safely migrate settings in a config-off PROD release environment.

This helper accepts only a regular 0600 file with one literal config-off flag.
It appends defaults for keys introduced after the file was created and rewrites
only explicitly recognized retired defaults, preserving all other assignments.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import NamedTuple


REQUIRED_DEFAULTS = (
    "SONSTENG_PROD_RELEASE_ENABLED",
    "SONSTENG_PROD_RELEASE_MODE",
    "SONSTENG_PROD_CANARY_RELEASE_ID",
    "SONSTENG_PROD_EXPECTED_CONFIG_DIGEST",
    "SONSTENG_PROD_LEDGER_URL",
    "SONSTENG_PROD_RELEASE_BEARER",
    "SONSTENG_PROD_PAGES_PROJECT",
    "SONSTENG_PROD_CLOUDFLARE_ACCOUNT_ID",
    "SONSTENG_PROD_CLOUDFLARE_API_TOKEN",
    "SONSTENG_PROD_PAGES_BRANCH",
    "SONSTENG_PROD_PAGES_ARTIFACT",
    "SONSTENG_PROD_PAGES_PROVENANCE_URL",
    "SONSTENG_PROD_WORKER_CONFIG",
    "SONSTENG_PROD_WORKER_PROVENANCE_URL",
    "SONSTENG_PROD_REPO",
    "SONSTENG_PROD_MANIFEST",
    "SONSTENG_PROD_RECOVERY_REGISTRY",
    "SONSTENG_PROD_LOCK",
    "SONSTENG_PROD_BOOTSTRAP_BASE_SHA",
    "SONSTENG_NEW_WORKER_ACCEPTS_OLD_PAGES",
    "SONSTENG_OLD_WORKER_ACCEPTS_NEW_PAGES",
)

_CREDENTIAL_KEYS = (
    "SONSTENG_PROD_RELEASE_BEARER",
    "SONSTENG_PROD_CLOUDFLARE_API_TOKEN",
)
_ASSIGNMENT = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$")
_LEGACY_PAGES_PROVENANCE_URL = "https://sonsteng.damienriehl.com/platform/"
_CANONICAL_PAGES_PROVENANCE_URL = "https://legalpracticum.org/platform/"


class MigrationError(RuntimeError):
    """The environment cannot be migrated without weakening safety."""


class MigrationResult(NamedTuple):
    added_count: int
    updated_count: int


def _defaults(daemon_root: Path, state_root: Path) -> dict[str, str]:
    return {
        "SONSTENG_PROD_RELEASE_ENABLED": "false",
        "SONSTENG_PROD_RELEASE_MODE": "routine",
        "SONSTENG_PROD_CANARY_RELEASE_ID": "",
        "SONSTENG_PROD_EXPECTED_CONFIG_DIGEST": "",
        "SONSTENG_PROD_LEDGER_URL": "https://sonsteng-chat.damienriehl.workers.dev",
        "SONSTENG_PROD_RELEASE_BEARER": "",
        "SONSTENG_PROD_PAGES_PROJECT": "sonsteng",
        "SONSTENG_PROD_CLOUDFLARE_ACCOUNT_ID": "",
        "SONSTENG_PROD_CLOUDFLARE_API_TOKEN": "",
        "SONSTENG_PROD_PAGES_BRANCH": "main",
        "SONSTENG_PROD_PAGES_ARTIFACT": str(daemon_root / "site"),
        "SONSTENG_PROD_PAGES_PROVENANCE_URL": "https://legalpracticum.org/platform/",
        "SONSTENG_PROD_WORKER_CONFIG": str(daemon_root / "app/worker/wrangler.jsonc"),
        "SONSTENG_PROD_WORKER_PROVENANCE_URL": (
            "https://sonsteng-chat-production.damienriehl.workers.dev/edit/release-provenance"
        ),
        "SONSTENG_PROD_REPO": str(daemon_root),
        "SONSTENG_PROD_MANIFEST": str(state_root / "authorized-manifest.json"),
        "SONSTENG_PROD_RECOVERY_REGISTRY": str(state_root / "known-good-pairs.json"),
        "SONSTENG_PROD_LOCK": str(daemon_root / ".locks/daemon.lock"),
        "SONSTENG_PROD_BOOTSTRAP_BASE_SHA": "",
        "SONSTENG_NEW_WORKER_ACCEPTS_OLD_PAGES": "false",
        "SONSTENG_OLD_WORKER_ACCEPTS_NEW_PAGES": "false",
    }


def active_assignments(text: str) -> set[str]:
    """Return active key names without retaining assignment values."""
    assignments: set[str] = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match:
            assignments.add(match.group(1))
    return assignments


def _enabled_values(text: str) -> list[str]:
    values = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match and match.group(1) == "SONSTENG_PROD_RELEASE_ENABLED":
            values.append(match.group(2).strip())
    return values


def _active_values(text: str, key: str) -> list[str]:
    values = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match and match.group(1) == key:
            values.append(match.group(2).strip())
    return values


def _rewrite_assignment(text: str, key: str, value: str) -> str:
    rewritten = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        match = _ASSIGNMENT.match(content)
        if match and match.group(1) == key and not content.lstrip().startswith("#"):
            content = content[:match.start(2)] + value
        rewritten.append(content + ending)
    return "".join(rewritten)


def _migrate_retired_defaults(text: str) -> tuple[str, int]:
    provenance_key = "SONSTENG_PROD_PAGES_PROVENANCE_URL"
    provenance_values = _active_values(text, provenance_key)
    if len(provenance_values) > 1:
        raise MigrationError("production environment has duplicate Pages provenance assignments")
    if provenance_values != [_LEGACY_PAGES_PROVENANCE_URL]:
        return text, 0

    digest_key = "SONSTENG_PROD_EXPECTED_CONFIG_DIGEST"
    digest_values = _active_values(text, digest_key)
    if len(digest_values) > 1:
        raise MigrationError("production environment has duplicate config digest assignments")

    migrated = _rewrite_assignment(text, provenance_key, _CANONICAL_PAGES_PROVENANCE_URL)
    updated_count = 1
    if digest_values:
        migrated = _rewrite_assignment(migrated, digest_key, "")
        updated_count += 1
    return migrated, updated_count


def _read_safe_existing(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        observed = path.lstat()
    except FileNotFoundError as exc:
        raise MigrationError("production environment file is missing") from exc
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise MigrationError("production environment must be a non-symlink regular file")
    if stat.S_IMODE(observed.st_mode) != 0o600:
        raise MigrationError("production environment must have mode 0600")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MigrationError("production environment could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
            raise MigrationError("production environment changed during validation")
        chunks = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks), opened
    finally:
        os.close(descriptor)


def _append_block(original: bytes, missing: list[tuple[str, str]]) -> bytes:
    non_secret = [(key, value) for key, value in missing if key not in _CREDENTIAL_KEYS]
    credentials = [(key, value) for key, value in missing if key in _CREDENTIAL_KEYS]
    lines = [
        "# Added by the config-off environment migrator; existing lines above are unchanged.",
    ]
    if non_secret:
        lines.extend([
            "# Non-secret controls. Blank values require an explicit operator choice before activation.",
            *(f"{key}={value}" for key, value in non_secret),
        ])
    if credentials:
        lines.extend([
            "# Credentials. Keep blank pending separate least-privilege provisioning; never reuse DEV secrets.",
            *(f"{key}=" for key, _value in credentials),
        ])
    separator = b"" if original.endswith(b"\n") else b"\n"
    return original + separator + ("\n".join(lines) + "\n").encode("utf-8")


def _atomic_replace(path: Path, payload: bytes, original_stat: os.stat_result) -> None:
    descriptor = -1
    staged_name = ""
    try:
        descriptor, staged_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        os.fchmod(descriptor, 0o600)
        try:
            os.fchown(descriptor, original_stat.st_uid, original_stat.st_gid)
        except PermissionError as exc:
            raise MigrationError("production environment ownership could not be preserved") from exc
        with os.fdopen(descriptor, "wb") as staged:
            descriptor = -1
            staged.write(payload)
            staged.flush()
            os.fsync(staged.fileno())

        current = path.lstat()
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
        if any(getattr(current, field) != getattr(original_stat, field) for field in identity):
            raise MigrationError("production environment changed during migration")
        os.replace(staged_name, path)
        staged_name = ""
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except MigrationError:
        raise
    except OSError as exc:
        raise MigrationError("production environment migration failed safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if staged_name:
            try:
                os.unlink(staged_name)
            except FileNotFoundError:
                pass


def migrate_env_file(env_file: Path, *, daemon_root: Path, state_root: Path) -> MigrationResult:
    """Add missing settings and migrate retired defaults in a config-off file."""
    path = Path(env_file)
    original, original_stat = _read_safe_existing(path)
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError("production environment must be valid UTF-8") from exc
    assignments = active_assignments(text)
    enabled = _enabled_values(text)
    if enabled != ["false"]:
        raise MigrationError("production environment is not unambiguously config-off")

    text, updated_count = _migrate_retired_defaults(text)
    migrated = text.encode("utf-8")

    defaults = _defaults(Path(daemon_root), Path(state_root))
    missing = [(key, defaults[key]) for key in REQUIRED_DEFAULTS if key not in assignments]
    if not missing and not updated_count:
        return MigrationResult(added_count=0, updated_count=0)
    payload = _append_block(migrated, missing) if missing else migrated
    _atomic_replace(path, payload, original_stat)
    return MigrationResult(added_count=len(missing), updated_count=updated_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--daemon-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = migrate_env_file(
            args.env_file, daemon_root=args.daemon_root, state_root=args.state_root
        )
    except MigrationError:
        print("[prod-release] environment migration refused; config remains unchanged", file=os.sys.stderr)
        return 1
    if result.added_count or result.updated_count:
        print(
            "[prod-release] migrated config-off environment "
            f"({result.added_count} added, {result.updated_count} updated)"
        )
    else:
        print("[prod-release] config-off environment is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
