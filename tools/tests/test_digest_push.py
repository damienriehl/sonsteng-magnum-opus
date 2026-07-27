#!/usr/bin/env python3
r"""Tests for tools/digest_push.py — the batched cumulative ntfy digest.

Pure-logic + dedupe tests: NO network, NO live ntfy, NO live Worker. The fetch
and publish steps are injected with fakes; the digest builder and the dedupe
state machine are exercised directly.

Run:  python3 -m pytest tools/tests/test_digest_push.py -q
  or: python3 tools/tests/test_digest_push.py
"""

from __future__ import annotations

import os
import sys
import io
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import digest_push as dp  # noqa: E402


def row(id, status="pending", source_ref="data/matters/m03/interview.json", page="m03"):
    return {"id": id, "status": status, "source_ref": source_ref, "page": page}


REVIEW_URL = "https://example.test/edit/review"


class TestBuildDigest(unittest.TestCase):
    def test_empty(self):
        d = dp.build_digest([], REVIEW_URL)
        self.assertEqual(d.count, 0)
        self.assertEqual(d.signature, "")
        self.assertEqual(d.by_matter, [])
        self.assertIn("No suggestions", d.body())

    def test_counts_only_reviewable_statuses(self):
        rows = [
            row("a", "pending"),
            row("b", "drift"),
            row("c", "needs_human"),
            row("d", "accepted_blocked"),
            row("e", "applied"),      # terminal — excluded
            row("f", "declined"),     # terminal — excluded
            row("g", "in_flight"),    # mid-apply — excluded
            row("h", "accepted"),     # accepted-not-yet-applied — excluded (not "waiting on reviewer")
            row("i", "superseded"),   # draft — excluded
        ]
        d = dp.build_digest(rows, REVIEW_URL)
        self.assertEqual(d.count, 4)
        self.assertEqual(d.by_status, {"pending": 1, "drift": 1, "needs_human": 1, "accepted_blocked": 1})

    def test_per_matter_breakdown(self):
        rows = [
            row("a", source_ref="data/matters/m03/x.json"),
            row("b", source_ref="data/matters/m03/y.json"),
            row("c", source_ref="data/matters/m11/z.json"),
            row("d", source_ref="data/firm/firm.json"),  # no matter -> None
        ]
        d = dp.build_digest(rows, REVIEW_URL)
        # m03 (2) first, then m11 (1), then None (1) sorts last.
        self.assertEqual(d.by_matter[0], ("m03", 2))
        matters = dict(d.by_matter)
        self.assertEqual(matters["m03"], 2)
        self.assertEqual(matters["m11"], 1)
        self.assertEqual(matters[None], 1)

    def test_matter_prefix_not_confused(self):
        # m3 / m30 must never be read as m03's matter.
        d = dp.build_digest([row("a", source_ref="data/matters/m30/x.json")], REVIEW_URL)
        self.assertEqual(dict(d.by_matter), {"m30": 1})

    def test_signature_is_order_independent(self):
        a = dp.build_digest([row("x"), row("y"), row("z")], REVIEW_URL)
        b = dp.build_digest([row("z"), row("x"), row("y")], REVIEW_URL)
        self.assertEqual(a.signature, b.signature)

    def test_signature_changes_with_membership(self):
        a = dp.build_digest([row("x"), row("y")], REVIEW_URL)
        b = dp.build_digest([row("x"), row("y"), row("z")], REVIEW_URL)
        self.assertNotEqual(a.signature, b.signature)

    def test_signature_ignores_terminal_membership(self):
        # Adding an already-applied row must NOT change the signature.
        a = dp.build_digest([row("x"), row("y")], REVIEW_URL)
        b = dp.build_digest([row("x"), row("y"), row("z", "applied")], REVIEW_URL)
        self.assertEqual(a.signature, b.signature)

    def test_body_is_content_light(self):
        # The body must carry counts + matter, never the suggestion text/new_text.
        rows = [{"id": "x", "status": "pending", "source_ref": "data/matters/m03/x.json",
                 "page": "m03", "new_text": "SECRET CLIENT TEXT", "original_text": "also secret"}]
        d = dp.build_digest(rows, REVIEW_URL)
        self.assertNotIn("SECRET", d.body())
        self.assertIn("m03", d.body())

    def test_title_singular_plural(self):
        one = dp.build_digest([row("x")], REVIEW_URL)
        self.assertIn("1 suggestion to review", one.title())
        many = dp.build_digest([row("x"), row("y")], REVIEW_URL)
        self.assertIn("2 suggestions to review", many.title())


class TestShouldNotify(unittest.TestCase):
    def test_quiet_when_zero(self):
        d = dp.build_digest([], REVIEW_URL)
        self.assertFalse(dp.should_notify(d, ""))

    def test_notify_when_new(self):
        d = dp.build_digest([row("x")], REVIEW_URL)
        self.assertTrue(dp.should_notify(d, ""))

    def test_quiet_when_unchanged(self):
        d = dp.build_digest([row("x"), row("y")], REVIEW_URL)
        self.assertFalse(dp.should_notify(d, d.signature))

    def test_notify_when_changed(self):
        old = dp.build_digest([row("x")], REVIEW_URL)
        new = dp.build_digest([row("x"), row("y")], REVIEW_URL)
        self.assertTrue(dp.should_notify(new, old.signature))


class _Capture:
    """Collects publish() calls so we can assert what would have been sent."""
    def __init__(self):
        self.calls = []

    def __call__(self, topic, title, body, click_url, **kw):
        self.calls.append({"topic": topic, "title": title, "body": body, "click": click_url})
        return 200


class TestRunDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self._env_backup = {k: os.environ.get(k) for k in
                            (dp.ENV_API_BASE, dp.ENV_SERVICE_TOKEN, dp.ENV_REVIEW_URL, dp.ENV_EDIT_ORIGIN)}
        os.environ[dp.ENV_API_BASE] = "https://worker.test/edit/v1"
        os.environ[dp.ENV_SERVICE_TOKEN] = "token-not-used-by-fake"
        os.environ[dp.ENV_REVIEW_URL] = REVIEW_URL

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run(self, rows, publish, **kw):
        return dp.run(fetch=lambda *_a, **_k: rows, publish=publish,
                      topic_resolver=lambda: "test-topic", state_path=self.state,
                      now_iso="2026-07-18T00:00:00+00:00", out=io.StringIO(), **kw)

    def test_first_run_notifies_and_persists(self):
        pub = _Capture()
        res = self._run([row("x"), row("y")], pub)
        self.assertTrue(res["notified"])
        self.assertEqual(len(pub.calls), 1)
        self.assertEqual(pub.calls[0]["click"], REVIEW_URL)
        self.assertTrue(os.path.exists(self.state))

    def test_second_run_same_set_is_quiet(self):
        pub = _Capture()
        self._run([row("x"), row("y")], pub)
        self._run([row("x"), row("y")], pub)
        self.assertEqual(len(pub.calls), 1)  # only the first fired

    def test_changed_set_renotifies(self):
        pub = _Capture()
        self._run([row("x")], pub)
        self._run([row("x"), row("y")], pub)  # new member -> re-notify
        self.assertEqual(len(pub.calls), 2)

    def test_drain_to_zero_clears_state_then_renotifies(self):
        pub = _Capture()
        self._run([row("x")], pub)             # notify #1, state written
        self._run([], pub)                     # drained -> quiet, state cleared
        self.assertFalse(os.path.exists(self.state))
        self._run([row("x")], pub)             # same id, but state was cleared -> notify #2
        self.assertEqual(len(pub.calls), 2)

    def test_dry_run_never_publishes_or_persists(self):
        pub = _Capture()
        res = self._run([row("x"), row("y")], pub, dry_run=True)
        self.assertFalse(res["notified"])
        self.assertEqual(res["reason"], "dry_run")
        self.assertEqual(len(pub.calls), 0)
        self.assertFalse(os.path.exists(self.state))

    def test_nothing_pending_reason(self):
        pub = _Capture()
        res = self._run([], pub)
        self.assertEqual(res["reason"], "nothing_pending")
        self.assertFalse(res["notified"])


class TestHelpers(unittest.TestCase):
    def test_matter_of(self):
        self.assertEqual(dp._matter_of("data/matters/m07/interview.json"), "m07")
        self.assertIsNone(dp._matter_of("data/firm/firm.json"))
        self.assertIsNone(dp._matter_of(""))

    def test_review_url_from_origin(self):
        backup = os.environ.get(dp.ENV_EDIT_ORIGIN), os.environ.get(dp.ENV_REVIEW_URL)
        try:
            os.environ.pop(dp.ENV_REVIEW_URL, None)
            os.environ[dp.ENV_EDIT_ORIGIN] = "https://w.example"
            self.assertEqual(dp.review_url_from_env(), "https://w.example/edit/review")
        finally:
            if backup[0] is None:
                os.environ.pop(dp.ENV_EDIT_ORIGIN, None)
            else:
                os.environ[dp.ENV_EDIT_ORIGIN] = backup[0]
            if backup[1] is not None:
                os.environ[dp.ENV_REVIEW_URL] = backup[1]

    def test_resolve_topic_env_wins(self):
        backup = os.environ.get(dp.ENV_NTFY_TOPIC)
        try:
            os.environ[dp.ENV_NTFY_TOPIC] = "my-topic"
            self.assertEqual(dp.resolve_topic(), "my-topic")
        finally:
            if backup is None:
                os.environ.pop(dp.ENV_NTFY_TOPIC, None)
            else:
                os.environ[dp.ENV_NTFY_TOPIC] = backup


if __name__ == "__main__":
    unittest.main()


class ReviewUrlFromListOrigin(unittest.TestCase):
    """EDIT_ORIGIN became a comma-separated list with the Access door (KTD6).

    Concatenating the whole list onto "/edit/review" would have produced a
    click-through URL that silently 404s -- the ntfy nudge would still fire and
    still look fine, and the tap would land nowhere.
    """

    def _url_with(self, origin):
        backup = os.environ.get(dp.ENV_EDIT_ORIGIN), os.environ.get(dp.ENV_REVIEW_URL)
        try:
            os.environ.pop(dp.ENV_REVIEW_URL, None)
            os.environ[dp.ENV_EDIT_ORIGIN] = origin
            return dp.review_url_from_env()
        finally:
            os.environ.pop(dp.ENV_EDIT_ORIGIN, None)
            os.environ.pop(dp.ENV_REVIEW_URL, None)
            if backup[0] is not None:
                os.environ[dp.ENV_EDIT_ORIGIN] = backup[0]
            if backup[1] is not None:
                os.environ[dp.ENV_REVIEW_URL] = backup[1]

    def test_single_origin_unchanged(self):
        self.assertEqual(
            self._url_with("https://w.example"), "https://w.example/edit/review"
        )

    def test_list_takes_the_first_entry_only(self):
        url = self._url_with("https://a.example,https://b.example")
        self.assertEqual(url, "https://a.example/edit/review")
        self.assertNotIn(",", url)

    def test_list_with_whitespace(self):
        self.assertEqual(
            self._url_with("  https://a.example , https://b.example "),
            "https://a.example/edit/review",
        )

    def test_empty_first_entry_falls_back_to_default(self):
        self.assertEqual(self._url_with(" , "), dp.DEFAULT_REVIEW_URL)
