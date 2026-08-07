"""Authoritative, fail-closed student-material archive manifest."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath


SCHEMA_VERSION = "1.0.0"
REQUIRED = ("matter.json", "exercise/exercise.json", "rubric.json")
OPTIONAL = ("business/business.json", "business/engagement-letter.md")
EXCLUDED_AUTHORED = {"facts.md", "exercise/instructor-notes.md", "exercise/answer-key.md"}
INSTRUCTOR_BASENAMES = {"facts.md", "instructor-notes.md", "answer-key.md"}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class StudentArchiveError(ValueError):
    pass


def _safe_member(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise StudentArchiveError("unsafe archive member: %s" % relative)
    if posix.name in INSTRUCTOR_BASENAMES:
        raise StudentArchiveError("instructor-only archive member: %s" % relative)
    allowed = relative in REQUIRED or relative in OPTIONAL or relative.startswith("case-file/")
    if not allowed:
        raise StudentArchiveError("member outside student-material allowlist: %s" % relative)
    path = root.joinpath(*posix.parts)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
        raise StudentArchiveError("symlinks are not student-safe: %s" % relative)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise StudentArchiveError("member escapes matter root: %s" % relative)
    return path


def student_material_manifest(matter_dir, slug):
    root = Path(matter_dir)
    missing = [name for name in REQUIRED if not _safe_member(root, name).is_file()]
    if missing:
        raise StudentArchiveError("missing required student materials: %s" % ", ".join(missing))
    exercise = json.loads((root / "exercise" / "exercise.json").read_text(encoding="utf-8"))
    exhibits = []
    excluded_authored = []
    case_file = ((exercise.get("sections") or {}).get("case_file") or {})
    for authored in case_file.get("files") or []:
        member = authored
        if member in EXCLUDED_AUTHORED:
            excluded_authored.append(member)
            continue
        path = _safe_member(root, member)
        if not path.is_file():
            raise StudentArchiveError("missing required learner exhibit: %s" % authored)
        exhibits.append(member)
    missing_optional = [name for name in OPTIONAL if not _safe_member(root, name).is_file()]
    members = sorted(set(REQUIRED + tuple(exhibits) + tuple(n for n in OPTIONAL if n not in missing_optional)))
    records = []
    for member in members:
        path = _safe_member(root, member)
        records.append({"path": member, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "required": member in REQUIRED or member in exhibits})
    return {"schema_version": SCHEMA_VERSION, "slug": slug, "root": str(root),
            "members": records, "missing_optional": sorted(missing_optional),
            "excluded_authored": sorted(excluded_authored)}


def public_data_members(manifest):
    return [item for item in manifest["members"] if item["path"] in REQUIRED]


def learner_exhibit_members(manifest):
    return [item for item in manifest["members"] if item["path"].startswith("case-file/")]


def write_student_archive(manifest, destination, transform=None):
    root = Path(manifest["root"])
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    public = {k: v for k, v in manifest.items() if k != "root"}
    public["members"] = []
    payloads = []
    for record in manifest["members"]:
        data = _safe_member(root, record["path"]).read_bytes()
        if transform:
            data = transform(record["path"], data)
        public["members"].append({**record, "sha256": hashlib.sha256(data).hexdigest()})
        payloads.append((record["path"], data))
    payloads.append(("manifest.json", (json.dumps(public, indent=2, sort_keys=True) + "\n").encode()))
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payloads):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return destination
