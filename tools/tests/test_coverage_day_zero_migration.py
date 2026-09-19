"""Offline migration boundary, protected-channel, and unwind coverage."""
import io
import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero_migration as migration
from test_day_zero_migration import FakePhases, FakeProduction, NEW, PRIOR, SHA_NEW, request


@pytest.mark.parametrize("content", ["", "bad token with spaces", "x" * 514, "\n", "x\ny"])
def test_real_protected_input_rejects_malformed_token(tmp_path, content):
    path = tmp_path / "synthetic-input.txt"
    path.write_text(content)
    path.chmod(0o600)
    with path.open() as stream, pytest.raises(migration.MigrationError, match="malformed"):
        migration.read_cloudflare_token(stream)


def test_real_protected_input_decodes_one_token_and_trims_newline(tmp_path):
    path = tmp_path / "synthetic-input.txt"
    path.write_text("test-token-1234567890\n")
    path.chmod(0o600)
    with path.open() as stream:
        assert migration.read_cloudflare_token(stream) == "test-token-1234567890"


def test_binary_protected_stream_rejected_without_exposing_contents(tmp_path):
    path = tmp_path / "synthetic-input.txt"
    path.write_bytes(b"test-token-1234567890")
    path.chmod(0o600)
    with path.open("rb") as stream, pytest.raises(migration.MigrationError, match="malformed"):
        migration.read_cloudflare_token(stream)


def test_unicode_read_failure_has_bounded_error(tmp_path):
    path = tmp_path / "synthetic-input.txt"
    path.write_bytes(b"\xff")
    path.chmod(0o600)
    with path.open(encoding="utf-8") as stream, pytest.raises(
        migration.MigrationError, match="could not be read"
    ):
        migration.read_cloudflare_token(stream)


@pytest.mark.parametrize("stream", [object(), io.StringIO("not-a-protected-channel")])
def test_non_descriptor_input_is_not_a_protected_channel(stream):
    with pytest.raises(migration.MigrationError, match="not a protected channel"):
        migration.read_cloudflare_token(stream)


def test_closed_descriptor_is_not_a_protected_channel(tmp_path):
    path = tmp_path / "synthetic-input.txt"
    path.write_text("fixture")
    descriptor = os.open(path, os.O_RDONLY)
    os.close(descriptor)
    class ClosedDescriptor:
        def fileno(self):
            return descriptor
    with pytest.raises(migration.MigrationError, match="not a protected channel"):
        migration.read_cloudflare_token(ClosedDescriptor())


def test_input_isatty_failure_is_redacted(tmp_path):
    path = tmp_path / "synthetic-input.txt"
    path.write_text("fixture")
    with path.open() as stream:
        class Uninspectable:
            def fileno(self):
                return stream.fileno()
            def isatty(self):
                raise OSError("private terminal details")
        with pytest.raises(migration.MigrationError, match="not a protected channel") as exc:
            migration.read_cloudflare_token(Uninspectable())
    assert "private" not in str(exc.value)


def test_interactive_input_rejected_before_read(tmp_path):
    path = tmp_path / "synthetic-input.txt"
    path.write_text("fixture")
    with path.open() as stream:
        class Interactive:
            def fileno(self):
                return stream.fileno()
            def isatty(self):
                return True
        with pytest.raises(migration.MigrationError, match="must not be interactive"):
            migration.read_cloudflare_token(Interactive())


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_signal_guard_interrupts_and_restores_all_original_handlers(signum):
    monitored = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    originals = {number: signal.getsignal(number) for number in monitored}
    with pytest.raises(migration.MigrationInterrupted, match=f"signal {signum}"):
        with migration.signal_unwind_guard():
            signal.getsignal(signum)(signum, None)
    assert {number: signal.getsignal(number) for number in monitored} == originals


def test_nested_signal_guard_restores_outer_then_process_handlers():
    original = signal.getsignal(signal.SIGTERM)
    with migration.signal_unwind_guard():
        outer = signal.getsignal(signal.SIGTERM)
        with migration.signal_unwind_guard():
            assert signal.getsignal(signal.SIGTERM) is not outer
        assert signal.getsignal(signal.SIGTERM) is outer
    assert signal.getsignal(signal.SIGTERM) is original


def test_operator_boundary_preserves_catchable_migration_interrupt():
    interruption = migration.MigrationInterrupted("cancel")
    def action():
        raise interruption
    with pytest.raises(migration.MigrationInterrupted) as exc:
        migration._safe_operator_call(action, "bounded failure")
    assert exc.value is interruption


@pytest.mark.parametrize("relative", [True, False])
def test_recovery_registry_rejects_relative_or_real_symlink(tmp_path, relative):
    registry = Path("relative.json")
    if not relative:
        target = tmp_path / "target.json"
        target.write_text("[]")
        registry = tmp_path / "registry.json"
        registry.symlink_to(target)
    with pytest.raises(migration.MigrationError, match="absolute, non-symlink"):
        migration._validate_request(request(tmp_path, recovery_registry=registry))


@pytest.mark.parametrize("path", ["/accounts/example?bad=1", "/accounts/example#fragment"])
def test_provider_api_rejects_query_or_fragment_before_transport(path):
    with pytest.raises(migration.MigrationError, match="not allowlisted"):
        migration._cloudflare_api_url(path)


def test_live_provenance_invalid_port_is_bounded():
    with pytest.raises(migration.MigrationError, match="pages provenance URL is invalid"):
        migration._live_provenance_url("https://example.test:invalid/path", "pages")


@pytest.mark.parametrize("function", [migration.rehearse, migration.verify_materialized])
@pytest.mark.parametrize("sha", ["B" * 40, "b" * 39, "HEAD", ""])
def test_invalid_candidate_is_rejected_before_copy(function, sha, tmp_path):
    with pytest.raises(migration.MigrationError, match="exact lowercase"):
        function(tmp_path, sha)


def test_non_checkout_is_rejected_before_isolation(tmp_path):
    with pytest.raises(migration.MigrationError, match="not a Git checkout"):
        with migration.isolated_git_copy(tmp_path, SHA_NEW):
            pytest.fail("invalid checkout must not yield")


def test_unknown_phase_has_start_marker_but_no_command(tmp_path, capsys):
    runner = migration.LocalRehearsalPhases(tmp_path)
    with pytest.raises(migration.MigrationError, match="unknown migration phase"):
        runner.run("unrecognized", SHA_NEW)
    assert "rehearsal-phase:start:unrecognized" in capsys.readouterr().err


@pytest.mark.parametrize("args,message", [
    (["--inspect-cloudflare-pair", "--execute"], "cannot be combined"),
    (["--inspect-cloudflare-pair"], "requires all non-secret coordinates"),
])
def test_cli_rejects_incomplete_modes_before_any_network(args, message, capsys):
    assert migration.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_timer_stop_readback_failure_never_enters_mutation_window(tmp_path):
    class DidNotStop(FakeProduction):
        def stop_timer(self, name):
            self.calls.append("stop-attempt:" + name)
    production = DidNotStop()
    with pytest.raises(migration.MigrationError, match="did not stop and disable"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert "window-enter" not in production.calls
    assert production.production_calls == 0
    assert production.states[migration.APPLY_TIMER] == migration.TimerState(True, True)


def test_timer_restoration_readback_failure_overrides_success(tmp_path):
    class DidNotRestore(FakeProduction):
        def restore_timer(self, name, state):
            self.calls.append("restore-attempt:" + name)
    production = DidNotRestore()
    with pytest.raises(migration.MigrationError, match="prior apply timer policy restored"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert production.live == NEW
    assert "lock-exit" in production.calls


def test_candidate_deploy_must_return_valid_exact_pair(tmp_path):
    production = FakeProduction(deployed=migration.ProductionPair(SHA_NEW, "", "worker-new"))
    with pytest.raises(migration.MigrationError, match="exact provider pair"):
        migration.execute(request(tmp_path), FakePhases(), production)
    assert production.live == PRIOR
    assert production.canonical_sha == PRIOR.sha
    assert "lock-exit" in production.calls


def test_world_readable_pipe_is_rejected_before_reading():
    read_fd, write_fd = os.pipe()
    try:
        os.fchmod(read_fd, 0o640)
        with os.fdopen(read_fd, "r") as stream:
            read_fd = None
            with pytest.raises(migration.MigrationError, match="pipe is not owner-held"):
                migration.read_cloudflare_token(stream)
    finally:
        if read_fd is not None:
            os.close(read_fd)
        os.close(write_fd)


def test_character_device_is_not_a_protected_token_channel():
    with open(os.devnull) as stream, pytest.raises(
        migration.MigrationError, match="not a protected channel"
    ):
        migration.read_cloudflare_token(stream)


@pytest.mark.parametrize("release_headers", [[], ["a" * 40], ["a" * 40, "b" * 40]])
def test_default_https_reader_preserves_duplicate_provenance_headers(monkeypatch, release_headers):
    from email.message import Message
    headers = Message()
    headers.add_header("Content-Type", "application/json")
    for value in release_headers:
        headers.add_header("x-release-sha", value)
    calls = []
    class Response:
        def __init__(self):
            self.headers = headers
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append("closed")
        def getcode(self):
            return 200
        def read(self, maximum):
            assert maximum == migration.MAX_HTTP_BODY_BYTES + 1
            return b'{"ok": true}'
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response()
    def build_opener(handler):
        assert handler is migration._NoRedirect
        return Opener()
    monkeypatch.setattr(migration.urllib.request, "build_opener", build_opener)
    request_object = migration.urllib.request.Request("https://example.test/provenance")
    result = migration._default_https_reader(request_object, 3)
    assert result.status == 200
    assert result.body == b'{"ok": true}'
    assert result.headers.get("x-release-sha") == (
        ",".join(release_headers) if release_headers else None
    )
    assert calls == [("https://example.test/provenance", 3), "closed"]


def test_default_https_reader_closes_oversized_response(monkeypatch):
    closed = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(True)
        def read(self, maximum):
            return b"x" * maximum
    class Opener:
        def open(self, request, timeout):
            return Response()
    monkeypatch.setattr(migration.urllib.request, "build_opener", lambda handler: Opener())
    with pytest.raises(ValueError, match="response too large"):
        migration._default_https_reader(migration.urllib.request.Request("https://example.test"), 1)
    assert closed == [True]
