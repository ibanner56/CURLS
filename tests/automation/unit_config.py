"""Render the upstream unit-test configuration for disposable hosted CI."""

import os
from pathlib import Path
import re


def main():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("Unit database configuration is restricted to hosted CI")
    password = os.environ["CURLS_CI_PASSWORD"]
    if not re.fullmatch(r"[0-9a-f]{64}", password):
        raise ValueError("Invalid synthetic test credential")
    source = Path("tests/data/config/yourls-tests-config-ci.php").read_text()
    for old, new in [
        ("define( 'YOURLS_DB_USER', 'root' );", "define( 'YOURLS_DB_USER', 'curls_ci' );"),
        ("define( 'YOURLS_DB_PASS', 'secret' );", f"define( 'YOURLS_DB_PASS', '{password}' );"),
    ]:
        if source.count(old) != 1:
            raise ValueError("Upstream test configuration changed; review the fixture")
        source = source.replace(old, new)
    configuration = Path("phpunit.xml.dist").read_text()
    for old, new in [
        ('cacheResult="true"', 'recordTestRunHistory="true"'),
        ('stopOnFailure="true"', 'stopOnFailure="false"'),
    ]:
        if configuration.count(old) != 1:
            raise ValueError("Upstream PHPUnit configuration changed; review the fixture")
        configuration = configuration.replace(old, new)
    with Path("user/config.php").open("x", encoding="utf-8") as stream:
        stream.write(source)
    with Path("phpunit.xml").open("x", encoding="utf-8") as stream:
        stream.write(configuration)


if __name__ == "__main__":
    main()
