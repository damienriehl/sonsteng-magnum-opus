"""Fail-closed orchestration coverage for the one-off Day Zero migration."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero_migration as migration


SHA_OLD = "a" * 40
SHA_NEW = "b" * 40
PRIOR = migration.ProductionPair(SHA_OLD, "pages-old", "worker-old")
NEW = migration.ProductionPair(SHA_NEW, "pages-new", "worker-new")
EXPECTED_WINDOW_ACTORS = {
    "canonical-writers",
    "canonical-merges",
    "apply-daemon",
    "production-release-daemon",
    "direct-deployments",
    "provider-deployment-actors",
}


class FakePhases:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []

    def run(self, phase, candidate_sha):
        self.calls.append((phase, candidate_sha))
        if phase == self.fail_at:
            raise RuntimeError("phase failed")


class FakeProduction:
    def __init__(self, *, captured=PRIOR, deployed=NEW, fail=None,
                 apply_state=migration.TimerState(enabled=True, active=True),
                 excluded_actors=None, canonical_sha=SHA_NEW,
                 fence_proof="proved", prior_proof="proved",
                 window_exit_failure=False):
        self.captured = captured
        self.deployed = deployed
        self.fail = fail
        self.states = {
            migration.APPLY_TIMER: apply_state,
            migration.RELEASE_TIMER: migration.TimerState(enabled=False, active=False),
        }
        self.live = captured
        self.calls = []
        self.production_calls = 0
        self.read_mismatch_remaining = 1 if fail == "read-live" else 0
        self.excluded_actors = set(
            migration.REQUIRED_CHANGE_WINDOW_ACTORS
            if excluded_actors is None else excluded_actors
        )
        self.window_fenced = False
        self.canonical_sha = canonical_sha
        self.editor_dev_sha = SHA_OLD
        self.fence_proof = fence_proof
        self.prior_proof = prior_proof
        self.window_exit_failure = window_exit_failure

    def assert_queue_empty(self):
        self.calls.append("queue-empty")
        if self.fail == "queue":
            raise RuntimeError("queue detail must be hidden")

    def timer_state(self, name):
        self.calls.append("timer-state:" + name)
        return self.states[name]

    def stop_timer(self, name):
        self.calls.append("stop:" + name)
        self.states[name] = migration.TimerState(enabled=False, active=False)

    def restore_timer(self, name, state):
        self.calls.append("restore:" + name)
        if self.fail == "restart":
            raise RuntimeError("restart detail must be hidden")
        self.states[name] = state

    def assert_no_activity(self):
        self.calls.append("no-activity")
        if self.fail == "no-activity":
            raise RuntimeError("activity detail")

    @contextlib.contextmanager
    def daemon_lock(self):
        self.calls.append("lock-enter")
        try:
            yield
        finally:
            self.calls.append("lock-exit")

    @contextlib.contextmanager
    def exclusive_change_window(self, required_actors):
        self.calls.append("window-enter")
        if set(required_actors) != self.excluded_actors:
            raise RuntimeError("unexcluded actor detail")
        try:
            yield
        finally:
            self.calls.append("window-exit-fenced" if self.window_fenced else "window-exit")
            if self.fail == "window-exit" or self.window_exit_failure:
                raise RuntimeError("window release detail")

    def assert_candidate_commit(self, candidate_sha, prior_sha):
        self.calls.append("candidate-commit")
        if self.fail == "candidate-tree":
            raise RuntimeError("dirty tree detail")
        if candidate_sha != self.canonical_sha or prior_sha != SHA_OLD:
            raise RuntimeError("divergent tree detail")

    def deploy_editor_dev(self, candidate_sha):
        self.calls.append("editor-dev:" + candidate_sha)
        if self.fail == "editor-dev" and candidate_sha == SHA_NEW:
            raise RuntimeError("editor deploy detail")
        if self.fail == "compensation-editor" and candidate_sha == SHA_OLD:
            raise RuntimeError("editor restore detail")
        self.editor_dev_sha = candidate_sha

    def restore_canonical_ref_exact(self, candidate_sha, prior_sha):
        self.calls.append("restore-canonical-exact")
        if self.fail == "compensation-canonical":
            raise RuntimeError("protected ref detail")
        if self.canonical_sha != candidate_sha:
            raise RuntimeError("unexpected canonical state")
        self.canonical_sha = prior_sha
        if self.fail == "compensation-canonical-readback":
            return candidate_sha
        return self.canonical_sha

    def prove_prior_state(self, prior_sha, prior_pair):
        self.calls.append("prove-prior-state")
        if self.prior_proof == "raise":
            raise RuntimeError("prior proof detail")
        if self.prior_proof == "unproved":
            return False
        if (self.live != prior_pair or self.canonical_sha != prior_sha or
                self.editor_dev_sha != prior_sha):
            raise RuntimeError("prior state mismatch detail")
        return True

    def prove_all_surfaces(self, expected_sha, expected_pair):
        self.calls.append("prove-all:" + expected_sha)
        if self.fail == "candidate-proof" and expected_sha == SHA_NEW:
            raise RuntimeError("candidate surface detail")
        if self.fail == "compensation-proof" and expected_sha == SHA_OLD:
            raise RuntimeError("surface detail")
        if self.live != expected_pair or self.editor_dev_sha != expected_sha:
            raise RuntimeError("surface mismatch detail")
        if expected_sha == SHA_OLD and self.canonical_sha != SHA_OLD:
            raise RuntimeError("canonical mismatch detail")

    def keep_change_window_fenced(self):
        self.calls.append("keep-fenced")
        if self.fence_proof == "raise":
            raise RuntimeError("fence detail")
        if self.fence_proof == "unproved":
            return False
        self.window_fenced = True
        return True

    def capture_live_pair(self):
        self.production_calls += 1
        self.calls.append("capture")
        if self.fail == "capture":
            raise RuntimeError("capture detail")
        return self.captured

    def deploy_candidate(self, candidate_sha):
        self.production_calls += 1
        self.calls.append("deploy")
        if self.fail == "deploy":
            raise RuntimeError("provider stdout secret")
        self.live = self.deployed
        return self.deployed

    def read_live_pair(self):
        self.production_calls += 1
        self.calls.append("read-live")
        if self.read_mismatch_remaining:
            self.read_mismatch_remaining -= 1
            return migration.ProductionPair(SHA_NEW, "wrong", "worker-new")
        return self.live

    def record_pair(self, pair, registry_path):
        self.production_calls += 1
        self.calls.append("record:" + pair.sha)
        if self.fail == "record":
            raise RuntimeError("registry path detail")

    def activate_pair(self, pair):
        self.production_calls += 1
        self.calls.append("activate:" + pair.sha)
        if self.fail == "restore" and pair == PRIOR:
            raise RuntimeError("provider stdout secret")
        if self.fail == "return" and pair == NEW:
            raise RuntimeError("provider stdout secret")
        self.live = pair


def request(tmp_path, **changes):
    values = dict(
        candidate_sha=SHA_NEW,
        prior_pair=PRIOR,
        recovery_registry=tmp_path / "known-good-pairs.json",
        john_notified=True,
        queue_empty_acknowledged=True,
        enabled=True,
        normal_release_config_off=True,
    )
    values.update(changes)
    return migration.MigrationRequest(**values)


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Migration Test")
    git(repo, "config", "user.email", "migration@example.test")
    (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "candidate.txt")
    git(repo, "commit", "-m", "candidate")
    return repo


def test_isolated_git_copy_accepts_only_the_exact_commit_object(tmp_path):
    repo = git_repo(tmp_path)
    commit_sha = git(repo, "rev-parse", "HEAD")
    (repo / "uncommitted.txt").write_text("must stay behind\n", encoding="utf-8")

    with migration.isolated_git_copy(repo, commit_sha) as checkout:
        assert (checkout / "candidate.txt").read_text(encoding="utf-8") == "candidate\n"
        assert not (checkout / "uncommitted.txt").exists()
        assert git(checkout, "rev-parse", "HEAD") == commit_sha
        assert git(checkout, "ls-files") == "candidate.txt"
        assert not (checkout / ".git" / "objects" / "info" / "alternates").exists()


def test_isolated_git_copy_fetches_candidate_retained_only_by_release_ref(tmp_path):
    repo = git_repo(tmp_path)
    candidate_branch = git(repo, "branch", "--show-current")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/sonsteng/releases/test-candidate", candidate_sha)
    git(repo, "checkout", "--orphan", "unrelated")
    (repo / "candidate.txt").write_text("unrelated\n", encoding="utf-8")
    git(repo, "add", "candidate.txt")
    git(repo, "commit", "-m", "unrelated")
    git(repo, "branch", "-D", candidate_branch)

    with migration.isolated_git_copy(repo, candidate_sha) as checkout:
        assert git(checkout, "rev-parse", "HEAD") == candidate_sha
        assert (checkout / "candidate.txt").read_text(encoding="utf-8") == "candidate\n"
        assert not (checkout / ".git" / "objects" / "info" / "alternates").exists()


@pytest.mark.parametrize("object_kind", ["tree", "annotated-tag"])
def test_isolated_git_copy_rejects_non_commit_object_ids(tmp_path, object_kind):
    repo = git_repo(tmp_path)
    if object_kind == "tree":
        candidate_sha = git(repo, "rev-parse", "HEAD^{tree}")
    else:
        git(repo, "tag", "-a", "candidate-tag", "-m", "candidate tag")
        candidate_sha = git(repo, "rev-parse", "candidate-tag")

    with pytest.raises(migration.MigrationError, match="not an exact commit object"):
        with migration.isolated_git_copy(repo, candidate_sha):
            pytest.fail("non-commit object unexpectedly produced a rehearsal checkout")


def test_rehearsal_runs_every_phase_in_an_isolated_copy_without_production_calls(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    phases = FakePhases()
    production = FakeProduction()
    copies = []

    @contextlib.contextmanager
    def isolated_copy(source, candidate_sha):
        copy = tmp_path / "isolated"
        copy.mkdir()
        copies.append((source, candidate_sha, copy))
        yield copy

    receipt = migration.rehearse(repo, SHA_NEW, phases, isolated_copy=isolated_copy)

    assert receipt["mode"] == "rehearsal"
    assert receipt["production_mutations"] == 0
    assert [name for name, _ in phases.calls] == list(migration.MATERIALIZATION_PHASES)
    assert all(candidate == SHA_NEW for _, candidate in phases.calls)
    assert copies == [(repo, SHA_NEW, tmp_path / "isolated")]
    assert production.production_calls == 0


@pytest.mark.parametrize("phase", migration.MATERIALIZATION_PHASES)
def test_rehearsal_aborts_at_each_failed_phase_without_continuing(tmp_path, phase):
    phases = FakePhases(fail_at=phase)

    @contextlib.contextmanager
    def isolated_copy(source, candidate_sha):
        yield tmp_path

    with pytest.raises(migration.MigrationError, match="rehearsal phase failed"):
        migration.rehearse(tmp_path, SHA_NEW, phases, isolated_copy=isolated_copy)
    names = [name for name, _ in phases.calls]
    assert names[-1] == phase
    assert names == list(migration.MATERIALIZATION_PHASES[: names.index(phase) + 1])


def test_materialized_candidate_verification_never_reruns_governed_write(tmp_path):
    phases = FakePhases()

    @contextlib.contextmanager
    def isolated_copy(source, candidate_sha):
        yield tmp_path

    receipt = migration.verify_materialized(
        tmp_path, SHA_NEW, phases, isolated_copy=isolated_copy,
    )

    names = [name for name, _ in phases.calls]
    assert names == list(migration.VERIFY_ONLY_PHASES)
    assert "governed-write" not in names
    assert receipt["mode"] == "verify-only"
    assert receipt["candidate_sha"] == SHA_NEW
    assert receipt["production_mutations"] == 0


def test_candidate_cleanliness_phase_requires_exact_head_and_no_changes(tmp_path):
    repo = git_repo(tmp_path)
    candidate_sha = git(repo, "rev-parse", "HEAD")
    runner = migration.LocalRehearsalPhases(repo)

    runner.run("candidate-commit", candidate_sha)
    (repo / "untracked.txt").write_text("not materialized\n", encoding="utf-8")

    with pytest.raises(migration.MigrationError, match="dirty or differs"):
        runner.run("final-tree-cleanliness", candidate_sha)


def test_strict_rehearsal_requires_date_and_identifier_evidence(tmp_path, monkeypatch):
    runner = migration.LocalRehearsalPhases(tmp_path)
    commands = []

    def command(argv):
        commands.append(argv)
        report = tmp_path / ".day-zero-migration-validation.json"
        report.write_text(json.dumps({
            "day_zero_offset_enforcement": True,
            "identifier_base_enforcement": True,
            "totals": {
                "checked_dates": 10,
                "offset_dates_checked": 8,
                "identifier_files_checked": 3,
                "identifier_base_values_checked": 4,
                "old_identifier_base_occurrences": 0,
            },
        }))

    monkeypatch.setattr(runner, "_command", command)
    runner.run("strict-day-zero-enforcement", SHA_NEW)

    assert "--enforce-day-zero-offsets" in commands[0]
    assert "--enforce-legal-practicum-identifiers" in commands[0]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"day_zero_offset_enforcement": False}, "did not execute"),
        ({"identifier_base_enforcement": False}, "did not execute"),
        ({"totals": {"checked_dates": 0}}, "did not execute"),
        ({"totals": {"offset_dates_checked": 0}}, "did not execute"),
        ({"totals": {"identifier_files_checked": 0}}, "did not execute"),
        ({"totals": {"identifier_base_values_checked": 0}}, "did not execute"),
        ({"totals": {"old_identifier_base_occurrences": 1}}, "did not execute"),
        ({"totals": {"checked_dates": "not-a-number"}}, "bounded evidence"),
    ],
)
def test_strict_rehearsal_rejects_invalid_evidence_and_removes_report(
        tmp_path, monkeypatch, change, message):
    runner = migration.LocalRehearsalPhases(tmp_path)
    report = tmp_path / ".day-zero-migration-validation.json"
    payload = {
        "day_zero_offset_enforcement": True,
        "identifier_base_enforcement": True,
        "totals": {
            "checked_dates": 10,
            "offset_dates_checked": 8,
            "identifier_files_checked": 3,
            "identifier_base_values_checked": 4,
            "old_identifier_base_occurrences": 0,
        },
    }
    if "totals" in change:
        payload["totals"].update(change["totals"])
    else:
        payload.update(change)

    def command(_argv):
        report.write_text(json.dumps(payload))

    monkeypatch.setattr(runner, "_command", command)
    with pytest.raises(migration.MigrationError, match=message):
        runner.run("strict-day-zero-enforcement", SHA_NEW)
    assert not report.exists()


def test_production_is_disabled_by_default_before_any_operator_call(tmp_path):
    production = FakeProduction()
    with pytest.raises(migration.MigrationError, match="disabled by default"):
        migration.execute(request(tmp_path, enabled=False), FakePhases(), production)
    assert production.calls == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"john_notified": False}, "John-notified"),
        ({"queue_empty_acknowledged": False}, "queue-empty"),
        ({"normal_release_config_off": False}, "config-off"),
        ({"candidate_sha": "main"}, "candidate SHA"),
        ({"prior_pair": migration.ProductionPair(SHA_OLD, "", "worker-old")}, "prior pair"),
    ],
)
def test_execute_requires_every_explicit_safety_input_before_operator_calls(tmp_path, change, message):
    production = FakeProduction()
    with pytest.raises(migration.MigrationError, match=message):
        migration.execute(request(tmp_path, **change), FakePhases(), production)
    assert production.calls == []


def test_execute_stops_timers_holds_lock_records_pair_and_proves_restore_and_return(tmp_path):
    production = FakeProduction()
    phases = FakePhases()

    receipt = migration.execute(request(tmp_path), phases, production)

    assert receipt == {
        "mode": "production",
        "candidate_sha": SHA_NEW,
        "prior_pair": PRIOR.redacted(),
        "new_pair": NEW.redacted(),
        "restoration_proved": True,
        "returned_to_candidate": True,
        "editor_dev_synced": True,
        "change_window_released": True,
        "apply_timer_restored": True,
    }
    assert [name for name, _ in phases.calls] == list(migration.VERIFY_ONLY_PHASES)
    assert production.live == NEW
    assert production.calls.index("window-enter") < production.calls.index("capture")
    assert production.calls.index("stop:" + migration.APPLY_TIMER) < production.calls.index("lock-enter")
    assert production.calls.index("lock-enter") < production.calls.index("deploy")
    assert "editor-dev:" + SHA_NEW in production.calls
    assert "prove-all:" + SHA_NEW in production.calls
    assert production.calls.index("activate:" + SHA_OLD) < production.calls.index("activate:" + SHA_NEW)
    assert production.calls.index("window-exit") < production.calls.index("restore:" + migration.APPLY_TIMER)


@pytest.mark.parametrize("phase", migration.VERIFY_ONLY_PHASES)
def test_each_migration_phase_failure_unwinds_lock_and_apply_timer(tmp_path, phase):
    production = FakeProduction()
    with pytest.raises(migration.MigrationError, match="migration phase failed"):
        migration.execute(request(tmp_path), FakePhases(fail_at=phase), production)
    assert production.calls[-4:] == ["window-exit", "lock-exit", "restore:" + migration.APPLY_TIMER,
                                    "timer-state:" + migration.APPLY_TIMER]
    assert "deploy" not in production.calls


def test_catchable_signal_during_a_phase_uses_the_same_unwind_path(tmp_path):
    production = FakeProduction()

    class InterruptedPhases(FakePhases):
        def run(self, phase, candidate_sha):
            self.calls.append((phase, candidate_sha))
            raise migration.MigrationInterrupted("SIGTERM")

    with pytest.raises(migration.MigrationInterrupted):
        migration.execute(request(tmp_path), InterruptedPhases(), production)
    assert production.calls[-4:] == ["window-exit", "lock-exit", "restore:" + migration.APPLY_TIMER,
                                    "timer-state:" + migration.APPLY_TIMER]
    assert "deploy" not in production.calls


def test_provider_failure_is_redacted_and_restores_prior_pair_before_unwind(tmp_path):
    production = FakeProduction(fail="record")
    with pytest.raises(migration.MigrationError) as failure:
        migration.execute(request(tmp_path), FakePhases(), production)
    assert "registry path detail" not in str(failure.value)
    assert production.live == PRIOR
    assert "activate:" + SHA_OLD in production.calls
    assert production.calls[-4:] == ["window-exit", "lock-exit", "restore:" + migration.APPLY_TIMER,
                                    "timer-state:" + migration.APPLY_TIMER]


def test_exact_prior_pair_mismatch_fences_when_complete_prior_state_is_unproved(tmp_path):
    production = FakeProduction(captured=migration.ProductionPair(SHA_OLD, "pages-other", "worker-old"))
    with pytest.raises(migration.MigrationFenced, match="prior state could not be proved"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert "window-enter" in production.calls
    assert production.calls.index("window-enter") < production.calls.index("capture")
    assert "prove-prior-state" in production.calls
    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls
    assert "deploy" not in production.calls


def test_exact_new_pair_readback_mismatch_restores_prior_pair(tmp_path):
    production = FakeProduction(fail="read-live")
    with pytest.raises(migration.MigrationError, match="candidate live pair mismatch"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert production.live == PRIOR
    assert production.calls.count("activate:" + SHA_OLD) == 1


def test_apply_timer_prior_disabled_state_is_preserved(tmp_path):
    production = FakeProduction(apply_state=migration.TimerState(enabled=False, active=False))
    migration.execute(request(tmp_path), FakePhases(), production)
    assert "restore:" + migration.APPLY_TIMER not in production.calls


@pytest.mark.parametrize("omitted_actor", sorted(EXPECTED_WINDOW_ACTORS))
def test_each_unexcluded_writer_aborts_before_prior_pair_capture(tmp_path, omitted_actor):
    actors = EXPECTED_WINDOW_ACTORS - {omitted_actor}
    production = FakeProduction(excluded_actors=actors)

    with pytest.raises(migration.MigrationFenced, match="persistent fence proved"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert "capture" not in production.calls
    assert "deploy" not in production.calls
    assert production.window_fenced is True
    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls
    assert production.states[migration.APPLY_TIMER] == migration.TimerState(False, False)


@pytest.mark.parametrize("failure", ["no-activity", "capture", "candidate-tree"])
def test_pre_candidate_failure_fences_when_complete_prior_state_is_unproved(tmp_path, failure):
    production = FakeProduction(fail=failure)

    with pytest.raises(migration.MigrationFenced, match="prior state could not be proved"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert "prove-prior-state" in production.calls
    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls
    assert "deploy" not in production.calls


def test_pre_candidate_failure_releases_only_after_complete_prior_state_proof(tmp_path):
    production = FakeProduction(fail="candidate-tree", canonical_sha=SHA_OLD)

    with pytest.raises(migration.MigrationError, match="exact clean committed migration"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert "prove-prior-state" in production.calls
    assert "keep-fenced" not in production.calls
    assert "restore:" + migration.APPLY_TIMER in production.calls


@pytest.mark.parametrize("prior_proof", ["raise", "unproved"])
def test_pre_candidate_requires_affirmative_prior_state_proof(tmp_path, prior_proof):
    production = FakeProduction(
        fail="no-activity", canonical_sha=SHA_OLD, prior_proof=prior_proof,
    )

    with pytest.raises(migration.MigrationFenced, match="prior state could not be proved"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls


def test_dirty_or_divergent_candidate_never_invokes_compensation_or_provider_deploy(tmp_path):
    production = FakeProduction(fail="candidate-tree")

    with pytest.raises(migration.MigrationFenced):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert "candidate-commit" in production.calls
    assert "deploy" not in production.calls
    assert "restore-canonical-exact" not in production.calls


def test_editor_dev_sync_failure_restores_provider_canonical_and_editor_surfaces(tmp_path):
    production = FakeProduction(fail="editor-dev")

    with pytest.raises(migration.MigrationError, match="synchronization failed"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert production.live == PRIOR
    assert production.canonical_sha == SHA_OLD
    assert production.editor_dev_sha == SHA_OLD
    assert "restore-canonical-exact" in production.calls
    assert "editor-dev:" + SHA_OLD in production.calls
    assert "prove-all:" + SHA_OLD in production.calls
    assert production.window_fenced is False
    assert "restore:" + migration.APPLY_TIMER in production.calls


@pytest.mark.parametrize(
    ("failure", "later_actions"),
    [
        ("restore", ["read-live", "restore-canonical-exact", "editor-dev:" + SHA_OLD,
                     "prove-all:" + SHA_OLD]),
        ("compensation-canonical", ["editor-dev:" + SHA_OLD, "prove-all:" + SHA_OLD]),
        ("compensation-canonical-readback",
         ["editor-dev:" + SHA_OLD, "prove-all:" + SHA_OLD]),
        ("compensation-editor", ["prove-all:" + SHA_OLD]),
        ("compensation-proof", []),
    ],
)
def test_failed_full_compensation_attempts_later_actions_and_keeps_fenced(
        tmp_path, failure, later_actions):
    production = FakeProduction(fail=failure)
    phases = FakePhases(fail_at=migration.VERIFY_ONLY_PHASES[-1])

    with pytest.raises(migration.MigrationFenced, match="persistent fence proved"):
        migration.execute(request(tmp_path), phases, production)

    for action in later_actions:
        assert action in production.calls
    assert production.window_fenced is True
    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls
    assert production.states[migration.APPLY_TIMER] == migration.TimerState(False, False)


def test_failed_window_close_compensates_under_lock_and_keeps_apply_timer_fenced(tmp_path):
    production = FakeProduction(fail="window-exit")

    with pytest.raises(migration.MigrationFenced, match="control boundary"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert production.live == PRIOR
    assert production.canonical_sha == SHA_OLD
    assert production.editor_dev_sha == SHA_OLD
    assert production.calls.index("window-exit") < production.calls.index("restore-canonical-exact")
    assert production.calls.index("restore-canonical-exact") < production.calls.index("lock-exit")
    assert production.window_fenced is True
    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls


def test_failed_window_close_error_precedes_earlier_body_error_without_double_compensation(tmp_path):
    production = FakeProduction(fail="record", window_exit_failure=True)

    with pytest.raises(migration.MigrationFenced, match="control boundary") as failure:
        migration.execute(request(tmp_path), FakePhases(), production)

    assert "registry" not in str(failure.value)
    assert production.calls.count("restore-canonical-exact") == 1
    assert production.calls.index("restore-canonical-exact") < production.calls.index("lock-exit")


@pytest.mark.parametrize("fence_proof", ["raise", "unproved"])
def test_unproved_persistent_fence_has_distinct_error_and_never_restores_timer(
        tmp_path, fence_proof):
    production = FakeProduction(
        excluded_actors=EXPECTED_WINDOW_ACTORS - {"canonical-merges"},
        fence_proof=fence_proof,
    )

    with pytest.raises(
            migration.MigrationFenced, match="persistent fencing could not be proved"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert production.window_fenced is False
    assert "keep-fenced" in production.calls
    assert "restore:" + migration.APPLY_TIMER not in production.calls


def test_final_candidate_surface_proof_failure_runs_complete_prior_compensation(tmp_path):
    production = FakeProduction(fail="candidate-proof")

    with pytest.raises(migration.MigrationError, match="every deployed surface"):
        migration.execute(request(tmp_path), FakePhases(), production)

    assert production.live == PRIOR
    assert production.canonical_sha == SHA_OLD
    assert production.editor_dev_sha == SHA_OLD
    assert "activate:" + SHA_OLD in production.calls
    assert "restore-canonical-exact" in production.calls
    assert "editor-dev:" + SHA_OLD in production.calls
    assert "prove-all:" + SHA_OLD in production.calls
    assert "restore:" + migration.APPLY_TIMER in production.calls


def test_release_timer_must_already_be_disabled_and_inactive(tmp_path):
    production = FakeProduction()
    production.states[migration.RELEASE_TIMER] = migration.TimerState(enabled=True, active=False)
    with pytest.raises(migration.MigrationError, match="release timer"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert "deploy" not in production.calls


def test_cli_execute_is_unwired_even_with_explicit_enablement(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SONSTENG_DAY_ZERO_MIGRATION_ENABLED", "true")
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_ENABLED", "false")
    code = migration.main([
        "--execute",
        "--candidate-sha", SHA_NEW,
        "--prior-sha", SHA_OLD,
        "--prior-pages-deployment-id", "pages-old",
        "--prior-worker-version-id", "worker-old",
        "--recovery-registry", str(tmp_path / "registry.json"),
        "--ack-john-notified",
        "--ack-queue-empty",
    ])
    assert code == migration.EX_CONFIG
    assert "no direct production adapter" in capsys.readouterr().err


def test_operator_plan_contains_exact_inputs_and_supervised_boundary(tmp_path):
    plan = migration.operator_plan(request(tmp_path))
    assert SHA_NEW in plan
    assert "pages-old" in plan
    assert "worker-old" in plan
    assert "Damien at the keyboard" in plan
    assert "SONSTENG_PROD_RELEASE_ENABLED=false" in plan
    assert "deploy/deploy-prod.sh" in plan
    assert "never" in plan.lower()
    assert "candidate SHA already exists" in plan
    assert "canonical, and clean commit" in plan
    assert "same exclusive change window remains held" in plan
    assert "governed write" not in plan
    assert "commit the complete" not in plan
    assert "Merge only" not in plan
