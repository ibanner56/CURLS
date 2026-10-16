"""GitHub-side stable release selection and candidate construction."""

import argparse
import json
from pathlib import Path
import re
import subprocess


SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?")


def version(tag):
    if not isinstance(tag, str) or not VERSION.fullmatch(tag):
        raise ValueError("Expected a numeric stable YOURLS release tag")
    parts = tuple(map(int, tag.split(".")))
    return parts + (0,) * (3 - len(parts))


def select_stable(releases):
    stable = [
        item["tag_name"] for item in releases
        if item.get("draft") is False and item.get("prerelease") is False
        and VERSION.fullmatch(item.get("tag_name", ""))
    ]
    if not stable or len({version(tag) for tag in stable}) != len(stable):
        raise ValueError("No unambiguous stable release in the upstream release feed")
    return max(stable, key=version)


def check_transition(current, tag, commit):
    if not SHA.fullmatch(commit) or not SHA.fullmatch(current["commit"]):
        raise ValueError("Invalid release commit")
    if version(tag) < version(current["tag"]):
        raise ValueError("Upstream stable release would move backwards")
    if version(tag) == version(current["tag"]) and commit != current["commit"]:
        raise ValueError("Upstream stable tag was moved; manual investigation required")


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def check_automation_transition(repo, previous, commit):
    if not all(SHA.fullmatch(value) for value in (previous, commit)):
        raise ValueError("Automation comparison requires exact upstream commits")
    changes = git(
        repo, "diff", "--name-only", previous, commit, "--",
        ".github", "ops/deploy", ".gitmodules",
    )
    if changes:
        raise ValueError(
            "Upstream changed trusted automation; review separately before any push:\n"
            + changes
        )


def prepare_candidate(repo, tag, commit):
    pin = repo / ".github" / "upstream-release.json"
    current = json.loads(pin.read_text(encoding="utf-8"))
    check_transition(current, tag, commit)
    if git(repo, "status", "--porcelain"):
        raise ValueError("Candidate workspace must be clean")
    if current == {"tag": tag, "commit": commit}:
        return git(repo, "rev-parse", "HEAD")
    check_automation_transition(repo, current["commit"], commit)
    git(repo, "merge", "--no-edit", "--no-ff", commit)
    pin.write_text(
        json.dumps({"tag": tag, "commit": commit}, indent=2) + "\n",
        encoding="utf-8",
    )
    git(repo, "add", "--", ".github/upstream-release.json")
    git(repo, "commit", "-m", f"Pin upstream stable YOURLS {tag}")
    return git(repo, "rev-parse", "HEAD")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("releases", type=Path)
    args = parser.parse_args()
    print(select_stable(json.loads(args.releases.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
