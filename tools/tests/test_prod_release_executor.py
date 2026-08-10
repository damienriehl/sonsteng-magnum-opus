import pathlib
import sys
import hashlib
import json
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from prod_release_executor import (  # noqa: E402
    AcceptedOnlyMaterializer,
    AcceptedProjectionCandidateBuilder,
    ProjectionTreeWriter,
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


def projection_source(**overrides):
    value = {
        "review_revision_id": "revision-1",
        "source_ref": "data/copy/home.json#lead",
        "source_revision": "dev-1",
        "prod_base": "prod-1",
        "original_text": "The bad idea",
        "operations": [
            {"id": "op-word", "decision_id": "op-word", "kind": "replace",
             "source_ref": "data/copy/home.json#lead", "old_text": "bad",
             "new_text": "good", "base_range": [4, 7], "created_at": 1,
             "context_before": ["The "], "context_after": [" idea"]},
            {"id": "op-comma", "decision_id": "op-comma", "kind": "insert",
             "source_ref": "data/copy/home.json#lead", "old_text": "",
             "new_text": ",", "base_range": [12, 12], "created_at": 2,
             "context_before": [" idea"], "context_after": []},
        ],
        "decisions": [
            {"operation_id": "op-word", "decision": "accepted"},
            {"operation_id": "op-comma", "decision": "rejected"},
        ],
        "stale": False,
    }
    value.update(overrides)
    return value


def test_accepted_only_materializer_projects_atoms_and_records_held_canaries():
    materialized = AcceptedOnlyMaterializer().materialize([projection_source()])
    assert materialized.patches[0].original_text == "The bad idea"
    assert materialized.patches[0].new_text == "The good idea"
    assert [item.reason for item in materialized.exclusions] == ["rejected"]
    assert "op-comma" not in materialized.accepted_operation_ids


@pytest.mark.parametrize("decision,stale,reason", [
    ("rejected", False, "rejected"),
    ("questioned", False, "questioned"),
    (None, False, "unanswered"),
    ("accepted", True, "stale"),
])
def test_accepted_only_materializer_positive_held_leak_canaries(decision, stale, reason):
    source = projection_source(stale=stale)
    source["operations"] = [source["operations"][0]]
    source["decisions"] = [] if decision is None else [
        {"operation_id": "op-word", "decision": decision}
    ]
    result = AcceptedOnlyMaterializer().materialize([source])
    assert result.patches == ()
    assert result.exclusions[0].operation_id == "op-word"
    assert result.exclusions[0].reason == reason


def test_accepted_only_materializer_projects_later_unique_anchor_across_held_edit():
    source = projection_source(original_text="One plain sentence.")
    source["operations"] = [
        {"id":"held", "kind":"replace", "source_ref":source["source_ref"],
         "old_text":"plain", "new_text":"short", "base_range":[4,9], "created_at":1,
         "context_before":["One "], "context_after":[" sentence."]},
        {"id":"accepted", "kind":"replace", "source_ref":source["source_ref"],
         "old_text":"sentence", "new_text":"example", "base_range":[10,18], "created_at":2,
         "context_before":["plain "], "context_after":["."]},
    ]
    source["decisions"] = [
        {"operation_id":"held", "decision":"rejected"},
        {"operation_id":"accepted", "decision":"accepted"},
    ]
    result = AcceptedOnlyMaterializer().materialize([source])
    assert result.patches[0].new_text == "One plain example."


def test_accepted_only_materializer_fails_before_output_on_ambiguous_or_overlap():
    ambiguous = projection_source(original_text="bad and bad")
    ambiguous["operations"] = [{"id":"op", "kind":"replace",
        "source_ref":ambiguous["source_ref"], "old_text":"bad", "new_text":"good",
        "base_range":[0,3], "created_at":1, "context_before":[], "context_after":[]}]
    ambiguous["decisions"] = [{"operation_id":"op", "decision":"accepted"}]
    with pytest.raises(ReleaseError, match="unique anchor"):
        AcceptedOnlyMaterializer().materialize([ambiguous])

    overlap = projection_source(original_text="abcdef")
    overlap["operations"] = [
        {"id":"a", "kind":"replace", "source_ref":overlap["source_ref"],
         "old_text":"bcd", "new_text":"X", "base_range":[1,4], "created_at":1},
        {"id":"b", "kind":"replace", "source_ref":overlap["source_ref"],
         "old_text":"cde", "new_text":"Y", "base_range":[2,5], "created_at":2},
    ]
    overlap["decisions"] = [{"operation_id":x, "decision":"accepted"} for x in ("a","b")]
    with pytest.raises(ReleaseError, match="overlapping"):
        AcceptedOnlyMaterializer().materialize([overlap])


def test_accepted_only_materializer_never_splits_structural_group():
    source = projection_source(original_text="A\n\nB")
    source["operations"] = [
        {"id":"move-from", "decision_id":"move-1", "group_id":"group-1",
         "kind":"move", "source_ref":source["source_ref"], "op":"move",
         "op_arg":"data/copy/home.json#b22222222", "created_at":1},
        {"id":"move-to", "decision_id":"move-1", "group_id":"group-1",
         "kind":"move", "source_ref":source["source_ref"], "op":"move",
         "op_arg":"data/copy/home.json#b22222222", "created_at":1},
    ]
    source["decisions"] = [{"operation_id":"move-1", "decision":"accepted"}]
    accepted = AcceptedOnlyMaterializer().materialize([source])
    assert len(accepted.structural_operations) == 2

    source["decisions"] = [{"operation_id":"move-1", "decision":"rejected"}]
    held = AcceptedOnlyMaterializer().materialize([source])
    assert held.structural_operations == ()
    assert {item.reason for item in held.exclusions} == {"rejected"}

    source["operations"][1]["decision_id"] = "move-2"
    source["decisions"] = [{"operation_id":"move-1", "decision":"accepted"}]
    with pytest.raises(ReleaseError, match="partial structural group"):
        AcceptedOnlyMaterializer().materialize([source])


def test_projection_builder_is_deterministic_and_never_moves_or_dirties_ambient_head(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git","init","-q","-b","main"],cwd=repo,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=repo,check=True)
    subprocess.run(["git","config","user.email","test@example.invalid"],cwd=repo,check=True)
    (repo / "data").mkdir()
    (repo / "data" / "copy.txt").write_text("base\n",encoding="utf-8")
    subprocess.run(["git","add","data/copy.txt"],cwd=repo,check=True)
    subprocess.run(["git","commit","-q","-m","base"],cwd=repo,check=True)
    base = subprocess.run(["git","rev-parse","HEAD"],cwd=repo,check=True,
        capture_output=True,text=True).stdout.strip()

    class Writer:
        def write(self, root, projection):
            pathlib.Path(root,"data","copy.txt").write_text(
                projection.patches[0].new_text + "\n",encoding="utf-8")
            return {"generator_id":"generator-v2","parity_verified":True}

    source = projection_source(prod_base=base,original_text="The bad idea")
    context = {"base_sha":base,"projection":{"sources":[source],
        "review_receipts":[{"receipt_hash":"receipt-1","created_at":120000}]}}
    builder = AcceptedProjectionCandidateBuilder(GitRefAdapter(repo),tmp_path / "manifest.json",
                                                  writer=Writer())
    first = builder.build(context)
    second = builder.build(context)
    assert first["candidate_sha"] == second["candidate_sha"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert subprocess.run(["git","rev-parse","HEAD"],cwd=repo,check=True,
        capture_output=True,text=True).stdout.strip() == base
    assert subprocess.run(["git","status","--porcelain"],cwd=repo,check=True,
        capture_output=True,text=True).stdout == ""
    candidate_copy = subprocess.run(["git","show",f"{first['candidate_sha']}:data/copy.txt"],
        cwd=repo,check=True,capture_output=True,text=True).stdout
    assert candidate_copy == "The good idea\n"  # rejected comma is a positive leak canary


def test_projection_tree_writer_uses_existing_atomic_writer_and_runs_parity(tmp_path):
    root = tmp_path / "candidate"
    (root / "data").mkdir(parents=True)
    target = root / "data" / "home.json"
    target.write_text('{"lead":"The bad idea"}\n',encoding="utf-8")

    class Pipeline:
        calls = []
        def regenerate_map(self, worktree):
            self.calls.append("map")
            return {"data/home.json#lead":{"kind":"json_scalar","json_path":"lead"}}
        def validate(self, worktree): self.calls.append("validate"); return True,{}
        def build(self, worktree): self.calls.append("build"); return True,{}
        def parity(self, worktree): self.calls.append("parity"); return True,{}
        def generator_identity(self, worktree): return "generator-v2"

    pipeline = Pipeline()
    projection = AcceptedOnlyMaterializer().materialize([projection_source(
        source_ref="data/home.json#lead",operations=[
            {**item,"source_ref":"data/home.json#lead"}
            for item in projection_source()["operations"]])])
    assert ProjectionTreeWriter(pipeline).write(root,projection) == {
        "generator_id":"generator-v2","parity_verified":True}
    assert json.loads(target.read_text(encoding="utf-8"))["lead"] == "The good idea"
    assert pipeline.calls == ["map","map","validate","build","parity"]


class Ledger:
    def __init__(self, release=None):
        self.release = release
        self.events = []
        self.renewals = 0
        self.renewal_leases = []

    def claim_authorized(self):
        return self.release

    def transition(self, release_id, state, detail, fencing_token=None):
        self.events.append((release_id, state, detail))
        return {"ok": True}

    def renew(self, release_id, fencing_token, lease_ms=None):
        self.renewals += 1
        self.renewal_leases.append(lease_ms)
        return {"ok": True, "lease_expires_at": lease_ms}


class Target:
    def __init__(self, name, fail=False, observed=None):
        self.name, self.fail, self.observed = name, fail, observed
        self.deployed = []

    def deploy(self, manifest):
        if self.fail:
            raise RuntimeError("provider failure")
        self.deployed.append(manifest.candidate_sha)
        return {"provider_id": f"{self.name}-1", "deployable_id":f"{self.name}-artifact-1"}

    def provenance(self):
        return self.observed

    def restore(self, sha):
        if self.fail:
            raise RuntimeError("restore failure")
        self.observed = sha


class ArtifactTarget(Target):
    def __init__(self, name, candidate, artifacts, **kwargs):
        super().__init__(name, **kwargs)
        self.candidate, self.artifacts = candidate, dict(artifacts)
        self.reactivated = []

    def restore(self, artifact_id):
        if self.fail:
            raise RuntimeError("restore failure")
        self.reactivated.append(artifact_id)
        self.observed = self.artifacts.get(artifact_id, "foreign")


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


def test_provider_operations_are_heartbeated_while_blocked():
    item = release()
    ledger = Ledger(item)

    class SlowTarget(Target):
        def deploy(self, manifest):
            deadline = time.monotonic() + 1
            while ledger.renewals < 2 and time.monotonic() < deadline:
                time.sleep(.002)
            return super().deploy(manifest)

    pages = SlowTarget("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    ProductionExecutor(ledger, pages, worker, heartbeat_interval=.005).run_once()
    # Before + during + after the slow call, plus bracketing the second provider.
    assert ledger.renewals >= 5


def test_lost_provider_heartbeat_fails_closed_before_next_provider():
    item = release()

    class LosingLedger(Ledger):
        def renew(self, release_id, fencing_token, lease_ms=None):
            self.renewals += 1
            self.renewal_leases.append(lease_ms)
            return {"ok": self.renewals < 2, "reason": "stale_fence"}

    ledger = LosingLedger(item)

    class SlowTarget(Target):
        max_operation_seconds = 2 * 240

        def deploy(self, manifest):
            deadline = time.monotonic() + 1
            while ledger.renewals < 2 and time.monotonic() < deadline:
                time.sleep(.002)
            return super().deploy(manifest)

    pages = SlowTarget("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    with pytest.raises(ReleaseError, match="renewal lost"):
        ProductionExecutor(ledger, pages, worker, heartbeat_interval=.005).run_once()
    # One successful renewal alone covers both maximum-length Worker commands
    # plus the executor's one-minute scheduling margin.
    assert ledger.renewal_leases[0] == (2 * 240 + 60) * 1000
    assert pages.deployed == [item.candidate_sha]
    assert worker.deployed == []


def test_worker_adapter_reports_complete_two_command_bound():
    adapter = WranglerWorkerAdapter("/candidate/app/worker/wrangler.jsonc",
        "https://edit.example/provenance", candidate_root="/candidate", timeout=240)
    assert adapter.max_operation_seconds == 480


def test_provider_bound_larger_than_lease_cap_fails_before_mutation():
    item = release()
    ledger = Ledger(item)

    class UnboundedTarget(Target):
        max_operation_seconds = 15 * 60

    pages = UnboundedTarget("pages", observed=item.candidate_sha)
    worker = Target("worker", observed=item.candidate_sha)
    with pytest.raises(ReleaseError, match="exceeds maximum"):
        ProductionExecutor(ledger, pages, worker).run_once()
    assert not pages.deployed and not worker.deployed


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
    item = release(state="restoring")
    ledger = Ledger(item)
    pages = ArtifactTarget("pages",item.base_sha,{"p-old":item.base_sha},
                           observed=item.candidate_sha)
    worker = ArtifactTarget("worker",item.base_sha,{"w-old":item.base_sha},
                            observed=item.candidate_sha)
    restorer = RecordedPairRestorer({item.base_sha:{"pages_deployment_id":"p-old",
      "worker_version_id":"w-old"}},pages.restore,worker.restore)
    assert ProductionExecutor(ledger,pages,worker,restorer=restorer,
        heartbeat_interval=.005).restore_recorded_base(item)["sha"] == item.base_sha
    assert worker.reactivated == ["w-old"] and pages.reactivated == ["p-old"]
    assert ledger.events[-1][1] == "restored"
    with pytest.raises(ReleaseError, match="remains fenced"):
        ProductionExecutor(Ledger(item),Target("pages"),Target("worker")).restore_recorded_base(item)

    resumed = release(state="restoring", completed_phases=("restoring",))
    resumed_ledger = Ledger(resumed)
    pages = ArtifactTarget("pages",resumed.base_sha,{"p-old":resumed.base_sha},
                           observed=resumed.base_sha)
    worker = ArtifactTarget("worker",resumed.base_sha,{"w-old":resumed.base_sha},
                            observed=resumed.candidate_sha)
    ProductionExecutor(resumed_ledger,pages,worker,
        restorer=RecordedPairRestorer({resumed.base_sha:{"pages_deployment_id":"p-old",
          "worker_version_id":"w-old"}},pages.restore,worker.restore)).restore_recorded_base(resumed)
    assert pages.reactivated == [] and worker.reactivated == ["w-old"]
    assert [event[1] for event in resumed_ledger.events] == ["restored"]

    with pytest.raises(ReleaseError,match="exclusive claimed"):
        ProductionExecutor(Ledger(release(state="failed_fenced")),pages,worker,
            restorer=restorer).restore_recorded_base(release(state="failed_fenced"))


def test_long_restore_is_heartbeated_and_stale_fence_halts_partial_recovery():
    item = release(state="restoring")
    ledger = Ledger(item)
    class SlowTarget(ArtifactTarget):
        max_operation_seconds = 2
        def restore(self,artifact_id):
            deadline = time.monotonic() + 1
            while ledger.renewals < 2 and time.monotonic() < deadline:
                time.sleep(.002)
            return super().restore(artifact_id)
    pages = ArtifactTarget("pages",item.base_sha,{"p-old":item.base_sha},
                           observed=item.candidate_sha)
    worker = SlowTarget("worker",item.base_sha,{"w-old":item.base_sha},
                        observed=item.candidate_sha)
    restorer = RecordedPairRestorer({item.base_sha:{"pages_deployment_id":"p-old",
      "worker_version_id":"w-old"}},pages.restore,worker.restore)
    ProductionExecutor(ledger,pages,worker,restorer=restorer,
        heartbeat_interval=.005).restore_recorded_base(item)
    assert ledger.renewals >= 4

    class StaleLedger(Ledger):
        def renew(self,*args,**kwargs): return {"ok":False,"reason":"stale_fence"}
    pages = ArtifactTarget("pages",item.base_sha,{"p-old":item.base_sha},
                           observed=item.candidate_sha)
    worker = ArtifactTarget("worker",item.base_sha,{"w-old":item.base_sha},
                            observed=item.candidate_sha)
    with pytest.raises(ReleaseError,match="fenced"):
        ProductionExecutor(StaleLedger(item),pages,worker,restorer=RecordedPairRestorer(
          {item.base_sha:{"pages_deployment_id":"p-old","worker_version_id":"w-old"}},
          pages.restore,worker.restore)).restore_recorded_base(item)
    assert not pages.reactivated and not worker.reactivated


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


def test_ledger_http_restore_claim_is_named_and_returns_fresh_fence():
    seen = {}
    item = release(state="restoring",fencing_token="restore-fence")
    payload = {"ok":True,"release":{"id":item.id,"state":item.state,
      "base_sha":item.base_sha,"candidate_sha":item.candidate_sha,
      "manifest_hash":item.manifest_hash,"membership_hash":item.membership_hash,
      "suggestion_ids":list(item.suggestion_ids),"fencing_token":item.fencing_token,
      "batches":[{"commit_sha":sha} for sha in item.batch_commits],"events":[]}}
    class Response:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def read(self,*_): return json.dumps(payload).encode()
    def opener(request,timeout):
        seen["url"],seen["body"] = request.full_url,json.loads(request.data)
        return Response()
    claimed = LedgerHTTP("https://edit.example","secret",opener).claim_restore("rel-1")
    assert seen == {"url":"https://edit.example/edit/v1/prod/releases/restore-claim",
                    "body":{"id":"rel-1"}}
    assert claimed.state == "restoring" and claimed.fencing_token == "restore-fence"


def test_both_provenance_probes_send_cloudflare_safe_service_user_agent(tmp_path):
    requests = []

    class Response:
        headers = {"X-Release-SHA": "b" * 40}
        def __enter__(self): return self
        def __exit__(self, *_): pass

    def opener(request, timeout):
        requests.append(request)
        return Response()

    pages = WranglerPagesAdapter("sonsteng", tmp_path, "https://pages.example/provenance",
        candidate_root=tmp_path, opener=opener)
    worker = WranglerWorkerAdapter(tmp_path / "wrangler.jsonc",
        "https://worker.example/provenance", candidate_root=tmp_path, opener=opener)
    assert pages.provenance() == "b" * 40
    assert worker.provenance() == "b" * 40
    assert [request.full_url for request in requests] == [
        "https://pages.example/provenance", "https://worker.example/provenance"]
    for request in requests:
        assert request.get_header("User-agent") == "sonsteng-prod-release/1.0"
        assert not request.get_header("User-agent").startswith("Python-urllib/")


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
    WranglerPagesAdapter("sonsteng",site,"https://pages.example",root,
                         run=run,timeout=17).restore("pagesdeploy123")
    WranglerWorkerAdapter(config,"https://worker.example",root,
                          run=run,timeout=19).restore(
                              "12345678-1234-4234-8234-123456789abc")
    assert [call[1]["timeout"] for call in calls] == [17, 19, 19, 17, 19]
    assert all(call[0][:2] == ["npx","wrangler@4"] for call in calls)
    assert all(call[1]["cwd"] == root.resolve() for call in calls)
    assert staged_headers == ["/*\n  X-Release-SHA: " + item.candidate_sha + "\n"]
    assert not (site / "_headers").exists()
    assert ["--branch", "main"] == calls[0][0][-4:-2]
    assert ["--env", "production"] == calls[1][0][6:8]
    assert calls[1][0][-2:] == ["--var", "RELEASE_SHA:" + item.candidate_sha]
    assert ["--env", "production"] == calls[2][0][-3:-1]
    assert calls[2][0][4] == "12345678-1234-4234-8234-123456789abc"
    assert calls[3][0][2:6] == ["pages","deployment","rollback","pagesdeploy123"]
    assert calls[4][0][2:5] == ["versions","deploy",
      "12345678-1234-4234-8234-123456789abc"]
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
    pages = ArtifactTarget("pages",item.candidate_sha,
        {"pagesdeploy123":item.candidate_sha},observed=item.candidate_sha)
    worker = ArtifactTarget("worker",item.candidate_sha,
        {"12345678-1234-4234-8234-123456789abc":item.candidate_sha},
        observed=item.candidate_sha)
    result = ProductionExecutor(ledger, pages, worker,
        recovery_registry=registry).run_once()
    assert result["state"] == "complete"
    assert pages.deployed == []
    assert worker.deployed == []
    assert pages.reactivated == []
    assert worker.reactivated == []


def test_fresh_attempt_reactivates_exact_recorded_pair_after_base_restore(tmp_path):
    item = release(id="rel-retry")
    registry = RecoveryRegistry(tmp_path / "known-good.json")
    registry.record_target(item.candidate_sha,"pages","pages-candidate")
    registry.record_target(item.candidate_sha,"worker","worker-candidate")
    order = []
    class OrderedTarget(ArtifactTarget):
        def restore(self, artifact_id):
            order.append(self.name)
            return super().restore(artifact_id)
    pages = OrderedTarget("pages",item.candidate_sha,
        {"pages-candidate":item.candidate_sha},observed=item.base_sha)
    worker = OrderedTarget("worker",item.candidate_sha,
        {"worker-candidate":item.candidate_sha},observed=item.base_sha)
    result = ProductionExecutor(Ledger(item),pages,worker,
        compatibility=CompatibilityGate(False,True),
        recovery_registry=registry).run_once()
    assert result["state"] == "complete"
    assert order == ["worker","pages"]
    assert pages.reactivated == ["pages-candidate"]
    assert worker.reactivated == ["worker-candidate"]
    assert not pages.deployed and not worker.deployed
    assert result["receipts"]["pages"]["reactivated"] is True


def test_reactivation_handles_one_target_partial_prior_attempt(tmp_path):
    item = release(id="rel-partial-retry")
    registry = RecoveryRegistry(tmp_path / "known-good.json")
    registry.record_target(item.candidate_sha,"pages","pages-candidate")
    pages = ArtifactTarget("pages",item.candidate_sha,
        {"pages-candidate":item.candidate_sha},observed=item.base_sha)
    worker = Target("worker",observed=item.candidate_sha)
    result = ProductionExecutor(Ledger(item),pages,worker,
        recovery_registry=registry).run_once()
    assert result["state"] == "complete"
    assert pages.reactivated == ["pages-candidate"]
    assert worker.deployed == [item.candidate_sha]
    assert registry.target(item.candidate_sha,"worker") == "worker-artifact-1"


def test_recorded_artifact_reactivation_fences_foreign_live_or_receipt(tmp_path):
    item = release(id="rel-foreign")
    registry = RecoveryRegistry(tmp_path / "known-good.json")
    registry.record_target(item.candidate_sha,"pages","pages-candidate")
    registry.record_target(item.candidate_sha,"worker","worker-candidate")
    pages = ArtifactTarget("pages",item.candidate_sha,
        {"pages-candidate":item.candidate_sha},observed="foreign-live")
    worker = ArtifactTarget("worker",item.candidate_sha,
        {"worker-candidate":item.candidate_sha},observed=item.base_sha)
    with pytest.raises(ReleaseError,match="live provenance or base"):
        ProductionExecutor(Ledger(item),pages,worker,recovery_registry=registry).run_once()
    assert not pages.reactivated and not worker.reactivated

    pages = ArtifactTarget("pages",item.candidate_sha,
        {"pages-candidate":"foreign-artifact"},observed=item.base_sha)
    with pytest.raises(ReleaseError,match="reactivation provenance mismatch"):
        ProductionExecutor(Ledger(item),pages,
            ArtifactTarget("worker",item.candidate_sha,
                {"worker-candidate":item.candidate_sha},observed=item.base_sha),
            recovery_registry=registry).run_once()
    assert pages.reactivated == ["pages-candidate"]


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
    made = ProductionCandidateBuilder(ledger,Git(),manifest_path,
        attempt_id_factory=lambda: "attempt-1").prepare_latest()
    manifest = json.loads(manifest_path.read_text())
    assert made["release"]["state"] == "prepared"
    assert manifest["batch_ids"] == ["batch-1","batch-2"]
    assert manifest["suggestion_ids"] == ["s1","s2"]
    assert ledger.binding["target_batch_id"] == "batch-2"
    assert ledger.binding["candidate_sha"] == "b" * 40
    assert ledger.binding["expected_suggestion_ids"] == ["s1","s2"]
    assert ledger.binding["id"].endswith("-attempt-1")


def test_candidate_builder_creates_fresh_attempt_after_restoration_and_replays_active(tmp_path):
    class Git:
        def require_clean_candidate(self, _candidate): pass
        def is_ancestor(self, _base, _candidate): return True
        def tree(self, _candidate): return "tree-1"

    batches = [{"batch_id":"batch-1","commit_sha":"b" * 40,
                "generator_id":"generator-1","suggestion_ids":["s1"]}]

    class RetryLedger:
        def __init__(self):
            self.active = None
            self.bindings = []
        def preparation_context(self):
            if self.active:
                return {"active_release":self.active,"batches":[]}
            return {"base_sha":"a" * 40,"active_release":None,"batches":batches}
        def prepare(self, binding):
            self.bindings.append(binding)
            self.active = {**binding,"state":"prepared","batches":[
                {"batch_id":"batch-1","commit_sha":"b" * 40}],
                "suggestion_ids":["s1"]}
            return {"ok":True,"release":self.active}

    attempts = iter(["restored-attempt", "retry-attempt"])
    ledger = RetryLedger()
    builder = ProductionCandidateBuilder(ledger,Git(),tmp_path / "manifest.json",
        attempt_id_factory=lambda: next(attempts))
    first = builder.prepare_latest()["release"]
    ledger.active = None  # the ledger now reports the first attempt as restored
    second = builder.prepare_latest()["release"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["id"] != second["id"]
    assert second["id"].endswith("-retry-attempt")
    assert builder.prepare_latest()["replay"] is True
    assert len(ledger.bindings) == 2


def test_candidate_builder_freezes_revert_only_frontier_with_exact_evidence(tmp_path):
    class Git:
        def require_clean_candidate(self, candidate): assert candidate == "b" * 40
        def is_ancestor(self, _base, _candidate): return True
        def tree(self, _candidate): return "revert-tree"
    class Ledger:
        binding = None
        def preparation_context(self):
            return {"base_sha":"a" * 40,"active_release":None,"batches":[{
                "batch_id":"revert-r1","commit_sha":"b" * 40,
                "generator_id":"generator-revert","suggestion_ids":["r1"]}]}
        def prepare(self, binding):
            self.binding = binding
            return {"ok":True,"release":binding}
    ledger = Ledger()
    path = tmp_path / "revert-manifest.json"
    ProductionCandidateBuilder(ledger,Git(),path,
        attempt_id_factory=lambda: "revert-attempt").prepare_latest()
    manifest = json.loads(path.read_text())
    assert manifest["batch_ids"] == ["revert-r1"]
    assert manifest["batch_commits"] == ["b" * 40]
    assert manifest["suggestion_ids"] == ["r1"]
    assert manifest["generator_id"] == "generator-revert"
    assert ledger.binding["expected_suggestion_ids"] == ["r1"]


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


def test_candidate_builder_stops_at_generator_boundary_and_honors_evidence_fence(tmp_path):
    class Git:
        def require_clean_candidate(self, _sha): pass
        def is_ancestor(self, _base, _candidate): return True
        def tree(self, _sha): return "tree"
    class Ledger:
        binding = None
        def preparation_context(self):
            return {"base_sha":"a" * 40,"active_release":None,"batches":[
                {"batch_id":"b1","commit_sha":"b" * 40,"generator_id":"g1","suggestion_ids":["s1"]},
                {"batch_id":"b2","commit_sha":"c" * 40,"generator_id":"g2","suggestion_ids":["s2"]}]}
        def prepare(self, binding): self.binding = binding; return {"ok":True,"release":binding}
    ledger = Ledger()
    ProductionCandidateBuilder(ledger,Git(),tmp_path / "manifest.json").prepare_latest()
    assert ledger.binding["expected_batch_ids"] == ["b1"]
    assert ledger.binding["candidate_sha"] == "b" * 40

    class Blocked(Ledger):
        def preparation_context(self):
            return {"active_release":None,"batches":[],"blocked_reason":"missing_batch_evidence"}
    with pytest.raises(ReleaseError, match="missing_batch_evidence"):
        ProductionCandidateBuilder(Blocked(),Git(),tmp_path / "blocked.json").prepare_latest()
