"""Contract checks for the shared Radical Casebook visual vocabulary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "site/platform/assets/theme.css"
PREVIEW = ROOT / "site/platform/assets/preview.html"
GENERATOR = ROOT / "tools/build_site.py"
PLATFORM_CSS = ROOT / "site/platform/platform.css"


def css() -> str:
    return THEME.read_text(encoding="utf-8")


def test_radical_casebook_tokens_and_surfaces_are_shared():
    theme = css()
    for token in (
        "--paper:#f3ead5",
        "--surface-card:#fffaf0",
        "--surface-featured:#ede0c7",
        "--surface-inset:#eadcbe",
        "--ink:#281e18",
        "--claret:#78363e",
        "--claret-strong:#642b33",
        "--border-strong:#3a2920",
        "--shadow-offset:5px 5px 0 var(--paper-deep)",
        "--fs-editorial-label:",
    ):
        assert token in theme
    assert ".card{" in theme and "background:var(--surface-card)" in theme
    assert ".card--featured{" in theme and "background:var(--surface-featured)" in theme
    assert ".editorial-label" in theme


def test_large_type_overrides_the_hierarchy_scale():
    block = css().split("html.type-lg{", 1)[1].split("}", 1)[0]
    for token in (
        "--fs-mono-xs",
        "--fs-editorial-label",
        "--fs-base",
        "--fs-md",
        "--fs-lg",
        "--fs-xl",
        "--fs-2xl",
        "--fs-display",
    ):
        assert token in block


def test_accessibility_and_output_modes_remain_first_class():
    theme = css()
    for contract in (
        ":focus-visible",
        "@media (prefers-reduced-motion: reduce)",
        "@media (prefers-contrast: more)",
        "@media print",
    ):
        assert contract in theme


def test_preview_uses_only_local_stylesheets_and_exercises_hierarchy():
    preview = PREVIEW.read_text(encoding="utf-8")
    assert 'href="fonts.css"' in preview
    assert 'href="theme.css"' in preview
    stylesheet_tags = [line for line in preview.splitlines() if 'rel="stylesheet"' in line]
    assert all("http://" not in line and "https://" not in line for line in stylesheet_tags)
    assert 'class="editorial-label"' in preview
    assert 'class="card card--featured"' in preview


def test_generated_shell_uses_the_radical_casebook_hierarchy():
    source = GENERATOR.read_text(encoding="utf-8")
    for contract in (
        'class="masthead__brand"',
        'class="hero__proposition"',
        'class="editorial-label"',
        'class="section-head"',
        'class="card volume',
        'card--featured',
        'class="doc-card"',
        'class="viz-card"',
    ):
        assert contract in source


def test_generated_layout_preserves_visual_rank_and_distinct_surfaces():
    generated = PLATFORM_CSS.read_text(encoding="utf-8")
    assert ".hero__proposition{" in generated
    assert "font-size:var(--fs-display)" in generated
    assert ".hero .lede{" in generated
    assert "font-size:var(--fs-md)" in generated
    assert ".section-head .editorial-label{" in generated
    assert "font-size:var(--fs-editorial-label)" in generated
    assert ".volume.card--featured{" in generated
    assert "background:var(--surface-featured)" in generated


def test_page_level_css_uses_shared_palette_tokens_only():
    source = GENERATOR.read_text(encoding="utf-8")
    platform_css = source.split('PLATFORM_CSS = r"""', 1)[1].split('"""', 1)[0]
    assert "#" not in platform_css
    for forbidden in ("rgb(", "rgba(", "hsl(", "hsla("):
        assert forbidden not in platform_css
