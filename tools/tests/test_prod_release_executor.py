import pathlib
import sys
import hashlib
import json

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from prod_release_executor import (  # noqa: E402
    CompatibilityGate,
    CandidateValidator,
    FrozenRelease,
    ProductionExecutor,
    ReleaseError,
    LedgerHTTP,
    RecordedPairRestorer,
)


class Ledger:
    def __init__(self, release=None):
        self.release = release
        self.events = []

    def claim_authorized(self):
        return self.release

    def transition(self, release_id, state, detail):
        self.events.append((release_id, state, detail))


class Target:
    def __init__(self, name, fail=False, observed=None):
        self.name, self.fail, self.observed = name, fail, observed
        self.deployed = []

    def deploy(self, manifest):
        if self.fail:
            raise RuntimeError("provider failure")
        self.deployed.append(manifest.candidate_sha)
        return {"provider_id": f"{self.name}-1"}

    def provenance(self):
        return self.observed

    def restore(self, sha):
        if self.fail:
            raise RuntimeError("restore failure")
        self.observed = sha


def release(**overrides):
    value = dict(id="rel-1", state="authorized", base_sha="a" * 40,
                 candidate_sha="b" * 40, manifest_hash="manifest",
                 membership_hash="members", suggestion_ids=("s1", "s2"),
                 batch_commits=("c" * 40, "b" * 40), fencing_token="fence-1")
    value.update(overrides)
    return FrozenRelease(**value)


def test_no_authorization_is_a_noop():
    ledger = Ledger()
    pages, worker = Target("pages"), Target("worker")
    assert ProductionExecutor(ledger, pages, worker).run_once() is None
    assert not pages.deployed and not worker.deployed and not ledger.events


def test_exact_membership_and_idempotent_completion():
    item = release()
    ledger = Ledger(item)
    pages = Target("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    executor = ProductionExecutor(ledger, pages, worker)
    assert executor.run_once()["state"] == "complete"
    assert pages.deployed == [item.candidate_sha]
    assert worker.deployed == [item.candidate_sha]
    assert ledger.events[-1][1] == "complete"
    ledger.release = release(state="complete")
    assert executor.run_once() is None


def test_partial_failure_fences_without_deploying_later_release():
    item = release()
    ledger = Ledger(item)
    pages, worker = Target("pages"), Target("worker", fail=True)
    with pytest.raises(ReleaseError, match="fenced"):
        ProductionExecutor(ledger, pages, worker).run_once()
    assert pages.deployed == [item.candidate_sha]
    assert ledger.events[-1][1] == "failed_fenced"


def test_resume_does_not_redeploy_target_with_recorded_receipt():
    item = release(state="pages_deployed", completed_phases=("executing", "pages_deployed"))
    ledger = Ledger(item)
    pages = Target("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    assert ProductionExecutor(ledger, pages, worker).run_once()["state"] == "complete"
    assert pages.deployed == []
    assert worker.deployed == [item.candidate_sha]


def test_live_provenance_mismatch_fences():
    item = release()
    ledger = Ledger(item)
    pages = Target("pages", observed=item.candidate_sha)
    worker = Target("worker", observed="wrong")
    with pytest.raises(ReleaseError, match="provenance"):
        ProductionExecutor(ledger, pages, worker).run_once()
    assert ledger.events[-1][1] == "failed_fenced"


def test_manifest_rejects_frontier_gaps_and_changed_membership():
    with pytest.raises(ValueError, match="candidate frontier"):
        release(batch_commits=("c" * 40, "d" * 40))
    with pytest.raises(ValueError, match="membership"):
        release(suggestion_ids=("s1", "s1"))


def test_compatibility_gate_selects_only_proven_transient_order():
    assert CompatibilityGate(True, False).deployment_order() == ("pages", "worker")
    assert CompatibilityGate(False, True).deployment_order() == ("worker", "pages")
    with pytest.raises(ReleaseError, match="compatible"):
        CompatibilityGate(False, False).deployment_order()


def test_candidate_validator_binds_git_tree_ancestry_frontier_and_membership():
    class Git:
        def is_ancestor(self, base, candidate): return True
        def tree(self, candidate): return "tree-1"
    manifest = {"base_sha":"a" * 40,"candidate_sha":"b" * 40,
                "candidate_tree":"tree-1","suggestion_ids":["s1","s2"],
                "batch_commits":["c" * 40,"b" * 40]}
    digest = hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    item = release(manifest_hash=digest)
    CandidateValidator(Git(), manifest)(item)
    with pytest.raises(ReleaseError, match="binding"):
        CandidateValidator(Git(), {**manifest,"suggestion_ids":["s1"]})(item)


def test_restoration_uses_recorded_base_and_failure_remains_fenced():
    item = release(state="executing")
    ledger = Ledger(item)
    pages, worker = Target("pages"), Target("worker")
    restored = []
    restorer = RecordedPairRestorer({item.base_sha:{"pages_deployment_id":"p-old",
      "worker_version_id":"w-old"}},lambda value:restored.append(value),lambda value:restored.append(value))
    # Provider probes observe the exact recorded base after restoration.
    class ProbeRestorer:
        def restore(self, sha):
            restorer.restore(sha); pages.observed = sha; worker.observed = sha
    assert ProductionExecutor(ledger,pages,worker,restorer=ProbeRestorer()).restore_recorded_base(item)["sha"] == item.base_sha
    assert restored == ["p-old","w-old"]
    assert ledger.events[-1][1] == "restored"
    with pytest.raises(ReleaseError, match="remains fenced"):
        ProductionExecutor(Ledger(item),Target("pages"),Target("worker")).restore_recorded_base(item)


def test_ledger_http_sends_bearer_csrf_marker_without_leaking_token():
    seen = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return b'{"ok":true,"release":null}'
    def opener(request, timeout):
        seen.update(request.headers)
        return Response()
    assert LedgerHTTP("https://edit.example", "secret", opener).claim_authorized() is None
    assert seen["X-edit-request"] == "1"
    assert seen["Authorization"] == "Bearer secret"
