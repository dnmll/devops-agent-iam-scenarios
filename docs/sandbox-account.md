# Sandbox account runbook

The live-validation loop runs **only** in a dedicated sandbox AWS account. Never point it at any account with real workloads.

## One-time bootstrap (operator, manual)

```bash
cd terraform/bootstrap
terraform init
terraform apply -var 'github_repo=<owner>/devops-agent-iam-scenarios'
```

Creates the GitHub OIDC provider, `iamscn-ci-role` (sub pinned to `environment:sandbox`), the `iamscn-boundary` permissions boundary, and the tfstate bucket. Record the three outputs.

## GitHub configuration

1. Create Environment **`sandbox`** with a **required reviewer** (you) — this is both the human gate on every live run and the OIDC credential guard.
2. Environment variables: `AWS_ROLE_ARN`, `AWS_REGION`, `TFSTATE_BUCKET`, `BOUNDARY_ARN` (from bootstrap outputs).
3. Branch protection on `main`: require the `static` check.

## Guardrails (why the loop is safe)

- OIDC `sub` condition releases credentials only to approved `sandbox`-environment runs.
- The CI role can create roles **only** with names `iamscn-*` **and only** with `iamscn-boundary` attached; it cannot modify itself or the boundary (explicit Deny).
- The boundary caps every role under test — an over-broad candidate policy cannot exceed it.
- Everything created is tagged `iamscn:scenario` / `iamscn:run-id` / `iamscn:expiry`; `destroy` runs `if: always()`, and the nightly `sweeper` deletes aged leftovers and **fails loudly** when it finds any.

## Persistent-by-design resources (sweeper allowlist)

- `iamscn-ci-role`, `iamscn-boundary`, tfstate bucket (bootstrap)
- Service-linked roles (`AWSServiceRoleForAIDevOps`, etc.) — not deletable per-run; account-wide singletons

## PAT rotation

The ABCA fine-grained PAT (Contents + Pull requests RW on this repo) lives in Secrets Manager on the ABCA platform side. Rotate every 90 days.
