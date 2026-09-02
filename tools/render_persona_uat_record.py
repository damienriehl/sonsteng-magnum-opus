#!/usr/bin/env python3
"""Render persona UAT JSON run files into one durable Markdown record."""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS = ROOT / "build" / "uat" / "runs"
DEFAULT_STORIES = ROOT / "docs" / "uat" / "user-stories.md"
DEFAULT_JOURNEYS = ROOT / "tools" / "persona_journeys.json"
DEFAULT_OUT = ROOT / "docs" / "uat" / "persona-uat-record.md"
STORY_HEADING_RE = re.compile(r"^#{1,6}\s+(US-\d+-(?:\d+|CANARY))\b", re.MULTILINE)
VERDICTS = ("PASS", "FAIL", "OPEN", "BLOCKED", "NOT RUN", "ERROR")


def story_ids(text: str) -> set[str]:
    """Return stable story IDs defined by Markdown headings."""
    return set(STORY_HEADING_RE.findall(text))


def build_key(build: dict[str, object]) -> tuple[str, str, str]:
    return tuple(str(build.get(name) or "") for name in ("spine_build_id", "git_base_sha", "release_sha"))


def load_runs(run_dir: Path) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    if not run_dir.exists():
        return runs
    for source in sorted(run_dir.glob("*.json")):
        try:
            run = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read run file {source}: {error}") from error
        if not isinstance(run, dict) or not isinstance(run.get("attempts"), list):
            raise ValueError(f"invalid run file {source}: expected an object with an attempts list")
        run["_source"] = str(source)
        runs.append(run)
    return runs


def flatten_attempts(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for run_order, run in enumerate(sorted(runs, key=lambda item: (str(item.get("started") or ""), str(item.get("run_id") or "")))):
        build = run.get("build") if isinstance(run.get("build"), dict) else {}
        for attempt_order, raw in enumerate(run.get("attempts", [])):
            if not isinstance(raw, dict):
                continue
            verdict = raw.get("verdict")
            if verdict not in VERDICTS:
                raise ValueError(f"invalid verdict {verdict!r} in {run.get('_source', 'run data')}")
            attempt = dict(raw)
            attempt.update(
                {
                    "run_id": str(run.get("run_id") or ""),
                    "env": str(run.get("env") or ""),
                    "base": str(run.get("base") or ""),
                    "started": str(run.get("started") or ""),
                    "build": build,
                    "_order": (run_order, attempt_order),
                }
            )
            flattened.append(attempt)
    return flattened


def current_attempts(attempts: list[dict[str, object]]) -> list[dict[str, object]]:
    """Select current rows on each environment and attempt kind's latest build."""
    latest_run_by_env_kind: dict[tuple[str, str], dict[str, object]] = {}
    for attempt in attempts:
        env = str(attempt.get("env") or "")
        kind = "binding" if str(attempt.get("viewport") or "") == "n/a" else "browser"
        env_kind = (env, kind)
        if env_kind not in latest_run_by_env_kind or attempt["_order"] > latest_run_by_env_kind[env_kind]["_order"]:
            latest_run_by_env_kind[env_kind] = attempt
    latest_build_by_env_kind = {
        env_kind: build_key(value.get("build") if isinstance(value.get("build"), dict) else {})
        for env_kind, value in latest_run_by_env_kind.items()
    }

    latest: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for attempt in attempts:
        env = str(attempt.get("env") or "")
        viewport = str(attempt.get("viewport") or "")
        kind = "binding" if viewport == "n/a" else "browser"
        build = attempt.get("build") if isinstance(attempt.get("build"), dict) else {}
        if build_key(build) != latest_build_by_env_kind.get((env, kind)):
            continue
        key = (str(attempt.get("story") or ""), env, viewport, kind)
        if key not in latest or attempt["_order"] > latest[key]["_order"]:
            latest[key] = attempt
    return sorted(latest.values(), key=lambda item: (str(item.get("story")), str(item.get("env")), str(item.get("viewport"))))


def clean_cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def artifact_cell(attempt: dict[str, object]) -> str:
    digest = str(attempt.get("digest") or "")
    reference = str(attempt.get("artifact") or attempt.get("shot_path") or "")
    if reference and digest:
        return f"{clean_cell(reference)} (`{digest}`)"
    if digest:
        return f"`{digest}`"
    return clean_cell(reference) or "—"


def build_cell(attempt: dict[str, object]) -> str:
    build = attempt.get("build") if isinstance(attempt.get("build"), dict) else {}
    parts = [str(build.get(name) or "") for name in ("spine_build_id", "git_base_sha", "release_sha")]
    return " / ".join(part for part in parts if part) or "unknown"


def unrun_attempts(current: list[dict[str, object]], journeys: list[dict[str, object]]) -> list[dict[str, object]]:
    covered = {str(item.get("story") or "") for item in current}
    placeholders = []
    for journey in journeys:
        story = str(journey.get("story") or "")
        if not story or story in covered:
            continue
        binding = str(journey.get("binding") or "")
        bound = journey.get(binding) if isinstance(journey.get(binding), dict) else {}
        placeholders.append(
            {
                "journey": journey.get("id"),
                "story": story,
                "persona": journey.get("persona"),
                "viewport": "n/a" if binding != "steps" else ", ".join(journey.get("viewports", [])),
                "env": "unrun",
                "verdict": "NOT RUN",
                "first_failure": "No run file recorded",
                "artifact": (bound.get("command") if isinstance(bound, dict) else None) or journey.get("id"),
                "digest": None,
                "canary": bool(journey.get("canary")),
                "build": {},
            }
        )
        covered.add(story)
    return placeholders


def render(runs: list[dict[str, object]], journeys: list[dict[str, object]] | None = None) -> str:
    attempts = flatten_attempts(runs)
    current = current_attempts(attempts)
    current.extend(unrun_attempts(current, journeys or []))
    current.sort(key=lambda item: (str(item.get("story")), str(item.get("env")), str(item.get("viewport"))))
    lines = [
        "# Persona UAT Record",
        "",
        "> Generated by `tools/render_persona_uat_record.py` from JSON run files. Do not edit verdict rows by hand.",
        "",
        "## Summary",
        "",
        f"- Run files: {len(runs)}",
        f"- Attempts: {len(attempts)}",
        f"- Current rows: {len(current)}",
        "",
        "## Current verdicts",
        "",
        "| Story | Persona | Environment | Viewport | Verdict | Build | Artifact | First failure |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in current:
        lines.append(
            "| {story} | {persona} | {env} | {viewport} | {verdict} | {build} | {artifact} | {failure} |".format(
                story=clean_cell(item.get("story")),
                persona=clean_cell(item.get("persona")),
                env=clean_cell(item.get("env")),
                viewport=clean_cell(item.get("viewport")),
                verdict=clean_cell(item.get("verdict")),
                build=clean_cell(build_cell(item)),
                artifact=artifact_cell(item),
                failure=clean_cell(item.get("first_failure")) or "—",
            )
        )
    if not current:
        lines.append("| — | — | — | — | NOT RUN | — | — | No run files rendered |")

    counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for item in current:
        if item.get("canary"):
            continue
        persona = str(item.get("persona") or "unknown")
        verdict = str(item.get("verdict") or "unknown")
        counts[persona]["total"] += 1
        counts[persona][verdict] += 1
    lines.extend(
        [
            "",
            "## Per-persona counts",
            "",
            "Deliberate canaries are excluded.",
            "",
            "| Persona | Current rows | PASS | FAIL | OPEN | BLOCKED | NOT RUN | ERROR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for persona in sorted(counts):
        total = counts[persona]["total"]
        verdict_counts = " | ".join(str(counts[persona][verdict]) for verdict in VERDICTS)
        lines.append(f"| {clean_cell(persona)} | {total} | {verdict_counts} |")
    if not counts:
        lines.append("| — | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")

    retained = [item for item in attempts if item.get("shot_path")]
    lines.extend(
        [
            "",
            "## Retained screenshots",
            "",
            "| Run | Journey | Verdict | Path | SHA-256 |",
            "|---|---|---|---|---|",
        ]
    )
    for item in retained:
        lines.append(
            "| {run} | {journey} | {verdict} | {path} | `{digest}` |".format(
                run=clean_cell(item.get("run_id")),
                journey=clean_cell(item.get("journey")),
                verdict=clean_cell(item.get("verdict")),
                path=clean_cell(item.get("shot_path")),
                digest=clean_cell(item.get("digest")),
            )
        )
    if not retained:
        lines.append("| — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Attempt history",
            "",
            "| Run | Started | Story | Journey | Persona | Environment | Viewport | Verdict | Build | Artifact | First failure |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in sorted(attempts, key=lambda value: value["_order"]):
        lines.append(
            "| {run} | {started} | {story} | {journey} | {persona} | {env} | {viewport} | {verdict} | {build} | {artifact} | {failure} |".format(
                run=clean_cell(item.get("run_id")),
                started=clean_cell(item.get("started")),
                story=clean_cell(item.get("story")),
                journey=clean_cell(item.get("journey")),
                persona=clean_cell(item.get("persona")),
                env=clean_cell(item.get("env")),
                viewport=clean_cell(item.get("viewport")),
                verdict=clean_cell(item.get("verdict")),
                build=clean_cell(build_cell(item)),
                artifact=artifact_cell(item),
                failure=clean_cell(item.get("first_failure")) or "—",
            )
        )
    if not attempts:
        lines.append("| — | — | — | — | — | — | — | NOT RUN | — | — | No attempts recorded |")
    return "\n".join(lines) + "\n"


def load_journeys(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    journeys = data.get("journeys", data) if isinstance(data, dict) else data
    if not isinstance(journeys, list):
        raise ValueError(f"invalid journey catalog {path}: expected a journeys list")
    return [item for item in journeys if isinstance(item, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--stories", type=Path, default=DEFAULT_STORIES)
    parser.add_argument("--journeys", type=Path, default=DEFAULT_JOURNEYS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    try:
        runs = load_runs(args.runs)
        journeys = load_journeys(args.journeys)
        rendered = render(runs, journeys)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        if not args.stories.exists():
            print(f"Story coverage check skipped: {args.stories} does not exist")
        else:
            defined = story_ids(args.stories.read_text(encoding="utf-8"))
            covered = {str(item.get("story")) for item in journeys if item.get("story")}
            missing = sorted(defined - covered)
            if missing:
                print("Stories without a journey or binding: " + ", ".join(missing), file=sys.stderr)
                return 1
        print(f"Rendered {len(runs)} run file(s) to {args.out}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"UAT record render error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
