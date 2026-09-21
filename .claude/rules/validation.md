# Validation pipeline

## Static (`python3 -m tools.checks`) — runs in agent sandbox AND CI

1. `check_manifest` — schema-validate every `scenario.yaml`; artifacts/probes/terraform exist; matrix-doc row present. Also resolves the artifact references in `artifacts:` and in the probes file's `simulate_artifacts:` (see scenario-manifest.md): a `../<scenario-id>/policies/<file>.json` reference must name an existing scenario that is `status: live` and that declares the file in its own `artifacts:`, and every reference must resolve inside `scenarios/<some-scenario>/` — anything else (absolute path, climb out of `scenarios/`, `_schema/`) is reported as a manifest error.
2. `check_policy_json` — JSON validity, IAM document shape, Sid uniqueness.
3. `check_required_conditions` — manifest-driven assertions (see scenario-manifest.md); also `forbidden_actions`.
4. `check_parliament` — parliament lint; `UNKNOWN_ACTION`/`UNKNOWN_PREFIX` on `aidevops` auto-downgraded (linter doesn't know the new service — our custom checks own that prefix); everything else fails unless suppressed with a reason in `scenario.yaml`.
5. `check_hcl` — HCL2 parse of scenario + module terraform (syntax only).

Exit non-zero on any failure; per-finding lines like
`b2-installer: check_required_conditions: FAIL — iam:PassRole statement missing iam:PassedToService`.
Also writes `checks-report.json` (gitignored).

## CI-only additions (`.github/workflows/static.yml`)

`terraform fmt -check` + `terraform validate -backend=false` (terraform binary is not available in the agent sandbox).

## Live (`.github/workflows/live-validate.yml`) — CI only, human-gated

deploy (terraform apply, `iamscn-*` named/tagged) → probe (`tools/probes/run_probes.py`: real STS/aidevops calls + `iam:SimulateCustomPolicy` for the deny matrix, retries for IAM propagation) → destroy always + tag-scoped sweep → PR comment. One run at a time (`concurrency: live-sandbox`). Agents never trigger or need this locally.

Which artifacts `simulate` probes evaluate is **declared, never inferred**: the probes file's `simulate_artifacts:` list, defaulting to all of the scenario's `artifacts`. (`role_under_test` selects the role `real` probes assume and nothing else — it used to double as a filename heuristic for artifact selection, which silently narrowed the set in multi-artifact scenarios.)

CI role guardrails: `simulate` = artifact fidelity (`iam:SimulateCustomPolicy` on the declared `simulate_artifacts` with `substitutions` applied, no `iamscn-boundary` intersection, so a legitimate grant outside the boundary's union can't report a false `implicitDeny`/`explicitDeny`); `real` = sandbox-capped execution (the deployed, boundary-capped role, correct for calls with blast radius). `iamscn-ci-role` therefore needs `iam:SimulateCustomPolicy` (account-level, no resource ARN) alongside the `iamscn-*` scoped `iam:SimulatePrincipalPolicy`.
