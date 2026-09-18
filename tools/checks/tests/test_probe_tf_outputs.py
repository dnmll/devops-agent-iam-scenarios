"""Unit tests for ${tf_output:NAME} interpolation in the probe runner.

Pure python — no AWS calls, no credentials. Only the substitution helpers in
tools/probes/run_probes.py are exercised; the boto3 call paths are CI-only.
"""

import json

import jsonschema
import pytest
import yaml

from tools.checks import REPO_ROOT, load_schema
from tools.probes.run_probes import (
    ProbeConfigError,
    resolve_tf_outputs,
    substitute,
    tf_output_values,
)

TF_OUTPUTS_JSON = json.dumps(
    {
        "installer_role_arn": {
            "sensitive": False,
            "type": "string",
            "value": "arn:aws:iam::555555555555:role/iamscn-b2-installer",
        },
        "agentspace_target_role_arn": {
            "sensitive": False,
            "type": "string",
            "value": "arn:aws:iam::555555555555:role/iamscn-b2-dar-agentspace",
        },
        "extra_role_arns": {
            "sensitive": False,
            "type": ["list", "string"],
            "value": ["arn:aws:iam::555555555555:role/one"],
        },
    }
)

TARGET_ARN = "arn:aws:iam::555555555555:role/iamscn-b2-dar-agentspace"


@pytest.fixture
def outputs() -> dict:
    return tf_output_values(json.loads(TF_OUTPUTS_JSON))


def test_tf_output_values_unwraps_terraform_json(outputs):
    assert outputs["agentspace_target_role_arn"] == TARGET_ARN
    assert outputs["extra_role_arns"] == ["arn:aws:iam::555555555555:role/one"]


def test_tf_output_values_accepts_plain_mapping():
    assert tf_output_values({"role": "arn:aws:iam::555555555555:role/r"}) == {
        "role": "arn:aws:iam::555555555555:role/r"
    }


def test_placeholder_resolved_in_nested_params(outputs):
    probe = {
        "name": "real-associate-aws-account",
        "kind": "real",
        "params": {
            "agentSpaceId": "${agent_space_id}",
            "configuration": {
                "aws": {"assumableRoleArn": "${tf_output:agentspace_target_role_arn}"}
            },
        },
    }
    resolved = resolve_tf_outputs(probe, outputs)
    assert resolved["params"]["configuration"]["aws"]["assumableRoleArn"] == TARGET_ARN
    # Non-tf_output placeholders (the run_real stash) must be left for later.
    assert resolved["params"]["agentSpaceId"] == "${agent_space_id}"


def test_placeholder_resolved_in_resource_and_lists(outputs):
    probe = {
        "resource": "${tf_output:installer_role_arn}",
        "context": {"iam:PassedToService": "aidevops.amazonaws.com"},
        "tolerate": ["${tf_output:agentspace_target_role_arn}"],
    }
    resolved = resolve_tf_outputs(probe, outputs)
    assert resolved["resource"].endswith("role/iamscn-b2-installer")
    assert resolved["context"] == {"iam:PassedToService": "aidevops.amazonaws.com"}
    assert resolved["tolerate"] == [TARGET_ARN]


def test_whole_string_placeholder_preserves_non_string_output(outputs):
    assert resolve_tf_outputs("${tf_output:extra_role_arns}", outputs) == [
        "arn:aws:iam::555555555555:role/one"
    ]


def test_embedded_placeholder_is_interpolated_as_text(outputs):
    got = resolve_tf_outputs("role=${tf_output:installer_role_arn}!", outputs)
    assert got == "role=arn:aws:iam::555555555555:role/iamscn-b2-installer!"


def test_multiple_placeholders_in_one_string(outputs):
    got = resolve_tf_outputs(
        "${tf_output:installer_role_arn}|${tf_output:agentspace_target_role_arn}", outputs
    )
    assert got == f"arn:aws:iam::555555555555:role/iamscn-b2-installer|{TARGET_ARN}"


def test_unknown_output_fails_loudly(outputs):
    with pytest.raises(ProbeConfigError) as e:
        resolve_tf_outputs({"params": {"R": "${tf_output:nope}"}}, outputs)
    assert "nope" in str(e.value)
    # The message must list what *is* available, so CI logs are actionable.
    assert "agentspace_target_role_arn" in str(e.value)


def test_probes_without_placeholders_are_untouched(outputs):
    probe = {
        "name": "sim-create-agentspace",
        "kind": "simulate",
        "action": "aidevops:CreateAgentSpace",
        "resource": "arn:aws:aidevops:us-east-1:111122223333:agentspace/probe",
        "expect": "allowed",
        "params": {"maxResults": 5, "dryRun": True, "nextToken": None},
    }
    assert resolve_tf_outputs(probe, outputs) == probe


def test_literal_substitutions_still_apply_before_tf_outputs(outputs):
    """scenario.yaml substitutions and ${tf_output:...} must compose."""
    probe = {
        "params": {
            "configuration": {
                "aws": {
                    "accountId": "111122223333",
                    "assumableRoleArn": "${tf_output:agentspace_target_role_arn}",
                }
            }
        }
    }
    subs = {"111122223333": "555555555555"}
    resolved = resolve_tf_outputs(substitute(probe, subs), outputs)
    aws_cfg = resolved["params"]["configuration"]["aws"]
    assert aws_cfg == {"accountId": "555555555555", "assumableRoleArn": TARGET_ARN}


def test_b2_probes_resolve_against_its_terraform_outputs(outputs):
    """Every ${tf_output:...} b2 references must exist in its terraform outputs."""
    probes_doc = yaml.safe_load(
        (REPO_ROOT / "scenarios" / "b2-installer" / "expected" / "probes.yaml").read_text()
    )
    jsonschema.validate(probes_doc, load_schema("probes.schema.json"))
    assert probes_doc["role_under_test"] in outputs
    resolve_tf_outputs(probes_doc["probes"], outputs)  # raises on unknown output

    # ...and it must be a *declared* output of b2's harness, not just fixture data.
    outputs_tf = (
        REPO_ROOT / "scenarios" / "b2-installer" / "terraform" / "outputs.tf"
    ).read_text()
    assert 'output "agentspace_target_role_arn"' in outputs_tf
