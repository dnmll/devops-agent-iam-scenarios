# Policy authoring conventions

- Policies are docs-style raw JSON: `Version: 2012-10-17`, `Sid` on every statement, placeholder account `111122223333`, placeholder region `us-east-1`.
- Least privilege is the product. Prefer resource-scoped ARNs; `Resource: "*"` only where the action requires it (e.g. `aidevops:ListAssetTypes`) and note why in the README.
- Required condition keys (enforced by `check_required_conditions` via `scenario.yaml`):
  - `iam:PassRole` → `iam:PassedToService: aidevops.amazonaws.com`
  - `iam:CreateServiceLinkedRole` → `iam:AWSServiceName` pinned to the specific service
  - Service-principal KMS statements → `aws:SourceArn` + `kms:EncryptionContext:...`
  - Caller KMS statements → `kms:ViaService: aidevops.<region>.amazonaws.com`
- DevOps Agent gotchas to respect:
  - Association actions authorize against BOTH the association ARN and the Agent Space ARN — single-ARN statements silently fail; use the two-statement pattern for tag conditions (tags exist on Agent Spaces only).
  - The Operator App policy scopes by `${aws:PrincipalTag/AgentSpaceId}` session tag.
  - Agent Space creation fails with `InvalidParameterException` without `iam:CreateServiceLinkedRole` for `aidevops.amazonaws.com` (the `AWSServiceRoleForAIDevOps` metrics SLR).
