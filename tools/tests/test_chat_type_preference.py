"""Static contracts for the shared, CSP-safe Large Type preference."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAT = ROOT / "app" / "chat"


def read(name: str) -> str:
    return (CHAT / name).read_text(encoding="utf-8")


def test_preference_loader_migrates_both_keys_without_losing_enabled_state():
    source = read("type-preference.js")
    assert "sonsteng-type-lg" in source
    assert "sonsteng_type_lg" in source
    assert "canonical === '1' || legacy === '1'" in source
    assert "removeItem(LEGACY_KEY)" in source
    assert "addEventListener('storage'" in source


def test_chat_pages_load_preference_before_styles_for_prepaint():
    for name in ("index.html", "critique.html"):
        page = read(name)
        loader = page.index('<script src="type-preference.js"></script>')
        theme = page.index('<link rel="stylesheet" href="../assets/theme.css">')
        assert loader < theme


def test_both_interactive_surfaces_use_shared_large_type_control():
    chat = read("chat.js")
    critique = read("critique.js")
    for source in (chat, critique):
        assert "SonstengTypePreference" in source
        assert "LARGE TYPE" in source
        assert "aria-pressed" in source

    assert "sonsteng_type_lg" not in chat


def test_interactive_heading_sequences_start_with_h1_then_h2():
    chat = read("chat.js")
    critique = read("critique.js")
    assert "el('h2', null, 'Take your transcript with you')" in chat
    assert "el('h2', 'crit-card__name'" in critique


def test_generated_shell_uses_the_same_prepaint_loader():
    generator = (ROOT / "tools" / "build_site.py").read_text(encoding="utf-8")
    generated = (ROOT / "site" / "platform" / "index.html").read_text(encoding="utf-8")
    assert '<script src="{up}assets/type-preference.js"></script>' in generator
    assert '<script src="assets/type-preference.js"></script>' in generated
    assert generated.index('assets/type-preference.js') < generated.index('assets/theme.css')
    assert "localStorage.getItem('sonsteng-type-lg')" not in generated


def test_byok_uses_quiet_casebook_surfaces_and_actions():
    source = read("byok.js")
    assert "background:var(--surface-card)" in source
    assert "background:var(--surface-inset)" in source
    assert "background:var(--surface-featured)" in source
    assert "box-shadow:var(--shadow-offset)" in source
    assert "background:var(--claret);color:var(--ink-invert)" not in source


def test_critique_criterion_headings_follow_the_page_title():
    source = read("critique.js")
    assert "el('h2', 'crit-card__name', criterionName" in source
    assert "el('h3', 'crit-card__name', criterionName" not in source
