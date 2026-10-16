import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release", ROOT / ".github" / "scripts" / "release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseSelectionTests(unittest.TestCase):
    def test_ignores_drafts_prereleases_and_nonrelease_tags(self):
        releases = [
            {"tag_name": "1.10.7-rc1", "draft": False, "prerelease": True},
            {"tag_name": "1.10.8", "draft": True, "prerelease": False},
            {"tag_name": "main", "draft": False, "prerelease": False},
            {"tag_name": "1.10.6", "draft": False, "prerelease": False},
            {"tag_name": "1.9.9", "draft": False, "prerelease": False},
        ]
        self.assertEqual("1.10.6", release.select_stable(releases))

    def test_rejects_empty_feed_and_duplicate_versions(self):
        with self.assertRaises(ValueError):
            release.select_stable([])
        with self.assertRaises(ValueError):
            release.select_stable([
                {"tag_name": tag, "draft": False, "prerelease": False}
                for tag in ["1.10.6", "1.10.6"]
            ])

    def test_rejects_version_rollback_and_retagging(self):
        current = {"tag": "1.10.6", "commit": "a" * 40}
        release.check_transition(current, "1.10.6", "a" * 40)
        release.check_transition(current, "1.11.0", "b" * 40)
        for tag, commit in [("1.9.9", "b" * 40), ("1.10.6", "b" * 40)]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.check_transition(current, tag, commit)

    def test_handles_upstream_two_component_release_versions(self):
        self.assertEqual("1.11", release.select_stable([
            {"tag_name": tag, "draft": False, "prerelease": False}
            for tag in ["1.10.6", "1.11"]
        ]))
        with self.assertRaises(ValueError):
            release.check_transition(
                {"tag": "1.11", "commit": "a" * 40}, "1.11.0", "b" * 40
            )


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release@example.test")
        (self.repo / ".github").mkdir()
        self.pin = self.repo / ".github" / "upstream-release.json"
        (self.repo / "core.php").write_text("stable\n")
        self.git("add", ".")
        self.git("commit", "-qm", "stable")
        self.stable = self.git("rev-parse", "HEAD")
        self.git("tag", "1.10.6")
        self.pin.write_text(json.dumps({"tag": "1.10.6", "commit": self.stable}))
        (self.repo / "frontend.php").write_text("custom\n")
        self.git("add", ".")
        self.git("commit", "-qm", "custom")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), *args], text=True
        ).strip()

    def test_merge_keeps_customizations_and_pins_exact_release(self):
        self.git("checkout", "-q", "--detach", self.stable)
        (self.repo / "core.php").write_text("new stable\n")
        self.git("add", ".")
        self.git("commit", "-qm", "new stable")
        newer = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.base)
        result = release.prepare_candidate(self.repo, "1.10.7", newer)
        self.assertEqual("custom\n", (self.repo / "frontend.php").read_text())
        self.assertEqual("new stable\n", (self.repo / "core.php").read_text())
        self.assertEqual(newer, json.loads(self.pin.read_text())["commit"])
        self.assertEqual(result, self.git("rev-parse", "HEAD"))
        self.assertEqual("", self.git("status", "--porcelain"))

    def test_conflicting_merge_does_not_create_success_commit(self):
        self.git("checkout", "-q", "--detach", self.stable)
        (self.repo / "core.php").write_text("upstream changed\n")
        self.git("add", ".")
        self.git("commit", "-qm", "upstream")
        newer = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.base)
        (self.repo / "core.php").write_text("fork changed\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fork")
        before = self.git("rev-parse", "HEAD")
        with self.assertRaises(subprocess.CalledProcessError):
            release.prepare_candidate(self.repo, "1.10.7", newer)
        self.assertEqual(before, self.git("rev-parse", "HEAD"))

    def test_automation_changes_are_rejected_before_merging(self):
        for path in [".github/workflows/injected.yml", ".github/scripts/task.py",
                     "ops/deploy/deploy.py", ".gitmodules"]:
            with self.subTest(path=path):
                self.git("checkout", "-q", "--detach", self.stable)
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("unreviewed upstream automation\n")
                self.git("add", ".")
                self.git("commit", "-qm", "upstream automation change")
                newer = self.git("rev-parse", "HEAD")
                self.git("checkout", "-q", "--detach", self.base)
                with self.assertRaisesRegex(ValueError, "review separately"):
                    release.prepare_candidate(self.repo, "1.10.7", newer)
                self.assertEqual(self.base, self.git("rev-parse", "HEAD"))
                self.assertFalse(target.exists())
                self.assertEqual("", self.git("status", "--porcelain"))

    def test_same_release_is_noop_except_explicit_heartbeat(self):
        self.assertEqual(
            self.base, release.prepare_candidate(self.repo, "1.10.6", self.stable)
        )


if __name__ == "__main__":
    unittest.main()
