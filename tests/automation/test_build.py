import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_artifact", ROOT / ".github/scripts/build_artifact.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class CommittedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Build test")
        self.git("config", "user.email", "build@example.test")
        self.git("config", "core.autocrlf", "false")
        for name, content in {
            ".github/upstream-release.json": json.dumps({
                "tag": "1.10.6", "commit": "b" * 40,
            }),
            ".gitattributes": "/.git* export-ignore\n/tests export-ignore\n",
            "includes/version.php":
                "<?php\ndefine( 'YOURLS_VERSION', '1.10.6' );\n"
                "define( 'YOURLS_DB_VERSION', '507' );\n",
            "index.php": "<?php echo 'committed';\n",
            "tests/synthetic.php": "<?php\n",
            "ops/not-public.py": "print('not public')\n",
        }.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD")

    def git(self, *args, **kwargs):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), *args], text=True, **kwargs
        ).strip()

    def test_exact_committed_tree_not_worktree_and_reproducible_bytes(self):
        self.git("config", "core.autocrlf", "true")
        (self.repo / "index.php").write_text("uncommitted change")
        (self.repo / "user").mkdir()
        (self.repo / "user/config.php").write_text("untracked private fixture")
        first, second = self.root / "first.tar.gz", self.root / "second.tar.gz"
        digest = builder.build(self.repo, self.commit, first)
        self.assertEqual(digest, builder.build(self.repo, self.commit, second))
        self.assertEqual(digest, hashlib.sha256(first.read_bytes()).hexdigest())
        with tarfile.open(first) as archive:
            self.assertEqual(
                {".curls-release.json", "includes/version.php", "index.php"},
                set(archive.getnames()),
            )
            self.assertEqual(
                b"<?php echo 'committed';\n", archive.extractfile("index.php").read()
            )
            self.assertEqual(
                self.commit, json.load(archive.extractfile(".curls-release.json"))["commit"]
            )

    def test_tracked_private_configuration_is_not_silently_packaged(self):
        (self.repo / "user").mkdir()
        (self.repo / "user/config.php").write_text("private fixture")
        self.git("add", ".")
        self.git("commit", "-qm", "unsafe tracked configuration")
        with self.assertRaises(ValueError):
            builder.build(
                self.repo, self.git("rev-parse", "HEAD"), self.root / "rejected.tar.gz"
            )
        self.assertFalse((self.root / "rejected.tar.gz").exists())

    def test_symlink_in_committed_tree_is_rejected(self):
        blob = self.git("hash-object", "-w", "--stdin", input="/etc/passwd")
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},link")
        self.git("commit", "-qm", "unsafe link")
        with self.assertRaises(ValueError):
            builder.build(
                self.repo, self.git("rev-parse", "HEAD"), self.root / "rejected.tar.gz"
            )
        self.assertFalse((self.root / "rejected.tar.gz").exists())

    def test_inspection_failure_leaves_no_output_and_can_be_retried(self):
        output = self.root / "release.tar.gz"
        with mock.patch.object(builder.artifact, "inspect", side_effect=ValueError("bad")):
            with self.assertRaisesRegex(ValueError, "bad"):
                builder.build(self.repo, self.commit, output)
        self.assertFalse(output.exists())
        self.assertEqual(["repo"], [path.name for path in self.root.iterdir()])
        builder.build(self.repo, self.commit, output)
        self.assertTrue(output.is_file())

    def test_existing_archive_is_not_replaced(self):
        output = self.root / "release.tar.gz"
        output.write_bytes(b"preserve existing artifact")
        with self.assertRaises(FileExistsError):
            builder.build(self.repo, self.commit, output)
        self.assertEqual(b"preserve existing artifact", output.read_bytes())


if __name__ == "__main__":
    unittest.main()
