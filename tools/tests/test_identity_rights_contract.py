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


ROOT = Path(__file__).resolve().parents[2]
HOME_COPY = ROOT / "data/copy/home.json"
PITCH = ROOT / "site/index.html"
README = ROOT / "README.md"
CONTENT_LICENSE = ROOT / "CONTENT-LICENSE.md"
MIT_LICENSE = ROOT / "LICENSE"
CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
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


def test_canonical_identity_records_settled_title_credit_and_host():
    assert identity() == {
        "title": "Legal Practicum",
        "byline": "John O. Sonsteng · Damien Riehl · with Roger S. Haydock",
        "host": "Hosted by Damien Riehl",
    }


def test_fresh_generated_pages_use_canonical_current_identity(fresh_site):
    pages = [page for page in fresh_site.rglob("*.html")
             if "assets" not in page.relative_to(fresh_site).parts]
    assert pages
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "Legal Practicum" in text, page
        assert "Sonsteng Practicum" not in text, page
    home = (fresh_site / "index.html").read_text(encoding="utf-8")
    assert identity()["byline"] in home
    assert identity()["host"] in home


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
        assert identity()["byline"] in text.replace("&nbsp;", " ").replace("</b>", "").replace("<b>", "")
        assert identity()["host"] in text
    assert "The Sonsteng Magnum Opus" not in pitch
    assert readme.startswith("# Legal Practicum\n")


def test_historical_mitchell_attribution_and_technical_names_are_preserved():
    readme = README.read_text(encoding="utf-8")
    pitch = PITCH.read_text(encoding="utf-8")
    assert "Mitchell Hamline\nC-LAB" in readme
    assert "run jointly by Mitchell Hamline's C-LAB and IGUL" in pitch
    assert "sonsteng.damienriehl.com" in readme
    assert "site/platform/" in readme


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
        "data/midstate/",
        "uncleared recordings",
    ):
        assert excluded in excluded_section.lower()
    assert CC_BY_URL in content
    assert "Legal Practicum — John O. Sonsteng · Damien Riehl · with Roger S. Haydock" in content
    assert "indicate if changes were made" in content


def test_mit_notice_is_unchanged_and_points_to_layered_scope():
    license_text = MIT_LICENSE.read_text(encoding="utf-8")
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
