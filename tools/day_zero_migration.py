#!/usr/bin/env python3
"""Materialize, verify, and model the one-off U15 Day Zero migration safely.

The default CLI rehearses the one-time rewrite in a disposable exact-commit
clone. ``verify_materialized`` separately proves an already committed candidate
without rerunning that governed write. The production state machine is fully
dependency-injected and tested, but no CLI production adapter exists: U15
remains a supervised act behind the Publisher release authority.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator


APPLY_TIMER = "sonsteng-apply.timer"
RELEASE_TIMER = "sonsteng-prod-release.timer"
MATERIALIZATION_PHASES = (
    "governed-verification",
    "governed-write",
    "generated-build",
    "build-parity",
    "strict-day-zero-enforcement",
    "preflight",
)
VERIFY_ONLY_PHASES = (
    "candidate-commit",
    "governed-verification",
    "generated-build",
    "generated-artifact-cleanliness",
    "build-parity",
    "strict-day-zero-enforcement",
    "preflight",
    "final-tree-cleanliness",
)
# Compatibility for callers which treat the write-bearing rehearsal as the
# migration phase list. Production execution deliberately uses VERIFY_ONLY_PHASES.
MIGRATION_PHASES = MATERIALIZATION_PHASES
REQUIRED_CHANGE_WINDOW_ACTORS = (
    "canonical-writers",
    "canonical-merges",
    "apply-daemon",
    "production-release-daemon",
    "direct-deployments",
    "provider-deployment-actors",
)
SHA_RE = re.compile(r"[0-9a-f]{40}")
PROVIDER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
CLOUDFLARE_ACCOUNT_ID_RE = re.compile(r"[0-9a-f]{32}")
CLOUDFLARE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
CLOUDFLARE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,512}")
CLOUDFLARE_API_ORIGIN = "https://api.cloudflare.com"
CLOUDFLARE_API_PREFIX = "/client/v4"
MAX_HTTP_BODY_BYTES = 1_048_576
HTTP_TIMEOUT_SECONDS = 20
EX_CONFIG = 78


class MigrationError(RuntimeError):
    """A bounded migration failure safe to print to an operator."""


class MigrationInterrupted(MigrationError):
    """Raised for SIGINT/SIGTERM so normal unwind logic runs."""


class MigrationFenced(MigrationError):
    """A control boundary could not close, so automatic unfreezing is unsafe."""


class BoundedArgumentParser(argparse.ArgumentParser):
    """Never reflect rejected command-line values into an error."""

    def error(self, _message):
        raise MigrationError("invalid command arguments")


@dataclasses.dataclass(frozen=True)
class TimerState:
    enabled: bool
    active: bool


@dataclasses.dataclass(frozen=True)
class ProductionPair:
    sha: str
    pages_deployment_id: str
    worker_version_id: str

    def redacted(self) -> dict[str, str]:
        return {
            "sha": self.sha,
            "pages_id_digest": _id_digest(self.pages_deployment_id),
            "worker_id_digest": _id_digest(self.worker_version_id),
        }


@dataclasses.dataclass(frozen=True)
class HTTPSResponse:
    """Allowlisted response data returned by an injected HTTPS reader."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclasses.dataclass(frozen=True)
class CloudflareControlState:
    """The provider coordinates whose stability is proved around live reads."""

    pages_deployment_id: str
    worker_deployment_id: str
    worker_allocations: tuple[tuple[str, float], ...]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _default_https_reader(
    request: urllib.request.Request,
    timeout: int,
) -> HTTPSResponse:
    """Perform one redirect-disabled request and return a bounded response."""
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        body = response.read(MAX_HTTP_BODY_BYTES + 1)
        if len(body) > MAX_HTTP_BODY_BYTES:
            raise ValueError("response too large")
        headers = {str(key): str(value) for key, value in response.headers.items()}
        release_values = response.headers.get_all("x-release-sha") or []
        if len(release_values) > 1:
            headers["x-release-sha"] = ",".join(str(value) for value in release_values)
        return HTTPSResponse(
            status=int(response.getcode()),
            headers=headers,
            body=body,
        )


def _cloudflare_api_url(path: str) -> str:
    """Build a token-safe URL from one internal, absolute API path."""
    url = f"{CLOUDFLARE_API_ORIGIN}{CLOUDFLARE_API_PREFIX}{path}"
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.cloudflare.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MigrationError("Cloudflare provider endpoint is not allowlisted")
    return url


def _live_provenance_url(value: str, surface: str) -> str:
    parsed = urllib.parse.urlsplit(value or "")
    try:
        port = parsed.port
    except ValueError:
        raise MigrationError(f"{surface} provenance URL is invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise MigrationError(f"{surface} provenance URL is invalid")
    return value


def read_cloudflare_token(stdin) -> str:
    """Read one API token from a pipe or a mode-0600 regular stdin file."""
    try:
        descriptor = stdin.fileno()
        metadata = os.fstat(descriptor)
    except (AttributeError, OSError):
        raise MigrationError("Cloudflare token stdin is not a protected channel") from None
    try:
        interactive = stdin.isatty()
    except (AttributeError, OSError):
        raise MigrationError("Cloudflare token stdin is not a protected channel") from None
    if interactive:
        raise MigrationError("Cloudflare token stdin must not be interactive")
    is_regular = stat.S_ISREG(metadata.st_mode)
    is_pipe = stat.S_ISFIFO(metadata.st_mode)
    permissions = stat.S_IMODE(metadata.st_mode)
    if not (is_regular or is_pipe):
        raise MigrationError("Cloudflare token stdin is not a protected channel")
    if metadata.st_uid != os.geteuid():
        raise MigrationError("Cloudflare token stdin is not owner-held")
    if is_regular and permissions != 0o600:
        raise MigrationError("Cloudflare token stdin file must be owner-held mode 0600")
    if is_pipe and (permissions & 0o077):
        raise MigrationError("Cloudflare token stdin pipe is not owner-held")
    try:
        value = stdin.read(514)
    except (OSError, UnicodeError):
        raise MigrationError("Cloudflare token could not be read from stdin") from None
    if not isinstance(value, str):
        raise MigrationError("Cloudflare token stdin is malformed")
    token = value.strip()
    if len(value) > 513 or not CLOUDFLARE_TOKEN_RE.fullmatch(token):
        raise MigrationError("Cloudflare token stdin is malformed")
    return token


class CloudflarePairInspector:
    """Read and prove the exact active Pages/Worker production pair."""

    def __init__(
        self,
        account_id: str,
        pages_project: str,
        worker_script: str,
        token: str,
        *,
        reader: Callable[[urllib.request.Request, int], HTTPSResponse] = _default_https_reader,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ):
        if not CLOUDFLARE_ACCOUNT_ID_RE.fullmatch(account_id or ""):
            raise MigrationError("Cloudflare account identifier is invalid")
        if not CLOUDFLARE_NAME_RE.fullmatch(pages_project or ""):
            raise MigrationError("Cloudflare Pages project name is invalid")
        if not CLOUDFLARE_NAME_RE.fullmatch(worker_script or ""):
            raise MigrationError("Cloudflare Worker script name is invalid")
        if not CLOUDFLARE_TOKEN_RE.fullmatch(token or ""):
            raise MigrationError("Cloudflare read token is invalid")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 30:
            raise MigrationError("Cloudflare inspection timeout is invalid")
        quoted_account = urllib.parse.quote(account_id, safe="")
        self._pages_url = _cloudflare_api_url(
            f"/accounts/{quoted_account}/pages/projects/"
            f"{urllib.parse.quote(pages_project, safe='')}"
        )
        self._worker_url = _cloudflare_api_url(
            f"/accounts/{quoted_account}/workers/scripts/"
            f"{urllib.parse.quote(worker_script, safe='')}/deployments"
        )
        self._token = token
        self._reader = reader
        self._timeout = timeout

    def _read(self, request: urllib.request.Request, category: str) -> HTTPSResponse:
        parsed = urllib.parse.urlsplit(request.full_url)
        bearer = request.get_header("Authorization")
        if request.get_method() != "GET":
            raise MigrationError(f"{category} request was not read-only")
        if bearer is not None and (
            parsed.scheme != "https"
            or parsed.hostname != "api.cloudflare.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise MigrationError("Cloudflare bearer target was not allowlisted")
        try:
            response = self._reader(request, self._timeout)
        except Exception:
            raise MigrationError(f"{category} request failed") from None
        if (
            not isinstance(response, HTTPSResponse)
            or response.status != 200
            or not isinstance(response.headers, dict)
            or not isinstance(response.body, bytes)
            or len(response.body) > MAX_HTTP_BODY_BYTES
        ):
            raise MigrationError(f"{category} request failed")
        return response

    def _provider_json(self, url: str, category: str) -> dict:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.cloudflare.com"
            or parsed.port not in (None, 443)
        ):
            raise MigrationError("Cloudflare provider endpoint is not allowlisted")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "sonsteng-read-only-pair-inspector/1",
            },
        )
        response = self._read(request, category)
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            raise MigrationError(f"{category} response was invalid") from None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise MigrationError(f"{category} response was invalid")
        return payload

    @staticmethod
    def _pages_id(payload: dict) -> str:
        result = payload.get("result")
        canonical = result.get("canonical_deployment") if isinstance(result, dict) else None
        stage = canonical.get("latest_stage") if isinstance(canonical, dict) else None
        deployment_id = canonical.get("id") if isinstance(canonical, dict) else None
        if (
            not isinstance(canonical, dict)
            or not isinstance(deployment_id, str)
            or not PROVIDER_ID_RE.fullmatch(deployment_id)
            or canonical.get("environment") != "production"
            or canonical.get("is_skipped") is not False
            or not isinstance(stage, dict)
            or stage.get("status") != "success"
        ):
            raise MigrationError("Pages canonical deployment was invalid")
        return deployment_id

    @staticmethod
    def _worker_state(payload: dict) -> tuple[str, tuple[tuple[str, float], ...]]:
        result = payload.get("result")
        deployments = result.get("deployments") if isinstance(result, dict) else None
        active = deployments[0] if isinstance(deployments, list) and deployments else None
        deployment_id = active.get("id") if isinstance(active, dict) else None
        allocations = active.get("versions") if isinstance(active, dict) else None
        if (
            not isinstance(deployment_id, str)
            or not PROVIDER_ID_RE.fullmatch(deployment_id)
            or not isinstance(allocations, list)
            or len(allocations) != 1
        ):
            raise MigrationError("Worker active deployment was ambiguous")
        allocation = allocations[0]
        version_id = allocation.get("version_id") if isinstance(allocation, dict) else None
        percentage = allocation.get("percentage") if isinstance(allocation, dict) else None
        if (
            not isinstance(version_id, str)
            or not PROVIDER_ID_RE.fullmatch(version_id)
            or isinstance(percentage, bool)
            or not isinstance(percentage, (int, float))
            or not float(percentage) == 100.0
        ):
            raise MigrationError("Worker active deployment was ambiguous")
        return deployment_id, ((version_id, float(percentage)),)

    def _control_state(self) -> CloudflareControlState:
        pages_id = self._pages_id(self._provider_json(self._pages_url, "Pages provider"))
        worker_deployment_id, allocations = self._worker_state(
            self._provider_json(self._worker_url, "Worker provider")
        )
        return CloudflareControlState(pages_id, worker_deployment_id, allocations)

    def _live_sha(self, url: str, surface: str) -> str:
        request = urllib.request.Request(
            _live_provenance_url(url, surface),
            method="GET",
            headers={"Accept": "*/*", "User-Agent": "sonsteng-read-only-pair-inspector/1"},
        )
        response = self._read(request, f"{surface} provenance")
        values = [value for name, value in response.headers.items()
                  if isinstance(name, str) and name.lower() == "x-release-sha"]
        if (
            len(values) != 1
            or not isinstance(values[0], str)
            or not SHA_RE.fullmatch(values[0])
        ):
            raise MigrationError(f"{surface} provenance was invalid")
        return values[0]

    def inspect(self, pages_provenance_url: str, worker_provenance_url: str) -> ProductionPair:
        """Prove stable provider coordinates around matching live SHA reads."""
        pages_provenance_url = _live_provenance_url(pages_provenance_url, "Pages")
        worker_provenance_url = _live_provenance_url(worker_provenance_url, "Worker")
        state_before = self._control_state()
        pages_sha = self._live_sha(pages_provenance_url, "Pages")
        worker_sha = self._live_sha(worker_provenance_url, "Worker")
        if pages_sha != worker_sha:
            raise MigrationError("live provenance SHAs did not match")
        state_after = self._control_state()
        if state_before != state_after:
            raise MigrationError("Cloudflare provider state changed during inspection")
        return ProductionPair(
            pages_sha,
            state_after.pages_deployment_id,
            state_after.worker_allocations[0][0],
        )


@dataclasses.dataclass(frozen=True)
class MigrationRequest:
    candidate_sha: str
    prior_pair: ProductionPair
    recovery_registry: pathlib.Path
    john_notified: bool = False
    queue_empty_acknowledged: bool = False
    enabled: bool = False
    normal_release_config_off: bool = True


def _id_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _valid_pair(pair: ProductionPair) -> bool:
    return bool(
        SHA_RE.fullmatch(pair.sha or "")
        and PROVIDER_ID_RE.fullmatch(pair.pages_deployment_id or "")
        and PROVIDER_ID_RE.fullmatch(pair.worker_version_id or "")
    )


def _validate_request(request: MigrationRequest) -> None:
    if not request.enabled:
        raise MigrationError("production Day Zero migration is disabled by default")
    if not request.normal_release_config_off:
        raise MigrationError("normal production release must remain config-off")
    if not request.john_notified:
        raise MigrationError("explicit John-notified acknowledgement is required")
    if not request.queue_empty_acknowledged:
        raise MigrationError("explicit queue-empty acknowledgement is required")
    if not SHA_RE.fullmatch(request.candidate_sha or ""):
        raise MigrationError("an exact lowercase candidate SHA is required")
    if not _valid_pair(request.prior_pair):
        raise MigrationError("an exact prior pair with SHA and provider IDs is required")
    registry = pathlib.Path(request.recovery_registry)
    if not registry.is_absolute() or registry.is_symlink():
        raise MigrationError("recovery registry must be an absolute, non-symlink path")


def _safe_operator_call(action: Callable, failure: str):
    """Discard provider/host exception text at the operational boundary."""
    try:
        return action()
    except MigrationInterrupted:
        raise
    except BaseException:
        raise MigrationError(failure) from None


@contextlib.contextmanager
def signal_unwind_guard() -> Iterator[None]:
    """Translate catchable termination signals into the normal unwind path."""
    previous = {}

    def interrupt(signum, _frame):
        raise MigrationInterrupted(f"migration interrupted by signal {signum}")

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _run_phases(phases, candidate_sha: str, *, context: str, phase_names) -> None:
    for phase in phase_names:
        try:
            phases.run(phase, candidate_sha)
        except MigrationInterrupted:
            raise
        except BaseException:
            raise MigrationError(f"{context} phase failed: {phase}") from None


@contextlib.contextmanager
def isolated_git_copy(repo: pathlib.Path, candidate_sha: str) -> Iterator[pathlib.Path]:
    """Check out one exact commit in a disposable, standalone local clone."""
    repo = pathlib.Path(repo).resolve()
    if not (repo / ".git").exists():
        raise MigrationError("rehearsal source is not a Git checkout")
    try:
        resolved_commit = subprocess.run(
            ["git", "rev-parse", "--verify", f"{candidate_sha}^{{commit}}"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise MigrationError("rehearsal candidate is not an exact commit object") from None
    if resolved_commit != candidate_sha:
        raise MigrationError("rehearsal candidate is not an exact commit object")
    try:
        with tempfile.TemporaryDirectory(prefix="sonsteng-day-zero-rehearsal-") as directory:
            target = pathlib.Path(directory) / "checkout"
            subprocess.run(
                [
                    "git", "clone", "--quiet", "--no-local", "--no-checkout",
                    str(repo), str(target),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["git", "fetch", "--quiet", "--no-tags", str(repo), resolved_commit],
                cwd=target,
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["git", "checkout", "--quiet", "--detach", resolved_commit],
                cwd=target,
                check=True,
                capture_output=True,
                timeout=120,
            )
            checked_out = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
            if checked_out != resolved_commit:
                raise MigrationError("isolated rehearsal checkout did not match the candidate")
            yield target
    except MigrationError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise MigrationError("could not materialize the isolated rehearsal copy") from None


class LocalRehearsalPhases:
    """Run bounded U15 gates in an isolated copy, suppressing authored output."""

    def __init__(self, checkout: pathlib.Path, timeout: int = 1800):
        self.checkout = pathlib.Path(checkout).resolve()
        self.timeout = timeout

    def _command(self, argv: list[str]) -> None:
        sensitive_markers = (
            "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "BEARER",
        )
        environment = {
            key: value for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in sensitive_markers)
        }
        environment.update({
            "HEADLESS": "1",
            "EDITOR_HEADLESS": "1",
            "SONSTENG_PROD_RELEASE_ENABLED": "false",
            "SONSTENG_DAY_ZERO_MIGRATION_ENABLED": "false",
        })
        try:
            subprocess.run(
                argv,
                cwd=self.checkout,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            raise MigrationError("bounded rehearsal command failed") from None

    def _assert_exact_clean_tree(self, candidate_sha: str) -> None:
        from prod_release_executor import GitRefAdapter, ReleaseError

        try:
            GitRefAdapter(self.checkout, timeout=30).require_clean_candidate(candidate_sha)
        except ReleaseError as exc:
            if str(exc) == "candidate checkout is not the clean frozen commit":
                raise MigrationError(
                    "candidate tree is dirty or differs from the exact commit"
                ) from None
            raise MigrationError("could not prove the exact committed candidate tree") from None
        except (OSError, subprocess.SubprocessError):
            raise MigrationError("could not prove the exact committed candidate tree") from None

    def run(self, phase: str, candidate_sha: str) -> None:
        print(f"rehearsal-phase:start:{phase}", file=sys.stderr, flush=True)
        if phase in {"candidate-commit", "generated-artifact-cleanliness",
                     "final-tree-cleanliness"}:
            self._assert_exact_clean_tree(candidate_sha)
        elif phase == "governed-verification":
            self._command(["python3", "tools/day_zero.py", "--repo", str(self.checkout)])
        elif phase == "governed-write":
            self._command(["python3", "tools/day_zero.py", "--repo", str(self.checkout), "--write"])
        elif phase == "generated-build":
            for argv in (
                ["python3", "tools/build_site.py", "--check"],
                ["python3", "tools/build_worker_personas.py"],
                ["python3", "tools/build_instructor_bundle.py"],
                ["python3", "tools/build_history.py"],
                ["node", "app/worker/scripts/bundle-editor-data.mjs"],
            ):
                self._command(argv)
        elif phase == "build-parity":
            self._command(["python3", "tools/check_build_parity.py"])
        elif phase == "strict-day-zero-enforcement":
            report = self.checkout / ".day-zero-migration-validation.json"
            try:
                self._command([
                    "python3", "tools/validate_spine.py", "--strict",
                    "--enforce-day-zero-offsets",
                    "--enforce-legal-practicum-identifiers",
                    "--quiet", "--json", str(report),
                ])
                payload = json.loads(report.read_text(encoding="utf-8"))
                totals = payload.get("totals") or {}
                if payload.get("day_zero_offset_enforcement") is not True or \
                   payload.get("identifier_base_enforcement") is not True or \
                   int(totals.get("checked_dates") or 0) <= 0 or \
                   int(totals.get("offset_dates_checked") or 0) <= 0 or \
                   int(totals.get("identifier_files_checked") or 0) <= 0 or \
                   int(totals.get("identifier_base_values_checked") or 0) <= 0 or \
                   int(totals.get("old_identifier_base_occurrences") or 0) != 0:
                    raise MigrationError(
                        "strict Day Zero gate did not execute date and identifier checks"
                    )
            except (OSError, ValueError, json.JSONDecodeError):
                raise MigrationError("strict Day Zero gate did not emit bounded evidence") from None
            finally:
                report.unlink(missing_ok=True)
        elif phase == "preflight":
            self._command(["bash", "tools/preflight.sh", "--no-browser"])
        else:
            raise MigrationError("unknown migration phase")


def rehearse(
    repo: pathlib.Path,
    candidate_sha: str,
    phases=None,
    *,
    isolated_copy: Callable = isolated_git_copy,
) -> dict:
    """Run every U15 phase in a disposable copy and make zero PROD calls."""
    if not SHA_RE.fullmatch(candidate_sha or ""):
        raise MigrationError("an exact lowercase candidate SHA is required")
    repo = pathlib.Path(repo).resolve()
    with isolated_copy(repo, candidate_sha) as checkout:
        runner = phases or LocalRehearsalPhases(checkout)
        _run_phases(
            runner, candidate_sha, context="rehearsal",
            phase_names=MATERIALIZATION_PHASES,
        )
    return {
        "mode": "rehearsal",
        "candidate_sha": candidate_sha,
        "phases": list(MATERIALIZATION_PHASES),
        "production_mutations": 0,
    }


def verify_materialized(
    repo: pathlib.Path,
    candidate_sha: str,
    phases=None,
    *,
    isolated_copy: Callable = isolated_git_copy,
) -> dict:
    """Verify an already committed migration candidate without governed writes."""
    if not SHA_RE.fullmatch(candidate_sha or ""):
        raise MigrationError("an exact lowercase candidate SHA is required")
    repo = pathlib.Path(repo).resolve()
    with isolated_copy(repo, candidate_sha) as checkout:
        runner = phases or LocalRehearsalPhases(checkout)
        _run_phases(
            runner, candidate_sha, context="verify-only",
            phase_names=VERIFY_ONLY_PHASES,
        )
    return {
        "mode": "verify-only",
        "candidate_sha": candidate_sha,
        "phases": list(VERIFY_ONLY_PHASES),
        "production_mutations": 0,
    }


def _assert_live_pair(production, expected: ProductionPair, failure: str) -> None:
    observed = _safe_operator_call(production.read_live_pair, failure)
    if observed != expected:
        raise MigrationError(failure)


class ChangeWindowCloseError(MigrationError):
    """The exclusive writer boundary failed while its daemon lock was held."""


def _prove_persistent_fence(production) -> None:
    """Require an affirmative adapter proof; stopped timers are not a proof."""
    try:
        proved = production.keep_change_window_fenced()
    except BaseException:
        raise MigrationFenced(
            "persistent fencing could not be proved; timers remain stopped"
        ) from None
    if proved is not True:
        raise MigrationFenced(
            "persistent fencing could not be proved; timers remain stopped"
        )


def _persistent_fence_error(production, proved_message: str) -> MigrationFenced:
    """Return the bounded fenced result, or the stronger unproved-fence error."""
    _prove_persistent_fence(production)
    return MigrationFenced(f"{proved_message}; persistent fence proved and timers remain stopped")


@contextlib.contextmanager
def _exclusive_change_window(production) -> Iterator[None]:
    """Enter the all-writer freeze without laundering body failures as entry failures."""
    try:
        manager = production.exclusive_change_window(REQUIRED_CHANGE_WINDOW_ACTORS)
        manager.__enter__()
    except MigrationInterrupted:
        raise
    except BaseException:
        raise _persistent_fence_error(
            production, "could not prove the exclusive change window",
        ) from None
    try:
        yield
    finally:
        failure = sys.exc_info()
        try:
            manager.__exit__(*failure)
        except BaseException:
            raise ChangeWindowCloseError(
                "exclusive change-window control boundary failed"
            ) from None


def _prove_complete_prior_state(production, request: MigrationRequest) -> None:
    proved = _safe_operator_call(
        lambda: production.prove_prior_state(request.prior_pair.sha, request.prior_pair),
        "complete prior state could not be proved",
    )
    if proved is not True:
        raise MigrationError("complete prior state could not be proved")


def _compensate_to_prior(production, request: MigrationRequest) -> bool:
    """Attempt every rollback surface; return true only for complete proof."""
    failed = False
    actions = (
        lambda: production.activate_pair(request.prior_pair),
        lambda: _assert_live_pair(
            production, request.prior_pair,
            "compensation prior provider-pair readback mismatch",
        ),
        lambda: _restore_canonical_ref_exact(production, request),
        lambda: production.deploy_editor_dev(request.prior_pair.sha),
        lambda: production.prove_all_surfaces(request.prior_pair.sha, request.prior_pair),
    )
    for action in actions:
        try:
            _safe_operator_call(action, "migration compensation step failed")
        except BaseException:
            failed = True
    return not failed


def _restore_canonical_ref_exact(production, request: MigrationRequest) -> None:
    observed = production.restore_canonical_ref_exact(
        request.candidate_sha, request.prior_pair.sha,
    )
    if observed != request.prior_pair.sha:
        raise MigrationError("canonical ref restoration readback mismatch")


def execute(request: MigrationRequest, phases, production) -> dict:
    """Execute the supervised algorithm through an injected production adapter.

    There is deliberately no CLI adapter.  This seam exists so every safety and
    compensation rule is executable against fakes before a provider reader can
    safely prove exact current Pages and Worker IDs.
    """
    _validate_request(request)
    _safe_operator_call(production.assert_queue_empty, "could not prove the editor queue empty")

    release_state = _safe_operator_call(
        lambda: production.timer_state(RELEASE_TIMER),
        "could not read the release timer state",
    )
    if release_state.enabled or release_state.active:
        raise MigrationError("production release timer must already be disabled and inactive")

    apply_state = _safe_operator_call(
        lambda: production.timer_state(APPLY_TIMER),
        "could not read the apply timer state",
    )
    primary_failure: BaseException | None = None
    candidate_commit_proved = False
    new_pair: ProductionPair | None = None
    restored = False
    returned = False
    keep_fenced = False
    compensation_attempted = False

    try:
        _safe_operator_call(
            lambda: production.stop_timer(APPLY_TIMER),
            "could not stop the apply timer",
        )
        stopped_state = _safe_operator_call(
            lambda: production.timer_state(APPLY_TIMER),
            "could not verify the stopped apply timer",
        )
        if stopped_state.enabled or stopped_state.active:
            raise MigrationError("apply timer did not stop and disable")

        with production.daemon_lock():
            try:
                with _exclusive_change_window(production):
                    try:
                        _safe_operator_call(
                            production.assert_no_activity,
                            "relevant release/apply activity remains after timer stop",
                        )
                        captured = _safe_operator_call(
                            production.capture_live_pair,
                            "could not capture the exact prior live pair",
                        )
                        if captured != request.prior_pair:
                            raise MigrationError("prior live pair mismatch")

                        _safe_operator_call(
                            lambda: production.assert_candidate_commit(
                                request.candidate_sha, request.prior_pair.sha,
                            ),
                            "candidate tree is not the exact clean committed migration",
                        )
                        candidate_commit_proved = True
                        _run_phases(
                            phases, request.candidate_sha, context="migration",
                            phase_names=VERIFY_ONLY_PHASES,
                        )

                        new_pair = _safe_operator_call(
                            lambda: production.deploy_candidate(request.candidate_sha),
                            "candidate deployment failed",
                        )
                        if not _valid_pair(new_pair) or new_pair.sha != request.candidate_sha:
                            raise MigrationError(
                                "candidate deploy did not return an exact provider pair"
                            )
                        _assert_live_pair(production, new_pair, "candidate live pair mismatch")

                        _safe_operator_call(
                            lambda: production.record_pair(new_pair, request.recovery_registry),
                            "could not atomically record the candidate recovery pair",
                        )
                        _safe_operator_call(
                            lambda: production.deploy_editor_dev(request.candidate_sha),
                            "editor/DEV candidate synchronization failed",
                        )

                        _safe_operator_call(
                            lambda: production.activate_pair(request.prior_pair),
                            "exact prior-pair restoration failed",
                        )
                        _assert_live_pair(
                            production, request.prior_pair,
                            "exact prior-pair readback mismatch",
                        )
                        restored = True

                        _safe_operator_call(
                            lambda: production.activate_pair(new_pair),
                            "return to intended candidate pair failed",
                        )
                        _assert_live_pair(
                            production, new_pair,
                            "returned candidate pair readback mismatch",
                        )
                        _safe_operator_call(
                            lambda: production.prove_all_surfaces(
                                request.candidate_sha, new_pair,
                            ),
                            "candidate was not proved across every deployed surface",
                        )
                        returned = True
                    except BaseException as exc:
                        primary_failure = exc
                        if candidate_commit_proved:
                            compensation_attempted = True
                            if not _compensate_to_prior(production, request):
                                keep_fenced = True
                                primary_failure = _persistent_fence_error(
                                    production,
                                    "migration failed and full compensation could not be proved",
                                )
                        else:
                            try:
                                _prove_complete_prior_state(production, request)
                            except BaseException:
                                keep_fenced = True
                                primary_failure = _persistent_fence_error(
                                    production,
                                    "failure before candidate proof and complete prior state "
                                    "could not be proved",
                                )
            except ChangeWindowCloseError:
                # This handler is deliberately inside daemon_lock. A close failure
                # outranks any earlier body error and can never auto-unfreeze.
                keep_fenced = True
                compensation_ok = True
                if candidate_commit_proved and not compensation_attempted:
                    compensation_attempted = True
                    compensation_ok = _compensate_to_prior(production, request)
                message = "exclusive change-window control boundary failed"
                if not compensation_ok:
                    message += " and full compensation could not be proved"
                primary_failure = _persistent_fence_error(production, message)
    except BaseException as exc:
        if isinstance(exc, MigrationFenced):
            keep_fenced = True
        if primary_failure is None or isinstance(exc, MigrationFenced):
            primary_failure = exc
    finally:
        if not keep_fenced and (apply_state.enabled or apply_state.active):
            try:
                _safe_operator_call(
                    lambda: production.restore_timer(APPLY_TIMER, apply_state),
                    "could not restore the apply timer policy",
                )
                observed = _safe_operator_call(
                    lambda: production.timer_state(APPLY_TIMER),
                    "could not read back the restored apply timer",
                )
                if observed != apply_state:
                    raise MigrationError("apply timer restoration readback mismatch")
            except BaseException:
                primary_failure = MigrationError(
                    "migration did not finish with the prior apply timer policy restored"
                )

    if primary_failure is not None:
        if isinstance(primary_failure, MigrationError):
            raise primary_failure
        raise MigrationError("migration failed") from None
    return {
        "mode": "production",
        "candidate_sha": request.candidate_sha,
        "prior_pair": request.prior_pair.redacted(),
        "new_pair": new_pair.redacted(),
        "restoration_proved": restored,
        "returned_to_candidate": returned,
        "editor_dev_synced": True,
        "change_window_released": True,
        "apply_timer_restored": True,
    }


def operator_plan(request: MigrationRequest) -> str:
    """Render exact, non-secret inputs beside the supervised execution order."""
    return f"""# U15 Day Zero supervised operator sheet

Candidate SHA: `{request.candidate_sha}`
Prior live SHA: `{request.prior_pair.sha}`
Prior Pages deployment ID: `{request.prior_pair.pages_deployment_id}`
Prior Worker version ID: `{request.prior_pair.worker_version_id}`
Recovery registry: `{request.recovery_registry}`

This remains a **Damien at the keyboard** production act. Keep
`SONSTENG_PROD_RELEASE_ENABLED=false`; keep `sonsteng-prod-release.timer`
disabled and inactive. Never use `deploy/deploy-prod.sh`, which is the disabled
Publisher-bypass tripwire.

This sheet is strictly post-materialization. The supplied candidate SHA already exists as a
materialized, reviewed, canonical, and clean commit; its parent is the prior SHA above.

1. Prove the supplied candidate is the exact clean canonical `main` commit and
   has the prior SHA above as its parent. Stop on any difference.
2. Confirm John has been notified and independently prove the editor/apply queue is empty.
3. Confirm `sonsteng-apply.timer` remains stopped and disabled, both
   release/apply services still have no process or lease, and the dedicated
   checkout's `.locks/daemon.lock` remains held.
4. Confirm the same exclusive change window remains held and still blocks
   canonical writers and merges, both apply and release daemons, direct deploy
   commands, and every provider deployment actor. Do not capture the prior pair
   unless all six actor classes remain excluded.
5. Inside that window, read the exact active Pages deployment ID, Worker version
   ID, and both live `x-release-sha` values. They must equal the prior identifiers above.
6. Run the write-free `verify_materialized` path against a fresh isolated copy
   of that exact commit. It must prove exact clean HEAD before and after builds,
   generated parity, strict Day Zero/identifier validation, and preflight.
7. Using only the bounded Cloudflare PROD principal, upload the Pages artifact
   and named production Worker version. Do not mutate DNS, Access, DEV, or account policy.
8. Read back the exact new provider IDs and both live SHA values. Atomically
   record the complete new pair in the recovery registry before proceeding.
9. Deploy/rebuild the editor and DEV surfaces from the same candidate SHA.
   Reactivate the exact prior IDs above and prove both live SHA values; then
   reactivate the exact new pair and prove production, DEV, editor, and canonical
   `main` all name the candidate SHA.
10. On failure, restore the exact prior provider pair, atomically compare-and-swap
    canonical `main` from the candidate SHA to the prior SHA with exact readback,
    rebuild/redeploy DEV and editor from the prior tree, and prove every surface
    is back on that prior SHA. If any compensation or persistent-fence proof
    fails, leave both timers stopped for supervised recovery. Release the window
    and restore the apply timer's exact prior policy only after final candidate
    proof or complete prior-state recovery.

Do not copy credentials, provider stdout/stderr, or authored corpus text into
the registry, receipts, terminal transcript, screenshots, or issue comments.
"""


def _head_sha(repo: pathlib.Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise MigrationError("could not resolve the rehearsal candidate SHA") from None


def build_parser() -> argparse.ArgumentParser:
    parser = BoundedArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--candidate-sha")
    parser.add_argument("--inspect-cloudflare-pair", action="store_true",
                        help="read and prove the active provider pair without mutation")
    parser.add_argument("--cloudflare-account-id")
    parser.add_argument("--pages-project")
    parser.add_argument("--worker-script")
    parser.add_argument("--pages-provenance-url")
    parser.add_argument("--worker-provenance-url")
    parser.add_argument("--execute", action="store_true",
                        help="request production mode (intentionally unwired and fail-closed)")
    parser.add_argument("--prior-sha")
    parser.add_argument("--prior-pages-deployment-id")
    parser.add_argument("--prior-worker-version-id")
    parser.add_argument("--recovery-registry", type=pathlib.Path)
    parser.add_argument("--ack-john-notified", action="store_true")
    parser.add_argument("--ack-queue-empty", action="store_true")
    parser.add_argument("--print-operator-plan", action="store_true")
    return parser


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        repo = args.repo.resolve()
        if args.inspect_cloudflare_pair:
            if args.execute:
                raise MigrationError("Cloudflare inspection cannot be combined with execution")
            required = (
                args.cloudflare_account_id,
                args.pages_project,
                args.worker_script,
                args.pages_provenance_url,
                args.worker_provenance_url,
            )
            if any(value is None for value in required):
                raise MigrationError("Cloudflare inspection requires all non-secret coordinates")
            token = read_cloudflare_token(sys.stdin)
            inspector = CloudflarePairInspector(
                args.cloudflare_account_id,
                args.pages_project,
                args.worker_script,
                token,
            )
            pair = inspector.inspect(
                args.pages_provenance_url,
                args.worker_provenance_url,
            )
            if args.print_operator_plan:
                if any((args.prior_sha, args.prior_pages_deployment_id,
                        args.prior_worker_version_id)):
                    raise MigrationError(
                        "inspected prior-pair coordinates cannot be overridden"
                    )
                request = MigrationRequest(
                    candidate_sha=args.candidate_sha or _head_sha(repo),
                    prior_pair=pair,
                    recovery_registry=(args.recovery_registry or pathlib.Path(".")),
                    john_notified=args.ack_john_notified,
                    queue_empty_acknowledged=args.ack_queue_empty,
                    enabled=(
                        os.environ.get("SONSTENG_DAY_ZERO_MIGRATION_ENABLED") == "true"
                    ),
                    normal_release_config_off=(
                        os.environ.get("SONSTENG_PROD_RELEASE_ENABLED", "false") == "false"
                    ),
                )
                _validate_request(request)
                print(operator_plan(request))
                return 0
            print(json.dumps({
                "mode": "read-only-cloudflare-inspection",
                "pair": pair.redacted(),
                "production_mutations": 0,
            }, sort_keys=True, separators=(",", ":")))
            return 0
        candidate_sha = args.candidate_sha or _head_sha(repo)
        if args.execute or args.print_operator_plan:
            request = MigrationRequest(
                candidate_sha=candidate_sha,
                prior_pair=ProductionPair(
                    args.prior_sha or "",
                    args.prior_pages_deployment_id or "",
                    args.prior_worker_version_id or "",
                ),
                recovery_registry=(args.recovery_registry or pathlib.Path(".")),
                john_notified=args.ack_john_notified,
                queue_empty_acknowledged=args.ack_queue_empty,
                enabled=os.environ.get("SONSTENG_DAY_ZERO_MIGRATION_ENABLED") == "true",
                normal_release_config_off=(
                    os.environ.get("SONSTENG_PROD_RELEASE_ENABLED", "false") == "false"
                ),
            )
            _validate_request(request)
            if args.print_operator_plan:
                print(operator_plan(request))
                return 0
            print(
                "error: no direct production adapter is installed; use the supervised "
                "operator sheet in docs/day-zero-migration-operations.md",
                file=sys.stderr,
            )
            return EX_CONFIG
        with signal_unwind_guard():
            receipt = rehearse(repo, candidate_sha)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
