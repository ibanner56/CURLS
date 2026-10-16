import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_environment", ROOT / ".github/scripts/check_environment.py"
)
environment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(environment)


class ApprovalGuardTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "protection_rules": [{
                "type": "required_reviewers",
                "reviewers": [{"type": "User", "reviewer": {"login": "ibanner56"}}],
            }],
            "deployment_branch_policy": {
                "protected_branches": False, "custom_branch_policies": True,
            },
        }
        self.branches = {"branch_policies": [{"name": "curls", "type": "branch"}]}

    def test_accepts_the_single_owner_and_control_branch(self):
        environment.check(self.settings, self.branches)

    def test_rejects_missing_reviewers_or_extra_approvers(self):
        missing = copy.deepcopy(self.settings)
        missing["protection_rules"] = []
        extra = copy.deepcopy(self.settings)
        extra["protection_rules"][0]["reviewers"].append({
            "type": "User", "reviewer": {"login": "somebody-else"},
        })
        for settings in (missing, extra, {}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                environment.check(settings, self.branches)

    def test_rejects_expanded_branch_or_tag_access(self):
        for policies in [
            [{"name": "*", "type": "branch"}],
            [{"name": "curls", "type": "tag"}],
            [{"name": "curls"}, {"name": "master"}],
        ]:
            with self.subTest(policies=policies), self.assertRaises(ValueError):
                environment.check(self.settings, {"branch_policies": policies})


if __name__ == "__main__":
    unittest.main()
