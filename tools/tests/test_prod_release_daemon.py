import importlib.util
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

TOOLS = pathlib.Path(__file__).parents[1]
sys.path.insert(0,str(TOOLS))
spec = importlib.util.spec_from_file_location("prod_release_daemon",TOOLS / "prod_release_daemon.py")
daemon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daemon)
digest_spec = importlib.util.spec_from_file_location("print_prod_release_config_digest",
    TOOLS / "print_prod_release_config_digest.py")
digest_tool = importlib.util.module_from_spec(digest_spec)
digest_spec.loader.exec_module(digest_tool)

from prod_release_executor import (GitRefAdapter, RecoveryRegistry, RecordedPairRestorer,
    WranglerPagesAdapter, WranglerWorkerAdapter)  # noqa: E402


def test_runtime_config_digest_excludes_credentials_and_binds_release_controls(monkeypatch):
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_BEARER", "super-secret")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "provider-secret")
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_MODE", "routine")
    monkeypatch.setenv("SONSTENG_PROD_LEDGER_URL", "https://ledger.example")

    digest = daemon.runtime_config_digest()

    assert len(digest) == 64
    assert "secret" not in digest
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_MODE", "canary")
    assert daemon.runtime_config_digest() != digest


def test_digest_tool_reads_only_allowlisted_nonsecret_environment(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("SONSTENG_PROD_RELEASE_MODE=routine\n"
        "SONSTENG_PROD_LEDGER_URL=https://ledger.example\n"
        "SONSTENG_PROD_RELEASE_BEARER=sentinel-secret\n"
        "CLOUDFLARE_API_TOKEN=provider-secret\n", encoding="utf-8")
    values = digest_tool.read_nonsecret_config(env_file)
    assert values == {"SONSTENG_PROD_RELEASE_MODE":"routine",
        "SONSTENG_PROD_LEDGER_URL":"https://ledger.example"}
    assert "secret" not in daemon.runtime_config_digest(values)


def test_enabled_tick_rejects_config_digest_before_credentials_or_imports(monkeypatch):
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_ENABLED", "true")
    monkeypatch.setenv("SONSTENG_PROD_EXPECTED_CONFIG_DIGEST", "0" * 64)
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_BEARER", "must-not-be-read")

    with pytest.raises(RuntimeError, match="configuration digest mismatch"):
        daemon.main([])


def test_canary_mode_requires_exact_release_and_never_prepares(monkeypatch, tmp_path):
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_ENABLED", "true")
    monkeypatch.setenv("SONSTENG_PROD_RELEASE_MODE", "canary")
    monkeypatch.delenv("SONSTENG_PROD_CANARY_RELEASE_ID", raising=False)
    monkeypatch.setenv("SONSTENG_PROD_EXPECTED_CONFIG_DIGEST", daemon.runtime_config_digest())

    with pytest.raises(RuntimeError, match="exact release id"):
        daemon.main([])


def git(repo,*args):
    return subprocess.run(["git",*args],cwd=repo,check=True,capture_output=True,text=True).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo,"init")
    git(repo,"config","user.email","test@example.invalid")
    git(repo,"config","user.name","Test")
    (repo / "app/worker").mkdir(parents=True)
    (repo / "site").mkdir()
    (repo / "app/worker/wrangler.jsonc").write_text(
        '{"env":{"production":{"name":"base-worker","routes":["base.example"]}}}\n')
    (repo / "site/index.html").write_text("base site\n")
    git(repo,"add",".")
    git(repo,"commit","-m","base production config")
    base = git(repo,"rev-parse","HEAD")
    (repo / "app/worker/wrangler.jsonc").write_text(
        '{"env":{"production":{"name":"current-worker","routes":["current.example"]}}}\n')
    (repo / "site/index.html").write_text("current site\n")
    git(repo,"commit","-am","advance production config")
    return repo,base


def test_restore_materializes_recorded_base_config_and_exact_provider_ids(tmp_path):
    repo,base = make_repo(tmp_path)
    registry_path = tmp_path / "registry.json"
    registry = RecoveryRegistry(registry_path)
    registry.record_target(base,"pages","pages-base-id")
    registry.record_target(base,"worker","worker-base-id")
    release = SimpleNamespace(base_sha=base)
    args = SimpleNamespace(restore_release_id="release-1",recovery_registry=str(registry_path),
        repo=str(repo),pages_artifact=str(repo / "site"),
        worker_config=str(repo / "app/worker/wrangler.jsonc"),pages_project="stable-pages",
        pages_provenance_url="https://pages.example",worker_provenance_url="https://worker.example",
        pages_branch="main")
    commands,roots = [],[]
    class Result:
        stdout = ""
    def run(argv,**kwargs):
        commands.append((argv,kwargs))
        return Result()

    def pages_factory(project,path,url,*,candidate_root,production_branch):
        assert production_branch == "main"
        roots.append(pathlib.Path(candidate_root))
        assert (pathlib.Path(path) / "index.html").read_text() == "base site\n"
        return WranglerPagesAdapter(project,path,url,candidate_root=candidate_root,
            production_branch=production_branch,run=run)
    def worker_factory(path,url,*,candidate_root):
        roots.append(pathlib.Path(candidate_root))
        assert "base-worker" in pathlib.Path(path).read_text()
        assert "current-worker" not in pathlib.Path(path).read_text()
        return WranglerWorkerAdapter(path,url,candidate_root=candidate_root,run=run)
    class Executor:
        def __init__(self,_ledger,_pages,_worker,_gate,*,restorer,recovery_registry):
            self.restorer = restorer
        def restore_recorded_base(self,item):
            self.restorer.restore(item.base_sha,("worker","pages"))

    daemon._restore_recorded_release(args,SimpleNamespace(claim_restore=lambda _id:release),object(),
        GitRefAdapter(repo),registry_factory=RecoveryRegistry,pages_factory=pages_factory,
        worker_factory=worker_factory,restorer_factory=RecordedPairRestorer,
        executor_factory=Executor,git_factory=GitRefAdapter)
    assert commands[0][0][4] == "worker-base-id"
    assert pathlib.Path(commands[0][0][6]).name == "wrangler.jsonc"
    assert commands[1][0][5] == "pages-base-id"
    assert all(argv[:2] == ["npx","wrangler@4"] for argv,_kwargs in commands)
    assert all(pathlib.Path(kwargs["cwd"]) in roots for _argv,kwargs in commands)
    assert roots and all(root != repo and not root.exists() for root in roots)


def test_restore_checkout_cleans_up_on_error_and_paths_fail_closed(tmp_path):
    repo,base = make_repo(tmp_path)
    registry_path = tmp_path / "registry.json"
    registry = RecoveryRegistry(registry_path)
    registry.record_target(base,"pages","pages-base-id")
    registry.record_target(base,"worker","worker-base-id")
    release = SimpleNamespace(base_sha=base)
    args = SimpleNamespace(restore_release_id="release-1",recovery_registry=str(registry_path),
        repo=str(repo),pages_artifact=str(repo / "site"),
        worker_config=str(repo / "app/worker/wrangler.jsonc"),pages_project="stable-pages",
        pages_provenance_url="https://pages.example",worker_provenance_url="https://worker.example",pages_branch="main")
    roots = []
    def fail_pages(*_args,**kwargs):
        roots.append(pathlib.Path(kwargs["candidate_root"]))
        raise RuntimeError("factory failed")
    with pytest.raises(RuntimeError,match="factory failed"):
        daemon._restore_recorded_release(args,SimpleNamespace(claim_restore=lambda _id:release),object(),
            GitRefAdapter(repo),registry_factory=RecoveryRegistry,pages_factory=fail_pages,
            worker_factory=lambda *_a,**_k:None,restorer_factory=RecordedPairRestorer,
            executor_factory=lambda *_a,**_k:None,git_factory=GitRefAdapter)
    assert roots and not roots[0].exists()
    with pytest.raises(RuntimeError,match="outside"):
        daemon._path_in_checkout(repo,repo,tmp_path / "foreign.json")
    with pytest.raises(RuntimeError,match="missing"):
        daemon._path_in_checkout(repo,repo,repo / "missing.json")
