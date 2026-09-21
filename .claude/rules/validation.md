# Validation pipeline

## Static (`python3 -m tools.checks`) — runs in agent sandbox AND CI

1. `check_manifest` — schema-validate every `scenario.yaml`; artifacts/probes/terraform exist; matrix-doc row present.
2. `check_policy_json` — JSON validity, IAM document shape, Sid uniqueness.
3. `check_required_conditions` — manifest-driven assertions (see scenario-manifest.md); also `forbidden_actions`.
4. `check_parliament` — parliament lint; `UNKNOWN_ACTION`/`UNKNOWN_PREFIX` on `aidevops` auto-downgraded (linter doesn't know the new service — our custom checks own that prefix); everything else fails unless suppressed with a reason in `scenario.yaml`.
5. `check_hcl` — HCL2 parse of scenario + module terraform (syntax only).

Exit non-zero on any failure; per-finding lines like
`b2-installer: check_required_conditions: FAIL — iam:PassRole statement missing iam:PassedToService`.
Also writes `checks-report.json` (gitignored).

## Three terraform categories

| Path | Lifetime | Applied by | Naming / tags | Boundary |
|---|---|---|---|---|
| `terraform/bootstrap/` | long-lived | operator, by hand | `iamscn-*` (`iamscn-ci-role`, `iamscn-boundary`, tfstate bucket) | n/a |
| `scenarios/*/terraform/` | **ephemeral** — destroyed every live-validate run | CI | `iamscn-*` names + `iamscn:scenario`/`iamscn:run-id` tags | `iamscn-boundary` **mandatory** |
| `deployments/*/` | long-lived | operator, by hand — **never CI** | `DevOpsAgentRole-*` names + `devops-agent:*` tags; `iamscn-` is **forbidden** | **none** |

The first two validate policies; `deployments/` **uses** them to stand up real, human-usable infrastructure whose prerequisites survive between sessions.

Two hard rules follow from the sweeper and the boundary, both enforced statically by `tools/checks/tests/test_deployments_sweeper_guard.py`:

- **No `iamscn-` names or `iamscn:` tags under `deployments/`.** `tools/probes/sweeper.py` deletes `iamscn-*` IAM roles *and* Agent Spaces older than 6 hours (scheduled by `sweeper.yml`), and live-validate's destroy step sweeps by `iamscn:*` tags. A live deployment in either namespace is destroyed overnight. Sweeper-safe by construction, not by exemption list. The `DevOpsAgentRole-` default also matches `b2`'s `iam:PassRole` resource scope.
- **No `permissions_boundary` under `deployments/`.** `iamscn-boundary` caps policies *under test* (`aidevops:*` + narrow IAM reads); a real Agent Space role capped by it applies cleanly and then fails every investigation, because `AIDevOpsAgentAccessPolicy` needs broad describe/read across many services.

Each deployment also owns its state key (`deployments/<name>/terraform.tfstate`), so no `terraform destroy` in bootstrap or a scenario can reach it.

Deployments have no `scenario.yaml` and no probes: `discover_scenarios()` only walks `scenarios/`, so `python3 -m tools.checks` does not see them and `check_hcl`/`check_shared_modules` do not parse them. Their coverage is instead: the pytest guard file above (which HCL2-parses every `deployments/**/*.tf`, so a syntax error fails there) plus `terraform fmt -check -recursive` in `static.yml`, which is repo-wide. The `terraform validate` loop in `static.yml` still iterates only `scenarios/*/terraform` and `terraform/bootstrap` — extending it to `deployments/*` is a known follow-up.

## CI-only additions (`.github/workflows/static.yml`)

`terraform fmt -check` + `terraform validate -backend=false` (terraform binary is not available in the agent sandbox).

## Live (`.github/workflows/live-validate.yml`) — CI only, human-gated

deploy (terraform apply, `iamscn-*` named/tagged) → probe (`tools/probes/run_probes.py`: real STS/aidevops calls + `iam:SimulateCustomPolicy` for the deny matrix, retries for IAM propagation) → destroy always + tag-scoped sweep → PR comment. One run at a time (`concurrency: live-sandbox`). Agents never trigger or need this locally.

CI role guardrails: `simulate` = artifact fidelity (`iam:SimulateCustomPolicy` on the scenario's `artifacts` with `substitutions` applied, no `iamscn-boundary` intersection, so a legitimate grant outside the boundary's union can't report a false `implicitDeny`/`explicitDeny`); `real` = sandbox-capped execution (the deployed, boundary-capped role, correct for calls with blast radius). `iamscn-ci-role` therefore needs `iam:SimulateCustomPolicy` (account-level, no resource ARN) alongside the `iamscn-*` scoped `iam:SimulatePrincipalPolicy`.
