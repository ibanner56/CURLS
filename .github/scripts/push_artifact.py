"""Push the already-tested archive over the environment-gated SSH channel."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


def push(archive, commit, digest, baseline):
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (commit, baseline)):
        raise ValueError("Invalid deployment commit/baseline")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid deployment digest")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("Downloaded application archive digest mismatch")
    host = os.environ["SATSUMA_HOST"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+", host):
        raise ValueError("Invalid deployment host")
    key = os.environ.pop("SATSUMA_SSH_KEY")
    hosts = os.environ.pop("SATSUMA_KNOWN_HOSTS")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        for name, value in [("identity", key), ("known_hosts", hosts)]:
            path = root / name
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value.rstrip() + "\n")
        with archive.open("rb") as stream:
            subprocess.run([
                "ssh", "-T", "-i", str(root / "identity"),
                "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ForwardAgent=no",
                "-o", "ClearAllForwardings=yes",
                "-o", f"UserKnownHostsFile={root / 'known_hosts'}",
                "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                f"curls-deploy@{host}", f"deploy {commit} {digest} {baseline}",
            ], stdin=stream, check=True, timeout=1200)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("commit")
    parser.add_argument("digest")
    parser.add_argument("baseline")
    args = parser.parse_args()
    push(args.archive, args.commit, args.digest, args.baseline)
