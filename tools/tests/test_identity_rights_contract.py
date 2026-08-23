"""Current-product identity contract for the public pitch and generated platform."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from fresh_site_build import build_fresh_site
import build_site
import verify_pitch


ROOT = Path(__file__).resolve().parents[2]
HOME_COPY = ROOT / "data/copy/home.json"
PITCH = ROOT / "site/index.html"
README = ROOT / "README.md"
CONTENT_LICENSE = ROOT / "CONTENT-LICENSE.md"
MIT_LICENSE = ROOT / "LICENSE"
MASTER_OUTLINE = ROOT / "docs/master-outline.md"
CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
COPYRIGHT_LINE = "Copyright (c) 2026 Damien Riehl, John O. Sonsteng, Roger S. Haydock"
COVER_BYLINE = "John O. Sonsteng · Damien Riehl · Roger S. Haydock"
MIT_BLOCK = """MIT License

Copyright (c) 2026 Damien Riehl, John O. Sonsteng, Roger S. Haydock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def identity() -> dict:
    return json.loads(HOME_COPY.read_text(encoding="utf-8"))["identity"]


@pytest.fixture(scope="module")
def fresh_site():
    tmp, site, _ = build_fresh_site("identity-rights-")
    try:
        yield Path(site)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_canonical_identity_records_settled_title():
    assert identity()["title"] == "Legal Practicum"
    assert identity()["byline"] == COVER_BYLINE


def test_fresh_generated_pages_use_canonical_current_identity(fresh_site):
    pages = [page for page in fresh_site.rglob("*.html")
             if "assets" not in page.relative_to(fresh_site).parts]
    assert pages
    shell_pages = 0
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "Legal Practicum" in text, page
        assert "Sonsteng Practicum" not in text, page
        if 'data-eb-origin="data/copy/home.json#identity.byline"' in text:
            shell_pages += 1
            assert COVER_BYLINE in text, page
            assert "with Roger S. Haydock" not in text, page
    assert shell_pages


def test_page_shell_identity_tracks_the_canonical_source_and_stays_locked(monkeypatch):
    replacement = {
        "title": "Perturbed Practicum",
        "byline": "Perturbed Authors",
        "host": "Perturbed Host",
    }
    monkeypatch.setattr(build_site, "PRODUCT_IDENTITY", replacement)
    page = build_site.page_shell("index.html", "Home", "HOME", [], "")
    for field, value in replacement.items():
        assert value in page
        assert f'data-eb-origin="data/copy/home.json#identity.{field}"' in page
    assert "PERTURBED PRACTICUM" in page


def test_hand_authored_current_surfaces_use_settled_identity():
    pitch = PITCH.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for text in (pitch, readme):
        assert "Legal Practicum" in text
    assert "The Sonsteng Magnum Opus" not in pitch
    assert readme.startswith("# Legal Practicum\n")


def test_pitch_uses_new_cover_byline():
    pitch = PITCH.read_text(encoding="utf-8")
    normalized_pitch = pitch.replace("&nbsp;", " ").replace("</b>", "").replace("<b>", "")
    assert COVER_BYLINE in normalized_pitch


def test_pitch_body_prose_does_not_name_authors():
    author_name_violations = [
        violation for violation in verify_pitch.verify_page(PITCH)
        if "author surname in body prose" in violation
    ]
    assert author_name_violations == []


def test_historical_mitchell_attribution_and_technical_paths_are_preserved():
    readme = README.read_text(encoding="utf-8")
    pitch = PITCH.read_text(encoding="utf-8")
    assert "Mitchell Hamline\nC-LAB" in readme
    assert "Mitchell Hamline" in pitch
    assert "IGUL" in pitch
    assert "site/platform/" in readme


def test_center_name_is_publicly_retired_but_outline_provenance_stands():
    assert "Center for Law and Business" not in PITCH.read_text(encoding="utf-8")
    assert "Center for Law and Business" not in README.read_text(encoding="utf-8")
    outline = MASTER_OUTLINE.read_text(encoding="utf-8").replace("&", "and")
    assert "Center for Law and Business" in outline


def test_readme_uses_legal_practicum_domain():
    readme = README.read_text(encoding="utf-8")
    assert "legalpracticum.org" in readme
    assert "sonsteng.damienriehl.com" not in readme


def test_content_license_has_conservative_scope_attribution_and_exclusions():
    content = CONTENT_LICENSE.read_text(encoding="utf-8")
    included_section = content.split("## Included content", 1)[1].split("## Required attribution", 1)[0]
    excluded_section = content.split("## Excluded material", 1)[1]
    for included in (
        "data/copy/",
        "data/curriculum/",
        "data/jurisdictions/",
        "data/matters/",
        "site/index.html",
    ):
        assert included in included_section
    for excluded in (
        "software",
        "third-party",
        "data/taxonomy/",
        "uncleared recordings",
    ):
        assert excluded in excluded_section.lower()
    assert CC_BY_URL in content
    assert "indicate if changes were made" in content


def test_content_license_includes_midstate_under_existing_dual_licence():
    content = CONTENT_LICENSE.read_text(encoding="utf-8")
    excluded_section = content.split("## Excluded material", 1)[1]
    assert "data/midstate/" not in excluded_section.lower()


def test_content_license_uses_new_cover_byline():
    content = CONTENT_LICENSE.read_text(encoding="utf-8")
    assert f"Legal Practicum — {COVER_BYLINE}" in content


def test_mit_notice_is_unchanged_and_points_to_layered_scope():
    license_text = MIT_LICENSE.read_text(encoding="utf-8")
    assert license_text.splitlines()[2] == COPYRIGHT_LINE
    assert license_text.startswith(MIT_BLOCK + "\nScope note")
    assert "CONTENT-LICENSE.md" in license_text


def test_public_surfaces_link_content_and_code_licenses(fresh_site):
    assert (fresh_site / "about/content-license.html").exists()
    assert (fresh_site / "about/code-license.html").exists()
    content_page = (fresh_site / "about/content-license.html").read_text(encoding="utf-8")
    assert f'href="{CC_BY_URL}"' in content_page
    assert content_page.count("<h1>") == 1
    assert "<h1>Content License</h1>" in content_page
    code_page = (fresh_site / "about/code-license.html").read_text(encoding="utf-8")
    assert code_page.count("<h1>") == 1
    assert "<h1>Code License</h1>" in code_page
    for rel in (
        "index.html",
        "matters/m01-arbitration-meridian/index.html",
        "chat/index.html",
        "chat/critique.html",
    ):
        html = (fresh_site / rel).read_text(encoding="utf-8")
        assert "CONTENT: CC BY 4.0" in html
        assert "CODE: MIT" in html
        assert "content-license.html" in html
        assert "code-license.html" in html

    generated_text = "\n".join(
        page.read_text(encoding="utf-8") for page in fresh_site.rglob("*.html")
    )
    assert "MIT-LICENSED" not in generated_text

    for surface in (PITCH.read_text(encoding="utf-8"), README.read_text(encoding="utf-8")):
        assert CC_BY_URL in surface
        assert "CONTENT-LICENSE.md" in surface
        assert "LICENSE" in surface
