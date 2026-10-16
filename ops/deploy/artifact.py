"""Shared artifact validation; installed root-owned on Satsuma."""

from contextlib import contextmanager
import gzip
import hashlib
import json
from pathlib import PurePosixPath
import re
import tarfile
import tempfile


SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?")
COMPRESSED_LIMIT = 256 * 1024 * 1024
EXPANDED_LIMIT = 512 * 1024 * 1024
TAR_LIMIT = EXPANDED_LIMIT + 16 * 1024 * 1024
PRIVATE_FILES = {"user/config.php", "frontend/config.php"}
EXCLUDED_ROOTS = {".git", ".github", "tests", "ops"}


@contextmanager
def open_archive(archive):
    # Bound the entire gzip expansion before parsing tar/PAX headers, not only
    # the sizes of regular members visible to tarfile.
    with tempfile.TemporaryFile() as raw, gzip.open(archive, "rb") as compressed:
        total = 0
        while chunk := compressed.read(1024 * 1024):
            total += len(chunk)
            if total > TAR_LIMIT:
                raise ValueError("Archive headers/data exceed the expansion limit")
            raw.write(chunk)
        raw.seek(0)
        with tarfile.open(fileobj=raw, mode="r:") as source:
            yield source


def safe_path(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\x00" in name or ":" in name
            or path.is_absolute() or any(p in {"", ".", ".."} for p in name.split("/"))
            or path.parts[0] in EXCLUDED_ROOTS or name in PRIVATE_FILES):
        raise ValueError(f"Forbidden artifact path: {name!r}")
    return path


def check_manifest(manifest, commit):
    if (not isinstance(manifest, dict)
            or set(manifest) != {"format", "commit", "upstream", "version", "db_version"}
            or type(manifest["format"]) is not int or manifest["format"] != 1
            or manifest["commit"] != commit
            or type(manifest["db_version"]) is not int or manifest["db_version"] < 507):
        raise ValueError("Invalid release manifest")
    upstream = manifest["upstream"]
    if (not isinstance(upstream, dict) or set(upstream) != {"tag", "commit"}
            or not isinstance(upstream["tag"], str)
            or not VERSION.fullmatch(upstream["tag"])
            or not isinstance(upstream["commit"], str)
            or not SHA.fullmatch(upstream["commit"])
            or manifest["version"] != upstream["tag"]):
        raise ValueError("Artifact is not an exact stable-version build")


def inspect(archive, commit, digest, *, expanded_limit=EXPANDED_LIMIT):
    if not SHA.fullmatch(commit) or not DIGEST.fullmatch(digest):
        raise ValueError("Invalid artifact identity")
    if archive.stat().st_size > COMPRESSED_LIMIT:
        raise ValueError("Compressed artifact exceeds the size limit")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("Artifact digest mismatch")
    files = set()
    directories = set()
    implicit_directories = set()
    total = 0
    manifest = None
    with open_archive(archive) as source:
        for index, member in enumerate(source):
            if index >= 50000:
                raise ValueError("Too many archive entries")
            name = member.name
            path = safe_path(name)
            if not member.isfile() and not member.isdir():
                raise ValueError("Only regular files and directories are accepted")
            if (name in files or name in directories
                    or any(str(parent) in files for parent in path.parents)
                    or member.isfile() and name in implicit_directories):
                raise ValueError(f"Duplicate or conflicting artifact path: {name}")
            (files if member.isfile() else directories).add(name)
            implicit_directories.update(str(parent) for parent in path.parents)
            total += member.size
            if member.size < 0 or total > expanded_limit:
                raise ValueError("Expanded artifact exceeds the size limit")
            if name == ".curls-release.json":
                if not member.isfile() or member.size > 4096:
                    raise ValueError("Invalid manifest entry")
                manifest = json.load(source.extractfile(member))
    if manifest is None or not {"includes/version.php", "index.php"} <= files:
        raise ValueError("Artifact is missing required release files")
    check_manifest(manifest, commit)
    return manifest


def extract(archive, destination, commit, digest):
    manifest = inspect(archive, commit, digest)
    destination.mkdir(mode=0o750, parents=False, exist_ok=False)
    # Avoid tar extract APIs entirely: every accepted entry is written as plain data.
    with open_archive(archive) as source:
        for member in source:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(mode=0o750, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                with source.extractfile(member) as data, target.open("xb") as output:
                    while chunk := data.read(1024 * 1024):
                        output.write(chunk)
                target.chmod(0o640)
    return manifest
