"""Exercise review orchestration with real isolated Git and local CLI fixtures."""
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editorial_pass as ep


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def commit(repo, path, text, subject, apply=True):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, 'add', path)
    email = ep.APPLY_AUTHOR_EMAIL if apply else 'fixture@example.invalid'
    git(repo, '-c', 'user.name=Fixture', '-c', f'user.email={email}', 'commit', '-qm', subject)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / 'repo'
    path.mkdir()
    git(path, 'init', '-q')
    commit(path, 'data/example.txt', 'Before\n', 'initial', apply=False)
    return path


def executable(tmp_path, body):
    path = tmp_path / 'fixture-reviewer'
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o700)
    return str(path)


def test_real_git_window_filters_authors_and_reviewable_paths(repo):
    first = commit(repo, 'data/example.txt', 'After\n', 'apply: batch alpha')
    second = commit(repo, 'notes.txt', 'Unreviewable\n', 'apply: batch beta')
    commit(repo, 'data/example.txt', 'Unrelated\n', 'ordinary change', apply=False)
    assert ep.git_apply_commits(str(repo), ['HEAD~3..HEAD']) == [second, first]
    assert ep.git_apply_commits(str(repo), 'HEAD~3..HEAD', batch_id='alpha') == [first]
    assert ep.git_apply_commits(str(repo), 'HEAD~3..HEAD', batch_id='absent') == [second, first]
    diffs = ep.collect_diffs(str(repo), [first, second])
    assert len(diffs) == 1
    assert '+After' in diffs[0]
    assert 'notes.txt' not in diffs[0]


def test_real_git_cli_parser_and_payload_chain_is_read_only_in_dry_run(repo, tmp_path, monkeypatch):
    sha = commit(repo, 'data/example.txt', 'After\n', 'apply: batch alpha')
    raw = json.dumps({'flags': [{'source_ref': 'data/example.txt', 'severity': 'voice', 'message': 'Clarify wording'}]})
    cli = executable(tmp_path, "printf '%s' '" + raw + "'")
    monkeypatch.setenv(ep.ENV_CLI, cli)
    output = io.StringIO()
    result = ep.run(api_base=None, token=None, repo_root=str(repo), since='HEAD~1..HEAD',
                    batch_id='alpha', trigger='session-end', dry_run=True, out=output)
    assert result.commits == [sha]
    assert result.filed == 0
    assert result.degraded_reason == ''
    assert result.payloads == [ep.flag_payload(result.flags[0], salt='alpha')]
    assert result.payloads[0]['comment'] == '[editorial:voice] Clarify wording'
    assert 'DRY-RUN' in output.getvalue()
    assert git(repo, 'status', '--porcelain') == ''
    assert git(repo, 'rev-parse', 'HEAD') == sha


@pytest.mark.parametrize('body, expected', [
    ('printf fixture-output', (True, 'fixture-output', None)),
    ('printf fixture-output; printf fixture-error >&2; exit 7',
     (False, 'fixture-outputfixture-error', 'cli_error_rc_7')),
])
def test_local_cli_exit_contract(tmp_path, body, expected):
    assert ep.run_cli('Public fixture prompt', cli=executable(tmp_path, body), timeout=2) == expected


def test_cli_timeout_and_missing_binary_degrade_without_crashing(tmp_path):
    # exec avoids leaving a shell child alive after subprocess timeout cleanup.
    slow = executable(tmp_path, 'exec sleep 2')
    assert ep.run_cli('fixture', cli=slow, timeout=0.02) == (False, '', 'cli_timeout')
    assert ep.run_cli('fixture', cli=str(tmp_path / 'missing')) == (False, '', 'cli_not_found')


def test_apply_commits_outside_reviewable_paths_skip_model_and_notify_empty(repo):
    commit(repo, 'notes.txt', 'Only notes\n', 'apply: batch notes')
    notifications = []
    result = ep.run(api_base=None, token=None, repo_root=str(repo), since='HEAD~1..HEAD',
                    cli_runner=lambda _: pytest.fail('No model needed'),
                    notifier=lambda *args, **kwargs: notifications.append((args, kwargs)))
    assert result.flags == []
    assert result.payloads == []
    assert notifications == [(([],), {'trigger': 'daily', 'filed_ok': 0})]


def test_flag_normalization_skips_nondictionaries_and_uses_legacy_aliases():
    raw = json.dumps({'result': 'Before {bad} then ' + json.dumps({'flags': [None, [], 1,
        {'ref': 'data/a', 'comment': 'Legacy comment', 'severity': 'unknown'}]})})
    assert ep.parse_flags(raw) == [{'source_ref': 'data/a', 'severity': 'note', 'message': 'Legacy comment'}]
    assert ep.select_apply_commits('bad\n\t\n') == []
    assert ep.parse_flags(json.dumps({'result': '{"flags":{}}'})) == []


@pytest.mark.parametrize('token', ['', 'fixture-service-token'])
def test_file_flag_constructs_bounded_transport_request(monkeypatch, token):
    class Response:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *_): pass
    seen = []
    def transport(request, timeout):
        seen.append((request, timeout))
        return Response()
    monkeypatch.setattr(ep.urllib.request, 'urlopen', transport)
    payload = ep.flag_payload({'source_ref': 'data/a', 'severity': 'note', 'message': 'Fixture'})
    assert ep.file_flag('https://fixture.invalid/edit/v1/', token, payload, timeout=4) == (True, 201)
    request, timeout = seen[0]
    assert request.full_url == 'https://fixture.invalid/edit/v1/system-suggest'
    assert request.get_method() == 'POST'
    assert timeout == 4
    assert json.loads(request.data) == payload
    assert request.get_header('X-edit-request') == '1'
    assert request.get_header('Authorization') == ('Bearer ' + token if token else None)


@pytest.mark.parametrize('failure, expected', [
    (urllib.error.HTTPError('https://fixture.invalid', 409, 'Conflict', {}, None), 'http_409'),
    (urllib.error.URLError('offline'), 'unreachable:offline'),
])
def test_file_flag_returns_transport_failure_without_aborting_batch(monkeypatch, failure, expected):
    def transport(*args, **kwargs): raise failure
    monkeypatch.setattr(ep.urllib.request, 'urlopen', transport)
    assert ep.file_flag('https://fixture.invalid', '', {}) == (False, expected)


@pytest.mark.parametrize('flags, filed, reason, expected', [
    ([], 0, None, 'found no issues'),
    ([{'severity': 'voice'}], 1, None, '1 comment (voice 1)'),
    ([{'severity': 'voice'}, {'severity': 'note'}], 0, None, '0 comments (note 1, voice 1)'),
    ([], 0, 'cli_timeout', 'could not run the reviewer (cli_timeout)'),
])
def test_notification_builds_content_light_summary_without_publishing(flags, filed, reason, expected):
    captured = []
    ep.notify_digest(flags, trigger='daily', filed_ok=filed, degraded_reason=reason,
                     topic_resolver=lambda: 'fixture-topic', review_url='https://fixture.invalid/review',
                     publish=lambda *args, **kwargs: captured.append((args, kwargs)))
    assert len(captured) == 1
    assert expected in captured[0][0][2]
    assert captured[0][0][0] == 'fixture-topic'


def test_notification_failure_does_not_break_editorial_result():
    def unavailable(): raise RuntimeError('fixture topic unavailable')
    ep.notify_digest([], trigger='daily', filed_ok=0, topic_resolver=unavailable,
                     publish=lambda *args, **kwargs: pytest.fail('No topic resolved'),
                     review_url='https://fixture.invalid/review')
