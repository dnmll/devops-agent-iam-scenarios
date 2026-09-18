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

## CI-only additions (`.github/workflows/static.yml`)

`terraform fmt -check` + `terraform validate -backend=false` (terraform binary is not available in the agent sandbox).

## Live (`.github/workflows/live-validate.yml`) — CI only, human-gated

deploy (terraform apply, `iamscn-*` named/tagged) → probe (`tools/probes/run_probes.py`: real STS/aidevops calls + `iam:SimulatePrincipalPolicy` for the deny matrix, retries for IAM propagation) → destroy always + tag-scoped sweep → PR comment. One run at a time (`concurrency: live-sandbox`). Agents never trigger or need this locally.
