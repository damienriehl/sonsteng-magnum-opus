"""Disk-backed coverage of pitch validation, diagnostics, and failure contracts."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_pitch as pitch


SHELL = '<html lang="EN"><meta name="VIEWPORT" content="width=device-width, initial-scale=1">{body}</html>'


def write_page(tmp_path, body='', name='page.html'):
    page = tmp_path / name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(SHELL.format(body=body), encoding='utf-8')
    return page


def test_real_disk_links_and_cli_report(tmp_path, capsys):
    site = tmp_path / 'site'
    write_page(site, '<a name="legacy"></a><p id="space id">Target</p>', 'nested/index.html')
    page = write_page(site, '<a href="/nested/#legacy">Legacy</a><a href="nested#space%20id">Encoded</a>'
                      '<a href="nested/index.html">Whole page</a>'
                      '<a href="https://example.invalid/">External citation</a><a href="mailto:person@example.invalid">Mail</a>')
    assert pitch.main([str(page)]) == 0
    output = capsys.readouterr()
    assert f'PASS {page}' in output.out
    assert '1 page(s) passed' in output.out
    assert output.err == ''


@pytest.mark.parametrize('href,target,expected', [
    ('missing.html', None, 'unresolved internal link missing.html'),
    ('asset.txt#heading', b'text', 'fragment on a non-HTML target'),
    ('target.html#heading', b'\xff', 'cannot read internal link target'),
    ('target.html#heading', b'<p id="other">text</p>', 'unresolved internal anchor #heading'),
])
def test_internal_link_failure_reports(tmp_path, href, target, expected):
    if target is not None:
        (tmp_path / href.split('#')[0]).write_bytes(target)
    page = write_page(tmp_path, f'<a href="{href}">Read more</a>')
    assert any(expected in error for error in pitch.verify_page(page))


@pytest.mark.parametrize('body,host,location', [
    ('<link rel="stylesheet preload" href="//cdn.invalid/a.css">', 'cdn.invalid', 'link[href]'),
    ('<p style="background:url(https://inline.invalid/image)">Hello</p>', 'inline.invalid', 'inline CSS'),
    ('<style>@import "https://sheet.invalid/base.css";</style>', 'sheet.invalid', 'CSS'),
    ('<style>p{background:url(//image.invalid/p.png)}</style>', 'image.invalid', 'CSS'),
    ('<svg><use xlink:href="https://svg.invalid/icons#book" /></svg>', 'svg.invalid', 'use[xlink:href]'),
    ('<video poster="//poster.invalid/a.png"></video>', 'poster.invalid', 'video[poster]'),
    ('<img srcset="local.png 1x, https://retina.invalid/a.png 2x">', 'retina.invalid', 'img[srcset]'),
])
def test_external_assets_are_rejected_across_html_and_css(tmp_path, body, host, location):
    assert any(f'external asset host {host} in {location}' in error
               for error in pitch.verify_page(write_page(tmp_path, body)))


def test_local_assets_hidden_content_and_omitted_body_are_accepted(tmp_path):
    page = write_page(tmp_path, '<link rel="canonical" href="https://example.invalid/page">'
                      '<img src="data:image/png;base64,AA//AA"><iframe src="about:blank"></iframe>'
                      '<video src="blob:local"></video><template>Sonsteng 123</template>'
                      '<p class="ribbon">Riehl 42</p><p>Learn by practicing.</p>')
    assert pitch.verify_page(page) == []


def test_long_statistics_excerpt_is_bounded(tmp_path):
    text = '42 ' + 'lengthy words ' * 20
    errors = pitch.verify_page(write_page(tmp_path, f'<p>{text}</p>'))
    assert len(errors) == 1
    excerpt = errors[0].split('block: ', 1)[1]
    assert len(excerpt) == 80 and excerpt.endswith('...')


def test_wrong_viewport_is_diagnostic(tmp_path):
    page = write_page(tmp_path)
    page.write_text(page.read_text().replace('initial-scale=1', 'initial-scale=2'))
    assert pitch.verify_page(page) == [f'viewport meta must use content="{pitch.EXPECTED_VIEWPORT_CONTENT}"']


@pytest.mark.parametrize('kind', ['missing', 'invalid-utf8'])
def test_unreadable_inputs_are_cli_failures(tmp_path, capsys, kind):
    page = tmp_path / 'broken.html'
    if kind == 'invalid-utf8':
        page.write_bytes(b'\xff')
    assert pitch.main([str(page)]) == 1
    result = capsys.readouterr()
    expected = 'cannot read page:' if kind == 'missing' else 'cannot parse page as UTF-8 HTML:'
    assert expected in result.err
    assert '1 violation(s)' in result.err
    assert 'PASS' not in result.out


def test_default_discovery_excludes_nested_platform_and_sorts(tmp_path, monkeypatch, capsys):
    site = tmp_path / 'site'
    first = write_page(site, name='a.html')
    second = write_page(site, name='more/b.html')
    write_page(site, '<p>99 prohibited</p>', name='platform/generated.html')
    write_page(site, '<p>99 prohibited</p>', name='more/platform/generated.html')
    monkeypatch.setattr(pitch, 'SITE', site)
    assert pitch.default_pages() == [first, second]
    assert pitch.main([]) == 0
    result = capsys.readouterr()
    assert '2 page(s) passed' in result.out
    assert result.err == ''


def test_empty_default_discovery_has_actionable_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pitch, 'SITE', tmp_path)
    assert pitch.main([]) == 1
    assert capsys.readouterr().err == 'verify_pitch: no pitch pages found\n'


def test_cli_aggregates_violations_and_continues_to_valid_page(tmp_path, capsys):
    bad = write_page(tmp_path, '<p>Riehl 42</p>', 'bad.html')
    good = write_page(tmp_path, name='good.html')
    assert pitch.main([str(bad), str(good)]) == 1
    output = capsys.readouterr()
    assert f'PASS {good}' in output.out
    assert '2 violation(s)' in output.err
    assert 'author surname' in output.err and 'statistic outside' in output.err


@pytest.mark.parametrize('original,replacement,expected', [
    ('<main id="main" tabindex="-1">', '<main id="different" tabindex="-1">', 'one main landmark'),
    ('<section id="skills">', '<section></section><section id="skills">', 'exactly nine major sections'),
    ('<details class="proof">', '<details class="proof" open>', 'closed by default'),
    (pitch.EXPECTED_PROOF_SUMMARIES[1], pitch.EXPECTED_PROOF_SUMMARIES[0], 'summaries must be unique'),
    ('id="problem"', 'id="different"', 'must open with the problem'),
    ('Midstate', 'Otherstate', 'demonstration must name Midstate'),
    ('SPEU', 'Union', 'demonstration must name SPEU'),
    ('Pat Rogers', 'Someone Else', 'demonstration must name Pat Rogers'),
    ('data-matter-id="m02"', 'data-matter-id="m01"', '20 uniquely identified'),
    ('class="matter-shape"', 'class="missing-field"', 'fields must be shape, skills, length, then link'),
    ('One week', 'Short course', 'omits a proposed length option'),
    ('function openDrawer()', 'function unusedOpen()', 'requires openDrawer()'),
    ('function closeDrawer()', 'function unusedClose()', 'requires closeDrawer()'),
    ('opener=drawerOpener&&document.contains(drawerOpener)?drawerOpener:fallbackOpener;',
     'opener=fallbackOpener;', 'restore focus to the invoking element'),
    ('drawerOpener=document.activeElement;', 'anotherOpener=document.activeElement;',
     'remember the invoking element'),
])
def test_pitch_structure_mutations_report_specific_contract(tmp_path, original, replacement, expected):
    source = (pitch.ROOT / 'site/index.html').read_text(encoding='utf-8')
    assert original in source
    source = source.replace(original, replacement)
    page = tmp_path / 'pitch.html'
    page.write_text(source, encoding='utf-8')
    errors = pitch._pitch_contract_errors(pitch._parse(page), source)
    assert any(expected in error for error in errors)


def test_skip_link_after_main_is_rejected(tmp_path):
    source = (pitch.ROOT / 'site/index.html').read_text(encoding='utf-8')
    link = '<a class="skip-link" href="#main">Skip to content</a>'
    assert link in source
    source = source.replace(link, '').replace('</main>', '</main>' + link)
    page = tmp_path / 'pitch.html'
    page.write_text(source)
    assert 'pitch Skip to content link must precede the main landmark' in pitch._pitch_contract_errors(pitch._parse(page), source)


def test_short_pitch_reports_word_count_and_structure_through_public_api(tmp_path):
    page = write_page(tmp_path, '<main><script>querySelectorAll(\'details.proof\')</script><p>Brief.</p></main>')
    errors = pitch.verify_page(page)
    assert any('authored prose has 1 words' in error for error in errors)
    assert any('exactly nine major sections' in error for error in errors)


def test_unterminated_javascript_function_is_not_a_valid_focus_contract():
    assert pitch._javascript_function_body('function openDrawer() { if (ready) { act(); }', 'openDrawer') is None
    assert pitch._javascript_function_body('function openDrawer() { if (ready) { act(); } }', 'openDrawer') == ' if (ready) { act(); } '
