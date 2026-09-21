# AWS DevOps Agent IAM scenario matrix

Status: `planned` → `draft` (artifacts sketched) → `static` (checks green) → `live` (passed a live-validate run).

## Plane A — roles the DevOps Agent service assumes

| Id | Title | Deliverable | Status |
|---|---|---|---|
| `a1-agentspace-role` | Agent Space role (primary account) | Trust policy (`aidevops.amazonaws.com` + SourceAccount/SourceArn) + `AIDevOpsAgentAccessPolicy` + Resource Explorer SLR inline; restricted-template variant | live |
| `a2-secondary-account-role` | Secondary account role (cross-account) | Same permission shape as a1, trust scoped to the Agent Space in the **monitoring** account (`aws:SourceAccount`/`aws:SourceArn` = `222233334444`) while the SLR inline stays in the member account (`111122223333`); no cross-account principal, no ExternalId | live |
| `a3-elevated-directed-actions` | Elevated role for directed actions | Trust policy with all three STS actions (`AssumeRole` + `SetSourceIdentity` + `TagSession`) + SourceAccount/SourceArn; worked **template** ceiling for operator-approved remediation writes (tag-gated `ec2:RebootInstances` / `lambda:UpdateFunctionConfiguration`) that customers narrow per runbook | live |
| `a4-operator-webapp-role` | Operator Web App role | `AIDevOpsOperatorAppAccessPolicy`-derived, session-tag (`AgentSpaceId`) scoped: `agentspace/${aws:PrincipalTag/AgentSpaceId}` + `aws:ResourceAccount=${aws:PrincipalAccount}` on every statement; trust policy adds `sts:TagSession`. Allow half is static-only — policy variables don't resolve under `iam:SimulateCustomPolicy` | live |
| `a5-service-linked-roles` | Service-linked roles | `AWSServiceRoleForAIDevOps` (vended metrics, `AWS/AIDevOps` namespace) + `AWSServiceRoleForLogDelivery` (Firehose logs) creation guidance; one `iam:CreateServiceLinkedRole` artifact per role, each pinning its own SLR ARN to its own `iam:AWSServiceName`, plus the never-delete-these cleanup rules | live |

## Plane B — human / CI identities

| Id | Title | Deliverable | Status |
|---|---|---|---|
| `b1-iam-preprovisioner` | IAM admin pre-provisioner (split duty) | Role/policy CRUD scoped to `DevOpsAgentRole-*`, attachment pinned by `iam:PolicyARN`; no PassRole, no aidevops | live |
| `b2-installer` | Installer — create/configure an Agent Space | aidevops lifecycle + associations + Operator App + scoped PassRole + SLR creation | live |
| `b3-webapp-tiers` | Web App / console user tiers | Admin / Operator / Read-only policies (+ `CreateOneTimeLoginSession` for Web App login) | live |
| `b4-secrets-manager` | Third-party integration via Secrets Manager | secretsmanager Create/Put/Describe/List/Tag scoped to a `devops-agent/*` name prefix; no `GetSecretValue`/`DeleteSecret` (the service reads, the installer only writes) | live |
| `b5-customer-kms-key` | Customer-managed KMS key | Caller policy (`kms:ViaService`) + key policy (service principal, SourceArn + EncryptionContext, agentspace/* and service/* statements) | live |
| `b6-cicd-deployer` | CI/CD deployer (Cloud Control / awscc) | Cloud Control entry points (IAM prefix `cloudformation:`, **not** `cloudcontrol:`) + the underlying aidevops CRUD scoped to `agentspace/*` + scoped PassRole; CloudFormation stack/change-set/type ops explicitly denied. Cloud Control → handler permission map still needs a live pass (D1) | live |
| `b7-log-delivery` | Vended log delivery configurer | `aidevops:AllowVendedLogDeliveryForResource` on both scopes (`agentspace/*` + `service/*`) + logs delivery V2 APIs; one policy per destination — CloudWatch Logs / S3 / Firehose (+ log-delivery SLR pinned by `iam:AWSServiceName`), S3+KMS caveat in the README | live |

## Cross-cutting test cases (attach to any scenario)

- Association actions authorize against **both** the association ARN and the Agent Space ARN (two-statement pattern for tag conditions).
- Tags live on Agent Spaces only — `aws:ResourceTag` conditions never match associations.
- Operator App policies scope by `${aws:PrincipalTag/AgentSpaceId}` session tag.
