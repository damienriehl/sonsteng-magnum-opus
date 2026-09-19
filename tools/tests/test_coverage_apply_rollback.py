"""Real temporary Git transactions at apply rollback and evidence boundaries."""
from pathlib import Path
import shutil
import subprocess

import pytest

import test_apply_suggestions as fixtures

ap = fixtures.ap


@pytest.fixture
def transaction():
    root = fixtures.make_repo()
    store = fixtures.InMemoryEditorStore()
    client = fixtures.FakeClient(store)
    index = fixtures.resolve_index(root, fixtures.SPEC)
    yield root, store, client, index
    shutil.rmtree(root)


def add(transaction, sid, ref, new_text, **extra):
    _, store, _, index = transaction
    block = index[ref]
    return store.add(id=sid, source_ref=ref, new_text=new_text,
                     original_hash=block['original_hash'],
                     original_text=block['original_text'],
                     json_path=block.get('json_path'),
                     kind=extra.pop('kind', 'json_scalar' if block['kind'] == 'json_scalar' else 'prose'),
                     **extra)


def run(transaction, pipeline, *, plan_only=True):
    root, _, client, _ = transaction
    return ap.run_apply(client, pipeline, 'rollback-coverage', canonical_root=root,
                        deploy_plan_only=plan_only, logger=lambda *_: None)


def assert_clean_rollback(transaction, before, head):
    root, store, _, _ = transaction
    assert fixtures.snapshot_data(root) == before
    assert ap.head_sha(root) == head
    assert store.batches['rollback-coverage']['phase'] == ap.PHASE_ROLLED_BACK
    for args, expected in [(['status', '--porcelain'], ''),
                           (['branch', '--list', 'apply/*'], '')]:
        result = subprocess.run(['git', *args], cwd=root, text=True,
                                stdout=subprocess.PIPE, check=True)
        assert result.stdout.strip() == expected
    worktrees = subprocess.run(['git', 'worktree', 'list', '--porcelain'], cwd=root,
                               text=True, stdout=subprocess.PIPE, check=True)
    assert worktrees.stdout.count('worktree ') == 1


@pytest.mark.parametrize('failure', ['build', 'deploy'])
def test_downstream_failure_discards_real_patch_commit_and_worktree(transaction, failure):
    root, store, _, _ = transaction
    ref = fixtures.bref(root, fixtures.M03_MD, 0)
    add(transaction, 'edit', ref, 'Revised intake narrative for review.')
    before, head = fixtures.snapshot_data(root), ap.head_sha(root)

    class FailurePipeline(fixtures.FakePipeline):
        def build(self, worktree):
            if failure == 'build':
                return False, {'step': 'student-bundle'}
            return super().build(worktree)

        def deploy(self, worktree, branch, plan_only):
            assert failure == 'deploy'
            assert plan_only is True
            assert ap.head_sha(worktree) != head
            return False, {'executed': False, 'reason': 'offline fixture rejection'}

    pipeline = FailurePipeline(fixtures.SPEC)
    result = run(transaction, pipeline)
    assert result.reason == ('build_failed:student-bundle' if failure == 'build' else 'deploy_failed')
    assert not result.committed and result.applied == []
    assert [patch.suggestion_id for patch in result.accepted_blocked] == ['edit']
    assert store.rows['edit']['status'] == 'accepted_blocked'
    assert 'Revised intake narrative' in pipeline.worktree_snapshot[fixtures.M03_MD]
    assert_clean_rollback(transaction, before, head)


@pytest.mark.parametrize('drift', ['missing-source', 'stale-hash'])
def test_apply_time_drift_rejects_entire_group_before_patch(transaction, drift):
    root, store, _, _ = transaction
    ref = fixtures.bref(root, fixtures.M03_MD, 0)
    row = add(transaction, 'a-drift', ref, 'Must never land.', group_id='atomic')
    add(transaction, 'b-peer', fixtures.M03_EX + '#caption', 'Must also never land.', group_id='atomic')
    if drift == 'missing-source':
        row['source_ref'] = 'data/missing.md#b00000000'
    else:
        row['original_hash'] = 'stale-hash'
    before, head = fixtures.snapshot_data(root), ap.head_sha(root)
    pipeline = fixtures.FakePipeline(fixtures.SPEC)
    result = run(transaction, pipeline)
    assert result.applied == []
    assert {row['id'] for row in result.drift} == {'a-drift', 'b-peer'}
    assert all(store.rows[sid]['status'] == 'drift' for sid in ['a-drift', 'b-peer'])
    assert 'Must never land' not in pipeline.worktree_snapshot[fixtures.M03_MD]
    assert_clean_rollback(transaction, before, head)


@pytest.mark.parametrize('operation', ['insert_after', 'delete'])
def test_structural_commit_finalizes_source_evidence(transaction, operation):
    root, store, _, _ = transaction
    store.prod_base = 'verified-prod-frontier'
    ref = fixtures.bref(root, fixtures.M03_MD, 0)
    original = transaction[3][ref]['original_text']
    add(transaction, 'structure', ref, 'Inserted review paragraph.' if operation == 'insert_after' else None,
        kind=operation, group_id='review-group', created_at=100)
    result = run(transaction, fixtures.FakePipeline(fixtures.SPEC), plan_only=False)
    assert result.committed
    revision, = store.batches['rollback-coverage']['review_revisions']
    evidence, = revision['operations']
    assert revision['commit_sha'] == ap.head_sha(root)
    assert revision['prod_base'] == 'verified-prod-frontier'
    assert revision['suggestion_ids'] == ['structure']
    assert revision['source_original_text'] and revision['source_proposed_text']
    assert evidence['kind'] == operation
    assert evidence['group_id'] == 'review-group'
    assert evidence['suggestion_id'] == 'structure'
    content = Path(root, fixtures.M03_MD).read_text()
    if operation == 'insert_after':
        assert 'Inserted review paragraph.' in content
    else:
        assert original not in content


def test_replay_missing_leaf_rolls_back_other_retained_edits_without_third_attempt(transaction, monkeypatch):
    root, store, _, _ = transaction
    add(transaction, 'bad-number', fixtures.M03_BUS + '#engagement.rate', 'not numeric')
    add(transaction, 'retained-a', fixtures.M03_EX + '#caption', 'Updated caption')
    add(transaction, 'retained-b', fixtures.M07_EX + '#caption', 'Other updated caption')
    before, head = fixtures.snapshot_data(root), ap.head_sha(root)
    real_apply = ap.apply_file_patches
    counts = {}

    def concurrent_change_during_replay(worktree, relpath, patches):
        for patch in patches:
            counts[patch.suggestion_id] = counts.get(patch.suggestion_id, 0) + 1
        if relpath == fixtures.M03_EX and counts.get('retained-a') == 2:
            # Simulate removal of the mapped leaf before the bounded replay.
            # The actual scalar patcher classifies this as validation_error.
            Path(worktree, relpath).write_text('{}')
        return real_apply(worktree, relpath, patches)

    monkeypatch.setattr(ap, 'apply_file_patches', concurrent_change_during_replay)
    result = run(transaction, fixtures.FakePipeline(fixtures.SPEC))
    assert result.reason == 'rollout_replay_failed'
    assert not result.committed and result.applied == []
    reasons = {row['id']: row['outcome_reason'] for row in result.needs_human}
    assert reasons['retained-a'].startswith('rollout_replay_failed:')
    assert reasons['retained-b'] == 'rollout_replay_batch_rollback'
    assert counts == {'bad-number': 1, 'retained-a': 2, 'retained-b': 2}
    assert all(row['status'] == 'needs_human' for row in store.rows.values())
    assert_clean_rollback(transaction, before, head)
