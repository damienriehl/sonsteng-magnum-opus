#!/usr/bin/env python3
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "tools" / "install-prod-promotion-daemon.sh"


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


if __name__ == "__main__":
    unittest.main()
