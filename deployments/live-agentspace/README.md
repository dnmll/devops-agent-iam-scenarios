# `deployments/live-agentspace` — long-lived prerequisites for a real Agent Space

> [!WARNING]
> **This module is never applied by CI.** It is applied **by hand, by an operator**, and what it creates is meant to **survive between sessions**. No workflow in `.github/workflows/` references it, it has its own Terraform state key, and it is deliberately outside the `iamscn-` namespace so that neither `live-validate.yml`'s destroy sweep nor `sweeper.yml` can select it. If you find yourself wiring this into a workflow, stop: that is not what it is for.

## The third terraform category

| Category | Lifetime | Applied by | What it is |
|---|---|---|---|
| `terraform/bootstrap/` | long-lived | operator, by hand | sandbox plumbing: GitHub OIDC provider, `iamscn-ci-role`, `iamscn-boundary`, tfstate bucket |
| `scenarios/*/terraform/` | **ephemeral** | CI (`live-validate.yml`), destroyed every run | the IAM primitives *under test* for one scenario |
| **`deployments/live-agentspace/`** | long-lived | operator, by hand | prerequisites for a real, human-usable Agent Space |

The first two exist to *validate* policies. This one exists to **use** them: it stands up the roles and the key that a real Agent Space needs, so a human can then create the Agent Space with the CLI and actually operate it.

## What it creates

IAM, KMS and one CloudWatch Logs group — nothing else. The Agent Space itself is created with the AWS CLI, per **decision D1** (`docs/decisions.md`): there is no verified `awscc`/Cloud Control coverage for `aidevops` resource types. A sibling docs task owns that runbook; this module only produces its inputs.

| Resource | Shape | Source of the statements |
|---|---|---|
| Agent Space role (`DevOpsAgentRole-agentspace`) | trust policy verbatim (`aidevops.amazonaws.com` + `aws:SourceAccount` + `ArnLike aws:SourceArn` on `agentspace/*`), `AIDevOpsAgentAccessPolicy` attached by ARN, Resource Explorer SLR inline | [`a1`](../../scenarios/a1-agentspace-role/) |
| Operator Web App role (`DevOpsAgentRole-operator-app`) | trust policy verbatim with **both** `sts:AssumeRole` and `sts:TagSession`, `AIDevOpsOperatorAppAccessPolicy` attached by ARN, **plus** the CMK caller grants inline | [`a4`](../../scenarios/a4-operator-webapp-role/) + [`b5`](../../scenarios/b5-customer-kms-key/) |
| Installer role (`DevOpsAgentRole-installer`) | three inline policies (one per source artifact), trusted by `var.operator_principal_arn` | [`b2`](../../scenarios/b2-installer/) + [`b5`](../../scenarios/b5-customer-kms-key/) caller + [`b7`](../../scenarios/b7-log-delivery/) CloudWatch-Logs target |
| Customer-managed key + alias | symmetric, `SYMMETRIC_DEFAULT`, `ENCRYPT_DECRYPT`, rotation on | [`b5`](../../scenarios/b5-customer-kms-key/) key policy **+ an administration statement** (see constraint 3) |
| Log group `/aws/vendedlogs/devops-agent/<name>` + account-level delivery resource policy | vended-log delivery destination | [`b7`](../../scenarios/b7-log-delivery/) |

Every policy body is `file()`-read from the scenario artifacts with only the documented placeholders substituted (account id, Region, b5's example key ARN). **Nothing in this module invents an IAM action or condition key** — if a statement needs to change, change the scenario and let `python3 -m tools.checks` gate it.

### Why the installer role is trusted by *you*

The deployment must be performed **by the least-privilege role, not by Admin**. That is the whole exercise: if a documented step fails while running as `DevOpsAgentRole-installer`, that is a real finding about the `b2`/`b5`/`b7` artifacts. Fix the scenario and re-apply — never widen this role in place, and never fall back to admin credentials to "get past" a step.

```bash
CREDS=$(aws sts assume-role \
  --role-arn "$(terraform output -raw installer_role_arn)" \
  --role-session-name install --output json)
export AWS_ACCESS_KEY_ID=$(jq -r .Credentials.AccessKeyId <<<"$CREDS")
export AWS_SECRET_ACCESS_KEY=$(jq -r .Credentials.SecretAccessKey <<<"$CREDS")
export AWS_SESSION_TOKEN=$(jq -r .Credentials.SessionToken <<<"$CREDS")
aws sts get-caller-identity   # should say DevOpsAgentRole-installer
```

## Three constraints that will break a live deployment if missed

### 1. Do **not** use the `iamscn-` name prefix

`tools/probes/sweeper.py` deletes IAM roles **and Agent Spaces** whose name starts with `iamscn-` and which are older than six hours, and `sweeper.yml` runs it on a schedule. A live deployment under that prefix would be destroyed overnight, mid-use.

`var.name_prefix` defaults to `DevOpsAgentRole-` — the convention from the AWS docs — and a variable `validation` block rejects any value starting with `iamscn-`. The same guard applies to `var.agentspace_name`, because the sweeper matches Agent Space names too. `tools/checks/tests/test_deployments_sweeper_guard.py` asserts statically that nothing in this directory can produce an `iamscn-*` name. **Sweeper-safe by construction, not by exemption list** — an exemption list is one forgotten entry away from deleting production.

Tags follow the same rule: everything here is tagged `devops-agent:deployment = live-agentspace`, never `iamscn:*`, so the tag-scoped cleanup in `live-validate.yml` cannot match it either.

### 2. Do **not** attach `iamscn-boundary`

That permissions boundary exists to cap the blast radius of policies **under test**: it allows `aidevops:*` plus a few narrow IAM reads. A real Agent Space role capped by it **applies successfully and then fails every investigation**, because `AIDevOpsAgentAccessPolicy` needs broad describe/read access across many services (EC2, Lambda, RDS, CloudWatch, X-Ray, …) and the boundary intersects all of it away. The failure surfaces later, as empty or erroring investigations, not as a terraform error — which is exactly why it is worth a written warning.

There is no `permissions_boundary` argument anywhere in this module, and none should be added. If you want to cap these roles, write a boundary **for this deployment** whose union actually covers `AIDevOpsAgentAccessPolicy`; do not reuse the test one.

### 3. The CMK key policy **must** include a key-administration statement

AWS's documented example key policy mentions `AllowKeyAdministration` in prose and then **omits it from the JSON**, and `b5` faithfully inherited that omission (`b5`'s README says key administration "belongs to the key owner's existing statement" and is not part of the deliverable). In a static artifact that is harmless. On a **real key it is a one-way door**: KMS authorizes `kms:PutKeyPolicy` from the key policy itself, so a key whose policy grants administration to nobody can **never be modified again** — no policy fix, no grant management, no rotation change, no tagging, and no deletion. IAM permissions alone cannot recover it.

`kms.tf` therefore adds `AllowKeyAdministration` for the **account root** (plus any `var.key_administrator_arns`), alongside the caller statement and the two service-principal statements. Root is the deliberate default: a statement naming only a role is one deleted role away from the same dead end.

The rest of the key policy is `b5` verbatim in intent — caller statement fenced with `kms:ViaService: aidevops.<region>.amazonaws.com` naming **both** the installer and Operator Web App roles, the unconditioned service-principal `DescribeKey` for configuration-time validation, and **two** service-principal crypto statements (`agentspace/*` and `service/*`) each correlating `aws:SourceArn` with `kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn`. Do not collapse those two into one statement with a list: the conditions would then be satisfiable independently, letting an Agent-Space-sourced request carry a *service* encryption context.

## Apply

State lives at `deployments/live-agentspace/terraform.tfstate` in the same bucket `terraform/bootstrap/` created — a **separate key** from bootstrap and from every scenario, so no `terraform destroy` elsewhere can reach these resources.

```bash
cd deployments/live-agentspace

terraform init \
  -backend-config="bucket=iamscn-tfstate-<account-id>" \
  -backend-config="region=us-east-1"

terraform plan \
  -var="account_id=<account-id>" \
  -var="operator_principal_arn=$(aws sts get-caller-identity --query Arn --output text)"

terraform apply <same -var flags>
terraform output          # feeds the CLI runbook
```

Apply with **your own** (admin-ish) credentials — creating roles and a key is IAM administration, which is `b1`'s persona, not the installer's. Then assume the installer role for the runbook itself (above).

`var.account_id` is cross-checked against the caller identity in a `precondition`, so a mis-targeted profile fails at plan time rather than creating half a deployment in the wrong account.

### Outputs the runbook consumes

`agentspace_role_arn`, `operator_app_role_arn`, `installer_role_arn`, `kms_key_arn`, `log_group_arn`, `log_group_name`. Do not rename them.

## Things to know before you apply

- **The CMK is creation-time only.** `--kms-key-arn` takes the **full key ARN** (not an alias, not a key id) and cannot be added to or changed on an existing Agent Space. Getting it wrong means creating a new Agent Space.
- **Destroying is not free.** `terraform destroy` schedules the key for deletion with `var.kms_deletion_window_days` (default 30) — and a deleted key means **permanent loss** of the Agent Space data it protected, because DevOps Agent does not re-encrypt under a new key. Delete the Agent Space first.
- **The delivery resource policy is account-wide.** CloudWatch Logs has one resource-policy document per account per Region per name. Read `aws logs describe-resource-policies` first; set `manage_delivery_resource_policy = false` if your account manages those documents centrally.
- **Changing `name_prefix` away from `DevOpsAgentRole-` breaks `iam:PassRole`.** b2's `PassAgentSpaceRoles` statement is scoped to `role/DevOpsAgentRole-*`; a different prefix means the installer cannot pass the Agent Space role and `create-agent-space` fails with `AccessDenied`. If you must rename, change b2's resource scope first (and re-run `python3 -m tools.checks`) rather than widening it here.
- **This module creates no `aidevops` resources.** No Agent Space, no associations, no Operator App enablement — all CLI, all in the runbook.

## Sources

The IAM/KMS content is not new: see the scenario READMEs for the citations — [`a1`](../../scenarios/a1-agentspace-role/README.md), [`a4`](../../scenarios/a4-operator-webapp-role/README.md), [`b2`](../../scenarios/b2-installer/README.md), [`b5`](../../scenarios/b5-customer-kms-key/README.md), [`b7`](../../scenarios/b7-log-delivery/README.md).
