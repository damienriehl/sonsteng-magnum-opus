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
    assert {1280, 960, 959, 672, 671, 480, 390} <= {v["width"] for v in data["viewports"]}
    assert data["typeModes"] == ["baseline", "large"]


def test_every_static_page_has_hierarchy_roles_and_document_families_print():
    data = matrix()
    static = [p for p in data["pages"] if not p.get("interactive")]
    assert all({"primary", "support", "section", "metadata"} <= set(p["hierarchy"]) for p in static)
    assert {p["family"] for p in data["pages"] if p.get("print")} == {"catalog-print", "packet", "facts", "law", "templates"}


def test_browser_gates_are_wired_into_preflight_and_never_soft_pass_launch_errors():
    preflight = (TOOLS / "preflight.sh").read_text()
    assert "verify_platform_layout.js" in preflight
    assert "verify_chat_critique.js" in preflight
    for name in ("verify_platform_layout.js", "verify_chat_critique.js"):
        source = (TOOLS / name).read_text()
        assert "process.exit(1)" in source
        assert "BROWSER GATE ERROR" in source


def test_browser_analysis_is_background_by_default_and_headful_is_explicit_opt_in():
    preflight = (TOOLS / "preflight.sh").read_text()
    assert 'if [ "${HEADFUL:-0}" = "1" ]' in preflight
    assert "export HEADLESS=1 EDITOR_HEADLESS=1" in preflight
    assert "headful-only gate" not in preflight

    scripts = [
        ROOT / "app/editor/verify-editor.js",
        ROOT / "app/editor/verify-rail-placement.js",
        ROOT / "app/editor/spikes/verify-spikes.js",
        TOOLS / "a11y_audit.js",
        TOOLS / "shot.js",
        TOOLS / "verify_catalog_client.js",
        TOOLS / "verify_chat_critique.js",
        TOOLS / "verify_platform_layout.js",
        TOOLS / "verify_publisher_client.mjs",
    ]
    for script in scripts:
        source = script.read_text()
        assert "process.env.HEADFUL" in source, script
        assert "process.env.HEADLESS==='1'" not in source, script
        assert "process.env.HEADLESS === '1'" not in source, script


def test_accessibility_audit_resolves_puppeteer_portably():
    source = (TOOLS / "a11y_audit.js").read_text()
    env_candidate = "process.env.PUP_DIR"
    package_candidate = "'puppeteer'"
    legacy_candidate = "'/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'"

    assert "function loadPuppeteer()" in source
    assert source.index(env_candidate) < source.index(package_candidate) < source.index(legacy_candidate)
    assert "const puppeteer = loadPuppeteer();" in source
    assert "Puppeteer unavailable (set PUP_DIR or install puppeteer)" in source


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

    # Pin the reviewed full-corpus digests. The weekly-hours navigation and new
    # static page intentionally advance every digest, including the page set.
    baseline = json.loads((TOOLS / "tests/fixtures/platform-semantic-baseline.json").read_text())
    assert baseline["fields"]["links"] == "630c7ca30bf13e030b36870a258a5201d3f8e1a771ee48ccc8ccbd2f0abdbe5e"
    assert baseline["fields"]["editor_blocks"] == "f8319739017055c03f71340f108d05677f7b24732ad99925537bd10cb5de1dca"
    assert baseline["fields"]["reading_order"] == "794691851ffd1e382ea23306d35ea66e32279675ab2f3b3f9d53765ff6acbf17"
