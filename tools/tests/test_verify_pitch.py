"""Contract tests for the hand-authored pitch-page verification gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import verify_pitch  # noqa: E402


VALID_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>body { background-image: url(data:image/png;base64,AA//AA); }</style>
  <title>Legal Practicum</title>
</head>
<body>
  <nav><a href="#case-study">Case study</a></nav>
  <header><p class="byline">John O. Sonsteng · Damien Riehl · Roger S. Haydock</p></header>
  <main>
    <section id="case-study">
      <h2>A practical legal education</h2>
      <p>Students learn by doing the work of lawyers.</p>
      <details>
        <summary>THE PROOF · 82% reported improvement</summary>
        <p>A survey of 1,200 participants supports the result.</p>
        <cite>Sonsteng et al. (2020)</cite>
      </details>
    </section>
  </main>
  <footer><p>Copyright 2026 · Sonsteng · Riehl · Haydock</p></footer>
</body>
</html>
"""


@pytest.fixture
def page(tmp_path: Path) -> Path:
    path = tmp_path / "index.html"
    path.write_text(VALID_PAGE, encoding="utf-8")
    return path


def messages(path: Path) -> str:
    return "\n".join(verify_pitch.verify_page(path))


def test_valid_self_contained_page_passes(page: Path):
    assert verify_pitch.verify_page(page) == []


def test_broken_internal_anchor_fails(page: Path):
    page.write_text(VALID_PAGE.replace('href="#case-study"', 'href="#missing"'),
                    encoding="utf-8")

    assert "unresolved internal anchor #missing" in messages(page)


def test_page_over_250_kb_fails(page: Path):
    page.write_text(VALID_PAGE + " " * 250_000, encoding="utf-8")

    assert "exceeds 250,000-byte ceiling" in messages(page)


def test_external_asset_host_fails(page: Path):
    page.write_text(
        VALID_PAGE.replace(
            "</main>",
            '<img src="https://assets.example.test/chart.png" alt="Chart"></main>',
        ),
        encoding="utf-8",
    )

    assert "external asset" in messages(page)
    assert "assets.example.test" in messages(page)


@pytest.mark.parametrize("surname", ["Sonsteng", "Riehl", "Haydock"])
def test_author_surname_in_body_prose_fails(page: Path, surname: str):
    page.write_text(
        VALID_PAGE.replace(
            "Students learn by doing the work of lawyers.",
            f"{surname} created a learn-by-doing course.",
        ),
        encoding="utf-8",
    )

    assert f"author surname in body prose: {surname}" in messages(page)


def test_statistic_outside_the_proof_block_fails(page: Path):
    page.write_text(
        VALID_PAGE.replace(
            "Students learn by doing the work of lawyers.",
            "Students complete 12 simulations before graduation.",
        ),
        encoding="utf-8",
    )

    assert "statistic outside a THE PROOF block" in messages(page)


def test_command_exits_nonzero_and_reports_failure(page: Path):
    page.write_text(VALID_PAGE.replace('href="#case-study"', 'href="#missing"'),
                    encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(TOOLS / "verify_pitch.py"), str(page)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "unresolved internal anchor #missing" in result.stderr
