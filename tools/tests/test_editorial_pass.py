#!/usr/bin/env python3
r"""Tests for tools/editorial_pass.py — the post-hoc editorial pass.

Pure-logic + orchestration tests: NO live model, NO network, NO git. The commit
reader, diff reader, CLI runner, flag filer, and notifier are injected; commit
selection, flag parsing, payload shape, filing best-effort, graceful degradation,
and dry-run are exercised directly.

Run:  python3 -m pytest tools/tests/test_editorial_pass.py -q
"""

from __future__ import annotations

import io
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import editorial_pass as ep  # noqa: E402


class TestSelectApplyCommits(unittest.TestCase):
    def test_selects_only_apply_engine_commits(self):
        log = "\n".join([
            "sha1\tapply@sonsteng.local\tapply: batch batch-A (3 suggestions)",
            "sha2\tdamienriehl@gmail.com\tfeat: something else",
            "sha3\tapply@sonsteng.local\tapply: batch batch-B (1 suggestions)",
            "sha4\tapply@sonsteng.local\tchore: not an apply subject",
        ])
        self.assertEqual(ep.select_apply_commits(log), ["sha1", "sha3"])

    def test_empty(self):
        self.assertEqual(ep.select_apply_commits(""), [])


class TestParseFlags(unittest.TestCase):
    def test_bare_object(self):
        raw = '{"flags":[{"source_ref":"data/matters/m03/x.json","severity":"voice","message":"drifty"}]}'
        flags = ep.parse_flags(raw)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["severity"], "voice")

    def test_cli_json_envelope(self):
        # `claude --output-format json` wraps the answer in {result: "<text>"}.
        raw = '{"type":"result","result":"{\\"flags\\":[{\\"source_ref\\":\\"data/a\\",\\"severity\\":\\"consistency\\",\\"message\\":\\"m\\"}]}"}'
        flags = ep.parse_flags(raw)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["source_ref"], "data/a")

    def test_envelope_with_flags_key_directly(self):
        raw = '{"flags":[], "result":"ignored"}'
        self.assertEqual(ep.parse_flags(raw), [])

    def test_object_embedded_in_prose(self):
        raw = 'Here you go:\n{"flags":[{"source_ref":"data/b","message":"x"}]}\nThanks!'
        flags = ep.parse_flags(raw)
        self.assertEqual(flags[0]["source_ref"], "data/b")
        self.assertEqual(flags[0]["severity"], "note")  # defaulted

    def test_malformed_returns_empty(self):
        self.assertEqual(ep.parse_flags("not json at all"), [])
        self.assertEqual(ep.parse_flags(""), [])
        self.assertEqual(ep.parse_flags("{broken"), [])

    def test_drops_flags_missing_ref_or_message(self):
        raw = '{"flags":[{"severity":"voice"},{"source_ref":"data/c","message":"ok"}]}'
        flags = ep.parse_flags(raw)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["source_ref"], "data/c")

    def test_unknown_severity_normalised_to_note(self):
        raw = '{"flags":[{"source_ref":"data/d","severity":"BOGUS","message":"m"}]}'
        self.assertEqual(ep.parse_flags(raw)[0]["severity"], "note")


class TestFlagPayload(unittest.TestCase):
    def test_shape_is_comment_only_ai_rewrite(self):
        flag = {"source_ref": "data/matters/m03/x.json", "severity": "contradiction",
                "message": "Date says 2024 but heading says 2025."}
        p = ep.flag_payload(flag, salt="batch-A")
        self.assertEqual(p["origin"], "ai_rewrite")
        self.assertEqual(p["source_ref"], flag["source_ref"])
        self.assertNotIn("new_text", p)              # COMMENT only -> stored as `comment` kind
        self.assertTrue(p["comment"].startswith("[editorial:contradiction]"))
        # id must satisfy the Worker's uuid ceiling [a-zA-Z0-9_-]{8,64}.
        self.assertRegex(p["id"], r"^[a-zA-Z0-9_-]{8,64}$")

    def test_id_deterministic_idempotent(self):
        flag = {"source_ref": "data/x", "severity": "note", "message": "same"}
        self.assertEqual(ep.flag_payload(flag, "s")["id"], ep.flag_payload(flag, "s")["id"])

    def test_id_differs_by_ref(self):
        a = ep.flag_payload({"source_ref": "data/a", "severity": "note", "message": "m"}, "s")
        b = ep.flag_payload({"source_ref": "data/b", "severity": "note", "message": "m"}, "s")
        self.assertNotEqual(a["id"], b["id"])


class TestBuildPrompt(unittest.TestCase):
    def test_includes_dimensions_and_diffs(self):
        prompt = ep.build_prompt(["diff --git a/data/x b/data/x\n+new line"])
        self.assertIn("VOICE", prompt)
        self.assertIn("CONSISTENCY", prompt)
        self.assertIn("FACTUAL SELF-CONTRADICTION", prompt)
        self.assertIn("+new line", prompt)
        self.assertIn('"flags"', prompt)

    def test_truncates_huge_diffs(self):
        prompt = ep.build_prompt(["x" * 100000], max_chars=100)
        self.assertIn("truncated", prompt)


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def __call__(self, flags, *, trigger, filed_ok, degraded_reason=None):
        self.calls.append({"flags": list(flags), "filed_ok": filed_ok,
                           "degraded_reason": degraded_reason, "trigger": trigger})


def _run(**kw):
    defaults = dict(api_base="https://x/edit/v1", token="tok", trigger="daily",
                    out=io.StringIO())
    defaults.update(kw)
    return ep.run(**defaults)


class TestOrchestration(unittest.TestCase):
    def test_happy_path_files_all_flags(self):
        filed = []
        raw = ('{"flags":[{"source_ref":"data/a","severity":"voice","message":"m1"},'
               '{"source_ref":"data/b","severity":"consistency","message":"m2"}]}')
        notifier = FakeNotifier()
        res = _run(
            commits_reader=lambda: ["sha1"],
            diff_reader=lambda shas: ["diff body"],
            cli_runner=lambda prompt: (True, raw, None),
            flag_filer=lambda p: (filed.append(p) or (True, 200)),
            notifier=notifier)
        self.assertEqual(res.filed, 2)
        self.assertEqual(len(filed), 2)
        self.assertEqual(notifier.calls[-1]["filed_ok"], 2)

    def test_no_commits_pings_zero(self):
        notifier = FakeNotifier()
        res = _run(commits_reader=lambda: [], notifier=notifier)
        self.assertEqual(res.flags, [])
        self.assertEqual(notifier.calls[-1]["filed_ok"], 0)

    def test_filing_best_effort_one_bad_ref_skipped(self):
        # A flag whose source_ref is unknown -> validation_error; the rest still file.
        raw = ('{"flags":[{"source_ref":"data/good","severity":"note","message":"ok"},'
               '{"source_ref":"data/BAD","severity":"note","message":"bad"}]}')

        def filer(p):
            return (False, "http_400") if "BAD" in p["source_ref"] else (True, 200)

        res = _run(
            commits_reader=lambda: ["sha1"],
            diff_reader=lambda shas: ["diff"],
            cli_runner=lambda prompt: (True, raw, None),
            flag_filer=filer, notifier=FakeNotifier())
        self.assertEqual(len(res.flags), 2)
        self.assertEqual(res.filed, 1)  # only the good one filed; no crash

    def test_dry_run_files_nothing(self):
        raw = '{"flags":[{"source_ref":"data/a","severity":"note","message":"m"}]}'
        filed = []
        res = _run(
            commits_reader=lambda: ["sha1"],
            diff_reader=lambda shas: ["diff"],
            cli_runner=lambda prompt: (True, raw, None),
            flag_filer=lambda p: (filed.append(p) or (True, 200)),
            dry_run=True)
        self.assertEqual(res.filed, 0)
        self.assertEqual(filed, [])          # nothing filed
        self.assertEqual(len(res.payloads), 1)  # but the plan is returned


class TestGracefulDegradation(unittest.TestCase):
    def test_cli_not_found_degrades_cleanly(self):
        notifier = FakeNotifier()
        res = _run(
            commits_reader=lambda: ["sha1"],
            diff_reader=lambda shas: ["diff"],
            cli_runner=lambda prompt: (False, "", "cli_not_found"),
            flag_filer=lambda p: (True, 200), notifier=notifier)
        self.assertEqual(res.degraded_reason, "cli_not_found")
        self.assertEqual(res.filed, 0)
        self.assertEqual(notifier.calls[-1]["degraded_reason"], "cli_not_found")

    def test_cli_timeout_degrades(self):
        res = _run(
            commits_reader=lambda: ["sha1"],
            diff_reader=lambda shas: ["diff"],
            cli_runner=lambda prompt: (False, "", "cli_timeout"),
            flag_filer=lambda p: (True, 200), notifier=FakeNotifier())
        self.assertEqual(res.degraded_reason, "cli_timeout")

    def test_run_cli_missing_binary_returns_reason_not_raise(self):
        ok, raw, reason = ep.run_cli("prompt", cli="definitely-not-a-real-binary-xyz")
        self.assertFalse(ok)
        self.assertEqual(reason, "cli_not_found")


if __name__ == "__main__":
    unittest.main()
