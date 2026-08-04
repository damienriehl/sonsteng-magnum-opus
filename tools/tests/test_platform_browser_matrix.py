import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"


def matrix():
    return json.loads((TOOLS / "platform_browser_matrix.json").read_text())


def test_matrix_has_every_required_family_exactly_once():
    data = matrix()
    families = [page["family"] for page in data["pages"]]
    assert sorted(families) == sorted(data["requiredFamilies"])
    assert len(families) == len(set(families))


def test_matrix_pins_breakpoint_edges_and_both_type_modes():
    data = matrix()
    assert {1280, 960, 959, 672, 671, 390} <= {v["width"] for v in data["viewports"]}
    assert data["typeModes"] == ["baseline", "large"]


def test_every_static_page_has_hierarchy_roles_and_document_families_print():
    data = matrix()
    static = [p for p in data["pages"] if not p.get("interactive")]
    assert all({"primary", "support", "section", "metadata"} <= set(p["hierarchy"]) for p in static)
    assert {p["family"] for p in data["pages"] if p.get("print")} == {"packet", "facts", "law", "templates"}


def test_browser_gates_are_wired_into_preflight_and_never_soft_pass_launch_errors():
    preflight = (TOOLS / "preflight.sh").read_text()
    assert "verify_platform_layout.js" in preflight
    assert "verify_chat_critique.js" in preflight
    for name in ("verify_platform_layout.js", "verify_chat_critique.js"):
        source = (TOOLS / name).read_text()
        assert "process.exit(1)" in source
        assert "BROWSER GATE ERROR" in source


def test_full_generated_corpus_overflow_check_is_not_only_curated_pages():
    source = (TOOLS / "verify_platform_layout.js").read_text()
    assert "walk(SITE)" in source
    assert "corpus-overflow" in source
    assert "generated pages" in source


def test_browser_gates_assert_the_requested_large_type_mode_is_active():
    layout = (TOOLS / "verify_platform_layout.js").read_text()
    a11y = (TOOLS / "a11y_audit.js").read_text()
    interactive = (TOOLS / "verify_chat_critique.js").read_text()
    assert "got.large!==(mode==='large')" in layout
    assert "state.large!==(mode==='large')" in layout
    assert "largeActive !== (mode === 'large')" in a11y
    assert "active() !== startsLarge" in interactive
    assert "if (!active()) largeButton.click()" in interactive


def test_semantic_heading_repairs_preserve_the_existing_visible_words():
    generator = (ROOT / "tools" / "build_site.py").read_text()
    for heading in (
        '<h2 class="eyebrow">THE VOLUME · HOW THIS MODULE TEACHES</h2>',
        '<h2 class="eyebrow">RULED INDEX · TASKS BY SKILL</h2>',
        '<h2 class="eyebrow">THE FACTS OF THE SCENARIO</h2>',
        '<h2 class="eyebrow">THE GOVERNING LAW</h2>',
    ):
        assert heading in generator

    # The semantic baseline deliberately changes only text placement/headings;
    # link, durable editor identity, and reading-order digests remain pinned.
    baseline = json.loads((TOOLS / "tests/fixtures/platform-semantic-baseline.json").read_text())
    assert baseline["fields"]["links"] == "7be8890c7a50456787215dff01b35c92844a0ac613648cbb8512272f992309e7"
    assert baseline["fields"]["editor_blocks"] == "9b169b283c7316fe7bc24fe6bce00649b3667cbd62538f6e526f460c6cba28e9"
    assert baseline["fields"]["reading_order"] == "94516e22ccf427575bafce1954b9938d6887ed8998251deca8fc6781d9ae7dfc"
