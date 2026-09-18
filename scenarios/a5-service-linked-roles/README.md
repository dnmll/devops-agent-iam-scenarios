# A5 — Service-linked roles (vended metrics + log delivery)

**Who this is for:** whoever onboards AWS DevOps Agent in an account, and whoever writes the cleanup automation afterwards. AWS DevOps Agent depends on **two service-linked roles (SLRs)**. This scenario is the guidance for both plus the single IAM grant that provisions them — [`policies/create-slr-policy.json`](./policies/create-slr-policy.json).

An SLR is not an ordinary role. AWS owns its trust policy and its permissions, you cannot edit either, and it only exists so a service can act in your account under its own identity. That is what makes "may create these two SLRs" a small, safe grant — and why it still needs a condition (see below).

| Service-linked role | Service principal | What it is for | Created by |
|---|---|---|---|
| `AWSServiceRoleForAIDevOps` | `aidevops.amazonaws.com` | Publishes DevOps Agent's **vended metrics** into the `AWS/AIDevOps` CloudWatch namespace | Automatically, during **Agent Space creation** ([b2](../b2-installer/)) |
| `AWSServiceRoleForLogDelivery` | `delivery.logs.amazonaws.com` | Lets the AWS log-delivery service write **vended logs** into an Amazon Data Firehose stream | The delivery configurer, on the **Firehose** path only ([b7](../b7-log-delivery/)) |

Docs: [Vended logs and metrics](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-vended-logs-and-metrics.html) · [DevOps Agent IAM permissions](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-devops-agent-iam-permissions.html) (service-linked roles section).

## `AWSServiceRoleForAIDevOps` — vended metrics

DevOps Agent publishes its own operational metrics (investigation counts, latencies and similar) as **vended metrics** in the CloudWatch namespace **`AWS/AIDevOps`**. Vended metrics are written by the service, under the SLR's identity, into your account — you do not grant `cloudwatch:PutMetricData` to anything, and there is nothing to configure per Agent Space. To *read* them, use ordinary CloudWatch metric permissions (`cloudwatch:GetMetricData` / `GetMetricStatistics`) on the `AWS/AIDevOps` namespace; that read grant belongs with your observability consumers, not here.

**The gotcha.** The SLR is created automatically as part of `CreateAgentSpace`, which means the *caller* must be allowed to create it. Without `iam:CreateServiceLinkedRole` for `aidevops.amazonaws.com`, Agent Space creation fails with **`InvalidParameterException`** — a message that says nothing about service-linked roles, which is why this is the single most common first-onboarding failure. The installer policy in [b2](../b2-installer/) carries that grant (`CreateDevOpsAgentServiceLinkedRole`) for exactly this reason.

**Agent Spaces created before 2026-03-13.** Automatic creation at Agent Space creation time is newer than the service. Agent Spaces created before **2026-03-13** predate it and have no `AWSServiceRoleForAIDevOps` in the account, so their vended metrics never appear. Create it once, manually:

```bash
aws iam create-service-linked-role \
  --aws-service-name aidevops.amazonaws.com
```

It is idempotent in effect — a second call returns `InvalidInput` ("Service role name AWSServiceRoleForAIDevOps has been taken in this account"), which means you already have it. One per account; it is not regional and not per-Agent-Space.

## `AWSServiceRoleForLogDelivery` — Firehose log delivery

DevOps Agent vended **logs** can go to CloudWatch Logs, Amazon S3 or Amazon Data Firehose. The first two are authorized by a **resource policy** on the destination (a CloudWatch Logs account resource policy, or the destination bucket policy). Firehose has no resource policy, so the log-delivery service instead assumes **`AWSServiceRoleForLogDelivery`** in your account and calls `firehose:PutRecord` with it. If the role does not exist, the first Firehose delivery setup fails.

This SLR is **shared across AWS** — it is the same role every service that uses vended log delivery (VPC flow logs, API Gateway, Bedrock, …) relies on. In most accounts it already exists. Create it once if not:

```bash
aws iam create-service-linked-role \
  --aws-service-name delivery.logs.amazonaws.com
```

The CloudWatch Logs and S3 destinations do **not** need it; see [b7](../b7-log-delivery/) for the per-destination policies.

## `policies/create-slr-policy.json` — the grant

```json
{
  "Sid": "CreateDevOpsAgentServiceLinkedRoles",
  "Effect": "Allow",
  "Action": "iam:CreateServiceLinkedRole",
  "Resource": [
    "arn:aws:iam::111122223333:role/aws-service-role/aidevops.amazonaws.com/AWSServiceRoleForAIDevOps",
    "arn:aws:iam::111122223333:role/aws-service-role/delivery.logs.amazonaws.com/AWSServiceRoleForLogDelivery"
  ],
  "Condition": {
    "StringEquals": {
      "iam:AWSServiceName": ["aidevops.amazonaws.com", "delivery.logs.amazonaws.com"]
    }
  }
}
```

Replace `111122223333` with your account ID and `us-east-1` with your Region (SLR ARNs are not regional, but the placeholder region appears elsewhere in this repo's artifacts and the harness substitutes both).

Attach it to whichever identity does onboarding — typically the [b2](../b2-installer/) installer (which already carries the metrics half) or your onboarding pipeline role. It is a **one-time** grant: once both SLRs exist, nothing in DevOps Agent needs `iam:CreateServiceLinkedRole` again, and removing it afterwards is a reasonable hardening step.

**Why `iam:AWSServiceName` is mandatory.** Unconditioned, `iam:CreateServiceLinkedRole` is a *create-an-SLR-for-any-AWS-service* primitive: dozens of services, each SLR arriving with its own AWS-managed permissions policy attached, in your account, without any further approval. The condition pins it to the two services that DevOps Agent actually needs. `check_required_conditions` asserts **both** values via `scenario.yaml`, so dropping either one fails `python3 -m tools.checks`:

```yaml
required_conditions:
  - artifact: policies/create-slr-policy.json
    action: iam:CreateServiceLinkedRole
    condition_key: iam:AWSServiceName
    expected: aidevops.amazonaws.com
  - artifact: policies/create-slr-policy.json
    action: iam:CreateServiceLinkedRole
    condition_key: iam:AWSServiceName
    expected: delivery.logs.amazonaws.com
```

**Why one statement and not two.** `check_required_conditions` requires *every* Allow statement granting the action to carry the asserted value, so two per-service statements would make the two assertions mutually exclusive — each statement would fail the other's rule. One statement with a two-value condition list is exactly as tight: IAM requires the request to match both the `Resource` list **and** the condition, and the two-ARN `Resource` list is what stops a correct-name/wrong-path cross-pairing. Probes `sim-create-log-delivery-slr-with-aidevops-name-denied` and `sim-create-aidevops-slr-with-log-delivery-name-denied` assert that independently of the condition. If you prefer one statement per service in your own deployment, split it — the effective permissions are identical.

## Never delete these

Both SLRs are **account-wide singletons with no owner**, and neither `iam:DeleteServiceLinkedRole` nor `iam:DeleteRole` appears in this artifact (both are in `forbidden_actions`). If your sweeper, teardown script or drift detector deletes them:

- **`AWSServiceRoleForAIDevOps`** — vended metrics stop for **every** Agent Space in the account, silently. Nothing errors; the `AWS/AIDevOps` namespace just goes quiet, and dashboards and alarms built on it flatline rather than alarm.
- **`AWSServiceRoleForLogDelivery`** — **every** Firehose-targeted vended log delivery in the account breaks, including other AWS services' deliveries that have nothing to do with DevOps Agent.

Practical rules for cleanup automation:

1. **Allowlist by path.** Skip anything under `arn:aws:iam::<account>:role/aws-service-role/` — an SLR is never "an orphaned role from a test run". This repo does exactly that: `docs/sandbox-account.md` lists SLRs as persistent-by-design in the sweeper allowlist, and this scenario's terraform harness deliberately never creates them (so `destroy` can never remove them).
2. **Do not tag-scope them.** SLRs created by a service carry no run tags, so a tag-based sweeper will not see them — but an "untagged resources" sweeper *will*, and must exclude the SLR path.
3. **Deleting is not the way to test this scenario.** Creation is idempotent-in-effect and the roles are shared; if you need to prove the grant works, simulate it (below) rather than create-and-delete.

## Deliberately absent

| Action | Why it's not here |
|---|---|
| `iam:DeleteServiceLinkedRole` | See above — both roles are account-wide singletons other Agent Spaces and other AWS services depend on. Deletion is a deliberate, human, out-of-band act |
| `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PutRolePolicy`, `iam:UpdateAssumeRolePolicy` | Creating an SLR is not role administration: AWS fixes an SLR's trust policy and permissions, which is the whole reason this grant is safe. Ordinary role/policy CRUD is [b1](../b1-iam-preprovisioner/) |
| `iam:PassRole` | Nothing here passes a role to the service; that is [b2](../b2-installer/), pinned by `iam:PassedToService` |
| `aidevops:*` | Provisioning the SLRs is a prerequisite for Agent Space creation, not a licence to do it. Agent Space lifecycle is [b2](../b2-installer/) |
| `logs:PutDeliverySource` / `PutDeliveryDestination` / `CreateDelivery` | Turning vended log delivery on is [b7](../b7-log-delivery/). This artifact only creates the role that delivery *depends* on |
| `cloudwatch:PutMetricData` | Vended metrics are written by the service under the SLR's own identity. No caller ever needs to publish into `AWS/AIDevOps` |
| The `AWSServiceRoleForResourceExplorer` SLR | Created by the Agent Space role itself at runtime — [a1](../a1-agentspace-role/), `policies/slr-inline-policy.json` |

## Live validation coverage

`status: static`. The harness ([`terraform/`](./terraform/)) creates one `iamscn-a5-slr` role carrying the artifact as its candidate policy, and every probe in [`expected/probes.yaml`](./expected/probes.yaml) is `kind: simulate` — `iam:CreateServiceLinkedRole` is in the IAM policy simulator's action database and honours `iam:AWSServiceName`, so `SimulateCustomPolicy` answers every question this artifact raises: both allows, the condition pin (wrong service name, look-alike service name, missing context key), the resource pin (both cross-pairings, plus the Resource Explorer SLR), and the adjacent-privilege denies.

There are **no `real` probes, on purpose**. A real `CreateServiceLinkedRole` either collides with an SLR that already exists in the sandbox account (which b2's probes have exercised) or leaves behind a role the per-run `destroy` is explicitly forbidden to delete — and deleting the log-delivery SLR would break concurrent live-validate runs and any other vended delivery in the account. The IAM question is fully answered by simulation, and simulation leaves nothing behind.
