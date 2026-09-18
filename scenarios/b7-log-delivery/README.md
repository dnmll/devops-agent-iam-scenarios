# B7 — Vended log delivery configurer

**Who this is for:** the person (or pipeline identity) who turns on **AWS DevOps Agent vended logs** for an Agent Space or a registered service and points the delivery at a destination.

Vended logs are delivered by AWS, not written by your code: you register the DevOps Agent resource as a **delivery source**, register a **delivery destination**, and join the two with a **delivery**. That takes permissions on two sides — one `aidevops` action that authorizes vending *from* the DevOps Agent resource, and the CloudWatch Logs *delivery V2* APIs plus whatever the chosen destination needs.

## Artifacts — one per destination

AWS DevOps Agent supports three destinations, and each needs a different set of destination-side permissions. Rather than one union policy, this scenario ships three, so nobody gets permissions for a destination they are not using:

| Artifact | Destination |
|---|---|
| [`policies/cloudwatch-logs-target.json`](./policies/cloudwatch-logs-target.json) | A CloudWatch Logs log group |
| [`policies/s3-target.json`](./policies/s3-target.json) | An Amazon S3 bucket |
| [`policies/firehose-target.json`](./policies/firehose-target.json) | An Amazon Data Firehose delivery stream |

Attach **exactly one**. Replace `111122223333` with your account ID, `us-east-1` with your Region, and the destination placeholders (`devops-agent-vended-logs` bucket / stream name, the `/aws/vendedlogs/devops-agent/` log-group prefix) with your own names.

## The common core (in all three artifacts)

| Sid | Purpose |
|---|---|
| `AllowVendedLogDeliveryForDevOpsAgentResources` | `aidevops:AllowVendedLogDeliveryForResource` on **both** scope ARNs — `agentspace/*` **and** `service/*`. See below |
| `ManageVendedLogDeliveryConfiguration` | The ARN-scoped delivery V2 lifecycle: `logs:PutDeliverySource`, `logs:PutDeliveryDestination`, `logs:CreateDelivery`, `logs:GetDelivery*`, `logs:UpdateDeliveryConfiguration`, `logs:DeleteDelivery*`, scoped to the `delivery-source`, `delivery-destination` and `delivery` resource types |
| `DescribeVendedLogDeliveryConfiguration` | `logs:DescribeDeliver*` — the three account-level list APIs (`DescribeDeliveries`, `DescribeDeliverySources`, `DescribeDeliveryDestinations`). They take no ARN, so IAM only accepts `Resource: "*"` (suppressed with a reason in `scenario.yaml`) |

### Why `AllowVendedLogDeliveryForResource` needs *two* resource ARNs

You can vend logs from an **Agent Space** (`arn:aws:aidevops:<region>:<account>:agentspace/<id>`) or from a **registered service** (`arn:aws:aidevops:<region>:<account>:service/<id>`). They are distinct resource types, so a wildcard on one does not cover the other: `agentspace/*` never matches a `service/...` ARN.

A policy with only `agentspace/*` looks correct, passes review, and then fails the moment somebody enables logs on a registered service — with an access-denied on an action most people have never seen before. Both ARNs are listed in every artifact, and `expected/probes.yaml` asserts `allowed` on each separately so a future edit that drops one is caught. (This mirrors the two-ARN habit that the association actions and the `b5` KMS key policy also need — see `.claude/rules/policy-authoring.md`.)

Note that the `Resource` here is the resource being *logged*, not a logs ARN. `aidevops:AllowVendedLogDeliveryForResource` is the source-side consent: without it, `logs:PutDeliverySource` fails even when every `logs:*` permission is in place.

## Destination-side additions

### CloudWatch Logs — `policies/cloudwatch-logs-target.json`

| Sid | Purpose |
|---|---|
| `PrepareCloudWatchLogsDestination` | `logs:CreateLogGroup`, pinned to the `/aws/vendedlogs/devops-agent/*` prefix. AWS's convention for vended logs is a `/aws/vendedlogs/` log group; keeping the grant to a prefix means the configurer cannot create (and therefore cannot later be granted write on) log groups elsewhere in the account |
| `InspectCloudWatchLogsDestination` | `logs:DescribeLogGroups` (pick an existing group) plus `logs:PutResourcePolicy` / `logs:DescribeResourcePolicies` — the **account-level** CloudWatch Logs resource policy that grants `delivery.logs.amazonaws.com` permission to write into the destination group. There is one such document per account per Region, it takes no resource ARN, so `Resource: "*"` is the only form IAM accepts |

`logs:PutResourcePolicy` is the sharpest edge in this artifact: the document it writes is account-wide, and an overwrite can revoke *other* services' log-delivery grants. If your account already has CloudWatch Logs resource policies in use, read the existing document (`logs:DescribeResourcePolicies`, granted here) and add a statement to it rather than replacing it — and consider making this action a reviewed/approved operation. Prefer having the log group already created by the destination owner and giving this persona only `logs:DescribeLogGroups`.

### S3 — `policies/s3-target.json`

| Sid | Purpose |
|---|---|
| `ManageDeliveryBucketPolicy` | `s3:GetBucketPolicy` + `s3:PutBucketPolicy` on the single destination bucket ARN |

**Why the bucket policy is needed.** Delivery to S3 is performed by the AWS log-delivery service, not by your identity, so the *bucket* must allow that service principal to write. Add a statement like this to the destination bucket's policy (this is the document the configurer writes with `s3:PutBucketPolicy`):

```json
{
  "Sid": "AWSLogDeliveryWrite",
  "Effect": "Allow",
  "Principal": {
    "Service": "delivery.logs.amazonaws.com"
  },
  "Action": "s3:PutObject",
  "Resource": "arn:aws:s3:::devops-agent-vended-logs/AWSLogs/111122223333/*",
  "Condition": {
    "StringEquals": {
      "aws:SourceAccount": "111122223333",
      "s3:x-amz-acl": "bucket-owner-full-control"
    },
    "ArnLike": {
      "aws:SourceArn": "arn:aws:logs:us-east-1:111122223333:*"
    }
  }
}
```

Keep the `aws:SourceAccount` / `aws:SourceArn` pair: without them the statement is a confused-deputy hole that lets any account's log delivery write into your bucket.

**SSE-KMS caveat.** If the destination bucket is encrypted with a **customer-managed KMS key** (SSE-KMS), the bucket policy above is not sufficient — delivery fails, silently from the configurer's point of view, because the failure happens inside the AWS delivery service. The **key policy** must also allow the log-delivery service principal to generate a data key:

```json
{
  "Sid": "AWSLogDeliveryEncrypt",
  "Effect": "Allow",
  "Principal": {
    "Service": "delivery.logs.amazonaws.com"
  },
  "Action": [
    "kms:GenerateDataKey",
    "kms:Decrypt"
  ],
  "Resource": "*",
  "Condition": {
    "StringEquals": {
      "aws:SourceAccount": "111122223333"
    },
    "ArnLike": {
      "aws:SourceArn": "arn:aws:logs:us-east-1:111122223333:*"
    }
  }
}
```

Editing a key policy is a **different permission** (`kms:PutKeyPolicy`) and it is deliberately **not** in this artifact: `kms:PutKeyPolicy` on a shared key lets the holder grant themselves decrypt on everything that key protects. Either have the key owner add the statement once, out of band, or use a bucket with SSE-S3 for delivery. Buckets with **S3 Object Lock** enabled are not valid log-delivery destinations at all. (The separate question of the CMK protecting DevOps Agent's own data — `kms:ViaService: aidevops.<region>.amazonaws.com` on the caller side plus an `aidevops.amazonaws.com` service-principal key statement — is scenario [`b5`](../../docs/scenario-matrix.md), not this one.)

`s3:PutBucketPolicy` is inherently self-escalating (the holder can write themselves `s3:GetObject`), which parliament flags and `scenario.yaml` suppresses with that reasoning spelled out. It is contained by scoping the statement to the one delivery bucket, by granting no object-level access here, and by keeping the destination bucket **delivery-only**. If your destination bucket holds anything else, put an SCP or a bucket-owner review over `s3:PutBucketPolicy` instead of handing this artifact out as-is.

### Firehose — `policies/firehose-target.json`

| Sid | Purpose |
|---|---|
| `InspectAndTagFirehoseDeliveryStream` | `firehose:DescribeDeliveryStream` (confirm the stream exists and is `ACTIVE` before pointing delivery at it) + `firehose:TagDeliveryStream` (the delivery setup tags the stream), on the one stream ARN |
| `CreateLogDeliveryServiceLinkedRole` | `iam:CreateServiceLinkedRole` for **`AWSServiceRoleForLogDelivery`**, conditioned on `iam:AWSServiceName: delivery.logs.amazonaws.com` |

**Why the Firehose path alone needs a service-linked role.** Delivering to CloudWatch Logs or S3 is authorized by a resource policy on the destination. Firehose has no resource policy, so the log-delivery service instead assumes `AWSServiceRoleForLogDelivery` in your account to call `firehose:PutRecord`. If that role does not exist yet, the first Firehose delivery setup fails — so the configurer needs to be able to create it.

`iam:CreateServiceLinkedRole` without a condition is a create-any-service-linked-role primitive (dozens of AWS services, each with its own attached managed policy). The `iam:AWSServiceName` condition pins it to exactly one service, and the resource ARN pins the role path. `check_required_conditions` enforces that condition via `scenario.yaml` `required_conditions`, so an edit that drops it fails `python3 -m tools.checks`:

```yaml
required_conditions:
  - artifact: policies/firehose-target.json
    action: iam:CreateServiceLinkedRole
    condition_key: iam:AWSServiceName
    expected: delivery.logs.amazonaws.com
```

Note this is a *different* service-linked role from the one in [`b2`](../b2-installer/) (`AWSServiceRoleForAIDevOps`, `aidevops.amazonaws.com`, the vended-**metrics** SLR). Both are listed in `a5-service-linked-roles`. The condition is what keeps this artifact from being able to create that one.

## Deliberately absent

| Action | Why it's not here |
|---|---|
| `logs:GetLogEvents`, `logs:FilterLogEvents`, `logs:StartQuery`, `s3:GetObject` | The configurer *enables* delivery; it does not read what was delivered. DevOps Agent vended logs contain investigation activity — granting the person who wired up logging the ability to read them all is a much bigger grant than "turn on logging" |
| `s3:PutObject`, `firehose:PutRecord`, `firehose:PutRecordBatch` | Records are written by the AWS log-delivery service (via the bucket policy / the SLR), never by this identity |
| `firehose:CreateDeliveryStream`, `firehose:DeleteDeliveryStream`, `firehose:UpdateDestination` | Owning the destination stream is the destination owner's job; this persona inspects and points at an existing one |
| `kms:PutKeyPolicy` | See the SSE-KMS caveat — that is a key-owner action, and on a shared key it is a decrypt-everything escalation |
| `aidevops:CreateAgentSpace` / `DeleteAgentSpace` / `RegisterService` / `AssociateService` | Agent Space lifecycle and service registration are scenario [`b2`](../b2-installer/). `AllowVendedLogDeliveryForResource` is the *only* `aidevops` action this persona has |
| `iam:PassRole`, `iam:CreateRole` | Role provisioning is [`b1`](../b1-iam-preprovisioner/). `iam:CreateServiceLinkedRole` (Firehose artifact only) is not `CreateRole`: the trust policy and permissions of an SLR are fixed by AWS |
| `secretsmanager:GetSecretValue` | Credential storage is [`b4`](../b4-secrets-manager/) |

All of these are in `scenario.yaml` `forbidden_actions`, so re-adding one fails `python3 -m tools.checks`, and the interesting ones have matching `implicitDeny` probes.

## Live validation coverage

All expectations are `kind: simulate` (`iam:SimulateCustomPolicy` against the artifact in isolation). [`expected/probes.yaml`](./expected/probes.yaml) is the **CloudWatch Logs** matrix (the console default): allows on both scope ARNs, allows across the whole delivery core, allows on the CWL destination additions, and denies on

- log groups outside `/aws/vendedlogs/devops-agent/*`,
- **the other two destinations** — `firehose:DescribeDeliveryStream` / `firehose:TagDeliveryStream`, `s3:GetBucketPolicy` / `s3:PutBucketPolicy` and the log-delivery SLR are all `implicitDeny` under the CloudWatch Logs artifact,
- reading delivered logs (`logs:GetLogEvents`, `logs:FilterLogEvents`),
- the adjacent `b1`/`b2` privileges (`aidevops:CreateAgentSpace`, `DeleteAgentSpace`, `RegisterService`, `iam:PassRole`).

`probes.schema.json` carries a single `role_under_test`, so one live run exercises one destination. The S3 and Firehose matrices live in [`probes-s3.yaml`](./expected/probes-s3.yaml) and [`probes-firehose.yaml`](./expected/probes-firehose.yaml) — same shape, mirrored cross-target denies, plus the S3 other-bucket / no-object-access denies and the Firehose "only this one SLR, only this one stream" denies. Their roles (`iamscn-b7-s3`, `iamscn-b7-firehose`) are deployed by the same terraform harness and their expectations are ready to wire up when the probes schema grows multi-role support (same arrangement as [`b3`](../b3-webapp-tiers/README.md)).

There are no `real` probes on purpose. A real `logs:PutDeliverySource` for a DevOps Agent resource needs an Agent Space in an account that has been onboarded to DevOps Agent (the sandbox has not — the same limitation documented in `b3` and `b4`), and a real `logs:PutResourcePolicy` would mutate the sandbox account's single account-wide CloudWatch Logs resource-policy document, which the destroy step cannot safely restore.

The terraform harness provisions **IAM primitives only** — three roles, one per destination, each capped by `iamscn-boundary`. No log groups, buckets, streams, delivery sources or destinations are created: every probe is a simulation, so none of those resources need to exist.

**Prerequisite for promoting this scenario to `live`:** the shared `iamscn-boundary` already allows `iam:CreateServiceLinkedRole` for `delivery.logs.amazonaws.com`, but does not yet carry scoped `logs`, `s3` or `firehose` statements (its `boundary.tf` explicitly reserves that widening for "B4/B5/B7"). `simulate` probes use `SimulateCustomPolicy` against the artifact in isolation, so they are unaffected; any future `real` probe here would need that boundary edit first. That file lives in `terraform/bootstrap/`, outside this scenario, so it is deliberately not part of this delta.

## Not included (by design)

- Vended **metrics** (the `AWSServiceRoleForAIDevOps` SLR) → `a5-service-linked-roles`
- The customer-managed KMS key caller policy + key policy for DevOps Agent's own data → `b5-customer-kms-key`
- Agent Space creation and service registration → [`b2`](../b2-installer/)
- Creating the destination log group / bucket / Firehose stream itself → destination owner, out of scope for an IAM scenario

## Sources

See `scenario.yaml` `docs:` for the AWS documentation pages every action is traced to:

- *Vended logs and metrics* — the delivery-source/destination/delivery model, the three supported destinations, `aidevops:AllowVendedLogDeliveryForResource` and the two resource scopes (Agent Space and registered service), the destination-side prerequisites (CloudWatch Logs resource policy, S3 bucket policy, the `AWSServiceRoleForLogDelivery` SLR for Firehose) and the SSE-KMS / Object Lock destination restrictions.
- *DevOps Agent IAM permissions* — the `aidevops` action surface the `AllowVendedLogDeliveryForResource` grant is drawn from, and the resource-ARN formats (`agentspace/*`, `service/*`) used throughout.
