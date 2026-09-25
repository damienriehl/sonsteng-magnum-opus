"""Rewrite queue coverage through the real CLI/map/RPC serialization chain."""
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor_ai_rewrite as rewrite


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, 'REPO_ROOT', str(tmp_path))
    monkeypatch.setenv(rewrite.ap.ENV_SERVICE_TOKEN, 'synthetic-test-token')
    monkeypatch.delenv(rewrite.ap.ENV_API_BASE, raising=False)
    return tmp_path


def transport(monkeypatch, payload):
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(rewrite.ap.urllib.request, 'urlopen', open_request)
    return requests


def test_cli_resolves_current_map_filters_review_and_writes_pending_queue(workspace, monkeypatch, capsys):
    ref = 'data/matters/m02/facts.md#intro'
    build = workspace / 'build'
    build.mkdir()
    (build / 'editor-map.generated.json').write_text(json.dumps({'pages': {'facts': [
        {'source_ref': ref, 'original_text': 'Current café facts', 'context': 'Current heading'}]}}))
    requests = transport(monkeypatch, {'items': [
        {'id': 'accepted', 'status': 'accepted', 'kind': 'comment', 'source_ref': ref,
         'original_text': 'stale text', 'context': 'stale context', 'comment': 'Clarify'},
        {'id': 'pending', 'status': 'pending', 'kind': 'comment'},
        {'id': 'prose', 'status': 'accepted', 'kind': 'prose'}]})
    output = workspace / 'nested' / 'queue.json'
    assert rewrite.main(['--base-url', 'https://example.invalid/edit/v1/', '--out', str(output)]) == 0
    task, = json.loads(output.read_text())['tasks']
    assert task['original_text'] == 'Current café facts'
    assert task['context'] == 'Current heading'
    assert task['matter'] == 'm02'
    assert task['id'] == 'accepted'
    assert task['submit_as'] == {'origin': 'ai_rewrite', 'kind': 'prose', 'source_ref': ref,
                                 'status': 'pending', 'attribution': 'AI (from JOS comment)'}
    assert requests[0].full_url == 'https://example.invalid/edit/v1/review'
    assert requests[0].get_method() == 'GET'
    assert 'wrote 1 AI-rewrite task(s)' in capsys.readouterr().out
    assert 'café' in output.read_text()
    assert output.read_text().endswith('\n')


@pytest.mark.parametrize('payload', [{}, {'items': []}, {'suggestions': []}])
def test_cli_without_map_writes_empty_queue(workspace, monkeypatch, payload):
    transport(monkeypatch, payload)
    output = workspace / 'out' / 'queue.json'
    assert rewrite.main(['--base-url', 'https://example.invalid', '--out', str(output)]) == 0
    bundle = json.loads(output.read_text())
    assert bundle['count'] == 0
    assert bundle['tasks'] == []


def test_legacy_review_schema_and_missing_map_fields_fall_back(monkeypatch):
    transport(monkeypatch, {'suggestions': [
        {'status': 'accepted', 'kind': 'comment', 'source_ref': 'other.md#x',
         'original_text': 'Fallback', 'context': 'Heading'},
        {'status': 'accepted', 'kind': 'comment'}]})
    client = rewrite.RewriteHttpClient('https://example.invalid', 'synthetic')
    tasks = rewrite.collect_rewrite_tasks(client, {'other.md#x': {'original_text': '', 'context': None}})
    assert tasks[0]['original_text'] == 'Fallback'
    assert tasks[0]['context'] == 'Heading'
    assert tasks[0]['matter'] is None
    assert tasks[1]['original_text'] == tasks[1]['context'] == tasks[1]['comment'] == ''
    assert tasks[1]['source_ref'] == ''
    assert tasks[1]['id'] is None


@pytest.mark.parametrize('missing', ['base', 'token'])
def test_cli_missing_configuration_returns_two_without_output(workspace, monkeypatch, capsys, missing):
    if missing == 'token':
        monkeypatch.delenv(rewrite.ap.ENV_SERVICE_TOKEN)
    args = [] if missing == 'base' else ['--base-url', 'https://example.invalid']
    output = workspace / 'out' / 'queue.json'
    assert rewrite.main(args + ['--out', str(output)]) == 2
    assert 'error: needs' in capsys.readouterr().err
    assert not output.exists()


def test_rpc_error_propagates_without_overwriting_existing_queue(workspace, monkeypatch):
    output = workspace / 'queue.json'
    output.write_text('previous queue')
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError('offline transport')
    monkeypatch.setattr(rewrite.ap.urllib.request, 'urlopen', unavailable)
    with pytest.raises(rewrite.ap.ApplyError, match='unreachable: offline transport'):
        rewrite.main(['--base-url', 'https://example.invalid', '--out', str(output)])
    assert output.read_text() == 'previous queue'


def test_invalid_map_json_fails_before_transport(workspace, monkeypatch):
    (workspace / 'build').mkdir()
    (workspace / 'build' / 'editor-map.generated.json').write_text('{broken')
    requests = transport(monkeypatch, {})
    with pytest.raises(json.JSONDecodeError):
        rewrite.main(['--base-url', 'https://example.invalid', '--out', str(workspace / 'queue.json')])
    assert requests == []


def test_write_queue_reports_invalid_parent(tmp_path):
    parent = tmp_path / 'file'
    parent.write_text('occupied')
    with pytest.raises(FileExistsError):
        rewrite.write_queue([], str(parent / 'queue.json'))
