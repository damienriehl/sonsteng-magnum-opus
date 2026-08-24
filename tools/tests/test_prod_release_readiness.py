import importlib.util
import json
import pathlib
import stat
from types import SimpleNamespace

import pytest

TOOLS = pathlib.Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("prod_release_readiness",TOOLS / "prod_release_readiness.py")
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)


SHA = "a" * 40


class Observer:
    def __init__(self, *, releases=None, batches=None, invariants=None, fail=None):
        self.releases = releases or []
        self.batches = batches or []
        self.invariants = invariants or {"broken":0}
        self.fail = fail
        self.calls = []

    def preparation_context(self):
        self.calls.append("frontier")
        if self.fail: raise self.fail
        return {"base_sha":SHA,"batches":self.batches,
                "active_release":self.releases[0] if self.releases else None,
                "private_text":"never emit"}

    def audit(self):
        self.calls.append("audit")
        return {"schema_version":1,"counts":{"applied_suggestions":3},"invariants":self.invariants,
                "active_releases":[{"id":r["id"],"state":r["state"],
                  "base_sha":r.get("base_sha",SHA),"candidate_sha":r.get("candidate_sha",SHA)}
                  for r in self.releases]}

    def get_release(self, release_id):
        self.calls.append(("status",release_id))
        return next(r for r in self.releases if r["id"] == release_id)


OFF = {"available":True,"enabled":False,"active":False}


def test_prepared_release_is_visible_but_active_and_output_is_text_free():
    observer = Observer(releases=[{"id":"release-1","state":"prepared",
        "base_sha":SHA,"candidate_sha":"b"*40,"manifest_hash":"manifest-1",
        "authored_operation":"secret prose"}])
    result = readiness.inspect_readiness(observer,release_enabled=False,timer=OFF)
    assert result["ready"] is False
    assert result["reason"] == "active_release"
    assert observer.calls == ["frontier","audit",("status","release-1")]
    assert "secret prose" not in json.dumps(result)
    assert result["releases"][0]["manifest_hash"] == "manifest-1"


@pytest.mark.parametrize("over,reason",[
    ({"release_enabled":True,"timer":OFF},"config_on"),
    ({"release_enabled":False,"timer":{"available":False,"enabled":None,"active":None}},
     "timer_not_proved_off"),
])
def test_local_safety_state_fails_closed(over, reason):
    observer = Observer(releases=[{"id":"release-1","state":"prepared"}])
    result = readiness.inspect_readiness(observer,**over)
    assert result["ready"] is False
    assert result["reason"] == reason


def test_unprepared_active_invariant_and_transport_failures_are_bounded():
    assert readiness.inspect_readiness(Observer(),release_enabled=False,timer=OFF)["reason"] == "unprepared"
    active = Observer(releases=[{"id":"release-1","state":"authorized"}])
    assert readiness.inspect_readiness(active,release_enabled=False,timer=OFF)["reason"] == "active_release"
    broken = Observer(releases=[{"id":"release-1","state":"prepared"}],invariants={"broken":1})
    assert readiness.inspect_readiness(broken,release_enabled=False,timer=OFF)["reason"] == "invariant_failure"
    failed = Observer(fail=readiness.ObserverError("observer request unavailable"))
    result = readiness.inspect_readiness(failed,release_enabled=False,timer=OFF)
    assert result["reason"] == "observer request unavailable"
    assert "secret" not in json.dumps(result)


def test_queue_is_bounded_to_ids_hashes_and_counts():
    observer = Observer(batches=[{"batch_id":"batch-1","commit_sha":SHA,
        "generator_id":"generator-1","member_count":2,
        "original_text":"private"}])
    result = readiness.inspect_readiness(observer,release_enabled=False,timer=OFF)
    assert result["ready"] is True
    assert result["reason"] == "ready_to_prepare"
    assert result["queue"] == [{"batch_id":"batch-1","commit_sha":SHA,"member_count":2}]
    assert "private" not in json.dumps(result)


def test_observer_file_requires_owned_regular_0600_and_one_key(tmp_path):
    env_file = tmp_path / "observer.env"
    env_file.write_text("SONSTENG_PROD_OBSERVER_BEARER=sentinel\n",encoding="utf-8")
    env_file.chmod(0o600)
    assert readiness.load_observer_bearer(env_file) == "sentinel"
    env_file.chmod(0o644)
    with pytest.raises(readiness.ObserverError,match="0600"):
        readiness.load_observer_bearer(env_file)
    env_file.chmod(0o600)
    env_file.write_text("EDIT_SERVICE_TOKEN=wrong\n",encoding="utf-8")
    with pytest.raises(readiness.ObserverError,match="unsupported"):
        readiness.load_observer_bearer(env_file)


def test_release_enabled_reads_only_one_explicit_protected_flag(tmp_path):
    env_file = tmp_path / "prod.env"
    env_file.write_text("SONSTENG_PROD_RELEASE_ENABLED=false\n"
        "SONSTENG_PROD_RELEASE_BEARER=never-emit\n",encoding="utf-8")
    env_file.chmod(0o600)
    assert readiness.read_release_enabled(env_file) is False
    env_file.write_text("SONSTENG_PROD_RELEASE_ENABLED=true\n",encoding="utf-8")
    assert readiness.read_release_enabled(env_file) is True
    env_file.write_text("SONSTENG_PROD_RELEASE_ENABLED=false\n"
                        "SONSTENG_PROD_RELEASE_ENABLED=true\n",encoding="utf-8")
    with pytest.raises(readiness.ObserverError,match="ambiguous"):
        readiness.read_release_enabled(env_file)


def test_timer_reader_uses_status_only():
    calls = []
    def run(argv,**kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout="disabled\n" if "is-enabled" in argv else "inactive\n")
    assert readiness.timer_state(run) == {"available":True,"enabled":False,"active":False}
    assert all(argv[2] in {"is-enabled","is-active"} for argv in calls)
