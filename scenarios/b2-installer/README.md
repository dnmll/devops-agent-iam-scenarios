# B2 — Installer: create and configure an Agent Space

**Who this is for:** the person (or pipeline identity) who creates an AWS DevOps Agent **Agent Space**, wires up account/service associations, enables the Operator Web App, and passes pre-created DevOps Agent roles to the service.

**Variant covered here:** *assign-existing-role* — the Plane-A roles (Agent Space role, Operator App role) were pre-created by an IAM administrator (scenario `b1`). The installer therefore needs `iam:PassRole` but **no IAM role-creation permissions**. For the console's *auto-create role* option, combine this policy with `b1`.

## Artifact

[`policies/installer-policy.json`](./policies/installer-policy.json) — replace `111122223333` with your account ID, `us-east-1` with your region, and `DevOpsAgentRole-*` with your role-naming prefix.

## What each statement does

| Sid | Purpose |
|---|---|
| `AgentSpaceLifecycle` | Create/read/update/delete Agent Spaces and manage their tags |
| `AgentSpaceList` | `ListAgentSpaces` targets the account, not a specific space, so it needs `Resource: "*"` |
| `AssociationManagement` | Add/validate/remove AWS account associations. Scoped to `agentspace/*`, which covers both the Agent Space ARN and the association ARNs beneath it (association actions authorize against **both**) |
| `ServiceRegistration` | Register the account-level service and third-party tool integrations |
| `OperatorAppConfiguration` | Enable/disable the Operator Web App and configure its IdP |
| `PassAgentSpaceRoles` | Pass only `DevOpsAgentRole-*` roles, and only to `aidevops.amazonaws.com` (`iam:PassedToService`) |
| `ViewRolesForConsoleSelection` | Console role-picker dropdowns (`iam:GetRole`/`ListRoles` don't support resource scoping usefully here) |
| `CreateDevOpsAgentServiceLinkedRole` | Agent Space creation auto-creates the `AWSServiceRoleForAIDevOps` metrics SLR; without this the create call fails with `InvalidParameterException` |

## Not included (by design)

- IAM role/policy creation → scenario `b1`
- Secrets Manager for third-party credentials → scenario `b4`
- Customer-managed KMS key (`kms:*` + key policy) → scenario `b5`
- Log delivery configuration → scenario `b7`

## Sources

See `scenario.yaml` `docs:` for the AWS documentation pages every action is traced to.
