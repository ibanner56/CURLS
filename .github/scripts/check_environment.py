"""Fail closed if the production environment has lost its approval rule."""

import json
import subprocess


def check(environment, branches):
    reviewers = [
        reviewer for rule in environment.get("protection_rules", [])
        if rule.get("type") == "required_reviewers"
        for reviewer in rule.get("reviewers", [])
    ]
    if (len(reviewers) != 1 or reviewers[0].get("type") != "User"
            or reviewers[0].get("reviewer", {}).get("login") != "ibanner56"):
        raise ValueError("Production requires ibanner56's explicit deployment approval")
    policy = environment.get("deployment_branch_policy") or {}
    if policy != {"protected_branches": False, "custom_branch_policies": True}:
        raise ValueError("Production must restrict deployment to the curls branch")
    policies = branches.get("branch_policies", [])
    if (len(policies) != 1 or policies[0].get("name") != "curls"
            or policies[0].get("type", "branch") != "branch"):
        raise ValueError("Unexpected production deployment branch policy")


if __name__ == "__main__":
    endpoint = "repos/ibanner56/CURLS/environments/satsuma-production"
    def api(path):
        return json.loads(subprocess.check_output(["gh", "api", path], text=True))
    check(api(endpoint), api(endpoint + "/deployment-branch-policies"))
