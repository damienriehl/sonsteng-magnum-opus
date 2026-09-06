from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
import time
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


def _walk_directives(
    directives: tuple[NginxDirective, ...],
    ancestry: tuple[NginxDirective, ...] = (),
):
    for directive in directives:
        yield directive, ancestry
        if directive.children is not None:
            yield from _walk_directives(
                directive.children, (*ancestry, directive)
            )


def _assert_clean_url_config(source: str) -> None:
    directives = _parse_nginx(source)
    walked_directives = tuple(_walk_directives(directives))
    top_level_servers = [
        directive
        for directive, ancestry in walked_directives
        if directive.name == "server"
        and directive.children is not None
        and not ancestry
    ]
    assert len(top_level_servers) == 1, (
        "exactly one top-level `server` block is required"
    )
    intended_server = top_level_servers[0]
    root_locations = [
        (directive, ancestry)
        for directive, ancestry in walked_directives
        if directive.name == "location"
        and directive.arguments == ("/",)
        and directive.children is not None
    ]
    assert len(root_locations) == 1, (
        "exactly one active `location /` block is required"
    )
    root_location, ancestry = root_locations[0]
    assert ancestry == (
        intended_server,
    ), "`location /` must be a direct child of the top-level `server` block"
    assert any(
        child.name == "try_files"
        and child.arguments == ("$uri", "$uri.html", "$uri/", "=404")
        and child.children is None
        for child in root_location.children or ()
    ), "`try_files $uri $uri.html $uri/ =404;` must be active inside `location /`"
    assert not any(
        directive.name == "autoindex" and directive.arguments == ("on",)
        for directive, _ in walked_directives
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


def test_nginx_contract_rejects_root_location_directly_under_http() -> None:
    with pytest.raises(AssertionError, match="must be a direct child"):
        _assert_clean_url_config(
            "http { location / { try_files $uri $uri.html $uri/ =404; } } "
            "server {}"
        )


def test_nginx_contract_rejects_root_location_nested_in_server_child() -> None:
    with pytest.raises(AssertionError, match="must be a direct child"):
        _assert_clean_url_config(
            "server { nested { "
            "location / { try_files $uri $uri.html $uri/ =404; } "
            "} }"
        )


def test_nginx_contract_rejects_duplicate_root_locations() -> None:
    clean_location = "location / { try_files $uri $uri.html $uri/ =404; }"
    with pytest.raises(
        AssertionError, match="exactly one active `location /` block"
    ):
        _assert_clean_url_config(f"server {{ {clean_location} {clean_location} }}")


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


def _preflight_function_source(source: str) -> str:
    end = source.index("# ---- headless gates")
    node_resolution = source[
        source.index("NODE_BIN=$(type -P node") : source.index('cd "$(dirname "$0")/.."')
    ]
    functions = source[source.index("WANT_BROWSER=1") : end]
    return node_resolution + functions


def _run_persona_gate(
    tmp_path: Path,
    runner_exit: int,
    inherited_runner_override: Path | None = None,
    *,
    curl_failures: int = 0,
    fake_clock_step_ms: int | None = None,
    fake_clock_fail_after: int | None = None,
    fake_probe_elapsed_ms: int = 0,
    curl_sleep_seconds: int = 0,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[list[str]],
    list[str],
    list[str],
]:
    source = PREFLIGHT.read_text(encoding="utf-8")
    functions = _preflight_function_source(source)
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
    after_start_bin_dir = tmp_path / "after-start-bin"
    after_start_bin_dir.mkdir()
    server_pid_file = tmp_path / "server.pid"
    curl_args_file = tmp_path / "curl.args"
    curl_count_file = tmp_path / "curl.count"
    curl_pid_file = tmp_path / "curl.pid"
    runner_args_file = tmp_path / "runner.args"
    runner_env_file = tmp_path / "runner.env"
    sleep_args_file = tmp_path / "sleep.args"
    fake_clock_file = tmp_path / "fake-clock.ms"
    startup_node_marker = tmp_path / "startup-node-ran"
    after_start_node_marker = tmp_path / "after-start-node-ran"
    browser = tmp_path / "browser"

    _write_executable(browser, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        bin_dir / "node",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            : > "$STARTUP_NODE_MARKER"
            exec "$TRUSTED_NODE_BIN" "$@"
            """
        ),
    )
    _write_executable(
        after_start_bin_dir / "node",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            : > "$AFTER_START_NODE_MARKER"
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
            if [ "$FAKE_CURL_SLEEP_SECONDS" -gt 0 ]; then
              printf '%s\n' "$$" > "$CURL_PID_FILE"
              trap '' TERM
              exec /bin/sleep "$FAKE_CURL_SLEEP_SECONDS"
            fi
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
            "AFTER_START_NODE_MARKER": str(after_start_node_marker),
            "AFTER_START_PATH": (
                f"{after_start_bin_dir}:{bin_dir}:{env['PATH']}"
            ),
            "CHROME_BIN": str(browser),
            "CURL_ARGS_FILE": str(curl_args_file),
            "CURL_COUNT_FILE": str(curl_count_file),
            "CURL_PID_FILE": str(curl_pid_file),
            "FAKE_CLOCK_STEP_MS": (
                "" if fake_clock_step_ms is None else str(fake_clock_step_ms)
            ),
            "FAKE_CLOCK_FILE": str(fake_clock_file),
            "FAKE_CLOCK_FAIL_AFTER": (
                "" if fake_clock_fail_after is None else str(fake_clock_fail_after)
            ),
            "FAKE_PROBE_ELAPSED_MS": str(fake_probe_elapsed_ms),
            "FAKE_CURL_SLEEP_SECONDS": str(curl_sleep_seconds),
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
            "STARTUP_NODE_MARKER": str(startup_node_marker),
            "TRUSTED_NODE_BIN": shutil.which("node", path=env["PATH"]) or "node",
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
        PATH="$AFTER_START_PATH"
        export PATH
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


def test_preflight_persona_gate_uses_node_from_startup_path_after_path_changes(
    tmp_path: Path,
) -> None:
    result, _, _, _ = _run_persona_gate(tmp_path, runner_exit=23)

    assert result.returncode == 23, result.stdout + result.stderr
    assert (tmp_path / "startup-node-ran").exists()
    assert not (tmp_path / "after-start-node-ran").exists()
    assert (tmp_path / "runner.env").read_text(encoding="utf-8") == (
        "<unset>\n<unset>\n"
    )


def test_preflight_fails_at_start_when_path_has_no_node(tmp_path: Path) -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    definitions = tmp_path / "preflight-functions.sh"
    definitions.write_text(_preflight_function_source(source), encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$PREFLIGHT_FUNCTIONS"'],
        env={"PATH": "", "PREFLIGHT_FUNCTIONS": str(definitions)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Node unavailable on PATH at preflight start.\n"


def test_preflight_rejects_bash_older_than_5_1_before_node_resolution(
    tmp_path: Path,
) -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    simulated_source = source.replace(
        "BASH_VERSINFO", "STUB_BASH_VERSINFO"
    ).replace("$BASH_VERSION", "$STUB_BASH_VERSION")
    simulated_preflight = tmp_path / "preflight.sh"
    simulated_preflight.write_text(simulated_source, encoding="utf-8")
    node_resolution_marker = tmp_path / "node-resolution-attempted"

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            textwrap.dedent(
                """\
                STUB_BASH_VERSINFO=(5 0)
                STUB_BASH_VERSION=5.0-test
                type() {
                  : > "$NODE_RESOLUTION_MARKER"
                  return 1
                }
                source "$SIMULATED_PREFLIGHT"
                """
            ),
        ],
        env={
            **os.environ,
            "NODE_RESOLUTION_MARKER": str(node_resolution_marker),
            "SIMULATED_PREFLIGHT": str(simulated_preflight),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Bash 5.1 or newer required; running 5.0-test.\n"
    assert not node_resolution_marker.exists()


def test_preflight_rejects_relative_node_path_at_start(tmp_path: Path) -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    definitions = tmp_path / "preflight-functions.sh"
    definitions.write_text(_preflight_function_source(source), encoding="utf-8")
    relative_bin = tmp_path / "relative-bin"
    relative_bin.mkdir()
    _write_executable(relative_bin / "node", "#!/bin/sh\nexit 0\n")

    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$PREFLIGHT_FUNCTIONS"'],
        cwd=tmp_path,
        env={"PATH": "relative-bin", "PREFLIGHT_FUNCTIONS": str(definitions)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Node unavailable on PATH at preflight start.\n"


def test_preflight_routes_every_node_gate_through_startup_node() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert re.search(r"^NODE_BIN=\$\(type -P node 2>/dev/null\)", source, re.MULTILINE)
    gate_source = source[source.index("run_node()") :]
    executable_node_references = [
        line.strip()
        for line in gate_source.splitlines()
        if not line.lstrip().startswith("#") and re.search(r"\bnode\b", line)
    ]

    assert not executable_node_references
    node_wrapper = source[source.index("run_node()") : source.index("find_chromium()")]
    assert "unset NODE_OPTIONS NODE_PATH" in node_wrapper
    assert 'exec "$NODE_BIN" "$@"' in node_wrapper
    assert "NODE_BIN" not in source[source.index("find_chromium()") :]


def _assert_single_offline_redteam_registration(source: str) -> None:
    headless_marker = "# ---- headless gates"
    browser_marker = "# ---- browser gates"
    assert source.count(headless_marker) == 1
    assert source.count(browser_marker) == 1
    headless_start = source.index(headless_marker)
    browser_start = source.index(browser_marker, headless_start)
    registrations = list(
        re.finditer(
            r'^run[\t ]+"offline red-team probe"[\t ]+run_offline_redteam_probe[\t ]*$',
            source,
            re.MULTILINE,
        )
    )
    assert len(registrations) == 1 and (
        headless_start < registrations[0].start() < browser_start
    ), (
        "preflight must contain exactly one executable top-level "
        '`run "offline red-team probe" run_offline_redteam_probe` registration'
    )


def test_preflight_registers_offline_redteam_probe_exactly_once() -> None:
    _assert_single_offline_redteam_registration(PREFLIGHT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("registration_count", [0, 2])
def test_offline_redteam_registration_contract_rejects_missing_or_duplicate_lines(
    registration_count: int,
) -> None:
    registration = 'run "offline red-team probe" run_offline_redteam_probe\n'
    source = (
        "# ---- headless gates\n"
        + registration * registration_count
        + "# ---- browser gates\n"
    )
    with pytest.raises(AssertionError, match="exactly one executable top-level"):
        _assert_single_offline_redteam_registration(source)


def test_offline_redteam_registration_contract_rejects_line_in_uncalled_function(
) -> None:
    registration = 'run "offline red-team probe" run_offline_redteam_probe\n'
    source = (
        "uncalled_probe_registration() {\n"
        + registration
        + "}\n"
        + "# ---- headless gates\n"
        + "# registration must execute here\n"
        + "# ---- browser gates\n"
    )

    with pytest.raises(AssertionError, match="exactly one executable top-level"):
        _assert_single_offline_redteam_registration(source)


@pytest.mark.parametrize(
    ("probe_exit", "probe_summary", "expected_exit", "expected_output"),
    [
        (0, "0/8", 0, ""),
        (0, "1/8", 1, "line one\nline two\n1/8\n"),
        (23, "0/8", 1, "line one\nline two\n0/8\n"),
    ],
)
def test_preflight_offline_redteam_probe_requires_zero_status_and_clean_summary(
    tmp_path: Path,
    probe_exit: int,
    probe_summary: str,
    expected_exit: int,
    expected_output: str,
) -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    definitions = tmp_path / "preflight-functions.sh"
    definitions.write_text(_preflight_function_source(source), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocations = tmp_path / "node-invocations"
    _write_executable(
        bin_dir / "node",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            printf '%s\n' "$@" >> "$INVOCATIONS_FILE"
            printf 'discarded diagnostic\n' >&2
            printf 'line one\nline two\n%s\n' "$STUB_PROBE_SUMMARY"
            exit "$STUB_PROBE_EXIT"
            """
        ),
    )
    env = {
        **os.environ,
        "INVOCATIONS_FILE": str(invocations),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PREFLIGHT_FUNCTIONS": str(definitions),
        "STUB_PROBE_EXIT": str(probe_exit),
        "STUB_PROBE_SUMMARY": probe_summary,
    }

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$PREFLIGHT_FUNCTIONS"; run_offline_redteam_probe',
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_exit
    assert result.stdout == expected_output
    assert result.stderr == ""
    assert invocations.read_text(encoding="utf-8").splitlines() == [
        "tools/offline_redteam_probe.mjs"
    ]


def test_preflight_persona_gate_handles_immediate_success_repeatedly(
    tmp_path: Path,
) -> None:
    for attempt in range(20):
        attempt_path = tmp_path / str(attempt)
        attempt_path.mkdir()
        result, _, _, _ = _run_persona_gate(
            attempt_path,
            runner_exit=0,
        )
        assert result.returncode == 0, result.stdout + result.stderr


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


def test_preflight_persona_gate_terminates_curl_at_readiness_deadline(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    result, curl_calls, runner_arguments, _ = _run_persona_gate(
        tmp_path,
        runner_exit=0,
        curl_sleep_seconds=30,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1, result.stdout + result.stderr
    assert 4.5 <= elapsed <= 5.75
    assert curl_calls
    assert all(
        float(call[call.index("--max-time") + 1]) <= 0.75 for call in curl_calls
    )
    assert not runner_arguments
    server_pid = int((tmp_path / "server.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(server_pid, 0)
    curl_pid = int((tmp_path / "curl.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(curl_pid, 0)


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
