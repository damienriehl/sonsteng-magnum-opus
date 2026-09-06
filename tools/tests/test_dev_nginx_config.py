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
    inherited_runner_override: Path | None = None,
    *,
    curl_failures: int = 0,
    fake_clock_step_ms: int | None = None,
    fake_clock_fail_after: int | None = None,
    fake_probe_elapsed_ms: int = 0,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[list[str]],
    list[str],
    list[str],
]:
    source = PREFLIGHT.read_text(encoding="utf-8")
    functions = source[
        source.index("resolve_node_binary() {") : source.index("# ---- headless gates")
    ]
    definitions = tmp_path / "preflight-functions.sh"
    definitions.write_text(functions, encoding="utf-8")

    repo_root = tmp_path / "repo"
    (repo_root / "site").mkdir(parents=True)
    (repo_root / "tools").mkdir()
    (repo_root / "tools" / "verify_persona_journeys.js").write_text(
        textwrap.dedent(
            """\
            const fs = require('node:fs');
            fs.writeFileSync(
              process.env.RUNNER_ARGS_FILE,
              `${process.argv.slice(1).join('\\n')}\\n`,
            );
            fs.writeFileSync(
              process.env.RUNNER_ENV_FILE,
              `${process.env.NODE_OPTIONS ?? '<unset>'}\\n` +
                `${process.env.NODE_PATH ?? '<unset>'}\\n`,
            );
            process.exit(Number(process.env.STUB_RUNNER_EXIT));
            """
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    server_pid_file = tmp_path / "server.pid"
    curl_args_file = tmp_path / "curl.args"
    curl_count_file = tmp_path / "curl.count"
    runner_args_file = tmp_path / "runner.args"
    runner_env_file = tmp_path / "runner.env"
    sleep_args_file = tmp_path / "sleep.args"
    fake_clock_file = tmp_path / "fake-clock.ms"
    fake_node_marker = tmp_path / "fake-node-ran"
    browser = tmp_path / "browser"

    _write_executable(browser, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        bin_dir / "node",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            : > "$FAKE_NODE_MARKER"
            exit 99
            """
        ),
    )
    _write_executable(
        bin_dir / "python3",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [ "${1:-}" = "-m" ] && [ "${2:-}" = "http.server" ]; then
              printf '%s\n' "$$" > "$SERVER_PID_FILE"
              exec /bin/sleep 30
            fi
            if [ "${1:-}" = "-c" ]; then
              if [[ "${2:-}" == *"time.monotonic_ns"* ]]; then
                exec "$TRUSTED_PYTHON_BIN" "$@"
              fi
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
              /bin/sleep 0.01
            done
            count=0
            [ ! -f "$CURL_COUNT_FILE" ] || count=$(cat "$CURL_COUNT_FILE")
            count=$((count + 1))
            printf '%s\n' "$count" > "$CURL_COUNT_FILE"
            {
              printf 'CALL\n'
              printf '%s\n' "$@"
            } >> "$CURL_ARGS_FILE"
            if [ "$FAKE_PROBE_ELAPSED_MS" -gt 0 ]; then
              timeout_seconds=""
              while [ "$#" -gt 0 ]; do
                if [ "$1" = "--max-time" ]; then
                  timeout_seconds="$2"
                  break
                fi
                shift
              done
              whole="${timeout_seconds%%.*}"
              fraction="${timeout_seconds#*.}000"
              timeout_ms=$((10#$whole * 1000 + 10#${fraction:0:3}))
              elapsed_ms=$FAKE_PROBE_ELAPSED_MS
              [ "$elapsed_ms" -le "$timeout_ms" ] || elapsed_ms=$timeout_ms
              now=$(cat "$FAKE_CLOCK_FILE")
              printf '%s\n' "$((now + elapsed_ms))" > "$FAKE_CLOCK_FILE"
            fi
            if [ "$count" -le "$STUB_CURL_FAILURES" ]; then
              exit 7
            fi
            exit 0
            """
        ),
    )

    env = os.environ.copy()
    env.pop("PERSONA_JOURNEY_RUNNER", None)
    env.update(
        {
            "CHROME_BIN": str(browser),
            "CURL_ARGS_FILE": str(curl_args_file),
            "CURL_COUNT_FILE": str(curl_count_file),
            "FAKE_CLOCK_STEP_MS": (
                "" if fake_clock_step_ms is None else str(fake_clock_step_ms)
            ),
            "FAKE_CLOCK_FILE": str(fake_clock_file),
            "FAKE_CLOCK_FAIL_AFTER": (
                "" if fake_clock_fail_after is None else str(fake_clock_fail_after)
            ),
            "FAKE_PROBE_ELAPSED_MS": str(fake_probe_elapsed_ms),
            "FAKE_NODE_MARKER": str(fake_node_marker),
            "NODE_OPTIONS": "--require=/hostile/path/node-options-shadow.js",
            "NODE_PATH": "/hostile/path/node-modules-shadow",
            "PATH": f"{bin_dir}:{env['PATH']}",
            "PREFLIGHT_FUNCTIONS": str(definitions),
            "REPO_ROOT": str(repo_root),
            "RUNNER_ARGS_FILE": str(runner_args_file),
            "RUNNER_ENV_FILE": str(runner_env_file),
            "SERVER_PID_FILE": str(server_pid_file),
            "SLEEP_ARGS_FILE": str(sleep_args_file),
            "STUB_CURL_FAILURES": str(curl_failures),
            "STUB_RUNNER_EXIT": str(runner_exit),
            "TRUSTED_PYTHON_BIN": shutil.which("python3", path=env["PATH"])
            or "python3",
        }
    )
    if inherited_runner_override is not None:
        env["PERSONA_JOURNEY_RUNNER"] = str(inherited_runner_override)
    harness = textwrap.dedent(
        """\
        source "$PREFLIGHT_FUNCTIONS"
        ROOT="$REPO_ROOT"
        if [ -n "$FAKE_CLOCK_STEP_MS" ]; then
          printf '0\n' > "$FAKE_CLOCK_FILE"
          fake_clock_calls=0
          monotonic_millis() {
            fake_clock_calls=$((fake_clock_calls + 1))
            if [ -n "$FAKE_CLOCK_FAIL_AFTER" ] && \
                [ "$fake_clock_calls" -gt "$FAKE_CLOCK_FAIL_AFTER" ]; then
              return 1
            fi
            printf -v "$1" '%d' "$(cat "$FAKE_CLOCK_FILE")"
          }
          sleep() {
            printf '%s\n' "$@" >> "$SLEEP_ARGS_FILE"
            sleep_seconds="$1"
            whole="${sleep_seconds%%.*}"
            fraction="${sleep_seconds#*.}000"
            requested_ms=$((10#$whole * 1000 + 10#${fraction:0:3}))
            elapsed_ms=$FAKE_CLOCK_STEP_MS
            [ "$elapsed_ms" -le "$requested_ms" ] || elapsed_ms=$requested_ms
            fake_time_ms=$(cat "$FAKE_CLOCK_FILE")
            printf '%s\n' "$((fake_time_ms + elapsed_ms))" > "$FAKE_CLOCK_FILE"
          }
        fi
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
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    curl_calls: list[list[str]] = []
    if curl_args_file.exists():
        for call in curl_args_file.read_text(encoding="utf-8").split("CALL\n"):
            if call:
                curl_calls.append(call.splitlines())
    runner_arguments = (
        runner_args_file.read_text(encoding="utf-8").splitlines()
        if runner_args_file.exists()
        else []
    )
    sleep_arguments = (
        sleep_args_file.read_text(encoding="utf-8").splitlines()
        if sleep_args_file.exists()
        else []
    )
    return result, curl_calls, runner_arguments, sleep_arguments


@pytest.mark.parametrize("runner_exit", [0, 23])
def test_preflight_persona_gate_propagates_status_and_cleans_up_server(
    tmp_path: Path,
    runner_exit: int,
) -> None:
    result, curl_calls, runner_arguments, _ = _run_persona_gate(
        tmp_path, runner_exit
    )

    assert result.returncode == runner_exit, result.stdout + result.stderr
    assert runner_arguments == [
        str(tmp_path / "repo" / "tools" / "verify_persona_journeys.js"),
        "--base",
        "http://127.0.0.1:49152",
        "--env-label",
        "local",
        "--run-dir",
        str(tmp_path / "repo" / "build" / "uat" / "preflight" / "runs"),
        "--shots-dir",
        str(tmp_path / "repo" / "build" / "uat" / "preflight" / "shots"),
    ]
    assert len(curl_calls) == 1
    curl_arguments = curl_calls[0]
    assert "--connect-timeout" in curl_arguments
    assert "--max-time" in curl_arguments
    connect_timeout = float(
        curl_arguments[curl_arguments.index("--connect-timeout") + 1]
    )
    request_timeout = float(curl_arguments[curl_arguments.index("--max-time") + 1])
    assert connect_timeout == 0.5
    assert request_timeout == 0.75


def test_preflight_persona_gate_ignores_inherited_runner_override(
    tmp_path: Path,
) -> None:
    bypass_runner = tmp_path / "bypass-runner.js"
    bypass_runner.write_text("process.exit(0);\n", encoding="utf-8")

    result, _, runner_arguments, _ = _run_persona_gate(
        tmp_path,
        runner_exit=23,
        inherited_runner_override=bypass_runner,
    )

    assert result.returncode == 23, result.stdout + result.stderr
    assert runner_arguments[0] == str(
        tmp_path / "repo" / "tools" / "verify_persona_journeys.js"
    )


def test_preflight_persona_gate_uses_pinned_node_when_path_is_shadowed(
    tmp_path: Path,
) -> None:
    result, _, _, _ = _run_persona_gate(tmp_path, runner_exit=23)

    assert result.returncode == 23, result.stdout + result.stderr
    assert not (tmp_path / "fake-node-ran").exists()
    assert (tmp_path / "runner.env").read_text(encoding="utf-8") == (
        "<unset>\n<unset>\n"
    )


def test_preflight_persona_gate_retries_with_delay_until_ready(tmp_path: Path) -> None:
    result, curl_calls, _, sleep_arguments = _run_persona_gate(
        tmp_path,
        runner_exit=0,
        curl_failures=5,
        fake_clock_step_ms=100,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(curl_calls) == 6
    assert sleep_arguments == ["0.100"] * 5


def test_preflight_persona_gate_stops_probing_at_readiness_deadline(
    tmp_path: Path,
) -> None:
    result, curl_calls, runner_arguments, sleep_arguments = _run_persona_gate(
        tmp_path,
        runner_exit=0,
        curl_failures=100,
        fake_clock_step_ms=1_000,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert len(curl_calls) == 50
    assert sleep_arguments == ["0.100"] * 50
    assert not runner_arguments


def test_preflight_persona_gate_caps_slow_probe_at_readiness_deadline(
    tmp_path: Path,
) -> None:
    result, curl_calls, runner_arguments, sleep_arguments = _run_persona_gate(
        tmp_path,
        runner_exit=0,
        curl_failures=100,
        fake_clock_step_ms=100,
        fake_probe_elapsed_ms=700,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert len(curl_calls) == 7
    assert [call[call.index("--max-time") + 1] for call in curl_calls] == [
        "0.750",
        "0.750",
        "0.750",
        "0.750",
        "0.750",
        "0.750",
        "0.200",
    ]
    assert sleep_arguments == ["0.100"] * 6
    assert int((tmp_path / "fake-clock.ms").read_text(encoding="utf-8")) == 5000
    assert not runner_arguments


def test_preflight_persona_gate_caps_final_sleep_at_readiness_deadline(
    tmp_path: Path,
) -> None:
    result, _, runner_arguments, sleep_arguments = _run_persona_gate(
        tmp_path,
        runner_exit=0,
        curl_failures=100,
        fake_clock_step_ms=100,
        fake_probe_elapsed_ms=740,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert sleep_arguments == ["0.100"] * 5 + ["0.060"]
    assert int((tmp_path / "fake-clock.ms").read_text(encoding="utf-8")) == 5000
    assert not runner_arguments


def test_preflight_persona_gate_cleans_up_when_clock_fails(tmp_path: Path) -> None:
    result, curl_calls, runner_arguments, sleep_arguments = _run_persona_gate(
        tmp_path,
        runner_exit=0,
        fake_clock_step_ms=100,
        fake_clock_fail_after=1,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not read the monotonic clock" in result.stderr
    assert not curl_calls
    assert not runner_arguments
    assert not sleep_arguments


def test_preflight_monotonic_clock_uses_portable_python_runtime(
    tmp_path: Path,
) -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    clock_function = source[
        source.index("monotonic_millis() {") : source.index(
            "run_local_persona_journeys() ("
        )
    ]
    assert "/proc/" not in clock_function
    definitions = tmp_path / "monotonic-clock.sh"
    definitions.write_text(clock_function, encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$CLOCK_FUNCTION"; '
            "monotonic_millis before; /bin/sleep 0.02; "
            "monotonic_millis after; "
            'printf "%s\\n%s\\n" "$before" "$after"',
        ],
        env={**os.environ, "CLOCK_FUNCTION": str(definitions)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    before, after = (int(value) for value in result.stdout.splitlines())
    assert before >= 0
    assert after > before


def test_preflight_persona_gate_requires_a_browser_and_is_skippable() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    gate = "persona journeys (local browser leg)"

    assert "Install Chromium or Google Chrome" in source
    assert re.search(rf'run "{re.escape(gate)}"\s+run_local_persona_journeys', source)
    assert source.count(f'skip "{gate}"') == 2
    assert source.index(f'run "{gate}"') > source.index('run "rail placement')
