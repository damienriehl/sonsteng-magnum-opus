"""Exercise local rehearsal subprocesses and Git proofs without a deployment."""

import json
import os
import sys

import pytest

from test_day_zero_migration import git, git_repo, materialized_git_repo
import day_zero_migration as migration


def test_rehearsal_command_sanitizes_child_environment_and_preserves_parent(tmp_path, monkeypatch):
    sensitive = ["COVERAGE_TOKEN", "coverage_secret", "COVERAGE_PASSWORD",
                 "COVERAGE_CREDENTIAL", "COVERAGE_API_KEY", "COVERAGE_BEARER"]
    for name in sensitive:
        monkeypatch.setenv(name, "synthetic-test-value")
    forced = {"HEADLESS": "1", "EDITOR_HEADLESS": "1",
              "SONSTENG_PROD_RELEASE_ENABLED": "false",
              "SONSTENG_DAY_ZERO_MIGRATION_ENABLED": "false"}
    for name in forced:
        monkeypatch.setenv(name, "parent-value")
    monkeypatch.setenv("COVERAGE_ORDINARY", "keep-this")
    names = sensitive + list(forced) + ["COVERAGE_ORDINARY"]
    script = (
        "import json, os, pathlib; "
        f"names = {names!r}; "
        "pathlib.Path('child-result.json').write_text(json.dumps("
        "{'cwd': os.getcwd(), 'values': {n: os.environ.get(n) for n in names}}))"
    )
    migration.LocalRehearsalPhases(tmp_path)._command([sys.executable, "-c", script])
    result = json.loads((tmp_path / "child-result.json").read_text())
    assert result["cwd"] == str(tmp_path)
    assert all(result["values"][name] is None for name in sensitive)
    assert {name: result["values"][name] for name in forced} == forced
    assert result["values"]["COVERAGE_ORDINARY"] == "keep-this"
    assert all(os.environ[name] == "parent-value" for name in forced)
    assert all(os.environ[name] == "synthetic-test-value" for name in sensitive)


@pytest.mark.parametrize("mode", ["nonzero", "missing-executable", "timeout", "missing-cwd"])
def test_command_failures_are_bounded_and_do_not_expose_child_arguments(tmp_path, mode):
    runner = migration.LocalRehearsalPhases(tmp_path, timeout=0.05 if mode == "timeout" else 5)
    command = [sys.executable, "-c", "raise SystemExit(7)", "synthetic-private-argument"]
    if mode == "timeout":
        command = [sys.executable, "-c", "import time; time.sleep(20)"]
    elif mode == "missing-executable":
        command = [str(tmp_path / "missing-child")]
    elif mode == "missing-cwd":
        runner.checkout = tmp_path / "missing-directory"
    with pytest.raises(migration.MigrationError, match="^bounded rehearsal command failed$"):
        runner._command(command)


def test_governed_write_runs_real_fixture_child_with_expected_repo_and_write_flag(tmp_path, capsys):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "day_zero.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path('write-receipt.json').write_text(json.dumps({"
        "'argv': sys.argv[1:], 'enabled': os.environ['SONSTENG_DAY_ZERO_MIGRATION_ENABLED']}))\n"
    )
    migration.LocalRehearsalPhases(tmp_path).run("governed-write", "a" * 40)
    assert json.loads((tmp_path / "write-receipt.json").read_text()) == {
        "argv": ["--repo", str(tmp_path), "--write"], "enabled": "false",
    }
    assert "rehearsal-phase:start:governed-write" in capsys.readouterr().err


def test_real_failed_child_stops_phase_chain_before_later_write(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/day_zero.py").write_text("raise SystemExit(2)\n")
    (tmp_path / "tools/build_site.py").write_text(
        "from pathlib import Path\nPath('must-not-run').write_text('wrong')\n"
    )
    runner = migration.LocalRehearsalPhases(tmp_path)
    with pytest.raises(migration.MigrationError, match="^rehearsal phase failed: governed-write$"):
        migration._run_phases(runner, "a" * 40, context="rehearsal",
                              phase_names=("governed-write", "generated-build"))
    assert not (tmp_path / "must-not-run").exists()


def test_exact_tree_proof_reports_non_repository_without_git_stderr_leak(tmp_path):
    with pytest.raises(migration.MigrationError, match="^could not prove the exact committed candidate tree$"):
        migration.LocalRehearsalPhases(tmp_path)._assert_exact_clean_tree("a" * 40)


@pytest.mark.parametrize("missing", ["committed-stamp", "regenerated-stamp"])
def test_stamp_comparison_requires_both_committed_and_generated_inputs(tmp_path, missing):
    if missing == "committed-stamp":
        repo = git_repo(tmp_path)
        sha = git(repo, "rev-parse", "HEAD")
    else:
        repo, _, sha = materialized_git_repo(tmp_path)
        (repo / migration.BUILD_STAMP_RELATIVE_PATH).unlink()
    with pytest.raises(migration.MigrationError, match="^could not compare the committed generated build stamp$"):
        migration.LocalRehearsalPhases(repo)._assert_generated_artifact_cleanliness(sha)


@pytest.mark.parametrize("malformed", ["[]", "null", '"not-an-object"'])
def test_non_object_stamp_is_rejected_and_committed_bytes_restored(tmp_path, malformed):
    repo, _, sha = materialized_git_repo(tmp_path)
    stamp = repo / migration.BUILD_STAMP_RELATIVE_PATH
    committed = stamp.read_bytes()
    stamp.write_text(malformed)
    with pytest.raises(migration.MigrationError, match="^generated build stamp was invalid$"):
        migration.LocalRehearsalPhases(repo)._assert_generated_artifact_cleanliness(sha)
    assert stamp.read_bytes() == committed
    assert git(repo, "status", "--porcelain") == ""


def test_head_resolution_reads_real_candidate_commit(tmp_path):
    repo = git_repo(tmp_path)
    assert migration._head_sha(repo) == git(repo, "rev-parse", "HEAD")


def test_cli_missing_repository_returns_bounded_error_without_rehearsal(tmp_path, capsys):
    assert migration.main(["--repo", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "error: could not resolve the rehearsal candidate SHA\n"


def test_operator_plan_rejects_abbreviated_commit_before_isolated_copy(tmp_path):
    repo = git_repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD")
    with pytest.raises(migration.MigrationError, match="^operator-plan candidate commit does not exist$"):
        migration.validate_operator_plan_candidate(repo, sha[:12], sha)


@pytest.mark.parametrize("evidence", ["valid", "missing-checks", "malformed-json"])
def test_strict_gate_runs_child_validates_evidence_and_always_removes_report(tmp_path, evidence):
    payload = {
        "day_zero_offset_enforcement": True,
        "identifier_base_enforcement": True,
        "totals": {"checked_dates": 1, "offset_dates_checked": 1,
                   "identifier_files_checked": 1, "identifier_base_values_checked": 1,
                   "old_identifier_base_occurrences": 0},
    }
    if evidence == "missing-checks":
        payload["totals"]["checked_dates"] = 0
    content = "not json" if evidence == "malformed-json" else json.dumps(payload)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/validate_spine.py").write_text(
        "import pathlib, sys\n"
        "assert '--strict' in sys.argv\n"
        "assert '--enforce-day-zero-offsets' in sys.argv\n"
        "assert '--enforce-legal-practicum-identifiers' in sys.argv\n"
        f"pathlib.Path(sys.argv[sys.argv.index('--json') + 1]).write_text({content!r})\n"
    )
    runner = migration.LocalRehearsalPhases(tmp_path)
    if evidence == "valid":
        runner.run("strict-day-zero-enforcement", "a" * 40)
    else:
        message = ("did not execute date and identifier checks" if evidence == "missing-checks"
                   else "did not emit bounded evidence")
        with pytest.raises(migration.MigrationError, match=message):
            runner.run("strict-day-zero-enforcement", "a" * 40)
    assert not (tmp_path / ".day-zero-migration-validation.json").exists()
