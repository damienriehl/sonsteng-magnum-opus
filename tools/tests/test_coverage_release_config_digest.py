"""Offline config-file to redaction-safe digest contracts."""
import hashlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
import print_prod_release_config_digest as digest


def expected(values):
    payload = {key: values.get(key, "") for key in digest.CONFIG_DIGEST_KEYS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_config_file_cli_filters_comments_unknowns_and_preserves_equals(tmp_path, capsys):
    config = tmp_path / "config.txt"
    config.write_text("\n# comment\nmalformed\nUNKNOWN=ignored\n"
                      "SONSTENG_PROD_PAGES_PROJECT=old\n"
                      "SONSTENG_PROD_PAGES_PROJECT=new\n"
                      "SONSTENG_PROD_LEDGER_URL=https://example.invalid/?a=b=c\n"
                      "SONSTENG_PROD_RELEASE_BEARER=fake-sensitive-marker\n")
    values = {"SONSTENG_PROD_PAGES_PROJECT": "new", "SONSTENG_PROD_LEDGER_URL": "https://example.invalid/?a=b=c"}
    assert digest.read_nonsecret_config(config) == values
    assert digest.main(["--env-file", str(config)]) == 0
    assert capsys.readouterr().out == expected(values) + "\n"


def test_empty_file_digest_ignores_ambient_environment(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.txt"
    config.write_text("")
    monkeypatch.setenv("SONSTENG_PROD_PAGES_PROJECT", "ambient-project")
    assert digest.main(["--env-file", str(config)]) == 0
    assert capsys.readouterr().out == expected({}) + "\n"


def test_secret_rotation_does_not_change_config_identity(tmp_path, capsys):
    config = tmp_path / "config.txt"
    results = []
    for value in ("fixture-one", "fixture-two"):
        config.write_text("SONSTENG_PROD_PAGES_PROJECT=project\nSONSTENG_PROD_RELEASE_BEARER=" + value)
        digest.main(["--env-file", str(config)])
        results.append(capsys.readouterr().out)
    assert results == [expected({"SONSTENG_PROD_PAGES_PROJECT": "project"}) + "\n"] * 2
    config.write_text("SONSTENG_PROD_PAGES_PROJECT=changed")
    digest.main(["--env-file", str(config)])
    assert capsys.readouterr().out != results[0]


@pytest.mark.parametrize("mode", ["missing", "directory", "invalid-encoding"])
def test_unreadable_file_never_prints_digest(tmp_path, capsys, mode):
    config = tmp_path / "config.txt"
    error = FileNotFoundError
    if mode == "directory":
        config.mkdir()
        error = IsADirectoryError
    elif mode == "invalid-encoding":
        config.write_bytes(b"\xff")
        error = UnicodeDecodeError
    with pytest.raises(error):
        digest.main(["--env-file", str(config)])
    assert capsys.readouterr().out == ""


def test_cli_requires_explicit_file(capsys):
    with pytest.raises(SystemExit) as caught:
        digest.main([])
    assert caught.value.code == 2
    assert "--env-file" in capsys.readouterr().err
