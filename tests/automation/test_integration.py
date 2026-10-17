import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "integration", ROOT / "tests/automation/integration.py"
)
integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integration)


class IntegrationConfigurationTests(unittest.TestCase):
    def test_generated_config_loads_before_or_after_core_root_is_defined(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("user", "frontend", "elsewhere"):
                (root / name).mkdir()
            (root / "frontend/config-sample.php").write_text("<?php\n", encoding="utf-8")
            (root / "user/reserved_words.php").write_text(
                "<?php\n$yourls_reserved_URL = ['fixture-reserved'];\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {
                "CURLS_CI_PASSWORD": "a" * 64, "DB_PORT": "3306",
            }):
                integration.config(root)
            script = """
if ($argv[2] === 'cli') {
    define('YOURLS_ABSPATH', $argv[1]);
}
require $argv[1] . '/user/config.php';
echo json_encode([$yourls_reserved_URL, defined('YOURLS_ABSPATH')]);
"""
            for mode in ("web", "cli"):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        ["php", "-n", "-d", "display_errors=stderr", "-r",
                         script, str(root), mode],
                        cwd=root / "elsewhere", text=True, capture_output=True,
                    )
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertEqual("", result.stderr)
                    self.assertEqual(
                        [["fixture-reserved"], mode == "cli"], json.loads(result.stdout)
                    )


class IntegrationWorkspaceTests(unittest.TestCase):
    def test_restores_candidate_owner_before_cleanup_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                root = Path(temporary.name)

                def restore(*args):
                    self.assertEqual(
                        ("sudo", "chown", "-R", "123:456", "--",
                         str(root / "candidate")),
                        args,
                    )
                    self.assertTrue((root / "candidate/index.php").is_file())

                with mock.patch.object(integration.tempfile, "TemporaryDirectory",
                                       return_value=temporary), \
                     mock.patch.object(integration.os, "getuid", return_value=123,
                                       create=True), \
                     mock.patch.object(integration.os, "getgid", return_value=456,
                                       create=True), \
                     mock.patch.object(integration, "run", side_effect=restore) as run:
                    def exercise():
                        with integration.workspace() as work:
                            self.assertEqual(
                                (ROOT / "ops/deploy/application.php").read_bytes(),
                                (work / "application.php").read_bytes(),
                            )
                            (work / "candidate").mkdir()
                            (work / "candidate/index.php").write_text("fixture")
                            if fail:
                                raise ValueError("synthetic HTTP failure")

                    if fail:
                        with self.assertRaisesRegex(ValueError, "synthetic HTTP failure"):
                            exercise()
                    else:
                        exercise()
                    run.assert_called_once()
                self.assertFalse(root.exists())

    def test_failure_before_extraction_does_not_chown_a_missing_candidate(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(integration.tempfile, "TemporaryDirectory",
                               return_value=temporary), \
             mock.patch.object(integration.os, "getuid", return_value=123, create=True), \
             mock.patch.object(integration.os, "getgid", return_value=456, create=True), \
             mock.patch.object(integration, "run") as run:
            with self.assertRaisesRegex(ValueError, "extraction failed"):
                with integration.workspace():
                    raise ValueError("extraction failed")
            run.assert_not_called()
        self.assertFalse(Path(temporary.name).exists())

    def test_only_probe_runs_as_web_account_with_accessible_helper(self):
        root = Path("fixture") / "candidate"
        for command in ("inspect", "upgrade", "probe"):
            with self.subTest(command=command), \
                 mock.patch.object(integration, "run",
                                   return_value=json.dumps({"links": 1})) as run:
                self.assertEqual({"links": 1}, integration.application(root, command))
                args = ["php", str(root.parent / "application.php"), str(root), command]
                if command == "probe":
                    args = ["sudo", "-u", "www-data", "--", *args]
                run.assert_called_once_with(*args)


if __name__ == "__main__":
    unittest.main()
