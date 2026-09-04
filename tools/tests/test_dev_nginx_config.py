from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = ROOT / "deploy" / "nginx" / "default.conf"
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.yml"
PREFLIGHT = ROOT / "tools" / "preflight.sh"


@dataclass(frozen=True)
class NginxDirective:
    name: str
    arguments: tuple[str, ...]
    children: tuple[NginxDirective, ...] | None


def _tokenize_nginx(source: str) -> list[str]:
    tokens: list[str] = []
    word: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    def flush_word() -> None:
        if word:
            tokens.append("".join(word))
            word.clear()

    while index < len(source):
        char = source[index]
        if escaped:
            word.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote is not None:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in {'"', "'"}:
            quote = char
        elif char == "#":
            flush_word()
            newline = source.find("\n", index)
            index = len(source) if newline == -1 else newline
        elif char.isspace():
            flush_word()
        elif char in "{};":
            flush_word()
            tokens.append(char)
        else:
            word.append(char)
        index += 1

    if quote is not None or escaped:
        raise ValueError("unterminated quoted or escaped nginx token")
    flush_word()
    return tokens


def _parse_nginx(source: str) -> tuple[NginxDirective, ...]:
    tokens = _tokenize_nginx(source)
    index = 0

    def parse_block(expect_closing_brace: bool) -> tuple[NginxDirective, ...]:
        nonlocal index
        directives: list[NginxDirective] = []
        while index < len(tokens):
            if tokens[index] == "}":
                if not expect_closing_brace:
                    raise ValueError("unexpected closing brace")
                index += 1
                return tuple(directives)

            words: list[str] = []
            while index < len(tokens) and tokens[index] not in "{};":
                words.append(tokens[index])
                index += 1
            if not words:
                token = tokens[index] if index < len(tokens) else "end of file"
                raise ValueError(f"unexpected {token!r}")
            if index == len(tokens):
                raise ValueError(f"unterminated nginx directive: {' '.join(words)}")

            terminator = tokens[index]
            index += 1
            if terminator == ";":
                children = None
            elif terminator == "{":
                children = parse_block(expect_closing_brace=True)
            else:
                raise ValueError(f"directive missing terminator before {terminator!r}")
            directives.append(NginxDirective(words[0], tuple(words[1:]), children))

        if expect_closing_brace:
            raise ValueError("unclosed nginx block")
        return tuple(directives)

    return parse_block(expect_closing_brace=False)


def _walk_directives(directives: tuple[NginxDirective, ...]):
    for directive in directives:
        yield directive
        if directive.children is not None:
            yield from _walk_directives(directive.children)


def _assert_clean_url_config(source: str) -> None:
    directives = _parse_nginx(source)
    root_locations = [
        directive
        for directive in _walk_directives(directives)
        if directive.name == "location"
        and directive.arguments == ("/",)
        and directive.children is not None
    ]
    assert root_locations, "an active `location /` block is required"
    assert any(
        child.name == "try_files"
        and child.arguments == ("$uri", "$uri.html", "$uri/", "=404")
        and child.children is None
        for location in root_locations
        for child in location.children or ()
    ), "`try_files $uri $uri.html $uri/ =404;` must be active inside `location /`"
    assert not any(
        directive.name == "autoindex" and directive.arguments == ("on",)
        for directive in _walk_directives(directives)
    ), "no active `autoindex on;` directive is allowed"


def test_dev_nginx_serves_clean_urls_without_enabling_autoindex() -> None:
    assert NGINX_CONFIG.is_file()
    _assert_clean_url_config(NGINX_CONFIG.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "broken_config",
    [
        "# location / { try_files $uri $uri.html $uri/ =404; }",
        "try_files $uri $uri.html $uri/ =404; server { location / {} }",
        "server { location / { try_files $uri $uri.html $uri/ =404; autoindex on; } }",
    ],
)
def test_nginx_contract_rejects_inactive_or_misplaced_directives(
    broken_config: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_clean_url_config(broken_config)


def test_nginx_parser_rejects_invalid_block_structure() -> None:
    with pytest.raises(ValueError, match="unclosed nginx block"):
        _parse_nginx("server { location / { try_files $uri $uri.html $uri/ =404; }")


def test_dev_nginx_config_passes_available_native_validation(tmp_path: Path) -> None:
    nginx = shutil.which("nginx")
    if nginx:
        main_config = tmp_path / "nginx.conf"
        main_config.write_text(
            f"error_log stderr;\npid {tmp_path / 'nginx.pid'};\n"
            f"events {{}}\nhttp {{ include {NGINX_CONFIG}; }}\n",
            encoding="utf-8",
        )
        command = [nginx, "-t", "-p", str(tmp_path), "-c", str(main_config)]
    else:
        docker = shutil.which("docker")
        if not docker:
            pytest.skip("neither nginx nor docker is available")
        image = subprocess.run(
            [docker, "image", "inspect", "nginx:alpine"],
            check=False,
            capture_output=True,
            text=True,
        )
        if image.returncode != 0:
            pytest.skip("docker cannot access a local nginx:alpine image")
        command = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--pull",
            "never",
            "--volume",
            f"{NGINX_CONFIG}:/etc/nginx/conf.d/default.conf:ro",
            "nginx:alpine",
            "nginx",
            "-t",
        ]

    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def _compose_mount(volume: object) -> tuple[str, str, bool] | None:
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) < 2:
            return None
        options = parts[2].split(",") if len(parts) > 2 else []
        return parts[0], parts[1], "ro" in options
    if isinstance(volume, dict):
        source = volume.get("source")
        target = volume.get("target")
        if isinstance(source, str) and isinstance(target, str):
            return source, target, volume.get("read_only") is True
    return None


def test_dev_nginx_config_is_mounted_read_only() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    volumes = compose["services"]["sonsteng"].get("volumes", [])
    mounts = [_compose_mount(volume) for volume in volumes]

    assert (
        "./nginx/default.conf",
        "/etc/nginx/conf.d/default.conf",
        True,
    ) in mounts


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _run_persona_gate(
    tmp_path: Path,
    runner_exit: int,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    source = PREFLIGHT.read_text(encoding="utf-8")
    functions = source[
        source.index("find_chromium() {") : source.index("# ---- headless gates")
    ]
    definitions = tmp_path / "preflight-functions.sh"
    definitions.write_text(functions, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    server_pid_file = tmp_path / "server.pid"
    curl_args_file = tmp_path / "curl.args"
    runner_args_file = tmp_path / "runner.args"
    browser = tmp_path / "browser"
    runner = tmp_path / "verify_persona_journeys.js"

    _write_executable(browser, "#!/usr/bin/env bash\nexit 0\n")
    runner.write_text(
        "require('node:fs').writeFileSync(process.env.RUNNER_ARGS_FILE, "
        "process.argv.slice(2).join('\\n'));\n"
        "process.exit(Number(process.env.STUB_RUNNER_EXIT));\n",
        encoding="utf-8",
    )
    _write_executable(
        bin_dir / "python3",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [ "${1:-}" = "-m" ] && [ "${2:-}" = "http.server" ]; then
              printf '%s\n' "$$" > "$SERVER_PID_FILE"
              exec sleep 30
            fi
            if [ "${1:-}" = "-c" ]; then
              printf '49152\n'
              exit 0
            fi
            exit 2
            """
        ),
    )
    _write_executable(
        bin_dir / "curl",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            for _ in $(seq 1 100); do
              [ -s "$SERVER_PID_FILE" ] && break
              sleep 0.01
            done
            printf '%s\n' "$@" > "$CURL_ARGS_FILE"
            exit 0
            """
        ),
    )

    env = os.environ.copy()
    env.update(
        {
            "CHROME_BIN": str(browser),
            "CURL_ARGS_FILE": str(curl_args_file),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "PERSONA_JOURNEY_RUNNER": str(runner),
            "PREFLIGHT_FUNCTIONS": str(definitions),
            "REPO_ROOT": str(ROOT),
            "RUNNER_ARGS_FILE": str(runner_args_file),
            "SERVER_PID_FILE": str(server_pid_file),
            "STUB_RUNNER_EXIT": str(runner_exit),
        }
    )
    harness = textwrap.dedent(
        """\
        source "$PREFLIGHT_FUNCTIONS"
        ROOT="$REPO_ROOT"
        if run_local_persona_journeys; then
          gate_status=0
        else
          gate_status=$?
        fi
        server_pid=$(cat "$SERVER_PID_FILE")
        if kill -0 "$server_pid" >/dev/null 2>&1; then
          printf 'local server still running: %s\n' "$server_pid" >&2
          kill "$server_pid" >/dev/null 2>&1 || true
          exit 97
        fi
        exit "$gate_status"
        """
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    curl_arguments = (
        curl_args_file.read_text(encoding="utf-8").splitlines()
        if curl_args_file.exists()
        else []
    )
    runner_arguments = (
        runner_args_file.read_text(encoding="utf-8").splitlines()
        if runner_args_file.exists()
        else []
    )
    return result, curl_arguments, runner_arguments


@pytest.mark.parametrize("runner_exit", [0, 23])
def test_preflight_persona_gate_propagates_status_and_cleans_up_server(
    tmp_path: Path,
    runner_exit: int,
) -> None:
    result, curl_arguments, runner_arguments = _run_persona_gate(tmp_path, runner_exit)

    assert result.returncode == runner_exit, result.stdout + result.stderr
    assert runner_arguments == [
        "--base",
        "http://127.0.0.1:49152",
        "--env-label",
        "local",
        "--run-dir",
        str(ROOT / "build" / "uat" / "preflight" / "runs"),
        "--shots-dir",
        str(ROOT / "build" / "uat" / "preflight" / "shots"),
    ]
    assert "--connect-timeout" in curl_arguments
    assert "--max-time" in curl_arguments
    for option in ("--connect-timeout", "--max-time"):
        timeout = float(curl_arguments[curl_arguments.index(option) + 1])
        assert 0 < timeout <= 0.1


def test_preflight_persona_gate_requires_a_browser_and_is_skippable() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    gate = "persona journeys (local browser leg)"

    assert "Install Chromium or Google Chrome" in source
    assert re.search(rf'run "{re.escape(gate)}"\s+run_local_persona_journeys', source)
    assert source.count(f'skip "{gate}"') == 2
    assert source.index(f'run "{gate}"') > source.index('run "rail placement')
