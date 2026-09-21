"""Artifact reference resolution and the check_manifest rules built on it.

Cross-scenario references (`../<scenario-id>/policies/<file>.json`) let a
composite scenario point at a parent scenario's deliverable instead of copying
it. That is only safe if the reference cannot silently rot, so the tests here
pin the four ways it can go wrong: unknown scenario, not-yet-live scenario,
missing file, and a path that escapes the scenarios/ tree.

Every fixture is built under tmp_path — a real broken scenario in scenarios/
would (correctly) fail the repo's own `python3 -m tools.checks`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pytest
import yaml

from tools.artifacts import ArtifactRefError, resolve_artifact_ref
from tools.checks import REPO_ROOT, Scenario, discover_scenarios, load_schema
from tools.checks.checks import check_manifest, check_policy_json, check_required_conditions

SCENARIOS = REPO_ROOT / "scenarios"
B2 = SCENARIOS / "b2-installer"


# ------------------------------------------------------------------- resolution


def test_own_scenario_reference_resolves_inside_the_scenario():
    ref = resolve_artifact_ref("policies/installer-policy.json", B2, SCENARIOS)
    assert ref.path == B2 / "policies" / "installer-policy.json"
    assert ref.scenario_id == "b2-installer"
    assert not ref.is_cross_scenario


def test_cross_scenario_reference_resolves_to_the_other_scenario():
    ref = resolve_artifact_ref(
        "../b2-installer/policies/installer-policy.json", SCENARIOS / "b9-composite", SCENARIOS
    )
    assert ref.path == B2 / "policies" / "installer-policy.json"
    assert ref.scenario_id == "b2-installer"
    assert ref.is_cross_scenario
    # The reference is reported as written — that is what a reader can grep for.
    assert ref.ref == "../b2-installer/policies/installer-policy.json"


@pytest.mark.parametrize("bad_ref", [
    "../../terraform/bootstrap/main.tf",          # climbs out of scenarios/
    "../../README.md",                            # ditto, repo root
    "../../../etc/passwd",                        # climbs out of the repo
    "/etc/passwd",                                # absolute
    "~/policies/x.json",                          # home-relative
    "../_schema/probes.schema.json",              # in-tree but not a scenario
    "..",                                         # scenarios/ itself
])
def test_references_outside_a_scenario_are_rejected(bad_ref):
    with pytest.raises(ArtifactRefError) as e:
        resolve_artifact_ref(bad_ref, B2, SCENARIOS)
    assert bad_ref in str(e.value)


def test_empty_reference_is_rejected():
    with pytest.raises(ArtifactRefError):
        resolve_artifact_ref("", B2, SCENARIOS)


# ----------------------------------------------------------------- check_manifest


@pytest.fixture
def composite(tmp_path):
    """A `scenarios/` copy holding b2 plus an empty composite scenario dir.

    Returns a factory: `composite(artifacts=[...], status="b2 status")` writes the
    composite's scenario.yaml and hands back the Scenario to check.
    """
    root = tmp_path / "scenarios"
    root.mkdir()
    shutil.copytree(SCENARIOS / "_schema", root / "_schema")
    shutil.copytree(B2, root / "b2-installer")
    comp_dir = root / "b9-composite"
    (comp_dir / "policies").mkdir(parents=True)
    (comp_dir / "terraform").mkdir()
    (comp_dir / "terraform" / "main.tf").write_text('output "x" { value = "y" }\n')
    (comp_dir / "expected").mkdir()
    (comp_dir / "policies" / "own-policy.json").write_text(json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Sid": "Own", "Effect": "Allow",
                       "Action": "aidevops:ListAgentSpaces", "Resource": "*"}],
    }))

    def make(artifacts, parent_status="live", simulate_artifacts=None, status="draft"):
        (root / "b2-installer" / "scenario.yaml").write_text(_with_status(
            SCENARIOS / "b2-installer" / "scenario.yaml", parent_status))
        manifest = {
            "id": "b9-composite",
            "plane": "B",
            "title": "Composite fixture",
            "description": "Fixture for cross-scenario artifact references.",
            "status": status,
            "docs": ["https://docs.aws.amazon.com/devopsagent/latest/userguide/"],
            "artifacts": artifacts,
        }
        if status in ("static", "live"):
            manifest["probes"] = "expected/probes.yaml"
            probes: dict = {"role_under_test": "x", "probes": [
                {"name": "p", "kind": "simulate", "action": "aidevops:ListAgentSpaces",
                 "expect": "allowed"}]}
            if simulate_artifacts is not None:
                probes["simulate_artifacts"] = simulate_artifacts
            (comp_dir / "expected" / "probes.yaml").write_text(yaml.safe_dump(probes))
        (comp_dir / "scenario.yaml").write_text(yaml.safe_dump(manifest))
        return Scenario(path=comp_dir, manifest=manifest)

    return make


def _with_status(manifest_path: Path, status: str) -> str:
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["status"] = status
    return yaml.safe_dump(manifest)


def _messages(findings) -> str:
    return "\n".join(f.message for f in findings)


def test_cross_scenario_reference_to_a_live_scenario_is_accepted(composite):
    s = composite(["policies/own-policy.json",
                   "../b2-installer/policies/installer-policy.json"])
    # The matrix-doc row check is the repo's, not the fixture's — ignore it here.
    findings = [f for f in check_manifest(s) if "scenario-matrix" not in f.message]
    assert findings == [], _messages(findings)


def test_cross_scenario_reference_to_a_missing_scenario_fails(composite):
    s = composite(["../b0-does-not-exist/policies/whatever.json"])
    findings = check_manifest(s)
    assert "unknown scenario 'b0-does-not-exist'" in _messages(findings)


def test_cross_scenario_reference_to_a_non_live_scenario_fails(composite):
    s = composite(["../b2-installer/policies/installer-policy.json"], parent_status="static")
    findings = check_manifest(s)
    msgs = _messages(findings)
    assert "status=static" in msgs
    assert "only a live scenario may be referenced" in msgs


def test_cross_scenario_reference_to_a_missing_file_fails(composite):
    s = composite(["../b2-installer/policies/not-there.json"])
    assert "artifact missing" in _messages(check_manifest(s))


def test_cross_scenario_reference_the_parent_does_not_declare_fails(composite):
    """Referencing a file the owner does not list in its own `artifacts:` is a
    copy by another name — nothing keeps it in sync with a deliverable."""
    s = composite(["../b2-installer/README.md"])
    assert "not declared in b2-installer/scenario.yaml artifacts" in _messages(check_manifest(s))


def test_reference_outside_the_scenarios_tree_is_a_manifest_error(composite):
    s = composite(["../../terraform/bootstrap/main.tf"])
    msgs = _messages(check_manifest(s))
    assert "outside the scenarios/ tree" in msgs
    assert "artifacts:" in msgs  # says which field it came from


def test_simulate_artifacts_references_are_validated_too(composite):
    s = composite(
        ["policies/own-policy.json"],
        simulate_artifacts=["../b0-does-not-exist/policies/x.json"],
        status="static",
    )
    msgs = _messages(check_manifest(s))
    assert "simulate_artifacts" in msgs
    assert "unknown scenario 'b0-does-not-exist'" in msgs


def test_simulate_artifacts_may_be_omitted(composite):
    s = composite(["policies/own-policy.json"], status="static")
    findings = [f for f in check_manifest(s) if "scenario-matrix" not in f.message]
    assert findings == [], _messages(findings)


# --------------------------------------- downstream checks follow the references


def test_policy_and_condition_checks_read_the_referenced_artifact(composite):
    """A cross-scenario reference is a real artifact for every check, not just
    an existence assertion: b2's PassRole conditions are asserted from here."""
    s = composite(["../b2-installer/policies/installer-policy.json"])
    s.manifest["required_conditions"] = [{
        "artifact": "../b2-installer/policies/installer-policy.json",
        "action": "iam:PassRole",
        "condition_key": "iam:PassedToService",
        "expected": "aidevops.amazonaws.com",
    }]
    assert check_policy_json(s) == []
    findings = check_required_conditions(s)
    assert findings == [], _messages(findings)


def test_required_conditions_on_an_undeclared_artifact_still_fails(composite):
    s = composite(["policies/own-policy.json"])
    s.manifest["required_conditions"] = [{
        "artifact": "../b2-installer/policies/installer-policy.json",
        "action": "iam:PassRole",
        "condition_key": "iam:PassedToService",
        "expected": "aidevops.amazonaws.com",
    }]
    assert "unknown artifact" in _messages(check_required_conditions(s))


# ------------------------------------------------------------- real scenarios


def test_no_shipped_scenario_uses_an_unresolvable_reference():
    for s in discover_scenarios():
        _, errors = s.artifact_refs()
        assert errors == [], f"{s.id}: {errors}"


def test_every_shipped_probes_file_is_schema_valid():
    """`simulate_artifacts` was added to probes.schema.json — nothing regressed."""
    schema = load_schema("probes.schema.json")
    for probes_path in sorted(SCENARIOS.glob("*/expected/*.yaml")):
        jsonschema.validate(yaml.safe_load(probes_path.read_text()), schema)


@pytest.mark.parametrize("bad", [[], "policies/a.json", [1]])
def test_probes_schema_rejects_a_malformed_simulate_artifacts(bad):
    doc = {
        "role_under_test": "x",
        "simulate_artifacts": bad,
        "probes": [{"name": "p", "kind": "simulate", "action": "a", "expect": "allowed"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, load_schema("probes.schema.json"))
