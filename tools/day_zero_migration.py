#!/usr/bin/env python3
"""Rehearse the one-off U15 Day Zero migration without touching production.

The command's default path exports an exact candidate commit into a temporary
copy and runs the complete corpus/write/build/validation/preflight sequence
there.  The production state machine is dependency-injected and tested, but no
CLI production adapter exists: U15 remains a supervised Damien-at-the-keyboard
act until an exact-current-provider-ID reader can be added without bypassing
the Publisher release authority.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterator


APPLY_TIMER = "sonsteng-apply.timer"
RELEASE_TIMER = "sonsteng-prod-release.timer"
MIGRATION_PHASES = (
    "governed-verification",
    "governed-write",
    "generated-build",
    "build-parity",
    "strict-day-zero-enforcement",
    "preflight",
)
SHA_RE = re.compile(r"[0-9a-f]{40}")
PROVIDER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
EX_CONFIG = 78


class MigrationError(RuntimeError):
    """A bounded migration failure safe to print to an operator."""


class MigrationInterrupted(MigrationError):
    """Raised for SIGINT/SIGTERM so normal unwind logic runs."""


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


def _run_phases(phases, candidate_sha: str, *, context: str) -> None:
    for phase in MIGRATION_PHASES:
        try:
            phases.run(phase, candidate_sha)
        except MigrationInterrupted:
            raise
        except BaseException:
            raise MigrationError(f"{context} phase failed: {phase}") from None


@contextlib.contextmanager
def git_archive_copy(repo: pathlib.Path, candidate_sha: str) -> Iterator[pathlib.Path]:
    """Export one exact commit to a disposable, non-Git working copy."""
    repo = pathlib.Path(repo).resolve()
    if not (repo / ".git").exists():
        raise MigrationError("rehearsal source is not a Git checkout")
    try:
        archived = subprocess.run(
            ["git", "archive", "--format=tar", candidate_sha],
            cwd=repo,
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise MigrationError("could not export the exact rehearsal candidate") from None
    with tempfile.TemporaryDirectory(prefix="sonsteng-day-zero-rehearsal-") as directory:
        target = pathlib.Path(directory) / "checkout"
        target.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as archive:
                archive.extractall(target, filter="data")
        except (OSError, tarfile.TarError):
            raise MigrationError("could not materialize the isolated rehearsal copy") from None
        yield target


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

    def run(self, phase: str, candidate_sha: str) -> None:
        print(f"rehearsal-phase:start:{phase}", file=sys.stderr, flush=True)
        if phase == "governed-verification":
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
                    "--enforce-day-zero-offsets", "--quiet", "--json", str(report),
                ])
                payload = json.loads(report.read_text(encoding="utf-8"))
                totals = payload.get("totals") or {}
                if payload.get("day_zero_offset_enforcement") is not True or \
                   int(totals.get("checked_dates") or 0) <= 0 or \
                   int(totals.get("offset_dates_checked") or 0) <= 0:
                    raise MigrationError("strict Day Zero gate did not execute date checks")
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
    isolated_copy: Callable = git_archive_copy,
) -> dict:
    """Run every U15 phase in a disposable copy and make zero PROD calls."""
    if not SHA_RE.fullmatch(candidate_sha or ""):
        raise MigrationError("an exact lowercase candidate SHA is required")
    repo = pathlib.Path(repo).resolve()
    with isolated_copy(repo, candidate_sha) as checkout:
        runner = phases or LocalRehearsalPhases(checkout)
        _run_phases(runner, candidate_sha, context="rehearsal")
    return {
        "mode": "rehearsal",
        "candidate_sha": candidate_sha,
        "phases": list(MIGRATION_PHASES),
        "production_mutations": 0,
    }


def _assert_live_pair(production, expected: ProductionPair, failure: str) -> None:
    observed = _safe_operator_call(production.read_live_pair, failure)
    if observed != expected:
        raise MigrationError(failure)


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

    captured = _safe_operator_call(
        production.capture_live_pair,
        "could not capture the exact prior live pair",
    )
    if captured != request.prior_pair:
        raise MigrationError("prior live pair mismatch")

    apply_state = _safe_operator_call(
        lambda: production.timer_state(APPLY_TIMER),
        "could not read the apply timer state",
    )
    primary_failure: BaseException | None = None
    candidate_may_be_live = False
    new_pair: ProductionPair | None = None
    restored = False
    returned = False

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
                _safe_operator_call(
                    production.assert_no_activity,
                    "relevant release/apply activity remains after timer stop",
                )
                _run_phases(phases, request.candidate_sha, context="migration")

                candidate_may_be_live = True
                new_pair = _safe_operator_call(
                    lambda: production.deploy_candidate(request.candidate_sha),
                    "candidate deployment failed",
                )
                if not _valid_pair(new_pair) or new_pair.sha != request.candidate_sha:
                    raise MigrationError("candidate deploy did not return an exact provider pair")
                _assert_live_pair(production, new_pair, "candidate live pair mismatch")

                _safe_operator_call(
                    lambda: production.record_pair(new_pair, request.recovery_registry),
                    "could not atomically record the candidate recovery pair",
                )

                _safe_operator_call(
                    lambda: production.activate_pair(request.prior_pair),
                    "exact prior-pair restoration failed",
                )
                _assert_live_pair(production, request.prior_pair, "exact prior-pair readback mismatch")
                restored = True

                _safe_operator_call(
                    lambda: production.activate_pair(new_pair),
                    "return to intended candidate pair failed",
                )
                _assert_live_pair(production, new_pair, "returned candidate pair readback mismatch")
                returned = True
            except BaseException as exc:
                primary_failure = exc
                if candidate_may_be_live:
                    try:
                        _safe_operator_call(
                            lambda: production.activate_pair(request.prior_pair),
                            "emergency prior-pair restoration failed",
                        )
                        _assert_live_pair(
                            production,
                            request.prior_pair,
                            "emergency prior-pair readback mismatch",
                        )
                    except BaseException:
                        primary_failure = MigrationError(
                            "migration failed and exact prior-pair restoration could not be proved"
                        )
    except BaseException as exc:
        if primary_failure is None:
            primary_failure = exc
    finally:
        if apply_state.enabled or apply_state.active:
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

1. Confirm John has been notified and independently prove the editor/apply queue is empty.
2. Read the exact active Pages deployment ID, Worker version ID, and both live
   `x-release-sha` values. They must equal the prior identifiers above.
3. Stop and disable `sonsteng-apply.timer`; prove both release/apply services
   have no process or lease; acquire the dedicated checkout's `.locks/daemon.lock`.
4. Run this command without `--execute` at the exact candidate SHA. Require all
   six rehearsal phases to pass in its isolated copy.
5. In a separate isolated candidate checkout, run the same governed write,
   generators, parity, strict Day Zero gate, and preflight. Stop on any failure.
6. Using only the bounded Cloudflare PROD principal, upload the Pages artifact
   and named production Worker version. Do not mutate DNS, Access, DEV, or account policy.
7. Read back the exact new provider IDs and both live SHA values. Atomically
   record the complete new pair in the recovery registry before proceeding.
8. Reactivate the exact prior IDs above and prove both live SHA values; then
   reactivate the exact new pair and prove it again.
9. On every exit or signal, restore the prior pair if the intended pair is not
   fully proved, release `.locks/daemon.lock`, restore the apply timer's prior
   enable/active policy, and read it back. SIGKILL/power loss requires the same
   recovery sheet before any retry.

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--candidate-sha")
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
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    try:
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
