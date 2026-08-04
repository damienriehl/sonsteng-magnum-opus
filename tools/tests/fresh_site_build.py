"""Build the production site into a temporary directory for test harnesses."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

import build_site as bs


def build_fresh_site(prefix="site-test-"):
    """Call the production entry point with its outputs redirected."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    saved = {name: getattr(bs, name) for name in
             ("OUT", "BUILD_DIR", "EDITOR_MAP_PATH", "SPINE_BUILD_ID")}
    bs.OUT = os.path.join(tmp, "site", "platform")
    bs.BUILD_DIR = os.path.join(tmp, "build")
    bs.EDITOR_MAP_PATH = os.path.join(bs.BUILD_DIR, "editor-map.generated.json")
    try:
        source_assets = os.path.join(saved["OUT"], "assets")
        if os.path.isdir(source_assets):
            shutil.copytree(source_assets, os.path.join(bs.OUT, "assets"))
        result = bs.main([])
        if result != 0:
            raise RuntimeError("build_site.main([]) returned %s" % result)
        with open(bs.EDITOR_MAP_PATH, encoding="utf-8") as fh:
            bundle = json.load(fh)
        return tmp, bs.OUT, bundle
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        bs.EDMAP.reset()
        for name, value in saved.items():
            setattr(bs, name, value)
