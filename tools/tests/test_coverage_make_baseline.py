"""Baseline CLI through real isolated git tags and history regeneration."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_history as bh
import make_baseline as mb


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True,
                              text=True, stdout=subprocess.PIPE).stdout.strip()
    git('init', '-q')
    git('config', 'user.name', 'Fixture Author')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'commit.gpgsign', 'false')
    git('config', 'tag.gpgsign', 'false')
    monkeypatch.setattr(mb, 'ROOT', str(tmp_path))
    for name, path in {'ROOT': tmp_path, 'DATA': tmp_path / 'data',
        'BUILD': tmp_path / 'build', 'HISTORY_DIR': tmp_path / 'build/history',
        'BUNDLE_PATH': tmp_path / 'build/history-bundle.generated.json',
        'EDITOR_MAP': tmp_path / 'build/editor-map.generated.json',
        'ASSETS_DIR': tmp_path / 'assets'}.items():
        monkeypatch.setattr(bh, name, str(path))
    monkeypatch.setattr(bh.assert_no_history_leak, '__defaults__', (str(tmp_path / 'public'),))
    source = tmp_path / 'data/curriculum/lesson.md'
    source.parent.mkdir(parents=True)
    source.write_text('Initial lesson.\n')
    git('add', 'data')
    git('commit', '-q', '-m', 'Initial')
    return tmp_path, git


def test_cli_creates_annotated_baseline_and_real_history_bundle(repository, capsys):
    root, git = repository
    head = git('rev-parse', 'HEAD')
    assert mb.main(['review', '-m', 'Reviewed café']) == 0
    assert git('cat-file', '-t', 'baseline-review') == 'tag'
    assert git('rev-parse', 'baseline-review^{}') == head
    bundle = json.loads((root / 'build/history-bundle.generated.json').read_text())
    baseline = bundle['docs']['data/curriculum/lesson.md']['baselines'][0]
    assert baseline['name'] == 'baseline-review'
    assert baseline['message'] == 'Reviewed café'
    assert baseline['sha'] == head
    assert 'created annotated tag baseline-review' in capsys.readouterr().out
    assert mb.main(['--list']) == 0
    assert 'baseline-review' in capsys.readouterr().out
    assert git('status', '--porcelain', '--untracked-files=no') == ''


def test_regeneration_failure_keeps_created_tag_and_reports_failure(repository, capsys):
    root, git = repository
    public = root / 'public'
    public.mkdir()
    (public / 'leak.html').write_text(bh.HISTORY_SENTINEL)
    assert mb.main(['leaked']) == 1
    output = capsys.readouterr()
    assert 'LEAK ASSERTION FAILED' in output.out
    assert 'history regen reported a problem' in output.err
    assert git('cat-file', '-t', 'baseline-leaked') == 'tag'
    assert (root / 'build/history-bundle.generated.json').is_file()


def test_tag_write_lock_failure_reports_git_error_and_leaves_no_tag(repository, capsys):
    root, git = repository
    lock = root / '.git/refs/tags/baseline-locked.lock'
    lock.write_text('owned by another process\n')
    assert mb.main(['locked', '--no-regen']) == 1
    assert 'git tag failed:' in capsys.readouterr().err
    assert git('tag', '--list') == ''
    assert lock.read_text() == 'owned by another process\n'
    assert not (root / 'build').exists()


def test_cli_explicit_old_ref_and_duplicate_never_move_baseline(repository, capsys):
    root, git = repository
    first = git('rev-parse', 'HEAD')
    (root / 'data/curriculum/lesson.md').write_text('Second lesson.\n')
    git('commit', '-qam', 'Second')
    assert mb.main(['first', '--at', 'HEAD~1', '--no-regen']) == 0
    assert git('rev-parse', 'baseline-first^{}') == first
    assert git('for-each-ref', '--format=%(contents:subject)', 'refs/tags/baseline-first') == 'Baseline first'
    original_tag = git('rev-parse', 'baseline-first')
    assert mb.main(['first', '--no-regen']) == 2
    assert 'refusing to move it' in capsys.readouterr().err
    assert git('rev-parse', 'baseline-first') == original_tag
    assert not (root / 'build').exists()


@pytest.mark.parametrize('name', ['', '-bad', 'A', 'with space', 'a/b', 'a' * 65])
def test_invalid_names_do_not_create_tags(repository, capsys, name):
    root, git = repository
    assert mb.make_baseline(name, '', 'HEAD', False) == 2
    assert 'invalid name' in capsys.readouterr().err
    assert git('tag', '--list') == ''
    assert not (root / 'build').exists()


@pytest.mark.parametrize('name', ['a', '1', 'a' * 64])
def test_slug_length_boundaries_create_real_tags(repository, name):
    _, git = repository
    assert mb.main([name, '--no-regen']) == 0
    assert git('cat-file', '-t', 'baseline-' + name) == 'tag'


@pytest.mark.parametrize('argv', [[], ['--no-regen']])
def test_cli_missing_name_prints_usage_without_mutation(repository, capsys, argv):
    _, git = repository
    assert mb.main(argv) == 2
    assert 'Usage:' in capsys.readouterr().out
    assert git('tag', '--list') == ''


def test_cli_missing_ref_does_not_create_tag(repository, capsys):
    _, git = repository
    assert mb.main(['review', '--at', 'absent-ref', '--no-regen']) == 2
    assert "ref 'absent-ref' not found" in capsys.readouterr().err
    assert git('tag', '--list') == ''


@pytest.mark.parametrize('flag', ['-m', '--at'])
def test_trailing_option_without_value_uses_defaults(repository, flag):
    _, git = repository
    assert mb.main(['defaults', '--no-regen', flag]) == 0
    assert git('rev-parse', 'baseline-defaults^{}') == git('rev-parse', 'HEAD')
    assert git('for-each-ref', '--format=%(contents:subject)', 'refs/tags/baseline-defaults') == 'Baseline defaults'


def test_list_empty_and_unrelated_tags_remains_read_only(repository, capsys):
    _, git = repository
    git('tag', 'unrelated')
    assert mb.main(['unused-name', '--list']) == 0
    assert 'no baselines yet' in capsys.readouterr().out
    assert git('tag', '--list') == 'unrelated'
