# B6 — CI/CD deployer: DevOps Agent resources via Cloud Control API (Terraform `awscc`)

**Who this is for:** the **pipeline identity** (GitHub Actions OIDC role, GitLab runner role, CodeBuild service role, Terraform Cloud dynamic credentials) that runs `terraform apply` against the `awscc_devopsagent_*` resource types from the [Getting started with AWS DevOps Agent using Terraform](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-getting-started-with-aws-devops-agent-using-terraform.html) guide — `awscc_devopsagent_agent_space` and `awscc_devopsagent_association` (and, with awscc ≥ 1.98.0, `awscc_devopsagent_asset` / `awscc_devopsagent_trigger`).

> [!IMPORTANT]
> **This scenario is `static`, and its permission map is not fully verified.** The `awscc` provider does not call the DevOps Agent API — it calls the **Cloud Control API**, which then invokes a resource handler that calls `aidevops` on your behalf. Which of those two layers each permission is evaluated in, and whether the handler needs anything this policy does not grant, can only be settled by a live run. See [Unverified: the Cloud Control → handler permission mapping](#unverified-the-cloud-control--handler-permission-mapping) below and `docs/decisions.md` **D1**.

## Artifact

[`policies/cicd-deployer-policy.json`](./policies/cicd-deployer-policy.json) — replace `111122223333` with your account ID, `us-east-1` with your Region, and `DevOpsAgentRole-` with the role-name prefix your `b1` pre-provisioner uses.

## Why two layers of permissions

`awscc` is a thin, stackless wrapper over the Cloud Control API. A single `terraform apply` that creates an Agent Space is really:

1. The pipeline calls **`cloudformation:CreateResource`** (Cloud Control's `CreateResource`) with `TypeName = AWS::DevOpsAgent::AgentSpace` and a desired-state document, then polls **`cloudformation:GetResourceRequestStatus`** with the returned request token.
2. Cloud Control invokes the resource type's handler, which calls **`aidevops:CreateAgentSpace`** — authorized against the *caller's* identity, not a service role, because no execution role is configured for a first-party resource type.
3. Creating an Agent Space hands the pre-created Plane-A roles to the service, so **`iam:PassRole`** (conditioned on `iam:PassedToService`) is evaluated too.

Granting only layer 1 produces an `AccessDeniedException` surfaced through Cloud Control's `FAILED` request status rather than a clean IAM error; granting only layer 2 fails at the entry point. Both are required.

### The IAM prefix for Cloud Control API is `cloudformation:`, not `cloudcontrol:`

This is the single most common mistake writing this policy, and it fails **open-ended**: `cloudcontrol:CreateResource` is not a real action, so a policy containing it is silently useless (IAM accepts unknown actions in a policy document). The Service Authorization Reference lists Cloud Control API's actions — `CancelResourceRequest`, `CreateResource`, `DeleteResource`, `GetResource`, `GetResourceRequestStatus`, `ListResourceRequests`, `ListResources`, `UpdateResource` — under the **`cloudformation`** prefix, shared with CloudFormation proper. `parliament` agrees: `cloudcontrol:CreateResource` raises `UNKNOWN_PREFIX` in `python3 -m tools.checks`, which is what keeps this mistake out of the repo.

That shared prefix is why this scenario's deny matrix is unusually large (see [`expected/probes.yaml`](./expected/probes.yaml)): the Cloud Control statement must not become a foothold for stack operations.

## What each statement does

| Sid | Purpose |
|---|---|
| `CloudControlResourceLifecycle` | The Cloud Control API entry points: `CreateResource`, `GetResource`, `UpdateResource`, `DeleteResource`, `ListResources` (Terraform read/plan/import) and `GetResourceRequestStatus` (awscc polls the async request token after every write). These actions define **no resource types** in the Service Authorization Reference, so `Resource: "*"` is the only scope IAM accepts — the real constraint comes from layer 2 |
| `DevOpsAgentResourceCrud` | The underlying `aidevops` CRUD the handler performs: `CreateAgentSpace`, `GetAgentSpace`, `UpdateAgentSpace`, `DeleteAgentSpace`, `AssociateService` and `TagResource`, all scoped to `agentspace/*`. Association actions authorize against **both** the association ARN and the Agent Space ARN, and `agentspace/*` covers both |
| `DevOpsAgentListForPlan` | `aidevops:ListAgentSpaces` is account-level enumeration (no `agentspace` ARN) that the plan/read path uses to resolve an existing Agent Space |
| `PassAgentSpaceRoles` | `iam:PassRole` scoped to `role/DevOpsAgentRole-*` **and** conditioned on `iam:PassedToService = aidevops.amazonaws.com`. `check_required_conditions` asserts that condition (see `scenario.yaml` → `required_conditions`), so an edit dropping it fails static checks |

### Why `Resource: "*"` on the Cloud Control statement is acceptable here

Because Cloud Control's `CreateResource` accepts **any** registered resource type, `Resource: "*"` on it looks alarming. It is contained by the second layer: the handler's downstream call is authorized against the caller, so `cloudformation:CreateResource` with `TypeName = AWS::S3::Bucket` still fails on the missing `s3:CreateBucket`. The Cloud Control grant is an entry-point capability, not an authorization to create anything — the ARN-scoped `aidevops` statement is the actual blast radius.

If your organization wants a hard cap anyway, add an SCP or a permissions boundary listing the `aidevops` actions above (and nothing else) on the pipeline role; the sandbox harness does exactly that with `iamscn-boundary`.

## Deliberately absent

All of these are in `scenario.yaml` `forbidden_actions` — an edit adding one back fails `python3 -m tools.checks` — and each has a matching `implicitDeny` probe.

| Action(s) | Why it's not here |
|---|---|
| `cloudformation:CreateStack` / `UpdateStack` / `DeleteStack` / `CreateChangeSet` / `ExecuteChangeSet` / `CreateStackSet` | `awscc` is **stackless**: it calls Cloud Control directly and keeps state in the Terraform state file. A stack can create arbitrary resource types, which would make the ARN-scoped `aidevops` layer moot. Because Cloud Control shares the `cloudformation:` prefix, excluding these has to be deliberate and probe-proven |
| `cloudformation:RegisterType` / `SetTypeConfiguration` | Registering a private resource type lets the holder install a handler that runs with an execution role of its choosing — a straight escalation out of this policy |
| `iam:CreateRole` / `AttachRolePolicy` / `PutRolePolicy` / `DeleteRole` | Split duty with [`b1`](../b1-iam-preprovisioner/): the pipeline **passes** pre-created roles, it never mints them. The Terraform guide's default path *does* create the roles (`iam.tf`), which is exactly why the guide also documents `existing_agentspace_role_arn` / `existing_operator_role_arn` — set those and the pipeline needs no IAM write at all. If you keep the role-creating variant, the pipeline identity needs `b1`'s policy **in addition** to this one, and you have given the pipeline a create-role-then-pass-it primitive; prefer the split |
| `iam:CreateServiceLinkedRole` | The `AWSServiceRoleForAIDevOps` metrics SLR is provisioned once, by the [`b2`](../b2-installer/) installer or the pre-provisioner, not on every pipeline run. Note the gotcha: Agent Space creation fails with `InvalidParameterException` if that SLR does not yet exist, so a **first-ever** deploy in a virgin account must either be done by `b2` or temporarily add the `iam:AWSServiceName`-pinned grant from b2's `CreateDevOpsAgentServiceLinkedRole` statement |
| `aidevops:RegisterService`, `aidevops:EnableOperatorApp`, `aidevops:CreateOneTimeLoginSession` | Third-party service registration and Operator Web App enablement/login are `b2` and [`b3`](../b3-webapp-tiers/). Part 3 of the Terraform guide (integrations) and the operator-app block therefore need those grants added on top — see [Scope: Part 1 only](#scope-part-1-only) |
| `secretsmanager:*` | Integration credentials are [`b4`](../b4-secrets-manager/). Part 3 of the guide stores client secrets, so a pipeline that deploys integrations also needs b4's registrar policy |

## Scope: Part 1 only

This artifact covers **Part 1** of the Terraform guide (Agent Space + operator-app configuration block + the monitoring-account AWS association) with pre-created roles. The optional parts each pull in another scenario:

| Guide part | Additional permissions |
|---|---|
| Part 2 — cross-account monitoring | The secondary-account role is deployed by the `aws.service` provider alias with *service-account* credentials — a second pipeline identity, and `a2`/`b1` territory, not this policy. The monitoring-account side is one more `awscc_devopsagent_association`, already covered |
| Part 3 — third-party integrations | `aidevops:RegisterService` (+ `DeregisterService`, `GetService`) from `b2`, and `b4`'s Secrets Manager registrar statements for the credentials |
| Part 4 — assets and triggers | `aidevops:CreateAsset` / `CreateAssetFile` / `GetAsset` / `DeleteAsset` and `aidevops:CreateTrigger` / `GetTrigger` / `DeleteTrigger`, scoped to `agentspace/*`. Not added here because this scenario's persona is infrastructure deployment; asset authoring is a separate persona (a future scenario) |
| Operator app enablement as a *separate* resource | If you enable the Operator App outside the Agent Space's own configuration block, add `b2`'s `OperatorAppConfiguration` statement |

## Unverified: the Cloud Control → handler permission mapping

This is the scenario's headline caveat and the reason it is `status: static` with no `real` probes.

`docs/decisions.md` **D1** records that the repo's harnesses use the plain `hashicorp/aws` provider and prove policies with direct API probes, because "the awscc/Cloud Control coverage for `aidevops` resource types is unverified" — and notes that **b6 is the scenario that would test exactly that**. Specifically, the following are *reasoned from the Cloud Control API contract and the two cited doc pages*, not observed:

1. **Whether the handler's `aidevops` calls are authorized against the caller at all.** First-party resource types generally run handlers with the caller's credentials (which is why Cloud Control docs tell you to grant the underlying service permissions), but nothing in the DevOps Agent documentation states this for `AWS::DevOpsAgent::AgentSpace`. If a handler instead used an execution role, the `aidevops` statement here would be unnecessary and something else would be missing.
2. **The exact action set per handler operation.** A create handler commonly performs a read-back (`GetAgentSpace`) and Cloud Control's `UpdateResource` does a read-modify-write, so `GetAgentSpace` is granted for both. Tagging may be folded into `CreateAgentSpace` (tags-on-create) or issued as a separate `aidevops:TagResource` — `TagResource` is granted to cover both, and if the handler also reads tags back it may additionally need `aidevops:ListTagsForResource`, which is **not** granted here.
3. **`aidevops:ListAgentSpaces` vs. a `List` handler.** Cloud Control's `ListResources` maps to the type's list handler; whether that calls `ListAgentSpaces` on `*` or a narrower read is unverified.
4. **Association ARN shape.** `awscc_devopsagent_association` writes association resources; the two-statement/`agentspace/*` pattern is carried over from `b2`'s real-probe-validated behaviour, but no Cloud Control-path evidence exists.
5. **Whether Cloud Control surfaces the denial usefully.** A missing layer-2 permission is expected to appear as a `FAILED` request status with a `GeneralServiceException`/`AccessDenied` message rather than an IAM `AccessDeniedException` on the `CreateResource` call — worth knowing before debugging a pipeline.

**What a live `b6` pass must do** (follow-up work, out of scope for this static scenario):

- Extend the harness (or the probe step) to run a real `terraform apply` with the `awscc` provider using the `iamscn-b6-deployer` role's credentials, creating and destroying one `awscc_devopsagent_agent_space` in the sandbox — the only test that exercises both layers.
- Iterate down: start from this artifact, observe every `FAILED` request status, and add **only** actions the run proves are needed (per the repo's hard rule, never widen a policy to make a check pass without evidence).
- Widen `iamscn-boundary` if needed — it currently allows `aidevops:*` and `iam:PassRole` on `role/iamscn-*` but **no `cloudformation:` action at all**, so the Cloud Control half of this matrix would evaluate as denied under the boundary (that edit lives in `terraform/bootstrap/`, outside this scenario's delta).
- Then update D1 and promote this scenario to `live`.

## Live validation coverage

[`expected/probes.yaml`](./expected/probes.yaml) is **simulate-only** (`iam:SimulateCustomPolicy` against the artifact in isolation):

- **allow** — all six Cloud Control actions, all seven `aidevops` actions, and `iam:PassRole` on `DevOpsAgentRole-*` with `iam:PassedToService = aidevops.amazonaws.com`
- **deny** — `iam:CreateRole` / `AttachRolePolicy` / `CreateServiceLinkedRole`; `PassRole` to `lambda.amazonaws.com` and to a non-prefixed role; the CloudFormation stack/change-set/type-registration family (the shared-prefix containment proof); and the adjacent `aidevops:RegisterService` / `EnableOperatorApp` / `CreateOneTimeLoginSession`, `secretsmanager:CreateSecret` and `logs:PutDeliverySource` grants that belong to `b2`/`b3`/`b4`/`b7`

There are deliberately **no `real` probes**. A real probe here would have to be a full `awscc` apply (see above — the harness cannot do that yet), and a real `aidevops:CreateAgentSpace` needs an onboarded account and leaves an Agent Space the destroy step cannot reliably remove. The simulator answers the "does the artifact grant this" question exactly; it cannot answer "is the artifact sufficient", which is precisely the unverified part.

`terraform/` provisions IAM primitives only: the `iamscn-b6-deployer` role under test plus one pre-existing `iamscn-b6-dar-agentspace` role (the substituted `DevOpsAgentRole-` prefix) so the `PassRole` probes name a real ARN.

## Sources

See `scenario.yaml` `docs:`:

- *Getting started with AWS DevOps Agent using Terraform* — the `awscc_devopsagent_agent_space` / `awscc_devopsagent_association` / `awscc_devopsagent_asset` / `awscc_devopsagent_trigger` resource types, the awscc provider version requirements, the `existing_agentspace_role_arn` / `existing_operator_role_arn` variables that make the split-duty pattern possible, the IAM-propagation `time_sleep` before Agent Space creation, and the four-part structure this scenario scopes itself against.
- *DevOps Agent IAM permissions* — the source for every `aidevops:*` action used here (`CreateAgentSpace`, `GetAgentSpace`, `UpdateAgentSpace`, `DeleteAgentSpace`, `AssociateService`, `TagResource`, `ListAgentSpaces`) and for the `aidevops` ARN shapes.
- The Cloud Control API action names and their `cloudformation` IAM prefix come from the *Service Authorization Reference* ("Actions, resources, and condition keys for AWS Cloud Control API"), which is also `parliament`'s action table — the linter's `UNKNOWN_PREFIX` on `cloudcontrol:` is the mechanical check that keeps the prefix right.
