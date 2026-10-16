"""Prepare, promote, or report a stable candidate. Runs only in GitHub Actions."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

# Local imports must not dirty the checkout checked by prepare_candidate.
sys.dont_write_bytecode = True

from release import (
    SHA, check_automation_transition, check_transition, git, prepare_candidate,
    select_stable,
)


REPO = "ibanner56/CURLS"
UPSTREAM = "YOURLS/YOURLS"
ROOT = Path.cwd()


def api(endpoint, *args):
    return json.loads(subprocess.check_output(
        ["gh", "api", endpoint, *args], text=True
    ))


def output(**values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write("## Stable release candidate\n\n")
        for key, value in values.items():
            stream.write(f"- {key}: `{value}`\n")


def prepare():
    git(ROOT, "config", "user.name", "CURLS release automation")
    git(ROOT, "config", "user.email", "curls-release@users.noreply.github.com")
    git(ROOT, "config", "credential.helper", "!gh auth git-credential")
    pages = api(f"repos/{UPSTREAM}/releases?per_page=100", "--paginate", "--slurp")
    tag = select_stable([item for page in pages for item in page])
    # Fetch only the selected tag, never upstream's development branch.
    git(ROOT, "fetch", "--no-tags", f"https://github.com/{UPSTREAM}.git",
        f"refs/tags/{tag}")
    upstream = git(ROOT, "rev-parse", "FETCH_HEAD^{commit}")
    current = json.loads((ROOT / ".github/upstream-release.json").read_text())
    check_transition(current, tag, upstream)
    check_automation_transition(ROOT, current["commit"], upstream)
    base = git(ROOT, "rev-parse", "HEAD")
    if base != api(f"repos/{REPO}/git/ref/heads/curls")["object"]["sha"]:
        raise ValueError("curls moved before preparation; retry against the new tip")
    mirror = api(f"repos/{REPO}/git/ref/heads/master")["object"]["sha"]
    if not SHA.fullmatch(mirror):
        raise ValueError("Invalid master tip")
    if mirror != upstream:
        git(ROOT, "push", "origin", f"--force-with-lease=refs/heads/master:{mirror}",
            f"{upstream}:refs/heads/master")
    candidate = prepare_candidate(ROOT, tag, upstream)
    # A monthly metadata-only commit prevents inactivity disabling the schedule.
    # It follows the same CI and production-approval path as every other commit.
    now = datetime.now(timezone.utc).strftime("%Y-%m")
    heartbeat = ROOT / ".github/release-heartbeat"
    if not heartbeat.exists() or heartbeat.read_text().strip() != now:
        heartbeat.write_text(now + "\n", encoding="utf-8")
        git(ROOT, "add", "--", ".github/release-heartbeat")
        git(ROOT, "commit", "-m", f"Keep stable release monitoring active ({now})")
        candidate = git(ROOT, "rev-parse", "HEAD")
    changed = candidate != base or os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
    baseline = os.environ.get("BOOTSTRAP_BASELINE") or base
    if not SHA.fullmatch(baseline):
        raise ValueError("Upgrade baseline must be a full commit SHA")
    if changed:
        branch = f"automation/stable-{tag}-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
        git(ROOT, "push", "origin", f"{candidate}:refs/heads/{branch}")
    output(changed=str(changed).lower(), candidate=candidate, baseline=baseline, tag=tag)


def promote(candidate):
    if not SHA.fullmatch(candidate):
        raise ValueError("Promotion requires an exact tested commit")
    git(ROOT, "config", "credential.helper", "!gh auth git-credential")
    git(ROOT, "fetch", "--no-tags", "origin", candidate)
    # No force: a concurrent curls change must reject this promotion.
    git(ROOT, "push", "origin", f"{candidate}:refs/heads/curls")


def report():
    run = os.environ["GITHUB_RUN_ID"]
    title = "Stable release automation needs attention"
    body = (
        "@ibanner56 A stable synchronization, CI, promotion, or approved deployment "
        f"failed. Production has not been automatically rolled back.\n\n"
        f"Run: https://github.com/{REPO}/actions/runs/{run}\n"
        "Inspect the failed job before retrying. If deployment reached database "
        "upgrade, the server deliberately remains in maintenance until recovery."
    )
    pages = api(f"repos/{REPO}/issues?state=open&per_page=100", "--paginate", "--slurp")
    issues = [issue for page in pages for issue in page]
    existing = next((issue for issue in issues if issue["title"] == title), None)
    payload = {"body": body} if existing else {"title": title, "body": body}
    endpoint = (f"repos/{REPO}/issues/{existing['number']}/comments" if existing
                else f"repos/{REPO}/issues")
    subprocess.run(
        ["gh", "api", endpoint, "--method", "POST", "--input", "-"],
        input=json.dumps(payload), text=True, check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "promote", "report"])
    parser.add_argument("--candidate")
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("Release automation is restricted to ibanner56/CURLS")
    {"prepare": prepare, "promote": lambda: promote(args.candidate),
     "report": report}[args.command]()
