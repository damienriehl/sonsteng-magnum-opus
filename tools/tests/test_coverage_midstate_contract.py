"""Published caption gate through real repository fixtures and CLI diagnostics."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import midstate_contract as contract


@pytest.fixture
def repository(tmp_path):
    for relative in (*contract.CANONICAL_DOCS, contract.CONTRACT_PATH):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(contract.EXPECTED_CONTRACT) if relative == contract.CONTRACT_PATH else 'Midstate and Rogers', encoding='utf-8')
    return tmp_path


def test_cli_scans_nested_uppercase_sources_and_ignores_nontext(repository, monkeypatch, capsys):
    folder = repository / 'data/midstate/nested'
    folder.mkdir(parents=True)
    (folder / 'lesson.JSON').write_text('{"title":"Rogers v. Midstate"}')
    (folder / 'photo.bin').write_bytes(b'\xffMidstate v. Rogers')
    monkeypatch.setattr(sys, 'argv', ['midstate', '--root', str(repository)])
    assert contract.main() == 0
    assert 'PASS' in capsys.readouterr().out
    (folder / 'lesson.JSON').write_text('{"title":"Midstate University (Employer) versus Pat Rogers"}')
    with pytest.raises(SystemExit) as result:
        contract.main()
    assert result.value.code == 1
    diagnostic = capsys.readouterr()
    assert not diagnostic.out
    assert 'lesson.JSON' in diagnostic.err and 'forbidden arbitration caption' in diagnostic.err


@pytest.mark.parametrize('failure', ['missing', 'directory', 'invalid-json'])
def test_unreadable_manifest_has_contract_diagnostic(repository, failure):
    target = repository / contract.CONTRACT_PATH
    target.unlink()
    if failure == 'directory':
        target.mkdir()
    elif failure == 'invalid-json':
        target.write_text('{broken')
    with pytest.raises(contract.ContractViolation, match='cannot read docs/contracts'):
        contract.validate_repository(repository)


@pytest.mark.parametrize('relative', contract.CANONICAL_DOCS)
def test_missing_required_document_is_not_silently_skipped(repository, relative):
    (repository / relative).unlink()
    with pytest.raises(contract.ContractViolation, match='cannot read required source') as result:
        contract.validate_repository(repository)
    assert str(relative) in str(result.value)


@pytest.mark.parametrize('value', [None, [], {}, {'matter': 'Midstate/Rogers'}])
def test_wrong_manifest_shapes_rejected(value):
    with pytest.raises(contract.ContractViolation, match='exact arbitration and court'):
        contract.validate_contract(value)
