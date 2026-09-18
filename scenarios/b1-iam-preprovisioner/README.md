# B1 — IAM admin pre-provisioner (split-duty role creation)

**Who this is for:** the IAM administrator who pre-creates the AWS DevOps Agent **Plane-A roles** — the Agent Space role, the Operator Web App role and (optionally) the cross-account role — so that the person who actually onboards the Agent Space (scenario [`b2-installer`](../b2-installer/README.md)) never needs IAM role-creation rights, only `iam:PassRole`.

This is the **split-duty** half of the onboarding: b1 creates roles but cannot use them; b2 uses roles but cannot create them. Neither identity alone can escalate to "create a role with broad permissions, then hand it to the service".

## Artifact

[`policies/preprovisioner-policy.json`](./policies/preprovisioner-policy.json) — replace `111122223333` with your account ID and `DevOpsAgentRole-` with your own role-naming prefix. Keep the prefix in sync with the installer's `PassAgentSpaceRoles` statement in b2; the two policies are only safe as a pair if they agree on the prefix.

## What each statement does

| Sid | Purpose |
|---|---|
| `ProvisionDevOpsAgentRoles` | `iam:CreateRole` with the documented trust policy, plus `GetRole` (read back the ARN the installer needs), `TagRole`, `UpdateAssumeRolePolicy` (retrofit `aws:SourceArn`/`aws:SourceAccount` confused-deputy conditions) and `DeleteRole` (decommission). Scoped to `role/DevOpsAgentRole-*` |
| `InlineResourceExplorerSlrPolicy` | `iam:PutRolePolicy` — the CLI onboarding guide attaches an inline `AllowCreateServiceLinkedRoles` policy to the Agent Space role so the service can create `AWSServiceRoleForResourceExplorer`. Same `DevOpsAgentRole-*` scope |
| `AttachDevOpsAgentManagedPolicies` | `iam:AttachRolePolicy` pinned by the `iam:PolicyARN` condition to exactly `arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy` and `arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy` — the two AWS managed policies the onboarding guide attaches. Without this condition, "create a `DevOpsAgentRole-*` role" would be a path to `AdministratorAccess` |

### Why `iam:PolicyARN` matters

`iam:AttachRolePolicy` scoped only by role ARN says nothing about *which* policy gets attached. An admin holding that grant could attach `AdministratorAccess` to `DevOpsAgentRole-Anything` and then have the installer pass it to the service. The `ArnEquals` condition on `iam:PolicyARN` closes that path, and `check_required_conditions` asserts both ARNs are present (see `scenario.yaml` → `required_conditions`).

`iam:PutRolePolicy` cannot be constrained the same way — IAM has no condition key for inline-policy content — so it stays a deliberate trust boundary: this persona is an IAM administrator for the `DevOpsAgentRole-*` namespace, and that namespace's roles are capped only by what you also attach as a permissions boundary. If you need a hard cap, add `iam:PermissionsBoundary` conditions to `CreateRole`/`PutRolePolicy` and require your own boundary policy.

## Not included (by design)

These are enforced by `forbidden_actions` in `scenario.yaml`, so a future edit that adds them fails static checks:

- **`iam:PassRole`** — passing a role to `aidevops.amazonaws.com` is the installer's job (`b2`)
- **All `aidevops:*` actions** — Agent Space lifecycle is `b2`, console/Web App use is `b3`
- **`iam:CreateServiceLinkedRole`** — the `AWSServiceRoleForAIDevOps` metrics SLR is created by the installer during Agent Space creation (`b2`), and the Resource Explorer SLR is created by the Agent Space role itself at runtime (`a1`)
- **`iam:CreateUser` / `iam:CreateAccessKey`** — no principal creation beyond the role namespace
- **`iam:PutRolePermissionsBoundary` / `iam:DeleteRolePermissionsBoundary`** — the pre-provisioner must not be able to lift a boundary
- **`sts:AssumeRole`** — creating a role must not imply the ability to use it

## Validation

`expected/probes.yaml` is **simulate-only** (`iam:SimulatePrincipalPolicy`): a role-creation policy can be proved without creating IAM principals in the sandbox, and b1 makes no `aidevops` calls at all, so there is no meaningful "real" allow-path probe. The matrix covers:

- allow — every lifecycle action on `DevOpsAgentRole-*`, and attachment of both documented managed policies
- deny — the same actions on a non-prefixed role name, `AttachRolePolicy` with `AdministratorAccess`, `iam:PassRole`, `aidevops:CreateAgentSpace`/`ListAgentSpaces`, `iam:CreateUser`, `iam:CreateServiceLinkedRole`, boundary tampering, and Secrets Manager reads

`terraform/` provisions the role under test via the shared `scenario-role` module, plus one pre-existing `iamscn-b1-dar-agentspace` role so the read/update/delete probes target a real ARN.

**Live-validate note:** the shared `iamscn-boundary` permissions boundary currently allows no `iam:` write actions (see `terraform/bootstrap/boundary.tf`), so the positive half of this matrix would simulate as denied in a live run. Promoting b1 from `static` to `live` requires widening the boundary with `iam:CreateRole`/`PutRolePolicy`/`AttachRolePolicy`/`TagRole`/`UpdateAssumeRolePolicy`/`DeleteRole` scoped to `role/iamscn-b1-dar-*` (and `iam:PolicyARN`-pinned attachment). That is bootstrap infrastructure and is intentionally left out of this change.

## Sources

See `scenario.yaml` `docs:`. Specifically the *CLI onboarding guide* (steps "Create the DevOps Agent space role" and "Create the operator app IAM role" — the exact `create-role` / `attach-role-policy` / `put-role-policy` calls and the two managed-policy ARNs) and the *Creating an agent space* page (the console's assign-existing-role vs. auto-create-role choice).
