import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "push_artifact", ROOT / ".github/scripts/push_artifact.py"
)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


class ArtifactDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive = Path(temporary.name) / "release.tar.gz"
        self.archive.write_bytes(b"synthetic tested archive")
        self.commit, self.baseline = "a" * 40, "b" * 40
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        patcher = mock.patch.dict(os.environ, {
            "SATSUMA_HOST": "satsuma.example.test",
            "SATSUMA_SSH_KEY": "synthetic-test-credential",
            "SATSUMA_KNOWN_HOSTS": "synthetic-test-known-hosts",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_invalid_identity_digest_or_host_never_starts_ssh(self):
        for commit, digest, baseline, host in [
            ("invalid", self.digest, self.baseline, "valid.test"),
            (self.commit, self.digest, "invalid", "valid.test"),
            (self.commit, "invalid", self.baseline, "valid.test"),
            (self.commit, "0" * 64, self.baseline, "valid.test"),
            (self.commit, self.digest, self.baseline, "-oProxyCommand=bad"),
        ]:
            with self.subTest(commit=commit, digest=digest, baseline=baseline, host=host):
                with mock.patch.dict(os.environ, {"SATSUMA_HOST": host}):
                    with mock.patch.object(delivery.subprocess, "run") as ssh:
                        with self.assertRaises(ValueError):
                            delivery.push(self.archive, commit, digest, baseline)
                        ssh.assert_not_called()

    def check_ssh(self, command, *, stdin, check, timeout):
        self.identity = Path(command[command.index("-i") + 1])
        known_hosts = next(arg.split("=", 1)[1] for arg in command
                           if arg.startswith("UserKnownHostsFile="))
        self.assertEqual("synthetic-test-credential\n", self.identity.read_text())
        self.assertEqual("synthetic-test-known-hosts\n", Path(known_hosts).read_text())
        if os.name != "nt":
            self.assertEqual(0o600, self.identity.stat().st_mode & 0o777)
            self.assertEqual(0o700, self.identity.parent.stat().st_mode & 0o777)
        self.assertNotIn("SATSUMA_SSH_KEY", os.environ)
        self.assertNotIn("SATSUMA_KNOWN_HOSTS", os.environ)
        self.assertEqual(b"synthetic tested archive", stdin.read())
        self.assertEqual(["curls-deploy@satsuma.example.test",
                          f"deploy {self.commit} {self.digest} {self.baseline}"],
                         command[-2:])
        for option in ["StrictHostKeyChecking=yes", "IdentitiesOnly=yes",
                       "BatchMode=yes", "ForwardAgent=no", "ClearAllForwardings=yes"]:
            self.assertIn(option, command)
        self.assertTrue(check)
        self.assertEqual(1200, timeout)

    def test_streams_exact_archive_and_removes_temporary_credentials(self):
        with mock.patch.object(delivery.subprocess, "run", side_effect=self.check_ssh):
            delivery.push(self.archive, self.commit, self.digest, self.baseline)
        self.assertFalse(self.identity.parent.exists())

    def test_ssh_failure_is_reported_and_credentials_are_still_removed(self):
        def fail(*args, **kwargs):
            self.check_ssh(*args, **kwargs)
            raise subprocess.CalledProcessError(1, "ssh")
        with mock.patch.object(delivery.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                delivery.push(self.archive, self.commit, self.digest, self.baseline)
        self.assertFalse(self.identity.parent.exists())


if __name__ == "__main__":
    unittest.main()
