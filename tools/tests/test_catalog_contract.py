import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import student_archives as sa  # noqa: E402


def test_student_archive_is_deterministic_and_exact(tmp_path):
    matter = tmp_path / "m01-safe"
    (matter / "exercise").mkdir(parents=True)
    (matter / "case-file").mkdir()
    (matter / "matter.json").write_text('{"id":"m01"}\n')
    (matter / "exercise" / "exercise.json").write_text(json.dumps({
        "id": "m01.ex", "sections": {"case_file": {"files": ["case-file/exhibit.md"]}}
    }))
    (matter / "rubric.json").write_text('{"id":"m01.rubric"}\n')
    (matter / "case-file" / "exhibit.md").write_text("public exhibit\n")
    (matter / "facts.md").write_text("concealed\n")
    (matter / "exercise" / "answer-key.md").write_text("key\n")

    manifest = sa.student_material_manifest(matter, "m01-safe")
    first = tmp_path / "one.zip"
    second = tmp_path / "two.zip"
    sa.write_student_archive(manifest, first)
    sa.write_student_archive(manifest, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert names == [
            "case-file/exhibit.md", "exercise/exercise.json",
            "manifest.json", "matter.json", "rubric.json",
        ]
        embedded = json.loads(archive.read("manifest.json"))
        assert embedded["schema_version"] == "1.0.0"
        assert embedded["missing_optional"] == [
            "business/business.json", "business/engagement-letter.md"
        ]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert not any("facts" in n or "answer-key" in n or "persona" in n for n in names)


def test_student_manifest_rejects_unsafe_members_and_missing_required(tmp_path):
    matter = tmp_path / "m01-safe"
    (matter / "exercise").mkdir(parents=True)
    (matter / "matter.json").write_text("{}")
    (matter / "rubric.json").write_text("{}")
    (matter / "exercise" / "exercise.json").write_text(json.dumps({
        "sections": {"case_file": {"files": ["../facts.md"]}}
    }))
    with pytest.raises(sa.StudentArchiveError, match="unsafe|allowlist"):
        sa.student_material_manifest(matter, "m01-safe")
    (matter / "matter.json").unlink()
    with pytest.raises(sa.StudentArchiveError, match="required"):
        sa.student_material_manifest(matter, "m01-safe")


def test_catalog_source_contract_has_histories_pagination_and_one_free_action():
    source = (TOOLS / "build_site.py").read_text()
    assert "CATALOG_PAGE_SIZE = 50" in source
    assert 'sections.get("history")' in source
    assert "history_summary" in source
    assert "Download student materials (.zip)" in source
    assert source.count("View complete public source repository (includes instructor materials and answer keys)") == 1
    assert "ALL 20" not in source
    assert "matters/print-all.html" in source
    assert "catalog-index.json" in source
    assert "catalog.js" in source


def test_browser_matrix_pins_catalog_widths_and_print_all():
    matrix = json.loads((TOOLS / "platform_browser_matrix.json").read_text())
    widths = {item["width"] for item in matrix["viewports"]}
    assert {390, 480, 1280} <= widths
    assert any(page["path"] == "matters/print-all.html" and page.get("print")
               for page in matrix["pages"])


def test_synthetic_thousand_is_complete_unique_and_page_bounded():
    import build_site
    fixture = [{"id": "synthetic-%04d" % i} for i in range(1000)]
    pages = build_site.paginate_catalog_records(fixture)
    assert len(pages) == 20
    assert all(len(page) <= 50 for page in pages)
    assert [item for page in pages for item in page] == fixture
    with pytest.raises(ValueError, match="unique"):
        build_site.paginate_catalog_records(fixture + [fixture[0]])


def test_catalog_client_preserves_state_agrees_with_index_and_restores_focus():
    source = (TOOLS / "build_site.py").read_text()
    assert "new URLSearchParams(location.search)" in source
    assert "index.matters.filter" in source
    assert "matches.slice((page-1)*index.page_size,page*index.page_size)" in source
    assert "history.pushState" in source
    assert "heading.focus()" in source
    assert "aria-live=\"polite\"" in source
    assert "No matters match your search and filters." in source


def test_catalog_filters_have_explicit_names_and_full_size_targets():
    source = (TOOLS / "build_site.py").read_text()
    for control in ("search", "shape", "tier", "fee"):
        assert 'for="catalog-%s"' % control in source
        assert 'id="catalog-%s"' % control in source
    assert ".lib-toolbar input,.lib-toolbar select,.lib-toolbar button" in source
    assert "min-height:44px" in source
