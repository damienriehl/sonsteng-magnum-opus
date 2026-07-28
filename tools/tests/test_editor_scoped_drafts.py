#!/usr/bin/env python3
"""Tests for tools/editor_scoped_drafts.py — the U7 drafter.

enumerate -> draft -> submit as ONE ai_rewrite group, with the canary ->
remainder progression for module/course scopes (KTD4/KTD5). The CLI call, the
RPC client and the map are injectable; no live model, no network.

Run:  python3 -m pytest tools/tests/test_editor_scoped_drafts.py -q
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import editor_scoped_drafts as sd  # noqa: E402


def fixture_map():
    """A miniature editor map with scopes, two matters, one module."""
    def blk(idx, ref, text, kind="prose", json_path=None):
        return {"index": idx, "kind": kind, "source_ref": ref,
                "original_text": text, "original_hash": "h" + str(idx),
                "has_inline_formatting": False, "context": "ctx",
                "json_path": json_path}
    return {
        "spine_build_id": "fixture",
        "scopes": {
            "matters": {
                "m01-alpha": {"id": "m01", "parts": ["case-file", "exercise", "matter"]},
                "m02-beta": {"id": "m02", "parts": ["case-file", "exercise", "matter"]},
            },
            "modules": {
                "M1": {"curriculum": "data/curriculum/m1.md",
                       "matters": ["m01-alpha", "m02-beta"]},
            },
        },
        "pages": {
            "matters/m01-alpha/index.html": [
                blk(0, "data/matters/m01-alpha/case-file/a.md#b00000001",
                    "The filing deadline is 14 days."),
                blk(1, "data/matters/m01-alpha/exercise/exercise.json#sections.intro.body_md.b00000002",
                    "You have 14 days to file."),
                blk(2, "data/matters/m01-alpha/matter.json#caption",
                    "Alpha v. Omega", kind="json_scalar", json_path="caption"),
            ],
            "matters/m02-beta/index.html": [
                blk(0, "data/matters/m02-beta/case-file/b.md#b00000003",
                    "An unrelated paragraph."),
                blk(1, "data/matters/m02-beta/exercise/exercise.json#sections.intro.body_md.b00000004",
                    "The deadline of 14 days applies here too."),
            ],
            "modules/m1.html": [
                blk(0, "data/curriculum/m1.md#b00000005",
                    "Module one instructs on the 14 day rule."),
            ],
        },
    }


class FakeClient:
    def __init__(self, requests=None, outcomes=None):
        self.requests = requests or []
        self.outcomes = outcomes or {}
        self.claimed = []
        self.resolved = []
        self.proposed = []

    def fetch_scoped_requests(self, status):
        return [r for r in self.requests if r["status"] == status]

    def claim_scoped(self, rid):
        self.claimed.append(rid)
        return {"ok": True}

    def resolve_scoped(self, rid, **patch):
        self.resolved.append((rid, patch))
        for r in self.requests:
            if r["id"] == rid:
                r.update({"status": patch.get("status", r["status"])})
                for k in ("group_id", "phase", "canary_matter"):
                    if patch.get(k) is not None:
                        r[k] = patch[k]
        return {"ok": True}

    def propose_scoped(self, payload):
        self.proposed.append(payload)
        return {"ok": True, "id": payload["id"], "status": "pending"}

    def group_status(self, gid):
        return {"ok": True, "outcome": self.outcomes.get(gid, {"total": 0, "by_status": {}})}


def fake_cli_deadline(prompt):
    """Drafts a 14->30 day change ONLY for blocks whose text mentions it."""
    body = json.loads(prompt[prompt.index("BLOCKS_JSON:") + len("BLOCKS_JSON:"):])
    drafts = []
    for b in body["blocks"]:
        if "14 day" in b["original_text"] or "14 days" in b["original_text"]:
            drafts.append({"source_ref": b["source_ref"],
                           "new_text": b["original_text"].replace("14", "30")})
    return json.dumps({"drafts": drafts})


class TestEnumerate(unittest.TestCase):
    def test_matter_part_module_course(self):
        m = fixture_map()
        self.assertEqual(len(sd.enumerate_blocks(m, {"level": "course"})), 6)
        self.assertEqual(len(sd.enumerate_blocks(m, {"level": "matter", "matter": "m01-alpha"})), 3)
        self.assertEqual(
            [b["source_ref"] for b in sd.enumerate_blocks(
                m, {"level": "part", "matter": "m01-alpha", "part": "case-file"})],
            ["data/matters/m01-alpha/case-file/a.md#b00000001"])
        mod = sd.enumerate_blocks(m, {"level": "module", "module": "M1"})
        self.assertEqual(len(mod), 3)  # curriculum + both matters' exercise blocks

    def test_unknown_scope_raises(self):
        with self.assertRaises(sd.ScopedError):
            sd.enumerate_blocks(fixture_map(), {"level": "matter", "matter": "m99"})


class TestDraftPhase(unittest.TestCase):
    def _req(self, **over):
        base = {"id": "req1", "editor": "slot:john", "level": "matter",
                "matter": "m01-alpha", "part": None, "module": None,
                "instruction": "Change the filing deadline from 14 to 30 days.",
                "status": "requested", "phase": "all", "group_id": None,
                "canary_matter": None, "radius_blocks": 3}
        base.update(over)
        return base

    def test_matter_request_drafts_only_matching_blocks_as_one_group(self):
        client = FakeClient([self._req()])
        sd.run_once(client, fixture_map(), fake_cli_deadline, out=None)
        self.assertEqual(client.claimed, ["req1"])
        # two m01 blocks mention the deadline; the scalar caption does not
        self.assertEqual(len(client.proposed), 2)
        gids = {p["group_id"] for p in client.proposed}
        self.assertEqual(len(gids), 1)
        for p in client.proposed:
            self.assertEqual(p["origin"], "ai_rewrite")
            self.assertIn("30", p["new_text"])
            self.assertIn(self._req()["instruction"], p["comment"])
        drafted = [r for r in client.resolved if r[1]["status"] == "drafted"]
        self.assertEqual(len(drafted), 1)
        self.assertEqual(drafted[0][0], "req1")

    def test_module_request_drafts_canary_matter_only_first(self):
        client = FakeClient([self._req(level="module", module="M1", matter=None,
                                       phase="canary")])
        sd.run_once(client, fixture_map(), fake_cli_deadline, out=None)
        refs = [p["source_ref"] for p in client.proposed]
        # canary = alphabetically first member (m01-alpha): only ITS exercise
        # block drafts; m02's matching block and the curriculum wait.
        self.assertEqual(refs, [
            "data/matters/m01-alpha/exercise/exercise.json#sections.intro.body_md.b00000002"])
        drafted = [p for r, p in client.resolved if p["status"] == "drafted"][0]
        self.assertEqual(drafted["canary_matter"], "m01-alpha")

    def test_zero_matches_resolves_failed_never_silent(self):
        client = FakeClient([self._req(
            instruction="Mentions nothing that exists.",
            matter="m02-beta")])
        def cli_none(prompt):
            return json.dumps({"drafts": []})
        sd.run_once(client, fixture_map(), cli_none, out=None)
        self.assertEqual(client.proposed, [])
        self.assertEqual(client.resolved[-1][1]["status"], "failed")

    def test_draft_validation_rejects_forged_refs_and_markers(self):
        client = FakeClient([self._req()])
        def cli_evil(prompt):
            return json.dumps({"drafts": [
                {"source_ref": "data/evil.md#bdeadbeef", "new_text": "x"},
                {"source_ref": "data/matters/m01-alpha/case-file/a.md#b00000001",
                 "new_text": "sneaky {#b:00000000} marker"},
                {"source_ref": "data/matters/m01-alpha/case-file/a.md#b00000001",
                 "new_text": "The filing deadline is 14 days."},  # unchanged
            ]})
        sd.run_once(client, fixture_map(), cli_evil, out=None)
        self.assertEqual(client.proposed, [])          # nothing valid survived
        self.assertEqual(client.resolved[-1][1]["status"], "failed")


class TestProgression(unittest.TestCase):
    def _drafted(self, **over):
        base = {"id": "req1", "editor": "slot:john", "level": "module",
                "matter": None, "part": None, "module": "M1",
                "instruction": "Change the filing deadline from 14 to 30 days.",
                "status": "drafted", "phase": "canary",
                "group_id": "scoped-req1-canary", "canary_matter": "m01-alpha",
                "radius_blocks": 3}
        base.update(over)
        return base

    def test_canary_all_applied_drafts_the_remainder(self):
        client = FakeClient([self._drafted()],
                            outcomes={"scoped-req1-canary":
                                      {"total": 1, "by_status": {"applied": 1}}})
        sd.run_once(client, fixture_map(), fake_cli_deadline, out=None)
        refs = [p["source_ref"] for p in client.proposed]
        # remainder = the module's OTHER blocks that match: m02's exercise + curriculum
        self.assertEqual(sorted(refs), [
            "data/curriculum/m1.md#b00000005",
            "data/matters/m02-beta/exercise/exercise.json#sections.intro.body_md.b00000004"])
        self.assertTrue(all(p["group_id"] == "scoped-req1-remainder"
                            for p in client.proposed))
        last = client.resolved[-1][1]
        self.assertEqual(last["status"], "drafted")
        self.assertEqual(last["phase"], "remainder")

    def test_canary_declined_declines_the_request(self):
        client = FakeClient([self._drafted()],
                            outcomes={"scoped-req1-canary":
                                      {"total": 1, "by_status": {"declined": 1}}})
        sd.run_once(client, fixture_map(), fake_cli_deadline, out=None)
        self.assertEqual(client.proposed, [])
        self.assertEqual(client.resolved[-1][1]["status"], "declined")

    def test_remainder_applied_resolves_done(self):
        client = FakeClient([self._drafted(phase="remainder",
                                           group_id="scoped-req1-remainder")],
                            outcomes={"scoped-req1-remainder":
                                      {"total": 2, "by_status": {"applied": 2}}})
        sd.run_once(client, fixture_map(), fake_cli_deadline, out=None)
        self.assertEqual(client.resolved[-1][1]["status"], "done")

    def test_pending_canary_waits_untouched(self):
        client = FakeClient([self._drafted()],
                            outcomes={"scoped-req1-canary":
                                      {"total": 1, "by_status": {"pending": 1}}})
        sd.run_once(client, fixture_map(), fake_cli_deadline, out=None)
        self.assertEqual(client.proposed, [])
        self.assertEqual(client.resolved, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
