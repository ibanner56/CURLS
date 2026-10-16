# Stable releases and Satsuma delivery

This automation belongs on `curls`, the default branch. `master` is a disposable
mirror of the selected **official stable tag**, not of upstream `master`.
The release pin is `.github/upstream-release.json`. Private application settings
remain in `/etc/contra-candidate`, outside Git and deployment artifacts.

## Release path

`release-sync.yml` runs daily and supports an explicit manual run on `curls`.
It checks the production approval rule, selects the highest numeric non-draft,
non-prerelease YOURLS release, and fetches only that tag. A backwards version or
moved tag fails. A change to `master` uses an exact expected-tip lease; candidate
branches and `curls` never use a force push.

Before any push, the selected tag is compared with the previously pinned
upstream commit. Changes under `.github/` or `ops/deploy/`, or to `.gitmodules`,
stop synchronization and require a separate trusted review. This protects both
the exact-tag `master` mirror and candidate branches from activating newly
supplied workflows before validation. Ordinary application-only releases can
still synchronize automatically.

The selected tag is merged into the current fork. A conflict leaves `curls` and
production unchanged and opens or updates the owner-notification issue. `master`
can already have advanced to the new stable tag at that point.

The reusable CI workflow runs without App or deployment secrets. It builds one
archive from an exact commit, then tests that archive against a synthetic
installation of the previous `curls` commit. A manual first run can supply the
separately verified live-source commit through `bootstrap_baseline`.

CI uses PHP 8.5, a digest-pinned MariaDB 11.8.6 container, and disposable test
databases. It checks the vendored Composer platform without updating dependencies,
the health-check plugin, upstream unit tests, official database upgrade,
repeat-upgrade idempotency, schema downgrade rejection, data/configuration
preservation, and frontend/caller/API/short-link behavior through Nginx/FPM.
The known 506-to-507 index migration is exercised while the target schema is 507.
Production data and credentials are never CI fixtures.

The synthetic HTTP installation enables URL Health Check, Allow Hyphens, and
Force Lowercase. This is not a claim that every production plugin configuration
has been reproduced. Production additionally checks the actual activated-plugin
list is preserved and probes the actual application with its private settings.

Only successful CI permits a normal fast-forward push of the tested candidate
to `curls`. The production job then waits for environment approval. Its name and
run summary identify the candidate. After approval it rejects a superseded
`curls` tip, downloads the same immutable artifact by ID, checks the archive's
SHA-256, and streams it to Satsuma. It never rebuilds after approval.

A monthly metadata-only heartbeat goes through this same path to keep scheduled
release monitoring active. No new upstream release and no due heartbeat means
the scheduled run does not create a candidate or deployment.

## Credentials and approval boundary

Create a private GitHub App installed **only on `ibanner56/CURLS`**. Its repository
permissions are Contents read/write for tag mirroring/promotion, Workflows
read/write for the exact upstream mirror, and Issues read/write for notifications.
Metadata read is implicit. The implementation does not need pull-request
approval, repository administration, or a server-side App installation.
Promotion requests only Contents write; it cannot change workflows. Environment
validation uses the workflow-provided token with job-level Actions read, not an
additional permission on the App.

Enable repository **Issues** before the first run. Failure notification creates
an owner-mentioned issue or comments on the existing notification issue; App
permission alone cannot enable this repository feature.

Set repository variable `CURLS_APP_CLIENT_ID` and repository secret
`CURLS_APP_PRIVATE_KEY`. Put the private key directly into GitHub from a trusted
workstation; do not store it on Satsuma. Each job mints only its needed short-lived
installation-token permissions. Do not reuse the inherited `BOT_*` secrets.

Before any run, create environment **`satsuma-production`** with:

- Required reviewer: the single user `ibanner56`, on **every deployment**.
- A custom deployment branch policy permitting only branch `curls`, not tags.
- Administrator bypass disabled in the environment settings.
- Self-review allowed so this single owner can approve a manually requested run.
- Environment variable `SATSUMA_HOST` and environment-only secrets
  `SATSUMA_SSH_KEY` and `SATSUMA_KNOWN_HOSTS`.

The workflow checks reviewer and branch-policy settings before synchronization
and again after approval. The administrator-bypass setting also needs explicit
verification in the environment UI. Environment secrets must not be duplicated
as repository secrets.

The known-hosts entry must use the Ed25519 **public** host key obtained on
Satsuma from `/etc/ssh/ssh_host_ed25519_key.pub`. Do not use unauthenticated
`ssh-keyscan`, disable host checking, or expose the private host key.

## One-time setup: explicit operator approval required

Implementation does not authorize publishing, history replacement, SSH setup,
workflow triggers, or production activation. The operator performs these actions
only after reviewing the changes and approving the initial setup batch.

Preserve the old remote `master` and `curls` tips in archive branches before the
initial replacements. Publish the verified live-source commit in a retained
archive branch so first-run CI can fetch that exact baseline. Set `curls` as the
default/control branch and set `master` to the official stable tag only after
the explicit initial force-update approval. Do not merge a stable tag into the
old development-containing `curls` history and call it a stable reconstruction.

Disable the inherited `ci.yml`, `auto-merge.yml`, `update-certificates.yml`,
`update-geoip.yml`, and `update-translations.yml` workflows at repository level.
The files must remain unchanged on the exact upstream `master` mirror. Keep
`curls-ci.yml` and `release-sync.yml` enabled. Restrict any other repository
automation from modifying these branches or bypassing deployment approval.

Generate a dedicated Ed25519 deployment key, place only the private half into the
protected environment secret, and supply the public half to the installer.
If Satsuma is used for one-time secret upload, use a mode-700 temporary directory
and `gh secret set ... < private-key-file`, without printing it or passing it on
the command line. Delete the exact temporary private-key files after upload and
verification. Remove the bootstrap GitHub login from Satsuma afterward.

After reviewing the installer, the approved server invocation is:

```sh
sudo /usr/bin/python3 -B ops/deploy/bootstrap.py \
  /absolute/path/to/deployment-key.pub VERIFIED_LIVE_SOURCE_COMMIT
```

The live-source argument must identify the code already on the server, not the
new stable candidate. Verify public-file correspondence against that retained
source tree before initializing the state. The installer deliberately refuses
existing deployment users/files rather than replacing access or configuration.
If it fails part-way, inspect the created paths and original Nginx backup; do not
blindly rerun it or restore an unrelated old snapshot.

Bootstrap adds only an empty maintenance include to the existing public vhost,
an SSH-only deployment user, root-owned helpers/state, restricted authorization,
and one sudo entry point. It does not move application code or upgrade a
database. Review the empty include, `nginx -t`, `sshd -t`, forced-command status
output, rejection of arbitrary SSH commands, and the existing public/private
HTTP checks before the first deployment.

There is no server Git fetch, GitHub polling, push, or workflow trigger afterward.
Changes to the root-owned deployment helpers require a separate approved
operator installation; an application artifact cannot replace those helpers.

## Server activation and recovery

The root helper accepts only `status` or
`deploy COMMIT ARCHIVE_SHA256 EXPECTED_ACTIVE_COMMIT` through the forced command.
It bounds upload size/time and gzip expansion, checks the manifest/digest,
rejects traversal, duplicate/conflicting paths, links/devices, private config,
and repository/CI/deployment files, and checks the expected active commit.
Release content is data to root; application PHP executes only as `www-data`.

Under a single deployment lock, activation closes the **public** vhost with
503/no-store/Retry-After, stops FPM to quiesce writers, records link/log/plugin
state, and requires a fresh checksum-verified database backup. It switches
`/srv/contra-candidate` to a root-owned immutable release while preserving the
private configuration bridges. The first switch preserves the original
directory under `/srv/contra-releases/bootstrap-COMMIT`.

Backups use the existing root-owned `contra-dance-backup` script and its
seven-copy rotation policy, shared with the daily backup job. Deployment does
not create a separate unrotated database-backup store.

The official upgrade runs as `www-data`. A completion guard treats an internal
`die()` as failure even if PHP would otherwise exit zero. The helper verifies
target schema/version and preserved populations/plugin activation, starts FPM
behind public maintenance, and probes the private frontend, caller login, and
signed read-only API. It records the active commit/digest before reopening.
The private Nginx vhost listens on `127.0.0.1:443`; the public vhost listens on
`172.16.0.4:443`. Only the public vhost includes the deployment maintenance rule.
The probe keeps the real host name and TLS verification but resolves it to
loopback, so public maintenance must not be added to that private vhost.

Before database upgrade begins, a failure restores old code/service and discards
only this invocation's inactive staging files. After database work starts, any
failure keeps public maintenance enabled and FPM stopped. **There is no automatic
database restore, old-code fallback, or reopening of writers in that state.**
This includes uncertain/partial upgrades and failed post-upgrade probes.

Inspect the root-only path reported under `/var/log/curls-deploy`, the fresh
backup under `/var/backups/contra-dance`, active-state JSON, and retained release
directories. Determine the actual database state and make an explicit recovery
decision. Never restore an earlier snapshot after public writers resumed.
An existing failed release directory or maintenance flag intentionally requires
operator investigation. A stale approved run cannot overwrite a newer deployment.

## Local checks

```sh
python3 -B -m unittest discover -s tests/automation -v
```

These standard-library tests cover release selection/retag/rollback, real Git
merges and conflicts, committed-tree/reproducible artifact construction,
archive safety/limits, approval settings, and deployment failure policy. Native
PHP/database/Nginx checks require the hosted CI path above. Do not point upstream
`tests/bootstrap.php` at a live or migration database: it reinstalls test tables.
