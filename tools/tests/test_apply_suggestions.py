#!/usr/bin/env python3
r"""Tests for tools/apply_suggestions.py — the apply-transaction engine.

Strategy: a REAL throwaway git repo fixture in a tmp dir (so the git-worktree
transaction, merge, and rollback all run for real against real files), plus an
in-memory EditorStore that faithfully models the Worker's claim/finalize/
reconcile semantics, plus a FakePipeline that stands in for the heavy spine tools
(build_site / validate_spine / bundles / parity / deploy) and re-resolves the
editor map from the CURRENT worktree source so the DRIFT gate is exercised
honestly. No live Worker, no network, no real deploy.

The load-bearing property under test is INTEGRITY: canonical data/ is proven
byte-identical after every failure path, and a suggestion never reaches canonical
except through validate + build + parity + (gated) deploy + merge.

Run:  python3 -m pytest tools/tests/test_apply_suggestions.py -q
  or: python3 tools/tests/test_apply_suggestions.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import text_norm  # noqa: E402
import apply_suggestions as ap  # noqa: E402
import stamp_block_ids as sb  # noqa: E402

BID_RE = sb.BID_RE


class GeneratorIdentityTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="generator-identity-")
        shutil.copytree(TOOLS, os.path.join(self.root, "tools"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _append(self, relpath, text):
        with open(os.path.join(self.root, relpath), "a", encoding="utf-8") as fh:
            fh.write(text)

    def test_dependency_closure_includes_transitive_generator_helpers(self):
        paths = ap.generator_dependency_paths(self.root)
        self.assertIn("tools/student_archives.py", paths)
        self.assertIn("tools/text_norm.py", paths)
        self.assertIn("tools/render_diff_lib.py", paths)

    def test_identity_changes_when_transitive_helpers_change(self):
        original = ap.generator_identity(self.root)
        self._append("tools/student_archives.py", "\n# identity catch-power\n")
        student_changed = ap.generator_identity(self.root)
        self.assertNotEqual(student_changed, original)
        self._append("tools/text_norm.py", "\n# second helper catch-power\n")
        self.assertNotEqual(ap.generator_identity(self.root), student_changed)

    def test_identity_ignores_unrelated_ambient_files(self):
        original = ap.generator_identity(self.root)
        self._append("tools/prod_release_executor.py", "\n# unrelated\n")
        with open(os.path.join(self.root, "ambient.txt"), "w", encoding="utf-8") as fh:
            fh.write("unrelated")
        self.assertEqual(ap.generator_identity(self.root), original)


class AtomicReviewEvidenceTest(unittest.TestCase):
    def _patch(self, suggestion_id, source_ref, old, new, group_id=None):
        return ap.Patch(suggestion_id=suggestion_id,
                        group_id=group_id or "solo:" + suggestion_id,
                        source_ref=source_ref, relpath=source_ref.split("#", 1)[0],
                        kind="prose_md", json_path="",
                        original_text=old, new_text=new)

    def test_cross_source_group_uses_one_shared_decision_identity(self):
        patches = [
            self._patch("s1", "data/a.md#baaaaaaaa", "Old alpha.", "New alpha.", "shared-1"),
            self._patch("s2", "data/b.md#bbbbbbbbb", "Old beta.", "New beta.", "shared-1"),
        ]
        revisions = ap.build_review_revisions(patches, "dev-tip", "prod-tip")
        operations = [operation for revision in revisions for operation in revision["operations"]]
        self.assertEqual({operation["group_id"] for operation in operations}, {"shared-1"})
        self.assertEqual(len({operation["decision_id"] for operation in operations}), 1)

    def test_duplicate_structural_edits_keep_distinct_identity_and_order_evidence(self):
        common = dict(group_id="shared-structural",source_ref="data/a.md#baaaaaaaa",
                      relpath="data/a.md",kind="prose_md",json_path="",
                      original_text="Anchor",new_text="Same addition",op="insert_after")
        first = ap.Patch(suggestion_id="insert-1",created_at=1000,**common)
        second = ap.Patch(suggestion_id="insert-2",created_at=2000,**common)

        operations = [ap._atomic_review_operations(patch,"dev-tip","prod-tip")[0]
                      for patch in (first,second)]

        self.assertEqual([operation["created_at"] for operation in operations],[1000,2000])
        self.assertEqual([operation["suggestion_id"] for operation in operations],
                         ["insert-1","insert-2"])
        self.assertEqual(len({operation["id"] for operation in operations}),2)

    def test_distinctive_exact_prose_move_pairs_both_endpoints(self):
        moved = "This distinctive sentence has enough words to qualify."
        stationary = "The substantially longer middle passage remains exactly where it was throughout this careful move test."
        patch = self._patch("s1", "data/a.md#baaaaaaaa",
                            moved + "\n\n" + stationary,
                            stationary + "\n\n" + moved)
        operations = ap._atomic_review_operations(patch, "dev-tip", "prod-tip")
        endpoints = [operation for operation in operations if operation.get("move_pair_id")]
        self.assertEqual({operation["move_role"] for operation in endpoints}, {"from", "to"})
        self.assertEqual(len({operation["move_pair_id"] for operation in endpoints}), 1)
        self.assertEqual(len({operation["decision_id"] for operation in endpoints}), 1)

    def test_short_or_repeated_prose_is_not_falsely_paired_as_move(self):
        short = self._patch("s1", "data/a.md#baaaaaaaa", "Tiny words move.\n\nKeep.",
                            "Keep.\n\nTiny words move.")
        repeated_text = "This repeated sentence has enough words to qualify."
        repeated = self._patch("s2", "data/a.md#baaaaaaaa",
                               repeated_text + "\n\n" + repeated_text + "\n\nKeep.",
                               "Keep.\n\n" + repeated_text + "\n\n" + repeated_text)
        for patch in (short, repeated):
            operations = ap._atomic_review_operations(patch, "dev-tip", "prod-tip")
            self.assertFalse(any(operation.get("move_pair_id") for operation in operations))

    def test_repetitive_near_limit_prose_bypasses_matcher_with_deterministic_fallback(self):
        old = ("repeat " * 2300) + "old."
        new = ("repeat " * 2300) + "new!"
        patch = self._patch("s1", "data/a.md#baaaaaaaa", old, new)

        started = time.monotonic()
        with mock.patch.object(ap.difflib, "SequenceMatcher",
                               side_effect=AssertionError("matcher must be bypassed")):
            first = ap._atomic_review_operations(patch, "dev-tip", "prod-tip")
            second = ap._atomic_review_operations(patch, "dev-tip", "prod-tip")
        self.assertLess(time.monotonic() - started, 2.0)

        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["kind"], "replace")
        self.assertEqual(first[0]["old_text"], old)
        self.assertEqual(first[0]["new_text"], new)
        self.assertEqual(first[0]["base_range"], [0, len(old)])
        self.assertEqual(first[0]["proposed_range"], [0, len(new)])
        self.assertEqual(first[0]["context_before"], [])
        self.assertEqual(first[0]["context_after"], [])

    def test_atomic_matcher_preserves_separated_punctuation_and_unicode_edits(self):
        patch = self._patch(
            "s1", "data/a.md#baaaaaaaa",
            "Café strong points, and weak points.",
            "Café strongest points, and weak points!",
        )

        operations = ap._atomic_review_operations(patch, "dev-tip", "prod-tip")

        self.assertEqual([(op["old_text"], op["new_text"]) for op in operations],
                         [("strong", "strongest"), (".", "!")])
        self.assertEqual(operations[0]["context_before"][-2:], ["Café", " "])

    def test_oversized_review_matrix_bypasses_matcher_below_token_ceiling(self):
        old = " ".join("old%d" % index for index in range(400))
        new = " ".join("new%d" % index for index in range(400))
        self.assertLess(len(ap._review_tokens(old)), ap.MAX_REVIEW_TOKENS)
        self.assertGreater(len(ap._review_tokens(old)) * len(ap._review_tokens(new)),
                           ap.MAX_REVIEW_MATRIX_CELLS)
        patch = self._patch("s1", "data/a.md#baaaaaaaa", old, new)

        with mock.patch.object(ap.difflib, "SequenceMatcher",
                               side_effect=AssertionError("matcher must be bypassed")):
            operations = ap._atomic_review_operations(patch, "dev-tip", "prod-tip")

        self.assertEqual([(op["kind"], op["old_text"], op["new_text"])
                          for op in operations], [("replace", old, new)])


def _bid_of(span):
    """The trailing {#b:xxxxxxxx} durable-ID of a source paragraph."""
    m = BID_RE.search(span)
    if not m:
        raise AssertionError("fixture block missing a bid marker: %r" % span[:60])
    return m.group(1)


# --------------------------------------------------------------------------- #
# Honest map resolver — mirrors build_site's classification for the fixture.
# --------------------------------------------------------------------------- #
def _strip_markers(span):
    s = BID_RE.sub("", span).strip()
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s


def _hash(span):
    return text_norm.norm_hash(_strip_markers(span))


def _has_fmt(span):
    return bool(re.search(r"\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|(?<!\*)\*[^*]+\*(?!\*)", span))


def _paragraphs(text):
    """Blank-line separated, single-logical-line paragraphs (build_site rule)."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def resolve_index(worktree, spec):
    """Build {source_ref: block} from CURRENT worktree files per `spec`:
      ("relpath.md", "md")
      ("relpath.json", "json", {"body_md_fields": [...], "scalars": [...]})
    """
    index = {}
    for entry in spec:
        relpath, kind = entry[0], entry[1]
        abspath = os.path.join(worktree, relpath)
        if not os.path.isfile(abspath):
            continue
        raw = open(abspath, encoding="utf-8").read()
        if kind == "md":
            for span in _paragraphs(raw):
                ref = "%s#b%s" % (relpath, _bid_of(span))
                text = BID_RE.sub("", span).strip()
                index[ref] = {
                    "source_ref": ref, "kind": "prose", "json_path": None,
                    "original_text": text, "original_hash": _hash(span),
                    "has_inline_formatting": _has_fmt(text), "context": "",
                }
        else:  # json
            cfg = entry[2]
            obj = json.loads(raw)
            for field in cfg.get("body_md_fields", []):
                body = ap.json_get(obj, field)
                for span in _paragraphs(body):
                    ref = "%s#%s.b%s" % (relpath, field, _bid_of(span))
                    text = BID_RE.sub("", span).strip()
                    index[ref] = {
                        "source_ref": ref, "kind": "prose", "json_path": None,
                        "original_text": text, "original_hash": _hash(span),
                        "has_inline_formatting": _has_fmt(text), "context": "",
                    }
            for path in cfg.get("scalars", []):
                val = ap.json_get(obj, path)
                rendered = str(val)
                ref = "%s#%s" % (relpath, path)
                index[ref] = {
                    "source_ref": ref, "kind": "json_scalar", "json_path": path,
                    "original_text": rendered, "original_hash": _hash(rendered),
                    "has_inline_formatting": False, "context": "",
                }
    return index


# --------------------------------------------------------------------------- #
# In-memory EditorStore — models the Worker RPC semantics.
# --------------------------------------------------------------------------- #
PRE_MERGED = {"claimed", "patched", "validated", "built", "parity_ok", "deployed"}


class InMemoryEditorStore:
    def __init__(self, clock=0):
        self.rows = {}          # id -> row dict
        self.batches = {}       # batch_id -> {phase, lease_expires_at}
        self._clock = clock
        self.lease_ms = 1000
        self.prod_base = None

    def now(self):
        return self._clock

    def add(self, **row):
        row.setdefault("status", "accepted")
        row.setdefault("origin", "human")
        row.setdefault("kind", "prose")
        row.setdefault("group_id", None)
        row.setdefault("apply_batch_id", None)
        row.setdefault("lease_expires_at", None)
        self.rows[row["id"]] = row
        return row

    # ---- RPCs ----
    def reconcile(self):
        swept = {"rolled_back": [], "completed": []}
        now = self.now()
        for bid, b in list(self.batches.items()):
            if b["phase"] in ("done", "rolled_back"):
                continue
            if (b["lease_expires_at"] or 0) > now:
                continue
            members = [r for r in self.rows.values() if r["apply_batch_id"] == bid]
            if b["phase"] in PRE_MERGED:
                for r in members:
                    if r["status"] == "in_flight":
                        r["status"] = "accepted"
                        r["apply_batch_id"] = None
                        r["lease_expires_at"] = None
                        swept["rolled_back"].append(r["id"])
                b["phase"] = "rolled_back"
            else:
                for r in members:
                    if r["status"] == "in_flight":
                        r["status"] = "applied"
                        r["lease_expires_at"] = None
                        swept["completed"].append(r["id"])
                b["phase"] = "done"
        # orphan in_flight (expired, batch gone/rolled) -> accepted
        for r in self.rows.values():
            if r["status"] == "in_flight":
                b = self.batches.get(r["apply_batch_id"])
                expired = (r["lease_expires_at"] or 0) <= now
                if expired and (b is None or b["phase"] in ("rolled_back", "done")):
                    r["status"] = "accepted"
                    r["apply_batch_id"] = None
                    r["lease_expires_at"] = None
                    swept["rolled_back"].append(r["id"])
        return {"ok": True, **swept}

    def claim(self, batch_id, base_sha=None):
        if batch_id in self.batches:
            return {"ok": False, "reason": "batch_exists"}
        accepted = [r for r in self.rows.values() if r["status"] == "accepted"]
        claim_ids = set()
        groups = set(r["group_id"] for r in accepted if r["group_id"])
        for r in accepted:
            if not r["group_id"]:
                claim_ids.add(r["id"])
        for g in groups:
            members = [r for r in self.rows.values() if r["group_id"] == g]
            if all(m["status"] == "accepted" for m in members):
                for m in members:
                    claim_ids.add(m["id"])
        if not claim_ids:
            return {"ok": False, "reason": "nothing_to_claim"}
        lease = self.now() + self.lease_ms
        self.batches[batch_id] = {"phase": "claimed", "lease_expires_at": lease}
        for cid in claim_ids:
            r = self.rows[cid]
            r["status"] = "in_flight"
            r["apply_batch_id"] = batch_id
            r["lease_expires_at"] = lease
        return {"ok": True, "batch_id": batch_id, "claimed": sorted(claim_ids),
                "lease_expires_at": lease, "prod_base": self.prod_base}

    def fetch_batch_rows(self, batch_id, claimed_ids):
        return [dict(self.rows[i]) for i in claimed_ids if i in self.rows]

    def propose_companion(self, payload):
        rid = payload["id"]
        self.rows[rid] = {
            **payload, "status": "pending", "apply_batch_id": None,
            "lease_expires_at": None,
        }
        return {"ok": True, "id": rid, "status": "pending"}

    def finalize(self, batch_id, phase=None, applied=None, accepted_blocked=None,
                 needs_human=None, drift=None, base_sha=None, commit_sha=None,
                 generator_id=None, review_revisions=None):
        b = self.batches.get(batch_id)
        if not b:
            return {"ok": False, "reason": "no_batch"}
        if phase:
            b["phase"] = phase
        if commit_sha is not None:
            b["commit_sha"] = commit_sha
        if generator_id is not None:
            b["generator_id"] = generator_id
        if review_revisions is not None:
            b["review_revisions"] = review_revisions
        for ids, st in ((applied, "applied"), (accepted_blocked, "accepted_blocked"),
                        (needs_human, "needs_human"), (drift, "drift")):
            for i in (ids or []):
                if i in self.rows:
                    self.rows[i]["status"] = st
                    self.rows[i]["lease_expires_at"] = None
        return {"ok": True}

    def fetch_accepted_comments(self):
        return [dict(r) for r in self.rows.values()
                if r["status"] == "accepted" and r["kind"] == "comment"]


# --------------------------------------------------------------------------- #
# Fake pipeline — stands in for the heavy spine tools; honest map re-resolution.
# --------------------------------------------------------------------------- #
class FakePipeline:
    def __init__(self, spec, validate_ok=True, parity_ok=True, build_ok=True):
        self.spec = spec
        self.validate_ok = validate_ok
        self.parity_ok = parity_ok
        self.build_ok = build_ok
        self.worktree_snapshot = {}  # relpath -> content seen at validate time

    def regenerate_map(self, worktree):
        return resolve_index(worktree, self.spec)

    def validate(self, worktree):
        # snapshot the patched worktree so tests can prove the patch landed
        for entry in self.spec:
            rel = entry[0]
            p = os.path.join(worktree, rel)
            if os.path.isfile(p):
                self.worktree_snapshot[rel] = open(p, encoding="utf-8").read()
        return self.validate_ok, {"report": {"errors": [] if self.validate_ok else ["x"]}}

    def build(self, worktree):
        return self.build_ok, {"stdout": "built" if self.build_ok else "build FAIL"}

    def parity(self, worktree):
        return self.parity_ok, {"stdout": "parity"}

    def generator_identity(self, worktree):
        return "sha256:test-generator"

    def deploy(self, worktree, branch, plan_only):
        plan = [["bash", "deploy/deploy-dev.sh", branch], ["npx", "wrangler", "deploy"]]
        if plan_only:
            return True, {"planned": plan, "executed": False}
        return True, {"planned": plan, "executed": True}  # fake: no real deploy


class FakeClient:
    """Adapts InMemoryEditorStore to the client interface run_apply expects."""
    def __init__(self, store):
        self.store = store
        self.proposed = []

    def reconcile(self):
        return self.store.reconcile()

    def claim(self, batch_id, base_sha=None):
        return self.store.claim(batch_id, base_sha)

    def fetch_batch_rows(self, batch_id, claimed_ids):
        return self.store.fetch_batch_rows(batch_id, claimed_ids)

    def propose_companion(self, payload):
        self.proposed.append(payload)
        return self.store.propose_companion(payload)

    def finalize(self, *a, **k):
        return self.store.finalize(*a, **k)


# --------------------------------------------------------------------------- #
# Git fixture
# --------------------------------------------------------------------------- #
def _git(args, cwd):
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd,
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _write(root, rel, content):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)


M03_EX = "data/matters/m03-tort-meridian/exercise/exercise.json"
M03_BUS = "data/matters/m03-tort-meridian/business/business.json"
M03_MD = "data/matters/m03-tort-meridian/case-file/exh-notes.md"
M03_FMT = "data/matters/m03-tort-meridian/case-file/exh-formatted.md"
M07_EX = "data/matters/m07-ucc-meridian/exercise/exercise.json"

SPEC = [
    (M03_EX, "json", {"body_md_fields": ["sections.intro.body_md"], "scalars": ["caption"]}),
    (M03_BUS, "json", {"body_md_fields": [], "scalars": ["engagement.rate"]}),
    (M03_MD, "md"),
    (M03_FMT, "md"),
    (M07_EX, "json", {"body_md_fields": ["sections.intro.body_md"], "scalars": ["caption"]}),
]


def make_repo():
    root = tempfile.mkdtemp(prefix="apply-fixture-")
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t.local"], root)
    _git(["config", "user.name", "t"], root)
    _git(["config", "commit.gpgsign", "false"], root)

    _write(root, M03_EX, json.dumps({
        "id": "m03",
        "caption": "Osgard v. Meridian Freight (Tort)",
        "sections": {"intro": {"body_md":
            "You represent the plaintiff in a negligence action.\n\n"
            "The retainer for this matter is $8,400 total, due on signing.\n\n"
            "The demand letter seeks $12,500 in special damages."}},
    }, indent=2) + "\n")
    _write(root, M03_MD,
           "Intake notes for the tort matter.\n\n"
           "Client confirmed the retainer of $8,400 was paid in full.\n")
    _write(root, M03_FMT,
           "This paragraph has **bold emphasis** that plain text cannot round-trip.\n")
    _write(root, M03_BUS, json.dumps({
        "engagement": {"fee_type": "hourly", "rate": 250},
    }, indent=2) + "\n")
    _write(root, "data/schemas/business.schema.json", json.dumps({
        "type": "object",
        "properties": {
            "engagement": {
                "type": "object",
                "properties": {"rate": {"type": "number"}},
            },
        },
    }, indent=2) + "\n")
    _write(root, M07_EX, json.dumps({
        "id": "m07",
        "caption": "Bell v. Osgard Supply (UCC)",
        "sections": {"intro": {"body_md":
            "An unrelated UCC matter where the deposit was $8,400 exactly."}},
    }, indent=2) + "\n")

    # Stamp durable block IDs with the REAL stamper — fixtures carry the same
    # {#b:} markers (and bid-keyed source_refs) as the migrated corpus.
    existing = set()
    for entry in SPEC:
        relpath, kind = entry[0], entry[1]
        fields = entry[2]["body_md_fields"] if kind == "json" else []
        sb.stamp_file(os.path.join(root, relpath), fields, existing)

    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "fixture"], root)
    return root


def bref(root, relpath, n, field=None):
    """The bid-keyed source_ref of the n-th paragraph of a fixture source
    (ordinals exist only in the tests' heads now — refs carry bids)."""
    raw = open(os.path.join(root, relpath), encoding="utf-8").read()
    if field is not None:
        raw = ap.json_get(json.loads(raw), field)
    bid = _bid_of(_paragraphs(raw)[n])
    return ("%s#%s.b%s" % (relpath, field, bid) if field is not None
            else "%s#b%s" % (relpath, bid))


def snapshot_data(root):
    """Content-hash every file under data/ (proves byte-identity)."""
    snap = {}
    data = os.path.join(root, "data")
    for dp, _dn, fns in os.walk(data):
        for fn in fns:
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, root)
            snap[rel] = hashlib.sha256(open(fp, "rb").read()).hexdigest()
    return snap


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class ReviewEvidenceTest(unittest.TestCase):
    def test_sequential_same_source_edits_become_one_cumulative_revision(self):
        common = dict(group_id=None,source_ref="data/copy/home.json#lead",
            relpath="data/copy/home.json",kind="json_scalar",json_path="lead")
        patches = [
            ap.Patch(suggestion_id="s1",original_text="The bad idea",
                new_text="The good idea",**common),
            ap.Patch(suggestion_id="s2",original_text="The good idea",
                new_text="The great idea",**common),
        ]

        revisions = ap.build_review_revisions(patches,"d" * 40,"p" * 40)

        self.assertEqual(revisions[0]["suggestion_ids"],["s1","s2"])
        self.assertEqual(revisions[0]["source_original_text"],"The bad idea")
        self.assertEqual(revisions[0]["source_proposed_text"],"The great idea")
        self.assertEqual([(item["old_text"],item["new_text"])
                          for item in revisions[0]["operations"]],[("bad","great")])


class ApplyEngineTest(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.store = InMemoryEditorStore()
        self.client = FakeClient(self.store)
        self.index = resolve_index(self.root, SPEC)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _add_edit(self, sid, source_ref, new_text, **extra):
        blk = self.index[source_ref]
        return self.store.add(
            id=sid, source_ref=source_ref, new_text=new_text,
            original_hash=blk["original_hash"], original_text=blk["original_text"],
            kind="json_scalar" if blk["kind"] == "json_scalar" else "prose",
            json_path=blk.get("json_path"), status="accepted", **extra)

    def _run(self, batch_id, pipeline, deploy_plan_only=True):
        return ap.run_apply(self.client, pipeline, batch_id,
                            worktree_parent=None, deploy_plan_only=deploy_plan_only,
                            branch="test", canonical_root=self.root, logger=lambda *a: None)

    # 1) Clean prose edit -> validator green -> parity holds -> STOPS pre-deploy.
    def test_clean_prose_build_only_stops_before_deploy(self):
        ref = bref(self.root, M03_EX, 0, "sections.intro.body_md")
        self._add_edit("s1", ref, "You represent the plaintiff in a serious negligence action.")
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC, validate_ok=True, parity_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual(res.reason, "build_only_stopped_pre_deploy")
        self.assertEqual([p.suggestion_id for p in res.applied], ["s1"])
        self.assertFalse(res.committed)
        # patch really landed in the worktree (validator saw it) ...
        self.assertIn("serious negligence", pipe.worktree_snapshot[M03_EX])
        # ... but canonical is byte-identical (we stopped before merge).
        self.assertEqual(before, snapshot_data(self.root))
        self.assertEqual(self._porcelain(), "")

    # Full round trip (deploy executed via fake) -> merges to canonical.
    def test_clean_prose_full_roundtrip_merges(self):
        ref = bref(self.root, M03_MD, 0)
        self._add_edit("s1", ref, "Revised intake notes for the tort matter.")
        self.store.prod_base = "trusted-production-tip"
        pipe = FakePipeline(SPEC, validate_ok=True, parity_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=False)

        self.assertTrue(res.committed)
        self.assertEqual([p.suggestion_id for p in res.applied], ["s1"])
        self.assertEqual(self.store.rows["s1"]["status"], "applied")
        self.assertEqual(self.store.batches["b1"]["commit_sha"], ap.head_sha(self.root))
        self.assertEqual(self.store.batches["b1"]["generator_id"],
                         "sha256:test-generator")
        revisions = self.store.batches["b1"]["review_revisions"]
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["source_ref"], ref)
        self.assertEqual(revisions[0]["source_revision"], ap.head_sha(self.root))
        self.assertEqual(revisions[0]["prod_base"], "trusted-production-tip")
        self.assertNotEqual(revisions[0]["prod_base"], res.base_sha)
        self.assertEqual(revisions[0]["suggestion_ids"], ["s1"])
        self.assertEqual([(op["kind"], op["old_text"], op["new_text"])
                          for op in revisions[0]["operations"]],
                         [("replace", "Intake", "Revised intake")])
        canonical = open(os.path.join(self.root, M03_MD), encoding="utf-8").read()
        self.assertIn("Revised intake notes", canonical)
        self.assertEqual(self._porcelain(), "")  # clean after merge

    def test_apply_without_completed_production_frontier_omits_review_evidence(self):
        ref = bref(self.root, M03_MD, 0)
        self._add_edit("s1", ref, "Revised intake notes for the tort matter.")
        res = self._run("bootstrap", FakePipeline(SPEC), deploy_plan_only=False)

        self.assertTrue(res.committed)
        self.assertNotIn("review_revisions", self.store.batches["bootstrap"])

    # 2) Money edit that breaks reconciliation (validator RED) -> accepted_blocked.
    def test_money_edit_validator_red_blocks_and_discards(self):
        ref = bref(self.root, M03_EX, 2, "sections.intro.body_md")
        self._add_edit("s1", ref, "The demand letter seeks $15,000 in special damages.")
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC, validate_ok=False)  # reconciliation broke
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual(res.reason, "validator_red")
        self.assertEqual([p.suggestion_id for p in res.accepted_blocked], ["s1"])
        self.assertEqual([], res.applied)
        self.assertEqual(self.store.rows["s1"]["status"], "accepted_blocked")
        # the worktree DID hold the patch ($15,000) — proving discard is real ...
        self.assertIn("$15,000", pipe.worktree_snapshot[M03_EX])
        # ... yet canonical is byte-identical (worktree discarded, nothing shipped).
        self.assertEqual(before, snapshot_data(self.root))  # canonical untouched
        self.assertEqual(self._porcelain(), "")

    # 3) Formatted block whose SPAN TEXT is edited away -> needs_human (WP7 keeps
    #    the conservative fallback: the bold span 'bold emphasis' disappears).
    def test_formatted_block_needs_human(self):
        ref = bref(self.root, M03_FMT, 0)
        self._add_edit("s1", ref, "This paragraph now says something else entirely.")
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC)
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual([r["id"] for r in res.needs_human], ["s1"])
        self.assertEqual([], res.applied)
        self.assertEqual(self.store.rows["s1"]["status"], "needs_human")
        self.assertEqual(before, snapshot_data(self.root))

    # 3b) WP7: formatted block edited AROUND an unchanged span -> auto-applies,
    #     preserving the raw **markup** exactly, and merges to canonical.
    def test_formatted_block_span_splice_applies(self):
        ref = bref(self.root, M03_FMT, 0)  # "...has **bold emphasis** that plain text cannot..."
        # plain edit keeps the span text 'bold emphasis' verbatim, changes around it
        self._add_edit(
            "s1", ref,
            "This paragraph has bold emphasis that plain text simply cannot round-trip.")
        pipe = FakePipeline(SPEC, validate_ok=True, parity_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=False)

        self.assertTrue(res.committed)
        self.assertEqual([p.suggestion_id for p in res.applied], ["s1"])
        self.assertEqual(self.store.rows["s1"]["status"], "applied")
        canonical = open(os.path.join(self.root, M03_FMT), encoding="utf-8").read()
        # raw markup preserved EXACTLY, and the surrounding plain edit landed.
        self.assertIn("**bold emphasis**", canonical)
        self.assertIn("simply cannot round-trip", canonical)
        self.assertEqual(self._porcelain(), "")

    # 3c) WP7: a formatted-block edit that changes the SPAN's own text -> needs_human,
    #     canonical untouched (never silently corrupts the markup).
    def test_formatted_block_span_text_edit_rejected(self):
        ref = bref(self.root, M03_FMT, 0)
        # 'bold emphasis' -> 'strong emphasis' edits the span interior
        self._add_edit(
            "s1", ref,
            "This paragraph has strong emphasis that plain text cannot round-trip.")
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC)
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual([r["id"] for r in res.needs_human], ["s1"])
        self.assertEqual([], res.applied)
        self.assertEqual(before, snapshot_data(self.root))

    # 4) Drift: source changed after the suggestion was made -> drift, not patched.
    def test_drift_when_source_changed_post_suggest(self):
        ref = bref(self.root, M03_MD, 0)
        self._add_edit("s1", ref, "New intake summary.")  # captures OLD hash
        # source changes post-suggest (a legitimate committed edit — the bid
        # marker survives; only the text changed, so the hash no longer matches)
        cur = open(os.path.join(self.root, M03_MD), encoding="utf-8").read()
        _write(self.root, M03_MD, cur.replace(
            "Intake notes for the tort matter.",
            "Intake notes for the tort matter (amended by staff)."))
        _git(["commit", "-aqm", "amend source"], self.root)
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC)
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual([r["id"] for r in res.drift], ["s1"])
        self.assertEqual([], res.applied)
        self.assertEqual(self.store.rows["s1"]["status"], "drift")
        self.assertEqual(before, snapshot_data(self.root))  # canonical untouched

    # 5) Value-sync: companion for in-matter duplicate, NOT the same value in m07.
    def test_value_sync_scope_is_matter_bounded(self):
        ref = bref(self.root, M03_EX, 1, "sections.intro.body_md")  # "$8,400 total"
        self._add_edit("s1", ref, "The retainer for this matter is $9,100 total, due on signing.")
        pipe = FakePipeline(SPEC, validate_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=True)

        proposed_refs = [c["source_ref"] for c in res.companions]
        # in-matter duplicate ($8,400 in m03 case-file .md) IS proposed ...
        m03md_p1 = bref(self.root, M03_MD, 1)
        self.assertIn(m03md_p1, proposed_refs)
        # ... the SAME value in another matter (m07) is NOT.
        self.assertNotIn(bref(self.root, M07_EX, 0, "sections.intro.body_md"), proposed_refs)
        # companions are pending, never applied.
        for c in res.companions:
            self.assertEqual(self.store.rows[c["id"]]["status"], "pending")
        # the m03 companion carries the swapped value for Damien's review.
        m03comp = next(c for c in res.companions if c["source_ref"] == m03md_p1)
        self.assertIn("$9,100", m03comp["new_text"])

    # 6) Parity mismatch aborts + rollback, canonical untouched.
    def test_parity_mismatch_aborts(self):
        ref = bref(self.root, M03_MD, 0)
        self._add_edit("s1", ref, "Edited intake notes.")
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC, validate_ok=True, parity_ok=False)
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual(res.reason, "parity_mismatch")
        self.assertEqual([], res.applied)
        self.assertEqual(self.store.rows["s1"]["status"], "accepted_blocked")
        self.assertEqual(before, snapshot_data(self.root))

    # 7) Crash-recovery reconcile re-queues an in_flight orphan.
    def test_reconcile_requeues_in_flight_orphan(self):
        # an orphaned in_flight from a crashed prior run (expired lease, pre-merged batch)
        self.store.add(id="orphan", source_ref=bref(self.root, M03_MD, 0), new_text="x",
                       status="in_flight", apply_batch_id="dead-batch",
                       lease_expires_at=-1)
        self.store.batches["dead-batch"] = {"phase": "patched", "lease_expires_at": -1}
        self.store._clock = 10_000

        # the reconcile RPC (which run_apply invokes FIRST, before any claim)
        self.client.reconcile()

        self.assertEqual(self.store.rows["orphan"]["status"], "accepted")
        self.assertIsNone(self.store.rows["orphan"]["apply_batch_id"])
        self.assertEqual(self.store.batches["dead-batch"]["phase"], "rolled_back")

    # 7b) run_apply reconciles BEFORE it claims (ordering guarantee).
    def test_reconcile_runs_before_claim(self):
        calls = []
        client = self.client
        orig_rec, orig_claim = client.reconcile, client.claim
        client.reconcile = lambda: (calls.append("reconcile"), orig_rec())[1]
        client.claim = lambda *a, **k: (calls.append("claim"), orig_claim(*a, **k))[1]
        # empty queue -> nothing_to_claim, but ordering still observable
        self._run("b1", FakePipeline(SPEC), deploy_plan_only=True)
        self.assertEqual(calls[:2], ["reconcile", "claim"])

    # 8) Group atomicity: one member drifts -> whole group drifts, none applied.
    def test_group_atomic_drift(self):
        r1 = bref(self.root, M03_MD, 0)
        r2 = bref(self.root, M03_EX, 0, "sections.intro.body_md")
        self._add_edit("g1", r1, "Edited notes.", group_id="grp")
        self._add_edit("g2", r2, "Edited intro.", group_id="grp")
        # break the hash of only ONE member post-suggest
        self.store.rows["g1"]["original_hash"] = "deadbeef"
        before = snapshot_data(self.root)
        pipe = FakePipeline(SPEC)
        res = self._run("b1", pipe, deploy_plan_only=True)

        self.assertEqual([], res.applied)
        self.assertEqual({r["id"] for r in res.drift}, {"g1", "g2"})
        self.assertEqual(before, snapshot_data(self.root))

    # Path safety: traversal / absolute / escape all rejected.
    def test_path_safety(self):
        for bad in ("../etc/passwd", "/etc/passwd", "data/../../x", "site/index.html"):
            with self.assertRaises(ap.ApplyError):
                ap.safe_data_path(self.root, bad)
        ok = ap.safe_data_path(self.root, M03_MD + "#b00000000")
        self.assertTrue(ok.startswith(os.path.join(self.root, "data")))

    # json_scalar edits are parse->set->serialize (valid JSON out, never spliced).
    def test_json_scalar_parse_set_serialize(self):
        ref = "%s#caption" % M03_EX
        self._add_edit("s1", ref, "Osgard v. Meridian Freight Co. (Tort)")
        pipe = FakePipeline(SPEC, validate_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=False)
        self.assertTrue(res.committed)
        obj = json.loads(open(os.path.join(self.root, M03_EX), encoding="utf-8").read())
        self.assertEqual(obj["caption"], "Osgard v. Meridian Freight Co. (Tort)")

    def test_numeric_json_scalar_round_trips_as_a_number(self):
        ref = "%s#engagement.rate" % M03_BUS
        self._add_edit("numeric1", ref, "275.5")
        pipe = FakePipeline(SPEC, validate_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=False)

        self.assertTrue(res.committed)
        obj = json.loads(open(os.path.join(self.root, M03_BUS), encoding="utf-8").read())
        self.assertEqual(obj["engagement"]["rate"], 275.5)
        self.assertIsInstance(obj["engagement"]["rate"], float)

    def test_json_numeric_grammar_rejects_plus_and_unicode_digits_consistently(self):
        for current in (1, 1.0):
            for incoming in ("+5", "１２３"):
                with self.subTest(current=current, incoming=incoming):
                    with self.assertRaises(ValueError):
                        ap.coerce_json_scalar(
                            self.root,
                            M03_BUS,
                            "engagement.rate",
                            incoming,
                            current,
                        )

    def test_large_non_integral_decimal_cannot_convert_to_infinity(self):
        with self.assertRaises(ValueError):
            ap.coerce_json_scalar(
                self.root,
                M03_BUS,
                "engagement.rate",
                "1.1e999",
                1.0,
            )

    def test_bad_numeric_scalar_isolated_from_other_suggestions(self):
        self._add_edit("bad-number", "%s#engagement.rate" % M03_BUS, "not-a-rate")
        self._add_edit("good-string", "%s#caption" % M03_EX,
                       "Osgard v. Meridian Freight Co. (Tort)")
        pipe = FakePipeline(SPEC, validate_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=False)

        self.assertTrue(res.committed)
        self.assertEqual([p.suggestion_id for p in res.applied], ["good-string"])
        self.assertEqual([r["id"] for r in res.needs_human], ["bad-number"])
        self.assertEqual(
            res.needs_human[0]["outcome_reason"],
            ap.OUT_VALIDATION_ERROR,
        )
        self.assertIn("reason: `validation_error`", res.digest_md)
        business = json.loads(open(os.path.join(self.root, M03_BUS), encoding="utf-8").read())
        exercise = json.loads(open(os.path.join(self.root, M03_EX), encoding="utf-8").read())
        self.assertEqual(business["engagement"]["rate"], 250)
        self.assertEqual(exercise["caption"], "Osgard v. Meridian Freight Co. (Tort)")

    def test_over_large_numeric_scalar_isolated_from_other_suggestions(self):
        self._add_edit(
            "over-large-number",
            "%s#engagement.rate" % M03_BUS,
            "1e999999999999999999",
        )
        self._add_edit(
            "good-string",
            "%s#caption" % M03_EX,
            "Osgard v. Meridian Freight Co. (Tort)",
        )
        pipe = FakePipeline(SPEC, validate_ok=True)

        res = self._run("b1", pipe, deploy_plan_only=False)

        self.assertTrue(res.committed)
        self.assertEqual([p.suggestion_id for p in res.applied], ["good-string"])
        self.assertEqual(
            [r["id"] for r in res.needs_human],
            ["over-large-number"],
        )
        business = json.loads(
            open(os.path.join(self.root, M03_BUS), encoding="utf-8").read()
        )
        exercise = json.loads(
            open(os.path.join(self.root, M03_EX), encoding="utf-8").read()
        )
        self.assertEqual(business["engagement"]["rate"], 250)
        self.assertEqual(
            exercise["caption"],
            "Osgard v. Meridian Freight Co. (Tort)",
        )

    def test_bad_numeric_scalar_rolls_out_its_whole_group(self):
        self._add_edit("group-bad-number", "%s#engagement.rate" % M03_BUS,
                       "not-a-rate", group_id="scoped-group")
        self._add_edit("group-good-string", "%s#caption" % M03_EX,
                       "Osgard v. Meridian Freight Co. (Tort)",
                       group_id="scoped-group")
        pipe = FakePipeline(SPEC, validate_ok=True)
        res = self._run("b1", pipe, deploy_plan_only=False)

        self.assertEqual([], res.applied)
        self.assertEqual(
            {r["id"] for r in res.needs_human},
            {"group-bad-number", "group-good-string"},
        )
        reasons = {
            r["id"]: r["outcome_reason"] for r in res.needs_human
        }
        self.assertEqual(
            reasons["group-bad-number"],
            ap.OUT_VALIDATION_ERROR,
        )
        self.assertEqual(
            reasons["group-good-string"],
            "group_rollback_due_to:group-bad-number",
        )
        business = json.loads(open(os.path.join(self.root, M03_BUS), encoding="utf-8").read())
        exercise = json.loads(open(os.path.join(self.root, M03_EX), encoding="utf-8").read())
        self.assertEqual(business["engagement"]["rate"], 250)
        self.assertEqual(exercise["caption"], "Osgard v. Meridian Freight (Tort)")

    def test_rollout_replay_failure_is_not_reported_as_applied(self):
        self._add_edit(
            "bad-number",
            "%s#engagement.rate" % M03_BUS,
            "not-a-rate",
        )
        self._add_edit(
            "kept-string",
            "%s#caption" % M03_EX,
            "Osgard v. Meridian Freight Co. (Tort)",
        )
        real_apply = ap.apply_file_patches
        calls = {}

        def fail_kept_on_second_application(worktree, relpath, patches):
            for patch in patches:
                calls[patch.suggestion_id] = calls.get(patch.suggestion_id, 0) + 1
            result = real_apply(worktree, relpath, patches)
            if any(
                patch.suggestion_id == "kept-string"
                and calls[patch.suggestion_id] == 2
                for patch in patches
            ):
                return {"kept-string": ap.OUT_NEEDS_HUMAN}
            return result

        with mock.patch.object(
            ap,
            "apply_file_patches",
            side_effect=fail_kept_on_second_application,
        ):
            res = self._run(
                "b1",
                FakePipeline(SPEC, validate_ok=True),
                deploy_plan_only=False,
            )

        self.assertEqual(calls["kept-string"], 2)
        self.assertNotIn(
            "kept-string",
            [patch.suggestion_id for patch in res.applied],
        )
        self.assertEqual(
            self.store.rows["kept-string"]["status"],
            "needs_human",
        )

    def test_json_scalar_with_absent_current_leaf_is_validation_error(self):
        patch = ap.Patch(
            suggestion_id="missing-leaf",
            group_id="solo:missing-leaf",
            source_ref="%s#missing.value" % M03_EX,
            relpath=M03_EX,
            kind="json_scalar",
            json_path="missing.value",
            original_text="",
            new_text="12",
        )

        outcomes = ap.apply_file_patches(self.root, M03_EX, [patch])

        self.assertEqual(outcomes, {"missing-leaf": ap.OUT_VALIDATION_ERROR})

    def _porcelain(self):
        out = subprocess.run(["git", "status", "--porcelain"], cwd=self.root,
                             check=True, stdout=subprocess.PIPE, text=True)
        return out.stdout.strip()


class HttpRpcRoutingTest(unittest.TestCase):
    """The HTTP client's companion proposer MUST hit the admin-scoped
    /system-suggest endpoint (NOT the human /suggest endpoint, which hardcodes
    origin:human and would 403 the admin service token)."""

    def test_finalize_posts_release_evidence(self):
        import io
        import urllib.request as urlreq
        seen = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp(json.dumps({"ok": True}).encode())

        orig = urlreq.urlopen
        urlreq.urlopen = fake_urlopen
        try:
            client = ap.HttpRpcClient("https://w.example.com/edit/v1", "tok")
            client.finalize("batch-1", phase=ap.PHASE_DONE, applied=["s1"],
                            commit_sha="a" * 40,
                            generator_id="sha256:" + "b" * 64,
                            review_revisions=[{"id": "revision-1"}])
        finally:
            urlreq.urlopen = orig

        self.assertEqual(seen["body"]["commit_sha"], "a" * 40)
        self.assertEqual(seen["body"]["generator_id"], "sha256:" + "b" * 64)
        self.assertEqual(seen["body"]["review_revisions"], [{"id": "revision-1"}])

    def test_propose_companion_posts_to_system_suggest(self):
        import io
        import urllib.request as urlreq
        seen = {}

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp(json.dumps({"ok": True, "id": "x", "status": "pending"}).encode())

        orig = urlreq.urlopen
        urlreq.urlopen = fake_urlopen
        try:
            client = ap.HttpRpcClient("https://w.example.com/edit/v1", "tok")
            client.propose_companion({"id": "companion-1", "origin": "companion",
                                      "source_ref": "data/x#p0", "new_text": "n"})
        finally:
            urlreq.urlopen = orig
        self.assertEqual(seen["url"], "https://w.example.com/edit/v1/system-suggest")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"]["origin"], "companion")

    def test_companion_id_fits_worker_uuid_ceiling(self):
        # The Worker's suggest/system-suggest endpoints require the id to match
        # ^[a-zA-Z0-9_-]{8,64}$. Real source_refs are long; the id must still fit.
        import re as _re
        pat = _re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
        long_ref = ("data/matters/m01-arbitration-meridian/case-file/"
                    "statement-rennick.md#sections.deep.body_md.b3fa9c21e")
        cid = ap._companion_id("Gerald Rennick", long_ref)
        self.assertTrue(pat.match(cid), "companion id %r must match the uuid ceiling" % cid)
        # deterministic + unique per target_ref
        self.assertEqual(cid, ap._companion_id("Gerald Rennick", long_ref))
        other = ap._companion_id("Gerald Rennick", long_ref + "x")
        self.assertNotEqual(cid, other)
        self.assertTrue(pat.match(other))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------- #
# Structural operations through the apply engine (U4)
# --------------------------------------------------------------------------- #
class StructuralApplyTest(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.store = InMemoryEditorStore()
        self.client = FakeClient(self.store)
        self.index = resolve_index(self.root, SPEC)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _add_op(self, sid, source_ref, op, new_text=None, op_arg=None, **extra):
        blk = self.index[source_ref]
        return self.store.add(
            id=sid, source_ref=source_ref, kind=op, new_text=new_text,
            op_arg=op_arg, original_hash=blk["original_hash"],
            original_text=blk["original_text"], json_path=None,
            status="accepted", **extra)

    def _run(self, batch_id, pipeline, deploy_plan_only=False):
        return ap.run_apply(self.client, pipeline, batch_id,
                            worktree_parent=None, deploy_plan_only=deploy_plan_only,
                            branch="test", canonical_root=self.root, logger=lambda *a: None)

    def _md(self):
        return open(os.path.join(self.root, M03_MD), encoding="utf-8").read()

    def test_insert_after_lands_with_fresh_bid_and_no_bid_moves(self):
        before_bids = BID_RE.findall(self._md())
        anchor = bref(self.root, M03_MD, 0)
        self._add_op("i1", anchor, "insert_after", new_text="A brand-new paragraph.")
        res = self._run("b1", FakePipeline(SPEC))
        self.assertTrue(res.committed)
        self.assertEqual([p.suggestion_id for p in res.applied], ["i1"])
        after = self._md()
        after_bids = BID_RE.findall(after)
        self.assertEqual([b for b in after_bids if b in set(before_bids)], before_bids)
        self.assertEqual(len(after_bids), len(before_bids) + 1)
        paras = _paragraphs(after)
        self.assertIn("A brand-new paragraph.", paras[1])  # right after the anchor
        self.assertEqual(self.store.rows["i1"]["status"], "applied")

    def test_delete_removes_block_and_history_can_restore_bid(self):
        victim = bref(self.root, M03_MD, 1)
        victim_bid = BID_RE.search(_paragraphs(open(
            os.path.join(self.root, M03_MD), encoding="utf-8").read())[1]).group(1)
        self._add_op("d1", victim, "delete")
        res = self._run("b1", FakePipeline(SPEC))
        self.assertTrue(res.committed)
        after = self._md()
        self.assertNotIn(victim_bid, after)
        self.assertNotIn("paid in full", after)
        # history retains it: reverting the applied commit restores the EXACT bid
        _git(["revert", "--no-edit", "HEAD"], self.root)
        self.assertIn(victim_bid, self._md())

    def test_two_inserts_after_one_anchor_read_chronologically(self):
        anchor = bref(self.root, M03_MD, 0)
        self._add_op("i1", anchor, "insert_after", new_text="First addition.",
                     created_at=1000)
        self._add_op("i2", anchor, "insert_after", new_text="Second addition.",
                     created_at=2000)
        res = self._run("b1", FakePipeline(SPEC))
        self.assertEqual({p.suggestion_id for p in res.applied}, {"i1", "i2"})
        paras = [_strip_markers(p) for p in _paragraphs(self._md())]
        self.assertIn("First addition.", paras[1])
        self.assertIn("Second addition.", paras[2])

    def test_move_reorders_without_changing_bids(self):
        mover = bref(self.root, M03_MD, 0)
        dest = bref(self.root, M03_MD, 1)
        before = set(BID_RE.findall(self._md()))
        self._add_op("m1", mover, "move", op_arg=dest)
        res = self._run("b1", FakePipeline(SPEC))
        self.assertTrue(res.committed)
        self.assertEqual(set(BID_RE.findall(self._md())), before)
        paras = [_strip_markers(p) for p in _paragraphs(self._md())]
        self.assertIn("paid in full", paras[0])
        self.assertIn("Intake notes", paras[1])

    def test_split_and_merge_preserve_text_through_engine(self):
        # split p0 of the json body ("You represent the plaintiff in a negligence action.")
        ref = bref(self.root, M03_EX, 0, "sections.intro.body_md")
        self._add_op("s1", ref, "split",
                     new_text="You represent the plaintiff\n\nin a negligence action.")
        res = self._run("b1", FakePipeline(SPEC))
        self.assertTrue(res.committed)
        obj = json.loads(open(os.path.join(self.root, M03_EX), encoding="utf-8").read())
        body = obj["sections"]["intro"]["body_md"]
        paras = [_strip_markers(p) for p in _paragraphs(body)]
        self.assertEqual(paras[0], "You represent the plaintiff")
        self.assertEqual(paras[1], "in a negligence action.")
        # now merge them back through the engine
        self.index = resolve_index(self.root, SPEC)
        first = bref(self.root, M03_EX, 0, "sections.intro.body_md")
        second = bref(self.root, M03_EX, 1, "sections.intro.body_md")
        self._add_op("g1", first, "merge", op_arg=second)
        res2 = self._run("b2", FakePipeline(SPEC))
        self.assertTrue(res2.committed)
        obj2 = json.loads(open(os.path.join(self.root, M03_EX), encoding="utf-8").read())
        merged = _strip_markers(_paragraphs(obj2["sections"]["intro"]["body_md"])[0])
        self.assertEqual(merged, "You represent the plaintiff in a negligence action.")

    def test_structural_ambiguity_routes_needs_human(self):
        # merge of NON-adjacent blocks: p0 + p2 of the json body
        first = bref(self.root, M03_EX, 0, "sections.intro.body_md")
        third = bref(self.root, M03_EX, 2, "sections.intro.body_md")
        before = snapshot_data(self.root)
        self._add_op("x1", first, "merge", op_arg=third)
        res = self._run("b1", FakePipeline(SPEC), deploy_plan_only=True)
        self.assertEqual([r["id"] for r in res.needs_human], ["x1"])
        self.assertEqual([], res.applied)
        self.assertEqual(before, snapshot_data(self.root))

    def test_mixed_text_and_structural_on_one_file(self):
        anchor = bref(self.root, M03_MD, 0)
        blk = self.index[anchor]
        self.store.add(id="t1", source_ref=anchor, new_text="Revised intake notes.",
                       original_hash=blk["original_hash"], original_text=blk["original_text"],
                       kind="prose", json_path=None, status="accepted")
        self._add_op("i1", anchor, "insert_after", new_text="Added after the revision.")
        res = self._run("b1", FakePipeline(SPEC))
        self.assertEqual({p.suggestion_id for p in res.applied}, {"t1", "i1"})
        paras = [_strip_markers(p) for p in _paragraphs(self._md())]
        self.assertEqual(paras[0], "Revised intake notes.")
        self.assertEqual(paras[1], "Added after the revision.")

    def test_structural_rows_never_propose_companions(self):
        anchor = bref(self.root, M03_MD, 0)
        self._add_op("i1", anchor, "insert_after",
                     new_text="The revised retainer is $9,999 in total.")
        res = self._run("b1", FakePipeline(SPEC), deploy_plan_only=True)
        self.assertEqual([p.suggestion_id for p in res.applied], ["i1"])
        self.assertEqual(res.companions, [])


# --------------------------------------------------------------------------- #
# U5 — json_add (new facts) + Stage 1.5 fact syndication
# --------------------------------------------------------------------------- #
class FactsApplyTest(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.store = InMemoryEditorStore()
        self.client = FakeClient(self.store)
        self.index = resolve_index(self.root, SPEC)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, batch_id, pipeline, deploy_plan_only=False):
        return ap.run_apply(self.client, pipeline, batch_id,
                            worktree_parent=None, deploy_plan_only=deploy_plan_only,
                            branch="test", canonical_root=self.root, logger=lambda *a: None)

    def test_json_add_creates_a_new_fact_key(self):
        ref = "%s#custom_facts.deadline-note" % M03_EX
        self.store.add(id="fa1", source_ref=ref, kind="json_add",
                       json_path="custom_facts.deadline-note",
                       new_text="The demand letter response is due within 21 days.",
                       original_hash=None, original_text=None, status="accepted")
        res = self._run("b1", FakePipeline(SPEC))
        self.assertTrue(res.committed)
        self.assertEqual([p.suggestion_id for p in res.applied], ["fa1"])
        obj = json.loads(open(os.path.join(self.root, M03_EX), encoding="utf-8").read())
        self.assertEqual(obj["custom_facts"]["deadline-note"],
                         "The demand letter response is due within 21 days.")

    def test_json_add_never_overwrites_an_existing_key(self):
        # seed the key, commit, then try to add it again
        p = os.path.join(self.root, M03_EX)
        obj = json.loads(open(p, encoding="utf-8").read())
        obj["custom_facts"] = {"deadline-note": "Already here."}
        _write(self.root, M03_EX, json.dumps(obj, indent=2) + "\n")
        _git(["commit", "-aqm", "seed"], self.root)
        ref = "%s#custom_facts.deadline-note" % M03_EX
        self.store.add(id="fa2", source_ref=ref, kind="json_add",
                       json_path="custom_facts.deadline-note",
                       new_text="A silent overwrite attempt.",
                       original_hash=None, original_text=None, status="accepted")
        before = snapshot_data(self.root)
        res = self._run("b1", FakePipeline(SPEC), deploy_plan_only=True)
        self.assertEqual([r["id"] for r in res.needs_human], ["fa2"])
        self.assertEqual(before, snapshot_data(self.root))

    def test_json_add_rejects_forged_paths(self):
        ref = "%s#caption" % M03_EX
        self.store.add(id="fa3", source_ref=ref, kind="json_add",
                       json_path="caption", new_text="hijack",
                       original_hash=None, original_text=None, status="accepted")
        res = self._run("b1", FakePipeline(SPEC), deploy_plan_only=True)
        self.assertEqual([r["id"] for r in res.needs_human], ["fa3"])


    def test_fact_edit_syndicates_restated_prose_as_one_group(self):
        # caption is restated verbatim in a case-file paragraph
        cur = open(os.path.join(self.root, M03_MD), encoding="utf-8").read()
        _write(self.root, M03_MD, cur +
               "\nThe caption Osgard v. Meridian Freight (Tort) appears on every filing.\n")
        import stamp_block_ids as sb2
        existing = set()
        sb2.stamp_file(os.path.join(self.root, M03_MD), [], existing)
        _git(["commit", "-aqm", "restate caption"], self.root)
        self.index = resolve_index(self.root, SPEC)

        ref = "%s#caption" % M03_EX
        blk = self.index[ref]
        self.store.add(id="fs1", source_ref=ref, kind="json_scalar",
                       json_path="caption",
                       new_text="Osgard v. Meridian Freight Lines (Tort)",
                       original_hash=blk["original_hash"],
                       original_text=blk["original_text"], status="accepted")
        res = self._run("b1", FakePipeline(SPEC))
        self.assertTrue(res.committed)
        # ONE companion for the restated paragraph, in ONE group, never applied
        self.assertEqual(len(res.companions), 1)
        comp = res.companions[0]
        self.assertIn("Meridian Freight Lines", comp["new_text"])
        self.assertTrue(comp["group_id"].startswith("vs-"))
        self.assertEqual(self.store.rows[comp["id"]]["status"], "pending")

    def test_short_fact_values_never_flood_companions(self):
        ref = "%s#caption" % M03_EX
        blk = self.index[ref]
        # simulate a short old value by adding a row whose original is 3 chars
        self.store.add(id="fs2", source_ref=ref, kind="json_scalar",
                       json_path="caption", new_text="Ozzy",
                       original_hash=blk["original_hash"],
                       original_text=blk["original_text"], status="accepted")
        # the REAL original is long, so syndication may propose; now verify the
        # guard directly instead: a 3-char literal yields no proposals
        payloads = ap.propose_value_sync(
            self.root, [ap.Patch(
                suggestion_id="x", group_id="g", source_ref=ref,
                relpath=M03_EX, kind="json_scalar", json_path="caption",
                original_text="Oz.", new_text="Ozzy")],
            self.index, self.client, "bX")
        self.assertEqual(payloads, [])


class PageOverridePatchTest(unittest.TestCase):
    def test_override_record_is_surface_owned_and_auditable(self):
        record = ap.page_override_record(
            page="modules/m1.html",
            shared_source_ref="data/copy/home.json#volumes.modules.M1.title",
            value="A page-only title",
            editor="John Sonsteng",
            deliberate_at="2026-08-04T15:30:00Z",
        )
        self.assertEqual(record["page"], "modules/m1.html")
        self.assertEqual(record["shared_source_ref"],
                         "data/copy/home.json#volumes.modules.M1.title")
        self.assertEqual(record["value"], "A page-only title")
        self.assertEqual(record["intent"], "deliberate_page_override")
        self.assertEqual(record["deliberate_by"], "John Sonsteng")
        self.assertEqual(record["deliberate_at"], "2026-08-04T15:30:00Z")

    def test_override_path_is_stable_and_ordinary(self):
        relpath, json_path, source_ref = ap.page_override_address(
            "modules/m1.html",
            "data/copy/home.json#volumes.modules.M1.title",
        )
        self.assertEqual(relpath, "data/copy/home.json")
        self.assertRegex(json_path, r"^overrides\.[a-f0-9]{16}\.value$")
        self.assertEqual(source_ref, relpath + "#" + json_path)

    def test_page_only_is_refused_for_non_shared_leaf(self):
        with self.assertRaisesRegex(ap.ApplyError, "not shared"):
            ap.validate_page_override_occurrences(
                "home/index.html", [{"page": "home/index.html", "index": 1}])

    def test_numeric_override_stays_numeric(self):
        self.assertEqual(ap.coerce_page_override_value("42", 7), 42)
        self.assertIsInstance(ap.coerce_page_override_value("42", 7), int)

    def test_invalid_typed_overrides_are_refused(self):
        for proposed, current in (("truthy", True), ("1.5", 7),
                                  ("not-a-number", 1.0), ("nan", 1.0)):
            with self.subTest(proposed=proposed, current=current):
                with self.assertRaises(ValueError):
                    ap.coerce_page_override_value(proposed, current)

    def test_revert_deletes_only_the_surface_leaf(self):
        obj = {
            "schema_version": "1.0.0", "type": "page_copy", "page": "home",
            "overrides": {
                "a" * 16: {"page": "modules/m1.html", "value": "local"},
                "b" * 16: {"page": "modules/m2.html", "value": "other"},
            },
        }
        ap.delete_json_path(obj, "overrides." + "a" * 16)
        self.assertNotIn("a" * 16, obj["overrides"])
        self.assertEqual(obj["overrides"]["b" * 16]["value"], "other")

    def test_gate_and_patcher_create_then_remove_an_ordinary_override_leaf(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "data", "copy"))
            relpath = "data/copy/home.json"
            source_ref = relpath + "#volumes.modules.M1.title"
            _write(root, relpath, json.dumps({
                "schema_version": "1.0.0", "type": "page_copy", "page": "home",
                "volumes": {"modules": {"M1": {"title": 7}}},
            }, indent=2) + "\n")
            source_index = {source_ref: {
                "kind": "json_scalar", "json_path": "volumes.modules.M1.title",
                "original_text": 7, "original_hash": "hash",
                "occurrences": [{"page": "home/index.html", "index": 1},
                                {"page": "modules/m1.html", "index": 2}],
            }}
            row = {"id": "ov1", "kind": "page_override", "source_ref": source_ref,
                   "new_text": "42", "page": "modules/m1.html", "editor": "slot:john",
                   "comment": "JOS", "created_at": 1785871800000,
                   "original_hash": "hash"}
            duplicate = dict(row, id="ov2", new_text="43")
            status, duplicate_patches = ap._gate_group(
                [row, duplicate], source_index, root)
            self.assertEqual(status, "")
            self.assertEqual(ap.apply_file_patches(
                root, relpath, duplicate_patches), {
                    "ov1": ap.OUT_NEEDS_HUMAN,
                    "ov2": ap.OUT_NEEDS_HUMAN,
                })
            invalid = dict(row, id="ov-invalid", new_text="1.5")
            status, patches = ap._gate_group([invalid], source_index, root)
            self.assertEqual(status, ap.OUT_NEEDS_HUMAN)
            self.assertEqual(patches, [])
            status, patches = ap._gate_group([row], source_index, root)
            self.assertEqual(status, "")
            self.assertEqual(ap.apply_file_patches(root, relpath, patches), {"ov1": True})
            obj = json.load(open(os.path.join(root, relpath), encoding="utf-8"))
            key, record = next(iter(obj["overrides"].items()))
            self.assertEqual(record["value"], 42)
            self.assertEqual(record["deliberate_by"], "JOS")
            self.assertEqual(obj["volumes"]["modules"]["M1"]["title"], 7)

            stale = dict(row, id="ov-stale", original_hash="stale")
            status, patches = ap._gate_group([stale], source_index, root)
            self.assertEqual(status, ap.OUT_DRIFT)
            self.assertEqual(patches, [])

            override_ref = relpath + "#overrides.%s.value" % key
            revert_index = {override_ref: {"kind": "json_scalar", "original_text": 42}}
            revert_row = {
                "id": "rv1", "kind": "page_override_revert", "source_ref": override_ref,
                "original_hash": "fresh",
            }
            revert_index[override_ref]["original_hash"] = "fresh"
            status, patches = ap._gate_group([revert_row], revert_index, root)
            self.assertEqual(status, "")
            self.assertEqual(ap.apply_file_patches(root, relpath, patches), {"rv1": True})
            reverted = json.load(open(os.path.join(root, relpath), encoding="utf-8"))
            self.assertEqual(reverted["volumes"]["modules"]["M1"]["title"], 7)
            self.assertEqual(reverted.get("overrides"), {})

            stale_revert = dict(revert_row, id="rv-stale", original_hash="stale")
            status, patches = ap._gate_group([stale_revert], revert_index, root)
            self.assertEqual(status, ap.OUT_DRIFT)
            self.assertEqual(patches, [])
