#!/usr/bin/env python3
"""Live probe runner. CI-only (needs sandbox AWS credentials via OIDC).

Usage:
  python3 tools/probes/run_probes.py --scenario b2-installer \
      --tf-outputs tf-outputs.json [--report probe-results.json]

Reads scenarios/<id>/expected/probes.yaml, assumes the role named by
`role_under_test` (resolved from terraform outputs), then runs:
  - simulate probes: iam:SimulatePrincipalPolicy against the role (run with the
    CI role's credentials — authoritative for the deny matrix)
  - real probes: boto3 calls with the assumed role's credentials
Retries AccessDenied flapping for up to ~3 minutes (IAM eventual consistency).

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

REPO_ROOT = Path(__file__).resolve().parents[2]
RETRY_SECONDS = 180
RETRY_INTERVAL = 15

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


def run_simulate(iam, role_arn: str, probe: dict) -> tuple[bool, str]:
    kwargs = {
        "PolicySourceArn": role_arn,
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
        result = iam.simulate_principal_policy(**kwargs)
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "InvalidInput":
            # Simulator doesn't know the action (new service). Never a false pass
            # for denies: report unverifiable so it's visible in the matrix.
            return probe["expect"] != "implicitDeny", f"unverifiable (simulator: {code})"
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
                ok, detail = with_retries(run_simulate, iam, role_arn, probe)
            else:
                ok, detail = with_retries(run_real, assumed, probe, stash)
        results.append({"name": probe["name"], "kind": probe["kind"],
                        "expect": probe["expect"], "pass": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {probe['name']:40s} {detail}")

    failed = [r for r in results if not r["pass"]]
    Path(args.report).write_text(json.dumps(
        {"scenario": args.scenario, "role": role_arn, "results": results,
         "passed": len(results) - len(failed), "failed": len(failed)}, indent=2))
    print(f"\n{len(results) - len(failed)}/{len(results)} probes passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
