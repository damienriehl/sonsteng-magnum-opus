import json
import subprocess
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "data/schemas/weekly-hours-log.schema.json"
CORE_PATH = ROOT / "app/hours/hours-core.js"


def synthetic_log():
    return {
        "schema_version": 1,
        "learner_id": "learner-synthetic-001",
        "offering_id": "offering-demo-001",
        "week": {"start": "2026-08-17", "end": "2026-08-23"},
        "entries": [{
            "id": "entry-001", "date": "2026-08-18", "project": "Synthetic project",
            "matter": "m01-synthetic", "activity": "Drafting", "worked_hours": 3.5,
            "billable_hours": 2.0, "class_time": True,
            "narrative": "Practised a synthetic client letter.",
        }],
        "contribution_log": [{
            "id": "contrib-001", "deliverable_id": "deliverable-001",
            "deliverable_title": "Synthetic client letter", "contribution_date": "2026-08-18",
            "contribution_type": "drafting", "description": "Drafted the issue statement.",
            "related_entry_ids": ["entry-001"]
        }],
    }


def run_core(expression):
    script = f"const H=require({json.dumps(str(CORE_PATH))});\n{expression}"
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def test_schema_accepts_d2_contribution_log_and_rejects_extra_properties():
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(synthetic_log(), schema)
    bad = synthetic_log()
    bad["entries"][0]["attestation_50_50"] = True
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(bad))
    assert errors


def test_semantic_validation_and_computed_gap():
    doc = synthetic_log()
    expression = f"console.log(JSON.stringify(H.validateLog({json.dumps(doc)})))"
    result = run_core(expression)
    assert result["valid"] is True
    assert result["totals"] == {"worked": 3.5, "billable": 2, "gap": 1.5}

    doc["entries"].append({**doc["entries"][0], "date": "2026-08-24",
                           "billable_hours": 4.0})
    result = run_core(f"console.log(JSON.stringify(H.validateLog({json.dumps(doc)})))")
    codes = {e["code"] for e in result["errors"]}
    assert {"duplicate_entry_id", "date_outside_week", "billable_exceeds_worked"} <= codes

    doc = synthetic_log()
    doc["contribution_log"].append({**doc["contribution_log"][0], "related_entry_ids": ["missing"]})
    result = run_core(f"console.log(JSON.stringify(H.validateLog({json.dumps(doc)})))")
    assert {"duplicate_contribution_id", "unknown_related_entry_id"} <= {e["code"] for e in result["errors"]}


def test_tenths_are_enforced():
    doc = synthetic_log()
    doc["entries"][0]["worked_hours"] = 1.25
    result = run_core(f"console.log(JSON.stringify(H.validateLog({json.dumps(doc)})))")
    assert "hours_not_tenths" in {e["code"] for e in result["errors"]}

    doc = synthetic_log()
    doc["entries"][0]["worked_hours"] = 999.0
    result = run_core(f"console.log(JSON.stringify(H.validateLog({json.dumps(doc)})))")
    assert "hours_exceed_week" in {e["code"] for e in result["errors"]}


def test_csv_round_trip_and_formula_neutralisation():
    doc = synthetic_log()
    doc["entries"][0]["project"] = "=HYPERLINK(\"bad\")"
    result = run_core(
        f"const d={json.dumps(doc)}; const csv=H.toCSV(d); "
        "console.log(JSON.stringify({csv:csv, rows:H.parseCSV(csv)}))"
    )
    assert "'=HYPERLINK" in result["csv"]
    entry_row = next(r for r in result["rows"] if r["record_type"] == "entry")
    assert entry_row["project"].startswith("'=HYPERLINK")
    assert '"bad"' in entry_row["project"]


def test_import_bounds_merge_conflicts_and_future_envelope_preservation():
    doc = synthetic_log()
    result = run_core(
        f"const d={json.dumps(doc)}; const changed=JSON.parse(JSON.stringify(d));"
        "changed.entries[0].narrative='Different';"
        "console.log(JSON.stringify(H.previewImport(d, changed, 'merge')))"
    )
    assert len(result["conflicts"]) == 1
    assert result["document"]["entries"] == []

    future = '{"storage_version":99,"opaque":"keep every byte"}'
    result = run_core(
        f"console.log(JSON.stringify(H.readEnvelope({json.dumps(future)})))"
    )
    assert result["status"] == "future"
    assert result["raw"] == future

    other_week = synthetic_log()
    other_week["week"] = {"start": "2026-08-24", "end": "2026-08-30"}
    other_week["entries"][0]["date"] = "2026-08-25"
    other_week["contribution_log"][0]["contribution_date"] = "2026-08-25"
    script = (
        f"try{{H.previewImport({json.dumps(synthetic_log())},{json.dumps(other_week)},'merge');}}"
        "catch(e){console.log(JSON.stringify({message:e.message}));}"
    )
    assert "same learner, offering, and week" in run_core(script)["message"]


def test_storage_envelope_migrates_one_week_and_preserves_multiple_weeks():
    doc = synthetic_log()
    result = run_core(
        f"const d={json.dumps(doc)}; const old=JSON.stringify({{storage_version:1,document:d}});"
        "const migrated=H.readEnvelope(old); const next=JSON.parse(JSON.stringify(d));"
        "next.week={start:'2026-08-24',end:'2026-08-30'}; next.entries=[]; next.contribution_log=[];"
        "console.log(JSON.stringify({migrated:migrated, saved:H.envelope([d,next],next.week.start)}))"
    )
    assert result["migrated"]["migrated_from"] == 1
    assert len(result["migrated"]["envelope"]["documents"]) == 1
    assert result["saved"]["storage_version"] == 2
    assert len(result["saved"]["documents"]) == 2
    assert result["saved"]["active_week_start"] == "2026-08-24"

    malformed = '{"storage_version":2,"active_week_start":"x","documents":[null]}'
    result = run_core(f"console.log(JSON.stringify(H.readEnvelope({json.dumps(malformed)})))")
    assert result["status"] == "malformed"


def test_static_client_has_restrictive_csp_no_network_and_accessible_status():
    html = (ROOT / "app/hours/index.html").read_text()
    js = (ROOT / "app/hours/hours.js").read_text()
    assert "connect-src 'none'" in html
    assert 'aria-live="polite"' in html
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "WebSocket" not in js
    assert "textContent" in js and ".innerHTML" not in js
    assert "sonsteng.weekly-hours.v1" in js


def test_build_site_copies_hours_client_and_links_it():
    source = (ROOT / "tools/build_site.py").read_text()
    assert "APP_HOURS" in source
    assert "copy_hours_app()" in source
    assert "hours/index.html" in source
