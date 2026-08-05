#!/usr/bin/env python3
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "tools" / "install-prod-promotion-daemon.sh"
DIRECT_DEPLOY = ROOT / "deploy" / "deploy-prod.sh"


class ProdPromotionInstallTest(unittest.TestCase):
    def run_installer(self, *args, env=None):
        return subprocess.run(
            ["bash", str(INSTALLER), *args], cwd=ROOT, env=env,
            text=True, capture_output=True, check=False,
        )

    def test_dry_run_is_isolated_default_off_and_secret_free(self):
        result = self.run_installer("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in ("sonsteng-prod-promotion.service", "environment=production",
                       "branch=main", "auto_enable=false", "NoNewPrivileges=true",
                       "ProtectSystem=strict", "credentials=configured(path redacted)"):
            self.assertIn(marker, result.stdout)
        self.assertNotIn("TOKEN=", result.stdout.upper())
        self.assertNotIn("systemctl", result.stdout)

    def test_install_writes_private_units_but_does_not_activate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            env = dict(os.environ,
                       PROD_PROMOTION_CONFIG_ROOT=str(root / "config"),
                       PROD_PROMOTION_DATA_ROOT=str(root / "data"),
                       PROD_PROMOTION_UNIT_DIR=str(root / "units"),
                       PROD_PROMOTION_CHECKOUT=str(root / "checkout"))
            result = self.run_installer("--install", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            service = root / "units" / "sonsteng-prod-promotion.service"
            timer = root / "units" / "sonsteng-prod-promotion.timer"
            self.assertTrue(service.exists() and timer.exists())
            self.assertEqual(service.stat().st_mode & 0o777, 0o600)
            self.assertIn("PROD_PROMOTION_ENABLED=0", (root / "config" / "env").read_text())
            self.assertNotIn("systemctl", result.stdout)

    def test_non_main_or_non_https_refuses_install(self):
        for override in ({"PROD_PROMOTION_BRANCH": "dev"},
                         {"PROD_PROMOTION_API_BASE": "http://example.invalid"}):
            env = dict(os.environ, **override)
            self.assertNotEqual(self.run_installer("--dry-run", env=env).returncode, 0)

    def test_direct_prod_deploy_is_a_non_mutating_lockout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            marker = root / "npx-invoked"
            fake = root / "npx"
            fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
            fake.chmod(0o700)
            env = dict(os.environ, PATH=f"{root}:{os.environ.get('PATH', '')}")
            refused = subprocess.run(["bash", str(DIRECT_DEPLOY)], cwd=ROOT, env=env,
                                     text=True, capture_output=True, check=False)
            self.assertEqual(refused.returncode, 64)
            self.assertIn("direct PROD deploy is disabled", refused.stderr)
            dry = subprocess.run(["bash", str(DIRECT_DEPLOY), "--dry-run"], cwd=ROOT, env=env,
                                 text=True, capture_output=True, check=False)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("coordinator-only", dry.stdout)
            self.assertFalse(marker.exists(), "direct deploy invoked npx/wrangler")


if __name__ == "__main__":
    unittest.main()
