"""Release activation gates and isolated-checkout trust boundaries, offline."""
import os
import io
import json
from types import SimpleNamespace

import pytest

from test_prod_release_daemon import daemon, make_repo, GitRefAdapter


@pytest.fixture(autouse=True)
def isolated_activation(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('SONSTENG_'):
            monkeypatch.delenv(key)


@pytest.mark.parametrize('enabled', [None, '', 'false', 'TRUE', '1', ' true'])
def test_disabled_tick_ignores_even_invalid_arguments(enabled, monkeypatch, capsys):
    if enabled is not None:
        monkeypatch.setenv('SONSTENG_PROD_RELEASE_ENABLED', enabled)
    assert daemon.main(['--not-a-valid-argument']) == 0
    assert 'disabled' in capsys.readouterr().out


@pytest.mark.parametrize('mode,release_id,message', [
    ('invalid', '', 'routine or canary'),
    ('canary', 'x' * 257, 'exact release id'),
    ('routine', 'some-release', 'cannot carry'),
])
def test_matching_digest_does_not_bypass_mode_authority(mode, release_id, message, monkeypatch):
    monkeypatch.setenv('SONSTENG_PROD_RELEASE_ENABLED', 'true')
    monkeypatch.setenv('SONSTENG_PROD_RELEASE_MODE', mode)
    monkeypatch.setenv('SONSTENG_PROD_CANARY_RELEASE_ID', release_id)
    monkeypatch.setenv('SONSTENG_PROD_EXPECTED_CONFIG_DIGEST', daemon.runtime_config_digest())
    with pytest.raises(RuntimeError, match=message):
        daemon.main([])


@pytest.mark.parametrize('missing', ['bearer', 'provider', 'restore-in-canary'])
def test_real_cli_stops_before_lock_or_network_for_invalid_activation(tmp_path, monkeypatch, capsys, missing):
    values = {
        'SONSTENG_PROD_RELEASE_ENABLED': 'true',
        'SONSTENG_PROD_RELEASE_MODE': 'canary',
        'SONSTENG_PROD_CANARY_RELEASE_ID': 'release-1',
        'SONSTENG_PROD_LOCK': str(tmp_path / 'never-created.lock'),
    }
    for suffix in ['LEDGER_URL', 'PAGES_PROJECT', 'CLOUDFLARE_ACCOUNT_ID', 'PAGES_ARTIFACT',
                   'PAGES_PROVENANCE_URL', 'WORKER_CONFIG', 'WORKER_PROVENANCE_URL', 'REPO',
                   'MANIFEST', 'RECOVERY_REGISTRY']:
        values['SONSTENG_PROD_' + suffix] = 'synthetic-' + suffix.lower()
    if missing != 'bearer':
        values['SONSTENG_PROD_RELEASE_BEARER'] = 'test-placeholder'
    if missing == 'restore-in-canary':
        values['SONSTENG_PROD_CLOUDFLARE_API_TOKEN'] = 'test-placeholder'
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('SONSTENG_PROD_EXPECTED_CONFIG_DIGEST', daemon.runtime_config_digest())
    if missing == 'restore-in-canary':
        with pytest.raises(RuntimeError, match='restoration path'):
            daemon.main(['--restore-release-id', 'release-1'])
    else:
        with pytest.raises(SystemExit) as result:
            daemon.main([])
        assert result.value.code == 2
        assert ('BEARER' if missing == 'bearer' else 'API_TOKEN') in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_real_git_checkout_resolves_recorded_assets_and_rejects_symlink_escape(tmp_path):
    repo, base = make_repo(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    with GitRefAdapter(repo).isolated_checkout(base) as checkout:
        asset = daemon._path_in_checkout(checkout, repo, repo / 'site/index.html')
        assert asset.read_text() == 'base site\n'
        (checkout / 'escape').symlink_to(outside, target_is_directory=True)
        with pytest.raises(RuntimeError, match='untrusted'):
            daemon._path_in_checkout(checkout, repo, repo / 'escape')
    assert not checkout.exists()


@pytest.mark.parametrize('scenario', ['routine-empty', 'canary-missing', 'canary-empty', 'canary-wrong-release'])
def test_cli_uses_real_ledger_and_preparation_with_offline_transport(tmp_path, monkeypatch, scenario):
    import prod_release_executor as executor
    requests = []
    def transport(request, timeout):
        requests.append(request)
        if request.full_url.endswith('/frontier'):
            return io.BytesIO(b'{"context":{"batches":[]}}')
        assert request.full_url.endswith('/claim')
        assert json.loads(request.data) == {'id': 'expected-release'}
        release = None
        if scenario == 'canary-wrong-release':
            release = {'id': 'different-release', 'state': 'authorized', 'base_sha': 'a' * 40,
                       'candidate_sha': 'b' * 40, 'manifest_hash': 'manifest', 'membership_hash': 'members',
                       'suggestion_ids': ['s1'], 'batches': [{'commit_sha': 'b' * 40}], 'fencing_token': 'fence'}
        return io.BytesIO(json.dumps({'release': release}).encode())
    original = executor.LedgerHTTP
    monkeypatch.setattr(executor, 'LedgerHTTP', lambda url, token: original(url, token, opener=transport))
    mode = 'routine' if scenario == 'routine-empty' else 'canary'
    values = {'RELEASE_ENABLED': 'true', 'RELEASE_MODE': mode, 'RELEASE_BEARER': 'synthetic',
              'CLOUDFLARE_API_TOKEN': 'synthetic', 'LEDGER_URL': 'https://example.invalid',
              'PAGES_PROJECT': 'fixture', 'CLOUDFLARE_ACCOUNT_ID': 'fixture',
              'PAGES_ARTIFACT': str(tmp_path / 'site'), 'PAGES_PROVENANCE_URL': 'https://example.invalid/pages',
              'WORKER_CONFIG': str(tmp_path / 'worker.json'), 'WORKER_PROVENANCE_URL': 'https://example.invalid/worker',
              'REPO': str(tmp_path), 'MANIFEST': str(tmp_path / 'manifest.json'),
              'RECOVERY_REGISTRY': str(tmp_path / 'registry.json'), 'LOCK': str(tmp_path / 'release.lock')}
    if mode == 'canary':
        values['CANARY_RELEASE_ID'] = 'expected-release'
    if scenario in ('canary-empty', 'canary-wrong-release'):
        (tmp_path / 'manifest.json').write_text('{}')
    for key, value in values.items():
        monkeypatch.setenv('SONSTENG_PROD_' + key, value)
    monkeypatch.setenv('SONSTENG_PROD_EXPECTED_CONFIG_DIGEST', daemon.runtime_config_digest())
    if scenario in ('canary-missing', 'canary-wrong-release'):
        with pytest.raises(RuntimeError, match='manifest is missing' if scenario == 'canary-missing' else 'outside the canary authority'):
            daemon.main([])
    else:
        assert daemon.main([]) == 0
    assert len(requests) == (0 if scenario == 'canary-missing' else 1)
    assert not (tmp_path / 'registry.json').exists()


@pytest.mark.parametrize('release', [None, SimpleNamespace(base_sha='')])
def test_restore_rejects_missing_base_before_checkout(release):
    args = SimpleNamespace(restore_release_id='restore')
    ledger = SimpleNamespace(claim_restore=lambda _id: release)
    with pytest.raises(RuntimeError, match='lacks a recorded base SHA'):
        daemon._restore_recorded_release(args, ledger, None, None,
            registry_factory=None, pages_factory=None, worker_factory=None,
            restorer_factory=None, executor_factory=None, git_factory=None)
