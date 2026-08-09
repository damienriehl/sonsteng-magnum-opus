import pathlib
import sys
import hashlib
import json

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from prod_release_executor import (  # noqa: E402
    CompatibilityGate,
    CandidateValidator,
    ProductionCandidateBuilder,
    FrozenRelease,
    ProductionExecutor,
    ReleaseError,
    LedgerHTTP,
    GitRefAdapter,
    RecordedPairRestorer,
    RecoveryRegistry,
    WranglerPagesAdapter,
    WranglerWorkerAdapter,
)


class Ledger:
    def __init__(self, release=None):
        self.release = release
        self.events = []

    def claim_authorized(self):
        return self.release

    def transition(self, release_id, state, detail, fencing_token=None):
        self.events.append((release_id, state, detail))
        return {"ok": True}


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


def test_rejected_fence_halts_before_second_provider():
    item = release()
    class RejectingLedger(Ledger):
        def transition(self, release_id, state, detail, fencing_token=None):
            self.events.append((release_id, state, detail))
            return {"ok": state == "executing", "reason": "stale_fence"}
    ledger = RejectingLedger(item)
    pages = Target("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    with pytest.raises(ReleaseError, match="ledger rejected transition"):
        ProductionExecutor(ledger, pages, worker).run_once()
    assert pages.deployed == [item.candidate_sha]
    assert worker.deployed == []


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
        def require_clean_candidate(self, candidate): return None
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
        def restore(self, sha, order):
            restorer.restore(sha, order); pages.observed = sha; worker.observed = sha
    assert ProductionExecutor(ledger,pages,worker,restorer=ProbeRestorer()).restore_recorded_base(item)["sha"] == item.base_sha
    assert restored == ["w-old","p-old"]
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
    assert seen["User-agent"] == "sonsteng-prod-release/1.0"


def test_wrangler_adapters_pin_candidate_root_and_timeout(tmp_path):
    root = tmp_path / "candidate"
    site = root / "site"
    config = root / "app" / "worker" / "wrangler.jsonc"
    site.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    calls = []
    class Result:
        stdout = ("Deployment complete! https://pagesdeploy123.sonsteng.pages.dev\n"
                  "Worker Version ID: 12345678-1234-4234-8234-123456789abc\n")
    staged_headers = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[2:4] == ["pages", "deploy"]:
            staged_headers.append((pathlib.Path(argv[4]) / "_headers").read_text())
        return Result()
    item = release()
    WranglerPagesAdapter("sonsteng", site, "https://pages.example", root,
                         run=run, timeout=17).deploy(item)
    WranglerWorkerAdapter(config, "https://worker.example", root,
                          run=run, timeout=19).deploy(item)
    assert [call[1]["timeout"] for call in calls] == [17, 19, 19]
    assert all(call[1]["cwd"] == root.resolve() for call in calls)
    assert staged_headers == ["/*\n  X-Release-SHA: " + item.candidate_sha + "\n"]
    assert not (site / "_headers").exists()
    assert ["--branch", "main"] == calls[0][0][-4:-2]
    assert ["--env", "production"] == calls[1][0][6:8]
    assert calls[1][0][-2:] == ["--var", "RELEASE_SHA:" + item.candidate_sha]
    assert ["--env", "production"] == calls[2][0][-3:-1]
    assert calls[2][0][4] == "12345678-1234-4234-8234-123456789abc"
    with pytest.raises(ReleaseError, match="outside"):
        WranglerPagesAdapter("sonsteng", tmp_path / "other", "https://pages.example",
                             root, run=run).deploy(item)

    class BadResult:
        stdout = "Uploaded, but no machine-readable version identity\n"
    with pytest.raises(ReleaseError, match="version identifier"):
        WranglerWorkerAdapter(config, "https://worker.example", root,
            run=lambda *_args, **_kwargs: BadResult()).deploy(item)


def test_recovery_registry_persists_exact_pair_atomically(tmp_path):
    path = tmp_path / "known-good.json"
    registry = RecoveryRegistry(path)
    sha = "b" * 40
    registry.record_target(sha, "pages", "pagesdeploy123")
    registry.record_target(sha, "worker", "12345678-1234-4234-8234-123456789abc")
    assert json.loads(path.read_text())[sha] == {
        "pages_deployment_id":"pagesdeploy123",
        "worker_version_id":"12345678-1234-4234-8234-123456789abc"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert registry.target(sha, "pages") == "pagesdeploy123"
    with pytest.raises(ReleaseError, match="conflict"):
        registry.record_target(sha, "worker", "different")


def test_registry_reconciles_crash_before_ledger_receipt_without_redeploy(tmp_path):
    item = release()
    registry = RecoveryRegistry(tmp_path / "known-good.json")
    registry.record_target(item.candidate_sha, "pages", "pagesdeploy123")
    registry.record_target(item.candidate_sha, "worker",
                           "12345678-1234-4234-8234-123456789abc")
    ledger = Ledger(item)
    pages = Target("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    result = ProductionExecutor(ledger, pages, worker,
        recovery_registry=registry).run_once()
    assert result["state"] == "complete"
    assert pages.deployed == []
    assert worker.deployed == []


def test_git_adapter_materializes_frozen_sha_away_from_advancing_checkout(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("frozen", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "frozen"], cwd=repo, check=True, capture_output=True)
    frozen = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                            capture_output=True, text=True).stdout.strip()
    tracked.write_text("advanced", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "advanced"], cwd=repo, check=True, capture_output=True)

    adapter = GitRefAdapter(repo)
    with adapter.isolated_checkout(frozen) as candidate:
        assert candidate != repo
        assert (candidate / "tracked.txt").read_text(encoding="utf-8") == "frozen"
        GitRefAdapter(candidate).require_clean_candidate(frozen)
    assert not candidate.exists()


def test_candidate_builder_freezes_latest_frontier_for_human_review(tmp_path):
    class BuilderLedger:
        def __init__(self): self.binding = None
        def preparation_context(self):
            return {"base_sha":"a" * 40,"active_release":None,"batches":[
                {"batch_id":"batch-1","commit_sha":"c" * 40,
                 "generator_id":"generator-1","suggestion_ids":["s2"]},
                {"batch_id":"batch-2","commit_sha":"b" * 40,
                 "generator_id":"generator-1","suggestion_ids":["s1"]}]}
        def prepare(self, binding):
            self.binding = binding
            return {"ok":True,"release":{"id":binding["id"],"state":"prepared"}}
    class Git:
        def require_clean_candidate(self, candidate): assert candidate == "b" * 40
        def is_ancestor(self, base, candidate): return True
        def tree(self, candidate): return "tree-1"
    ledger = BuilderLedger()
    manifest_path = tmp_path / "state" / "manifest.json"
    made = ProductionCandidateBuilder(ledger,Git(),manifest_path).prepare_latest()
    manifest = json.loads(manifest_path.read_text())
    assert made["release"]["state"] == "prepared"
    assert manifest["batch_ids"] == ["batch-1","batch-2"]
    assert manifest["suggestion_ids"] == ["s1","s2"]
    assert ledger.binding["target_batch_id"] == "batch-2"
    assert ledger.binding["candidate_sha"] == "b" * 40
    assert ledger.binding["expected_suggestion_ids"] == ["s1","s2"]


def test_candidate_builder_requires_recorded_first_base_and_reproduces_active(tmp_path):
    class Git:
        def tree(self, candidate): return "tree-1"
    class EmptyBaseLedger:
        def preparation_context(self):
            return {"base_sha":None,"active_release":None,"batches":[{
                "batch_id":"batch-1","commit_sha":"b" * 40,
                "generator_id":"generator-1","suggestion_ids":["s1"]}]}
    with pytest.raises(ReleaseError, match="recorded production base"):
        ProductionCandidateBuilder(EmptyBaseLedger(),Git(),tmp_path / "manifest.json").prepare_latest()

    manifest = ProductionCandidateBuilder._manifest("a" * 40,"b" * 40,"tree-1",
        "generator-1",["batch-1"],["b" * 40],["s1"])
    active = {"id":"release-1","state":"authorized","base_sha":"a" * 40,
        "candidate_sha":"b" * 40,"generator_id":"generator-1",
        "manifest_hash":hashlib.sha256(json.dumps(manifest,sort_keys=True,
          separators=(",", ":")).encode()).hexdigest(),"suggestion_ids":["s1"],
        "batches":[{"batch_id":"batch-1","commit_sha":"b" * 40}]}
    class ActiveLedger:
        def preparation_context(self): return {"active_release":active,"batches":[]}
    path = tmp_path / "active.json"
    assert ProductionCandidateBuilder(ActiveLedger(),Git(),path).prepare_latest()["replay"]
    assert json.loads(path.read_text()) == manifest
