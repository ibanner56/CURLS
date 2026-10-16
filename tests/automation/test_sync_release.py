import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
SPEC = importlib.util.spec_from_file_location(
    "sync_release", ROOT / ".github/scripts/sync_release.py"
)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class ReleaseStartupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        scripts = self.root / ".github/scripts"
        scripts.mkdir(parents=True)
        for name in ("sync_release.py", "release.py"):
            shutil.copyfile(ROOT / ".github/scripts" / name, scripts / name)
        (self.root / ".github/upstream-release.json").write_text(json.dumps({
            "tag": "1.10.6", "commit": "a" * 40,
        }))
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("-c", "user.name=Release test", "-c",
                 "user.email=release@example.test", "commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True
        ).strip()

    def prepare(self):
        environment = os.environ.copy()
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)
        return subprocess.run(
            [sys.executable, "-c", """
import runpy
import sys
from pathlib import Path
script = Path(sys.argv[1])
sys.path.insert(0, str(script.parent))
module = runpy.run_path(str(script))
print(module["prepare_candidate"](script.parents[2], "1.10.6", "a" * 40))
""", str(self.root / ".github/scripts/sync_release.py")],
            env=environment, capture_output=True, text=True,
        )

    def test_startup_preserves_clean_checkout_for_candidate_preparation(self):
        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.base, result.stdout.strip())
        self.assertEqual("", self.git("status", "--porcelain"))

    def test_startup_does_not_hide_untracked_or_tracked_changes(self):
        for relative in ("unexpected.txt", ".github/upstream-release.json"):
            with self.subTest(path=relative):
                path = self.root / relative
                original = path.read_text() if path.exists() else None
                path.write_text((original or "") + "\n")
                result = self.prepare()
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Candidate workspace must be clean", result.stderr)
                self.assertEqual(self.base, self.git("rev-parse", "HEAD"))
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original)


class ReleaseOrchestrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".github").mkdir()
        self.base, self.upstream, self.candidate = "a" * 40, "b" * 40, "c" * 40
        (self.root / ".github/upstream-release.json").write_text(json.dumps({
            "tag": "1.10.6", "commit": self.base,
        }))
        (self.root / ".github/release-heartbeat").write_text("2099-01\n")
        for patcher in [
            mock.patch.object(sync, "ROOT", self.root),
            mock.patch.object(sync, "git", side_effect=self.git),
            mock.patch.object(sync, "api", side_effect=self.api),
            mock.patch.object(sync, "prepare_candidate", return_value=self.candidate),
            mock.patch.object(sync, "datetime"),
            mock.patch.dict(os.environ, {
                "GITHUB_EVENT_NAME": "schedule", "GITHUB_RUN_ID": "123",
                "GITHUB_RUN_ATTEMPT": "1", "BOOTSTRAP_BASELINE": "",
                "GITHUB_OUTPUT": str(self.root / "outputs"),
                "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            }),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sys.modules["release"], "git", sync.git)
        patcher.start()
        self.addCleanup(patcher.stop)
        sync.datetime.now.return_value.strftime.return_value = "2099-01"

    def git(self, root, *args):
        if args == ("rev-parse", "FETCH_HEAD^{commit}"):
            return self.upstream
        if args == ("rev-parse", "HEAD"):
            return self.base
        return ""

    def api(self, endpoint, *args):
        if "/releases?" in endpoint:
            return [[{"tag_name": "1.10.7", "draft": False, "prerelease": False}]]
        return {"object": {"sha": self.base}}

    def pushes(self):
        return [call.args[1:] for call in sync.git.call_args_list
                if call.args[1] == "push"]

    def test_preparation_guards_before_mirroring_and_emits_tested_baseline(self):
        sync.prepare()
        comparison = next(index for index, call in enumerate(sync.git.call_args_list)
                          if call.args[1] == "diff")
        first_push = next(index for index, call in enumerate(sync.git.call_args_list)
                          if call.args[1] == "push")
        self.assertLess(comparison, first_push)
        self.assertEqual([
            ("push", "origin", f"--force-with-lease=refs/heads/master:{self.base}",
             f"{self.upstream}:refs/heads/master"),
            ("push", "origin",
             f"{self.candidate}:refs/heads/automation/stable-1.10.7-123-1"),
        ], self.pushes())
        self.assertIn(f"baseline={self.base}\n", (self.root / "outputs").read_text())
        self.assertIn(f"- candidate: `{self.candidate}`\n", (self.root / "summary").read_text())

    def test_automation_change_stops_before_any_remote_push(self):
        def unsafe(root, *args):
            return ".github/workflows/new.yml" if args[0] == "diff" else self.git(root, *args)
        sync.git.side_effect = unsafe
        with self.assertRaisesRegex(ValueError, "trusted automation"):
            sync.prepare()
        self.assertEqual([], self.pushes())
        sync.prepare_candidate.assert_not_called()
        self.assertFalse((self.root / "outputs").exists())

    def test_concurrent_control_branch_change_stops_before_mirror(self):
        sync.api.side_effect = lambda endpoint, *args: (
            self.api(endpoint, *args) if "/releases?" in endpoint
            else {"object": {"sha": "d" * 40}}
        )
        with self.assertRaisesRegex(ValueError, "curls moved"):
            sync.prepare()
        self.assertEqual([], self.pushes())

    def test_scheduled_noop_and_manual_bootstrap(self):
        sync.prepare_candidate.return_value = self.base
        sync.prepare()
        self.assertIn("changed=false\n", (self.root / "outputs").read_text())
        self.assertEqual(1, len(self.pushes()))
        sync.git.reset_mock()
        with mock.patch.dict(os.environ, {
            "GITHUB_EVENT_NAME": "workflow_dispatch", "BOOTSTRAP_BASELINE": "d" * 40,
        }):
            sync.prepare()
        self.assertEqual(2, len(self.pushes()))
        outputs = (self.root / "outputs").read_text()
        self.assertIn("changed=true\n", outputs)
        self.assertIn(f"baseline={'d' * 40}\n", outputs)

    def test_promotion_never_forces_and_propagates_push_failure(self):
        sync.promote(self.candidate)
        self.assertEqual([
            ("push", "origin", f"{self.candidate}:refs/heads/curls"),
        ], self.pushes())
        def reject(root, *args):
            if args[0] == "push":
                raise subprocess.CalledProcessError(1, "git push")
            return ""
        sync.git.side_effect = reject
        with self.assertRaises(subprocess.CalledProcessError):
            sync.promote(self.candidate)
        sync.git.reset_mock()
        with self.assertRaises(ValueError):
            sync.promote("invalid")
        sync.git.assert_not_called()

    def test_failure_report_creates_issue_or_comments_on_existing(self):
        title = "Stable release automation needs attention"
        for issues, suffix in [
            ([], "/issues"), ([{"title": title, "number": 17}], "/issues/17/comments"),
        ]:
            with self.subTest(existing=bool(issues)):
                sync.api.return_value = [issues]
                sync.api.side_effect = None
                with mock.patch.object(sync.subprocess, "run") as post:
                    sync.report()
                args, kwargs = post.call_args
                self.assertTrue(args[0][2].endswith(suffix))
                payload = json.loads(kwargs["input"])
                self.assertIn("@ibanner56", payload["body"])
                self.assertIn("/actions/runs/123", payload["body"])
                self.assertEqual(not issues, "title" in payload)
                self.assertTrue(kwargs["check"])


if __name__ == "__main__":
    unittest.main()
