from __future__ import annotations

import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tools" / "verify_bot_gate.js"


def sockets_available() -> bool:
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def test_help_exits_zero_and_documents_worker_argument() -> None:
    result = subprocess.run(
        ["node", str(PROBE), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--worker" in result.stdout


@pytest.mark.skipif(not sockets_available(), reason="localhost sockets are unavailable")
def test_probe_requires_all_three_requests_to_return_typed_403() -> None:
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append((self.path, self.headers.get("Origin")))
            body = json.dumps({"error": {"code": "turnstile_failed", "message": "Try again."}}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            ["node", str(PROBE), "--worker", f"http://127.0.0.1:{server.server_port}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    if not seen and "request failed: fetch failed" in result.stdout:
        pytest.skip("Node localhost sockets are unavailable")
    assert result.returncode == 0, result.stderr
    assert [path for path, _ in seen] == [
        "/v1/session",
        "/v1/session?cf_ts=invalid-token-value",
        "/v1/session?bypass=not-a-real-bypass",
    ]
    assert all(origin == "https://legalpracticum.org" for _, origin in seen)
    assert "3/3" in result.stdout
