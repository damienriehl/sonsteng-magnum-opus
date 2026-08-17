"""Contract tests for the hand-authored pitch-page verification gate."""

from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import verify_pitch  # noqa: E402


EXPECTED_PROOF_SUMMARIES = [
    "THE PROOF · 19,077 attorneys surveyed",
    "THE PROOF · 70-point client-development gap",
    "THE PROOF · diagnosis, method, and open resource",
    "THE PROOF · 3 layers, 1 open whole",
    "THE PROOF · ~1,438 assessed points",
    "THE PROOF · 24/7 first-pass critique",
    "THE PROOF · all 26 skills mapped",
    "THE PROOF · CC BY 4.0 content + MIT code",
    "THE PROOF · 8 decision prompts captured in one place",
]


def content_word_count(parser: verify_pitch.PageParser) -> int:
    """Count all authored content, including text in closed disclosures."""
    has_main = any(element.tag == "main" for element in parser.elements)
    text = " ".join(
        node.value
        for node in parser.text_nodes
        if verify_pitch._is_content_text(node, has_main)
    )
    return len(re.findall(r"\b[\w~$%]+(?:[-'\u2019][\w]+)*\b", text))


def proof_contract_errors(path: Path) -> list[str]:
    parser = verify_pitch._parse(path)
    sections = [element for element in parser.elements if element.tag == "section"]
    errors: list[str] = []
    summaries: list[str] = []
    if len(sections) != 9:
        errors.append("expected exactly nine major sections")
    for section in sections:
        proofs = [
            child for child in section.children
            if isinstance(child, verify_pitch.Element)
            and child.tag == "details"
            and "proof" in child.attrs.get("class", "").split()
        ]
        if len(proofs) != 1:
            errors.append("each section needs one direct-child proof disclosure")
            continue
        proof = proofs[0]
        if "open" in proof.attrs:
            errors.append("proof disclosures must be closed by default")
        summary = next(
            (child for child in proof.children
             if isinstance(child, verify_pitch.Element) and child.tag == "summary"),
            None,
        )
        summaries.append(verify_pitch._descendant_text(summary).strip() if summary else "")
    if summaries != EXPECTED_PROOF_SUMMARIES:
        errors.append("proof summaries do not match the approved list")
    if len(set(summaries)) != len(summaries):
        errors.append("proof summaries must be unique")
    source = path.read_text(encoding="utf-8")
    required_fragments = (
        'id="proofToggle"', 'aria-expanded="false"',
        "querySelectorAll('details.proof')", "beforeprint", "afterprint",
        "@media print{details.proof>summary", "details.proof[open]",
        ".reveal{opacity:1!important;transform:none!important}",
    )
    for fragment in required_fragments:
        if fragment not in source:
            errors.append(f"missing disclosure contract fragment: {fragment}")
    return errors


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


def test_authored_payload_over_250_kb_fails(page: Path):
    page.write_text(VALID_PAGE + " " * 250_000, encoding="utf-8")

    assert "authored payload" in messages(page)
    assert "exceeds 250,000-byte ceiling" in messages(page)


def test_large_inlined_data_uri_does_not_count_toward_authored_payload(page: Path):
    page.write_text(
        VALID_PAGE.replace("AA//AA", "A" * 300_000),
        encoding="utf-8",
    )

    assert page.stat().st_size > verify_pitch.PAGE_SIZE_CEILING
    assert verify_pitch.verify_page(page) == []


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


def test_command_reports_transfer_weight_on_success(page: Path):
    result = subprocess.run(
        [sys.executable, str(TOOLS / "verify_pitch.py"), str(page)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    data_uri = "data:image/png;base64,AA//AA"
    assert result.returncode == 0
    assert (
        f"transfer weight: {page.stat().st_size:,} bytes total; "
        f"{len(data_uri.encode()):,} bytes in inlined base64 data URIs"
    ) in result.stdout
    assert "violation" not in result.stderr


def test_transfer_weight_does_not_increase_violation_count(page: Path):
    page.write_text(
        VALID_PAGE.replace('href="#case-study"', 'href="#missing"').replace(
            "AA//AA", "A" * 300_000
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(TOOLS / "verify_pitch.py"), str(page)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "transfer weight:" in result.stdout
    assert "verify_pitch: 1 violation(s)" in result.stderr


def test_pitch_has_nine_closed_unique_direct_child_proofs_with_exact_summaries():
    assert proof_contract_errors(ROOT / "site/index.html") == []


def test_pitch_authored_prose_retains_55_to_65_percent_of_baseline():
    count = content_word_count(verify_pitch._parse(ROOT / "site/index.html"))
    assert 1_808 <= count <= 2_137


def test_statistics_tables_and_citations_are_inside_their_section_proof():
    parser = verify_pitch._parse(ROOT / "site/index.html")
    for section in (element for element in parser.elements if element.tag == "section"):
        proof = next(
            child for child in section.children
            if isinstance(child, verify_pitch.Element)
            and child.tag == "details"
            and "proof" in child.attrs.get("class", "").split()
        )
        for element in parser.elements:
            if element.tag not in {"table", "cite"} and not (
                element.tag in {"b", "span", "div"}
                and re.search(r"(?<!\w)\d", verify_pitch._descendant_text(element))
            ):
                continue
            if section not in tuple(verify_pitch._ancestors(element.parent)):
                continue
            assert proof in tuple(verify_pitch._ancestors(element.parent)) or element is proof


def test_missing_disclosure_mutation_is_caught(tmp_path: Path):
    source = (ROOT / "site/index.html").read_text(encoding="utf-8")
    mutated = source.replace('<details class="proof">', '<div class="proof">', 1)
    path = tmp_path / "missing-disclosure.html"
    path.write_text(mutated, encoding="utf-8")
    assert proof_contract_errors(path)


def test_missing_print_rule_mutation_is_caught(tmp_path: Path):
    source = (ROOT / "site/index.html").read_text(encoding="utf-8")
    mutated = source.replace(
        "@media print{details.proof>summary",
        "@media screen{details.proof>summary",
        1,
    )
    path = tmp_path / "missing-print-rule.html"
    path.write_text(mutated, encoding="utf-8")
    assert proof_contract_errors(path)
