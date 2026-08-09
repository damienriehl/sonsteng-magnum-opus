"""Characterization contracts for the pre-Publisher publication boundary.

These tests deliberately describe current U1 behavior.  Accepted editor rows are
eligible for the existing DEV apply daemon, but that daemon and its user-facing
promise must not imply that public production was published.
"""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import direct_apply_daemon as daemon  # noqa: E402


def test_accepted_rows_are_currently_claimable_by_the_dev_daemon():
    rows = [
        {"id": "accepted-canary", "status": "accepted"},
        {"id": "pending-canary", "status": "pending"},
    ]

    assert daemon.accepted_ids(rows) == ["accepted-canary"]


def test_dev_daemon_and_prod_deployer_are_separate_writers():
    daemon_source = (ROOT / "tools/direct_apply_daemon.py").read_text()
    dev_deploy = (ROOT / "deploy/deploy-dev.sh").read_text()
    prod_deploy = (ROOT / "deploy/deploy-prod.sh").read_text()

    assert "DEV ONLY, never PROD" in daemon_source
    assert "sonsteng-dev.damienriehl.com" in dev_deploy
    assert "wrangler@latest pages deploy" in prod_deploy
    assert "--branch main" in prod_deploy
    assert "deploy-prod.sh" not in daemon_source


def test_editor_liveness_copy_names_the_editing_site_not_public_prod():
    editor = (ROOT / "app/editor/editor.js").read_text()
    guide = (ROOT / "docs/editor-guide-for-john.md").read_text()

    assert "Your edits appear on the editing site automatically (~2 min)." in editor
    assert "Your edits appear on the editing site automatically (~2 min)." in guide
    assert "Damien publishes the public production release" in guide
