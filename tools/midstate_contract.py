"""Fail-closed caption and remedy contract for the Midstate/Rogers postures.

The source materials describe one matter through multiple exercises. Arbitration
does not use a versus caption; the court conversion reverses the parties and
changes the available remedy. The structured manifest is the ingestion/build
boundary, while the repository scan catches legacy captions in published content.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


class ContractViolation(ValueError):
    """Raised when Midstate material would publish the wrong posture contract."""


EXPECTED_CONTRACT = {
    "matter": "Midstate/Rogers",
    "postures": {
    "arbitration": {
        "title": "Midstate and Rogers",
            "remedy": "reinstatement",
    },
    "court": {
        "title": "Rogers v. Midstate",
            "remedy": "money damages",
        },
    },
}

CONTRACT_PATH = Path("docs/contracts/midstate-contract.json")
CANONICAL_DOCS = (
    Path("docs/master-outline.md"),
    Path("docs/decisions/2026-07-18-midstate-deferred.md"),
)
TEXT_SUFFIXES = {".html", ".json", ".md", ".txt"}
LEGACY_ARBITRATION_CAPTION = re.compile(
    r"Midstate(?:\s+University)?(?:\s+\(Employer\))?\s+"
    r"(?:v\.?|versus)\s+(?:Pat\s+)?Rogers",
    re.IGNORECASE,
)


def validate_contract(contract: object) -> None:
    """Require the exact structured title/remedy mapping John approved."""

    if contract != EXPECTED_CONTRACT:
        raise ContractViolation(
            f"{CONTRACT_PATH} must preserve the exact arbitration and court "
            "caption/remedy mapping"
        )


def _published_sources(root: Path):
    yield from CANONICAL_DOCS
    for directory in (Path("site"), Path("data/midstate")):
        base = root / directory
        if base.is_dir():
            yield from (
                path.relative_to(root)
                for path in base.rglob("*")
                if path.is_file() and path.suffix.casefold() in TEXT_SUFFIXES
            )


def validate_repository(root: Path) -> None:
    """Validate structured posture rules and reject legacy public captions."""

    manifest = root / CONTRACT_PATH
    try:
        validate_contract(json.loads(manifest.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractViolation(f"cannot read {CONTRACT_PATH}: {error}") from error

    for relative_path in _published_sources(root):
        source = root / relative_path
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as error:
            raise ContractViolation(f"cannot read required source {relative_path}: {error}") from error
        match = LEGACY_ARBITRATION_CAPTION.search(text)
        if match:
            raise ContractViolation(
                f"{relative_path} retains forbidden arbitration caption {match.group(0)!r}; "
                "use 'Midstate and Rogers'"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        validate_repository(args.root)
    except ContractViolation as error:
        parser.exit(1, f"MIDSTATE CONTRACT: FAIL — {error}\n")
    print("MIDSTATE CONTRACT: PASS — structured postures and published captions agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
