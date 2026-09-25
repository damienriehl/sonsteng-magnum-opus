"""Real local Git inventory, fail-closed paths, and opaque/binary inputs."""
import hashlib
import json
import os
import subprocess

import pytest

from test_repo_rename_inventory import inventory, build, OWNER, CURRENT, TARGET, write


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout


def test_cli_real_tracked_files_remotes_and_worktree_hashes(tmp_path, capsys):
    git(tmp_path, 'init', '-q')
    write(tmp_path, 'README.md', 'current-repo\n')
    write(tmp_path, 'untracked.txt', 'current-repo must not be scanned')
    git(tmp_path, 'add', 'README.md')
    git(tmp_path, 'remote', 'add', 'origin', 'https://github.com/example-owner/current-repo')
    git(tmp_path, 'remote', 'add', 'unrelated', 'https://example.invalid/elsewhere')
    args = ['--repo', str(tmp_path), '--owner', OWNER, '--current', CURRENT, '--target', TARGET]
    before = git(tmp_path, 'status', '--porcelain')
    assert inventory.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['runtime']['remote_names_requiring_review'] == ['origin']
    digest = 'sha256:' + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16]
    assert report['runtime']['worktree_path_digests_requiring_review'] == [digest]
    assert [r['path'] for r in report['references']] == ['README.md']
    assert git(tmp_path, 'status', '--porcelain') == before


def test_non_repository_cli_reports_failure(tmp_path, capsys):
    assert inventory.main(['--repo', str(tmp_path), '--owner', OWNER, '--current', CURRENT, '--target', TARGET]) == 2
    result = capsys.readouterr()
    assert not result.out and 'tracked-file inventory is unavailable' in result.err
    with pytest.raises(inventory.InventoryError, match='runtime inventory is unavailable'):
        inventory._runtime_state(tmp_path, CURRENT)


def test_non_utf8_git_filename_is_rejected(tmp_path):
    git(tmp_path, 'init', '-q')
    name = os.fsencode(tmp_path) + b'/invalid-\xff'
    with open(name, 'wb') as target:
        target.write(b'ordinary fixture')
    git(tmp_path, 'add', '--all')
    with pytest.raises(inventory.InventoryError, match='not UTF-8'):
        inventory._tracked_paths(tmp_path)


@pytest.mark.parametrize('path', ['../outside', '/absolute', 'docs/../../escape'])
def test_escape_paths_rejected_before_reading(tmp_path, path):
    with pytest.raises(inventory.InventoryError, match='escapes repository'):
        build(tmp_path, [path])


@pytest.mark.parametrize('runtime', [[], {}, {'extra': []}])
def test_runtime_shape_rejected(tmp_path, runtime):
    with pytest.raises(inventory.InventoryError, match='unexpected shape'):
        build(tmp_path, [], runtime)


@pytest.mark.parametrize('name', ['', 'a/b', 'a' * 101, 'spaces here'])
def test_invalid_repository_names_rejected(tmp_path, name):
    with pytest.raises(inventory.InventoryError, match='bounded GitHub names'):
        inventory.build_inventory(tmp_path, owner=OWNER, current=CURRENT, target=name, tracked_paths=[])


def test_binary_and_non_utf8_sources_skip_but_text_rows_deduplicate(tmp_path):
    (tmp_path / 'binary').write_bytes(b'current-repo\x00')
    (tmp_path / 'nonutf8').write_bytes(b'current-repo\xff')
    write(tmp_path, 'tools/task.py', 'unrelated\ncurrent-repo\n')
    report = build(tmp_path, ['binary', 'nonutf8', 'tools/task.py', 'tools/task.py'])
    assert report['references'] == [{'path': 'tools/task.py', 'line': 2, 'classification': 'active_operational_reference_patch'}]
    assert inventory.classify_reference('README.md', 'unrelated', owner=OWNER, current=CURRENT) is None
