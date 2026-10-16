import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "artifact", ROOT / "ops" / "deploy" / "artifact.py"
)
artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifact)
COMMIT = "a" * 40
MANIFEST = {
    "format": 1,
    "commit": COMMIT,
    "upstream": {"tag": "1.10.6", "commit": "b" * 40},
    "version": "1.10.6",
    "db_version": 507,
}


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "release.tar.gz"

    def make_archive(self, extra=(), manifest=None):
        entries = [
            (".curls-release.json", json.dumps(
                MANIFEST if manifest is None else manifest
            ).encode(), tarfile.REGTYPE),
            ("includes/version.php", b"<?php\n", tarfile.REGTYPE),
            ("index.php", b"<?php\n", tarfile.REGTYPE),
        ]
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content, kind in [*entries, *extra]:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.type = kind
                info.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                archive.addfile(info, io.BytesIO(content))
        return hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_valid_archive_extracts_only_after_complete_validation(self):
        digest = self.make_archive()
        destination = self.root / "release"
        self.assertEqual(
            MANIFEST, artifact.extract(self.archive, destination, COMMIT, digest)
        )
        self.assertTrue((destination / "index.php").is_file())

    def test_rejects_paths_links_private_configuration_and_duplicates(self):
        for name, kind in [
            ("../escape", tarfile.REGTYPE),
            ("/etc/passwd", tarfile.REGTYPE),
            ("user/../escape", tarfile.REGTYPE),
            ("user\\config.php", tarfile.REGTYPE),
            ("user/config.php", tarfile.REGTYPE),
            ("frontend/config.php", tarfile.REGTYPE),
            (".git/config", tarfile.REGTYPE),
            (".github/workflows/ci.yml", tarfile.REGTYPE),
            ("tests/test.php", tarfile.REGTYPE),
            ("ops/helper.py", tarfile.REGTYPE),
            ("link", tarfile.SYMTYPE),
            ("device", tarfile.CHRTYPE),
            ("index.php", tarfile.REGTYPE),
            ("index.php/nested", tarfile.REGTYPE),
        ]:
            with self.subTest(name=name):
                digest = self.make_archive([(name, b"x", kind)])
                destination = self.root / "rejected"
                with self.assertRaises(ValueError):
                    artifact.extract(self.archive, destination, COMMIT, digest)
                self.assertFalse(destination.exists())
                self.assertFalse((self.root / "escape").exists())

    def test_rejects_digest_commit_and_upstream_version_mismatch(self):
        cases = [
            (MANIFEST, "c" * 40, None),
            (MANIFEST, COMMIT, "0" * 64),
            ({**MANIFEST, "version": "1.10.7-dev"}, COMMIT, None),
            ({**MANIFEST, "db_version": True}, COMMIT, None),
        ]
        for manifest, commit, bad_digest in cases:
            with self.subTest(manifest=manifest, commit=commit):
                digest = self.make_archive(manifest=manifest)
                with self.assertRaises(ValueError):
                    artifact.extract(
                        self.archive, self.root / "rejected", commit,
                        bad_digest or digest,
                    )
                self.assertFalse((self.root / "rejected").exists())

    def test_rejects_member_size_and_total_expansion_limits(self):
        digest = self.make_archive([("large", b"x" * 1024, tarfile.REGTYPE)])
        with self.assertRaises(ValueError):
            artifact.inspect(self.archive, COMMIT, digest, expanded_limit=1024)

    def test_gzip_expansion_is_bounded_including_nonmember_data(self):
        digest = self.make_archive()
        with patch.object(artifact, "TAR_LIMIT", 1024), self.assertRaises(ValueError):
            artifact.inspect(self.archive, COMMIT, digest)


if __name__ == "__main__":
    unittest.main()
