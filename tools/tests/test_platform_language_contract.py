"""Contracts for the recording-reconciled platform language decisions."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)

from fresh_site_build import build_fresh_site  # noqa: E402


AUTHORED = [
    os.path.join(ROOT, "data", "copy", "home.json"),
    *[os.path.join(ROOT, "data", "curriculum", name) for name in ("m1.md", "m2.md", "m3.md")],
]
AUTHORED += [
    os.path.join(root, name)
    for root, _dirs, names in os.walk(os.path.join(ROOT, "data", "matters"))
    for name in names
    if name == "exercise.json"
]
AUTHORED += [str(path) for path in Path(ROOT, "data", "curriculum", "templates").glob("*.md")]


def learner_text(paths=AUTHORED):
    return "\n".join(Path(path).read_text(encoding="utf-8") for path in paths)


LEGITIMATE_DOMAIN_USES = (
    "creamery grader",
    "first-grader",
    "seven-percent grade",
    "grades the offense",
)


def forbidden_educational_grading(text):
    scrubbed = text
    for phrase in LEGITIMATE_DOMAIN_USES:
        scrubbed = re.sub(re.escape(phrase), "", scrubbed, flags=re.I)
    return re.search(r"\bgrad(?:e|ed|er|ers|es|ing)\b", scrubbed, re.I)


def forbidden_advocate_stem(text):
    return re.search(r"\badvocat\w*", text, re.I)


class TestPlatformLanguageContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.site, _editor_map = build_fresh_site("platform-language-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_authored_learner_language_uses_locked_vocabulary(self):
        text = learner_text()
        self.assertIsNone(forbidden_educational_grading(text))
        self.assertIn("assessment and feedback", text.lower())
        self.assertIn("Planning Guide and Checklist", text)

    def test_generated_learner_surfaces_use_locked_vocabulary(self):
        learner_pages = [
            Path(self.site, "index.html"),
            Path(self.site, "templates", "index.html"),
            *Path(self.site, "modules").glob("*.html"),
        ]
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in learner_pages)
        self.assertIsNone(forbidden_educational_grading(rendered))
        self.assertIn("Planning Guide and Checklist", rendered)

    def test_hand_authored_pitch_page_uses_locked_vocabulary(self):
        # The pitch page is hand-authored and lives outside site/platform/, so every
        # generator-scoped gate misses it. It is also the most public surface we own.
        # Without this assertion "grading" survives here indefinitely: it did, five
        # times, from the vocabulary lock on 2026-08-06 until the 2026-08-17 audit.
        pitch = Path(ROOT, "site", "index.html").read_text(encoding="utf-8")
        self.assertIsNone(forbidden_educational_grading(pitch))
        self.assertIsNone(forbidden_advocate_stem(pitch))
        self.assertIn(
            "Training the next generation of lawyers as trusted advisors.",
            pitch,
        )

        readme = Path(ROOT, "README.md").read_text(encoding="utf-8")
        self.assertIsNone(forbidden_advocate_stem(readme))

    def test_mutation_canary_detects_forbidden_pitch_language(self):
        pitch = Path(ROOT, "site", "index.html").read_text(encoding="utf-8")
        self.assertIsNotNone(forbidden_educational_grading(pitch + "\nWork is graded."))
        self.assertIsNotNone(forbidden_advocate_stem(pitch + "\nThey become advocates."))

    def test_ai_default_and_scripted_sample_are_both_accurately_labelled(self):
        home = json.loads(Path(ROOT, "data", "copy", "home.json").read_text(encoding="utf-8"))
        copy = json.dumps(home)
        self.assertRegex(copy, r"AI is the default speaker")
        self.assertRegex(copy, r"Scripted sample, not a live AI client")
        self.assertNotRegex(copy, r"(?i)(human|alumni).{0,40}speaker")

    def test_mutation_canary_detects_forbidden_learner_language(self):
        mutated = learner_text() + "\nYour submission is graded."
        self.assertIsNotNone(forbidden_educational_grading(mutated))

    def test_legitimate_domain_grader_is_not_in_the_learner_input_scope(self):
        for phrase in LEGITIMATE_DOMAIN_USES:
            self.assertIsNone(forbidden_educational_grading(phrase))

    def test_no_alumni_assessment_or_notification_route_in_runtime(self):
        source_root = Path(ROOT, "app", "worker", "src")
        runtime = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.js"))
        self.assertNotRegex(runtime, r"(?i)/[^\s\"']*alumni|notify\w*\([^)]*alumni")
        validation = Path(source_root, "validate.js").read_text(encoding="utf-8")
        self.assertIn("Alumni routing fields are not supported.", validation)


if __name__ == "__main__":
    unittest.main()
