"""Current-product identity contract for the public pitch and generated platform."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from fresh_site_build import build_fresh_site


ROOT = Path(__file__).resolve().parents[2]
HOME_COPY = ROOT / "data/copy/home.json"
PITCH = ROOT / "site/index.html"
README = ROOT / "README.md"


def identity() -> dict:
    return json.loads(HOME_COPY.read_text(encoding="utf-8"))["identity"]


def test_canonical_identity_records_settled_title_credit_and_host():
    assert identity() == {
        "title": "Legal Practicum",
        "byline": "John O. Sonsteng · Damien Riehl · with Roger S. Haydock",
        "host": "Hosted by Damien Riehl",
    }


def test_fresh_generated_pages_use_canonical_current_identity():
    tmp, site, _ = build_fresh_site("identity-rights-")
    try:
        pages = [page for page in Path(site).rglob("*.html")
                 if "assets" not in page.relative_to(site).parts]
        assert pages
        for page in pages:
            text = page.read_text(encoding="utf-8")
            assert "Legal Practicum" in text, page
            assert "Sonsteng Practicum" not in text, page
        home = (Path(site) / "index.html").read_text(encoding="utf-8")
        assert identity()["byline"] in home
        assert identity()["host"] in home
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
