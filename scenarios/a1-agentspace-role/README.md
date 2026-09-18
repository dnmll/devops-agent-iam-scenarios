# A1 — Agent Space role (primary account)

**Who this is for:** whoever creates the IAM role that **AWS DevOps Agent itself assumes** in the account an Agent Space lives in. This is a **Plane-A** role: no human ever assumes it, and it has no console login. The `aidevops.amazonaws.com` service principal assumes it to read your telemetry and resource inventory while it investigates.

The [CLI onboarding guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html) builds this role in three pieces:

| Piece | Where it comes from | In this repo |
|---|---|---|
| **Trust policy** | hand-written | [`policies/trust-policy.json`](./policies/trust-policy.json) |
| **Permissions** | AWS managed policy `AIDevOpsAgentAccessPolicy`, attached **by ARN** | *not a file here* — see below |
| **One inline permission** | `iam:CreateServiceLinkedRole` for AWS Resource Explorer | [`policies/slr-inline-policy.json`](./policies/slr-inline-policy.json) |

Replace `111122223333` with your account ID and `us-east-1` with your Region in both files.

```bash
aws iam create-role \
  --role-name DevOpsAgentRole-agentspace \
  --assume-role-policy-document file://policies/trust-policy.json

aws iam attach-role-policy \
  --role-name DevOpsAgentRole-agentspace \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy

aws iam put-role-policy \
  --role-name DevOpsAgentRole-agentspace \
  --policy-name ResourceExplorerServiceLinkedRole \
  --policy-document file://policies/slr-inline-policy.json
```

Hand the finished role's ARN to the installer (**[b2](../b2-installer/)**), which passes it to the service via `AssociateService` (`configuration.aws.assumableRoleArn`). If role creation and Agent Space creation are split across two people, the creation half is **[b1](../b1-iam-preprovisioner/)**.

## `policies/trust-policy.json` — the confused-deputy defence

```json
"Principal": { "Service": "aidevops.amazonaws.com" },
"Action": "sts:AssumeRole",
"Condition": {
  "StringEquals": { "aws:SourceAccount": "111122223333" },
  "ArnLike":      { "aws:SourceArn": "arn:aws:aidevops:us-east-1:111122223333:agentspace/*" }
}
```

A trust policy whose `Principal` is an AWS **service** is not scoped by the principal alone. `aidevops.amazonaws.com` is the *same* principal for every AWS customer, so `"Principal": {"Service": "aidevops.amazonaws.com"}` with no conditions says: **any** DevOps Agent resource, in **anyone's** account, can make the service assume this role and read your account with it. The service becomes the "confused deputy" — it holds your grant and acts on someone else's instruction.

The two conditions close that, and they answer different questions:

| Condition key | Question it answers | What it stops |
|---|---|---|
| `aws:SourceAccount` = your account ID | *Whose* resource triggered this? | Another customer's Agent Space borrowing your role. This is the cross-account half |
| `aws:SourceArn` `ArnLike` `…:agentspace/*` | *Which* resource triggered this? | Any **other** DevOps Agent resource type in your own account (`service/…`, and anything AWS adds later) assuming this role |

Keep **both**. `aws:SourceAccount` alone still lets every present and future DevOps Agent resource in your account use this role; `aws:SourceArn` alone is *usually* equivalent (the ARN contains the account id) but stops constraining the moment you widen the ARN pattern, which is a one-character edit. `check_required_conditions` asserts both keys on the `sts:AssumeRole` statement (`scenario.yaml` → `required_conditions`), so dropping either one fails `python3 -m tools.checks`.

Tightening further, if you can: replace `agentspace/*` with the specific Agent Space ARN once it exists. That is a chicken-and-egg ordering problem — the role must exist before `AssociateService` accepts it — so the documented flow is *create with the wildcard, narrow afterwards*. Note that `ArnLike` is required for the wildcard form; `StringEquals` on a pattern matches literally and would deny everything.

## Permissions: attach `AIDevOpsAgentAccessPolicy`, don't inline it

```
arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy
```

This repo deliberately ships **no copy** of that document:

- It is **30KB+** — hundreds of read actions across CloudWatch, X-Ray, Application Signals, Config, CloudTrail, Resource Explorer, Health, Systems Manager and more. A copy is unreviewable, and pasting it back as an inline policy risks the 10,240-character inline-policy limit.
- It is **AWS-owned and AWS-revised**. As DevOps Agent gains data sources, AWS updates the managed policy and every role that *attached* it picks the change up. A forked copy silently rots, and the failure mode is an investigation that quietly returns less than it should.
- Attaching by ARN is also what makes the **b1** guardrail meaningful: the pre-provisioner's `iam:AttachRolePolicy` is pinned by `iam:PolicyARN` to exactly this policy and `AIDevOpsOperatorAppAccessPolicy`.

### Restricted-template variant

If your organisation cannot accept the managed policy's full read surface, AWS documents a **restricted permissions template** — the same shape with the data sources you do not use removed, managed by you as a customer-managed policy and attached in its place. Everything else in this scenario is unchanged: same trust policy, same inline SLR grant. Two things to know before you take that route:

- The trade is **capability for surface**: DevOps Agent can only correlate what it can read, and a removed data source shows up as a weaker investigation, not as an `AccessDenied` you will notice.
- You now own the drift. New DevOps Agent data sources will not appear in your copy; re-diff it against `AIDevOpsAgentAccessPolicy` whenever AWS revises it.

A worked, checked restricted template is out of scope for this scenario (it belongs with the per-data-source scenarios); this README is the pointer, and the trust + SLR artifacts here are the parts it reuses verbatim.

## `policies/slr-inline-policy.json` — the one grant the managed policy omits

```json
"Action": "iam:CreateServiceLinkedRole",
"Resource": "arn:aws:iam::111122223333:role/aws-service-role/resource-explorer-2.amazonaws.com/AWSServiceRoleForResourceExplorer",
"Condition": { "StringEquals": { "iam:AWSServiceName": "resource-explorer-2.amazonaws.com" } }
```

DevOps Agent uses **AWS Resource Explorer** to discover what exists in the account it is investigating. Resource Explorer needs its own service-linked role (`AWSServiceRoleForResourceExplorer`); if it is not already present in the account, the first Resource Explorer call made on your behalf has to create it, and an `iam:*` grant is never part of an AWS managed *access* policy. Hence this one inline statement.

`iam:AWSServiceName` is the whole least-privilege story. Unconditioned, `iam:CreateServiceLinkedRole` creates a service-linked role for **any** AWS service — each one arriving with its own AWS-managed permissions policy attached, which is an privilege-acquisition primitive, not a read. Pinned to `resource-explorer-2.amazonaws.com` (note the `-2`: the modern Resource Explorer service prefix), the grant can do exactly one thing. The `Resource` ARN is scoped to the matching SLR path as well, so both halves have to line up. `check_required_conditions` asserts the condition; probes `sim-create-slr-for-lambda-denied`, `sim-create-slr-for-aidevops-denied` and `sim-create-slr-other-path-denied` are the proof.

If `AWSServiceRoleForResourceExplorer` already exists in the account (Resource Explorer is enabled), you may drop this inline policy entirely — nothing else depends on it. It is cheaper to leave it: creation is idempotent-ish (`InvalidInput` when the role exists), and it removes a first-investigation failure mode.

Note that the DevOps Agent **metrics** SLR (`AWSServiceRoleForAIDevOps`, created once at onboarding, without which Agent Space creation fails with `InvalidParameterException`) is **not** here — that is the installer's grant, in [b2](../b2-installer/). This role does not create it, and `sim-create-slr-for-aidevops-denied` asserts so.

## Validation harness

`terraform/` provisions IAM primitives only:

- **`iamscn-a1-agentspace`** — the deliverable, created with `policies/trust-policy.json` **verbatim** (only the account-id/Region substitutions applied — the HCL uses `file()` + `replace()`, never `jsonencode()`), with `AIDevOpsAgentAccessPolicy` attached by ARN and `policies/slr-inline-policy.json` put as an inline policy. That IAM accepts the trust document, with both conditions intact, is the live evidence for an artifact that cannot be simulated.
- **`iamscn-a1-slr`** — a probe anchor carrying the inline SLR policy as its candidate policy. It exists because `iamscn-a1-agentspace` is assumable by `aidevops.amazonaws.com` *only*: adding the CI role to its trust policy would mean the harness no longer deploys the deliverable.

Both roles carry the **`iamscn-boundary` permissions boundary**, which is mandatory for every `iamscn-*` role in the sandbox account and is **not part of the customer deliverable** — customers attach no boundary to this role. In the sandbox the Agent Space role's effective permissions are therefore `AIDevOpsAgentAccessPolicy ∩ iamscn-boundary`, i.e. a small fraction of the managed policy (the boundary's union is `aidevops:*`, scoped IAM reads, `iam:PassRole` on `iamscn-*` and three named service-linked roles). The boundary's `KnownServiceLinkedRolesOnly` statement already lists `resource-explorer-2.amazonaws.com`, so the inline grant survives the cap; the managed policy's CloudWatch/X-Ray/Config reads do not, which is deliberate — this harness tests the artifacts, and the artifacts are the trust policy and the SLR statement.

### Live validation coverage

| Artifact | How it is validated | Why |
|---|---|---|
| `policies/slr-inline-policy.json` | `simulate` probes (`iam:SimulateCustomPolicy`), the full allow/deny matrix in [`expected/probes.yaml`](./expected/probes.yaml) | It is an identity policy, so the simulator evaluates it directly |
| `policies/trust-policy.json` | static (`check_required_conditions` on both condition keys) + `terraform apply` creating the role with it verbatim | `iam:SimulateCustomPolicy` accepts **identity** policies only. A trust policy has a `Principal` and no `Resource`, so there is nothing to simulate — the same reason b5's KMS key policy is static-only |
| `AIDevOpsAgentAccessPolicy` | not validated here | AWS-owned and AWS-revised; this repo neither copies nor probes managed policies. Its attachment *is* exercised (the `terraform apply` attaches it by ARN) |

There are no `real` probes: the interesting call (`sts:AssumeRole` by `aidevops.amazonaws.com`) can only be made by the service, and every permission worth probing belongs to the AWS managed policy. Parliament's `MALFORMED` finding on the trust policy ("neither Resource nor NotResource") is suppressed with a written reason in `scenario.yaml` — parliament lints every document as an identity policy.

## Sources

- [Getting started — CLI onboarding guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html) — the role, its trust policy, the `AIDevOpsAgentAccessPolicy` attachment and the Resource Explorer SLR inline policy
- [Creating an Agent Space](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-creating-an-agent-space.html) — where the role ARN is handed to the service
- [DevOps Agent IAM permissions](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-devops-agent-iam-permissions.html) — managed policies and the restricted-template variant
