"""Run the taxonomy generator only in isolated miniature repository trees."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]


@pytest.fixture
def taxonomy(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "build_taxonomy_contract.py"
    shutil.copyfile(TOOLS / script.name, script)
    tax = tmp_path / "data" / "taxonomy"
    tax.mkdir(parents=True)
    source = {
        "skills.json": {"description": "Skill catalogue", "skills": [
            {"id": "s1", "name": "Café skill", "alt_name": "Alternative"},
            {"id": "s2", "name": "Second"}]},
        "tasks.json": {"description": "Task catalogue", "tasks": [
            {"id": "t1", "skill_id": "s1", "name": "Task", "description": "Task description",
             "subtasks": [{"id": "u1", "name": "Subtask", "description": "Subtask description"}]}]},
        "folio-crosswalk.json": {"description": "Crosswalk", "skills": [
            {"id": "s1", "note": "Unmapped skill"}, {"id": "s2"}],
            "tasks": [{"id": "t1", "note": "Unmapped task"}]},
    }
    for name, content in source.items():
        (tax / name).write_text(json.dumps(content))
    return script, tax


def run(taxonomy):
    script, _ = taxonomy
    # Inherit stderr so expected failures remain visible instead of suppressing them.
    return subprocess.run([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)


def read(taxonomy, name):
    return json.loads((taxonomy[1] / name).read_text())


def write(taxonomy, name, content):
    (taxonomy[1] / name).write_text(json.dumps(content))


def test_first_build_emits_exact_editable_leaves_and_seed_identities(taxonomy):
    result = run(taxonomy)
    assert result.returncode == 0
    assert "taxonomy identities: 1 tasks" in result.stdout
    identities = read(taxonomy, "taxonomy-identities.json")
    assert identities["tasks"] == [{"id": "t1", "skill_id": "s1", "seed_name": "Task",
                                    "subtasks": [{"id": "u1", "seed_name": "Subtask"}]}]
    fields = read(taxonomy, "editable-fields.json")["editable"]
    assert len(fields) == 12
    assert len({row["source_ref"] for row in fields}) == len(fields)
    assert {row["family"] for row in fields} == {"document_description", "skill_name", "skill_alt_name",
        "task_name", "task_description", "subtask_name", "subtask_description", "no_folio_note"}
    for row in fields:
        file, dotted = row["source_ref"].removeprefix("data/taxonomy/").split("#")
        value = read(taxonomy, file)
        for part in dotted.split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
        assert row["authored_text"] == value
    assert "Café" in (taxonomy[1] / "editable-fields.json").read_text()
    assert (taxonomy[1] / "editable-fields.json").read_bytes().endswith(b"\n")
    before = {name: (taxonomy[1] / name).read_bytes() for name in ["taxonomy-identities.json", "editable-fields.json"]}
    assert run(taxonomy).returncode == 0
    assert all((taxonomy[1] / name).read_bytes() == contents for name, contents in before.items())


def test_rebuild_preserves_seeds_by_id_through_renames_reordering_and_addition(taxonomy):
    assert run(taxonomy).returncode == 0
    tasks = read(taxonomy, "tasks.json")
    task = tasks["tasks"][0]
    task["name"] = "Renamed task"
    task["subtasks"][0]["name"] = "Renamed subtask"
    task["subtasks"].insert(0, {"id": "u2", "name": "New subtask", "description": "new"})
    tasks["tasks"].insert(0, {"id": "t2", "skill_id": "s2", "name": "New task", "description": "new", "subtasks": []})
    write(taxonomy, "tasks.json", tasks)
    assert run(taxonomy).returncode == 0
    identities = read(taxonomy, "taxonomy-identities.json")["tasks"]
    assert [task["seed_name"] for task in identities] == ["New task", "Task"]
    assert identities[1]["subtasks"] == [{"id": "u2", "seed_name": "New subtask"}, {"id": "u1", "seed_name": "Subtask"}]
    assert "Renamed task" in [row["authored_text"] for row in read(taxonomy, "editable-fields.json")["editable"]]


def test_locked_skill_change_fails_before_overwriting_previous_outputs(taxonomy):
    assert run(taxonomy).returncode == 0
    before = {name: (taxonomy[1] / name).read_bytes() for name in ["taxonomy-identities.json", "editable-fields.json"]}
    tasks = read(taxonomy, "tasks.json")
    tasks["tasks"][0]["skill_id"] = "s2"
    write(taxonomy, "tasks.json", tasks)
    assert run(taxonomy).returncode != 0
    assert all((taxonomy[1] / name).read_bytes() == contents for name, contents in before.items())


def test_empty_collections_still_expose_document_descriptions(taxonomy):
    write(taxonomy, "skills.json", {"description": "", "skills": []})
    write(taxonomy, "tasks.json", {"description": "", "tasks": []})
    write(taxonomy, "folio-crosswalk.json", {"description": "", "skills": [], "tasks": []})
    assert run(taxonomy).returncode == 0
    assert read(taxonomy, "taxonomy-identities.json")["tasks"] == []
    fields = read(taxonomy, "editable-fields.json")["editable"]
    assert len(fields) == 3
    assert all(row["family"] == "document_description" and row["authored_text"] == "" for row in fields)


@pytest.mark.parametrize("name", ["skills.json", "tasks.json", "folio-crosswalk.json", "taxonomy-identities.json"])
def test_corrupt_inputs_fail_without_creating_outputs(taxonomy, name):
    (taxonomy[1] / name).write_text("not JSON")
    assert run(taxonomy).returncode != 0
    assert not (taxonomy[1] / "editable-fields.json").exists()


def test_missing_required_file_is_not_treated_as_optional(taxonomy):
    (taxonomy[1] / "skills.json").unlink()
    assert run(taxonomy).returncode != 0
    assert not (taxonomy[1] / "taxonomy-identities.json").exists()


def test_removed_records_and_optional_wording_do_not_survive_regeneration(taxonomy):
    assert run(taxonomy).returncode == 0
    skills = read(taxonomy, "skills.json")
    skills["skills"][0].pop("alt_name")
    write(taxonomy, "skills.json", skills)
    crosswalk = read(taxonomy, "folio-crosswalk.json")
    for family in ("skills", "tasks"):
        for entry in crosswalk[family]:
            entry.pop("note", None)
    write(taxonomy, "folio-crosswalk.json", crosswalk)
    write(taxonomy, "tasks.json", {"description": "No current tasks", "tasks": []})
    assert run(taxonomy).returncode == 0
    assert read(taxonomy, "taxonomy-identities.json")["tasks"] == []
    families = {row["family"] for row in read(taxonomy, "editable-fields.json")["editable"]}
    assert families == {"document_description", "skill_name"}


def test_new_subtask_seed_is_assigned_while_removed_subtask_disappears(taxonomy):
    assert run(taxonomy).returncode == 0
    tasks = read(taxonomy, "tasks.json")
    tasks["tasks"][0]["subtasks"] = [{"id": "replacement", "name": "Replacement", "description": ""}]
    write(taxonomy, "tasks.json", tasks)
    assert run(taxonomy).returncode == 0
    assert read(taxonomy, "taxonomy-identities.json")["tasks"][0]["subtasks"] == [
        {"id": "replacement", "seed_name": "Replacement"}]
