#!/usr/bin/env python3
"""Build the reviewed taxonomy identity and editability inventories."""

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAX = os.path.join(REPO, "data", "taxonomy")


def read(name):
    with open(os.path.join(TAX, name), encoding="utf-8") as fh:
        return json.load(fh)


def write(name, value):
    with open(os.path.join(TAX, name), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


skills = read("skills.json")
tasks = read("tasks.json")
crosswalk = read("folio-crosswalk.json")

identities = {
    "schema_version": "1.0.0",
    "note": "Literal preservation keys migrated from the 2026-08-09 positional taxonomy.",
    "tasks": [
        {
            "id": task["id"],
            "skill_id": task["skill_id"],
            "seed_name": task["name"],
            "subtasks": [
                {"id": subtask["id"], "seed_name": subtask["name"]}
                for subtask in task["subtasks"]
            ],
        }
        for task in tasks["tasks"]
    ],
}

editable = []


def add(file_name, path, value, family, record_id):
    editable.append({
        "source_ref": f"data/taxonomy/{file_name}#{path}",
        "family": family,
        "record_id": record_id,
        "authored_text": value,
    })


add("skills.json", "description", skills["description"], "document_description", "skills")
for i, skill in enumerate(skills["skills"]):
    add("skills.json", f"skills.{i}.name", skill["name"], "skill_name", skill["id"])
    if "alt_name" in skill:
        add("skills.json", f"skills.{i}.alt_name", skill["alt_name"], "skill_alt_name", skill["id"])

add("tasks.json", "description", tasks["description"], "document_description", "tasks")
for i, task in enumerate(tasks["tasks"]):
    add("tasks.json", f"tasks.{i}.name", task["name"], "task_name", task["id"])
    add("tasks.json", f"tasks.{i}.description", task["description"], "task_description", task["id"])
    for j, subtask in enumerate(task["subtasks"]):
        add("tasks.json", f"tasks.{i}.subtasks.{j}.name", subtask["name"], "subtask_name", subtask["id"])
        add("tasks.json", f"tasks.{i}.subtasks.{j}.description", subtask["description"], "subtask_description", subtask["id"])

add("folio-crosswalk.json", "description", crosswalk["description"], "document_description", "folio-crosswalk")
for family in ("skills", "tasks"):
    for i, entry in enumerate(crosswalk[family]):
        if "note" in entry:
            add("folio-crosswalk.json", f"{family}.{i}.note", entry["note"], "no_folio_note", entry["id"])

inventory = {
    "schema_version": "1.0.0",
    "generated_by": "tools/build_taxonomy_contract.py",
    "editable": editable,
    "locked_field_names": [
        "@id", "branch", "category", "exercise_refs", "extension",
        "extension_count", "folio", "folio_iri", "folio_label", "id",
        "mapping_confidence", "module", "no_folio_equivalent", "schema_version",
        "skill_id", "source", "spine_version", "survey", "surveyed_count",
        "task_count", "verified_at", "bloom_level",
    ],
}

write("taxonomy-identities.json", identities)
write("editable-fields.json", inventory)
print(f"taxonomy identities: {len(identities['tasks'])} tasks")
print(f"editable taxonomy leaves: {len(editable)}")
