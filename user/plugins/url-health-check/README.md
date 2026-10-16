# URL Health Check integration

This bundled plugin uses the YOURLS 1.10.4+ shunt contract. A successful check
preserves the incoming continue sentinel, and an earlier plugin rejection is
returned unchanged. URL validation, redirect handling, and rejection responses
remain enabled. HTTPS checks verify the certificate chain and hostname.

## Focused tests

Install the existing test dependencies with `composer --working-dir=tests install`
if PHPUnit is unavailable. Run these commands from the repository root:

```bash
php tests/vendor/bin/phpunit --no-configuration \
  --bootstrap tests/includes/url-health-check-bootstrap.php \
  --exclude-group network tests/tests/plugins/URLHealthCheckTest.php

php tests/vendor/bin/phpunit --no-configuration \
  --bootstrap tests/includes/url-health-check-bootstrap.php \
  --group network tests/tests/plugins/URLHealthCheckTest.php
```

The dedicated bootstrap does not read application configuration, connect to a
database, or invoke the database-installing `tests/bootstrap.php`. Do not replace
it with the normal application test bootstrap against a migration database.

The network group additionally requires the cURL extension and outbound HTTPS
access to `example.com` and `self-signed.badssl.com`. It checks both a trusted
certificate and rejection of a self-signed certificate, and explicitly fails if
the latter fixture cannot be reached well enough to test certificate validation.
