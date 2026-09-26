"""Wrangler OAuth bearer support on the Day Zero runbook path.

The operator supplies the wrangler OAuth access token through a helper that
prints only the bearer (ask 2026-09-07 dayzero-token-path). Those bearers are
RFC 6750 ``b64token`` values that contain ``.`` and are roughly 90-100
characters long, so the stdin reader and inspector must accept that grammar
while still refusing anything that could split or inject into a header.
All tokens below are synthetic fixtures.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import day_zero_migration as migration
import prod_release_executor as release
from test_day_zero_migration import (
    CF_ACCOUNT,
    PAGES_URL,
    SHA_NEW,
    WORKER_URL,
    FakeHTTPSReader,
    inspector_responses,
    pages_payload,
    worker_payload,
)

# Synthetic OAuth-shaped bearer: dotted segments, '_' and '-', 95 characters.
OAUTH_BEARER = ("synthOAuth." + "AbC-dEf_GhI" * 4 + "." + "jKl_MnO-pQr" * 3
                + ".sTu")
# Every RFC 6750 b64token character class, plus trailing '=' padding.
B64TOKEN_BEARER = "Az09-._~+/" * 3 + "tail=="


def test_synthetic_oauth_bearer_is_realistic():
    assert 90 <= len(OAUTH_BEARER) <= 100
    assert OAUTH_BEARER.count(".") >= 3


def _protected_file(tmp_path, content):
    credential = tmp_path / "synthetic-bearer"
    credential.write_text(content, encoding="utf-8")
    credential.chmod(0o600)
    return credential


@pytest.mark.parametrize("bearer", [OAUTH_BEARER, B64TOKEN_BEARER])
def test_stdin_reader_accepts_oauth_bearer_from_protected_file(tmp_path, bearer):
    credential = _protected_file(tmp_path, bearer + "\n")
    with credential.open(encoding="utf-8") as stream:
        assert migration.read_cloudflare_token(stream) == bearer


def test_stdin_reader_accepts_oauth_bearer_from_owner_held_pipe():
    read_descriptor, write_descriptor = os.pipe()
    try:
        os.write(write_descriptor, (OAUTH_BEARER + "\n").encode("utf-8"))
    finally:
        os.close(write_descriptor)
    with os.fdopen(read_descriptor, encoding="utf-8") as stream:
        assert migration.read_cloudflare_token(stream) == OAUTH_BEARER


@pytest.mark.parametrize(
    "content",
    [
        "",
        "\n",
        "short.token",                              # below the 20-char floor
        "x" * 513,                                  # above the 512-char ceiling
        OAUTH_BEARER + "\n" + OAUTH_BEARER,          # a second line
        OAUTH_BEARER + "\r\nX-Injected: 1",         # header injection (CRLF)
        OAUTH_BEARER + "\rX-Injected: 1",           # bare CR
        "Bearer " + OAUTH_BEARER,                   # embedded space
        OAUTH_BEARER[:40] + "\t" + OAUTH_BEARER[40:],
        OAUTH_BEARER[:40] + "\x00" + OAUTH_BEARER[40:],
        OAUTH_BEARER[:40] + "\x7f" + OAUTH_BEARER[40:],
        OAUTH_BEARER[:40] + "é" + OAUTH_BEARER[40:],   # non-ASCII
        OAUTH_BEARER[:40] + "．" + OAUTH_BEARER[40:],   # fullwidth dot
        OAUTH_BEARER[:40] + "٠" + OAUTH_BEARER[40:],   # non-ASCII digit
        OAUTH_BEARER[:40] + " " + OAUTH_BEARER[40:],   # line separator
        OAUTH_BEARER + ";x",
        OAUTH_BEARER + ",x",
        '"' + OAUTH_BEARER + '"',
        OAUTH_BEARER + ":x",
        OAUTH_BEARER + "<",
        "=" + OAUTH_BEARER,                         # padding only at the end
        OAUTH_BEARER + "=x",                        # padding then more text
        "=" * 30,                                   # padding with no body
    ],
)
def test_stdin_reader_still_rejects_malformed_or_header_unsafe_input(tmp_path, content):
    credential = _protected_file(tmp_path, content)
    with credential.open(encoding="utf-8") as stream:
        with pytest.raises(migration.MigrationError, match="malformed") as failure:
            migration.read_cloudflare_token(stream)
    assert OAUTH_BEARER not in str(failure.value)


@pytest.mark.parametrize(
    "token",
    ["Bearer " + OAUTH_BEARER, OAUTH_BEARER + "\r\nX: y", OAUTH_BEARER + "\n", "", None],
)
def test_inspector_constructor_rejects_header_unsafe_bearer(token):
    with pytest.raises(migration.MigrationError, match="read token"):
        migration.CloudflarePairInspector(CF_ACCOUNT, "pages", "worker", token)


def _oauth_inspector(responses):
    reader = FakeHTTPSReader(responses)
    return migration.CloudflarePairInspector(
        CF_ACCOUNT, "legal-practicum", "sonsteng-chat", OAUTH_BEARER, reader=reader,
    ), reader


def test_inspector_sends_oauth_bearer_only_to_cloudflare_api():
    inspector, reader = _oauth_inspector(inspector_responses())
    pair = inspector.inspect(PAGES_URL, WORKER_URL)
    assert pair.sha == SHA_NEW
    sent = [request for request, _timeout in reader.requests]
    api = [r for r in sent if r.full_url.startswith(migration.CLOUDFLARE_API_ORIGIN)]
    live = [r for r in sent if r not in api]
    assert api and live
    assert all(r.get_header("Authorization") == "Bearer " + OAUTH_BEARER for r in api)
    assert all(r.get_header("Authorization") is None for r in live)


def test_inspector_failure_does_not_reflect_oauth_bearer():
    inspector, _reader = _oauth_inspector([TimeoutError("token=" + OAUTH_BEARER)])
    with pytest.raises(migration.MigrationError, match="Pages provider request failed") as failure:
        inspector.inspect(PAGES_URL, WORKER_URL)
    assert OAUTH_BEARER not in str(failure.value)
    assert failure.value.__cause__ is None


def test_cli_print_recovery_ids_runs_with_oauth_bearer_without_reflecting_it(
        monkeypatch, tmp_path, capsys):
    credential = _protected_file(tmp_path, OAUTH_BEARER + "\n")
    reader = FakeHTTPSReader(inspector_responses(
        pages_a=pages_payload("pages-exact-recovery-id", provider_body="do-not-print"),
        worker_a=worker_payload(deployment_id="worker-deployment-do-not-print"),
    ))
    real_inspector = migration.CloudflarePairInspector
    seen_argv = []

    def build(account, project, script, token):
        seen_argv.extend(sys.argv)
        return real_inspector(account, project, script, token, reader=reader)

    monkeypatch.setattr(migration, "CloudflarePairInspector", build)
    monkeypatch.setenv("SONSTENG_DAY_ZERO_MIGRATION_ENABLED", "true")
    with credential.open(encoding="utf-8") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        code = migration.main([
            "--inspect-cloudflare-pair",
            "--print-recovery-ids",
            "--cloudflare-account-id", CF_ACCOUNT,
            "--pages-project", "legal-practicum",
            "--worker-script", "sonsteng-chat",
            "--pages-provenance-url", PAGES_URL,
            "--worker-provenance-url", WORKER_URL,
            "--ack-john-notified",
            "--ack-queue-empty",
        ])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert json.loads(captured.out)["pages_deployment_id"] == "pages-exact-recovery-id"
    assert OAUTH_BEARER not in captured.out + captured.err
    for segment in OAUTH_BEARER.split("."):
        assert segment not in captured.out + captured.err
    assert all(OAUTH_BEARER not in arg for arg in seen_argv)
    assert "do-not-print" not in captured.out + captured.err
    assert any(r.get_header("Authorization") == "Bearer " + OAUTH_BEARER
               for r, _timeout in reader.requests)


def test_cli_rejected_oauth_shaped_stdin_does_not_reflect_it(monkeypatch, tmp_path, capsys):
    credential = _protected_file(tmp_path, "Bearer " + OAUTH_BEARER + "\n")
    monkeypatch.setattr(migration, "CloudflarePairInspector",
                        lambda *_a, **_k: pytest.fail("inspector must not be built"))
    with credential.open(encoding="utf-8") as stream:
        monkeypatch.setattr(sys, "stdin", stream)
        code = migration.main([
            "--inspect-cloudflare-pair",
            "--cloudflare-account-id", CF_ACCOUNT,
            "--pages-project", "legal-practicum",
            "--worker-script", "sonsteng-chat",
            "--pages-provenance-url", PAGES_URL,
            "--worker-provenance-url", WORKER_URL,
        ])
    captured = capsys.readouterr()
    assert code != 0
    assert "malformed" in captured.err
    assert OAUTH_BEARER not in captured.out + captured.err


def test_pages_rollback_adapter_carries_oauth_bearer_unchanged(tmp_path):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({"success": True, "result": {"id": "pagesdeploy123"}}).encode()

    def opener(request, timeout):
        requests.append(request)
        return Response()

    adapter = release.WranglerPagesAdapter(
        "sonsteng", str(tmp_path / "site"), "https://legalpracticum.org/",
        candidate_root=tmp_path, account_id="0" * 32, api_token=OAUTH_BEARER, opener=opener)
    adapter.restore("pagesdeploy123")
    assert len(requests) == 1
    assert requests[0].get_header("Authorization") == "Bearer " + OAUTH_BEARER
    assert requests[0].full_url.startswith("https://api.cloudflare.com/client/v4/accounts/")
    assert OAUTH_BEARER not in requests[0].full_url
