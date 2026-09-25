"""Canonical hashing edge cases and the real instructor rendering chain."""
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import text_norm as tn


@pytest.mark.parametrize("source,expected", [
    (None, ""), ("", ""), (" \r\n\t\u200b\ufeff ", ""),
    (42, "42"), (False, "False"),
    ("Cafe\u0301", "Café"),
    ("A\r\nB\rC\nD\u2028E\u2029F", "A B C D E F"),
    ("‘’‚‛′", "'''''"), ("“”„‟″", '\"\"\"\"\"'),
    ("‐‑‒–—―−", "-------"),
    ("A\u00a0B\u2007C\u202fD\u2009E\u200aF", "A B C D E F"),
    ("a\u200bb\u200cc\u200dd\ufeffe", "abcde"),
    ("Wait…\t  next\vline\fend", "Wait... next line end"),
    ("日本語 Ελληνικά café", "日本語 Ελληνικά café"),
])
def test_normalization_contract_and_utf8_hash(source, expected):
    assert tn.normalize(source) == expected
    assert tn.normalize(tn.normalize(source)) == expected
    assert tn.norm_hash(source) == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_typographic_and_browser_artifacts_share_hash_but_text_edits_do_not():
    canonical = 'Café "client" - next...'
    variants = [canonical, '  Cafe\u0301\u00a0“client” —\r\nnext…\u200b  ',
                'Café\t"client" −\u2029next...']
    assert len({tn.norm_hash(text) for text in variants}) == 1
    assert tn.norm_hash(canonical + "!") != tn.norm_hash(canonical)


def test_instructor_markdown_to_editor_block_hash_matches_browser_plaintext(tmp_path, monkeypatch):
    import build_instructor_bundle as bundle
    import build_site as bs

    # Use the real recorder and renderer, isolating only mutable build state.
    monkeypatch.setattr(bs, "EDMAP", bs._EditorMap())
    source = tmp_path / "facts.md"
    source.write_text('  Cafe\u0301 **“client”** — next… {#b:1234abcd}\n', encoding="utf-8")
    html, blocks = bundle._render_doc(str(tmp_path), "facts.md")
    assert '<strong>“client”</strong>' in html
    assert len(blocks) == 1
    block = blocks[0]
    assert block["has_inline_formatting"] is True
    assert block["original_hash"] == hashlib.sha256('Café "client" - next...'.encode()).hexdigest()
    assert tn.norm_hash('Café “client” —\nnext…') == block["original_hash"]


def test_module_selfcheck_executes_with_three_normalized_examples():
    result = subprocess.run([sys.executable, str(Path(tn.__file__))], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    assert len(result.stdout.splitlines()) == 3
    assert "non breakingspace" in result.stdout
