"""Prove the check suite actually catches broken policies (no false greens)."""

import json
import shutil
from pathlib import Path

import pytest

from tools.checks import REPO_ROOT, Scenario, discover_scenarios
from tools.checks.checks import (
    check_manifest,
    check_policy_json,
    check_required_conditions,
)

FIXTURES = Path(__file__).parent / "fixtures"
B2 = REPO_ROOT / "scenarios" / "b2-installer"


def _b2() -> Scenario:
    return next(s for s in discover_scenarios() if s.id == "b2-installer")


def test_b2_is_clean():
    s = _b2()
    assert check_manifest(s) == []
    assert check_policy_json(s) == []
    assert check_required_conditions(s) == []


@pytest.fixture
def broken_b2(tmp_path):
    """Copy of b2 with iam:PassedToService stripped from the PassRole statement."""
    dst = tmp_path / "b2-installer"
    shutil.copytree(B2, dst)
    policy_path = dst / "policies" / "installer-policy.json"
    policy = json.loads(policy_path.read_text())
    for stmt in policy["Statement"]:
        stmt.pop("Condition", None)
    policy_path.write_text(json.dumps(policy))
    import yaml
    manifest = yaml.safe_load((dst / "scenario.yaml").read_text())
    return Scenario(path=dst, manifest=manifest)


def test_missing_passrole_condition_fails(broken_b2):
    findings = check_required_conditions(broken_b2)
    assert any("iam:PassedToService" in f.message for f in findings)
    assert any("iam:AWSServiceName" in f.message for f in findings)


def test_forbidden_action_fails(tmp_path):
    dst = tmp_path / "b2-installer"
    shutil.copytree(B2, dst)
    policy_path = dst / "policies" / "installer-policy.json"
    policy = json.loads(policy_path.read_text())
    policy["Statement"].append(
        {"Sid": "Oops", "Effect": "Allow", "Action": ["iam:*"], "Resource": "*"}
    )
    policy_path.write_text(json.dumps(policy))
    import yaml
    manifest = yaml.safe_load((dst / "scenario.yaml").read_text())
    findings = check_required_conditions(Scenario(path=dst, manifest=manifest))
    assert any("forbidden" in f.message for f in findings)


def test_manifest_schema_violation(tmp_path):
    dst = tmp_path / "b2-installer"
    shutil.copytree(B2, dst)
    (dst / "scenario.yaml").write_text("id: b2-installer\nplane: Q\n")
    import yaml
    manifest = yaml.safe_load((dst / "scenario.yaml").read_text())
    findings = check_manifest(Scenario(path=dst, manifest=manifest))
    assert findings, "invalid plane must be rejected"
