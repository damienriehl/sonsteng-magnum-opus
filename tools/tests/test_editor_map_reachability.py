#!/usr/bin/env python3
r"""The editor map is TWO allowlists at once, and the second one used to be wrong.

`build/editor-map.generated.json` is the editable-BLOCK allowlist (what a
suggestion may target) *and* the PAGE allowlist the /edit proxy resolves request
paths against (`resolvePagePath` in app/worker/src/editor-map.js). The generator
originally registered a page only when it carried at least one editable block —
which silently made block-free pages unreachable inside /edit, even though the injector rewrites
every same-origin link into /edit space. A reviewer who clicked "Matter Library"
from a matter packet got a uniform 404.

These tests pin the contract that fixes it:
  * every built page is registered, block-carrying or not;
  * the chat surfaces are deliberately NOT registered (the injector strips a
    wrapped page's own scripts, so the simulator would render inert);
  * registering empty pages does not invent editable blocks;
  * the three authored-copy landing pages expose their expected blocks.

Run:  python3 -m pytest tools/tests/test_editor_map_reachability.py -q
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import build_site as bs  # noqa: E402
from fresh_site_build import build_fresh_site  # noqa: E402

# Pages with no editable prose that a reviewer still has to be able to reach,
# because the site's own navigation links to them.
NAVIGATIONAL_PAGES = (
    "index.html",
    "matters/index.html",
    "skills/index.html",
    "firm/index.html",
)


def _built_pages(site_out):
    """Every generated HTML page, site-relative, in the generator's own terms."""
    out = []
    for root, _dirs, files in os.walk(site_out):
        for name in files:
            if not name.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(root, name), site_out)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


class EditorMapReachabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.site_out, cls.bundle = build_fresh_site("edreach-")
        cls.pages = cls.bundle["pages"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_built_page_is_registered_except_the_exclusions(self):
        expected = [p for p in _built_pages(self.site_out)
                    if not p.startswith(bs.EDITOR_MAP_EXCLUDE_PREFIXES)]
        self.assertEqual(sorted(self.pages), expected,
                         "the page allowlist must cover every hostable built page")

    def test_navigational_pages_are_reachable(self):
        for page in NAVIGATIONAL_PAGES:
            with self.subTest(page=page):
                self.assertIn(page, self.pages,
                              f"{page} is linked from the site nav; /edit must resolve it")

    def test_authored_landing_pages_are_editable(self):
        self.assertEqual(len(self.pages["index.html"]), 21)
        self.assertEqual(len(self.pages["matters/index.html"]), 3)
        self.assertEqual(len(self.pages["firm/index.html"]), 33)

    def test_chat_surfaces_are_excluded(self):
        self.assertIn("chat/", bs.EDITOR_MAP_EXCLUDE_PREFIXES)
        for page in self.pages:
            self.assertFalse(page.startswith("chat/"),
                             "the chat surfaces are their own scripts — the injector "
                             "strips page scripts, so /edit must not host them")

    def test_assets_are_excluded(self):
        self.assertIn("assets/", bs.EDITOR_MAP_EXCLUDE_PREFIXES)
        for page in self.pages:
            self.assertFalse(page.startswith("assets/"))

    def test_registering_empty_pages_invents_no_blocks(self):
        """A zero-block page is readable and navigable, with nothing to edit."""
        empty = [k for k, v in self.pages.items() if not v]
        self.assertTrue(empty, "the navigational pages should register with no blocks")
        for page in empty:
            self.assertEqual(self.bundle["counts"][page], 0)
        counted = sum(len(v) for v in self.pages.values())
        self.assertEqual(counted, self.bundle["counts"]["_total"],
                         "_total must equal the real block count, not the page count")

    def test_matter_pages_still_carry_their_blocks(self):
        """Guard against the fix hollowing out the block allowlist."""
        matters = {k: v for k, v in self.pages.items()
                   if k.startswith("matters/m") and k.endswith("/index.html")
                   and k.count("/") == 2}   # the packet page, not facts/ (U5)
        self.assertEqual(len(matters), 20)
        for page, blocks in matters.items():
            with self.subTest(page=page):
                self.assertGreater(len(blocks), 100)


if __name__ == "__main__":
    unittest.main()
