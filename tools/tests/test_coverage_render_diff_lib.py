"""File-to-HTML integration and lossless redline reconstruction cases."""
import html
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_diff_lib as rd


@pytest.mark.parametrize(('old', 'new'), [('', ''), ('delete all', ''),
    ('line\t one\nsecond', 'line  one\nsecond\n'),
    ('<&> "quoted"', '<script>replacement</script>'),
    ('猫 café \u00a0 words', '犬 café \u00a0 words')])
def test_redline_reconstructs_exact_old_and_new_text(old, new):
    result = rd.diff_html(old, new)
    def reconstruct(removed):
        fragment = re.sub(fr'<{removed}>.*?</{removed}>', '', result.html, flags=re.S)
        return html.unescape(re.sub(r'</?(?:ins|del)>', '', fragment))
    assert reconstruct('ins') == old
    assert reconstruct('del') == new


@pytest.mark.parametrize(('words', 'collapsed'), [(13, False), (14, True)])
def test_context_collapse_threshold_and_preserved_escaped_content(words, collapsed):
    text = ' '.join(f'<word{i}>' for i in range(words))
    result = rd.diff_html(text, text, ctx=3)
    assert ('<details>' in result.html) is collapsed
    fragment = re.sub(r'<summary>.*?</summary>', '', result.html)
    fragment = fragment.replace('<details>', '').replace('</details>', '')
    assert html.unescape(fragment) == text
    assert (result.n_ins, result.n_del) == (0, 0)
    assert '<word' not in result.html


@pytest.mark.parametrize('title', [None, '<Review & approve>'])
def test_cli_reads_utf8_files_and_writes_complete_page(tmp_path, monkeypatch, capsys, title):
    old, new, output = (tmp_path / name for name in ('old.txt', 'new.txt', 'diff.html'))
    old.write_text('café old\n', encoding='utf-8')
    new.write_text('café new\n', encoding='utf-8')
    args = ['render_diff_lib.py', str(old), str(new), str(output)]
    if title is not None:
        args.append(title)
    monkeypatch.setattr(sys, 'argv', args)
    rd.main()
    expected_title = title if title is not None else f'{old} → {new}'
    assert output.read_text() == rd.diff_page(old.read_text(), new.read_text(), expected_title)
    assert '(1 insertions, 1 deletions)' in capsys.readouterr().out


def test_cli_missing_arguments_exits_with_usage(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['render_diff_lib.py'])
    with pytest.raises(SystemExit) as error:
        rd.main()
    assert 'Word-level HTML diff' in str(error.value)


def test_cli_missing_input_does_not_overwrite_output(tmp_path, monkeypatch):
    output = tmp_path / 'result.html'
    output.write_text('preserve previous result')
    monkeypatch.setattr(sys, 'argv', ['render_diff_lib.py', str(tmp_path / 'absent'),
                                    str(tmp_path / 'new'), str(output)])
    with pytest.raises(FileNotFoundError):
        rd.main()
    assert output.read_text() == 'preserve previous result'


def test_cli_unwritable_output_reports_error(tmp_path, monkeypatch):
    source = tmp_path / 'source.txt'
    source.write_text('text')
    monkeypatch.setattr(sys, 'argv', ['render_diff_lib.py', str(source), str(source), str(tmp_path)])
    with pytest.raises(IsADirectoryError):
        rd.main()
