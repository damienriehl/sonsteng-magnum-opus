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


ROOT = Path(__file__).resolve().parents[2]
HOME_COPY = ROOT / "data/copy/home.json"
PITCH = ROOT / "site/index.html"
README = ROOT / "README.md"
CONTENT_LICENSE = ROOT / "CONTENT-LICENSE.md"
MIT_LICENSE = ROOT / "LICENSE"
CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"


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
    for included in (
        "data/copy/",
        "data/curriculum/",
        "data/jurisdictions/",
        "data/matters/",
        "site/index.html",
    ):
        assert included in content
    for excluded in (
        "software",
        "third-party",
        "data/taxonomy/",
        "data/midstate/",
        "uncleared recordings",
    ):
        assert excluded in content.lower()
    assert CC_BY_URL in content
    assert "Legal Practicum — John O. Sonsteng · Damien Riehl · with Roger S. Haydock" in content
    assert "indicate if changes were made" in content


def test_mit_notice_is_unchanged_and_points_to_layered_scope():
    license_text = MIT_LICENSE.read_text(encoding="utf-8")
    assert "Copyright (c) 2026 Damien Riehl, John O. Sonsteng, Roger S. Haydock" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert "CONTENT-LICENSE.md" in license_text


def test_public_surfaces_link_content_and_code_licenses(fresh_site):
    assert (fresh_site / "about/content-license.html").exists()
    assert (fresh_site / "about/code-license.html").exists()
    content_page = (fresh_site / "about/content-license.html").read_text(encoding="utf-8")
    assert f'href="{CC_BY_URL}"' in content_page
    for rel in ("index.html", "matters/m01-arbitration-meridian/index.html"):
        html = (fresh_site / rel).read_text(encoding="utf-8")
        assert "CONTENT: CC BY 4.0" in html
        assert "CODE: MIT" in html
        assert "content-license.html" in html
        assert "code-license.html" in html

    for surface in (PITCH.read_text(encoding="utf-8"), README.read_text(encoding="utf-8")):
        assert CC_BY_URL in surface
        assert "CONTENT-LICENSE.md" in surface
        assert "LICENSE" in surface
