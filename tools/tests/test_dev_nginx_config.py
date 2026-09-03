from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = ROOT / "deploy" / "nginx" / "default.conf"
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.yml"
PREFLIGHT = ROOT / "tools" / "preflight.sh"


def test_dev_nginx_serves_clean_urls_without_enabling_autoindex() -> None:
    assert NGINX_CONFIG.is_file()

    config = NGINX_CONFIG.read_text(encoding="utf-8")
    assert "try_files $uri $uri.html $uri/ =404;" in config
    assert not re.search(r"^\s*autoindex\s+on\s*;", config, flags=re.MULTILINE)


def test_dev_nginx_config_is_mounted_read_only() -> None:
    compose = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" in compose


def test_preflight_runs_local_persona_journeys_with_repo_local_artifacts() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")

    assert 's.bind(("127.0.0.1", 0))' in source
    assert 'python3 -m http.server "$port" --bind 127.0.0.1 --directory "$ROOT/site"' in source
    assert 'trap cleanup EXIT' in source
    assert 'node tools/verify_persona_journeys.js \\' in source
    assert '--base "http://127.0.0.1:$port"' in source
    assert "--env-label local" in source
    assert '--run-dir "$ROOT/build/uat/preflight/runs"' in source
    assert '--shots-dir "$ROOT/build/uat/preflight/shots"' in source
    function_body = source.split("run_local_persona_journeys() (", 1)[1].split("\n)\n", 1)[0]
    assert "grep" not in function_body
    assert "status=$?" in function_body
    assert 'return "$status"' in function_body


def test_preflight_persona_gate_requires_a_browser_and_is_skippable() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    gate = 'persona journeys (local browser leg)'

    assert "Install Chromium or Google Chrome" in source
    assert re.search(rf'run "{re.escape(gate)}"\s+run_local_persona_journeys', source)
    assert source.count(f'skip "{gate}"') == 2
    assert source.index(f'run "{gate}"') > source.index('run "rail placement')
