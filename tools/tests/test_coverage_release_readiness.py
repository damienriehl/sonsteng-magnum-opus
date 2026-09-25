"""Readiness input boundaries; all credentials and files are synthetic fixtures."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prod_release_readiness as readiness

OFF = {'available': True, 'enabled': False, 'active': False}
SHA = 'a' * 40


def inspect(context=None, audit=None, release=None):
    context = {'base_sha': SHA, 'batches': []} if context is None else context
    audit = {'schema_version': 1, 'counts': {}, 'invariants': {}, 'active_releases': []} if audit is None else audit
    observer = SimpleNamespace(preparation_context=lambda: context, audit=lambda: audit,
                               get_release=lambda _: release)
    return readiness.inspect_readiness(observer, release_enabled=False, timer=OFF)


@pytest.mark.parametrize('counts', [None, [], {'Invalid': 1}, {'x': True}, {'x': -1}, {'x': 1.5}])
def test_readiness_rejects_malformed_counters_without_returning_partial_data(counts):
    result = inspect(audit={'schema_version': 1, 'counts': counts, 'invariants': {}, 'active_releases': []})
    assert result['reason'] == 'readiness counts malformed'
    assert result['counts'] == {}
    assert result['queue_count'] == 0
    assert result['ready'] is False


@pytest.mark.parametrize('release', [None, [], {}, {'id': '../bad', 'state': 'prepared'},
    {'id': 'r', 'state': 'prepared', 'manifest_hash': 'contains spaces'}])
def test_readiness_bounds_release_identifiers_and_hashes(release):
    with pytest.raises(readiness.ObserverError, match='release malformed'):
        readiness._release_summary(release)


@pytest.mark.parametrize('batch', [None, {}, {'batch_id': 'b', 'commit_sha': SHA, 'member_count': True},
    {'batch_id': 'b', 'commit_sha': 'bad', 'member_count': 0},
    {'batch_id': 'b', 'commit_sha': SHA, 'member_count': -1}])
def test_readiness_queue_rows_fail_closed(batch):
    result = inspect(context={'base_sha': SHA, 'batches': [batch]})
    assert result['reason'] == 'readiness queue malformed'
    assert result['queue'] == []


@pytest.mark.parametrize('context, audit, reason', [
    ({}, {'schema_version': 2}, 'readiness audit malformed'),
    ({'batches': None}, None, 'readiness queue malformed'),
    ({'batches': [{}] * 1001}, None, 'readiness queue malformed'),
    ({'base_sha': 'invalid'}, None, 'readiness frontier malformed'),
    ({'active_release': {'id': 'r', 'state': 'prepared'}}, None, 'readiness active release mismatch'),
])
def test_readiness_schema_size_and_frontier_disagreements(context, audit, reason):
    assert inspect(context=context, audit=audit)['reason'] == reason


def test_audit_release_without_context_match_fails_closed():
    release = {'id': 'r', 'state': 'prepared'}
    audit = {'schema_version': 1, 'counts': {}, 'invariants': {}, 'active_releases': [release]}
    assert inspect(context={}, audit=audit, release=release)['reason'] == 'readiness active release mismatch'


@pytest.mark.parametrize('failure', [OSError('offline fixture'), subprocess.TimeoutExpired(['systemctl'], 5)])
def test_timer_probe_failure_does_not_claim_safety(failure):
    def run(*args, **kwargs): raise failure
    assert readiness.timer_state(run) == {'available': False, 'enabled': None, 'active': None}


def protected(path, content):
    path.write_text(content)
    path.chmod(0o600)
    return path


@pytest.mark.parametrize('content, message', [
    ('# comment\n\n', 'credential unavailable'), ('malformed line', 'file malformed'),
    (readiness.OBSERVER_ENV_KEY + '=\n', 'credential unavailable'),
    (readiness.OBSERVER_ENV_KEY + '=' + 'x' * 4097, 'credential unavailable'),
])
def test_observer_fixture_file_rejects_empty_malformed_and_oversize_values(tmp_path, content, message):
    path = protected(tmp_path / 'observer.fixture', content)
    with pytest.raises(readiness.ObserverError, match=message):
        readiness.load_observer_bearer(path)


def test_protected_file_parser_accepts_comments_and_reads_only_explicit_config_flag(tmp_path):
    observer = protected(tmp_path / 'observer.fixture', '# Fixture only\n\n' + readiness.OBSERVER_ENV_KEY + '=synthetic-observer\n')
    config = protected(tmp_path / 'production.fixture', '# Fixture only\n\nIGNORED=synthetic\nSONSTENG_PROD_RELEASE_ENABLED=false\n')
    assert readiness.load_observer_bearer(observer) == 'synthetic-observer'
    assert readiness.read_release_enabled(config) is False


def test_readiness_cli_real_file_chain_rejects_ambiguous_configuration_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('SONSTENG_PROD_RELEASE_BEARER', raising=False)
    monkeypatch.delenv('EDIT_SERVICE_TOKEN', raising=False)
    observer = protected(tmp_path / 'observer.fixture', readiness.OBSERVER_ENV_KEY + '=synthetic-observer\n')
    config = protected(tmp_path / 'production.fixture', 'SONSTENG_PROD_RELEASE_ENABLED=false\nSONSTENG_PROD_RELEASE_ENABLED=true\n')
    before = config.read_bytes()
    assert readiness.main(['--ledger-url', 'https://fixture.invalid/edit/v1',
                           '--observer-env-file', str(observer), '--prod-env-file', str(config)]) == 2
    assert json.loads(capsys.readouterr().out) == {'ready': False, 'reason': 'observer configuration unavailable'}
    assert config.read_bytes() == before
