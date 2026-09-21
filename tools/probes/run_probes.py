#!/usr/bin/env python3
"""Live probe runner. CI-only (needs sandbox AWS credentials via OIDC).

Usage:
  python3 tools/probes/run_probes.py --scenario b2-installer \
      --tf-outputs tf-outputs.json [--report probe-results.json]

Reads scenarios/<id>/expected/probes.yaml, assumes the role named by
`role_under_test` (resolved from terraform outputs), then runs:
  - simulate probes: iam:SimulateCustomPolicy against the policy ARTIFACT(s) the
    probes file declares in `simulate_artifacts:` (default: all of scenario.yaml
    `artifacts`) — run with the CI role's credentials, authoritative for the
    deny matrix
  - real probes: boto3 calls with the assumed role's credentials
Retries AccessDenied flapping for up to ~3 minutes (IAM eventual consistency).

Why SimulateCustomPolicy and not SimulatePrincipalPolicy: deployed scenario
roles carry the `iamscn-boundary` permissions boundary (a sandbox safety cap).
SimulatePrincipalPolicy evaluates policy ∩ boundary, so any legitimate grant
outside the boundary's union reports a false implicitDeny (or an explicitDeny
from the boundary's anti-tamper statement). Customers do not run these policies
under our boundary, so the artifact-in-isolation verdict is the correct fidelity
for `simulate`. `real` probes keep using the deployed, boundary-capped role —
those calls have blast radius and the cap is exactly what we want there.

Probe fields (`params`, `resource`, `context`, ...) may reference any terraform
output with a `${tf_output:NAME}` placeholder; it is resolved from the
--tf-outputs file before the probe runs. `role_under_test` stays a bare output
name for backwards compatibility.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import boto3
import botocore.exceptions
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.artifacts import ArtifactRefError, resolve_artifact_ref  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRY_SECONDS = 180
RETRY_INTERVAL = 15

# Simulate expectations that assert absence of a grant: an unverifiable probe
# must never pass for these (that would be a false "it is denied").
DENY_EXPECTATIONS = frozenset({"implicitDeny", "explicitDeny"})

# ${tf_output:NAME} — NAME is a terraform output name (HCL identifier rules).
TF_OUTPUT_RE = re.compile(r"\$\{tf_output:([A-Za-z_][A-Za-z0-9_-]*)\}")


class ProbeConfigError(RuntimeError):
    """probes.yaml references something the run can't resolve — fail loudly."""


def tf_output_values(tf_outputs: dict) -> dict:
    """Flatten `terraform output -json` ({name: {value, sensitive, type}}).

    Values are returned as-is (a terraform output may be a list or object);
    plain {name: value} maps are accepted too so callers can pass either shape.
    """
    flat = {}
    for name, body in tf_outputs.items():
        flat[name] = body["value"] if isinstance(body, dict) and "value" in body else body
    return flat


def resolve_tf_outputs(value, outputs: dict):
    """Replace every ${tf_output:NAME} placeholder in a probe (recursively).

    A string that is *only* a placeholder resolves to the raw output value, so
    non-string terraform outputs (lists, numbers, objects) survive intact;
    placeholders embedded in a larger string are interpolated as text.
    """
    if isinstance(value, str):
        whole = TF_OUTPUT_RE.fullmatch(value)
        if whole:
            return _lookup(whole.group(1), outputs)
        return TF_OUTPUT_RE.sub(lambda m: str(_lookup(m.group(1), outputs)), value)
    if isinstance(value, dict):
        return {k: resolve_tf_outputs(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_tf_outputs(v, outputs) for v in value]
    return value


def _lookup(name: str, outputs: dict):
    if name not in outputs:
        known = ", ".join(sorted(outputs)) or "(none)"
        raise ProbeConfigError(
            f"probe references unknown terraform output '{name}' — declared outputs: {known}"
        )
    return outputs[name]


def load_expectations(scenario: str) -> tuple[dict, dict]:
    sdir = REPO_ROOT / "scenarios" / scenario
    manifest = yaml.safe_load((sdir / "scenario.yaml").read_text())
    probes = yaml.safe_load((sdir / manifest["probes"]).read_text())
    return manifest, probes


def substitute(value, subs: dict[str, str]):
    if isinstance(value, str):
        for old, new in subs.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {k: substitute(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, subs) for v in value]
    return value


def simulated_artifacts(manifest: dict, probes_doc: dict) -> list[str]:
    """Which artifacts the simulate probes evaluate against — declared, never inferred.

    A probes file states its own PolicyInputList in `simulate_artifacts:`. That is
    how a probes file targeting one identity tier of a multi-tier scenario (b3:
    admin / operator / read-only) or excluding a non-simulatable trust / resource
    policy (a1-a4's trust policies, b5's KMS key policy) says so: simulating the
    union of independent tiers would grant the other tiers' actions and turn every
    deny expectation into a false pass.

    Absent `simulate_artifacts`, the default is *all* of the scenario's
    `artifacts` — a customer attaches a scenario's policies together, and a
    composite scenario's grants are spread across them, so narrowing the set by
    guessing would report real grants as false implicitDenies.
    """
    declared = probes_doc.get("simulate_artifacts")
    if declared:
        return list(declared)
    return list(manifest.get("artifacts", []))


def load_policy_inputs(scenario: str, artifacts: list[str], subs: dict[str, str]) -> list[str]:
    """Artifact JSON text with scenario substitutions applied, for PolicyInputList.

    The raw docs-style artifact is what the customer gets; the substitutions pass
    (placeholder account id / region / role-name prefix) is the same one
    terraform and the probe bodies use, so simulated ARNs line up with the
    sandbox-real resources the probes name.

    References are resolved relative to the scenario directory and may point at
    another scenario's artifact (`../b2-installer/policies/installer-policy.json`);
    `check_manifest` has already asserted the referenced scenario is `status:
    live` and declares the artifact, so a bad reference is a static failure, not
    a surprise mid-run.
    """
    sdir = REPO_ROOT / "scenarios" / scenario
    inputs = []
    for artifact in artifacts:
        try:
            path = resolve_artifact_ref(artifact, sdir).path
        except ArtifactRefError as e:
            raise ProbeConfigError(f"artifact reference '{artifact}': {e}") from None
        if not path.is_file():
            raise ProbeConfigError(f"artifact '{artifact}' not found at {path}")
        # Re-serialize compactly: PolicyInputList entries are size-capped and the
        # docs-style artifacts are heavily commented-out-by-formatting whitespace.
        inputs.append(json.dumps(json.loads(substitute(path.read_text(), subs))))
    if not inputs:
        raise ProbeConfigError(f"scenario '{scenario}' declares no artifacts to simulate")
    return inputs


def run_simulate(iam, policy_inputs: list[str], probe: dict) -> tuple[bool, str]:
    kwargs = {
        "PolicyInputList": list(policy_inputs),
        "ActionNames": [probe["action"]],
    }
    if probe.get("resource") and probe["resource"] != "*":
        kwargs["ResourceArns"] = [probe["resource"]]
    if probe.get("context"):
        kwargs["ContextEntries"] = [
            {"ContextKeyName": k, "ContextKeyValues": [v], "ContextKeyType": "string"}
            for k, v in probe["context"].items()
        ]
    try:
        result = iam.simulate_custom_policy(**kwargs)
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "InvalidInput":
            # Simulator doesn't know the action (new service). Never a false pass
            # for denies: report unverifiable so it's visible in the matrix.
            message = e.response["Error"].get("Message", "")[:200]
            return (
                probe["expect"] not in DENY_EXPECTATIONS,
                f"unverifiable (simulator: {code}: {message})" if message
                else f"unverifiable (simulator: {code})",
            )
        raise
    decision = result["EvaluationResults"][0]["EvalDecision"]
    return decision == probe["expect"], f"decision={decision}"


def run_real(session: boto3.Session, probe: dict, stash: dict) -> tuple[bool, str]:
    try:
        client = session.client(probe["service"])
    except botocore.exceptions.UnknownServiceError:
        return False, f"unverifiable: boto3 has no '{probe['service']}' model — upgrade boto3 or mark probe simulate-only"
    params = substitute(probe.get("params", {}), stash)
    try:
        response = getattr(client, probe["operation"])(**params)
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code in probe.get("tolerate", []):
            return True, f"tolerated {code}"
        if probe["expect"] == f"error:{code}":
            return True, f"expected error {code}"
        return False, f"error {code}: {e.response['Error'].get('Message', '')[:200]}"
    if probe.get("save_output_as"):
        alias, _, path = probe["save_output_as"].partition("=")
        value = response
        for part in path.split("."):
            value = value[part]
        stash[f"${{{alias}}}"] = value
    return probe["expect"] == "success", "ok"


def with_retries(fn, *args) -> tuple[bool, str]:
    deadline = time.time() + RETRY_SECONDS
    while True:
        ok, detail = fn(*args)
        # Retry only denials/failures that IAM propagation could still fix.
        if ok or time.time() > deadline or "unverifiable" in detail:
            return ok, detail
        time.sleep(RETRY_INTERVAL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--tf-outputs", required=True, help="terraform output -json file")
    ap.add_argument("--report", default="probe-results.json")
    args = ap.parse_args()

    manifest, probes_doc = load_expectations(args.scenario)
    outputs = tf_output_values(json.loads(Path(args.tf_outputs).read_text()))
    role_arn = _lookup(probes_doc["role_under_test"], outputs)

    base = boto3.Session()
    account_id = base.client("sts").get_caller_identity()["Account"]
    region = base.region_name or "us-east-1"
    subs = {
        old: new.replace("${account_id}", account_id).replace("${region}", region)
        for old, new in manifest.get("substitutions", {}).items()
    }

    creds = base.client("sts").assume_role(
        RoleArn=role_arn, RoleSessionName=f"iamscn-probe-{args.scenario}"
    )["Credentials"]
    assumed = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )

    iam = base.client("iam")
    artifacts = simulated_artifacts(manifest, probes_doc)
    policy_inputs = load_policy_inputs(args.scenario, artifacts, subs)
    print(f"simulate probes evaluate: {', '.join(artifacts)} (artifact fidelity, no boundary)")
    stash: dict[str, str] = {}
    results = []
    for probe in probes_doc["probes"]:
        # Literal substitutions first (they apply to the docs-style placeholders),
        # then ${tf_output:...} — terraform values are already sandbox-real.
        probe = substitute(probe, subs)
        try:
            probe = resolve_tf_outputs(probe, outputs)
        except ProbeConfigError as e:
            # A bad output reference is a misconfiguration, not a policy verdict:
            # record it as a visible failure instead of aborting the whole matrix.
            ok, detail = False, f"config error: {e}"
        else:
            if probe["kind"] == "simulate":
                ok, detail = with_retries(run_simulate, iam, policy_inputs, probe)
            else:
                ok, detail = with_retries(run_real, assumed, probe, stash)
        results.append({"name": probe["name"], "kind": probe["kind"],
                        "expect": probe["expect"], "pass": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {probe['name']:40s} {detail}")

    failed = [r for r in results if not r["pass"]]
    Path(args.report).write_text(json.dumps(
        {"scenario": args.scenario, "role": role_arn,
         "simulated_artifacts": artifacts, "results": results,
         "passed": len(results) - len(failed), "failed": len(failed)}, indent=2))
    print(f"\n{len(results) - len(failed)}/{len(results)} probes passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
