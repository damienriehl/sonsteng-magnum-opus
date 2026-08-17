"""Contract tests for the hand-authored pitch-page verification gate."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import verify_pitch  # noqa: E402


EXPECTED_PROOF_SUMMARIES = list(verify_pitch.EXPECTED_PROOF_SUMMARIES)


def content_word_count(parser: verify_pitch.PageParser) -> int:
    """Count pitch prose, including closed disclosures but excluding data cards."""
    has_main = any(element.tag == "main" for element in parser.elements)
    text = " ".join(
        node.value
        for node in parser.text_nodes
        if verify_pitch._is_content_text(node, has_main)
        and not any(
            ancestor.tag == "article"
            and "matter-cover" in ancestor.attrs.get("class", "").split()
            for ancestor in verify_pitch._ancestors(node.parent)
        )
    )
    return len(re.findall(r"\b[\w~$%]+(?:[-'\u2019][\w]+)*\b", text))


def proof_contract_errors(path: Path) -> list[str]:
    parser = verify_pitch._parse(path)
    return verify_pitch._pitch_contract_errors(
        parser, path.read_text(encoding="utf-8")
    )


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
    assert any("direct-child THE PROOF" in error for error in verify_pitch.verify_page(path))


def test_missing_print_rule_mutation_is_caught(tmp_path: Path):
    source = (ROOT / "site/index.html").read_text(encoding="utf-8")
    mutated = source.replace(
        "@media print{details.proof>summary",
        "@media screen{details.proof>summary",
        1,
    )
    path = tmp_path / "missing-print-rule.html"
    path.write_text(mutated, encoding="utf-8")
    assert any("@media print" in error for error in verify_pitch.verify_page(path))


def test_missing_expand_all_control_cannot_disable_pitch_contract(tmp_path: Path):
    source = (ROOT / "site/index.html").read_text(encoding="utf-8")
    mutated = re.sub(
        r'<div class="proof-toggle-wrap wrap"><button[^>]*id="proofToggle"[^>]*>'
        r'.*?</button></div>',
        "",
        source,
        count=1,
    )
    path = tmp_path / "missing-proof-toggle.html"
    path.write_text(mutated, encoding="utf-8")
    errors = verify_pitch.verify_page(path)
    assert any('id="proofToggle"' in error for error in errors)
    assert not any("author surname" in error for error in errors)


def test_pitch_opens_problem_then_midstate_demonstration():
    parser = verify_pitch._parse(ROOT / "site/index.html")
    section_ids = [
        element.attrs.get("id")
        for element in parser.elements
        if element.tag == "section"
    ]
    assert section_ids[:2] == ["problem", "practicum"]
    demonstration = next(
        element for element in parser.elements
        if element.tag == "section" and element.attrs.get("id") == "practicum"
    )
    text = verify_pitch._descendant_text(demonstration)
    assert all(term in text for term in ("Midstate", "SPEU", "Pat Rogers"))


def test_pitch_has_one_linked_cover_for_every_manifest_matter():
    manifest = json.loads((ROOT / "data/matters/manifest.json").read_text())
    parser = verify_pitch._parse(ROOT / "site/index.html")
    covers = [
        element for element in parser.elements
        if element.tag == "article" and "matter-cover" in element.attrs.get("class", "").split()
    ]
    ids = [cover.attrs.get("data-matter-id") for cover in covers]
    assert len(covers) == 20
    assert len(set(ids)) == 20
    assert set(ids) == {matter["id"] for matter in manifest["matters"]}
    by_id = {matter["id"]: matter for matter in manifest["matters"]}
    for cover in covers:
        entry = by_id[cover.attrs["data-matter-id"]]
        assert entry["caption"] in verify_pitch._descendant_text(cover)
        links = [
            child for child in parser.elements
            if child.tag == "a" and cover in tuple(verify_pitch._ancestors(child.parent))
        ]
        assert len(links) == 1
        assert links[0].attrs["href"] == (
            f'/platform/matters/{entry["slug"]}/'
        )


def test_every_cover_skill_ref_resolves_against_build_catalogue():
    manifest = json.loads((ROOT / "data/matters/manifest.json").read_text())
    catalogue = json.loads((ROOT / "data/taxonomy/skills.json").read_text())
    known_skills = {skill["id"] for skill in catalogue["skills"]}
    parser = verify_pitch._parse(ROOT / "site/index.html")
    covers = {
        element.attrs["data-matter-id"]: element
        for element in parser.elements
        if element.tag == "article" and "matter-cover" in element.attrs.get("class", "").split()
    }
    for entry in manifest["matters"]:
        matter = json.loads(
            (ROOT / "data/matters" / entry["slug"] / "matter.json").read_text()
        )
        assert matter["skill_refs"]
        assert set(matter["skill_refs"]) <= known_skills
        rendered_refs = {
            child.attrs["data-skill-ref"]
            for child in parser.elements
            if "data-skill-ref" in child.attrs
            and covers[entry["id"]] in tuple(verify_pitch._ancestors(child.parent))
        }
        assert rendered_refs == set(matter["skill_refs"])


def test_proposed_length_vocabulary_is_dean_editable_and_enumerated():
    vocabulary = json.loads(
        (ROOT / "data/copy/matter-length-options.json").read_text(encoding="utf-8")
    )
    assert vocabulary["schema_version"] == "1.0.0"
    assert vocabulary["type"] == "enumerated_copy_options"
    options = vocabulary["options"]
    assert [option["value"] for option in options] == [
        "one_week", "three_week", "full_semester"
    ]
    assert all(option["label"] and option["description"] for option in options)

    parser = verify_pitch._parse(ROOT / "site/index.html")
    covers = [
        element for element in parser.elements
        if element.tag == "article"
        and "matter-cover" in element.attrs.get("class", "").split()
    ]
    labels = [option["label"] for option in options]
    for cover in covers:
        length = next(
            element for element in parser.elements
            if cover in tuple(verify_pitch._ancestors(element.parent))
            and "matter-length" in element.attrs.get("class", "").split()
        )
        assert all(label in verify_pitch._descendant_text(length) for label in labels)


def test_matter_covers_have_keyboard_hover_focus_and_390px_contract():
    source = (ROOT / "site/index.html").read_text(encoding="utf-8")
    assert '.matter-cover>a{' in source
    assert '.matter-cover>a:hover' in source
    assert '.matter-cover>a:focus-visible' in source
    assert '@media(max-width:390px)' in source
    assert '.matter-grid{grid-template-columns:1fr}' in source
    assert 'min-width:0' in source


def test_preflight_runs_the_pitch_content_contract():
    preflight = (ROOT / "tools" / "preflight.sh").read_text(encoding="utf-8")
    gate = 'run "pitch content contract"'
    assert gate in preflight
    assert "python3 tools/verify_pitch.py" in preflight
    assert preflight.index('run "Midstate naming/remedy contract"') < preflight.index(gate)
