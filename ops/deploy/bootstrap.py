"""One-time, operator-approved installer. Does not deploy code or change data."""

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from artifact import SHA


def run(*args):
    subprocess.run(args, check=True)


def create(path, content, mode):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
    path.chmod(mode)


def install(public_key, baseline):
    if os.geteuid() != 0:
        raise PermissionError("Bootstrap requires explicit root invocation")
    os.umask(0o077)
    if not SHA.fullmatch(baseline):
        raise ValueError("The verified live source commit is required")
    key = public_key.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [^\r\n]+)?", key):
        raise ValueError("Supply a plain Ed25519 public key, not a private key/options")
    fingerprint = subprocess.run(
        ["/usr/bin/ssh-keygen", "-l", "-f", str(public_key)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if (len(fingerprint.splitlines()) != 1 or not fingerprint.startswith("256 ")
            or not fingerprint.endswith(" (ED25519)")):
        raise ValueError("The deployment public key must be a valid Ed25519 key")
    site = Path("/etc/nginx/sites-available/contra-live")
    original = site.read_text(encoding="utf-8")
    pattern = r"(?m)^(\s*server\s*\{\s*)$"
    if len(re.findall(pattern, original)) != 1 or "/srv/contra-candidate" not in original:
        raise ValueError("Unexpected live Nginx layout; review before changing it")
    include = "/etc/nginx/snippets/contra-deploy-maintenance.conf"
    if include in original:
        raise ValueError("Deployment maintenance is already installed")
    for path in [
        Path("/var/lib/curls-deploy"), Path("/srv/contra-releases"),
        Path("/usr/local/lib/curls-deploy"), Path("/var/log/curls-deploy"),
        Path("/etc/sudoers.d/curls-deploy"),
        Path("/etc/ssh/sshd_config.d/90-curls-deploy.conf"), Path(include),
        Path("/usr/local/sbin/curls-deploy"),
        site.with_name("contra-live.before-curls-deploy"),
    ]:
        if path.exists() or path.is_symlink():
            raise ValueError(f"Refusing to replace existing bootstrap path: {path}")
    try:
        pwd.getpwnam("curls-deploy")
    except KeyError:
        pass
    else:
        raise ValueError("Deployment user already exists; do not overwrite its access")
    run("/usr/sbin/nginx", "-t")
    run("/usr/sbin/sshd", "-t")
    run("/usr/bin/systemctl", "is-active", "--quiet", "php8.5-fpm")
    for path in [
        Path("/var/lib/curls-deploy"), Path("/usr/local/lib/curls-deploy"),
        Path("/var/log/curls-deploy"),
    ]:
        path.mkdir(mode=0o700)
    Path("/srv/contra-releases").mkdir(mode=0o750)
    shutil.chown("/srv/contra-releases", user="root", group="www-data")
    home = Path("/var/lib/curls-deploy/ssh-home")
    home.mkdir(mode=0o755)
    # sshd can read root-owned keys before changing UID; no deployment user can
    # modify its home, forced command, sudo rule, or application release files.
    (home / ".ssh").mkdir(mode=0o755)
    run("/usr/sbin/useradd", "--system", "--user-group",
        "--home-dir", str(home), "--shell", "/bin/sh", "curls-deploy")
    # The containing directory is traversable, but state/logs/helpers stay private.
    Path("/var/lib/curls-deploy").chmod(0o711)
    for name in ["artifact.py", "state.py", "deploy.py", "application.php"]:
        content = (HERE / name).read_bytes()
        destination = Path("/usr/local/lib/curls-deploy") / name
        with destination.open("xb") as stream:
            stream.write(content)
        destination.chmod(0o644 if name == "application.php" else 0o600)
    Path("/usr/local/lib/curls-deploy").chmod(0o755)
    create(Path("/usr/local/sbin/curls-deploy"), """#!/bin/sh
exec /usr/bin/python3 -I -B /usr/local/lib/curls-deploy/deploy.py "$@"
""", 0o700)
    create(Path("/usr/local/lib/curls-deploy/ssh-entry"), """#!/bin/sh
exec /usr/bin/sudo -n /usr/local/sbin/curls-deploy "$SSH_ORIGINAL_COMMAND"
""", 0o755)
    create(home / ".ssh/authorized_keys",
           'restrict,command="/usr/local/lib/curls-deploy/ssh-entry" ' + key + "\n", 0o644)
    create(Path("/etc/sudoers.d/curls-deploy"),
           "curls-deploy ALL=(root) NOPASSWD: /usr/local/sbin/curls-deploy\n", 0o440)
    run("/usr/sbin/visudo", "-cf", "/etc/sudoers.d/curls-deploy")
    create(Path("/etc/ssh/sshd_config.d/90-curls-deploy.conf"), """Match User curls-deploy
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
    ForceCommand /usr/local/lib/curls-deploy/ssh-entry
Match all
""", 0o644)
    create(Path("/var/lib/curls-deploy/active.json"),
           json.dumps({"commit": baseline, "bootstrap": True}) + "\n", 0o600)
    create(Path(include), "", 0o644)
    create(site.with_name("contra-live.before-curls-deploy"), original, 0o600)
    replacement = re.sub(pattern, lambda match: match[0] + f"\n    include {include};",
                         original, count=1)
    # The live file was read above. Preserve the existing vhost around one include.
    temporary = site.with_name("contra-live.curls-deploy-new")
    create(temporary, replacement, 0o644)
    os.replace(temporary, site)
    run("/usr/sbin/nginx", "-t")
    run("/usr/sbin/sshd", "-t")
    run("/usr/bin/systemctl", "reload", "nginx")
    run("/usr/bin/systemctl", "reload", "ssh")
    print("Bootstrap installed. No application switch or database upgrade performed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_key", type=Path)
    parser.add_argument("verified_live_commit")
    args = parser.parse_args()
    install(args.public_key, args.verified_live_commit)
