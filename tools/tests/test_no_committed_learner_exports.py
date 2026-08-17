import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_FILES = {
    "data/schemas/weekly-hours-log.schema.json",
    "tools/tests/test_no_committed_learner_exports.py",
    "tools/tests/test_weekly_hours_log.py",
}


def looks_like_learner_export(rel, content):
    name = Path(rel).name.lower()
    if any(part in name for part in ("weekly-hours-export", "hours-log-export", "learner-hours")) or name.startswith("weekly-hours-"):
        return True
    if "record_type,entry_id,date,project,matter,activity,worked_hours,billable_hours" in content.replace('"', ''):
        return True
    if Path(rel).suffix.lower() != ".json":
        return False
    try:
        obj = json.loads(content)
    except (ValueError, TypeError):
        return False
    if isinstance(obj, dict) and obj.get("storage_version") in {1, 2}:
        candidates = obj.get("documents") or [obj.get("document")]
        return any(isinstance(item, dict) and item.get("schema_version") == 1 for item in candidates)
    return isinstance(obj, dict) and obj.get("schema_version") == 1 and {
        "learner_id", "offering_id", "week", "entries", "contribution_log"
    } <= set(obj)


def test_no_learner_export_is_tracked():
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split("\0")
    hits = []
    for rel in tracked:
        if not rel or rel in ALLOWED_FILES:
            continue
        path = ROOT / rel
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        if looks_like_learner_export(rel, path.read_text(encoding="utf-8", errors="ignore")):
            hits.append(rel)
    assert hits == [], ("Learner hour exports must never be committed. Keep real learner data "
                        f"in browser-local storage or an external chosen channel. Found: {hits}")


def test_guard_catches_filename_and_document_shape():
    assert looks_like_learner_export("downloads/weekly-hours-export.json", "{}")
    fixture = {"schema_version": 1, "learner_id": "synthetic", "offering_id": "demo",
               "week": {}, "entries": [], "contribution_log": []}
    assert looks_like_learner_export("unexpected.json", json.dumps(fixture))
    assert looks_like_learner_export("notes.csv", "record_type,entry_id,date,project,matter,activity,worked_hours,billable_hours\n")
    assert looks_like_learner_export("backup.json", json.dumps({"storage_version": 2, "documents": [fixture]}))
    assert not looks_like_learner_export("data/matters/m01.json", '{"id":"m01"}')
