import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("state", ROOT / "ops/deploy/state.py")
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class Backend:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.public = True
        self.workers = True
        self.new_code = False
        self.recorded = False

    def call(self, name):
        self.calls.append(name)
        if name == self.failure or isinstance(self.failure, set) and name in self.failure:
            raise RuntimeError(name)

    def preflight(self):
        self.call("preflight")

    def maintenance(self, enabled):
        self.public = not enabled
        self.call("close" if enabled else "open")

    def stop_workers(self):
        self.workers = False
        self.call("stop")

    def start_workers(self):
        self.workers = True
        self.call("start")

    def snapshot(self):
        self.call("snapshot")
        return {"links": 92, "logs": 407743}

    def backup(self):
        self.call("backup")

    def switch(self):
        self.new_code = True
        self.call("switch")

    def restore_code(self):
        self.new_code = False
        self.call("restore-code")

    def upgrade(self):
        self.call("upgrade")

    def verify_snapshot(self, before):
        self.call("preserve-data")

    def probe(self):
        self.call("probe")

    def record(self):
        self.recorded = True
        self.call("record")


class DeploymentPolicyTests(unittest.TestCase):
    def test_success_reopens_only_after_backup_upgrade_probe_and_record(self):
        backend = Backend()
        state.activate(backend)
        self.assertTrue(backend.public and backend.workers and backend.recorded)
        self.assertLess(backend.calls.index("backup"), backend.calls.index("upgrade"))
        self.assertEqual(["probe", "record", "open"], backend.calls[-3:])

    def test_preflight_failure_never_changes_live_state(self):
        backend = Backend("preflight")
        with self.assertRaises(RuntimeError):
            state.activate(backend)
        self.assertEqual(["preflight"], backend.calls)
        self.assertTrue(backend.public and backend.workers)

    def test_failure_before_database_work_restores_code_and_service(self):
        for failure in ["snapshot", "backup", "switch"]:
            with self.subTest(failure=failure):
                backend = Backend(failure)
                with self.assertRaises(RuntimeError):
                    state.activate(backend)
                self.assertTrue(backend.public and backend.workers)
                self.assertFalse(backend.new_code or backend.recorded)

    def test_any_failure_after_database_entry_keeps_writers_closed(self):
        for failure in ["upgrade", "preserve-data", "probe", "record", "open"]:
            with self.subTest(failure=failure):
                backend = Backend(failure)
                with self.assertRaises(RuntimeError):
                    state.activate(backend)
                self.assertFalse(backend.public or backend.workers)
                self.assertNotIn("restore-code", backend.calls)

    def test_failed_pre_database_recovery_also_fails_closed(self):
        backend = Backend({"backup", "start"})
        with self.assertRaises(RuntimeError):
            state.activate(backend)
        self.assertFalse(backend.public or backend.workers)


if __name__ == "__main__":
    unittest.main()
