"""Regression contracts for pitch accessibility auditing and palette contrast."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PITCH = ROOT / "site/index.html"
AUDIT = ROOT / "tools/a11y_audit.js"
PREFLIGHT = ROOT / "tools/preflight.sh"


def _channel(value: int) -> float:
    component = value / 255
    return component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    channels = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    return sum(weight * _channel(channel) for weight, channel in zip((0.2126, 0.7152, 0.0722), channels))


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _token(source: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{6}})", source)
    assert match, f"missing {name} token"
    return match.group(1).lower()


def test_remedy_a_tokens_meet_normal_text_contrast_on_pitch_surfaces():
    source = PITCH.read_text(encoding="utf-8")
    ink_faint = _token(source, "--ink-faint")
    brass = _token(source, "--brass")
    brass_lite = _token(source, "--brass-lite")
    assert ink_faint == "#675e51"
    assert brass == "#785816"
    assert brass_lite == "#c6a04c"

    # Include each solid endpoint or rendered light surface where these text
    # tokens paint. The band gradient's darker endpoint is the limiting case.
    pairs = {
        "muted on paper": (ink_faint, "#f4efe4"),
        "muted on darkest cream band": (ink_faint, "#e4d9c2"),
        "gold on paper": (brass, "#f4efe4"),
        "gold on darkest cream band": (brass, "#e4d9c2"),
        "gold on critique card": (brass, "#f2ead9"),
        "gold link on ink": (brass_lite, "#1d1a16"),
        "gold link on warm dark": (brass_lite, "#241a17"),
        "light gold on composited AI badge": (brass_lite, "#433a29"),
        "cream button text on claret": ("#f5ece0", "#7c1e2b"),
        "cream button text on hover claret": ("#f5ece0", "#5c141d"),
    }

    failures = {
        label: _contrast(foreground, background)
        for label, (foreground, background) in pairs.items()
        if _contrast(foreground, background) < 4.5
    }
    assert failures == {}


def test_pitch_uses_remedy_a_tokens_for_buttons_and_dark_sections():
    source = PITCH.read_text(encoding="utf-8")
    assert ".hero-cta{" in source and "background:var(--claret);color:#f5ece0" in source
    assert ".band-ink details.proof>summary" in source
    assert "footer a{color:var(--brass-lite)}" in source


def test_default_accessibility_pages_include_pitch_root():
    source = AUDIT.read_text(encoding="utf-8")
    assert "const PITCH = 'file://' + path.join(REPO, 'site', 'index.html');" in source
    assert ".concat([PITCH, EDITOR_HARNESS])" in source
    assert "if (explicitTargets || url === PITCH) return [{url, mode:'baseline'}];" in source


def test_accessibility_audit_finishes_scroll_reveal_before_navigation():
    source = AUDIT.read_text(encoding="utf-8")
    emulation = "page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}])"
    assert emulation in source
    assert source.index(emulation) < source.index("page.goto(url")


def test_accessibility_audit_skips_aria_hidden_decorative_text():
    source = AUDIT.read_text(encoding="utf-8")
    assert "el.closest('[aria-hidden=\"true\"]')" in source
    assert "if (!text || !visible(el) || decorative(el)) return;" in source


def test_accessibility_audit_composites_translucent_background_layers():
    source = AUDIT.read_text(encoding="utf-8")
    assert "const a = fg.a + bg.a * (1 - fg.a);" in source
    assert "bg[name] * bg.a * (1 - fg.a)" in source
    assert "/ a;" in source


def test_preflight_accessibility_gate_uses_default_page_set():
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert 'run "accessibility audit (0 FAIL required)"  node tools/a11y_audit.js' in source
