#!/usr/bin/env python3
"""U2 contracts for editable taxonomy wording and immutable identity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
TAXONOMY = os.path.join(REPO, "data", "taxonomy")
sys.path.insert(0, TOOLS)

from fresh_site_build import build_fresh_site  # noqa: E402
import build_site  # noqa: E402


def load(name):
    with open(os.path.join(TAXONOMY, name), encoding="utf-8") as fh:
        return json.load(fh)


def expected_editable_refs():
    skills = load("skills.json")
    tasks = load("tasks.json")
    crosswalk = load("folio-crosswalk.json")
    refs = {
        "data/taxonomy/skills.json#description",
        "data/taxonomy/tasks.json#description",
        "data/taxonomy/folio-crosswalk.json#description",
    }
    for i, skill in enumerate(skills["skills"]):
        refs.add(f"data/taxonomy/skills.json#skills.{i}.name")
        if "alt_name" in skill:
            refs.add(f"data/taxonomy/skills.json#skills.{i}.alt_name")
    for i, task in enumerate(tasks["tasks"]):
        refs.update({
            f"data/taxonomy/tasks.json#tasks.{i}.name",
            f"data/taxonomy/tasks.json#tasks.{i}.description",
        })
        for j, _subtask in enumerate(task["subtasks"]):
            refs.update({
                f"data/taxonomy/tasks.json#tasks.{i}.subtasks.{j}.name",
                f"data/taxonomy/tasks.json#tasks.{i}.subtasks.{j}.description",
            })
    for family in ("skills", "tasks"):
        for i, entry in enumerate(crosswalk[family]):
            if "note" in entry:
                refs.add(f"data/taxonomy/folio-crosswalk.json#{family}.{i}.note")
    return refs


def test_checked_in_inventory_is_complete_and_exact():
    inventory = load("editable-fields.json")
    declared = {item["source_ref"] for item in inventory["editable"]}
    assert declared == expected_editable_refs()
    assert inventory["locked_field_names"] == [
        "@id", "branch", "category", "exercise_refs", "extension",
        "extension_count", "folio", "folio_iri", "folio_label", "id",
        "mapping_confidence", "module", "no_folio_equivalent", "schema_version",
        "skill_id", "source", "spine_version", "survey", "surveyed_count",
        "task_count", "verified_at", "bloom_level",
    ]


def test_literal_identity_manifest_matches_current_ids():
    identities = load("taxonomy-identities.json")
    tasks = load("tasks.json")["tasks"]
    assert [(item["id"], item["skill_id"], [sub["id"] for sub in item["subtasks"]])
            for item in identities["tasks"]] == [
        (task["id"], task["skill_id"], [subtask["id"] for subtask in task["subtasks"]])
        for task in tasks]
    assert len({item["seed_name"] for item in identities["tasks"]}) == len(tasks)


def test_fresh_editor_map_exposes_exact_taxonomy_allowlist():
    tmp, _site, bundle = build_fresh_site("taxonomy-u2-")
    try:
        actual = {
            block["source_ref"]
            for blocks in bundle["pages"].values()
            for block in blocks
            if block["source_ref"].startswith("data/taxonomy/")
        }
        assert actual == expected_editable_refs()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_generator_round_trip_preserves_wording_by_literal_id():
    import shutil
    with tempfile.TemporaryDirectory(prefix="taxonomy-generator-") as tmp:
        data = os.path.join(tmp, "data")
        tax = os.path.join(data, "taxonomy")
        shutil.copytree(TAXONOMY, tax)
        shutil.copytree(os.path.join(REPO, "data", "schemas"), os.path.join(data, "schemas"))
        os.makedirs(os.path.join(data, "matters"))
        shutil.copy2(os.path.join(REPO, "data", "matters", "manifest.json"),
                     os.path.join(data, "matters", "manifest.json"))

        skills_path = os.path.join(tax, "skills.json")
        tasks_path = os.path.join(tax, "tasks.json")
        skills = json.load(open(skills_path, encoding="utf-8"))
        tasks = json.load(open(tasks_path, encoding="utf-8"))
        skills["skills"][0]["name"] = "Edited <script>alert(1)</script> skill"
        tasks["tasks"][0]["name"] = "Edited task wording"
        tasks["tasks"][0]["subtasks"][0]["description"] = "Edited subtask wording"
        with open(skills_path, "w", encoding="utf-8") as fh:
            json.dump(skills, fh, ensure_ascii=False, indent=2)
        with open(tasks_path, "w", encoding="utf-8") as fh:
            json.dump(tasks, fh, ensure_ascii=False, indent=2)

        before_ids = [
            (task["id"], [sub["id"] for sub in task["subtasks"]])
            for task in tasks["tasks"]
        ]
        subprocess.run([sys.executable, os.path.join(tax, "_build_taxonomy.py")],
                       check=True, cwd=tmp, capture_output=True, text=True)
        regenerated_skills = json.load(open(skills_path, encoding="utf-8"))
        regenerated_tasks = json.load(open(tasks_path, encoding="utf-8"))
        assert regenerated_skills["skills"][0]["name"] == "Edited <script>alert(1)</script> skill"
        assert regenerated_tasks["tasks"][0]["name"] == "Edited task wording"
        assert regenerated_tasks["tasks"][0]["subtasks"][0]["description"] == "Edited subtask wording"
        assert [
            (task["id"], [sub["id"] for sub in task["subtasks"]])
            for task in regenerated_tasks["tasks"]
        ] == before_ids


def test_hostile_taxonomy_wording_is_contextually_escaped_as_inert_text():
    from html.parser import HTMLParser

    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)

    payload = '<script>alert(1)</script><img src=x onerror="alert(2)">'
    rendered = build_site.esc(payload)
    parser = Tags()
    parser.feed(rendered)
    assert parser.tags == []
    assert "<script" not in rendered and "<img" not in rendered
    assert "&lt;script&gt;" in rendered and "&lt;img" in rendered
