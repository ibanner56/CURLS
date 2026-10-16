"""Build an immutable application archive from a committed Git tree."""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops" / "deploy"))
import artifact


def build(repo, commit, output):
    if not artifact.SHA.fullmatch(commit):
        raise ValueError("Build requires a full commit SHA")
    identity = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"{commit}^{{commit}}"], text=True
    ).strip()
    if identity != commit:
        raise ValueError("Commit did not resolve exactly")
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        raw = Path(temporary) / "source.tar"
        staged = Path(temporary) / "release.tar.gz"
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
             "-C", str(repo), "archive", "--format=tar",
             f"--output={raw}", commit], check=True,
        )
        with tarfile.open(raw, "r:") as source:
            # Upstream .gitattributes intentionally omits .github from archives.
            pin = json.loads(subprocess.check_output(
                ["git", "-C", str(repo), "show",
                 f"{commit}:.github/upstream-release.json"], text=True,
            ))
            code = source.extractfile("includes/version.php").read().decode("utf-8")
            code_version = re.search(
                r"define\(\s*'YOURLS_VERSION'\s*,\s*'([^']+)'\s*\)", code
            )
            db_version = re.search(
                r"define\(\s*'YOURLS_DB_VERSION'\s*,\s*'([0-9]+)'\s*\)", code
            )
            if not code_version or not db_version:
                raise ValueError("Cannot identify the application version/schema")
            manifest = {
                "format": 1, "commit": commit, "upstream": pin,
                "version": code_version[1], "db_version": int(db_version[1]),
            }
            artifact.check_manifest(manifest, commit)
            with staged.open("xb") as target, gzip.GzipFile(
                fileobj=target, mode="wb", filename="", mtime=0
            ) as compressed, tarfile.open(fileobj=compressed, mode="w|") as package:
                for member in source:
                    if member.isdir():
                        continue
                    if member.name.split("/")[0] in artifact.EXCLUDED_ROOTS:
                        continue
                    artifact.safe_path(member.name)
                    if not member.isfile() or member.name == ".curls-release.json":
                        raise ValueError(f"Unexpected application entry: {member.name}")
                    entry = tarfile.TarInfo(member.name)
                    entry.mode = 0o640
                    entry.size = member.size
                    package.addfile(entry, source.extractfile(member))
                content = (json.dumps(manifest, sort_keys=True) + "\n").encode()
                entry = tarfile.TarInfo(".curls-release.json")
                entry.mode = 0o640
                entry.size = len(content)
                package.addfile(entry, io.BytesIO(content))
        with staged.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        artifact.inspect(staged, commit, digest)
        # Publish atomically without replacing a pre-existing artifact.
        os.link(staged, output)
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build(Path.cwd(), args.commit, args.output))
