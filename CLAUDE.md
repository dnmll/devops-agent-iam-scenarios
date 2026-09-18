# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this repo is

Validated AWS IAM permission scenarios for the **AWS DevOps Agent** service (`aidevops`). Each scenario is a customer-facing IAM policy artifact plus machine-checked validation metadata. Scenarios are statically checked in-sandbox and live-validated by GitHub Actions (deploy → probe → destroy in a dedicated sandbox AWS account). **Agents never run AWS CLI/API calls — live validation is CI's job.**

## Commands

```bash
python3 -m pip install -r tools/requirements.txt
python3 -m tools.checks            # static validation — MUST pass before any commit
python3 -m pytest tools/checks/tests   # unit tests for the checks themselves
```

`mise run build` runs the same static checks. There is no compile step.

## How to add or modify a scenario

1. Every scenario lives in `scenarios/<id>/` and is driven entirely by data files — **do not add per-scenario Python**:
   - `scenario.yaml` — the contract (validated against `scenarios/_schema/scenario.schema.json`): artifacts, `required_conditions`, `forbidden_actions`, parliament suppressions (each needs a `reason`), substitutions, terraform dir.
   - `policies/*.json` — the raw, docs-style customer deliverable. Account IDs use the placeholder `111122223333`; the harness substitutes real values via `scenario.yaml` `substitutions`.
   - `expected/probes.yaml` — allow/deny expectations (`kind: simulate|real`), validated against `probes.schema.json`.
   - `terraform/` — harness that provisions **IAM primitives only** (roles/policies under test). The `aidevops` service is exercised by probes, never by terraform.
   - `README.md` — customer-facing explanation.
2. Add/keep the scenario's row in `docs/scenario-matrix.md` (checked by `check_manifest`).
3. Run `python3 -m tools.checks` until green.

## Hard rules

- **Never invent IAM actions or condition keys.** Every `aidevops:*` action must be traceable to the AWS DevOps Agent documentation; cite the doc page in the scenario README.
- **Never weaken a policy to make a check pass.** Fix the metadata or flag the conflict in the PR instead.
- Do not edit `terraform/bootstrap/`, `.github/workflows/`, or `tools/checks/` core logic unless the task explicitly asks for it.
- All harness resource names start with `iamscn-`. Never rename the permissions boundary or CI role.
- Parliament suppressions require a written `reason` and belong in `scenario.yaml`, never inline config edits.

See `.claude/rules/` for policy-authoring conventions, the manifest contract, and validation details.
