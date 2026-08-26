import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

from scripts.prepare_runtime_env import prepare_env


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _bash_command():
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if pathlib.Path(candidate).exists():
                return candidate
        return None
    return shutil.which("bash")


def _run_deploy_with_mocked_docker(docker_logic: str):
    bash = _bash_command()
    if not bash:
        raise unittest.SkipTest("bash is unavailable")
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (root / "caddy").mkdir()
        (root / "reports").mkdir()
        shutil.copy2(ROOT / "deploy_public.sh", root / "deploy_public.sh")
        shutil.copy2(ROOT / "caddy" / "Caddyfile", root / "caddy" / "Caddyfile")
        (root / ".env.example").write_text("SECRET_KEY=placeholder\n", encoding="utf-8")
        docker_log = root / "docker.log"
        (bin_dir / "docker").write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{docker_log.as_posix()}'\n"
            + docker_logic
            + "\nexit 0\n",
            encoding="utf-8",
        )
        for name in ("python3", "python"):
            (bin_dir / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "curl").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        for executable in bin_dir.iterdir():
            executable.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(bin_dir), os.environ.get("PATH", "")))
        env.update(
            {
                "DUCKDNS_TOKEN": "redacted",
                "DUCKDNS_SUBDOMAIN": "example",
                "SERVER_IP": "redacted",
                "PYTHON_BIN": "python3",
                "PYTHON_FALLBACK": "python",
            }
        )
        before_reports = set((root / "reports").iterdir())
        result = subprocess.run(
            [bash, str(root / "deploy_public.sh")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        after_reports = set((root / "reports").iterdir())
        return result, docker_log.read_text(encoding="utf-8"), before_reports == after_reports


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
        self.assertIn("umask 077", script)
        self.assertIn("health_file=\"$(mktemp)\"", script)
        self.assertIn("public_test is still running", script)
        self.assertNotIn("stop public_test >/dev/null 2>&1 || true", script)

    def test_config_smoke_is_self_contained(self):
        script = (ROOT / "scripts" / "deployment_config_smoke_test.sh").read_text(encoding="utf-8")
        self.assertIn(".env.example", script)
        self.assertIn("install -m 600", script)
        self.assertIn("trap cleanup EXIT", script)

    def test_deploy_fails_closed_when_initial_public_status_query_fails(self):
        result, docker_log, reports_unchanged = _run_deploy_with_mocked_docker(
            "case \"$*\" in\n"
            "  *\"ps --all --services\"*) exit 17 ;;\n"
            "esac"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to inspect public_test containers", result.stderr)
        self.assertNotIn("up -d --build gateway caddy", docker_log)
        self.assertNotIn("PUBLIC_DEPLOYMENT_OK", result.stdout)
        self.assertTrue(reports_unchanged)

    def test_deploy_fails_closed_when_shutdown_verification_query_fails(self):
        result, docker_log, reports_unchanged = _run_deploy_with_mocked_docker(
            "case \"$*\" in\n"
            "  *\"ps --all --services\"*) printf 'public_test\\n'; exit 0 ;;\n"
            "  *\"ps --status running --services\"*) exit 18 ;;\n"
            "esac"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to verify public_test shutdown", result.stderr)
        self.assertIn("stop public_test", docker_log)
        self.assertIn("rm -f public_test", docker_log)
        self.assertNotIn("up -d --build gateway caddy", docker_log)
        self.assertNotIn("PUBLIC_DEPLOYMENT_OK", result.stdout)
        self.assertTrue(reports_unchanged)

    def test_public_template_is_redacted(self):
        template = (ROOT / "PUBLIC_DEPLOY_REPORT.md").read_text(encoding="utf-8")
        self.assertNotRegex(template, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        self.assertNotRegex(template, r"(?i)(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]+")
        self.assertNotRegex(template, r"(?i)(?:[a-z0-9-]+\.)+(?:com|net|org|io)\b")
        self.assertNotRegex(template, r"DUCKDNS_TOKEN=.+")
        self.assertNotRegex(template, r"(?:sk-|Bearer\s+)[A-Za-z0-9._-]{12,}")
        self.assertNotRegex(template, r"(?i)(?:oauth|cookie|api[_-]?key|access[_-]?token)")
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

    def test_start_script_really_uses_python_fallback(self):
        bash = _bash_command()
        if not bash:
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = pathlib.Path(directory) / "bin"
            bin_dir.mkdir()
            log = pathlib.Path(directory) / "docker.log"
            real_python = "python.exe" if os.name == "nt" else pathlib.Path(os.sys.executable).as_posix()
            (bin_dir / "python3").write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            (bin_dir / "python").write_text(f"#!/bin/sh\nexec '{real_python}' \"$@\"\n", encoding="utf-8")
            (bin_dir / "docker").write_text(
                f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log.as_posix()}'\nexit 0\n", encoding="utf-8"
            )
            (bin_dir / "curl").write_text("#!/bin/sh\nprintf '{\"status\":\"ok\"}\\n'\n", encoding="utf-8")
            for executable in bin_dir.iterdir():
                executable.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(bin_dir), os.environ.get("PATH", "")))
            env["PYTHON_BIN"] = "python3"
            env["PYTHON_FALLBACK"] = "python"
            result = subprocess.run([bash, str(ROOT / "start.sh")], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("up -d --build gateway", log.read_text(encoding="utf-8"))

    def test_start_script_stops_before_docker_when_both_python_fail(self):
        bash = _bash_command()
        if not bash:
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = pathlib.Path(directory) / "bin"
            bin_dir.mkdir()
            log = pathlib.Path(directory) / "docker.log"
            for name in ("python3", "python"):
                (bin_dir / name).write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            (bin_dir / "docker").write_text(
                f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log.as_posix()}'\nexit 0\n", encoding="utf-8"
            )
            for executable in bin_dir.iterdir():
                executable.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(bin_dir), os.environ.get("PATH", "")))
            env["PYTHON_BIN"] = "python3"
            env["PYTHON_FALLBACK"] = "python"
            result = subprocess.run([bash, str(ROOT / "start.sh")], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No usable Python interpreter found", result.stderr)
            self.assertNotIn("compose up", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
