import importlib.util
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
