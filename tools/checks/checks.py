"""Individual check implementations. Each returns list[Finding]."""

from __future__ import annotations

import json
from pathlib import Path

import hcl2
import jsonschema
import yaml

from . import ALWAYS_FORBIDDEN, MATRIX_DOC, REPO_ROOT, Finding, Scenario, load_schema

# Parliament doesn't know the aidevops service yet; our manifest-driven checks
# own correctness for that prefix, so unknown-action noise there is downgraded.
PARLIAMENT_DOWNGRADED_PREFIXES = ("aidevops",)
PARLIAMENT_DOWNGRADABLE_ISSUES = {"UNKNOWN_ACTION", "UNKNOWN_PREFIX"}


def _statements(policy: dict) -> list[dict]:
    stmts = policy.get("Statement", [])
    return stmts if isinstance(stmts, list) else [stmts]


def _actions(stmt: dict) -> list[str]:
    actions = stmt.get("Action", [])
    return actions if isinstance(actions, list) else [actions]


def check_manifest(s: Scenario) -> list[Finding]:
    f: list[Finding] = []
    if not (s.path / "scenario.yaml").is_file():
        return [Finding(s.id, "check_manifest", "scenario.yaml missing")]
    try:
        jsonschema.validate(s.manifest, load_schema("scenario.schema.json"))
    except jsonschema.ValidationError as e:
        return [Finding(s.id, "check_manifest", f"scenario.yaml schema: {e.message}")]
    if s.manifest["id"] != s.path.name:
        f.append(Finding(s.id, "check_manifest", f"id '{s.manifest['id']}' != directory '{s.path.name}'"))
    for a in s.artifact_paths():
        if not a.is_file():
            f.append(Finding(s.id, "check_manifest", f"artifact missing: {a.relative_to(s.path)}"))
    status = s.manifest.get("status", "planned")
    if status in ("static", "live"):
        tf_dir = s.path / s.manifest.get("terraform_dir", "terraform")
        if not tf_dir.is_dir():
            f.append(Finding(s.id, "check_manifest", f"status={status} requires terraform_dir {tf_dir.name}/"))
        probes_rel = s.manifest.get("probes")
        if not probes_rel:
            f.append(Finding(s.id, "check_manifest", f"status={status} requires a probes file"))
        else:
            probes_path = s.path / probes_rel
            if not probes_path.is_file():
                f.append(Finding(s.id, "check_manifest", f"probes file missing: {probes_rel}"))
            else:
                try:
                    jsonschema.validate(
                        yaml.safe_load(probes_path.read_text()), load_schema("probes.schema.json")
                    )
                except jsonschema.ValidationError as e:
                    f.append(Finding(s.id, "check_manifest", f"probes schema: {e.message}"))
    if MATRIX_DOC.is_file():
        if f"`{s.id}`" not in MATRIX_DOC.read_text():
            f.append(Finding(s.id, "check_manifest", f"no row for `{s.id}` in docs/scenario-matrix.md"))
    else:
        f.append(Finding(s.id, "check_manifest", "docs/scenario-matrix.md missing"))
    return f


def check_policy_json(s: Scenario) -> list[Finding]:
    f: list[Finding] = []
    for path in s.artifact_paths():
        rel = path.relative_to(s.path)
        if not path.is_file():
            continue  # reported by check_manifest
        try:
            policy = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            f.append(Finding(s.id, "check_policy_json", f"{rel}: invalid JSON: {e}"))
            continue
        if policy.get("Version") != "2012-10-17":
            f.append(Finding(s.id, "check_policy_json", f"{rel}: Version must be 2012-10-17"))
        stmts = _statements(policy)
        if not stmts:
            f.append(Finding(s.id, "check_policy_json", f"{rel}: no Statement"))
        sids = [st.get("Sid") for st in stmts]
        if None in sids:
            f.append(Finding(s.id, "check_policy_json", f"{rel}: every statement needs a Sid"))
        if len([x for x in sids if x]) != len(set(x for x in sids if x)):
            f.append(Finding(s.id, "check_policy_json", f"{rel}: duplicate Sid"))
        for st in stmts:
            if "Effect" not in st or ("Action" not in st and "NotAction" not in st):
                f.append(Finding(s.id, "check_policy_json", f"{rel}: statement {st.get('Sid')} missing Effect/Action"))
    return f


def check_required_conditions(s: Scenario) -> list[Finding]:
    f: list[Finding] = []
    policies = s.load_policies()
    by_rel = {str(p.relative_to(s.path)): pol for p, pol in policies.items()}

    for rule in s.manifest.get("required_conditions", []):
        pol = by_rel.get(rule["artifact"])
        if pol is None:
            f.append(Finding(s.id, "check_required_conditions", f"unknown artifact {rule['artifact']}"))
            continue
        matched = [st for st in _statements(pol) if rule["action"] in _actions(st) and st.get("Effect") == "Allow"]
        if not matched:
            f.append(Finding(s.id, "check_required_conditions",
                             f"{rule['artifact']}: no Allow statement grants {rule['action']}"))
            continue
        for st in matched:
            found = None
            for op_values in st.get("Condition", {}).values():
                for key, val in op_values.items():
                    if key.lower() == rule["condition_key"].lower():
                        found = val if isinstance(val, list) else [val]
            if found is None:
                f.append(Finding(s.id, "check_required_conditions",
                                 f"{rule['artifact']}: statement {st.get('Sid')} grants {rule['action']} "
                                 f"without condition {rule['condition_key']}"))
            elif rule["expected"] not in found:
                f.append(Finding(s.id, "check_required_conditions",
                                 f"{rule['artifact']}: {st.get('Sid')} {rule['condition_key']}={found} "
                                 f"!= expected {rule['expected']}"))

    forbidden = set(ALWAYS_FORBIDDEN) | set(s.manifest.get("forbidden_actions", []))
    for rel, pol in by_rel.items():
        for st in _statements(pol):
            if st.get("Effect") != "Allow":
                continue
            hits = forbidden.intersection(_actions(st))
            if hits:
                f.append(Finding(s.id, "check_required_conditions",
                                 f"{rel}: statement {st.get('Sid')} grants forbidden action(s) {sorted(hits)}"))
    return f


def check_parliament(s: Scenario) -> list[Finding]:
    import parliament

    f: list[Finding] = []
    suppressions = {sup["issue"]: sup["reason"] for sup in s.manifest.get("parliament_suppressions", [])}
    for path, _ in s.load_policies().items():
        rel = path.relative_to(s.path)
        analyzed = parliament.analyze_policy_string(path.read_text())
        for finding in analyzed.findings:
            issue = finding.issue
            detail = str(finding.detail)
            if issue in PARLIAMENT_DOWNGRADABLE_ISSUES and any(
                p in detail for p in PARLIAMENT_DOWNGRADED_PREFIXES
            ):
                continue
            if issue in suppressions:
                continue
            f.append(Finding(s.id, "check_parliament", f"{rel}: {issue} — {detail}"))
    return f


def check_hcl(s: Scenario) -> list[Finding]:
    f: list[Finding] = []
    tf_dirs = [s.path / s.manifest.get("terraform_dir", "terraform")]
    for tf_dir in tf_dirs:
        if not tf_dir.is_dir():
            continue
        for tf in sorted(tf_dir.glob("*.tf")):
            try:
                with tf.open() as fh:
                    hcl2.load(fh)
            except Exception as e:
                f.append(Finding(s.id, "check_hcl", f"{tf.relative_to(s.path)}: HCL parse error: {e}"))
    return f


def check_shared_modules() -> list[Finding]:
    """Repo-level: parse shared terraform (modules + bootstrap) once."""
    f: list[Finding] = []
    for tf in sorted((REPO_ROOT / "terraform").rglob("*.tf")):
        try:
            with tf.open() as fh:
                hcl2.load(fh)
        except Exception as e:
            f.append(Finding("(repo)", "check_hcl", f"{tf.relative_to(REPO_ROOT)}: HCL parse error: {e}"))
    return f


SCENARIO_CHECKS = [
    check_manifest,
    check_policy_json,
    check_required_conditions,
    check_parliament,
    check_hcl,
]
