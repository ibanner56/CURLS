"""Restricted, root-owned Satsuma deploy entry point. No GitHub/network client."""

import grp
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import traceback


sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact
from state import activate


CURRENT = Path("/srv/contra-candidate")
RELEASES = Path("/srv/contra-releases")
STATE = Path("/var/lib/curls-deploy/active.json")
MAINTENANCE = Path("/etc/nginx/snippets/contra-deploy-maintenance.conf")
BACKUPS = Path("/var/backups/contra-dance")
LIB = Path("/usr/local/lib/curls-deploy")


def run(*args, capture=False, timeout=180):
    result = subprocess.run(
        args, check=True, text=True, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else None


def atomic_write(path, text, mode=0o600):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            os.chmod(temporary, mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink()
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_state():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if not artifact.SHA.fullmatch(state["commit"]):
        raise ValueError("Invalid installed release state")
    return state


class Deployer:
    def __init__(self, commit, digest, baseline, manifest, destination):
        self.commit = commit
        self.digest = digest
        self.baseline = baseline
        self.manifest = manifest
        self.destination = destination
        self.old_target = CURRENT.resolve(strict=True)
        self.old_state = read_state()

    def application(self, command):
        return json.loads(run(
            "/usr/sbin/runuser", "-u", "www-data", "--",
            "/usr/bin/php", "-d", "display_errors=stderr",
            str(LIB / "application.php"), str(CURRENT), command,
            capture=True,
        ))

    def preflight(self):
        if self.old_state["commit"] != self.baseline:
            raise ValueError("Stale deployment: active commit differs from tested baseline")
        if MAINTENANCE.read_text().strip():
            raise ValueError("Existing maintenance requires operator recovery")
        run("/usr/bin/systemctl", "is-active", "--quiet", "php8.5-fpm")
        run("/usr/bin/systemctl", "is-active", "--quiet", "mariadb")
        run("/usr/sbin/nginx", "-t")
        current = self.application("inspect")
        if current["db_version"] > self.manifest["db_version"]:
            raise ValueError("Refusing a database schema downgrade")
        for config in [
            Path("/etc/contra-candidate/app-config.php"),
            Path("/etc/contra-candidate/frontend-config.php"),
        ]:
            if (not config.is_file() or config.stat().st_uid != 0
                    or config.stat().st_mode & 0o022):
                raise ValueError("Private configuration must not be writable by the service")

    def maintenance(self, enabled):
        content = ('add_header Retry-After "120" always;\n'
                   'add_header Cache-Control "no-store" always;\nreturn 503;\n'
                   if enabled else "")
        atomic_write(MAINTENANCE, content, mode=0o644)
        run("/usr/sbin/nginx", "-t")
        run("/usr/bin/systemctl", "reload", "nginx")
        expected = "503" if enabled else "200"
        for attempt in range(20):
            actual = run(
                "/usr/bin/curl", "--silent", "--show-error", "--max-time", "10",
                "--output", "/dev/null", "--write-out", "%{http_code}",
                "--resolve", "contra.dance:443:172.16.0.4",
                "https://contra.dance/", capture=True,
            ).strip()
            if actual == expected:
                return
            time.sleep(0.2)
        raise ValueError("Public maintenance/service status did not become effective")

    def stop_workers(self):
        run("/usr/bin/systemctl", "stop", "php8.5-fpm")

    def start_workers(self):
        run("/usr/bin/systemctl", "start", "php8.5-fpm")
        run("/usr/bin/systemctl", "is-active", "--quiet", "php8.5-fpm")

    def snapshot(self):
        return self.application("inspect")

    def backup(self):
        before = set(BACKUPS.iterdir())
        run("/usr/local/sbin/contra-dance-backup")
        completed = [
            path for path in set(BACKUPS.iterdir()) - before
            if path.is_dir() and (path / "SHA256SUMS").is_file()
            and (path / "yourls_live.sql.gz").is_file()
        ]
        if len(completed) != 1:
            raise ValueError("A fresh completed database backup was not produced")
        backup = completed[0]
        run("/usr/bin/gzip", "-t", str(backup / "yourls_live.sql.gz"))
        expected = (backup / "SHA256SUMS").read_text().split()[0]
        with (backup / "yourls_live.sql.gz").open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("Fresh database backup checksum mismatch")
        print(f"Recovery backup: {backup}", flush=True)

    def replace_link(self, target):
        temporary = CURRENT.with_name(f".contra-candidate-next-{os.getpid()}")
        temporary.symlink_to(target, target_is_directory=True)
        try:
            os.replace(temporary, CURRENT)
        finally:
            temporary.unlink(missing_ok=True)

    def switch(self):
        if not CURRENT.is_symlink():
            bootstrap = RELEASES / f"bootstrap-{self.old_state['commit']}"
            if bootstrap.exists():
                raise ValueError("Bootstrap recovery directory already exists")
            CURRENT.rename(bootstrap)
            self.old_target = bootstrap
        self.replace_link(self.destination)

    def restore_code(self):
        if CURRENT.is_symlink() or not CURRENT.exists():
            self.replace_link(self.old_target)

    def upgrade(self):
        self.application("upgrade")

    def verify_snapshot(self, before):
        after = self.application("inspect")
        if any(after[key] != before[key] for key in ("links", "logs", "plugins")):
            raise ValueError("Upgrade changed link/log populations or plugin activation")
        if (after["version"] != self.manifest["version"]
                or after["db_version"] != self.manifest["db_version"]):
            raise ValueError("Upgrade did not reach the tested release metadata")

    def probe(self):
        self.application("probe")

    def record(self):
        atomic_write(STATE, json.dumps({
            "commit": self.commit, "digest": self.digest,
            "previous": self.old_state["commit"],
            "upstream": self.manifest["upstream"],
        }, sort_keys=True) + "\n")


def receive():
    received = 0
    def timeout(signum, frame):
        raise TimeoutError("Artifact upload timed out")
    previous_handler = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(120)
    stream = tempfile.NamedTemporaryFile(dir=STATE.parent, delete=False)
    path = Path(stream.name)
    try:
        with stream:
            while data := sys.stdin.buffer.read(1024 * 1024):
                received += len(data)
                if received > artifact.COMPRESSED_LIMIT:
                    raise ValueError("Upload exceeds size limit")
                stream.write(data)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def main():
    import fcntl

    if os.geteuid() != 0:
        raise PermissionError("The restricted deploy entry point requires root")
    os.umask(0o077)
    os.environ.clear()
    os.environ.update(PATH="/usr/sbin:/usr/bin:/sbin:/bin", LANG="C.UTF-8")
    args = sys.argv[1].split(" ") if len(sys.argv) == 2 else []
    if args == ["status"]:
        print(json.dumps({
            "commit": read_state()["commit"],
            "maintenance": bool(MAINTENANCE.read_text().strip()),
        }))
        return
    if (len(args) != 4 or args[0] != "deploy"
            or not artifact.SHA.fullmatch(args[1])
            or not artifact.DIGEST.fullmatch(args[2])
            or not artifact.SHA.fullmatch(args[3])):
        raise ValueError("Allowed commands: status; deploy COMMIT SHA256 BASELINE")
    _, commit, digest, baseline = args
    with (STATE.parent / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        upload = receive()
        destination = RELEASES / commit
        created = False
        backend = None
        try:
            manifest = artifact.inspect(upload, commit, digest)
            active = read_state()
            if active["commit"] == commit:
                if (active.get("digest") != digest or MAINTENANCE.read_text().strip()
                        or CURRENT.resolve() != RELEASES / commit):
                    raise ValueError("Existing deployment requires operator investigation")
                Deployer(commit, digest, baseline, manifest, RELEASES / commit).probe()
                return
            if active["commit"] != baseline:
                raise ValueError("Stale deployment; no live changes made")
            if destination.exists() or destination.is_symlink():
                raise ValueError("Existing release directory requires operator investigation")
            created = True
            artifact.extract(upload, destination, commit, digest)
            group = grp.getgrnam("www-data").gr_gid
            for directory, directories, files in os.walk(destination):
                os.chown(directory, 0, group)
                os.chmod(directory, 0o750)
                for name in files:
                    os.chown(Path(directory) / name, 0, group)
            for relative, private in [
                ("user/config.php", "/etc/contra-candidate/app-config.php"),
                ("frontend/config.php", "/etc/contra-candidate/frontend-config.php"),
            ]:
                bridge = destination / relative
                with bridge.open("x", encoding="utf-8") as stream:
                    stream.write(f"<?php\nrequire '{private}';\n")
                os.chown(bridge, 0, group)
                os.chmod(bridge, 0o640)
            backend = Deployer(commit, digest, baseline, manifest, destination)
            activate(backend)
        except Exception:
            if (created and not getattr(backend, "database_started", False)
                    and destination.exists() and not destination.is_symlink()
                    and CURRENT.resolve() != destination):
                # Only this invocation's new, inactive staging tree is disposable.
                shutil.rmtree(destination)
            raise
        finally:
            upload.unlink(missing_ok=True)


if __name__ == "__main__":
    log = None
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "status":
            main()
        else:
            os.umask(0o077)
            log = Path("/var/log/curls-deploy") / f"{time.time_ns()}.log"
            with log.open("x") as stream:
                saved_output = os.dup(1)
                saved_error = os.dup(2)
                try:
                    os.dup2(stream.fileno(), 1)
                    os.dup2(stream.fileno(), 2)
                    main()
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    os.dup2(saved_output, 1)
                    os.dup2(saved_error, 2)
                    os.close(saved_output)
                    os.close(saved_error)
            print("Deployment and private health checks completed.")
    except Exception as error:
        # No PHP/configuration output crosses the SSH boundary.
        if log is not None and log.exists():
            with log.open("a") as stream:
                traceback.print_exc(file=stream)
        print(f"Deployment failed ({type(error).__name__}); inspect {log}.",
              file=sys.stderr)
        sys.exit(1)
