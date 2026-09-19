"""Read-only report integration and notification failure/deduplication boundaries."""
import io
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import todo_report as report


@pytest.fixture
def task_file(tmp_path):
    path = tmp_path / 'tasks.md'
    path.write_text('## Today\n- [ ] **T01 — Synthetic private prose** `due:2026-09-19`\n')
    return path


def test_real_cli_dry_run_and_due_today_report(task_file, tmp_path):
    before = task_file.read_bytes()
    assert report.main(['--todo-file', str(task_file), '--state-file', str(tmp_path / 'state'), '--today', '2026-09-19', '--dry-run']) == 0
    assert task_file.read_bytes() == before
    assert not (tmp_path / 'state').exists()
    parsed = report.build_report(report.parse_tasks(task_file.read_text()), '2026-09-19')
    assert report.notify_title(parsed).endswith('1 due today')
    assert 'Synthetic private prose' not in report.notify_body(parsed, '2026-09-19')
    assert '@?' in report.notify_body(parsed, '2026-09-19')


@pytest.mark.parametrize('error', [OSError('fixture write failure'), urllib.error.URLError('fixture offline')])
def test_publish_failure_does_not_advance_state(task_file, tmp_path, capsys, error):
    state = tmp_path / 'state.json'
    report.save_state(str(state), 'previous', 2, 'earlier')
    before = state.read_bytes()
    def fail(*args, **kwargs):
        raise error
    assert report.run(todo_path=str(task_file), state_path=str(state), publish=fail, topic='synthetic-topic', today='2026-09-19', out=io.StringIO()) == 0
    assert 'ntfy publish failed' in capsys.readouterr().err
    assert state.read_bytes() == before


def test_missing_topic_keeps_state_absent(task_file, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(report.ENV_TOPIC, raising=False)
    monkeypatch.setattr(report, 'DEFAULT_TOPIC_FILE', str(tmp_path / 'absent-topic'))
    assert report.run(todo_path=str(task_file), state_path=str(tmp_path / 'state'), out=io.StringIO()) == 0
    assert 'no ntfy topic' in capsys.readouterr().err
    assert not (tmp_path / 'state').exists()


def test_duplicate_ids_warn_but_report_is_available(task_file, tmp_path, capsys):
    task_file.write_text(task_file.read_text() * 2)
    output = io.StringIO()
    assert report.run(todo_path=str(task_file), state_path=str(tmp_path / 'state'), dry_run=True, out=output) == 0
    assert 'duplicate task ids: T01' in capsys.readouterr().err
    assert '2 open' in output.getvalue()


@pytest.mark.parametrize('content,expected', [('', None), (' \n', None), (' synthetic-topic\n', 'synthetic-topic')])
def test_topic_from_synthetic_fixture(tmp_path, monkeypatch, content, expected):
    topic = tmp_path / 'topic-fixture'
    topic.write_text(content)
    monkeypatch.delenv(report.ENV_TOPIC, raising=False)
    monkeypatch.setattr(report, 'DEFAULT_TOPIC_FILE', str(topic))
    assert report.resolve_topic() == expected


def test_state_path_selection_and_unreadable_or_corrupt_state(tmp_path, monkeypatch):
    monkeypatch.delenv(report.ENV_STATE_FILE, raising=False)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    assert report.default_state_path() == str(tmp_path / 'sonsteng/todo-report.json')
    monkeypatch.setenv(report.ENV_STATE_FILE, str(tmp_path / 'custom.json'))
    assert report.default_state_path() == str(tmp_path / 'custom.json')
    assert report.load_state(str(tmp_path)) == {}
    bad = tmp_path / 'bad.json'
    bad.write_text('{invalid')
    assert report.load_state(str(bad)) == {}
