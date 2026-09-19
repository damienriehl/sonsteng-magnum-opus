"""Offline notification contracts; integration reads file URLs and never sends."""
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import digest_push as digest


@pytest.fixture(autouse=True)
def local_configuration(monkeypatch, tmp_path):
    for name in [digest.ENV_API_BASE, digest.ENV_SERVICE_TOKEN, digest.ENV_REVIEW_URL,
                 digest.ENV_EDIT_ORIGIN, digest.ENV_NTFY_TOPIC, digest.ENV_STATE_FILE, digest.ENV_NTFY_SERVER]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(digest, 'DEFAULT_TOPIC_FILE', str(tmp_path / 'topic-fixture'))


def test_real_file_url_to_cli_digest_does_not_write_state_or_publish(tmp_path, monkeypatch, capsys):
    rows = [{'id': str(i), 'status': 'drift' if i == 0 else 'pending',
             'source_ref': f'data/matters/m{i:02d}/facts.md#b', 'new_text': 'private fixture prose'} for i in range(8)]
    (tmp_path / 'review').write_text(json.dumps({'items': rows}))
    monkeypatch.setenv(digest.ENV_API_BASE, tmp_path.as_uri())
    state = tmp_path / 'state.json'
    assert digest.main(['--dry-run', '--state-file', str(state)]) == 0
    output = capsys.readouterr().out
    assert '8 suggestions' in output and '+2 more' in output and 'drift 1' in output
    assert 'private fixture prose' not in output
    assert not state.exists()


@pytest.mark.parametrize('payload,expected', [({'suggestions': [{'id': 'a'}]}, [{'id': 'a'}]), ({}, []), ({'items': []}, [])])
def test_real_file_fetch_accepts_supported_envelopes(tmp_path, payload, expected):
    (tmp_path / 'review').write_text(json.dumps(payload))
    assert digest.fetch_rows(tmp_path.as_uri(), None) == expected


def test_cli_missing_api_reports_error_without_topic_access(tmp_path, capsys):
    assert digest.main(['--dry-run', '--state-file', str(tmp_path / 'state')]) == 2
    assert 'EDIT_API_BASE is required' in capsys.readouterr().err


@pytest.mark.parametrize('kind', ['http', 'connection'])
def test_fetch_transport_failure_has_actionable_diagnostic(monkeypatch, kind):
    def fail(request, timeout):
        assert request.get_header('Authorization') == 'Bearer synthetic-token'
        assert request.get_header('X-edit-request') == '1'
        if kind == 'http':
            raise urllib.error.HTTPError(request.full_url, 503, 'unavailable', {}, io.BytesIO(b'fixture outage'))
        raise urllib.error.URLError('fixture offline')
    monkeypatch.setattr(digest.urllib.request, 'urlopen', fail)
    with pytest.raises(RuntimeError, match='HTTP 503: fixture outage' if kind == 'http' else 'unreachable: fixture offline'):
        digest.fetch_rows('https://example.invalid/', 'synthetic-token')


@pytest.mark.parametrize('click', ['', 'https://example.invalid/review'])
def test_publish_request_contract_uses_only_intercepted_transport(monkeypatch, click):
    class Response:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def intercept(request, timeout):
        assert request.full_url == 'https://example.invalid/topic-fixture'
        assert request.method == 'POST' and request.data == 'café'.encode()
        assert request.get_header('Click') == (click or None)
        assert request.get_header('Title') == 'Summary'
        assert request.get_header('Priority') == 'low'
        assert timeout == 4
        return Response()
    monkeypatch.setattr(digest.urllib.request, 'urlopen', intercept)
    assert digest.publish_ntfy('topic-fixture', 'Summary', 'café', click, timeout=4, server='https://example.invalid/', priority='low') == 202


@pytest.mark.parametrize('content', [None, '', '  ', ' local-fixture-topic\n'])
def test_topic_resolution_uses_only_synthetic_file(tmp_path, content):
    if content is not None:
        Path(digest.DEFAULT_TOPIC_FILE).write_text(content)
    if content and content.strip():
        assert digest.resolve_topic() == content.strip()
    else:
        with pytest.raises(RuntimeError, match='No ntfy topic'):
            digest.resolve_topic()


def test_state_paths_and_corrupt_state_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    assert digest.default_state_path() == str(tmp_path / 'sonsteng-digest/last-notified.json')
    state = tmp_path / 'nested/state.json'
    monkeypatch.setenv(digest.ENV_STATE_FILE, str(state))
    assert digest.default_state_path() == str(state)
    digest.save_state(str(state), 'signature', 2, 'timestamp')
    assert digest.load_state(str(state)) == {'signature': 'signature', 'count': 2, 'notified_at': 'timestamp'}
    assert not Path(str(state) + '.tmp').exists()
    state.write_text('{broken')
    assert digest.load_state(str(state)) == {}
    digest.clear_state(str(state))
    digest.clear_state(str(state))
