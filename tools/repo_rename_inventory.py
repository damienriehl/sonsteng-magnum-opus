#!/usr/bin/env python3
"""Create a deterministic, read-only inventory for a future repository rename."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


HISTORICAL_ROOTS = (
    "docs/plans/",
    "docs/decisions/",
    "docs/handoffs/",
    "docs/evidence/",
)
TRANSITION_STEPS = (
    "confirm-quiet-window",
    "confirm-no-active-release",
    "rename-external-repository",
    "patch-active-references",
    "repair-remotes",
    "repair-worktrees",
    "repair-systemd-units",
    "verify-daemons",
    "verify-clone-and-web-redirects",
    "verify-hosted-actions-consumers",
)
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SAFE_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?$")


class InventoryError(ValueError):
    """The repository inventory cannot be classified safely."""


def _validate_parameters(owner: str, current: str, target: str) -> None:
    if not SAFE_OWNER_RE.fullmatch(owner):
        raise InventoryError("owner is not a bounded GitHub owner")
    if not SAFE_NAME_RE.fullmatch(current) or not SAFE_NAME_RE.fullmatch(target):
        raise InventoryError("repository names must be bounded GitHub names")
    if current.casefold() == target.casefold():
        raise InventoryError("current and target repository names must differ")


def _tracked_paths(repo: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise InventoryError("tracked-file inventory is unavailable")
    try:
        paths = completed.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise InventoryError("tracked paths are not UTF-8") from exc
    return sorted(path for path in paths if path)


def _historical(path: str) -> bool:
    return path.startswith(HISTORICAL_ROOTS)


def classify_reference(path: str, line: str, *, owner: str, current: str) -> str | None:
    """Classify one textual current-name reference without returning its content."""
    if current not in line:
        return None
    if _historical(path):
        return "historical_evidence_preserve"
    hosted_action = re.search(
        rf"\buses\s*:\s*{re.escape(owner)}/{re.escape(current)}(?:/[^@\s]+)?@[^\s#]+",
        line,
        re.IGNORECASE,
    )
    if hosted_action:
        return "hosted_actions_consumer_patch"
    if path.startswith("tools/tests/") or path.startswith("app/worker/test/"):
        return "active_contract_test_patch"
    if path.startswith("tools/install-") or path.endswith(".service"):
        return "local_installer_template_patch"
    github_url = f"github.com/{owner}/{current}".casefold()
    if github_url in line.casefold():
        if path.startswith("tools/") and ("build" in path or "todo" in path):
            return "generated_url_patch"
        return "clone_or_web_url_patch"
    if path in {"README.md", "README.txt"} or path.startswith("docs/"):
        return "active_operator_documentation_patch"
    if path.startswith(("tools/", "app/", "deploy/", ".github/")):
        return "active_operational_reference_patch"
    return None


def _read_reference_rows(
    repo: Path,
    paths: Iterable[str],
    *,
    owner: str,
    current: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    for relative in sorted(set(paths)):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise InventoryError("tracked path escapes repository")
        candidate = repo / path
        try:
            data = (
                candidate.readlink().as_posix().encode("utf-8")
                if candidate.is_symlink()
                else candidate.read_bytes()
            )
            if b"\0" in data:
                continue
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if current not in line:
                continue
            category = classify_reference(relative, line, owner=owner, current=current)
            row = {"path": relative, "line": line_number}
            if category is None:
                unclassified.append(row)
            else:
                rows.append({**row, "classification": category})
    return rows, unclassified


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _runtime_state(repo: Path, current: str) -> dict[str, Any]:
    remote_result = subprocess.run(
        ["git", "-C", str(repo), "remote", "-v"], check=False, capture_output=True, text=True
    )
    worktree_result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if remote_result.returncode != 0 or worktree_result.returncode != 0:
        raise InventoryError("read-only Git runtime inventory is unavailable")
    remotes = sorted({
        line.split()[0]
        for line in remote_result.stdout.splitlines()
        if len(line.split()) >= 2 and current in line.split()[1]
    })
    worktrees = sorted(
        _digest(line.removeprefix("worktree "))
        for line in worktree_result.stdout.splitlines()
        if line.startswith("worktree ")
    )
    return {
        "remote_names_requiring_review": remotes,
        "worktree_path_digests_requiring_review": worktrees,
    }


def build_inventory(
    repo: Path,
    *,
    owner: str,
    current: str,
    target: str,
    tracked_paths: Iterable[str] | None = None,
    runtime_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_parameters(owner, current, target)
    root = repo.resolve()
    paths = list(tracked_paths) if tracked_paths is not None else _tracked_paths(root)
    references, unclassified = _read_reference_rows(
        root, paths, owner=owner, current=current
    )
    if unclassified:
        locations = ", ".join(f"{row['path']}:{row['line']}" for row in unclassified)
        raise InventoryError(f"unclassified active repository-name references: {locations}")
    runtime = runtime_state if runtime_state is not None else _runtime_state(root, current)
    allowed_runtime = {"remote_names_requiring_review", "worktree_path_digests_requiring_review"}
    if not isinstance(runtime, dict) or set(runtime) != allowed_runtime:
        raise InventoryError("runtime inventory has an unexpected shape")
    return {
        "schema_version": 1,
        "repository": {"owner": owner, "current": current, "target": target},
        "activation_status": "prepared_not_activated",
        "references": sorted(
            references,
            key=lambda row: (row["path"], row["line"], row["classification"]),
        ),
        "runtime": {
            "remote_names_requiring_review": sorted(set(runtime["remote_names_requiring_review"])),
            "worktree_path_digests_requiring_review": sorted(
                set(runtime["worktree_path_digests_requiring_review"])
            ),
        },
        "transition_steps": list(TRANSITION_STEPS),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--owner", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--target", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_inventory(
            args.repo, owner=args.owner, current=args.current, target=args.target
        )
    except InventoryError as exc:
        print(f"rename inventory rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
