#!/usr/bin/env python3
"""tools/validate_spine.py — the integrity gate for the Sonsteng data spine.

This is the spine's ONLY automated integrity gate, designed to run at
20-parallel-author scale. It implements the 30 checks in
docs/research/validator-spec.md plus the countable depth floor from
docs/content-style-guide.md.

Design (per the spec):
  * Per-matter isolation — one broken matter never blocks the other 19.
  * Two-pass symbol table — pass 1 collects every declared id by namespace,
    pass 2 resolves references.
  * Severity model — ERROR blocks ship; WARN is advisory. Per-matter money /
    referential / schema / persona-fact-fidelity = ERROR. Matter<->firm
    aggregate reconciliation = WARN. Name-collision sweep = WARN (review table).
  * Partial-spine tolerance — a cross-ref whose target is not yet authored is a
    WARN by default and an ERROR under --strict (the ship gate).
  * Offline by default — FOLIO IRI *format* is checked offline always; IRI
    *existence* is checked only under --online against a local crosswalk snapshot
    (never live MCP).
  * Money math in decimal.Decimal (the schemas deliberately omit multipleOf:0.1
    because floats are unreliable); ±$0.01 tolerance where allowed.
  * Deterministic, sorted output. Human summary to stdout; --json machine report.
  * Exit non-zero on any ERROR.

Run `python3 tools/validate_spine.py --help` for usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import build_site

# ---------------------------------------------------------------------------
# jsonschema is used when importable; otherwise we degrade to structural checks
# with a loud WARN so OSS clones still get most of the gate.
# ---------------------------------------------------------------------------
try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft202012Validator

    HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover - exercised only on bare clones
    HAVE_JSONSCHEMA = False


# ===========================================================================
# Constants
# ===========================================================================

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"
SEVERITY_RANK = {ERROR: 0, WARN: 1, INFO: 2}

CENT = Decimal("0.01")
VALIDATOR_CHECK_COUNT = 30

# U16a compatibility mode: resolve and validate additive offsets now, but do
# not require every convertible absolute date to carry one until U16b.
ENFORCE_DAY_ZERO_OFFSETS = False
DECLARED_HOLDOUT_STATUS = "declared_absolute_holdout"
DAY_ZERO_FULL_DATE_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)|"
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}\b"
)
DAY_ZERO_RAW_LOCATOR_RE = re.compile(
    r"^raw:([0-9a-f]{16}):occurrence:([1-9][0-9]*):date:([0-9]+)$"
)
DAY_ZERO_JSON_LOCATOR_RE = re.compile(r"^json:(.+):date:([0-9]+)$")

SECTION_KEYS = [
    "intro",
    "objectives",
    "activities",
    "instructions",
    "case_file",
    "history",
    "considerations",
    "substantive_info",
]

TRIGGER_VOCAB = {
    "open_ended_invitation",
    "wellbeing_question",
    "acknowledged_emotion",
    "no_interruption_streak",
    "confidentiality_reassurance",
    "nonjudgmental_response",
    "follow_up_on_hint",
    "explained_process",
}

# Master-outline pinned exercise totals, keyed by the manifest shape key.
SHAPE_EXERCISE_TOTAL = {
    "employment_arbitration": Decimal("202"),  # Arbitration
    "attorney_discipline": Decimal("207"),  # PR
    "auto_negligence": Decimal("325"),  # Tort
    "real_estate_negotiation": Decimal("185.5"),  # Real Estate
    "criminal_dwi": Decimal("316.5"),  # DWI
    "noncompete_trade_secret": Decimal("189"),  # Non-Compete
    # ucc_sale_of_goods / juvenile_delinquency / marriage_dissolution /
    # wills_probate are not pinned by the master outline.
}

# The 26 surveyed skills (17 Legal Practice + 9 Practice Management), each as a
# set of accepted exact phrasings per docs/research/skills-survey.md.
SURVEYED_SKILLS = [
    {"Ability to diagnose and plan solutions for legal problems",
     "Ability to diagnose and plan for legal problems"},
    {"Ability in legal analysis and reasoning",
     "Ability in legal analysis and legal reasoning"},
    {"Knowledge of substantive law"},
    {"Knowledge of procedural law"},
    {"Library legal research", "Skills to conduct legal library research"},
    {"Computer legal research", "Knowledge of computer legal research"},
    {"Fact gathering"},
    {"Oral communication"},
    {"Written communication"},
    {"Counseling"},
    {"Instilling others' confidence in you",
     "The ability to instill others' confidence in their work"},
    {"Ability to obtain and keep clients"},
    {"Negotiation"},
    {"Litigation", "Understanding and conducting litigation"},
    {"Organization and management of legal work"},
    {"Sensitivity to professional and ethical concerns"},
    {"Drafting legal documents", "Ability to draft legal documents"},
    {"Fee arrangements, pricing, billing"},
    {"Human resources, hiring, support staff"},
    {"Capitalization, investment"},
    {"Project and time management, efficiency"},
    {"Planning, resource allocation, budgeting"},
    {"Marketing, client development"},
    {"Technology, computers, communications"},
    {"Governance, decision-making, long-range strategic planning"},
    {"Interpersonal communications, staff relations"},
]

SCHEMA_FILES = {
    "matter": "matter.schema.json",
    "persona": "persona.schema.json",
    "rubric": "rubric.schema.json",
    "exercise": "exercise.schema.json",
    "business": "business.schema.json",
    "skill": "skill.schema.json",
    "task": "task.schema.json",
    "firm": "firm.schema.json",
    "debrief_scorecard": "debrief.scorecard.schema.json",
    "critique_scorecard": "critique.scorecard.schema.json",
    "memo_scorecard": "memo-scorecard.schema.json",
    "page_copy": "page-copy.schema.json",
    "date_offsets": "date-offsets.schema.json",
    "day_zero_audit": "day-zero-audit.schema.json",
    "day_zero_holdouts": "day-zero-holdouts.schema.json",
    "assessment_instrument": "assessment-instrument.schema.json",
}

# Depth-floor minimums (docs/content-style-guide.md §3).
DEPTH_MIN_WITNESSES = 3
DEPTH_MIN_EXHIBITS = 5
DEPTH_MIN_PERSONAS = 2
DEPTH_MIN_RAPPORT_FACTS = 2  # for the "≥1 persona with ≥2 rapport-gated facts"
DEPTH_SECTION_MIN_WORDS = 150
DEPTH_FACTS_MIN_WORDS = 1200
DEPTH_FACTS_MAX_WORDS = 2500

# Compiled id patterns.
RE = {
    "matter": re.compile(r"^m\d{2}$"),
    "matter_slug": re.compile(r"^m\d{2}-[a-z0-9-]+$"),
    "skill": re.compile(r"^SK-(LP|PM)-\d{2}$"),
    "task": re.compile(r"^TSK-\d{3}$"),
    "subtask": re.compile(r"^TSK-\d{3}\.\d{2}$"),
    "role": re.compile(r"^m\d{2}\.role\.[a-z0-9-]+$"),
    "persona": re.compile(r"^m\d{2}\.per\.[a-z0-9-]+$"),
    "fact": re.compile(r"^m\d{2}\.fact\.\d{3}$"),
    "witness": re.compile(r"^m\d{2}\.wit\.[a-z0-9-]+$"),
    "exhibit": re.compile(r"^m\d{2}\.exh\.\d{3}$"),
    "exercise": re.compile(r"^m\d{2}\.ex$"),
    "rubric": re.compile(r"^m\d{2}\.rub$"),
    "criterion": re.compile(r"^m\d{2}\.rub\.c\d{2}(\.s\d{2})*$"),
    "business": re.compile(r"^m\d{2}\.biz$"),
    "time_entry": re.compile(r"^m\d{2}\.te\.\d{4}$"),
    "invoice": re.compile(r"^m\d{2}\.inv\.\d{3}$"),
    "trust": re.compile(r"^m\d{2}\.tr\.\d{3}$"),
    "client": re.compile(r"^FIRM-C-\d{2}$"),
    "timekeeper": re.compile(r"^FIRM-TK-\d{2}$"),
    "budget": re.compile(r"^FIRM-B-\d{2}$"),
    "folio_iri": re.compile(r"^(https://folio\.openlegalstandard\.org/R[A-Za-z0-9]+|R[A-Za-z0-9]+)$"),
    "mNN_prefix": re.compile(r"^(m\d{2})(?:\.|$)"),
}

FACT_ANCHOR_RE = re.compile(r"\[(m\d{2}\.fact\.\d{3})\]")
WORD_RE = re.compile(r"\S+")


# ===========================================================================
# Finding / Report model
# ===========================================================================

class Finding:
    __slots__ = ("scope", "check", "severity", "message", "detail")

    def __init__(self, scope, check, severity, message, detail=None):
        self.scope = scope            # matter id, or a global module name
        self.check = check            # e.g. "A2", "B10", "DEPTH"
        self.severity = severity      # ERROR | WARN | INFO
        self.message = message
        self.detail = detail or {}

    def sort_key(self):
        return (SEVERITY_RANK[self.severity], self.check, self.message)

    def to_dict(self):
        d = {
            "scope": self.scope,
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }
        if self.detail:
            d["detail"] = self.detail
        return d


class Report:
    def __init__(self):
        self.findings: list[Finding] = []
        self.matters_seen: set[str] = set()
        self.modules_seen: set[str] = set()
        self.checked_dates = 0
        self.offset_dates_checked = 0
        self.day_zero_offset_enforcement = ENFORCE_DAY_ZERO_OFFSETS
        self._checked_date_fields: set[tuple[str, str]] = set()

    def add(self, scope, check, severity, message, detail=None):
        self.findings.append(Finding(scope, check, severity, message, detail))

    def for_scope(self, scope):
        return [f for f in self.findings if f.scope == scope]

    def counts(self, scope):
        e = sum(1 for f in self.for_scope(scope) if f.severity == ERROR)
        w = sum(1 for f in self.for_scope(scope) if f.severity == WARN)
        return e, w

    def has_errors(self):
        return any(f.severity == ERROR for f in self.findings)

    def record_checked_date(self, source, locator, used_offset):
        field = (str(source), locator)
        if field in self._checked_date_fields:
            return
        self._checked_date_fields.add(field)
        self.checked_dates += 1
        if used_offset:
            self.offset_dates_checked += 1


# ===========================================================================
# Small helpers
# ===========================================================================

def dec(x):
    """Decimal from anything numeric/str; raises on garbage."""
    return Decimal(str(x))


def money_eq(a, b, tol=CENT):
    try:
        return abs(dec(a) - dec(b)) <= tol
    except (InvalidOperation, TypeError):
        return False


def is_tenth(hours) -> bool:
    """True iff hours is a positive multiple of 0.1 (Decimal-exact)."""
    try:
        q = dec(hours) * 10
    except (InvalidOperation, TypeError):
        return False
    return q == q.to_integral_value()


def parse_date(s):
    if not isinstance(s, str):
        return None
    for parser in (date.fromisoformat,
                   lambda value: datetime.strptime(value, "%B %d, %Y").date()):
        try:
            return parser(s)
        except ValueError:
            pass
    return None


def word_count(text) -> int:
    if not text:
        return 0
    return len(WORD_RE.findall(text))


def norm_name(s: str) -> str:
    s = (s or "").strip().casefold()
    s = s.replace("’", "'")  # curly -> straight apostrophe
    return re.sub(r"\s+", " ", s)


def surname_of(full_name: str) -> str:
    """Best-effort surname = last alphabetic token (drops Jr./III/parentheticals)."""
    if not full_name:
        return ""
    cleaned = re.sub(r"\(.*?\)", " ", full_name)
    cleaned = cleaned.replace("Hon.", " ")
    tokens = [t.strip(".,") for t in cleaned.split() if t.strip(".,")]
    tokens = [t for t in tokens if re.search(r"[A-Za-z]", t)]
    skip = {"Jr", "Sr", "II", "III", "IV", "LLP", "LLC", "Inc", "Corp", "Co", "Ltd"}
    while tokens and tokens[-1].rstrip(".") in skip:
        tokens.pop()
    return tokens[-1] if tokens else ""


def walk_strings(obj):
    """Yield every string leaf in a nested JSON structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)


def classify(obj):
    """Return the spine entity_type for a loaded JSON doc, or None."""
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "matter_manifest" or "matters" in obj and "shape_keys" in obj:
        return "matter_manifest"
    if obj.get("type") == "page_copy":
        return "page_copy"
    _id = obj.get("id")
    if _id == "FIRM":
        return "firm"
    if isinstance(_id, str):
        if RE["skill"].match(_id):
            return "skill"
        if RE["task"].match(_id):
            return "task"
        if RE["business"].match(_id):
            return "business"
        if RE["rubric"].match(_id):
            return "rubric"
        if RE["exercise"].match(_id):
            return "exercise"
        if RE["persona"].match(_id):
            return "persona"
        if RE["matter"].match(_id):
            return "matter"
    if "axis_a" in obj and "persona_id" in obj:
        return "debrief_scorecard"
    if "revise_resubmit_note" in obj and "rubric_id" in obj:
        return "critique_scorecard"
    return None


# ===========================================================================
# Loading
# ===========================================================================

class Loaded:
    """A loaded entity file: parsed obj + provenance + schema status."""

    def __init__(self, path, obj, entity_type):
        self.path = path
        self.obj = obj
        self.entity_type = entity_type
        self.schema_ok = True  # set False by F29 on schema failure


class MatterBundle:
    def __init__(self, mid, slug, directory):
        self.id = mid
        self.slug = slug
        self.dir = directory
        self.matter: Loaded | None = None
        self.personas: dict[str, Loaded] = {}
        self.rubric: Loaded | None = None
        self.exercise: Loaded | None = None
        self.business: Loaded | None = None
        self.other: list[Loaded] = []      # scorecards etc.
        self.date_offsets: Loaded | None = None
        self.fact_anchors: set[str] = set()
        self.facts_words: int = 0
        self.facts_present: bool = False


class World:
    def __init__(self, data_dir: Path, schemas_dir: Path):
        self.data_dir = data_dir
        self.schemas_dir = schemas_dir
        self.spine_manifest = None
        self.matters_manifest = None
        self.manifest_index: dict[str, dict] = {}
        self.surname_ledger: dict[str, list] = {}
        self.firm: Loaded | None = None
        self.skills: dict[str, Loaded] = {}
        self.tasks: dict[str, Loaded] = {}
        self.page_copies: list[Loaded] = []
        self.day_zero_artifacts: list[Loaded] = []
        self.assessment_instruments: list[Loaded] = []
        self.matters: dict[str, MatterBundle] = {}
        self.meridian_reserved: set[str] = set()  # surnames of judges/counties/cities
        self.load_errors: list[tuple[Path, str]] = []


def read_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def discover(data_dir: Path, only_matter: str | None) -> World:
    schemas_dir = data_dir / "schemas"
    world = World(data_dir, schemas_dir)

    # spine manifest (schema versions)
    sm_path = data_dir / "spine-manifest.json"
    if sm_path.exists():
        obj, err = read_json(sm_path)
        if err:
            world.load_errors.append((sm_path, err))
        else:
            world.spine_manifest = obj

    # Day Zero review artifacts are spine contracts even though they are not
    # ordinary id-bearing entities and therefore cannot be classified by id.
    for fname, etype in (
        ("day-zero-anchor-audit.json", "day_zero_audit"),
        ("day-zero-holdouts.json", "day_zero_holdouts"),
    ):
        artifact_path = data_dir / fname
        if artifact_path.exists():
            obj, err = read_json(artifact_path)
            if err:
                world.load_errors.append((artifact_path, err))
            else:
                world.day_zero_artifacts.append(Loaded(artifact_path, obj, etype))

    # Curriculum assessment instruments are global spine contracts. Their
    # human-readable ids intentionally do not share a matter entity pattern,
    # so load them explicitly instead of relying on classify().
    assessment_path = data_dir / "curriculum" / "assessment-instrument.json"
    if assessment_path.exists():
        obj, err = read_json(assessment_path)
        if err:
            world.load_errors.append((assessment_path, err))
        else:
            world.assessment_instruments.append(
                Loaded(assessment_path, obj, "assessment_instrument")
            )

    # matter registry
    reg_path = data_dir / "matters" / "manifest.json"
    if reg_path.exists():
        obj, err = read_json(reg_path)
        if err:
            world.load_errors.append((reg_path, err))
        elif obj:
            world.matters_manifest = obj
            for m in obj.get("matters", []):
                world.manifest_index[m.get("id")] = m
            world.surname_ledger = (
                obj.get("name_collision_sweep", {}).get("surname_ledger", {})
            )

    # meridian reserved names (for A6)
    mer_path = data_dir / "jurisdictions" / "meridian.json"
    if mer_path.exists():
        obj, err = read_json(mer_path)
        if not err and obj:
            for j in obj.get("judge_pool", {}).get("judges", []):
                world.meridian_reserved.add(norm_name(surname_of(j.get("name", ""))))
            for c in obj.get("counties", []):
                world.meridian_reserved.add(norm_name(c.get("name", "").replace("County", "").strip()))
                world.meridian_reserved.add(norm_name(c.get("seat", "")))
            gov = obj.get("government", {})
            for k in ("capital_city", "largest_city"):
                world.meridian_reserved.add(norm_name(gov.get(k, "")))
            world.meridian_reserved.discard("")

    # Walk the data tree (excluding schemas/) for entity files.
    for root, dirs, files in os.walk(data_dir):
        rootp = Path(root)
        # never descend into the schema/example area
        if schemas_dir in rootp.parents or rootp == schemas_dir:
            continue
        for fname in files:
            if not fname.endswith(".json"):
                continue
            fpath = rootp / fname
            if fpath in (sm_path, reg_path, mer_path, assessment_path):
                continue
            if fpath.name in {"day-zero-anchor-audit.json", "day-zero-holdouts.json"}:
                continue  # loaded explicitly above with their non-entity schema types
            if fname in {"folio-crosswalk.json", "editable-fields.json",
                         "taxonomy-identities.json"} or fname.startswith("_"):
                continue  # --online snapshot / generator scripts, not entity data
            obj, err = read_json(fpath)
            if err:
                world.load_errors.append((fpath, err))
                continue
            # taxonomy collection files ({"skills": [...]}, {"tasks": [...]})
            if isinstance(obj, dict) and isinstance(obj.get("skills"), list):
                for el in obj["skills"]:
                    if isinstance(el, dict) and isinstance(el.get("id"), str) \
                            and RE["skill"].match(el["id"]):
                        world.skills[el["id"]] = Loaded(fpath, el, "skill")
                continue
            if isinstance(obj, dict) and isinstance(obj.get("tasks"), list) \
                    and "shape_keys" not in obj:
                for el in obj["tasks"]:
                    if isinstance(el, dict) and isinstance(el.get("id"), str) \
                            and RE["task"].match(el["id"]):
                        world.tasks[el["id"]] = Loaded(fpath, el, "task")
                continue
            etype = classify(obj)
            if etype in (None, "matter_manifest"):
                continue
            loaded = Loaded(fpath, obj, etype)
            if etype == "firm":
                world.firm = loaded
            elif etype == "page_copy":
                world.page_copies.append(loaded)
            elif etype == "skill":
                world.skills[obj["id"]] = loaded
            elif etype == "task":
                world.tasks[obj["id"]] = loaded
            # matter-scoped entities are attached below via bundles

    # Matter dirs live under data/matters/<slug>/
    matters_root = data_dir / "matters"
    if matters_root.exists():
        for child in sorted(matters_root.iterdir()):
            if not child.is_dir():
                continue
            if not RE["matter_slug"].match(child.name):
                continue
            mid = child.name[:3]
            if only_matter and mid != only_matter:
                continue
            bundle = MatterBundle(mid, child.name, child)
            _fill_bundle(bundle, child, world)
            world.matters[mid] = bundle

    return world


def _fill_bundle(bundle: MatterBundle, directory: Path, world: World):
    # facts.md
    facts_path = directory / "facts.md"
    if facts_path.exists():
        try:
            text = facts_path.read_text(encoding="utf-8")
            bundle.facts_present = True
            bundle.facts_words = word_count(text)
            bundle.fact_anchors = set(FACT_ANCHOR_RE.findall(text))
        except Exception:  # noqa: BLE001
            bundle.facts_present = False

    for root, dirs, files in os.walk(directory):
        for fname in files:
            if not fname.endswith(".json"):
                continue
            fpath = Path(root) / fname
            obj, err = read_json(fpath)
            if err:
                world.load_errors.append((fpath, err))
                continue
            if fname == "date-offsets.json":
                bundle.date_offsets = Loaded(fpath, obj, "date_offsets")
                continue
            etype = classify(obj)
            if etype is None:
                continue
            loaded = Loaded(fpath, obj, etype)
            if etype == "matter":
                bundle.matter = loaded
            elif etype == "persona":
                bundle.personas[obj["id"]] = loaded
            elif etype == "rubric":
                bundle.rubric = loaded
            elif etype == "exercise":
                bundle.exercise = loaded
            elif etype == "business":
                bundle.business = loaded
            else:
                bundle.other.append(loaded)


# ===========================================================================
# Schema validation (F29) + structural degrade
# ===========================================================================

class SchemaSet:
    def __init__(self, schemas_dir: Path):
        self.dir = schemas_dir
        self.raw: dict[str, dict] = {}
        self.validators: dict[str, object] = {}
        for etype, fname in SCHEMA_FILES.items():
            path = schemas_dir / fname
            if not path.exists():
                continue
            obj, err = read_json(path)
            if err or obj is None:
                continue
            self.raw[etype] = obj
            if HAVE_JSONSCHEMA:
                try:
                    self.validators[etype] = Draft202012Validator(obj)
                except Exception:  # noqa: BLE001
                    pass

    def validate(self, etype, obj):
        """Return list of (path, message) errors. Uses jsonschema if available,
        else a structural degrade (top-level required keys + id pattern)."""
        errors = []
        if HAVE_JSONSCHEMA and etype in self.validators:
            v = self.validators[etype]
            for e in sorted(v.iter_errors(obj), key=lambda x: list(x.path)):
                loc = "/".join(str(p) for p in e.path) or "<root>"
                errors.append((loc, e.message))
            return errors
        # structural degrade
        schema = self.raw.get(etype)
        if not schema:
            return errors
        for req in schema.get("required", []):
            if req not in obj:
                errors.append((req, f"missing required property '{req}'"))
        idprop = schema.get("properties", {}).get("id", {})
        pat = idprop.get("pattern")
        if pat and isinstance(obj.get("id"), str) and not re.match(pat, obj["id"]):
            errors.append(("id", f"'{obj['id']}' does not match {pat}"))
        return errors


# ===========================================================================
# Two-pass symbol table
# ===========================================================================

class SymbolTable:
    """Namespace -> id -> list of source descriptions (for collision reports)."""

    def __init__(self):
        self.ns: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    def declare(self, namespace, _id, source):
        self.ns[namespace][_id].append(source)

    def has(self, namespace, _id):
        return _id in self.ns[namespace]

    def collisions(self):
        out = []
        for namespace, ids in sorted(self.ns.items()):
            for _id, sources in sorted(ids.items()):
                if len(sources) > 1:
                    out.append((namespace, _id, sources))
        return out


def build_symbols(world: World, report: Report) -> SymbolTable:
    st = SymbolTable()

    # Global namespaces from manifest + firm.
    for mid, m in world.manifest_index.items():
        st.declare("matter", mid, f"manifest:{mid}")
        if m.get("client_id"):
            st.declare("client", m["client_id"], f"manifest:{mid}")

    if world.firm:
        fobj = world.firm.obj
        src = str(world.firm.path)
        for c in fobj.get("clients", []):
            st.declare("client", c.get("id"), src)
        for tk in fobj.get("timekeepers", []):
            st.declare("timekeeper", tk.get("id"), src)
        for b in fobj.get("budget", []):
            st.declare("budget", b.get("id"), src)

    for sid, s in world.skills.items():
        st.declare("skill", sid, str(s.path))
    for tid, t in world.tasks.items():
        st.declare("task", tid, str(t.path))
        for sub in t.obj.get("subtasks", []):
            st.declare("subtask", sub.get("id"), str(t.path))

    # Matter-scoped namespaces.
    for mid, bundle in world.matters.items():
        if bundle.matter:
            src = str(bundle.matter.path)
            mo = bundle.matter.obj
            st.declare("matter", mo.get("id"), src)
            for side in mo.get("sides", []):
                st.declare("role", side.get("role_id"), src)
            for w in mo.get("witnesses", []):
                st.declare("witness", w.get("id"), src)
            for ex in mo.get("exhibits", []):
                st.declare("exhibit", ex.get("id"), src)
        for f in sorted(bundle.fact_anchors):
            st.declare("fact", f, str(bundle.dir / "facts.md"))
        for pid, p in bundle.personas.items():
            st.declare("persona", pid, str(p.path))
        if bundle.rubric:
            _declare_criteria(bundle.rubric.obj.get("criteria", []), str(bundle.rubric.path), st)
        if bundle.business:
            src = str(bundle.business.path)
            bo = bundle.business.obj
            for te in bo.get("time_entries", []):
                st.declare("time_entry", te.get("id"), src)
            for inv in bo.get("invoices", []):
                st.declare("invoice", inv.get("id"), src)
            for tr in bo.get("trust_entries", []):
                st.declare("trust", tr.get("id"), src)

    return st


def _declare_criteria(criteria, src, st: SymbolTable):
    for c in criteria:
        st.declare("criterion", c.get("id"), src)
        _declare_criteria(c.get("subcriteria", []), src, st)


# ===========================================================================
# Check runner
# ===========================================================================

class Validator:
    def __init__(self, world: World, schemas: SchemaSet, report: Report,
                 strict: bool, online: bool,
                 enforce_day_zero_offsets: bool = ENFORCE_DAY_ZERO_OFFSETS):
        self.world = world
        self.schemas = schemas
        self.report = report
        self.strict = strict
        self.online = online
        self.enforce_day_zero_offsets = enforce_day_zero_offsets
        self.report.day_zero_offset_enforcement = enforce_day_zero_offsets
        self.st: SymbolTable | None = None
        self.declared_holdouts = self._declared_holdout_keys()
        self._resolved_dates: dict[tuple[str, str], date | None] = {}

    def _normal_source(self, source):
        path = Path(source)
        if path.is_absolute():
            try:
                path = path.relative_to(self.world.data_dir)
            except ValueError:
                pass
        value = str(path).replace("\\", "/")
        if "/data/" in value:
            value = value.split("/data/", 1)[1]
        return value.removeprefix("data/")

    def _declared_holdout_keys(self):
        keys = set()
        for artifact in self.world.day_zero_artifacts:
            if artifact.entity_type != "day_zero_holdouts":
                continue
            for entry in artifact.obj.get("entries", []):
                if entry.get("review_status") != DECLARED_HOLDOUT_STATUS:
                    continue
                keys.add((self._normal_source(entry.get("source", "")),
                          entry.get("locator", ""), entry.get("literal")))
        return keys

    def _is_declared_holdout(self, source, locator, literal):
        return (self._normal_source(source), locator, literal) in self.declared_holdouts

    def _resolve_date(self, mid, record, key, source, locator, anchor,
                      convertible=True):
        """Resolve a converter-emitted offset sibling, or an absolute literal."""
        field = (str(source), locator)
        if field in self._resolved_dates:
            return self._resolved_dates[field]
        literal = record.get(key)
        offset_key = key + "_day_zero_offset"
        offset = record.get(offset_key)
        if offset is not None:
            if anchor is None or isinstance(offset, bool) or not isinstance(offset, int):
                self.report.add(mid, "F30", ERROR,
                                f"{mid} cannot resolve {locator} from {offset_key}={offset!r}.")
                self._resolved_dates[field] = None
                return None
            resolved = anchor + timedelta(days=offset)
            absolute = parse_date(literal)
            if absolute is not None and absolute != resolved:
                self.report.add(mid, "F30", ERROR,
                                f"{mid} {locator} literal {literal} disagrees with "
                                f"{offset_key}={offset} (resolves to {resolved}).")
            self.report.record_checked_date(source, locator, used_offset=True)
            self._resolved_dates[field] = resolved
            return resolved

        absolute = parse_date(literal)
        if absolute is None:
            self._resolved_dates[field] = None
            return None
        if (self.enforce_day_zero_offsets and convertible and
                not self._is_declared_holdout(source, locator, literal)):
            self.report.add(mid, "F30", ERROR,
                            f"{mid} {locator} remains absolute without {offset_key}.")
        self.report.record_checked_date(source, locator, used_offset=False)
        self._resolved_dates[field] = absolute
        return absolute

    def _walk_structured_dates(self, mid, value, source, anchor, locator=""):
        if isinstance(value, dict):
            for key, child in value.items():
                child_locator = f"{locator}.{key}" if locator else key
                if (not key.endswith("_day_zero_offset") and
                        parse_date(child) is not None):
                    self._resolve_date(mid, value, key, source, child_locator, anchor)
                elif isinstance(child, (dict, list)):
                    self._walk_structured_dates(mid, child, source, anchor,
                                                child_locator)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_locator = f"{locator}.{index}" if locator else str(index)
                self._walk_structured_dates(mid, child, source, anchor, child_locator)

    @staticmethod
    def _json_value_at(obj, dotted):
        current = obj
        for part in dotted.split(".") if dotted else []:
            current = current[int(part)] if isinstance(current, list) else current[part]
        return current

    def _resolve_sidecar_source(self, mid, entry, index):
        source = self._normal_source(entry.get("source", ""))
        source_path = self.world.data_dir / source
        durable = entry.get("durable_locator")
        literal = entry.get("literal")
        locator = f"entries.{index}"
        try:
            source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self.report.add(mid, "F30", ERROR,
                            f"{mid} date-offsets {locator} source is unreadable: "
                            f"{entry.get('source')!r}.")
            return False

        if durable:
            json_match = DAY_ZERO_JSON_LOCATOR_RE.fullmatch(durable)
            raw_match = DAY_ZERO_RAW_LOCATOR_RE.fullmatch(durable)
            try:
                if json_match:
                    value = self._json_value_at(json.loads(source_text), json_match.group(1))
                    dates = list(DAY_ZERO_FULL_DATE_RE.finditer(value))
                    ordinal = int(json_match.group(2))
                    resolved = dates[ordinal].group(0)
                elif raw_match:
                    requested = int(raw_match.group(2))
                    date_ordinal = int(raw_match.group(3))
                    candidates = []
                    for line in source_text.splitlines():
                        dates = list(DAY_ZERO_FULL_DATE_RE.finditer(line))
                        if not any(item.group(0) == literal for item in dates):
                            continue
                        normalized = " ".join(DAY_ZERO_FULL_DATE_RE.sub("<date>", line).split())
                        candidates.append((hashlib.sha256(normalized.encode()).hexdigest()[:16], dates))
                    fingerprint, dates = candidates[requested - 1]
                    if fingerprint != raw_match.group(1):
                        raise IndexError
                    resolved = dates[date_ordinal].group(0)
                else:
                    raise IndexError
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                resolved = None
            if resolved != literal:
                self.report.add(mid, "F30", ERROR,
                                f"{mid} date-offsets {locator} has stale literal/block "
                                f"identity for {entry.get('source')!r}.")
                return False
            return True

        block_id = str(entry.get("block_id", "")).removeprefix("b:")
        if source_path.suffix == ".md":
            spans = []
            build_site.markdown(source_text, spans=spans)
            spans = [span for span in spans if span.get("bid") == block_id]
            occurrence = entry.get("locator")
            if len(spans) == 1:
                dates = list(DAY_ZERO_FULL_DATE_RE.finditer(spans[0]["raw"]))
                if (isinstance(occurrence, int) and occurrence < len(dates) and
                        dates[occurrence].group(0) == literal):
                    return True
        else:
            marker = "{#b:%s}" % block_id
            if marker in source_text and literal in source_text:
                return True
        self.report.add(mid, "F30", ERROR,
                        f"{mid} date-offsets {locator} has stale literal/block "
                        f"identity for {entry.get('source')!r}.")
        return False

    def _embedded_prose_inventory(self, bundle):
        inventory = defaultdict(int)
        root = bundle.dir
        for path in sorted(root.rglob("*.md")):
            source = self._normal_source(path)
            for match in DAY_ZERO_FULL_DATE_RE.finditer(path.read_text(encoding="utf-8")):
                inventory[(source, match.group(0))] += 1

        def walk(value, source):
            if isinstance(value, dict):
                for child in value.values():
                    walk(child, source)
            elif isinstance(value, list):
                for child in value:
                    walk(child, source)
            elif isinstance(value, str) and parse_date(value) is None:
                for match in DAY_ZERO_FULL_DATE_RE.finditer(value):
                    inventory[(source, match.group(0))] += 1

        for path in sorted(root.rglob("*.json")):
            if path.name == "date-offsets.json":
                continue
            try:
                walk(json.loads(path.read_text(encoding="utf-8")), self._normal_source(path))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
        return inventory

    def _check_day_zero_representation(self, mid, bundle):
        if not bundle.matter or not bundle.matter.schema_ok:
            return
        anchor = parse_date(bundle.matter.obj.get("open_date"))
        loaded = [x for x in (bundle.matter, bundle.rubric, bundle.exercise,
                              bundle.business) if x and x.schema_ok]
        loaded.extend(x for x in bundle.personas.values() if x.schema_ok)
        loaded.extend(x for x in bundle.other if x.schema_ok)
        for item in loaded:
            self._walk_structured_dates(mid, item.obj, item.path, anchor)

        sidecar = bundle.date_offsets
        prose_inventory = self._embedded_prose_inventory(bundle)
        if not sidecar or not sidecar.schema_ok:
            if self.enforce_day_zero_offsets and prose_inventory:
                self.report.add(mid, "F30", ERROR,
                                f"{mid} has convertible prose dates but is missing date-offsets.json.")
            return
        sidecar_anchor = parse_date(sidecar.obj.get("anchor"))
        if sidecar_anchor != anchor:
            self.report.add(mid, "F30", ERROR,
                            f"{mid} date-offsets anchor {sidecar.obj.get('anchor')!r} "
                            f"does not match matter open_date {anchor}.")
            return
        covered = defaultdict(int)
        for index, entry in enumerate(sidecar.obj.get("entries", [])):
            literal = parse_date(entry.get("literal"))
            offset = entry.get("day_zero_offset")
            locator = f"entries.{index}"
            if literal is None or isinstance(offset, bool) or not isinstance(offset, int):
                self.report.add(mid, "F30", ERROR,
                                f"{mid} cannot resolve date-offsets {locator}.")
                continue
            resolved = sidecar_anchor + timedelta(days=offset)
            if literal != resolved:
                self.report.add(mid, "F30", ERROR,
                                f"{mid} date-offsets {locator} literal {entry.get('literal')} "
                                f"disagrees with day_zero_offset={offset} "
                                f"(resolves to {resolved}).")
            if self._resolve_sidecar_source(mid, entry, index):
                covered[(self._normal_source(entry.get("source", "")), entry.get("literal"))] += 1
            self.report.record_checked_date(sidecar.path, locator, used_offset=True)
        if self.enforce_day_zero_offsets:
            held_out = defaultdict(int)
            for source, _locator, literal in self.declared_holdouts:
                held_out[(source, literal)] += 1
            for (source, literal), count in sorted(prose_inventory.items()):
                missing = count - covered[(source, literal)] - held_out[(source, literal)]
                if missing > 0:
                    self.report.add(mid, "F30", ERROR,
                                    f"{mid} {source} literal {literal!r} has no "
                                    f"date-offsets entry ({missing} occurrence(s)).")

    # -- severity helper for cross-ref tolerance -------------------------
    def ref_sev(self):
        return ERROR if self.strict else WARN

    # -- entry point -----------------------------------------------------
    def run(self):
        if not HAVE_JSONSCHEMA:
            self.report.add(
                "GLOBAL", "F29", WARN,
                "jsonschema not importable — degraded to structural checks "
                "(required-keys + id-pattern only). Full schema conformance NOT verified.",
            )
        for path, err in self.world.load_errors:
            self.report.add("GLOBAL", "LOAD", ERROR, f"could not parse {path}: {err}")

        self._check_spine_manifest()
        self.st = build_symbols(self.world, self.report)

        # F29 schema conformance first (short-circuits semantics per file).
        self._schema_pass()

        # A1 global uniqueness / collisions.
        self._check_collisions()

        # Global modules.
        self._check_firm_module()
        self._check_taxonomy_module()

        # Per-matter.
        for mid in sorted(self.world.matters):
            self.report.matters_seen.add(mid)
            self._check_matter(mid)

        # Cross-matter WARN modules.
        self._check_name_collision_sweep()
        self._check_firm_aggregate()

    # -- versioning ------------------------------------------------------
    def _check_spine_manifest(self):
        if not self.world.spine_manifest:
            self.report.add("GLOBAL", "F29", ERROR,
                            "data/spine-manifest.json missing — cannot verify schema_version.")

    def _manifest_version(self, etype):
        if not self.world.spine_manifest:
            return None
        return self.world.spine_manifest.get("schemas", {}).get(etype)

    # -- F29 schema pass -------------------------------------------------
    def _schema_pass(self):
        loaded: list[Loaded] = []
        if self.world.firm:
            loaded.append(self.world.firm)
        loaded.extend(self.world.skills.values())
        loaded.extend(self.world.tasks.values())
        loaded.extend(self.world.page_copies)
        loaded.extend(self.world.day_zero_artifacts)
        loaded.extend(self.world.assessment_instruments)
        for b in self.world.matters.values():
            loaded.extend([x for x in (b.matter, b.rubric, b.exercise, b.business) if x])
            if b.date_offsets:
                loaded.append(b.date_offsets)
            loaded.extend(b.personas.values())
            loaded.extend(b.other)

        for lo in loaded:
            scope = self._scope_of(lo)
            errs = self.schemas.validate(lo.entity_type, lo.obj)
            if errs:
                lo.schema_ok = False
                for loc, msg in errs[:12]:
                    self.report.add(scope, "F29", ERROR,
                                    f"{lo.entity_type} schema fail at '{loc}': {msg}",
                                    {"file": str(lo.path)})
            # schema_version vs manifest
            want = self._manifest_version(lo.entity_type)
            got = lo.obj.get("schema_version") if isinstance(lo.obj, dict) else None
            if want is not None and got is not None and got != want:
                self.report.add(scope, "F29", ERROR,
                                f"{lo.entity_type} schema_version {got!r} != manifest {want!r}",
                                {"file": str(lo.path)})

    def _scope_of(self, lo: Loaded):
        mid = None
        if not isinstance(lo.obj, dict):
            return "GLOBAL"
        if isinstance(lo.obj.get("matter_id"), str):
            mid = lo.obj["matter_id"]
        elif isinstance(lo.obj.get("id"), str):
            mm = RE["mNN_prefix"].match(lo.obj["id"])
            if mm:
                mid = mm.group(1)
        return mid if mid in self.world.matters else ("GLOBAL" if mid is None else mid)

    # -- A1 collisions ---------------------------------------------------
    def _check_collisions(self):
        for namespace, _id, sources in self.st.collisions():
            # manifest + matter both declaring the matter id is expected, not a collision
            uniq = sorted(set(sources))
            if len(uniq) < 2:
                continue
            if namespace == "matter" and any(s.startswith("manifest:") for s in uniq):
                continue
            if namespace == "client" and any(s.startswith("manifest:") for s in uniq):
                # manifest + firm both list the client — only a collision if two firm files
                non_manifest = [s for s in uniq if not s.startswith("manifest:")]
                if len(non_manifest) < 2:
                    continue
            self.report.add("GLOBAL", "A1", ERROR,
                            f"duplicate id '{_id}' in namespace '{namespace}'",
                            {"sources": uniq})

    # -- Firm module -----------------------------------------------------
    def _check_firm_module(self):
        if not self.world.firm:
            self.report.add("FIRM", "FIRM", WARN, "firm dataset not present (authored first; not yet found).")
            self.report.modules_seen.add("FIRM")
            return
        self.report.modules_seen.add("FIRM")
        fobj = self.world.firm.obj
        # book_of_business fee_type / client_id agreement vs manifest (per-matter deeper check in matter loop)
        book = {b.get("matter_id"): b for b in fobj.get("book_of_business", [])}
        for mid, m in self.world.manifest_index.items():
            b = book.get(mid)
            if b is None:
                self.report.add("FIRM", "B11", WARN, f"firm book_of_business has no entry for {mid}.")
                continue
            if b.get("client_id") != m.get("client_id"):
                self.report.add("FIRM", "B11", WARN,
                                f"{mid} client_id firm={b.get('client_id')} manifest={m.get('client_id')}")
            if b.get("fee_type") != m.get("fee_type"):
                self.report.add("FIRM", "B11", WARN,
                                f"{mid} fee_type firm={b.get('fee_type')} manifest={m.get('fee_type')}")

    # -- Taxonomy module (E23-E28) --------------------------------------
    def _check_taxonomy_module(self):
        skills = self.world.skills
        tasks = self.world.tasks
        if not skills and not tasks:
            self.report.add("TAXONOMY", "E24", WARN,
                            "taxonomy (skills/tasks) not present yet — E23-E28 skipped (partial-spine).")
            self.report.modules_seen.add("TAXONOMY")
            return
        self.report.modules_seen.add("TAXONOMY")

        # E23 FOLIO mapping XOR + format.
        for coll in (skills, tasks):
            for _id, lo in coll.items():
                if not lo.schema_ok:
                    continue
                self._check_folio(lo, "TAXONOMY")

        # E24/E25 surveyed-26 coverage (exclude extensions).
        non_ext = [s for s in skills.values() if not s.obj.get("extension")]
        present_names = [norm_name(s.obj.get("name", "")) for s in non_ext]
        for group in SURVEYED_SKILLS:
            accepted = {norm_name(n) for n in group}
            if not (accepted & set(present_names)):
                canonical = sorted(group)[0]
                self.report.add("TAXONOMY", "E24", ERROR,
                                f"surveyed skill not present: '{canonical}'")
        # names not matching any surveyed phrasing
        all_accepted = set()
        for group in SURVEYED_SKILLS:
            all_accepted |= {norm_name(n) for n in group}
        for s in non_ext:
            nm = norm_name(s.obj.get("name", ""))
            alt = norm_name(s.obj.get("alt_name", "")) if s.obj.get("alt_name") else None
            if nm not in all_accepted and (alt is None or alt not in all_accepted):
                self.report.add("TAXONOMY", "E24", WARN,
                                f"skill {s.obj.get('id')} name '{s.obj.get('name')}' not an exact survey phrasing.")
        if len(non_ext) != len(SURVEYED_SKILLS):
            self.report.add("TAXONOMY", "E25", WARN,
                            f"non-extension skill count = {len(non_ext)} (expected {len(SURVEYED_SKILLS)}).")

        # E27 task hierarchy: every task -> exactly one existing skill.
        for tid, t in tasks.items():
            if not t.schema_ok:
                continue
            skid = t.obj.get("skill_id")
            if not self.st.has("skill", skid):
                self.report.add("TAXONOMY", "E27", self.ref_sev(),
                                f"task {tid} references unknown skill_id {skid}.")

    def _check_folio(self, lo: Loaded, scope):
        obj = lo.obj
        has_folio = "folio" in obj
        has_none = obj.get("no_folio_equivalent") is True
        if has_folio == has_none:  # both or neither
            self.report.add(scope, "E23", ERROR,
                            f"{obj.get('id')} must carry exactly one of folio{{}} XOR no_folio_equivalent:true.")
            return
        if has_folio:
            folio = obj.get("folio", {})
            iri = folio.get("iri", "")
            if not RE["folio_iri"].match(iri or ""):
                self.report.add(scope, "E23", ERROR,
                                f"{obj.get('id')} folio.iri malformed: {iri!r}")
            if folio.get("mapping_confidence") not in ("exact", "near", "parent"):
                self.report.add(scope, "E23", ERROR,
                                f"{obj.get('id')} folio.mapping_confidence invalid: "
                                f"{folio.get('mapping_confidence')!r}")
            if self.online:
                self._check_folio_online(lo, scope, iri)

    def _check_folio_online(self, lo, scope, iri):
        crosswalk = self.world.data_dir / "taxonomy" / "folio-crosswalk.json"
        if not crosswalk.exists():
            self.report.add(scope, "E23", WARN,
                            "--online requested but data/taxonomy/folio-crosswalk.json snapshot absent; "
                            "existence check skipped (never live MCP).")
            return
        obj, err = read_json(crosswalk)
        if err or not obj:
            self.report.add(scope, "E23", WARN, "folio-crosswalk.json unreadable; existence check skipped.")
            return
        known = set(obj.get("iris", obj if isinstance(obj, list) else []))
        bare = iri.rsplit("/", 1)[-1]
        if iri not in known and bare not in known:
            self.report.add(scope, "E23", WARN,
                            f"{lo.obj.get('id')} FOLIO IRI {iri} not found in crosswalk snapshot.")

    # -- Per-matter ------------------------------------------------------
    def _check_matter(self, mid):
        bundle = self.world.matters[mid]
        m = self.world.manifest_index.get(mid)
        mo = bundle.matter.obj if bundle.matter else None

        self._check_day_zero_representation(mid, bundle)
        self._check_prefix_bleed(bundle)
        if mo and bundle.matter.schema_ok:
            self._check_a5_manifest(mid, mo, m)
            self._check_matter_refs(mid, bundle)
            self._check_bidirectional(mid, bundle)
            self._check_d1_chain(mid, bundle)
            self._check_dates_matter(mid, bundle)
        self._check_personas(mid, bundle)
        self._check_rubric(mid, bundle, m)
        self._check_business(mid, bundle, m)
        self._check_depth_floor(mid, bundle, m)

    # A/§1 foreign-prefix bleed
    def _check_prefix_bleed(self, bundle: MatterBundle):
        mine = bundle.id
        loaded = [x for x in (bundle.matter, bundle.rubric, bundle.exercise, bundle.business) if x]
        loaded += list(bundle.personas.values()) + bundle.other
        seen = set()
        for lo in loaded:
            for s in walk_strings(lo.obj):
                mm = RE["mNN_prefix"].match(s)
                if mm and mm.group(1) != mine:
                    key = (mm.group(1), s, str(lo.path))
                    if key in seen:
                        continue
                    seen.add(key)
                    self.report.add(bundle.id, "A1", ERROR,
                                    f"foreign matter-prefix id '{s}' ({mm.group(1)}) bled into "
                                    f"{bundle.id} file {lo.path.name}.")

    # A5 manifest agreement
    def _check_a5_manifest(self, mid, mo, m):
        if not m:
            self.report.add(mid, "A5", ERROR, f"{mid} not present in data/matters/manifest.json.")
            return
        # tier: manifest 'fictional' <-> schema 'meridian'
        man_tier = m.get("tier")
        want_tier = "meridian" if man_tier == "fictional" else man_tier
        checks = [
            ("slug", mo.get("slug"), m.get("slug")),
            ("jurisdiction", mo.get("jurisdiction"), m.get("jurisdiction")),
            ("fee_type", mo.get("fee_type"), m.get("fee_type")),
            ("client_id", mo.get("client_id"), m.get("client_id")),
            ("tier", mo.get("tier"), want_tier),
        ]
        for field, got, want in checks:
            if got != want:
                self.report.add(mid, "A5", ERROR,
                                f"{mid}.{field}={got!r} disagrees with frozen manifest {want!r}.")

    # A2 cross-ref resolution
    def _check_matter_refs(self, mid, bundle):
        mo = bundle.matter.obj
        for skid in mo.get("skill_refs", []):
            if not self.st.has("skill", skid):
                self.report.add(mid, "A2", self.ref_sev(),
                                f"{mid} skill_ref {skid} does not resolve in taxonomy.")
        for tkid in mo.get("task_refs", []):
            if not self.st.has("task", tkid):
                self.report.add(mid, "A2", self.ref_sev(),
                                f"{mid} task_ref {tkid} does not resolve in taxonomy.")
        cid = mo.get("client_id")
        if cid and not self.st.has("client", cid):
            self.report.add(mid, "A2", self.ref_sev(),
                            f"{mid} client_id {cid} does not resolve to a firm client.")

    # A4 bidirectional listings
    def _check_bidirectional(self, mid, bundle):
        mo = bundle.matter.obj
        listed = set(mo.get("personas", []))
        actual = set(bundle.personas.keys())
        for pid in listed - actual:
            self.report.add(mid, "A4", ERROR,
                            f"{mid} lists persona {pid} but no such persona file present.")
        for pid in actual - listed:
            self.report.add(mid, "A4", ERROR,
                            f"persona {pid} present but not listed in {mid}.matter.personas.")

    # A3 D1 chain
    def _check_d1_chain(self, mid, bundle):
        mo = bundle.matter.obj
        # ordered links: skill -> task -> exercise -> matter -> rubric -> persona
        if not any(self.st.has("skill", s) for s in mo.get("skill_refs", [])):
            self.report.add(mid, "A3", self.ref_sev(),
                            f"{mid} D1 chain broken at skill: no skill_ref resolves.")
            return
        if mo.get("task_refs") and not any(self.st.has("task", t) for t in mo["task_refs"]):
            self.report.add(mid, "A3", self.ref_sev(),
                            f"{mid} D1 chain broken at task: no task_ref resolves.")
            return
        if not bundle.exercise:
            self.report.add(mid, "A3", ERROR, f"{mid} D1 chain broken at exercise: no exercise packet.")
            return
        if not bundle.rubric:
            self.report.add(mid, "A3", ERROR, f"{mid} D1 chain broken at rubric: no rubric.")
            return
        if not bundle.personas:
            self.report.add(mid, "A3", ERROR, f"{mid} D1 chain broken at persona: no personas.")

    # B12 date sanity (matter-level)
    def _check_dates_matter(self, mid, bundle):
        mo = bundle.matter.obj
        anchor = parse_date(mo.get("open_date"))
        open_d = self._resolve_date(mid, mo, "open_date", bundle.matter.path,
                                    "open_date", anchor)
        as_of = self._resolve_date(mid, mo, "as_of_date", bundle.matter.path,
                                   "as_of_date", anchor)
        if as_of and open_d and open_d > as_of:
            self.report.add(mid, "B12", ERROR, f"{mid} open_date {open_d} after as_of_date {as_of}.")

    # C13-C17 personas
    def _check_personas(self, mid, bundle):
        for pid, lo in sorted(bundle.personas.items()):
            if not lo.schema_ok:
                continue  # schema failure short-circuits semantics for this file
            p = lo.obj
            # C16 interviewable_by roles exist in matter.sides
            for role in p.get("interviewable_by", []):
                if not self.st.has("role", role):
                    self.report.add(mid, "C16", ERROR,
                                    f"persona {pid} interviewable_by {role} not a side in {mid}.matter.sides.")
            # C13 disclosure fact_refs resolve; concealed/rapport-gated dangling = ERROR always
            disclosure = p.get("disclosure", {})
            material_texts = []
            for tier in ("volunteered", "revealed_if_asked", "rapport_gated", "concealed", "unknown"):
                hard = tier in ("concealed", "rapport_gated")
                for item in disclosure.get(tier, []):
                    fr = item.get("fact_ref")
                    if tier in ("concealed", "rapport_gated", "revealed_if_asked"):
                        material_texts.append(norm_name(item.get("text", "")))
                    if fr and fr not in bundle.fact_anchors:
                        sev = ERROR if hard else self.ref_sev()
                        self.report.add(mid, "C13", sev,
                                        f"persona {pid} {tier} fact_ref {fr} unresolved in {mid}/facts.md.")
                    # C15 rapport triggers (belt-and-suspenders vs schema enum)
                    if tier == "rapport_gated":
                        for trg in item.get("requires", []):
                            if trg not in TRIGGER_VOCAB:
                                self.report.add(mid, "C15", ERROR,
                                                f"persona {pid} rapport trigger '{trg}' outside closed vocabulary.")
            # C14 color_topics disjoint from material facts
            kb = p.get("knowledge_boundary", {})
            for topic in kb.get("color_topics", []):
                nt = norm_name(topic)
                if len(nt) < 12:
                    continue
                for mt in material_texts:
                    if mt and (nt in mt or mt in nt):
                        self.report.add(mid, "C14", ERROR,
                                        f"persona {pid} color_topic '{topic}' overlaps a material fact.")
                        break
            # C18 layperson heuristic: statute/case citations in persona text
            if self._has_citation(p):
                self.report.add(mid, "C18", WARN,
                                f"persona {pid} text appears to contain a statute/case citation (layperson review).")

    def _has_citation(self, persona):
        blob = " ".join(
            str(persona.get(k, "")) for k in
            ("background", "personality", "emotional_state", "communication_style")
        )
        for item in walk_strings(persona.get("disclosure", {})):
            blob += " " + item
        return bool(re.search(r"§|v\.\s+[A-Z]|\bU\.S\.C\.|Stat\.\s*§|\bF\.\dd\b|\bId\.\b", blob))

    # D19-D21 rubric
    def _check_rubric(self, mid, bundle, m):
        if not bundle.rubric or not bundle.rubric.schema_ok:
            return
        r = bundle.rubric.obj
        declared = dec(r.get("declared_total", 0))
        top_sum = sum((dec(c.get("weight_points", 0)) for c in r.get("criteria", [])), Decimal(0))
        if abs(top_sum - declared) > CENT:
            self.report.add(mid, "D19", ERROR,
                            f"{mid} rubric Σ(criteria)={top_sum} != declared_total={declared}.")
        # subcriteria sum to parent
        self._check_subcriteria(mid, r.get("criteria", []))
        # pinned exercise total by shape
        shape_key = m.get("shape") if m else None
        pin = SHAPE_EXERCISE_TOTAL.get(shape_key)
        if pin is not None and abs(declared - pin) > CENT:
            self.report.add(mid, "D19", ERROR,
                            f"{mid} rubric declared_total={declared} != master-outline pin {pin} "
                            f"for shape '{shape_key}'.")
        # D20 criterion skill/task resolve
        self._check_criteria_refs(mid, r.get("criteria", []))
        # D21 letter grade map monotonic
        lgm = r.get("letter_grade_map")
        if lgm:
            pts = [dec(x.get("points", 0)) for x in lgm]
            if any(pts[i] < pts[i + 1] for i in range(len(pts) - 1)):
                self.report.add(mid, "D21", ERROR, f"{mid} letter_grade_map points not monotonic (descending).")

    def _check_subcriteria(self, mid, criteria):
        for c in criteria:
            subs = c.get("subcriteria", [])
            if subs:
                s = sum((dec(x.get("weight_points", 0)) for x in subs), Decimal(0))
                if abs(s - dec(c.get("weight_points", 0))) > CENT:
                    self.report.add(mid, "D19", ERROR,
                                    f"{mid} criterion {c.get('id')} subcriteria Σ={s} != "
                                    f"weight_points={c.get('weight_points')}.")
                self._check_subcriteria(mid, subs)

    def _check_criteria_refs(self, mid, criteria):
        for c in criteria:
            skid = c.get("skill_id")
            tkid = c.get("task_id")
            if skid and not self.st.has("skill", skid):
                self.report.add(mid, "D20", self.ref_sev(),
                                f"{mid} criterion {c.get('id')} skill_id {skid} unresolved.")
            if tkid and not self.st.has("task", tkid):
                self.report.add(mid, "D20", self.ref_sev(),
                                f"{mid} criterion {c.get('id')} task_id {tkid} unresolved.")
            self._check_criteria_refs(mid, c.get("subcriteria", []))

    # B7-B12 money
    def _check_business(self, mid, bundle, m):
        if not bundle.business or not bundle.business.schema_ok:
            return
        b = bundle.business.obj
        fee_type = b.get("engagement", {}).get("fee_type")
        # B8 fee_type agreement with matter + manifest
        if m and fee_type and fee_type != m.get("fee_type"):
            self.report.add(mid, "B8", ERROR,
                            f"{mid} business fee_type {fee_type} != manifest {m.get('fee_type')}.")

        rate_set = self._firm_rate_set()
        eng_rate = b.get("engagement", {}).get("rate")
        allowed_rates = set(rate_set)
        if eng_rate is not None:
            allowed_rates.add(dec(eng_rate))

        # B7 rate-card consistency + hours-in-0.1
        for te in b.get("time_entries", []):
            h = te.get("hours")
            if h is not None and not is_tenth(h):
                self.report.add(mid, "B8h", ERROR,
                                f"{mid} time entry {te.get('id')} hours={h} not a 0.1 increment "
                                "(Decimal-checked; schemas omit multipleOf:0.1 by design).")
            rate = te.get("rate")
            if rate is not None and allowed_rates and dec(rate) not in allowed_rates:
                if rate_set:
                    self.report.add(mid, "B7", ERROR,
                                    f"{mid} time entry {te.get('id')} rate {rate} matches neither the firm "
                                    f"rate card nor a declared engagement rate.")
                else:
                    self.report.add(mid, "B7", WARN,
                                    f"{mid} time entry {te.get('id')} rate {rate} unverifiable (firm rate card absent).")

        te_by_id = {
            te.get("id"): (index, te)
            for index, te in enumerate(b.get("time_entries", []))
        }

        # B9 invoice arithmetic + hourly fee tie-out
        anchor = parse_date(bundle.matter.obj.get("open_date")) if bundle.matter else None
        for inv_index, inv in enumerate(b.get("invoices", [])):
            fees = inv.get("fees")
            exp = inv.get("expenses")
            pay = inv.get("payments_received")
            bal = inv.get("balance_due")
            if None not in (fees, exp, pay, bal):
                expect = dec(fees) + dec(exp) - dec(pay)
                if abs(expect - dec(bal)) > CENT:
                    self.report.add(mid, "B9", ERROR,
                                    f"{mid} invoice {inv.get('id')} balance_due={bal} != "
                                    f"fees+expenses-payments={expect}.")
            if fee_type == "hourly":
                line_sum = Decimal(0)
                for ref in inv.get("line_refs", []):
                    indexed_te = te_by_id.get(ref)
                    te = indexed_te[1] if indexed_te else None
                    if te and te.get("hours") is not None and te.get("rate") is not None:
                        line_sum += dec(te["hours"]) * dec(te["rate"])
                if inv.get("line_refs") and fees is not None and abs(line_sum - dec(fees)) > CENT:
                    self.report.add(mid, "B8", ERROR,
                                    f"{mid} invoice {inv.get('id')} fees={fees} != Σ(hours×rate) over "
                                    f"line_refs={line_sum}.")
            # B12 invoice date >= latest billed entry
            inv_d = self._resolve_date(mid, inv, "date", bundle.business.path,
                                       f"invoices.{inv_index}.date", anchor)
            latest = None
            for ref in inv.get("line_refs", []):
                indexed_te = te_by_id.get(ref)
                if indexed_te:
                    te_index, te = indexed_te
                    d = self._resolve_date(mid, te, "date", bundle.business.path,
                                           f"time_entries.{te_index}.date", anchor)
                else:
                    d = None
                if d and (latest is None or d > latest):
                    latest = d
            if inv_d and latest and inv_d < latest:
                self.report.add(mid, "B12", ERROR,
                                f"{mid} invoice {inv.get('id')} date {inv_d} precedes latest billed entry {latest}.")

        # B8 fee-type ↔ structure specifics
        eng = b.get("engagement", {})
        if fee_type == "retainer":
            ra = eng.get("retainer_amount")
            deposits = [dec(t["amount"]) for t in b.get("trust_entries", []) if t.get("type") == "deposit"]
            if ra is not None and not any(abs(d - dec(ra)) <= CENT for d in deposits):
                self.report.add(mid, "B8", ERROR,
                                f"{mid} retainer engagement lacks a matching trust deposit of {ra}.")
        if fee_type == "flat":
            ff = eng.get("flat_fee")
            fee_lines = [dec(inv.get("fees")) for inv in b.get("invoices", []) if inv.get("fees") is not None]
            if ff is not None and fee_lines and not any(abs(f - dec(ff)) <= CENT for f in fee_lines):
                self.report.add(mid, "B8", WARN,
                                f"{mid} flat fee {ff} not reflected as a fixed fee line on any invoice.")

        # B10 trust ledger (date-ordered running balance, never negative)
        self._check_trust(mid, bundle, b)

        # B12 nothing after as_of_date
        as_of = None
        if bundle.matter:
            mo = bundle.matter.obj
            matter_anchor = parse_date(mo.get("open_date"))
            as_of = self._resolve_date(mid, mo, "as_of_date", bundle.matter.path,
                                       "as_of_date", matter_anchor)
        if as_of:
            for index, te in enumerate(b.get("time_entries", [])):
                d = self._resolve_date(mid, te, "date", bundle.business.path,
                                       f"time_entries.{index}.date", anchor)
                if d and d > as_of:
                    self.report.add(mid, "B12", ERROR,
                                    f"{mid} time entry {te.get('id')} dated {d} after as_of_date {as_of}.")

    def _firm_rate_set(self):
        rates = set()
        if self.world.firm:
            f = self.world.firm.obj
            for tk in f.get("timekeepers", []):
                if tk.get("rate") is not None:
                    rates.add(dec(tk["rate"]))
            for rc in f.get("rate_card", []):
                if rc.get("rate") is not None:
                    rates.add(dec(rc["rate"]))
        return rates

    def _check_trust(self, mid, bundle, b):
        entries = b.get("trust_entries", [])
        if not entries:
            return
        anchor = parse_date(bundle.matter.obj.get("open_date")) if bundle.matter else None
        resolved = []
        for index, entry in enumerate(entries):
            entry_date = self._resolve_date(mid, entry, "date", bundle.business.path,
                                            f"trust_entries.{index}.date", anchor)
            resolved.append((entry_date or date.max, entry.get("id") or "", entry))
        ordered = [entry for _, _, entry in sorted(resolved, key=lambda row: row[:2])]
        running = Decimal(0)
        invoices = {inv.get("id"): inv for inv in b.get("invoices", [])}
        for t in ordered:
            amt = dec(t.get("amount", 0))
            if t.get("type") == "deposit":
                running += amt
            else:
                running -= amt
                # firm disbursement corresponds to an issued invoice ≤ earned
                rid = t.get("related_invoice_id")
                if rid:
                    inv = invoices.get(rid)
                    if inv is None:
                        self.report.add(mid, "B10", self.ref_sev(),
                                        f"{mid} trust disbursement {t.get('id')} references unknown invoice {rid}.")
                    elif inv.get("fees") is not None and inv.get("expenses") is not None:
                        earned = dec(inv["fees"]) + dec(inv["expenses"])
                        if amt - earned > CENT:
                            self.report.add(mid, "B10", ERROR,
                                            f"{mid} trust disbursement {t.get('id')} {amt} exceeds invoice "
                                            f"{rid} earned {earned}.")
            if running < -CENT:
                self.report.add(mid, "B10", ERROR,
                                f"{mid} trust running balance goes negative ({running}) at entry {t.get('id')}.")
            stated = t.get("running_balance")
            if stated is not None and abs(dec(stated) - running) > CENT:
                self.report.add(mid, "B10", ERROR,
                                f"{mid} trust entry {t.get('id')} stated running_balance {stated} != "
                                f"recomputed {running}.")

    # Depth floor (content-style-guide §3) — ERROR below floor.
    def _check_depth_floor(self, mid, bundle, m):
        mo = bundle.matter.obj if bundle.matter else None
        if mo is None:
            self.report.add(mid, "DEPTH", ERROR, f"{mid} has no matter.json — cannot meet depth floor.")
            return

        witnesses = mo.get("witnesses", [])
        if len(witnesses) < DEPTH_MIN_WITNESSES:
            self.report.add(mid, "DEPTH", ERROR,
                            f"{mid} depth floor: {len(witnesses)} witnesses < {DEPTH_MIN_WITNESSES}.")
        exhibits = mo.get("exhibits", [])
        if len(exhibits) < DEPTH_MIN_EXHIBITS:
            self.report.add(mid, "DEPTH", ERROR,
                            f"{mid} depth floor: {len(exhibits)} exhibits < {DEPTH_MIN_EXHIBITS}.")

        personas = list(bundle.personas.values())
        if len(personas) < DEPTH_MIN_PERSONAS:
            self.report.add(mid, "DEPTH", ERROR,
                            f"{mid} depth floor: {len(personas)} personas < {DEPTH_MIN_PERSONAS}.")
        has_client = any("client" in norm_name(p.obj.get("identity", {}).get("role", "")) for p in personas)
        if personas and not has_client:
            self.report.add(mid, "DEPTH", ERROR,
                            f"{mid} depth floor: no persona has role 'client' (interview_focus side).")
        rapport_ok = any(
            len(p.obj.get("disclosure", {}).get("rapport_gated", [])) >= DEPTH_MIN_RAPPORT_FACTS
            for p in personas
        )
        if personas and not rapport_ok:
            self.report.add(mid, "DEPTH", ERROR,
                            f"{mid} depth floor: no persona carries ≥{DEPTH_MIN_RAPPORT_FACTS} rapport-gated facts.")

        # facts.md word range + anchors
        if not bundle.facts_present:
            self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: facts.md missing.")
        else:
            if not (DEPTH_FACTS_MIN_WORDS <= bundle.facts_words <= DEPTH_FACTS_MAX_WORDS):
                self.report.add(mid, "DEPTH", ERROR,
                                f"{mid} depth floor: facts.md {bundle.facts_words} words outside "
                                f"{DEPTH_FACTS_MIN_WORDS}-{DEPTH_FACTS_MAX_WORDS}.")
            if not bundle.fact_anchors:
                self.report.add(mid, "DEPTH", ERROR,
                                f"{mid} depth floor: facts.md has no [mNN.fact.NNN] anchors.")

        # 8 sections present & non-trivial
        if bundle.exercise and bundle.exercise.schema_ok:
            sections = bundle.exercise.obj.get("sections", {})
            for key in SECTION_KEYS:
                sec = sections.get(key)
                if sec is None:
                    self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: exercise section '{key}' missing.")
                    continue
                wc = word_count(sec.get("body_md", ""))
                has_files = bool(sec.get("files"))
                if wc < DEPTH_SECTION_MIN_WORDS and not has_files:
                    self.report.add(mid, "DEPTH", ERROR,
                                    f"{mid} depth floor: section '{key}' only {wc} words (<{DEPTH_SECTION_MIN_WORDS}) "
                                    f"and no files.")
        elif not bundle.exercise:
            self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: no exercise packet (8 sections).")

        # Business layer complete
        self._check_business_completeness(mid, bundle, m)

    def _check_business_completeness(self, mid, bundle, m):
        if not bundle.business:
            self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: business layer absent.")
            return
        b = bundle.business.obj
        eng = b.get("engagement", {})
        fee_type = eng.get("fee_type") or (m.get("fee_type") if m else None)
        if fee_type == "hourly":
            if not b.get("time_entries"):
                self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: hourly matter has no time entries.")
            if not b.get("invoices"):
                self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: hourly matter has no invoices.")
            if eng.get("rate") is None:
                self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: hourly engagement missing rate.")
        elif fee_type == "contingency" and eng.get("contingency_pct") is None:
            self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: contingency engagement missing contingency_pct.")
        elif fee_type == "flat" and eng.get("flat_fee") is None:
            self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: flat engagement missing flat_fee.")
        elif fee_type == "retainer":
            if eng.get("retainer_amount") is None:
                self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: retainer engagement missing retainer_amount.")
            if not b.get("trust_entries"):
                self.report.add(mid, "DEPTH", ERROR, f"{mid} depth floor: retainer matter has no trust entries.")

    # -- Cross-matter WARN modules --------------------------------------
    def _check_name_collision_sweep(self):
        """A6: WARN-level review table of surname reuse across matters + vs Meridian canon."""
        self.report.modules_seen.add("NAME-SWEEP")
        # Build surname -> matters from live matter data (parties/personas/witnesses)
        by_surname: dict[str, set[str]] = defaultdict(set)
        for mid, bundle in self.world.matters.items():
            if not bundle.matter:
                continue
            mo = bundle.matter.obj
            names = []
            for party in mo.get("parties", []):
                names.append(party.get("name", ""))
            for side in mo.get("sides", []):
                names.extend(side.get("party_names", []))
            for w in mo.get("witnesses", []):
                names.append(w.get("name", ""))
            for p in bundle.personas.values():
                names.append(p.obj.get("identity", {}).get("name", ""))
            for nm in names:
                sn = norm_name(surname_of(nm))
                if sn:
                    by_surname[sn].add(mid)

        # Cross-matter reuse vs the frozen ledger.
        ledger_owner: dict[str, str] = {}
        for owner, surnames in self.world.surname_ledger.items():
            for sn in surnames:
                ledger_owner[norm_name(sn)] = owner

        for sn, mids in sorted(by_surname.items()):
            if len(mids) > 1:
                self.report.add("NAME-SWEEP", "A6", WARN,
                                f"surname '{sn}' appears in multiple matters: {sorted(mids)}.")
            owner = ledger_owner.get(sn)
            for mid in sorted(mids):
                if owner and owner != mid:
                    self.report.add("NAME-SWEEP", "A6", WARN,
                                    f"{mid} uses surname '{sn}' owned by {owner} in the frozen ledger.")
            if sn in self.world.meridian_reserved:
                self.report.add("NAME-SWEEP", "A6", WARN,
                                f"surname '{sn}' collides with a Meridian judge/county/city name.")

        # Extra-scrutiny flag for discipline (m02/m12) + DWI (m05/m15).
        for mid in ("m02", "m12", "m05", "m15"):
            if mid in self.world.matters:
                self.report.add("NAME-SWEEP", "A6", WARN,
                                f"{mid} is a discipline/DWI shape — names flagged for manual defamation review.")

    def _check_firm_aggregate(self):
        """B11: recompute firm aggregates from the matters; WARN-only (evidence-pack)."""
        if not self.world.firm:
            return
        self.report.modules_seen.add("FIRM-AGG")
        f = self.world.firm.obj
        total_ar = Decimal(0)
        for mid, bundle in self.world.matters.items():
            if not bundle.business:
                continue
            for inv in bundle.business.obj.get("invoices", []):
                if inv.get("balance_due") is not None:
                    total_ar += dec(inv["balance_due"])
        aging = f.get("ar_aging", {})
        stated = sum((dec(v) for v in aging.values()), Decimal(0)) if aging else None
        if stated is not None and self.world.matters:
            if abs(stated - total_ar) > CENT:
                self.report.add("FIRM-AGG", "B11", WARN,
                                f"firm AR aging Σ={stated} != Σ(matter invoice balances)={total_ar} "
                                f"(aggregate reconciliation; advisory).")


# ===========================================================================
# Output
# ===========================================================================

def emit_human(world: World, report: Report, strict: bool) -> None:
    print("=" * 72)
    print("Sonsteng data-spine integrity gate")
    print(f"data dir : {world.data_dir}")
    print(f"mode     : {'STRICT (ship gate)' if strict else 'lenient (fleet run)'}"
          f"{'  [jsonschema]' if HAVE_JSONSCHEMA else '  [DEGRADED: structural only]'}")
    print(f"day zero : checked_dates={report.checked_dates} "
          f"(offset={report.offset_dates_checked}); "
          f"offset enforcement={'ON' if report.day_zero_offset_enforcement else 'OFF'}")
    print("=" * 72)

    print("\nPER-MATTER")
    if not report.matters_seen:
        print("  (no matter directories found under data/matters/*)")
    for mid in sorted(report.matters_seen):
        e, w = report.counts(mid)
        status = "PASS" if e == 0 else "FAIL"
        extra = f" ({e} ERROR, {w} WARN)" if (e or w) else ""
        print(f"  {mid}  {status}{extra}")
        for f in sorted(report.for_scope(mid), key=lambda x: x.sort_key()):
            print(f"        [{f.severity:5}] {f.check:5} {f.message}")

    print("\nGLOBAL MODULES")
    for scope in ("GLOBAL", "FIRM", "TAXONOMY", "NAME-SWEEP", "FIRM-AGG"):
        fs = report.for_scope(scope)
        if not fs and scope not in report.modules_seen:
            continue
        e = sum(1 for f in fs if f.severity == ERROR)
        w = sum(1 for f in fs if f.severity == WARN)
        status = "PASS" if e == 0 else "FAIL"
        print(f"  {scope}  {status} ({e} ERROR, {w} WARN)")
        for f in sorted(fs, key=lambda x: x.sort_key()):
            print(f"        [{f.severity:5}] {f.check:5} {f.message}")

    te = sum(1 for f in report.findings if f.severity == ERROR)
    tw = sum(1 for f in report.findings if f.severity == WARN)
    print("\n" + "-" * 72)
    print(f"TOTAL: {te} ERROR, {tw} WARN across "
          f"{len(report.matters_seen)} matter(s) + {len(report.modules_seen)} module(s)")
    print("RESULT:", "FAIL (errors block ship)" if te else "PASS")
    print("-" * 72)


def build_json_report(world: World, report: Report, strict: bool) -> dict:
    matters = {}
    for mid in sorted(report.matters_seen):
        e, w = report.counts(mid)
        matters[mid] = {
            "status": "PASS" if e == 0 else "FAIL",
            "errors": e,
            "warnings": w,
            "findings": [f.to_dict() for f in sorted(report.for_scope(mid), key=lambda x: x.sort_key())],
        }
    modules = {}
    for scope in ("GLOBAL", "FIRM", "TAXONOMY", "NAME-SWEEP", "FIRM-AGG"):
        fs = report.for_scope(scope)
        if not fs:
            continue
        e = sum(1 for f in fs if f.severity == ERROR)
        modules[scope] = {
            "status": "PASS" if e == 0 else "FAIL",
            "findings": [f.to_dict() for f in sorted(fs, key=lambda x: x.sort_key())],
        }
    return {
        "tool": "validate_spine.py",
        "strict": strict,
        "jsonschema": HAVE_JSONSCHEMA,
        "data_dir": str(world.data_dir),
        "totals": {
            "errors": sum(1 for f in report.findings if f.severity == ERROR),
            "warnings": sum(1 for f in report.findings if f.severity == WARN),
            "checked_dates": report.checked_dates,
            "offset_dates_checked": report.offset_dates_checked,
        },
        "day_zero_offset_enforcement": report.day_zero_offset_enforcement,
        "result": "FAIL" if report.has_errors() else "PASS",
        "matters": matters,
        "modules": modules,
    }


# ===========================================================================
# CLI
# ===========================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate_spine.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Integrity gate for the Sonsteng data spine.\n\n"
            f"Implements the {VALIDATOR_CHECK_COUNT} checks in "
            "docs/research/validator-spec.md plus the\n"
            "countable depth floor from docs/content-style-guide.md. Runs per-matter\n"
            "in isolation (one broken matter never blocks the other 19), builds a\n"
            "two-pass namespaced symbol table, does all money math in Decimal, and\n"
            "emits a deterministic sorted report. Exits non-zero on any ERROR.\n\n"
            "Severity model:\n"
            "  ERROR (blocks ship): schema, referential, per-matter money,\n"
            "                       persona fact-fidelity, depth floor.\n"
            "  WARN  (advisory)   : matter<->firm aggregate reconciliation,\n"
            "                       name-collision sweep, unresolved-target under\n"
            "                       lenient mode, layperson-citation heuristic.\n\n"
            "Modes:\n"
            "  (default)   lenient fleet run: an unresolved cross-ref target is a WARN.\n"
            "  --strict    ship gate: unresolved cross-ref targets become ERRORs.\n"
            "  --matter mNN  validate ONE matter in isolation (a fleet agent's self-gate).\n"
            "  --online    FOLIO IRI existence vs data/taxonomy/folio-crosswalk.json\n"
            "              (a local snapshot; never live MCP). IRI *format* is always\n"
            "              checked offline.\n"
            "  Day Zero additive offsets are resolved and counted; converted-\n"
            "  representation enforcement is opt-in with --enforce-day-zero-offsets.\n"
        ),
        epilog=(
            "Examples:\n"
            "  python3 tools/validate_spine.py\n"
            "  python3 tools/validate_spine.py --strict --json report.json\n"
            "  python3 tools/validate_spine.py --matter m06\n"
            "  python3 tools/validate_spine.py --data-dir /tmp/spine --strict\n"
        ),
    )
    p.add_argument("--data-dir", default=None,
                   help="Spine data root (default: <repo>/data next to this tool).")
    p.add_argument("--matter", metavar="mNN", default=None,
                   help="Validate only this matter (e.g. m06), in isolation.")
    p.add_argument("--strict", action="store_true",
                   help="Ship-gate mode: unresolved cross-ref targets become ERRORs.")
    p.add_argument("--online", action="store_true",
                   help="Check FOLIO IRI existence against the local crosswalk snapshot.")
    p.add_argument("--enforce-day-zero-offsets", action="store_true",
                   help="Reject missing/stale prose sidecars and convertible absolute dates.")
    p.add_argument("--json", metavar="PATH", default=None,
                   help="Write a machine-readable JSON report to PATH.")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the human summary (use with --json).")
    return p


def default_data_dir() -> Path:
    here = Path(__file__).resolve().parent
    return (here.parent / "data")


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    data_dir = Path(args.data_dir).resolve() if args.data_dir else default_data_dir()

    if not data_dir.exists():
        print(f"error: data dir not found: {data_dir}", file=sys.stderr)
        return 2

    if args.matter and not RE["matter"].match(args.matter):
        print(f"error: --matter must look like m01..m20, got {args.matter!r}", file=sys.stderr)
        return 2

    schemas_dir = data_dir / "schemas"
    world = discover(data_dir, args.matter)
    schemas = SchemaSet(schemas_dir)
    report = Report()

    Validator(world, schemas, report, strict=args.strict, online=args.online,
              enforce_day_zero_offsets=args.enforce_day_zero_offsets).run()

    if not args.quiet:
        emit_human(world, report, args.strict)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(build_json_report(world, report, args.strict), fh, indent=2, sort_keys=True)
            fh.write("\n")
        if not args.quiet:
            print(f"\nwrote JSON report: {args.json}")

    return 1 if report.has_errors() else 0


if __name__ == "__main__":
    sys.exit(main())
