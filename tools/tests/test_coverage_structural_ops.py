"""Structural edit boundaries exercised through the real markdown renderer."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_site
import stamp_block_ids
import structural_ops as ops


def stamped(text):
    return stamp_block_ids.stamp_md_text(text, set())[0]


def blocks(text):
    spans = []
    build_site.markdown(text, spans=spans)
    return spans


@pytest.mark.parametrize('payload', [None, 4, '', '  ', '\n\t'])
def test_insert_rejects_empty_or_nonstring_payload(payload):
    text = stamped('Anchor')
    with pytest.raises(ops.StructuralError, match='empty payload'):
        ops.op_insert_after(text, blocks(text)[0]['bid'], payload, set())


def test_ordered_list_edit_chain_preserves_ids_and_rendering():
    text = stamped('1. First\n2. Last')
    first, last = [b['bid'] for b in blocks(text)]
    inserted, new = ops.op_insert_after(text, first, 'Middle', {first, last})
    assert [b['raw'] for b in blocks(inserted)] == ['First', 'Middle', 'Last']
    moved = ops.op_move(inserted, first, last)
    assert [b['bid'] for b in blocks(moved)] == [new, last, first]
    deleted = ops.op_delete(moved, new)
    assert [b['raw'] for b in blocks(deleted)] == ['Last', 'First']
    html = build_site.markdown(deleted)
    assert html.count('<ol>') == 1
    assert html.count('<li') == 2


@pytest.mark.parametrize('text,index,expected', [('First\n\nLast', 1, 'First'), ('Only', 0, ''), ('First\n\nLast', 0, 'Last')])
def test_delete_at_boundaries_removes_separator(text, index, expected):
    source = stamped(text)
    output = ops.op_delete(source, blocks(source)[index]['bid'])
    assert [b['raw'] for b in blocks(output)] == ([expected] if expected else [])
    assert not output.endswith('\n\n')


def test_move_paragraph_between_adjacent_headings():
    source = stamped('# One\n## Two\n\nMove me')
    a, b, c = [block['bid'] for block in blocks(source)]
    output = ops.op_move(source, c, a)
    assert [block['bid'] for block in blocks(output)] == [a, c, b]


@pytest.mark.parametrize('text', ['# Two words', '- Two words', '> Two words'])
def test_split_rejects_nonparagraph_blocks(text):
    source = stamped(text)
    with pytest.raises(ops.StructuralError, match='paragraphs only'):
        ops.op_split(source, blocks(source)[0]['bid'], 'Two', 'words', set())


@pytest.mark.parametrize('first,second', [('ffffffff', '00000000'), ('00000000', 'ffffffff')])
def test_merge_rejects_missing_identity(first, second):
    with pytest.raises(ops.StructuralError, match='unknown bid'):
        ops.op_merge('Known {#b:00000000}', first, second)


def test_move_rejects_same_identity():
    with pytest.raises(ops.StructuralError, match='after itself'):
        ops.op_move('Known {#b:00000000}', '00000000', '00000000')


def test_list_item_cannot_move_after_paragraph():
    source = stamped('- Item\n\nParagraph')
    a, b = [block['bid'] for block in blocks(source)]
    with pytest.raises(ops.StructuralError, match='list item may only move'):
        ops.op_move(source, a, b)


def test_duplicate_source_identity_is_ambiguous():
    with pytest.raises(ops.StructuralError, match='matches 2 block'):
        ops.locate_block('One {#b:00000000}\n\nTwo {#b:00000000}', '00000000')


def test_edit_rejects_preexisting_unmarked_sibling():
    with pytest.raises(ops.StructuralError, match='unmarked block'):
        ops.op_insert_after('Known {#b:00000000}\n\nUnmarked', '00000000', 'New', set())


def test_edit_rejects_duplicate_unrelated_identity():
    text = 'Anchor {#b:00000000}\n\nOne {#b:11111111}\n\nTwo {#b:11111111}'
    with pytest.raises(ops.StructuralError, match='duplicate bid'):
        ops.op_insert_after(text, '00000000', 'New', set())


def test_multiline_crlf_split_and_merge_round_trip():
    source = stamped('First words\nlast words').replace('\n', '\r\n')
    first = blocks(source)[0]['bid']
    output, second = ops.op_split(source, first, 'First words', 'last words', set())
    merged = ops.op_merge(output, first, second)
    assert [(b['raw'], b['bid']) for b in blocks(merged)] == [('First words last words', first)]
    assert '\r' not in merged


def test_insert_preserves_adjacent_heading_blocks_and_their_tags():
    source = stamped('# First\n## Second')
    original = blocks(source)
    output, new = ops.op_insert_after(source, original[0]['bid'], 'Between headings', set())
    parsed = blocks(output)
    assert [(b['tag'], b['raw'], b['bid']) for b in (parsed[0], parsed[2])] == [(b['tag'], b['raw'], b['bid']) for b in original]
    assert (parsed[1]['tag'], parsed[1]['raw'], parsed[1]['bid']) == ('p', 'Between headings', new)
