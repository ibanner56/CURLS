import contextlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "unit_config", ROOT / "tests/automation/unit_config.py"
)
unit_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(unit_config)


class UnitConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "user").mkdir()
        self.fixture = self.root / "tests/data/config/yourls-tests-config-ci.php"
        self.fixture.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "tests/data/config" / self.fixture.name, self.fixture)
        shutil.copyfile(ROOT / "phpunit.xml.dist", self.root / "phpunit.xml.dist")
        for context in (
            contextlib.chdir(self.root),
            mock.patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                "CURLS_CI_PASSWORD": "a" * 64,
            }),
        ):
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)

    def test_renders_disposable_database_and_strict_modern_runner_configuration(self):
        unit_config.main()
        source = (self.root / "user/config.php").read_text()
        self.assertIn("define( 'YOURLS_DB_USER', 'curls_ci' );", source)
        self.assertIn(f"define( 'YOURLS_DB_PASS', '{'a' * 64}' );", source)
        original = ET.parse(self.root / "phpunit.xml.dist")
        expected = original.getroot()
        del expected.attrib["cacheResult"]
        expected.set("recordTestRunHistory", "true")
        expected.set("stopOnFailure", "false")
        generated = ET.parse(self.root / "phpunit.xml").getroot()
        self.assertEqual(expected.attrib, generated.attrib)
        expected.attrib.clear()
        generated.attrib.clear()
        self.assertEqual(ET.tostring(expected), ET.tostring(generated))

    def test_rejects_unsafe_execution_context_and_invalid_synthetic_credentials(self):
        for environment in (
            {"GITHUB_ACTIONS": "false"},
            {"RUNNER_ENVIRONMENT": "self-hosted"},
            {"CURLS_CI_PASSWORD": "'invalid"},
        ):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment):
                with self.assertRaises(ValueError):
                    unit_config.main()
                self.assertFalse((self.root / "user/config.php").exists())
                self.assertFalse((self.root / "phpunit.xml").exists())

    def test_rejects_upstream_fixture_drift_before_writing_configuration(self):
        for path, old in (
            (self.fixture, "define( 'YOURLS_DB_USER', 'root' );"),
            (self.root / "phpunit.xml.dist", 'cacheResult="true"'),
            (self.root / "phpunit.xml.dist", 'stopOnFailure="true"'),
        ):
            with self.subTest(setting=old):
                original = path.read_text()
                path.write_text(original.replace(old, "changed upstream"))
                with self.assertRaisesRegex(ValueError, "review the fixture"):
                    unit_config.main()
                self.assertFalse((self.root / "user/config.php").exists())
                self.assertFalse((self.root / "phpunit.xml").exists())
                path.write_text(original)

    def test_never_overwrites_existing_configuration(self):
        for relative in ("user/config.php", "phpunit.xml"):
            (self.root / relative).write_text("existing configuration\n")
        with self.assertRaises(FileExistsError):
            unit_config.main()
        for relative in ("user/config.php", "phpunit.xml"):
            self.assertEqual("existing configuration\n", (self.root / relative).read_text())


if __name__ == "__main__":
    unittest.main()
