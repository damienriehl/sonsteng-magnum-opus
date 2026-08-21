"""Fail-closed orchestration coverage for the one-off Day Zero migration."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero_migration as migration


SHA_OLD = "a" * 40
SHA_NEW = "b" * 40
PRIOR = migration.ProductionPair(SHA_OLD, "pages-old", "worker-old")
NEW = migration.ProductionPair(SHA_NEW, "pages-new", "worker-new")


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
                 apply_state=migration.TimerState(enabled=True, active=True)):
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

    @contextlib.contextmanager
    def daemon_lock(self):
        self.calls.append("lock-enter")
        try:
            yield
        finally:
            self.calls.append("lock-exit")

    def capture_live_pair(self):
        self.production_calls += 1
        self.calls.append("capture")
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
    assert [name for name, _ in phases.calls] == list(migration.MIGRATION_PHASES)
    assert all(candidate == SHA_NEW for _, candidate in phases.calls)
    assert copies == [(repo, SHA_NEW, tmp_path / "isolated")]
    assert production.production_calls == 0


@pytest.mark.parametrize("phase", migration.MIGRATION_PHASES)
def test_rehearsal_aborts_at_each_failed_phase_without_continuing(tmp_path, phase):
    phases = FakePhases(fail_at=phase)

    @contextlib.contextmanager
    def isolated_copy(source, candidate_sha):
        yield tmp_path

    with pytest.raises(migration.MigrationError, match="rehearsal phase failed"):
        migration.rehearse(tmp_path, SHA_NEW, phases, isolated_copy=isolated_copy)
    names = [name for name, _ in phases.calls]
    assert names[-1] == phase
    assert names == list(migration.MIGRATION_PHASES[: names.index(phase) + 1])


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
        "apply_timer_restored": True,
    }
    assert [name for name, _ in phases.calls] == list(migration.MIGRATION_PHASES)
    assert production.live == NEW
    assert production.calls.index("stop:" + migration.APPLY_TIMER) < production.calls.index("lock-enter")
    assert production.calls.index("lock-enter") < production.calls.index("deploy")
    assert production.calls.index("activate:" + SHA_OLD) < production.calls.index("activate:" + SHA_NEW)
    assert production.calls.index("lock-exit") < production.calls.index("restore:" + migration.APPLY_TIMER)


@pytest.mark.parametrize("phase", migration.MIGRATION_PHASES)
def test_each_migration_phase_failure_unwinds_lock_and_apply_timer(tmp_path, phase):
    production = FakeProduction()
    with pytest.raises(migration.MigrationError, match="migration phase failed"):
        migration.execute(request(tmp_path), FakePhases(fail_at=phase), production)
    assert production.calls[-3:] == ["lock-exit", "restore:" + migration.APPLY_TIMER,
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
    assert production.calls[-3:] == ["lock-exit", "restore:" + migration.APPLY_TIMER,
                                    "timer-state:" + migration.APPLY_TIMER]
    assert "deploy" not in production.calls


def test_provider_failure_is_redacted_and_restores_prior_pair_before_unwind(tmp_path):
    production = FakeProduction(fail="record")
    with pytest.raises(migration.MigrationError) as failure:
        migration.execute(request(tmp_path), FakePhases(), production)
    assert "registry path detail" not in str(failure.value)
    assert production.live == PRIOR
    assert "activate:" + SHA_OLD in production.calls
    assert production.calls[-3:] == ["lock-exit", "restore:" + migration.APPLY_TIMER,
                                    "timer-state:" + migration.APPLY_TIMER]


def test_exact_prior_pair_mismatch_aborts_before_freeze(tmp_path):
    production = FakeProduction(captured=migration.ProductionPair(SHA_OLD, "pages-other", "worker-old"))
    with pytest.raises(migration.MigrationError, match="prior live pair mismatch"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert "stop:" + migration.APPLY_TIMER not in production.calls
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
