"""Unit tests for the SimulateCustomPolicy simulate path in the probe runner.

Pure python — no AWS calls, no credentials: the boto3 client is replaced by a
stub that records kwargs (or raises a ClientError) so the request composition
and the unverifiable handling are both testable in the agent sandbox.
"""

import json

import botocore.exceptions
import pytest

from tools.checks import REPO_ROOT, discover_scenarios
from tools.probes.run_probes import (
    ProbeConfigError,
    load_policy_inputs,
    run_simulate,
    simulated_artifacts,
)

ACCOUNT = "555555555555"
REGION = "eu-west-1"
B2_SUBS = {
    "111122223333": ACCOUNT,
    "us-east-1": REGION,
    "DevOpsAgentRole-": "iamscn-b2-dar-",
}


class FakeIam:
    """Records the last simulate_custom_policy kwargs; optionally raises."""

    def __init__(self, decision="allowed", error_code=None, message=""):
        self.decision = decision
        self.error_code = error_code
        self.message = message
        self.calls = []

    def simulate_custom_policy(self, **kwargs):
        self.calls.append(kwargs)
        if self.error_code:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": self.error_code, "Message": self.message}},
                "SimulateCustomPolicy",
            )
        return {"EvaluationResults": [{"EvalDecision": self.decision}]}

    def simulate_principal_policy(self, **kwargs):  # pragma: no cover - must not be used
        raise AssertionError("simulate probes must not use SimulatePrincipalPolicy")


# ---------------------------------------------------------------- artifact load


def test_policy_inputs_apply_substitutions():
    (policy,) = load_policy_inputs("b2-installer", ["policies/installer-policy.json"], B2_SUBS)
    doc = json.loads(policy)
    assert doc["Version"] == "2012-10-17"
    text = json.dumps(doc)
    # Docs-style placeholders must be gone; sandbox-real values in their place.
    assert "111122223333" not in text
    assert "DevOpsAgentRole-" not in text
    assert ACCOUNT in text
    assert "iamscn-b2-dar-" in text


def test_policy_inputs_are_valid_json_for_every_artifact():
    """All artifacts of a scenario compose into one PolicyInputList."""
    scenario = next(s for s in discover_scenarios() if s.id == "b3-webapp-tiers")
    artifacts = scenario.manifest["artifacts"]
    inputs = load_policy_inputs(
        "b3-webapp-tiers", artifacts, {"111122223333": ACCOUNT, "us-east-1": REGION}
    )
    assert len(inputs) == len(artifacts) == 3
    for policy in inputs:
        assert json.loads(policy)["Statement"]


def test_policy_inputs_missing_artifact_fails_loudly():
    with pytest.raises(ProbeConfigError) as e:
        load_policy_inputs("b2-installer", ["policies/nope.json"], {})
    assert "nope.json" in str(e.value)


def test_policy_inputs_empty_artifact_list_fails_loudly():
    with pytest.raises(ProbeConfigError):
        load_policy_inputs("b2-installer", [], {})


# ------------------------------------------------------------ artifact selection


def test_single_artifact_scenario_simulates_that_artifact():
    manifest = {"artifacts": ["policies/installer-policy.json"]}
    probes_doc = {"role_under_test": "installer_role_arn"}
    assert simulated_artifacts(manifest, probes_doc) == ["policies/installer-policy.json"]


@pytest.mark.parametrize(
    "role_output,expected",
    [
        ("operator_role_arn", "policies/operator-policy.json"),
        ("admin_role_arn", "policies/admin-policy.json"),
        ("readonly_role_arn", "policies/readonly-policy.json"),
    ],
)
def test_tiered_scenario_simulates_only_the_tier_under_test(role_output, expected):
    """b3's tiers are independent policies: simulating the union would turn
    every deny expectation into a false pass."""
    scenario = next(s for s in discover_scenarios() if s.id == "b3-webapp-tiers")
    assert simulated_artifacts(scenario.manifest, {"role_under_test": role_output}) == [expected]


def test_multi_artifact_scenario_without_tier_match_uses_all_artifacts():
    """A customer attaches a scenario's policies together — that's the default."""
    manifest = {"artifacts": ["policies/a.json", "policies/b.json"]}
    got = simulated_artifacts(manifest, {"role_under_test": "installer_role_arn"})
    assert got == ["policies/a.json", "policies/b.json"]


# ------------------------------------------------------------------ simulate call


def test_run_simulate_passes_policy_input_list_not_a_principal():
    iam = FakeIam(decision="allowed")
    policy_inputs = load_policy_inputs(
        "b2-installer", ["policies/installer-policy.json"], B2_SUBS
    )
    probe = {
        "name": "sim-passrole",
        "kind": "simulate",
        "action": "iam:PassRole",
        "resource": f"arn:aws:iam::{ACCOUNT}:role/iamscn-b2-dar-agentspace",
        "context": {"iam:PassedToService": "aidevops.amazonaws.com"},
        "expect": "allowed",
    }
    ok, detail = run_simulate(iam, policy_inputs, probe)
    assert ok and detail == "decision=allowed"
    (kwargs,) = iam.calls
    assert kwargs["PolicyInputList"] == policy_inputs
    assert "PolicySourceArn" not in kwargs  # no boundary-capped role in the picture
    assert kwargs["ActionNames"] == ["iam:PassRole"]
    assert kwargs["ResourceArns"] == [probe["resource"]]
    assert kwargs["ContextEntries"] == [
        {
            "ContextKeyName": "iam:PassedToService",
            "ContextKeyValues": ["aidevops.amazonaws.com"],
            "ContextKeyType": "string",
        }
    ]


def test_run_simulate_omits_resource_arns_for_star_and_context_when_absent():
    iam = FakeIam(decision="implicitDeny")
    probe = {
        "name": "sim-list-asset-types",
        "kind": "simulate",
        "action": "aidevops:ListAssetTypes",
        "resource": "*",
        "expect": "implicitDeny",
    }
    ok, _ = run_simulate(iam, ["{}"], probe)
    assert ok
    (kwargs,) = iam.calls
    assert "ResourceArns" not in kwargs
    assert "ContextEntries" not in kwargs


def test_run_simulate_reports_decision_mismatch():
    iam = FakeIam(decision="implicitDeny")
    probe = {"name": "p", "kind": "simulate", "action": "aidevops:CreateChat", "expect": "allowed"}
    ok, detail = run_simulate(iam, ["{}"], probe)
    assert not ok and detail == "decision=implicitDeny"


def test_run_simulate_matches_explicit_deny_expectation():
    iam = FakeIam(decision="explicitDeny")
    probe = {"name": "p", "kind": "simulate", "action": "aidevops:TagResource",
             "expect": "explicitDeny"}
    ok, detail = run_simulate(iam, ["{}"], probe)
    assert ok and detail == "decision=explicitDeny"


@pytest.mark.parametrize("expect,should_pass", [
    ("allowed", True),
    ("implicitDeny", False),
    ("explicitDeny", False),
])
def test_invalid_input_is_unverifiable_never_a_false_deny(expect, should_pass):
    iam = FakeIam(error_code="InvalidInput", message="Invalid Action: aidevops:CreateChat")
    probe = {"name": "p", "kind": "simulate", "action": "aidevops:CreateChat", "expect": expect}
    ok, detail = run_simulate(iam, ["{}"], probe)
    assert ok is should_pass
    assert "unverifiable" in detail and "InvalidInput" in detail
    # `with_retries` keys off the word "unverifiable" to stop retrying.
    assert "unverifiable" in detail


def test_other_client_errors_propagate():
    iam = FakeIam(error_code="AccessDenied", message="ci role lacks SimulateCustomPolicy")
    probe = {"name": "p", "kind": "simulate", "action": "aidevops:CreateChat",
             "expect": "allowed"}
    with pytest.raises(botocore.exceptions.ClientError):
        run_simulate(iam, ["{}"], probe)


# --------------------------------------------------------------------- contract


def test_runner_no_longer_calls_simulate_principal_policy():
    source = (REPO_ROOT / "tools" / "probes" / "run_probes.py").read_text()
    assert "simulate_principal_policy" not in source
    assert "simulate_custom_policy" in source
