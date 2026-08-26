import os
import pathlib
import stat
import tempfile
import unittest

import yaml

from scripts.prepare_runtime_env import prepare_env


ROOT = pathlib.Path(__file__).resolve().parents[1]


class DeploySafetyTests(unittest.TestCase):
    def test_port_boundary_is_exact(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(compose["services"]["gateway"]["ports"], ["127.0.0.1:8080:8080"])
        self.assertEqual(compose["services"]["caddy"]["ports"], ["80:80", "443:443"])

    def test_deploy_script_rebuilds_and_uses_private_report_path(self):
        script = (ROOT / "deploy_public.sh").read_text(encoding="utf-8")
        self.assertIn("up -d --build gateway caddy", script)
        self.assertIn("reports/public-deploy-", script)
        self.assertNotIn("cat > PUBLIC_DEPLOY_REPORT.md", script)

    def test_public_template_is_redacted(self):
        template = (ROOT / "PUBLIC_DEPLOY_REPORT.md").read_text(encoding="utf-8")
        self.assertNotRegex(template, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        self.assertNotRegex(template, r"DUCKDNS_TOKEN=.+")
        self.assertNotRegex(template, r"(?:sk-|Bearer\s+)[A-Za-z0-9._-]{12,}")
        self.assertIn("reports/", template)

    def test_env_secret_cases_are_idempotent_and_restrictive(self):
        cases = [
            "# keep me\nBIND_HOST=127.0.0.1\n",
            "SECRET_KEY=\nBIND_HOST=127.0.0.1\n",
            "SECRET_KEY=replace-with-32-byte-random-secret\n",
            "SECRET_KEY=already-random-and-valid\n",
        ]
        for initial in cases:
            with self.subTest(initial=initial):
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / ".env"
                    path.write_text(initial, encoding="utf-8")
                    prepare_env(path, {"PUBLIC_TEST_MODE": "false"})
                    first = path.read_text(encoding="utf-8")
                    prepare_env(path, {"PUBLIC_TEST_MODE": "false"})
                    second = path.read_text(encoding="utf-8")
                    self.assertEqual(first, second)
                    self.assertIn("BIND_HOST=0.0.0.0", first)
                    self.assertIn("SECRET_KEY=", first)
                    if os.name != "nt":
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_secret_is_added_without_printing_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / ".env"
            path.write_text("OTHER=value\n", encoding="utf-8")
            prepare_env(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("OTHER=value", text)
            self.assertEqual(text.count("SECRET_KEY="), 1)
            self.assertNotIn("replace-with-32-byte-random-secret", text)

    def test_duplicate_keys_are_updated_consistently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / ".env"
            path.write_text("DOMAIN=old.example\nDOMAIN=older.example\nSECRET_KEY=\n", encoding="utf-8")
            prepare_env(path, {"DOMAIN": "new.example"})
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("DOMAIN="), 2)
            self.assertEqual(text.count("DOMAIN=new.example"), 2)

    def test_start_script_checks_both_python_candidates(self):
        script = (ROOT / "start.sh").read_text(encoding="utf-8")
        self.assertIn('"$python_bin" -c \'import sys\'', script)
        self.assertIn('"$python_fallback" -c \'import sys\'', script)
        self.assertIn("No usable Python interpreter found", script)


if __name__ == "__main__":
    unittest.main()
