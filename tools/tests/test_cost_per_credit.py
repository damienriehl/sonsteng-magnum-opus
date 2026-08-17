"""Focused contracts for the dean-facing cost-per-credit calculator."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "site/cost-per-credit.html"
PITCH = ROOT / "site/index.html"
COPY = ROOT / "data/copy/cost-per-credit.json"


def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def run_core(expression: str):
    match = re.search(
        r'<script id="calculator-core">\s*(.*?)\s*</script>',
        source(),
        re.DOTALL,
    )
    assert match, "calculator core must be embedded for a standalone page"
    script = f"{match.group(1)}\n{expression}"
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True
    )
    return json.loads(result.stdout)


def test_default_stipend_model_and_settled_hours_initialize_on_load_without_invented_costs():
    html = source()
    assert 'value="stipend" checked' in html
    assert 'data-hours="225"' in html
    assert "initializeCalculator()" in html
    financial_inputs = re.findall(r'<input(?![^>]*type="radio")[^>]*>', html)
    assert financial_inputs
    assert all('value=""' in field for field in financial_inputs)


def test_pitch_has_one_click_nav_and_inline_delivery_cost_routes():
    html = PITCH.read_text(encoding="utf-8")
    href = 'href="cost-per-credit.html"'
    assert html.count(href) >= 2
    assert re.search(r'<nav[\s\S]*?href="cost-per-credit\.html"', html)
    assert re.search(r'class="ledger"[\s\S]*?href="cost-per-credit\.html"', html)


def test_four_comparator_cost_inputs_start_blank_with_units_and_ranges():
    html = source()
    for key in ("standard", "seminar", "clinic", "internship"):
        assert re.search(fr'id="{key}-cost"[^>]*value=""', html)
    config = json.loads(COPY.read_text(encoding="utf-8"))
    assert [item["id"] for item in config["comparators"]] == [
        "standard", "seminar", "clinic", "internship"
    ]
    comparator_ids = {item["id"] for item in config["comparators"]}
    for item in config["comparators"] + config["faculty_inputs"]:
        assert item["unit"] and item["range"]["min"] < item["range"]["max"]
        field_id = f'{item["id"]}-cost' if item["id"] in comparator_ids else item["id"]
        field = re.search(fr'<input[^>]*id="{re.escape(field_id)}"[^>]*>', html)
        assert field, field_id
        tag = field.group(0)
        assert f'data-min="{item["range"]["min"]}"' in tag
        assert f'data-max="{item["range"]["max"]}"' in tag
        assert f'<label for="{field_id}">{item["label"]}</label>' in html
        assert item["unit"].casefold() in html.casefold()


def test_aba_arithmetic_is_rendered_and_reconciles():
    html = source()
    assert "(1 classroom hour + 2 out-of-class hours) × 15 weeks × 5 credits = 225 hours" in html
    assert run_core("console.log(JSON.stringify(CostCalculator.creditHours()))") == 225


def test_each_pay_model_calculates_after_inputs_are_filled():
    result = run_core(
        "console.log(JSON.stringify({"
        "stipend:CostCalculator.calculatePracticum('stipend',{exerciseCount:20,stipendPerExercise:1500,credits:5}),"
        "load:CostCalculator.calculatePracticum('load',{annualSalary:120000,annualLoadCredits:12,credits:5})"
        "}))"
    )
    assert result == {"stipend": {"total": 30000, "perCredit": 6000},
                      "load": {"total": 50000, "perCredit": 10000}}


def test_model_switch_changes_only_practicum_state():
    result = run_core(
        "console.log(JSON.stringify([CostCalculator.activePanel('stipend'),"
        "CostCalculator.activePanel('load')]))"
    )
    assert result == ["stipend-panel", "load-panel"]
    html = source()
    assert "for(const id of ['stipend-panel','load-panel'])" in html
    assert "recomputePracticum(model)" in html


def test_immediate_recompute_invalid_state_and_last_valid_output_contracts():
    html = source()
    assert "addEventListener('input', handleInput)" in html
    assert "addEventListener('change', handleModelChange)" in html
    assert 'role="status" aria-live="polite"' in html
    assert "if(announceId)" in html
    assert "recomputePracticum(model,id)" in html
    assert "if(!checks.every(item=>item.valid))return" in html
    assert "if(!check.valid)return" in html
    result = run_core(
        "console.log(JSON.stringify(["
        "CostCalculator.validate('',0,100),"
        "CostCalculator.validate('words',0,100),"
        "CostCalculator.validate('101',0,100),"
        "CostCalculator.validate('50',0,100)]))"
    )
    assert [item["valid"] for item in result] == [False, False, False, True]


def test_no_nan_infinity_persistence_submission_or_network_apis():
    html = source()
    forbidden = ("NaN", "Infinity", "localStorage", "sessionStorage", "document.cookie",
                 "fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "<form")
    assert not any(token in html for token in forbidden)


def test_verify_pitch_asset_and_size_gate_covers_page():
    result = subprocess.run(
        ["python3", "tools/verify_pitch.py", str(PAGE)], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert PAGE.stat().st_size < 250_000


def test_mobile_and_print_css_contracts():
    html = source()
    assert "@media (max-width: 390px)" in html
    assert "@media print" in html
    assert re.search(r"overflow-x:\s*auto", html)
    assert ":focus-visible" in html
    assert re.search(r"break-inside:\s*avoid", html)
    assert "details:not([open])>.sheet{display:block!important}" in html


def test_browser_interaction_gate_is_wired_into_preflight():
    preflight = (ROOT / "tools/preflight.sh").read_text(encoding="utf-8")
    verifier = ROOT / "tools/verify_cost_per_credit.js"
    assert verifier.exists()
    assert 'run "cost-per-credit interactions"' in preflight
    assert "node tools/verify_cost_per_credit.js" in preflight
    assert 'run "cost-per-credit accessibility"' in preflight
    assert 'node tools/a11y_audit.js "file://$ROOT/site/cost-per-credit.html"' in preflight
