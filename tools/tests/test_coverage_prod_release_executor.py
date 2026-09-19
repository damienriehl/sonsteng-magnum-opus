"""Failure-boundary and local integration coverage for frozen releases."""
import dataclasses
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import urllib.error
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
import prod_release_executor as release


def frozen(**changes):
    values = dict(id="release-1", state="authorized", base_sha="a" * 40,
                  candidate_sha="b" * 40, manifest_hash="hash", membership_hash="members",
                  suggestion_ids=("suggestion-1",), batch_commits=("b" * 40,),
                  fencing_token="fence")
    values.update(changes)
    return release.FrozenRelease(**values)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("changes,message", [
    ({"state": "prepared"}, "not executable"),
    ({"fencing_token": ""}, "fencing token"),
    ({"schema_version": 2, "operation_ids": ("op",)}, "review/projection"),
    ({"suggestion_ids": ()}, "unique IDs"),
])
def test_frozen_release_rejects_incomplete_authority(changes, message):
    with pytest.raises(ValueError, match=message):
        frozen(**changes)


def test_frozen_digest_changes_when_fence_or_completed_evidence_changes():
    value = frozen()
    assert value.digest == digest(dataclasses.asdict(value))
    assert dataclasses.replace(value, fencing_token="fresh").digest != value.digest
    assert dataclasses.replace(value, completed_phases=("executing",)).digest != value.digest


@pytest.mark.parametrize("bearer", [None, "", 1, "x" * 4097])
def test_observer_rejects_missing_or_unbounded_credentials(bearer):
    with pytest.raises(release.ObserverError, match="credential unavailable"):
        release.ReleaseObserverHTTP("https://ledger.invalid", bearer)


@pytest.mark.parametrize("raw,message", [
    (b"{", "malformed"), (b"\xff", "malformed"),
    (b"[]", "rejected"), (b'{"ok":1}', "rejected"),
    (b'{"ok":false}', "rejected"),
    (b"x" * (1024 * 1024 + 1), "exceeded bound"),
])
def test_observer_rejects_untrusted_wire_payload(raw, message):
    def opener(request, timeout):
        assert request.method == "GET"
        assert timeout == 15
        return io.BytesIO(raw)
    observer = release.ReleaseObserverHTTP("https://ledger.invalid", "fixture", opener=opener)
    with pytest.raises(release.ObserverError, match=message):
        observer.audit()


@pytest.mark.parametrize("error", [TimeoutError("private"), OSError("private"), urllib.error.URLError("private")])
def test_observer_transport_failure_has_bounded_public_message(error):
    def opener(*args, **kwargs):
        raise error
    observer = release.ReleaseObserverHTTP("https://ledger.invalid", "fixture", opener=opener)
    with pytest.raises(release.ObserverError, match="^observer request unavailable$"):
        observer.audit()


@pytest.mark.parametrize("method,args,message", [
    ("audit", (), "audit malformed"),
    ("preparation_context", (), "frontier malformed"),
    ("get_release", ("release-1",), "release malformed"),
])
def test_observer_requires_endpoint_specific_object(method, args, message):
    observer = release.ReleaseObserverHTTP("https://ledger.invalid", "fixture",
        opener=lambda *a, **k: io.BytesIO(b'{"ok":true}'))
    with pytest.raises(release.ObserverError, match=message):
        getattr(observer, method)(*args)


def test_observer_rejects_mutating_endpoint_before_opening_transport():
    observer = release.ReleaseObserverHTTP("https://ledger.invalid", "fixture",
        opener=lambda *a, **k: pytest.fail("transport must not be called"))
    with pytest.raises(release.ObserverError, match="not allowlisted"):
        observer._ReleaseObserverHTTP__get("/edit/v1/prod/releases/claim")
    assert observer._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://else.invalid") is None


@pytest.fixture
def local_git(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE,
                              text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.name", "Coverage Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "content.txt").write_text("base\n")
    git("add", "content.txt")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "content.txt").write_text("candidate\n")
    git("commit", "-qam", "candidate")
    return repo, git, base, git("rev-parse", "HEAD")


def test_real_git_freeze_validate_registry_and_replay_chain(local_git, tmp_path):
    repo, git, base, candidate = local_git
    context = {"base_sha": base, "batches": [{"batch_id": "batch-1", "commit_sha": candidate,
                "suggestion_ids": ["suggestion-1"], "generator_id": "generator-1"}]}
    bindings = []
    def prepare(binding):
        bindings.append(binding)
        return {"ok": True, "release": binding}
    ledger = SimpleNamespace(preparation_context=lambda: context, prepare=prepare)
    path = tmp_path / "manifest.json"
    builder = release.ProductionCandidateBuilder(ledger, release.GitRefAdapter(repo), path,
                                                  attempt_id_factory=lambda: "fixture")
    result = builder.prepare_latest()
    manifest = json.loads(path.read_text())
    assert result["ok"] is True
    assert manifest["candidate_tree"] == git("rev-parse", "HEAD^{tree}")
    authority = frozen(base_sha=base, candidate_sha=candidate, batch_commits=(candidate,),
                       manifest_hash=bindings[0]["manifest_hash"])
    release.CandidateValidator(release.GitRefAdapter(repo), manifest)(authority)
    registry = release.RecoveryRegistry(tmp_path / "recovery" / "registry.json")
    assert registry.record_pair(candidate, "pages-1", "worker-1") is False
    assert registry.record_pair(candidate, "pages-1", "worker-1") is True
    calls = []
    release.RecordedPairRestorer(registry.pairs(), lambda x: calls.append(("pages", x)),
        lambda x: calls.append(("worker", x))).restore(candidate)
    assert calls == [("pages", "pages-1"), ("worker", "worker-1")]
    assert registry.path.stat().st_mode & 0o777 == 0o600
    assert git("status", "--porcelain") == ""
    assert len(git("worktree", "list", "--porcelain").split("worktree ")) == 2
    context["active_release"] = {"schema_version": 2, "manifest_hash": digest(manifest)}
    assert builder.prepare_latest()["replay"] is True
    assert len(bindings) == 1


@pytest.mark.parametrize("failure", ["hash", "ancestry", "tree"])
def test_validator_rejects_corrupt_manifest_with_real_git(local_git, failure):
    repo, git, base, candidate = local_git
    manifest = {"base_sha": base, "candidate_sha": candidate, "candidate_tree": git("rev-parse", "HEAD^{tree}"),
                "suggestion_ids": ["suggestion-1"], "batch_commits": [candidate]}
    if failure == "ancestry":
        manifest["base_sha"], manifest["candidate_sha"] = candidate, base
        manifest["batch_commits"] = [base]
    if failure == "tree":
        manifest["candidate_tree"] = git("rev-parse", base + "^{tree}")
    authority = frozen(base_sha=manifest["base_sha"], candidate_sha=manifest["candidate_sha"],
        batch_commits=tuple(manifest["batch_commits"]), manifest_hash="wrong" if failure == "hash" else digest(manifest))
    with pytest.raises(release.ReleaseError, match={"hash": "hash mismatch", "ancestry": "not descended", "tree": "tree mismatch"}[failure]):
        release.CandidateValidator(release.GitRefAdapter(repo), manifest)(authority)


@pytest.mark.parametrize("state", ["absent", "mismatch"])
def test_active_operation_manifest_must_be_available_and_exact(tmp_path, state):
    path = tmp_path / "manifest.json"
    if state == "mismatch":
        path.write_text("{}")
    ledger = SimpleNamespace(preparation_context=lambda: {"active_release": {"schema_version": 2, "manifest_hash": "wrong"}})
    with pytest.raises(release.ReleaseError, match="unavailable" if state == "absent" else "cannot be reproduced"):
        release.ProductionCandidateBuilder(ledger, object(), path).prepare_latest()


@pytest.mark.parametrize("context,message", [
    ({"batches": [{"suggestion_ids": []}]}, "no applied membership"),
    ({"batches": [{"suggestion_ids": ["s"]}]}, "lacks generator evidence"),
    ({"blocked_reason": "manual hold"}, "frontier blocked"),
])
def test_candidate_builder_rejects_bad_frontier_before_git(tmp_path, context, message):
    builder = release.ProductionCandidateBuilder(SimpleNamespace(preparation_context=lambda: context), object(), tmp_path / "manifest.json")
    with pytest.raises(release.ReleaseError, match=message):
        builder.prepare_latest()
    assert not builder.manifest_path.exists()


@pytest.mark.parametrize("sha,pages,worker,message", [
    ("not-a-sha", "pages", "worker", "exact candidate SHA"),
    ("a" * 40, "", "worker", "complete provider pair"),
    ("a" * 40, "pages", "", "complete provider pair"),
])
def test_registry_rejects_invalid_pair_without_creating_file(tmp_path, sha, pages, worker, message):
    registry = release.RecoveryRegistry(tmp_path / "registry.json")
    with pytest.raises(release.ReleaseError, match=message):
        registry.record_pair(sha, pages, worker)
    assert not registry.path.exists()


def test_registry_pair_conflict_preserves_exact_bytes_and_existing_recovery(tmp_path):
    registry = release.RecoveryRegistry(tmp_path / "registry.json")
    registry.record_pair("a" * 40, "pages", "worker")
    before = registry.path.read_bytes()
    with pytest.raises(release.ReleaseError, match="complete pair conflict"):
        registry.record_pair("a" * 40, "different", "worker")
    assert registry.path.read_bytes() == before
    assert registry.pair_state("b" * 40) == (False, None)
    for target, artifact in [("unknown", "id"), ("pages", "")]:
        with pytest.raises(release.ReleaseError, match="exact deployable"):
            registry.record_target("b" * 40, target, artifact)
    assert registry.path.read_bytes() == before


@pytest.mark.parametrize("pair,target", [({}, "pages"), ({"pages_deployment_id": "p"}, "worker"), ({"pages_deployment_id": "p"}, "unknown")])
def test_restorer_requires_recorded_exact_target(pair, target):
    restorer = release.RecordedPairRestorer({"base": pair}, None, None)
    with pytest.raises(release.ReleaseError, match="exact recorded"):
        restorer.artifact("base", target)


def source():
    return {"source_ref": "data/home.json#lead", "original_text": "old prose", "review_revision_id": "revision",
            "operations": [{"id": "op", "source_ref": "data/home.json#lead", "old_text": "old",
                            "new_text": "new", "base_range": [0, 3]}],
            "decisions": [{"operation_id": "op", "decision": "accepted"}]}


@pytest.mark.parametrize("mutation,message", [
    (lambda s: s.update(original_text=None), "durable source evidence"),
    (lambda s: s["operations"][0].update(source_ref="elsewhere"), "bind its durable source"),
    (lambda s: s["operations"].append(dict(s["operations"][0])), "duplicate operation identity"),
    (lambda s: s["decisions"][0].update(decision="maybe"), "unknown submitted decision"),
    (lambda s: s["operations"][0].update(base_range=None), "lacks a base range"),
    (lambda s: s["operations"][0].update(base_range=[3, 0]), "invalid base range"),
    (lambda s: s["operations"][0].update(old_text="", context_before=[], context_after=[]), "unique anchor"),
])
def test_materializer_rejects_ambiguous_or_unbound_review_evidence(mutation, message):
    value = source()
    mutation(value)
    with pytest.raises(release.ReleaseError, match=message):
        release.AcceptedOnlyMaterializer().materialize([value])


def test_materializer_rejects_partially_accepted_local_prose_group():
    value = source()
    value["operations"][0]["group_id"] = "group"
    value["operations"].append({**value["operations"][0], "id": "op-other"})
    value["decisions"].append({"operation_id": "op-other", "decision": "rejected"})
    with pytest.raises(release.ReleaseError, match="partial structural group"):
        release.AcceptedOnlyMaterializer().materialize([value])


class FilePipeline:
    """Local pipeline boundary: actual JSON parsing, with explicit failure stage."""
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def regenerate_map(self, root):
        self.calls.append("map")
        return {"data/" + path.name + "#lead": {"kind": "json_scalar", "json_path": "lead"}
                for path in pathlib.Path(root, "data").glob("*.json")}

    def step(self, name, root):
        self.calls.append(name)
        for path in pathlib.Path(root, "data").glob("*.json"):
            assert isinstance(json.loads(path.read_text()), dict)
        return name != self.fail, {"step": name}

    def validate(self, root):
        return self.step("validate", root)

    def build(self, root):
        return self.step("build", root)

    def parity(self, root):
        return self.step("parity", root)

    def generator_identity(self, root):
        return "fixture-generator"


def test_projection_stage_discards_first_success_when_later_json_path_is_missing(tmp_path):
    (tmp_path / "data").mkdir()
    first, second = tmp_path / "data/a.json", tmp_path / "data/z.json"
    first.write_text('{"lead":"old prose"}\n')
    second.write_text('{"other":"unchanged"}\n')
    snapshots = {p: p.read_bytes() for p in (first, second)}
    patches = tuple(release.ProjectionPatch("data/" + p.name + "#lead", "old prose", "new prose", (p.stem,), "revision") for p in (first, second))
    pipeline = FilePipeline()
    with pytest.raises(release.ReleaseError, match="ambiguous or invalid"):
        release.ProjectionTreeWriter(pipeline).write(tmp_path, release.MaterializedProjection(patches, (), (), ("a", "z")))
    assert {p: p.read_bytes() for p in snapshots} == snapshots
    assert pipeline.calls == ["map"]


@pytest.mark.parametrize("stage,expected", [
    ("validate", ["map", "map", "validate"]),
    ("build", ["map", "map", "validate", "build"]),
    ("parity", ["map", "map", "validate", "build", "parity"]),
])
def test_real_projection_write_stops_at_failed_pipeline_stage(tmp_path, stage, expected):
    (tmp_path / "data").mkdir()
    path = tmp_path / "data/home.json"
    path.write_text('{"lead":"old prose"}')
    projection = release.AcceptedOnlyMaterializer().materialize([source()])
    pipeline = FilePipeline(stage)
    with pytest.raises(release.ReleaseError, match="projected candidate .* failed"):
        release.ProjectionTreeWriter(pipeline).write(tmp_path, projection)
    assert json.loads(path.read_text()) == {"lead": "new prose"}
    assert pipeline.calls == expected


@pytest.mark.parametrize("structural", [False, True])
def test_projection_writer_rejects_missing_source_before_staging(tmp_path, structural):
    (tmp_path / "data").mkdir()
    patches = () if structural else (release.ProjectionPatch("data/missing.json#lead", "old", "new", ("op",), "revision"),)
    operations = ({"id": "op", "source_ref": "data/missing.json#lead", "op": "delete"},) if structural else ()
    with pytest.raises(release.ReleaseError, match="source is absent"):
        release.ProjectionTreeWriter(FilePipeline()).write(tmp_path,
            release.MaterializedProjection(patches, operations, (), ("op",)))
    assert list((tmp_path / "data").iterdir()) == []


@pytest.mark.parametrize("status,body,success", [
    (400, b'{"errors":[{"code":8000039}]}', True),
    (403, b'{"errors":[{"code":8000039}]}', False),
    (400, b'{"errors":[{"code":123}]}', False),
    (500, b"invalid json", False),
])
def test_pages_rollback_only_treats_exact_already_active_response_as_success(tmp_path, status, body, success):
    def opener(request, timeout):
        assert request.method == "POST"
        assert request.full_url.endswith("/deployments/deployment-1/rollback")
        raise urllib.error.HTTPError(request.full_url, status, "fixture", {}, io.BytesIO(body))
    adapter = release.WranglerPagesAdapter("project", str(tmp_path / "dist"), "https://site.invalid",
        candidate_root=tmp_path, account_id="account", api_token="fixture", opener=opener)
    if success:
        assert adapter.restore("deployment-1") is None
    else:
        with pytest.raises(release.ReleaseError, match="API rejected the exact deployment"):
            adapter.restore("deployment-1")


@pytest.mark.parametrize("payload", [{"success": False}, {"success": True, "result": {"id": "wrong"}}, {"success": True}])
def test_pages_rollback_requires_positive_exact_id_receipt(tmp_path, payload):
    adapter = release.WranglerPagesAdapter("project", str(tmp_path / "dist"), "https://site.invalid",
        candidate_root=tmp_path, account_id="account", api_token="fixture",
        opener=lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(release.ReleaseError, match="did not bind the exact deployment"):
        adapter.restore("deployment-1")


@pytest.mark.parametrize("target", ["../escape", "", "x" * 129])
def test_pages_rollback_invalid_id_cannot_reach_provider(tmp_path, target):
    adapter = release.WranglerPagesAdapter("project", str(tmp_path / "dist"), "https://site.invalid",
        candidate_root=tmp_path, account_id="account", api_token="fixture",
        opener=lambda *a, **k: pytest.fail("invalid authority reached transport"))
    with pytest.raises(release.ReleaseError, match="bounded API authority"):
        adapter.restore(target)


def test_ledger_client_serializes_renew_transition_prepare_and_status_bindings():
    requests = []
    authority = {**dataclasses.asdict(frozen()), "batches": [{"commit_sha": "b" * 40}]}
    def opener(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps({"ok": True, "context": {"batches": []}, "release": authority}).encode())
    client = release.LedgerHTTP("https://ledger.invalid/", "fixture", opener=opener)
    assert client.prepare({"id": "frozen"})["ok"] is True
    assert client.preparation_context() == {"batches": []}
    assert client.get_release("release /1").candidate_sha == "b" * 40
    client.transition("release-1", "executing", {"candidate_sha": "b" * 40}, "fence")
    client.renew("release-1", "fence")
    client.renew("release-1", "fence", 300000)
    assert requests[2][0].full_url.endswith("status?id=release%20%2F1")
    assert json.loads(requests[3][0].data) == {"id": "release-1", "state": "executing",
        "detail": {"candidate_sha": "b" * 40}, "fencing_token": "fence"}
    assert json.loads(requests[4][0].data) == {"id": "release-1", "fencing_token": "fence"}
    assert json.loads(requests[5][0].data)["lease_ms"] == 300000
    assert all(timeout == 30 for _, timeout in requests)


@pytest.mark.parametrize("context,message", [
    ({"projection": {"blocked_reason": "review hold"}}, "projection blocked"),
    ({"projection": {"sources": [{"prod_base": ""}], "review_receipts": [{}]}}, "verified production base"),
])
def test_projection_builder_blocks_invalid_review_before_checkout(tmp_path, context, message):
    with pytest.raises(release.ReleaseError, match=message):
        release.AcceptedProjectionCandidateBuilder(object(), tmp_path / "manifest.json").build(context)
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.parametrize("context", [{}, {"projection": {"sources": [{}]}}, {"projection": {"review_receipts": [{}]}}])
def test_projection_builder_empty_review_is_noop(tmp_path, context):
    assert release.AcceptedProjectionCandidateBuilder(object(), tmp_path / "manifest.json").build(context) is None
    assert not (tmp_path / "manifest.json").exists()


def test_candidate_builder_rejected_prepare_does_not_publish_manifest(local_git, tmp_path):
    repo, git, base, candidate = local_git
    context = {"base_sha": base, "batches": [{"batch_id": "batch", "commit_sha": candidate,
               "suggestion_ids": ["s"], "generator_id": "generator"}]}
    ledger = SimpleNamespace(preparation_context=lambda: context, prepare=lambda binding: {"ok": False, "reason": "stale fence"})
    path = tmp_path / "manifest.json"
    with pytest.raises(release.ReleaseError, match="ledger rejected preparation: stale fence"):
        release.ProductionCandidateBuilder(ledger, release.GitRefAdapter(repo), path).prepare_latest()
    assert not path.exists()
    assert git("status", "--porcelain") == ""


def test_executor_candidate_validation_fences_before_provider_access():
    events = []
    def validate(authority):
        raise release.ReleaseError("candidate tree mismatch")
    ledger = SimpleNamespace(transition=lambda identifier, state, detail, fence: events.append((state, detail)) or {"ok": True})
    executor = release.ProductionExecutor(ledger, object(), object(), candidate_validator=validate)
    with pytest.raises(release.ReleaseError, match="candidate tree mismatch; release is fenced"):
        executor.run_once(frozen())
    assert events == [("failed_fenced", {"reason": "ReleaseError"})]


@pytest.mark.parametrize("state", ["failed_fenced", "restoring", "complete"])
def test_executor_non_executable_state_does_not_access_adapters(state):
    assert release.ProductionExecutor(object(), object(), object()).run_once(frozen(state=state)) is None


@pytest.mark.parametrize("live,after", [("foreign", "a" * 40), ("b" * 40, "still-candidate")])
def test_restore_fences_unknown_or_unrestored_provenance(live, after):
    events, restores = [], []
    worker = SimpleNamespace(provenance=lambda: live if not restores else after,
                             restore=lambda artifact: restores.append(artifact))
    ledger = SimpleNamespace(transition=lambda identifier, state, detail, fence: events.append(state) or {"ok": True},
                             renew=lambda *args: {"ok": True})
    restorer = release.RecordedPairRestorer({"a" * 40: {"pages_deployment_id": "pages-base", "worker_version_id": "worker-base"}}, None, None)
    executor = release.ProductionExecutor(ledger, object(), worker, restorer=restorer)
    with pytest.raises(release.ReleaseError, match="restoration failed; release remains fenced"):
        executor.restore_recorded_base(frozen(state="restoring"))
    assert events == ["failed_fenced"]
    assert restores == ([] if live == "foreign" else ["worker-base"])
