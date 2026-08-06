#!/usr/bin/env python3
r"""Tests for tools/todo_report.py — the docs/TODO.md parser and its nudge.

Pure-logic + dedupe tests: NO network, NO live ntfy. The publish step is injected
with a fake; the parser, the report builder, and the dedupe state machine are
exercised directly. The real docs/TODO.md is also parsed, so a format break in the
document itself fails the suite rather than silently emptying the reminder.

Run:  python3 -m pytest tools/tests/test_todo_report.py -q
  or: python3 tools/tests/test_todo_report.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import todo_report as tr  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_TODO = os.path.join(REPO_ROOT, "docs", "TODO.md")

SAMPLE = """# Sample

## Format

```
- [ ] **T99 — Not a real task, it is inside a fence** `@nobody`
```

## Today

- [x] **T01 — Collect the disc** `@damien` `due:2026-08-06` `origin:call-2026-08-06`
      Detail line that the parser ignores.
- [ ] **T02 — Overdue thing** `@damien` `due:2026-08-01` `origin:call-2026-08-06`

## Later

- [ ] **T03 — Due today** `@john` `due:2026-08-06`
- [ ] **T04 — No date at all** `@agent`
- [-] **T05 — Dropped on purpose** `@agent`
- not a task line at all
"""


class FakePublisher:
    def __init__(self):
        self.calls = []

    def __call__(self, topic, title, body, click, priority="default", **kw):
        self.calls.append({"topic": topic, "title": title, "body": body,
                           "click": click, "priority": priority})
        return 200


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.tasks = tr.parse_tasks(SAMPLE)

    def test_fenced_example_is_not_a_task(self):
        self.assertNotIn("T99", [t.id for t in self.tasks])

    def test_finds_every_real_task(self):
        self.assertEqual([t.id for t in self.tasks],
                         ["T01", "T02", "T03", "T04", "T05"])

    def test_statuses(self):
        by_id = {t.id: t for t in self.tasks}
        self.assertEqual(by_id["T01"].status, "done")
        self.assertEqual(by_id["T02"].status, "open")
        self.assertEqual(by_id["T05"].status, "dropped")

    def test_tags(self):
        by_id = {t.id: t for t in self.tasks}
        self.assertEqual(by_id["T01"].owner, "damien")
        self.assertEqual(by_id["T01"].due, "2026-08-06")
        self.assertEqual(by_id["T01"].origin, "call-2026-08-06")
        self.assertIsNone(by_id["T04"].due)

    def test_section_is_captured(self):
        by_id = {t.id: t for t in self.tasks}
        self.assertEqual(by_id["T01"].section, "Today")
        self.assertEqual(by_id["T03"].section, "Later")

    def test_title_excludes_the_tags(self):
        by_id = {t.id: t for t in self.tasks}
        self.assertEqual(by_id["T02"].title, "Overdue thing")

    def test_no_duplicate_ids_in_sample(self):
        self.assertEqual(tr.duplicate_ids(self.tasks), [])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.report = tr.build_report(tr.parse_tasks(SAMPLE), "2026-08-06")

    def test_open_excludes_done_and_dropped(self):
        self.assertEqual(sorted(t.id for t in self.report["open"]),
                         ["T02", "T03", "T04"])

    def test_overdue_is_strictly_before_today(self):
        self.assertEqual([t.id for t in self.report["overdue"]], ["T02"])

    def test_due_today_is_separate_from_overdue(self):
        self.assertEqual([t.id for t in self.report["due_today"]], ["T03"])

    def test_dropped_is_never_open(self):
        self.assertEqual([t.id for t in self.report["dropped"]], ["T05"])

    def test_grouped_by_owner(self):
        self.assertEqual(sorted(self.report["by_owner"]), ["agent", "damien", "john"])

    def test_body_is_content_light(self):
        """Task detail prose must never ride out in a notification."""
        body = tr.notify_body(self.report, "2026-08-06")
        self.assertNotIn("Detail line", body)
        self.assertIn("T02", body)

    def test_overdue_drives_the_title(self):
        self.assertIn("overdue", tr.notify_title(self.report))


class SignatureTests(unittest.TestCase):
    def sig(self, text, today="2026-08-06"):
        return tr.signature(tr.build_report(tr.parse_tasks(text), today), today)

    def test_retitling_does_not_refire(self):
        other = SAMPLE.replace("No date at all", "Renamed entirely")
        self.assertEqual(self.sig(SAMPLE), self.sig(other))

    def test_closing_a_task_refires(self):
        other = SAMPLE.replace("- [ ] **T04", "- [x] **T04")
        self.assertNotEqual(self.sig(SAMPLE), self.sig(other))

    def test_opening_a_task_refires(self):
        other = SAMPLE.replace("- [x] **T01", "- [ ] **T01")
        self.assertNotEqual(self.sig(SAMPLE), self.sig(other))

    def test_overdue_pile_refires_on_a_new_day(self):
        """Same open set, next day, still overdue -> nudge again."""
        self.assertNotEqual(self.sig(SAMPLE, "2026-08-06"),
                            self.sig(SAMPLE, "2026-08-07"))

    def test_quiet_when_nothing_is_dated(self):
        """No overdue and nothing due today -> the date must not churn the signature."""
        text = "## X\n\n- [ ] **T10 — Undated** `@agent`\n"
        self.assertEqual(self.sig(text, "2026-08-06"), self.sig(text, "2026-09-30"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.todo = os.path.join(self.tmp.name, "TODO.md")
        with open(self.todo, "w", encoding="utf-8") as fh:
            fh.write(SAMPLE)
        self.state = os.path.join(self.tmp.name, "state.json")
        self.pub = FakePublisher()

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, **kw):
        out = io.StringIO()
        code = tr.run(todo_path=self.todo, state_path=self.state, publish=self.pub,
                      today="2026-08-06", out=out, **kw)
        return code, out.getvalue()

    def test_first_run_notifies_then_second_is_silent(self):
        code, _ = self.go()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pub.calls), 1)
        code, text = self.go()
        self.assertEqual(len(self.pub.calls), 1)
        self.assertIn("Unchanged", text)

    def test_force_notifies_anyway(self):
        self.go()
        self.go(force=True)
        self.assertEqual(len(self.pub.calls), 2)

    def test_dry_run_never_publishes(self):
        code, text = self.go(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.pub.calls, [])
        self.assertIn("would notify", text)

    def test_dry_run_does_not_poison_the_dedupe_state(self):
        self.go(dry_run=True)
        self.go()
        self.assertEqual(len(self.pub.calls), 1)

    def test_overdue_raises_priority(self):
        self.go()
        self.assertEqual(self.pub.calls[0]["priority"], "high")

    def test_missing_file_is_a_clear_failure(self):
        out = io.StringIO()
        code = tr.run(todo_path=os.path.join(self.tmp.name, "nope.md"),
                      state_path=self.state, publish=self.pub, out=out)
        self.assertEqual(code, 2)

    def test_format_break_fails_loudly_rather_than_going_quiet(self):
        """An empty parse is the dangerous case: it looks like 'all done'."""
        broken = os.path.join(self.tmp.name, "broken.md")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("# Nothing parseable here\n\n* [ ] wrong bullet\n")
        out = io.StringIO()
        code = tr.run(todo_path=broken, state_path=self.state, publish=self.pub, out=out)
        self.assertEqual(code, 3)
        self.assertEqual(self.pub.calls, [])

    def test_all_done_means_no_nudge(self):
        done = os.path.join(self.tmp.name, "done.md")
        with open(done, "w", encoding="utf-8") as fh:
            fh.write("## X\n\n- [x] **T01 — Finished** `@agent`\n")
        out = io.StringIO()
        code = tr.run(todo_path=done, state_path=self.state, publish=self.pub, out=out)
        self.assertEqual(code, 0)
        self.assertEqual(self.pub.calls, [])
        self.assertIn("Nothing open", out.getvalue())


class RealDocumentTests(unittest.TestCase):
    """The shipped docs/TODO.md must stay parseable — a silent format break here
    would turn the reminder off without anyone noticing."""

    def setUp(self):
        with open(REAL_TODO, "r", encoding="utf-8") as fh:
            self.tasks = tr.parse_tasks(fh.read())

    def test_parses_to_real_tasks(self):
        self.assertGreater(len(self.tasks), 10)

    def test_ids_are_unique(self):
        self.assertEqual(tr.duplicate_ids(self.tasks), [])

    def test_every_task_has_an_owner(self):
        missing = [t.id for t in self.tasks if not t.owner]
        self.assertEqual(missing, [])

    def test_owners_are_known(self):
        known = {"damien", "john", "roger", "agent"}
        unknown = sorted({t.owner for t in self.tasks} - known)
        self.assertEqual(unknown, [])

    def test_due_dates_parse_as_dates(self):
        import datetime
        for t in self.tasks:
            if t.due:
                datetime.date.fromisoformat(t.due)

    def test_the_naming_rule_survives(self):
        """T12 carries the arbitration naming correction; losing it loses the rule."""
        by_id = {t.id: t for t in self.tasks}
        self.assertIn("T12", by_id)
        self.assertIn("naming", by_id["T12"].title.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
