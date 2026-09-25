"""Offline rollback and retry integration against real temporary Git repositories."""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import direct_apply_daemon as dad


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()

    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, text=True,
                              stdout=subprocess.PIPE).stdout.strip()

    git('init', '-q')
    git('config', 'user.name', 'Offline fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'commit.gpgsign', 'false')
    (root / 'data').mkdir()
    (root / 'site').mkdir()
    (root / 'data/document.txt').write_text('before\n')
    (root / 'site/index.html').write_text('before\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    (root / 'data/document.txt').write_text('after\n')
    (root / 'site/index.html').write_text('after\n')
    git('add', '.')
    git('commit', '-qm', 'apply document')
    applied = git('rev-parse', 'HEAD')
    lock = dad.apply_lock
    monkeypatch.setattr(dad, 'apply_lock', lambda: lock(str(tmp_path / 'apply.lock')))
    request = {'id': 'offline-rq', 'doc': 'data/document.txt',
               'run_first': applied, 'run_last': applied, 'editor': 'slot:fixture'}
    return root, git, request


def execute(repo, **kwargs):
    root, _, request = repo

    def rebuild():
        (root / 'site/index.html').write_text((root / 'data/document.txt').read_text())
        return True, ''

    options = dict(repo_root=str(root), do_rebuild=rebuild,
                   do_history=lambda: (True, ''), generator=lambda _: 'fixture-generator')
    options.update(kwargs)
    return dad.execute_revert(request, **options)


def assert_restored(repo):
    root, git, request = repo
    assert git('rev-parse', 'HEAD') == request['run_last']
    assert git('status', '--porcelain') == ''
    assert (root / 'data/document.txt').read_text() == 'after\n'
    assert (root / 'site/index.html').read_text() == 'after\n'


@pytest.mark.parametrize('revert_request', [None, {}, {'run_first': 'abc'}, {'run_last': 'abc'}])
def test_missing_range_refuses_before_lock_or_git(tmp_path, revert_request):
    assert dad.execute_revert(revert_request, repo_root=str(tmp_path)) == (False, 'bad_run_range')
    assert list(tmp_path.iterdir()) == []


def test_non_repository_refuses_status_without_building(tmp_path, monkeypatch):
    lock = dad.apply_lock
    monkeypatch.setattr(dad, 'apply_lock', lambda: lock(str(tmp_path / 'apply.lock')))
    assert dad.execute_revert({'run_first': 'abc', 'run_last': 'abc'},
                              repo_root=str(tmp_path)) == (False, 'git_status_failed')


def test_rebuild_failure_rolls_back_source_and_generated_output_then_retries(repo):
    root, _, _ = repo

    def fail_rebuild():
        assert (root / 'data/document.txt').read_text() == 'before\n'
        (root / 'site/index.html').write_text('partial build\n')
        return False, 'fixture build failure'

    assert execute(repo, do_rebuild=fail_rebuild) == (False, 'rebuild_failed')
    assert_restored(repo)
    ok, evidence = execute(repo)
    assert ok
    assert evidence['new_text'] == 'before\n'


def test_rejected_commit_rolls_back_and_can_retry_after_hook_removed(repo):
    root, _, _ = repo
    hook = root / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    assert execute(repo) == (False, 'commit_failed')
    assert_restored(repo)
    hook.unlink()
    assert execute(repo)[0] is True


def test_missing_document_refuses_before_inverse_or_rebuild(repo):
    _, _, request = repo
    request['doc'] = 'data/missing.txt'
    assert execute(repo) == (False, 'revert_evidence_unavailable')
    assert_restored(repo)


def test_retry_after_new_commit_is_ambiguous_and_preserves_new_head(repo):
    root, git, _ = repo
    assert execute(repo)[0] is True
    git('commit', '--allow-empty', '-qm', 'later unrelated work')
    head = git('rev-parse', 'HEAD')
    assert execute(repo) == (False, 'revert_retry_ambiguous')
    assert git('rev-parse', 'HEAD') == head
    assert git('status', '--porcelain') == ''
    assert (root / 'data/document.txt').read_text() == 'before\n'


def test_retry_with_unavailable_document_evidence_does_not_create_commit(repo):
    _, git, request = repo
    assert execute(repo)[0] is True
    head = git('rev-parse', 'HEAD')
    request['doc'] = 'data/missing.txt'
    assert execute(repo) == (False, 'revert_evidence_unavailable')
    assert git('rev-parse', 'HEAD') == head
    assert git('status', '--porcelain') == ''


def test_unrelated_document_range_is_rejected_when_evidence_is_unchanged(repo):
    root, git, request = repo
    (root / 'data/other.txt').write_text('new unrelated document')
    git('add', '.')
    git('commit', '-qm', 'add unrelated document')
    request['run_first'] = request['run_last'] = git('rev-parse', 'HEAD')
    assert execute(repo) == (False, 'revert_evidence_unavailable')
    assert_restored(repo)
    assert (root / 'data/other.txt').read_text() == 'new unrelated document'


def test_reverting_document_creation_without_after_evidence_rolls_back(repo):
    root, git, request = repo
    (root / 'data/added.txt').write_text('created content')
    git('add', '.')
    git('commit', '-qm', 'create document')
    request.update(doc='data/added.txt', run_first=git('rev-parse', 'HEAD'),
                   run_last=git('rev-parse', 'HEAD'))
    assert execute(repo) == (False, 'revert_evidence_unavailable')
    assert_restored(repo)
    assert (root / 'data/added.txt').read_text() == 'created content'


def test_dirty_generated_site_is_restored_and_full_chain_commits_evidence(repo):
    root, git, request = repo
    (root / 'site/index.html').write_text('routine generated churn')
    history_heads = []

    def history():
        history_heads.append(git('rev-parse', 'HEAD'))
        return False, 'non-gating history generation failure'

    ok, evidence = execute(repo, do_history=history)
    assert ok
    assert evidence['original_text'] == 'after\n'
    assert evidence['new_text'] == 'before\n'
    assert evidence['actor'] == 'slot:fixture'
    assert evidence['base_sha'] == request['run_last']
    assert history_heads == [evidence['commit_sha']]
    assert (root / 'site/index.html').read_text() == 'before\n'
    assert git('status', '--porcelain') == ''
    assert git('show', '-s', '--format=%B').endswith('Revert-Request: offline-rq')
    retry_ok, retry_evidence = execute(repo)
    assert retry_ok and retry_evidence == evidence


@pytest.mark.parametrize('size, accepted', [(131072, True), (131073, False)])
def test_original_evidence_byte_limit_is_inclusive_and_refusal_is_atomic(repo, size, accepted):
    root, git, request = repo
    content = 'x' * size
    (root / 'data/document.txt').write_text(content)
    git('add', '.')
    git('commit', '-qm', 'large source revision')
    request['run_first'] = request['run_last'] = git('rev-parse', 'HEAD')
    ok, detail = execute(repo)
    assert ok is accepted
    if accepted:
        assert detail['original_text'] == content
        assert detail['new_text'] == 'after\n'
        assert git('rev-parse', 'HEAD') != request['run_last']
    else:
        assert detail == 'revert_evidence_unavailable'
        assert git('rev-parse', 'HEAD') == request['run_last']
        assert (root / 'data/document.txt').read_text() == content
    assert git('status', '--porcelain') == ''
