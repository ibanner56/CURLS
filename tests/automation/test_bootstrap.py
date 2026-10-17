import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops/deploy"))
SPEC = importlib.util.spec_from_file_location(
    "bootstrap", ROOT / "ops/deploy/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {"pwd": mock.Mock()}):
    SPEC.loader.exec_module(bootstrap)


class BootstrapKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.key = Path(temporary.name) / "deployment.pub"
        self.key.write_text("ssh-ed25519 AAAA synthetic-test\n")

    def test_malformed_key_is_rejected_before_any_system_change(self):
        with mock.patch.object(bootstrap.os, "geteuid", return_value=0, create=True):
            with mock.patch.object(bootstrap.os, "umask"):
                with mock.patch.object(bootstrap.subprocess, "run", side_effect=
                                       subprocess.CalledProcessError(1, "ssh-keygen")):
                    with mock.patch.object(bootstrap, "create") as create:
                        with self.assertRaises(subprocess.CalledProcessError):
                            bootstrap.install(self.key, "a" * 40)
                        create.assert_not_called()

    def test_wrong_key_type_and_multiple_keys_are_rejected(self):
        for fingerprint in [
            "2048 SHA256:synthetic comment (RSA)\n",
            "256 SHA256:synthetic comment (ED25519)\n"
            "256 SHA256:synthetic other (ED25519)\n",
        ]:
            with self.subTest(fingerprint=fingerprint):
                with mock.patch.object(bootstrap.os, "geteuid", return_value=0, create=True):
                    with mock.patch.object(bootstrap.os, "umask"):
                        with mock.patch.object(bootstrap.subprocess, "run",
                                               return_value=mock.Mock(stdout=fingerprint)):
                            with mock.patch.object(bootstrap, "create") as create:
                                with self.assertRaisesRegex(ValueError, "valid Ed25519"):
                                    bootstrap.install(self.key, "a" * 40)
                                create.assert_not_called()


@unittest.skipUnless(os.name == "posix", "Requires POSIX directory permissions")
class BootstrapPermissionTests(unittest.TestCase):
    def test_service_directories_remain_traversable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def installed_path(value):
                return root.joinpath(*Path(value).parts[1:])

            for relative in [
                "etc/nginx/sites-available", "etc/nginx/snippets",
                "etc/ssh/sshd_config.d", "etc/sudoers.d",
                "usr/local/lib", "usr/local/sbin", "var/lib", "var/log", "srv",
            ]:
                (root / relative).mkdir(parents=True, exist_ok=True)
            (root / "etc/nginx/sites-available/contra-live").write_text(
                "server {\n    root /srv/contra-candidate;\n}\n"
            )
            key = root / "deployment.pub"
            key.write_text("ssh-ed25519 AAAA synthetic-test\n")
            previous_umask = os.umask(0o022)
            try:
                with (
                    mock.patch.object(bootstrap, "Path", side_effect=installed_path),
                    mock.patch.object(bootstrap.os, "geteuid", return_value=0),
                    mock.patch.object(bootstrap.pwd, "getpwnam", side_effect=KeyError),
                    mock.patch.object(bootstrap, "run"),
                    mock.patch.object(bootstrap.shutil, "chown"),
                    mock.patch.object(
                        bootstrap.subprocess, "run",
                        return_value=mock.Mock(
                            stdout="256 SHA256:synthetic key (ED25519)\n"
                        ),
                    ),
                ):
                    bootstrap.install(key, "a" * 40)
            finally:
                os.umask(previous_umask)

            for relative, mode in [
                ("srv/contra-releases", 0o750),
                ("var/lib/curls-deploy/ssh-home", 0o755),
                ("var/lib/curls-deploy/ssh-home/.ssh", 0o755),
                ("var/lib/curls-deploy/active.json", 0o600),
            ]:
                with self.subTest(path=relative):
                    self.assertEqual(stat.S_IMODE((root / relative).stat().st_mode), mode)


if __name__ == "__main__":
    unittest.main()
