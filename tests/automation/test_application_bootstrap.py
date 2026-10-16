import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

# Real sanitizers and hooks, with no application configuration or database connection.
LOADER = """<?php
require getenv('CURLS_TEST_SOURCE') . '/includes/vendor/autoload.php';
foreach (['plugins', 'kses', 'formatting', ''] as $file) {
    require getenv('CURLS_TEST_SOURCE') . '/includes/functions' .
        ($file === '' ? '' : '-' . $file) . '.php';
}
define('YOURLS_DB_NAME', 'curls_upgrade');
define('YOURLS_DB_USER', 'curls_ci');
define('YOURLS_DB_TABLE_URL', 'yourls_url');
define('YOURLS_DB_TABLE_LOG', 'yourls_log');
define('YOURLS_VERSION', '1.10.6');
define('YOURLS_DB_VERSION', 507);
yourls_add_action('plugins_loaded', function() {
    throw new RuntimeException('Fast bootstrap must not run plugin hooks.');
});
yourls_add_filter('kses_allowed_protocols', function($protocols) {
    $protocols[] = 'fixture:';
    return $protocols;
});
function fixture_check_sanitizer() {
    foreach ([
        'https://example.test/preserved' => 'https://example.test/preserved',
        'javascript:alert(1)' => '',
        'fixture:preserved' => 'fixture:preserved',
    ] as $input => $expected) {
        if (yourls_esc_url($input) !== $expected) {
            throw new RuntimeException('Sanitizer behavior changed.');
        }
    }
}
function yourls_create_sql_tables() {
    fixture_check_sanitizer();
    return ['error' => false];
}
function yourls_get_all_options() { fixture_check_sanitizer(); }
function yourls_add_new_link($url, $keyword, $title) {
    fixture_check_sanitizer();
    return ['status' => 'success'];
}
function yourls_update_option($name, $value) {}
function yourls_get_option($name, $default = null) {
    return ['version' => YOURLS_VERSION, 'db_version' => YOURLS_DB_VERSION,
        'active_plugins' => ['fixture/plugin.php']][$name] ?? $default;
}
function yourls_get_db($reason) {
    return new class {
        public function perform($sql) {}
        public function fetchValue($sql) { return 1; }
    };
}
function yourls_upgrade($step, $old, $new, $old_db, $new_db) {
    fixture_check_sanitizer();
}
"""


class ApplicationBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "includes").mkdir()
        (self.root / "includes/load-yourls.php").write_text(LOADER, encoding="utf-8")
        (self.root / "user").mkdir()
        (self.root / "user/config.php").write_text("<?php\n", encoding="utf-8")

    def run_helper(self, helper, *args):
        result = subprocess.run(
            ["php", str(ROOT / helper), str(self.root), *args],
            env={**os.environ, "CURLS_TEST_SOURCE": str(ROOT),
                 "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"},
            text=True, capture_output=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        return result.stdout

    def test_seed_initializes_real_sanitizers_before_installing(self):
        self.assertEqual(
            "Synthetic baseline installed.\n",
            self.run_helper("tests/automation/seed.php"),
        )

    def test_application_initializes_real_sanitizers_without_plugin_hooks(self):
        for command in ("inspect", "upgrade"):
            with self.subTest(command=command):
                result = json.loads(self.run_helper("ops/deploy/application.php", command))
                self.assertEqual(
                    {"version": "1.10.6", "db_version": 507, "links": 1, "logs": 1,
                     "plugins": ["fixture/plugin.php"]},
                    result,
                )


if __name__ == "__main__":
    unittest.main()
