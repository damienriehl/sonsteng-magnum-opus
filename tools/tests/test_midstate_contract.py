"""Regression coverage for John Sonsteng's Midstate/Rogers naming rule."""

from pathlib import Path
import json
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from midstate_contract import (  # noqa: E402
    ContractViolation,
    EXPECTED_CONTRACT,
    validate_contract,
    validate_repository,
)


@pytest.mark.parametrize(
    ("posture", "field", "wrong_value"),
    [
        ("arbitration", "title", "Midstate v. Rogers"),
        ("arbitration", "remedy", "money damages"),
        ("court", "title", "Midstate and Rogers"),
        ("court", "remedy", "reinstatement"),
    ],
)
def test_wrong_structured_caption_or_remedy_fails_closed(posture, field, wrong_value):
    contract = json.loads(json.dumps(EXPECTED_CONTRACT))
    contract["postures"][posture][field] = wrong_value
    with pytest.raises(ContractViolation, match="exact arbitration and court"):
        validate_contract(contract)


def test_current_canonical_sources_satisfy_the_repository_gate():
    validate_repository(ROOT)


def test_repository_gate_fails_on_caption_drift(tmp_path):
    for relative_path in (
        "docs/master-outline.md",
        "docs/decisions/2026-07-18-midstate-deferred.md",
    ):
        source = tmp_path / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("Midstate and Rogers\n", encoding="utf-8")
    manifest = tmp_path / "docs/contracts/midstate-contract.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(EXPECTED_CONTRACT), encoding="utf-8")
    public = tmp_path / "site/platform/matters/m99-midstate/index.html"
    public.parent.mkdir(parents=True)
    public.write_text("<h1>Midstate and Rogers</h1>", encoding="utf-8")

    validate_repository(tmp_path)
    public.write_text("<h1>Midstate v. Rogers</h1>", encoding="utf-8")

    with pytest.raises(ContractViolation, match="forbidden arbitration caption"):
        validate_repository(tmp_path)


def test_preflight_runs_the_repository_contract_gate():
    preflight = (ROOT / "tools" / "preflight.sh").read_text(encoding="utf-8")
    assert 'run "Midstate naming/remedy contract"' in preflight
    assert "python3 tools/midstate_contract.py" in preflight
    assert preflight.index("python3 tools/build_site.py --check") < preflight.index(
        "python3 tools/midstate_contract.py"
    )
