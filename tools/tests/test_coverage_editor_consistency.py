"""Exercise consistency checking with isolated on-disk fact/map snapshots."""
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor_consistency as ec


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    old, current = tmp_path / "old", tmp_path / "current"
    pages = {}
    for slug in ("alpha", "beta"):
        rel = Path("data/matters") / slug
        write_json(old / rel / "matter.json", {"caption": "Old Caption", "id": slug})
        write_json(current / rel / "matter.json", {"caption": "New Caption", "id": slug})
        write_json(old / rel / "business/business.json", {"intake": {"budget": 2500}})
        write_json(current / rel / "business/business.json", {"intake": {"budget": 5000}})
        pages[slug] = [
            {"source_ref": f"{rel}/facts.md#b1", "kind": "prose",
             "original_text": "Old Caption has a budget of $2,500."},
            {"source_ref": f"{rel}/matter.json#caption", "kind": "json_scalar",
             "original_text": "New Caption"},
        ]
    pages["duplicate"] = list(pages["alpha"])
    pages["empty"] = None
    pages["no-prose"] = [{"source_ref": "data/matters/unused/matter.json#caption",
                           "kind": "json_scalar", "original_text": "Unused"}]
    map_path = tmp_path / "editor-map.json"
    write_json(map_path, {"pages": pages})
    monkeypatch.setattr(ec, "EDITOR_MAP_PATH", str(map_path))
    return dict(repo_root=str(current), since="snapshot", dry_run=True,
                no_model=True, old_facts_loader=lambda slug: ec.load_fact_rows(str(old), slug))


def test_disk_snapshots_to_deduplicated_comment_payloads(snapshots):
    output = io.StringIO()
    result = ec.run(**snapshots, out=output)
    assert result.matters == ["alpha", "beta", "unused"]
    assert len(result.stale_flags) == len(result.payloads) == 4
    assert result.filed == 0 and result.model_flags == []
    assert len({payload["id"] for payload in result.payloads}) == 4
    assert all(payload["origin"] == "ai_rewrite" and "new_text" not in payload
               and ec.REPAIR_ROUTES in payload["comment"] for payload in result.payloads)
    assert {flag["old_literal"] for flag in result.stale_flags} == {"Old Caption", "$2,500"}
    assert ec.daemon_summary(result) == {"status": "flagged", "stale_count": 4,
                                        "model_count": 0, "filed": 0}
    assert "4 stale-value" in output.getvalue()
    assert ec.run(**snapshots).payloads == result.payloads


def test_model_failure_preserves_real_deterministic_results(snapshots):
    snapshots["no_model"] = False
    seen = []
    def unavailable(prompt):
        seen.append(prompt)
        return False, "", None
    output = io.StringIO()
    result = ec.run(**snapshots, cli_runner=unavailable, out=output)
    assert len(seen) == 2
    assert all("New Caption" in prompt and "Old Caption" in prompt for prompt in seen)
    assert len(result.stale_flags) == 4 and result.model_flags == []
    assert result.model_degraded == "cli_failed"
    assert output.getvalue().count("deterministic flags unaffected") == 2


def test_model_parsing_and_deterministic_flags_share_comment_pipeline(snapshots):
    snapshots["no_model"] = False
    snapshots["matter"] = "alpha"
    raw = json.dumps({"flags": [{"source_ref": "data/matters/alpha/facts.md#b1",
                                 "message": ec.GUESS_PREFIX + "Check the caption."}]})
    result = ec.run(**snapshots, cli_runner=lambda prompt: (True, raw, None))
    assert len(result.stale_flags) == 2 and len(result.model_flags) == 1
    comment = result.payloads[-1]["comment"]
    assert comment.count(ec.GUESS_PREFIX) == 1
    assert comment.endswith(ec.REPAIR_ROUTES)
    assert result.model_flags[0]["old_literal"] == ""


@pytest.mark.parametrize("contents", [None, "{broken json"])
def test_unavailable_map_reports_nothing_checked(tmp_path, monkeypatch, contents):
    path = tmp_path / "map.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(ec, "EDITOR_MAP_PATH", str(path))
    output = io.StringIO()
    result = ec.run(since="snapshot", old_facts_loader=lambda slug: [], out=output)
    assert result.matters == result.payloads == [] and result.filed == 0
    assert "nothing checked" in output.getvalue()


@pytest.mark.parametrize("selector", ["..HEAD", "...HEAD"])
def test_empty_base_is_rejected_before_map_access(selector):
    output = io.StringIO()
    result = ec.run(since=selector, out=output)
    assert result.model_degraded == "bad_since"
    assert "no base revision" in output.getvalue()


@pytest.mark.parametrize("helper,args", [(ec._rev_exists, ("missing", "HEAD")),
                                          (ec._git_show_json, ("missing", "HEAD", "a.json"))])
@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("git", 1)])
def test_git_process_failure_degrades(helper, args, error, monkeypatch):
    def fail(*unused, **kwargs):
        raise error
    monkeypatch.setattr(ec.subprocess, "run", fail)
    assert helper(*args) in (False, None)


def test_git_non_json_content_is_not_a_fact(monkeypatch):
    monkeypatch.setattr(ec.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, stdout="not json"))
    assert ec._git_show_json("fixture", "HEAD", "a.json") is None


def test_new_fact_without_old_counterpart_is_not_stale():
    changes = ec.diff_fact_rows([("a.json", "retired", "Previous")],
                               [("a.json", "new_fact", "Current")])
    assert changes == []
    assert ec.stale_value_flags(changes, [{"source_ref": "a.md", "original_text": "Previous"}]) == []


def test_empty_forms_and_empty_model_sample_need_no_process():
    assert ec._form_spans("anything", set()) == []
    assert ec.model_contradiction_flags("empty", [], []) == ([], None)


def test_prompt_applies_sample_and_character_budgets():
    blocks = [{"source_ref": "first", "original_text": "x" * 100},
              {"source_ref": "excluded", "original_text": "never include"}]
    prompt = ec.build_contradiction_prompt("fixture", [("a", "caption", 'Quoted "caption"')],
                                            blocks, sample=1, max_chars=20)
    body = prompt.split("=== PROSE BLOCKS ===\n")[1]
    assert body == "[first]\n" + "x" * 12 + "\n\n[...blocks truncated for length...]"
    assert "excluded" not in prompt and '\\"caption\\"' in prompt


def test_comment_cap_preserves_identity():
    flag = {"source_ref": "a.md#b1", "fact_path": "a.json#caption",
            "old_literal": "Earlier", "message": "x" * 17000}
    payload = ec.flag_payload(flag)
    assert len(payload["comment"]) == 16000
    flag["message"] = "Reworded explanation"
    assert ec.flag_payload(flag)["id"] == payload["id"]


def test_cli_without_api_uses_dry_run_for_disk_snapshot(snapshots, monkeypatch, capsys):
    from functools import partial
    monkeypatch.delenv(ec.ep.ENV_API_BASE, raising=False)
    monkeypatch.delenv(ec.ep.ENV_SERVICE_TOKEN, raising=False)
    # Bind only the fixture's filesystem/revision source; main and run stay real.
    monkeypatch.setattr(ec, "run", partial(ec.run, repo_root=snapshots["repo_root"],
                                           old_facts_loader=snapshots["old_facts_loader"]))
    assert ec.main(["--since", "snapshot", "--matter", "alpha", "--no-model"]) == 0
    output = capsys.readouterr().out
    assert "unset — running as --dry-run" in output
    assert "2 stale-value, 0 AI-guess" in output


def test_disk_snapshot_empty_matter_is_clean(snapshots):
    result = ec.run(**snapshots, matter="absent")
    assert result.matters == ["absent"]
    assert result.payloads == result.stale_flags == result.model_flags == []
    assert ec.daemon_summary(result) == {"status": "clean", "stale_count": 0,
                                        "model_count": 0, "filed": 0}
