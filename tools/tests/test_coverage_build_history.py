"""Isolated git-to-bundle integration, discovery, and history error paths."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_history as bh


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True,
                              text=True, stdout=subprocess.PIPE).stdout.strip()
    git('init', '-q')
    git('config', 'user.name', 'Test Author')
    git('config', 'user.email', 'test@example.invalid')
    for name, value in {'ROOT': tmp_path, 'DATA': tmp_path / 'data',
        'BUILD': tmp_path / 'build', 'HISTORY_DIR': tmp_path / 'build/history',
        'BUNDLE_PATH': tmp_path / 'build/history-bundle.generated.json',
        'EDITOR_MAP': tmp_path / 'build/editor-map.generated.json',
        'ASSETS_DIR': tmp_path / 'assets'}.items():
        monkeypatch.setattr(bh, name, str(value))
    return tmp_path, git, bh.GitRepo(str(tmp_path))


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def commit(root, git, rel, text, message):
    write(root, rel, text)
    git('add', rel)
    git('commit', '-q', '-m', message)
    return git('rev-parse', 'HEAD')


def test_discovery_git_baselines_diff_and_emit_round_trip(repository):
    root, git, repo = repository
    rel = 'data/curriculum/lesson.md'
    first = commit(root, git, rel, 'Original café', 'Initial lesson')
    git('tag', 'baseline-start')
    second = commit(root, git, rel, 'Revised café </script>', 'apply: batch second\n\nEditor: JOS')
    write(root, 'build/editor-map.generated.json', json.dumps({'pages': {
        'lesson': [{'source_ref': rel + '#one'}, {'source_ref': rel + '#two'},
                   {'source_ref': 'data/missing.md#one'}]}}))
    assert bh.discover_docs(repo, None) == [rel]
    bundle = bh.build_bundle(repo, [rel])
    doc = bundle['docs'][rel]
    assert bundle['head'] == second
    assert doc['revisions'][0]['attribution'] == 'JOS'
    assert doc['revisions'][0]['parent'] == first
    assert doc['baselines'][0]['name'] == 'baseline-start'
    assert doc['baselines'][0]['message'] == 'Initial lesson'
    assert '<del>Original</del>' in doc['diffs'][f'{first}..{second}']['html']
    assert '&lt;/script&gt;' in doc['diffs'][f'{first}..{second}']['html']
    write(root, 'assets/history.css', 'body { color: red; }')
    write(root, 'assets/history.js', '/* </script> */')
    stale = write(root, 'build/history/old.json', '{}')
    stale_preview = write(root, 'build/history/old.preview.html', 'old')
    retained = write(root, 'build/history/keep.txt', 'keep')
    bh.emit(bundle)
    assert not stale.exists() and not stale_preview.exists()
    assert retained.read_text() == 'keep'
    assert json.loads(Path(bh.BUNDLE_PATH).read_text()) == bundle
    assert json.loads((Path(bh.HISTORY_DIR) / (doc['slug'] + '.json')).read_text()) == doc
    index = json.loads((Path(bh.HISTORY_DIR) / 'index.json').read_text())
    assert index['docs'][0]['revisions'] == 2
    preview = (Path(bh.HISTORY_DIR) / (doc['slug'] + '.preview.html')).read_text()
    assert 'body { color: red; }' in preview
    assert '/* <\\/script> */' in preview
    assert bh.HISTORY_SENTINEL in preview
    bh.emit(bh.build_bundle(repo, []))
    assert sorted(p.name for p in Path(bh.HISTORY_DIR).iterdir()) == ['index.json', 'keep.txt']


def test_empty_git_repository_has_no_head_history_or_diffs(repository, capsys):
    root, git, repo = repository
    assert repo.head_sha() is None
    assert repo.rev_parse('missing-ref') is None
    assert repo.log_follow('missing.md') == []
    assert repo.baseline_tags() == []
    assert repo.read_at('HEAD', 'missing.md') == ''
    for ref in (None, 'EMPTY', 'empty', ''):
        assert repo.read_at(ref, 'missing.md') == ''
    doc = bh.build_doc_history(repo, 'missing.md')
    assert doc.revisions == doc.baselines == []
    assert doc.diffs == {}
    assert bh.main(['--docs', 'missing.md']) == 0
    assert 'nothing to build' in capsys.readouterr().err
    assert not Path(bh.BUNDLE_PATH).exists()


def test_curated_glob_and_map_union_keeps_only_tracked_sources(repository):
    root, git, repo = repository
    files = ['data/matters/m01/matter.json', 'data/matters/m01/facts.md',
             'data/matters/m01/rubric.json', 'data/firm/firm.json',
             'data/taxonomy/skills.json', 'data/taxonomy/tasks.json',
             'data/curriculum/one.md', 'data/custom.md']
    for rel in files:
        write(root, rel, '{}')
    write(root, 'data/matters/README.txt', 'not a directory')
    write(root, 'data/curriculum/ignore.txt', 'not curriculum')
    git('add', 'data')
    git('commit', '-q', '-m', 'Seed sources')
    write(root, 'data/curriculum/untracked.md', 'untracked')
    write(root, 'build/editor-map.generated.json', json.dumps({
        'pages': {'one': [None, {}, {'source_ref': 'data/custom.md#x'}], 'empty': None},
        'blocks': [{'source_ref': 'data/custom.md#y'}, None]}))
    assert bh.discover_docs(repo, None) == sorted(files)
    assert bh.discover_docs(repo, ['data/custom.md', 'missing', 'data/custom.md']) == ['data/custom.md']


@pytest.mark.parametrize('pages', [None, [], 'invalid'])
def test_flat_editor_map_schema_and_empty_pages(repository, pages):
    root, _, _ = repository
    write(root, 'build/editor-map.generated.json', json.dumps({'pages': pages,
        'blocks': [{'source_ref': 'data/a.md#a'}, {'source_ref': 'data/a.md#b'}, {}, 1]}))
    assert bh._docs_from_editor_map() == ['data/a.md']


def test_invalid_editor_map_reports_json_error(repository):
    root, _, repo = repository
    write(root, 'build/editor-map.generated.json', '{invalid')
    with pytest.raises(json.JSONDecodeError):
        bh.discover_docs(repo, None)


@pytest.mark.parametrize(('name', 'email', 'subject', 'body', 'expected'), [
    ('Unknown Person Extra Fourth', 'x@y', '', '', 'UPE'), ('', 'x@y', '', '', '?'),
    ('Unknown', 'x@y', 'edit RSH', '', 'RSH'),
    ('Unknown', 'x@y', 'DVR', 'Attribution: John Sonsteng', 'JOS'),
    ('Unknown', 'x@y', 'DVR', 'Editor: unmapped person', 'DVR'),
    ('John Sonsteng', 'unknown', '', '', 'JOS')])
def test_attribution_fallbacks(name, email, subject, body, expected):
    assert bh._attribution(name, email, subject, body) == expected


@pytest.mark.parametrize(('committer', 'committer_email', 'author_email'), [
    ('apply-engine', 'unknown', 'unknown'), ('human', bh.APPLY_ENGINE_EMAIL, 'unknown'),
    ('human', 'unknown', bh.APPLY_ENGINE_EMAIL)])
def test_apply_identity_without_batch_is_still_an_edit(committer, committer_email, author_email):
    assert bh._classify('change', committer, committer_email, author_email) == ('edit', None)


def test_summary_deduplicates_batches_and_handles_identity_only_edit():
    assert bh._summary({'kind': 'edit', 'shas': ['a', 'b', 'c'],
                        'batches': ['x', 'x', 'y']}) == 'Applied editor changes (x, y) · 3 saves'
    assert bh._summary({'kind': 'edit', 'shas': ['a'], 'batches': []}) == 'Applied editor changes'
    assert bh.coalesce([]) == []


def test_leak_scan_ignores_root_assets_but_checks_nested_assets_and_history(tmp_path):
    write(tmp_path, 'assets/private.json', bh.HISTORY_SENTINEL)
    write(tmp_path, 'nested/assets/page.HTML', bh.HISTORY_SENTINEL)
    write(tmp_path, 'history/doc.txt', 'content')
    write(tmp_path, 'preview.preview.html', 'content')
    violations = bh.assert_no_history_leak(str(tmp_path))
    assert len(violations) == 3
    assert any('nested/assets/page.HTML' in violation for violation in violations)
    assert any('history/ dir' in violation for violation in violations)
    assert any('preview.preview.html' in violation for violation in violations)


@pytest.mark.parametrize('leaking', [False, True])
def test_cli_emits_real_bundle_and_reports_leak_status(repository, monkeypatch, capsys, leaking):
    root, git, _ = repository
    rel = 'data/firm/firm.json'
    commit(root, git, rel, '{"name":"Example"}', 'Initial')
    public = root / 'public'
    write(public, 'page.html', bh.HISTORY_SENTINEL if leaking else '<p>Clean</p>')
    scan = bh.assert_no_history_leak
    monkeypatch.setattr(bh, 'assert_no_history_leak', lambda: scan(str(public)))
    assert bh.main(['--docs', rel, '--check']) == int(leaking)
    assert rel in json.loads(Path(bh.BUNDLE_PATH).read_text())['docs']
    stdout = capsys.readouterr().out
    assert ('LEAK ASSERTION FAILED' if leaking else '--check OK') in stdout


def test_edit_authors_split_runs_and_batchless_edits_coalesce(repository):
    root, git, repo = repository
    rel = 'data/firm/firm.json'
    commit(root, git, rel, 'first', 'apply: batch repeated')
    commit(root, git, rel, 'second', 'apply: batch repeated')
    git('config', 'user.name', 'apply-engine')
    git('config', 'user.email', bh.APPLY_ENGINE_EMAIL)
    commit(root, git, rel, 'third', 'manual apply identity')
    commit(root, git, rel, 'fourth', 'manual apply identity again')
    doc = bh.build_doc_history(repo, rel)
    assert len(doc.revisions) == 2
    newest, oldest = doc.revisions
    assert newest['n_commits'] == oldest['n_commits'] == 2
    assert newest['batches'] == []
    assert oldest['batches'] == ['repeated']
    assert newest['summary'] == 'Applied editor changes · 2 saves'
    assert oldest['summary'] == 'Applied editor changes (repeated) · 2 saves'


def test_annotated_baseline_and_empty_document_history(repository):
    root, git, repo = repository
    commit(root, git, 'data/one.md', 'one', 'Initial')
    git('tag', '-a', 'baseline-reviewed', '-m', 'Review complete')
    tags = repo.baseline_tags()
    assert tags[0]['message'] == 'Review complete'
    assert tags[0]['sha'] == repo.head_sha()
    missing = bh.build_doc_history(repo, 'data/absent.md')
    assert missing.revisions == []
    assert missing.diffs == {}
    assert missing.baselines == tags
    assert repo.read_at('HEAD', 'data/absent.md') == ''
