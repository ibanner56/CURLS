import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_unit_results", ROOT / ".github/scripts/check_unit_results.py"
)
results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(results)


class UnitResultPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / "upstream-unit.xml"

    def write_report(self, cases):
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        for class_name, name, outcome in cases:
            case = ET.SubElement(suite, "testcase", {"class": class_name, "name": name})
            if outcome:
                ET.SubElement(case, outcome)
        ET.ElementTree(root).write(self.report)

    def test_allows_only_the_two_known_theme_skips(self):
        self.write_report([
            ("ThemesTest", "test_get_themes", "skipped"),
            ("ThemesTest", "test_theme_activate", "skipped"),
            ("LoginAPITest", "test_login", None),
        ])
        self.assertEqual(2, results.check_results(self.report))

    def test_theme_tests_can_run_normally_when_supported(self):
        self.write_report([
            ("ThemesTest", "test_get_themes", None),
            ("ThemesTest", "test_theme_activate", None),
        ])
        self.assertEqual(0, results.check_results(self.report))

    def test_rejects_every_other_skipped_test_and_duplicate_allowed_skips(self):
        for cases in (
            [("LoginAPITest", "test_login", "skipped")],
            [("ThemesTest", "test_other_theme", "skipped")],
            [("OtherTest", "test_get_themes", "skipped")],
            [("ThemesTest", "test_get_themes with data set 1", "skipped")],
            [("ThemesTest", "test_get_themes", "skipped")] * 2,
        ):
            with self.subTest(cases=cases):
                self.write_report(cases)
                with self.assertRaisesRegex(ValueError, "Unexpected skipped"):
                    results.check_results(self.report)

    def test_known_theme_names_do_not_exempt_failures_or_errors(self):
        for outcome in ("failure", "error"):
            with self.subTest(outcome=outcome):
                self.write_report([("ThemesTest", "test_get_themes", outcome)])
                with self.assertRaisesRegex(ValueError, "failures or errors"):
                    results.check_results(self.report)

    def test_missing_malformed_empty_and_wrong_format_reports_fail_closed(self):
        with self.assertRaises(FileNotFoundError):
            results.check_results(self.report)
        for content, exception in (
            ("<broken", ET.ParseError),
            ("<testsuites/>", ValueError),
            ('<other><testcase class="ThemesTest" name="test_get_themes"/></other>', ValueError),
            ("<testsuites><testsuite tests='1114'/></testsuites>", ValueError),
        ):
            with self.subTest(content=content):
                self.report.write_text(content)
                with self.assertRaises(exception):
                    results.check_results(self.report)


if __name__ == "__main__":
    unittest.main()
