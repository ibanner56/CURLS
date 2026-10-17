import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs


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

    def test_signed_probe_accepts_stats_contract_and_rejects_invalid_responses(self):
        posts = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = b"password" if self.path == "/caller/" else b"shortenlink"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                posts.append(parse_qs(
                    self.rfile.read(int(self.headers["Content-Length"])).decode()
                ))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(response_body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            site = f"http://127.0.0.1:{server.server_port}"
            with (self.root / "includes/load-yourls.php").open("a", encoding="utf-8") as stream:
                stream.write("""
define('YOURLS_SITE', getenv('CURLS_TEST_SITE'));
$yourls_user_passwords = ['fixture' => 'fixture-password'];
function yourls_auth_signature($username = false) { return 'fixture-signature'; }
""")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            valid = {
                "message": "success", "statusCode": "200",
                "stats": {"total_links": 1, "total_clicks": 0},
            }
            try:
                for response, success in [
                    (valid, True),
                    ({"errorCode": "403", "message": "Forbidden"}, False),
                    ({"status": "success"}, False),
                    ({**valid, "stats": {"total_links": 2}}, False),
                    ("not JSON", False),
                ]:
                    with self.subTest(response=response):
                        response_body = (
                            response if isinstance(response, str) else json.dumps(response)
                        ).encode()
                        posts.clear()
                        result = subprocess.run(
                            ["php", str(ROOT / "ops/deploy/application.php"),
                             str(self.root), "probe"],
                            env={**os.environ, "CURLS_TEST_SOURCE": str(ROOT),
                                 "CURLS_TEST_SITE": site},
                            text=True, capture_output=True,
                        )
                        self.assertEqual(success, result.returncode == 0,
                                         result.stdout + result.stderr)
                        self.assertEqual(1, len(posts))
                        self.assertEqual(["stats"], posts[0]["action"])
                        self.assertEqual(["sha256"], posts[0]["hash"])
                        self.assertEqual(
                            [hashlib.sha256(
                                (posts[0]["timestamp"][0] + "fixture-signature").encode()
                            ).hexdigest()],
                            posts[0]["signature"],
                        )
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
