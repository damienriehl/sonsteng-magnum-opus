import json
import pathlib
import subprocess
import sys

import pytest


TOOLS = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(TOOLS))
import prod_release_bootstrap as bootstrap  # noqa: E402
from prod_release_executor import RecoveryRegistry, ReleaseError  # noqa: E402


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "app/worker").mkdir(parents=True)
    (repo / "site").mkdir()
    (repo / "app/worker/wrangler.jsonc").write_text("{}\n", encoding="utf-8")
    (repo / "site/index.html").write_text("legacy\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "legacy pair")
    return repo, git(repo, "rev-parse", "HEAD")


class Target:
    def __init__(self, name, sha, calls, provider_sha=None):
        self.name = name
        self.sha = sha
        self.calls = calls
        self.provider_sha = provider_sha or sha

    def provenance(self):
        return self.sha

    def restore(self, provider_id):
        self.calls.append((self.name, provider_id))
        self.sha = self.provider_sha


def authority_env(monkeypatch):
    monkeypatch.setenv("SONSTENG_PROD_BOOTSTRAP_AUTHORITY", "local-operator")
    monkeypatch.setenv("SONSTENG_PROD_BOOTSTRAP_OPERATOR_ID", "operator-17")
    monkeypatch.setenv("SONSTENG_PROD_BOOTSTRAP_AUTHORITY_CHANNEL", "local-console")
    monkeypatch.delenv("SONSTENG_PROD_RELEASE_BEARER", raising=False)
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_ENABLED", "false")


def test_bootstrap_records_complete_pair_after_exact_sha_drill(tmp_path, monkeypatch):
    authority_env(monkeypatch)
    repo, sha = make_repo(tmp_path)
    registry_path = tmp_path / "known-good.json"
    receipt_path = tmp_path / "bootstrap.jsonl"
    calls = []
    roots = []

    def targets(root, _args):
        roots.append(pathlib.Path(root))
        assert pathlib.Path(root) != repo
        assert (pathlib.Path(root) / "site/index.html").read_text() == "legacy\n"
        return Target("pages", sha, calls), Target("worker", sha, calls)

    result = bootstrap.run_bootstrap(
        bootstrap.BootstrapRequest(
            repo=repo,
            source_sha=sha,
            candidate_sha=sha,
            pages_deployment_id="pages-legacy-1",
            worker_version_id="12345678-1234-4234-8234-123456789abc",
            expected_pages_provenance=sha,
            expected_worker_provenance=sha,
            recovery_registry=registry_path,
            receipt_log=receipt_path,
            pages_artifact=repo / "site",
            worker_config=repo / "app/worker/wrangler.jsonc",
        ),
        target_factory=targets,
    )

    pair = RecoveryRegistry(registry_path).pair(sha)
    assert pair == {
        "pages_deployment_id": "pages-legacy-1",
        "worker_version_id": "12345678-1234-4234-8234-123456789abc",
    }
    assert calls == [
        ("pages", "pages-legacy-1"),
        ("worker", "12345678-1234-4234-8234-123456789abc"),
        ("worker", "12345678-1234-4234-8234-123456789abc"),
        ("pages", "pages-legacy-1"),
    ]
    assert roots and all(not root.exists() for root in roots)
    receipt = json.loads(receipt_path.read_text().splitlines()[0])
    assert receipt["event"] == "legacy_pair_bootstrap_verified"
    assert receipt["operator_id"] == "operator-17"
    assert receipt["authority_channel"] == "local-console"
    assert receipt["source_sha"] == sha
    assert "pages-legacy-1" not in receipt_path.read_text()
    assert "12345678-1234" not in receipt_path.read_text()
    assert result["replay"] is False


def test_bootstrap_exact_replay_is_idempotent_but_change_conflicts(tmp_path, monkeypatch):
    authority_env(monkeypatch)
    repo, sha = make_repo(tmp_path)
    request = bootstrap.BootstrapRequest(
        repo=repo, source_sha=sha, candidate_sha=sha,
        pages_deployment_id="pages-1", worker_version_id="worker-1",
        expected_pages_provenance=sha, expected_worker_provenance=sha,
        recovery_registry=tmp_path / "registry.json",
        receipt_log=tmp_path / "receipts.jsonl",
        pages_artifact=repo / "site",
        worker_config=repo / "app/worker/wrangler.jsonc",
    )
    factory = lambda _root, _args: (Target("pages", sha, []), Target("worker", sha, []))
    assert bootstrap.run_bootstrap(request, target_factory=factory)["replay"] is False
    assert bootstrap.run_bootstrap(request, target_factory=factory)["replay"] is True
    changed = bootstrap.dataclasses.replace(request, worker_version_id="worker-2")
    with pytest.raises(ReleaseError, match="complete pair conflict"):
        bootstrap.run_bootstrap(changed, target_factory=factory)
    assert len(request.receipt_log.read_text().splitlines()) == 1


def test_registry_never_exposes_partial_pair_and_partial_legacy_state_fails(tmp_path):
    registry = RecoveryRegistry(tmp_path / "registry.json")
    sha = "a" * 40
    registry.path.write_text(json.dumps({sha: {"pages_deployment_id": "pages-1"}}))
    assert registry.pair(sha) is None
    assert sha not in registry.pairs()
    with pytest.raises(ReleaseError, match="partial recovery pair"):
        registry.record_pair(sha, "pages-1", "worker-1")


def test_bootstrap_rejects_unauthorized_and_routine_service_paths(tmp_path, monkeypatch):
    repo, sha = make_repo(tmp_path)
    request = bootstrap.BootstrapRequest(
        repo=repo, source_sha=sha, candidate_sha=sha,
        pages_deployment_id="pages-1", worker_version_id="worker-1",
        expected_pages_provenance=sha, expected_worker_provenance=sha,
        recovery_registry=tmp_path / "registry.json",
        receipt_log=tmp_path / "receipts.jsonl",
        pages_artifact=repo / "site", worker_config=repo / "app/worker/wrangler.jsonc")
    monkeypatch.delenv("SONSTENG_PROD_BOOTSTRAP_AUTHORITY", raising=False)
    with pytest.raises(ReleaseError, match="operator authority"):
        bootstrap.run_bootstrap(request, target_factory=lambda *_: None)
    authority_env(monkeypatch)
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_BEARER", "canary-secret-value")
    with pytest.raises(ReleaseError, match="routine release-service"):
        bootstrap.run_bootstrap(request, target_factory=lambda *_: None)
    assert not request.recovery_registry.exists()
    assert not request.receipt_log.exists()
    monkeypatch.delenv("SONSTENG_PROD_RELEASE_BEARER")
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_ENABLED", "true")
    with pytest.raises(ReleaseError, match="config-off"):
        bootstrap.run_bootstrap(request, target_factory=lambda *_: None)


def test_bootstrap_rejects_paths_symlinks_ids_and_provenance_before_record(tmp_path, monkeypatch):
    authority_env(monkeypatch)
    repo, sha = make_repo(tmp_path)
    base = dict(
        repo=repo, source_sha=sha, candidate_sha=sha,
        pages_deployment_id="pages-1", worker_version_id="worker-1",
        expected_pages_provenance=sha, expected_worker_provenance=sha,
        recovery_registry=tmp_path / "registry.json",
        receipt_log=tmp_path / "receipts.jsonl",
        pages_artifact=repo / "site", worker_config=repo / "app/worker/wrangler.jsonc")
    with pytest.raises(ReleaseError, match="provider identifiers"):
        bootstrap.run_bootstrap(bootstrap.BootstrapRequest(**{**base, "pages_deployment_id": ""}))
    with pytest.raises(ReleaseError, match="candidate/source SHA"):
        bootstrap.run_bootstrap(bootstrap.BootstrapRequest(**{**base, "candidate_sha": "b" * 40}))
    outside = tmp_path / "outside"
    outside.write_text("x")
    with pytest.raises(ReleaseError, match="outside"):
        bootstrap.run_bootstrap(bootstrap.BootstrapRequest(**{**base, "worker_config": outside}))
    link = repo / "linked-site"
    link.symlink_to(repo / "site", target_is_directory=True)
    with pytest.raises(ReleaseError, match="symlink"):
        bootstrap.run_bootstrap(bootstrap.BootstrapRequest(**{**base, "pages_artifact": link}))

    calls = []
    def mismatch(_root, _args):
        return Target("pages", "foreign", calls), Target("worker", sha, calls)
    with pytest.raises(ReleaseError, match="live provenance mismatch"):
        bootstrap.run_bootstrap(bootstrap.BootstrapRequest(**base), target_factory=mismatch)
    assert not pathlib.Path(base["recovery_registry"]).exists()


def test_bootstrap_redacts_canary_secret_from_errors_receipts_and_cli_output(
        tmp_path, monkeypatch, capsys):
    authority_env(monkeypatch)
    repo, sha = make_repo(tmp_path)
    secret = "canary-secret-do-not-log"
    request = bootstrap.BootstrapRequest(
        repo=repo, source_sha=sha, candidate_sha=sha,
        pages_deployment_id="pages-1", worker_version_id="worker-1",
        expected_pages_provenance=sha, expected_worker_provenance=sha,
        recovery_registry=tmp_path / "registry.json",
        receipt_log=tmp_path / "receipts.jsonl",
        pages_artifact=repo / "site", worker_config=repo / "app/worker/wrangler.jsonc")

    class LeakyTarget(Target):
        def restore(self, _provider_id):
            raise subprocess.CalledProcessError(1, ["wrangler"], output=secret, stderr=secret)

    with pytest.raises(ReleaseError, match="provider operation failed") as caught:
        bootstrap.run_bootstrap(
            request,
            target_factory=lambda *_: (
                LeakyTarget("pages", sha, []), Target("worker", sha, [])),
        )
    assert secret not in str(caught.value)
    assert secret not in capsys.readouterr().out
    assert not request.receipt_log.exists()


def test_bootstrap_receipt_failure_never_exposes_recovery_authority(
        tmp_path, monkeypatch):
    authority_env(monkeypatch)
    repo, sha = make_repo(tmp_path)
    registry = tmp_path / "registry.json"
    request = bootstrap.BootstrapRequest(
        repo=repo, source_sha=sha, candidate_sha=sha,
        pages_deployment_id="pages-1", worker_version_id="worker-1",
        expected_pages_provenance=sha, expected_worker_provenance=sha,
        recovery_registry=registry, receipt_log=tmp_path / "receipts.jsonl",
        pages_artifact=repo / "site", worker_config=repo / "app/worker/wrangler.jsonc")
    monkeypatch.setattr(
        bootstrap, "_append_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        bootstrap.run_bootstrap(
            request,
            target_factory=lambda *_: (
                Target("pages", sha, []), Target("worker", sha, [])),
        )
    assert not registry.exists()
