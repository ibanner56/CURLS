"""Hosted-CI-only synthetic upgrade and Nginx/FPM application checks."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops/deploy"))
import artifact


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def config(root):
    password = os.environ["CURLS_CI_PASSWORD"]
    port = int(os.environ["DB_PORT"])
    if len(password) != 64 or any(char not in "0123456789abcdef" for char in password):
        raise ValueError("Invalid synthetic database password")
    text = f"""<?php
define('YOURLS_DB_USER', 'curls_ci');
define('YOURLS_DB_PASS', '{password}');
define('YOURLS_DB_NAME', 'curls_upgrade');
define('YOURLS_DB_HOST', '127.0.0.1:{port}');
define('YOURLS_DB_PREFIX', 'yourls_');
define('YOURLS_SITE', 'http://contra-ci.test:8080');
define('YOURLS_COOKIEKEY', '{secrets.token_hex(32)}');
define('YOURLS_PRIVATE', true);
define('YOURLS_DEBUG', false);
define('YOURLS_FLOOD_DELAY_SECONDS', 0);
$yourls_user_passwords = ['curls-ci' => '{secrets.token_hex(24)}'];
require __DIR__ . '/reserved_words.php';
"""
    (root / "user/config.php").write_text(text, encoding="utf-8")
    shutil.copyfile(root / "frontend/config-sample.php", root / "frontend/config.php")


def application(root, command):
    args = ["php", str(root.parent / "application.php"), str(root), command]
    if command == "probe":
        args = ["sudo", "-u", "www-data", "--", *args]
    return json.loads(run(*args))


def verify_http_configuration(root, original):
    for name, expected in original.items():
        digest = run(
            "sudo", "-u", "www-data", "--", "sha256sum", "--", str(root / name),
        ).split()[0]
        if digest != expected:
            raise ValueError("HTTP authentication changed private configuration")


@contextmanager
def workspace():
    # Do not depend on the service account being able to traverse the runner home.
    with tempfile.TemporaryDirectory(prefix="curls-http-", dir="/tmp") as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        helper = work / "application.php"
        shutil.copyfile(ROOT / "ops/deploy/application.php", helper)
        helper.chmod(0o644)
        owner = f"{os.getuid()}:{os.getgid()}"
        try:
            yield work
        finally:
            candidate = work / "candidate"
            if candidate.exists():
                # Restore the disposable tree before unprivileged tempfile cleanup.
                run("sudo", "chown", "-R", owner, "--", str(candidate))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(path, data=None, port=8080):
    opener = urllib.request.build_opener(NoRedirect)
    headers = {"Host": f"contra-ci.test:{port}"}
    content = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}" + path, content, headers)
    try:
        with opener.open(req, timeout=15) as response:
            return response.status, response.read().decode(), response.headers
    except urllib.error.HTTPError as response:
        return response.code, response.read().decode(), response.headers


def main(archive, commit, digest, baseline):
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("Integration is restricted to disposable GitHub-hosted CI")
    if not artifact.SHA.fullmatch(baseline):
        raise ValueError("Invalid upgrade baseline")
    with workspace() as work:
        old = work / "baseline"
        old.mkdir()
        raw = work / "baseline.tar"
        run("git", "archive", "--format=tar", f"--output={raw}", baseline)
        with tarfile.open(raw) as source:
            for member in source:
                if member.isdir() or member.name.split("/")[0] in artifact.EXCLUDED_ROOTS:
                    continue
                artifact.safe_path(member.name)
                if not member.isfile():
                    raise ValueError("Baseline contains a non-regular application entry")
                target = old / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as data, target.open("xb") as stream:
                    shutil.copyfileobj(data, stream)
        config(old)
        print(run("php", str(ROOT / "tests/automation/seed.php"), str(old)), flush=True)
        before = application(old, "inspect")
        candidate = work / "candidate"
        manifest = artifact.extract(archive, candidate, commit, digest)
        shutil.copyfile(old / "user/config.php", candidate / "user/config.php")
        shutil.copyfile(old / "frontend/config.php", candidate / "frontend/config.php")
        configs = ["user/config.php", "frontend/config.php"]
        original = {
            name: hashlib.sha256((candidate / name).read_bytes()).hexdigest()
            for name in configs
        }
        after = application(candidate, "upgrade")
        for key in ("links", "logs", "plugins"):
            if before[key] != after[key]:
                raise ValueError(f"Upgrade did not preserve {key}")
        if after["db_version"] != manifest["db_version"] or after["version"] != manifest["version"]:
            raise ValueError("Upgrade metadata mismatch")
        if after != application(candidate, "upgrade"):
            raise ValueError("Repeated upgrade is not idempotent")
        if manifest["db_version"] == 507:
            run("php", str(ROOT / "tests/automation/schema_fixture.php"),
                str(candidate), "506")
            if application(candidate, "upgrade") != after:
                raise ValueError("Official 506-to-507 schema upgrade lost data")
        run("php", str(ROOT / "tests/automation/schema_fixture.php"),
            str(candidate), "future")
        try:
            application(candidate, "upgrade")
        except subprocess.CalledProcessError:
            pass
        else:
            raise ValueError("A database schema downgrade was accepted")
        run("php", str(ROOT / "tests/automation/schema_fixture.php"),
            str(candidate), "restore")
        for name, expected in original.items():
            if hashlib.sha256((candidate / name).read_bytes()).hexdigest() != expected:
                raise ValueError("Upgrade changed private configuration")

        run("sudo", "chown", "-R", "root:www-data", str(candidate))
        for path in (candidate / "index.php", candidate / "user/config.php"):
            run("sudo", "-u", "www-data", "--", "test", "-r", str(path))
        nginx = work / "nginx.conf"
        nginx.write_text(f"""
user www-data;
pid {work}/nginx.pid;
error_log {work}/nginx-error.log;
events {{}}
http {{
    include /etc/nginx/mime.types;
    access_log off;
    server {{
        listen 127.0.0.1:8081;
        server_name contra-ci.test;
        add_header Retry-After "120" always;
        add_header Cache-Control "no-store" always;
        return 503;
    }}
    server {{
        listen 127.0.0.1:8080;
        server_name contra-ci.test;
        root {candidate};
        index index.php;
        location ~ /\\. {{ return 404; }}
        location ~ ^/(includes|tests|ops|user)/.*\\.php$ {{ return 404; }}
        location = /frontend/config.php {{ return 404; }}
        location / {{ try_files $uri $uri/ /yourls-loader.php$is_args$args; }}
        location ~ \\.php$ {{
            try_files $uri =404;
            include /etc/nginx/fastcgi_params;
            fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
            fastcgi_param HTTP_PROXY "";
            fastcgi_pass unix:/run/php/php8.5-fpm.sock;
        }}
    }}
}}
""", encoding="utf-8")
        run("sudo", "systemctl", "restart", "php8.5-fpm")
        run("sudo", "nginx", "-t", "-c", str(nginx))
        run("sudo", "nginx", "-c", str(nginx))
        try:
            for attempt in range(20):
                status, body, _ = request("/")
                if status == 200 and "shortenlink" in body:
                    break
                time.sleep(0.5)
            else:
                raise ValueError(
                    f"Frontend failed through Nginx/FPM: HTTP {status}, "
                    f"form marker present: {'shortenlink' in body}"
                )
            status, body, _ = request("/caller/")
            if status != 200 or "password" not in body:
                raise ValueError("Caller login form missing")
            for path in ["/user/config.php", "/frontend/config.php",
                         "/includes/version.php", "/.curls-release.json"]:
                if request(path)[0] != 404:
                    raise ValueError(f"Private path was served: {path}")
            if request("/yourls-api.php", {"action": "stats", "format": "json"})[0] != 403:
                raise ValueError("Unauthenticated API was not rejected")
            # The shared runner uses a signed POST, never credentials in URLs.
            if request("/", port=8081)[0] != 503:
                raise ValueError("Public maintenance listener did not remain closed")
            application(candidate, "probe")
            if request("/", port=8081)[0] != 503:
                raise ValueError("Private probing reopened the public listener")
            status, body, _ = request("/", {
                "url": "https://example.com/", "keyword": "CI-created",
            })
            if status != 200 or "Your shortened link" not in body:
                raise ValueError("Public shortening with actual plugins failed")
            status, _, headers = request("/cipreserved")
            if status != 301 or headers.get("Location") != "https://example.test/preserved":
                raise ValueError("Existing short-link redirect was not preserved")
            status, _, headers = request("/ci-created")
            if status != 301 or headers.get("Location") != "https://example.com/":
                raise ValueError("Lowercase plugin/new-link redirect failed")
            verify_http_configuration(candidate, original)
        except Exception:
            # Only this synthetic server's errors; no response bodies or configs.
            diagnostic = subprocess.run(
                ["sudo", "tail", "-n", "40", str(work / "nginx-error.log")],
                check=False,
            )
            if diagnostic.returncode != 0:
                print("Could not read the synthetic Nginx error log.",
                      file=sys.stderr, flush=True)
            raise
        finally:
            run("sudo", "nginx", "-s", "quit", "-c", str(nginx))
        print("Synthetic baseline upgrade, redeploy, data/config preservation and HTTP checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("commit")
    parser.add_argument("digest")
    parser.add_argument("baseline")
    args = parser.parse_args()
    main(args.archive, args.commit, args.digest, args.baseline)
