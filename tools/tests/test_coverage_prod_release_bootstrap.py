"""Bootstrap trust boundaries and offline git/receipt integration."""
import dataclasses
import datetime
import json
import pathlib
import stat
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
import prod_release_bootstrap as bootstrap


@pytest.fixture
def request_pair(tmp_path, monkeypatch):
    for key in tuple(bootstrap.os.environ):
        if key.startswith("SONSTENG_"):
            monkeypatch.delenv(key)
    for key, value in {"AUTHORITY": "local-operator", "OPERATOR_ID": "fixture-operator", "AUTHORITY_CHANNEL": "console"}.items():
        monkeypatch.setenv("SONSTENG_PROD_BOOTSTRAP_" + key, value)
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "site").mkdir()
    (repo / "site/index.html").write_text("exact release fixture")
    (repo / "config.txt").write_text("{}")
    git("add", ".")
    git("commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    return bootstrap.BootstrapRequest(
        repo=repo, source_sha=sha, candidate_sha=sha,
        pages_deployment_id="pages-fixture", worker_version_id="worker-fixture",
        expected_pages_provenance=sha, expected_worker_provenance=sha,
        recovery_registry=tmp_path / "state/registry.json",
        receipt_log=tmp_path / "state/receipts.jsonl",
        pages_artifact=repo / "site", worker_config=repo / "config.txt",
        pages_provenance_url="https://example.invalid/pages",
        worker_provenance_url="https://example.invalid/worker")


@pytest.mark.parametrize("field,value,message", [
    ("expected_pages_provenance", "different", "provenance evidence"),
    ("expected_worker_provenance", "different", "provenance evidence"),
    ("worker_version_id", "invalid identifier", "provider identifiers"),
    ("source_sha", "", "candidate/source SHA"),
])
def test_invalid_bindings_leave_no_state(request_pair, field, value, message):
    with pytest.raises(bootstrap.ReleaseError, match=message):
        bootstrap.run_bootstrap(dataclasses.replace(request_pair, **{field: value}))
    assert not request_pair.recovery_registry.parent.exists()


@pytest.mark.parametrize("field", ["OPERATOR_ID", "AUTHORITY_CHANNEL"])
@pytest.mark.parametrize("value", ["", "has space", "x" * 129])
def test_invalid_operator_identity_fails_before_state(request_pair, monkeypatch, field, value):
    monkeypatch.setenv("SONSTENG_PROD_BOOTSTRAP_" + field, value)
    with pytest.raises(bootstrap.ReleaseError, match="operator identity and authority channel"):
        bootstrap.run_bootstrap(request_pair)
    assert not request_pair.recovery_registry.parent.exists()


@pytest.mark.parametrize("case", ["missing", "non-git", "registry-link", "receipt-link", "lock-link"])
def test_untrusted_paths_preserve_external_file(request_pair, tmp_path, case):
    external = tmp_path / "external.txt"
    external.write_text("must remain unchanged")
    request = request_pair
    message = "symlink"
    if case == "missing":
        request = dataclasses.replace(request, worker_config=request.repo / "absent.txt")
        message = "path is missing"
    elif case == "non-git":
        request = dataclasses.replace(request, repo=tmp_path)
        message = "not a git checkout"
    else:
        request.recovery_registry.parent.mkdir()
        target = {"registry-link": request.recovery_registry, "receipt-link": request.receipt_log,
                  "lock-link": pathlib.Path(str(request.recovery_registry) + ".bootstrap.lock")}[case]
        target.symlink_to(external)
    with pytest.raises(bootstrap.ReleaseError, match=message):
        bootstrap.run_bootstrap(request)
    assert external.read_text() == "must remain unchanged"


def test_partial_registry_is_unchanged_and_no_provider_is_called(request_pair):
    request_pair.recovery_registry.parent.mkdir()
    partial = json.dumps({request_pair.source_sha: {"pages_deployment_id": "pages-fixture"}})
    request_pair.recovery_registry.write_text(partial)
    with pytest.raises(bootstrap.ReleaseError, match="partial recovery pair conflicts"):
        bootstrap.run_bootstrap(request_pair)
    assert request_pair.recovery_registry.read_text() == partial
    assert not request_pair.receipt_log.exists()


@pytest.mark.parametrize("case", ["missing", "symlink", "parent-escape"])
def test_isolated_checkout_rejects_missing_and_redirected_artifacts(request_pair, tmp_path, case):
    root = tmp_path / "isolated"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    configured = request_pair.pages_artifact
    if case == "symlink":
        (root / "site").symlink_to(outside, target_is_directory=True)
    elif case == "parent-escape":
        (outside / "index.html").write_text("outside")
        (root / "site").symlink_to(outside, target_is_directory=True)
        configured = configured / "index.html"
    with pytest.raises(bootstrap.ReleaseError, match="isolated bootstrap path"):
        bootstrap._checkout_path(root, request_pair.repo, configured)


def test_default_adapters_resolve_exact_checkout_without_provider_calls(request_pair, monkeypatch):
    monkeypatch.setenv("SONSTENG_PROD_CLOUDFLARE_ACCOUNT_ID", "fixture-account")
    monkeypatch.setenv("SONSTENG_PROD_CLOUDFLARE_API_TOKEN", "fake-fixture-token")
    with bootstrap.GitRefAdapter(request_pair.repo).isolated_checkout(request_pair.source_sha) as root:
        pages, worker = bootstrap._default_targets(root, request_pair)
        assert pages.artifact_dir == pathlib.Path(root) / "site"
        assert worker.config == pathlib.Path(root) / "config.txt"
        assert pages.account_id == "fixture-account"
        assert pages.candidate_root == worker.candidate_root == pathlib.Path(root).resolve()
        assert pages.provenance_url == request_pair.pages_provenance_url
        assert worker.provenance_url == request_pair.worker_provenance_url


def test_main_env_to_real_checkout_registry_and_private_receipts(request_pair, monkeypatch, capsys):
    request = request_pair
    mapping = {
        "REPO": request.repo, "BOOTSTRAP_SOURCE_SHA": request.source_sha,
        "BOOTSTRAP_CANDIDATE_SHA": request.candidate_sha,
        "BOOTSTRAP_PAGES_DEPLOYMENT_ID": request.pages_deployment_id,
        "BOOTSTRAP_WORKER_VERSION_ID": request.worker_version_id,
        "BOOTSTRAP_PAGES_PROVENANCE": request.source_sha,
        "BOOTSTRAP_WORKER_PROVENANCE": request.source_sha,
        "RECOVERY_REGISTRY": request.recovery_registry,
        "BOOTSTRAP_RECEIPT_LOG": request.receipt_log,
        "PAGES_ARTIFACT": request.pages_artifact, "WORKER_CONFIG": request.worker_config,
        "PAGES_PROJECT": "fixture-project", "PAGES_PROVENANCE_URL": request.pages_provenance_url,
        "WORKER_PROVENANCE_URL": request.worker_provenance_url, "PAGES_BRANCH": "release",
    }
    for key, value in mapping.items():
        monkeypatch.setenv("SONSTENG_PROD_" + key, str(value))
    monkeypatch.setenv("SONSTENG_NEW_WORKER_ACCEPTS_OLD_PAGES", "true")
    calls, roots = [], []
    class OfflineTarget:
        def __init__(self, name):
            self.name = name
        def provenance(self):
            return request.source_sha
        def restore(self, provider_id):
            calls.append((self.name, provider_id))
    def targets(root, parsed):
        roots.append(pathlib.Path(root))
        assert (pathlib.Path(root) / "site/index.html").read_text() == "exact release fixture"
        assert parsed.pages_project == "fixture-project"
        assert parsed.pages_branch == "release"
        return OfflineTarget("pages"), OfflineTarget("worker")
    run = bootstrap.run_bootstrap
    now = datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(bootstrap, "run_bootstrap", lambda parsed: run(parsed, target_factory=targets, now=now))
    assert bootstrap.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "replay": False, "source_sha": request.source_sha}
    receipts = [json.loads(line) for line in request.receipt_log.read_text().splitlines()]
    assert [item["event"] for item in receipts] == ["legacy_pair_bootstrap_started", "legacy_pair_bootstrap_verified"]
    assert all(item["recorded_at"] == now.isoformat() for item in receipts)
    assert bootstrap.RecoveryRegistry(request.recovery_registry).pair(request.source_sha) == {
        "pages_deployment_id": request.pages_deployment_id, "worker_version_id": request.worker_version_id}
    assert [name for name, _ in calls] == ["worker", "pages", "pages", "worker"]
    assert stat.S_IMODE(request.receipt_log.stat().st_mode) == 0o600
    assert stat.S_IMODE(pathlib.Path(str(request.recovery_registry) + ".bootstrap.lock").stat().st_mode) == 0o600
    assert all(not root.exists() for root in roots)


@pytest.mark.parametrize("failure_stage", ["reactivation", "restoration", "incompatible"])
def test_failed_drill_never_records_pair_and_cleans_checkout(request_pair, failure_stage):
    calls, roots = [], []
    request = request_pair
    if failure_stage == "incompatible":
        request = dataclasses.replace(request, old_worker_accepts_new_pages=False,
                                      new_worker_accepts_old_pages=False)
    class OfflineTarget:
        def __init__(self, name):
            self.name = name
            self.sha = request.source_sha
        def provenance(self):
            return self.sha
        def restore(self, provider_id):
            calls.append((self.name, provider_id))
            limit = 2 if failure_stage == "reactivation" else 3
            if len(calls) == limit:
                self.sha = "foreign-build"
    def targets(root, _request):
        roots.append(pathlib.Path(root))
        return OfflineTarget("pages"), OfflineTarget("worker")
    with pytest.raises(bootstrap.ReleaseError):
        bootstrap.run_bootstrap(request, target_factory=targets)
    assert len(calls) == {"reactivation": 2, "restoration": 4, "incompatible": 0}[failure_stage]
    assert not request.recovery_registry.exists()
    assert not request.receipt_log.exists()
    assert roots and all(not root.exists() for root in roots)


def test_parser_requires_inputs_and_reports_usage(monkeypatch, capsys):
    for key in tuple(bootstrap.os.environ):
        if key.startswith("SONSTENG_"):
            monkeypatch.delenv(key)
    with pytest.raises(SystemExit) as caught:
        bootstrap.main([])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "--source-sha" in error and "--worker-config" in error


def test_receipt_append_preserves_prior_record_and_tightens_permissions(tmp_path):
    path = tmp_path / "receipts.jsonl"
    path.write_text('{"prior":true}\n')
    path.chmod(0o644)
    receipt = {"operator": "fixture", "text": "unicode λ and newline\ninside value"}
    bootstrap._append_receipt(path, receipt)
    assert [json.loads(line) for line in path.read_text().splitlines()] == [{"prior": True}, receipt]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_receipt_write_failure_closes_descriptor(tmp_path, monkeypatch):
    captured = []
    def fail_write(descriptor, _payload):
        captured.append(descriptor)
        raise OSError("fixture disk failure")
    monkeypatch.setattr(bootstrap.os, "write", fail_write)
    with pytest.raises(OSError, match="fixture disk failure"):
        bootstrap._append_receipt(tmp_path / "receipts.jsonl", {"event": "fixture"})
    assert len(captured) == 1
    with pytest.raises(OSError):
        bootstrap.os.fstat(captured[0])
