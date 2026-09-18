# A2 — Secondary (cross-account) Agent Space role

**Who this is for:** whoever creates the IAM role in a **secondary account** — an account that has no Agent Space of its own, but whose resources you want DevOps Agent to investigate. This is the cross-account variant of **[a1](../a1-agentspace-role/)**: same **Plane-A** shape (no human assumes it, no console login, `aidevops.amazonaws.com` assumes it), same two hand-written artifacts, one condition-value difference that is the entire scenario.

## Two accounts, two placeholders

The single most common way to get this role wrong is to copy a1's trust policy and leave the account id alone. Both files here use placeholders, and they mean **different accounts**:

| Placeholder | Account | What lives there | Appears in |
|---|---|---|---|
| `222233334444` | **Monitoring** (primary) account — AWS docs call it `<MONITORING_ACCOUNT_ID>` | The Agent Space, the a1 role, the Operator App role | [`policies/trust-policy.json`](./policies/trust-policy.json) — **both** `aws:SourceAccount` and `aws:SourceArn` |
| `111122223333` | **Member** (secondary / source) account — AWS docs call it `<EXTERNAL_ACCOUNT_ID>` | **This role**, the resources under investigation, this account's Resource Explorer SLR | [`policies/slr-inline-policy.json`](./policies/slr-inline-policy.json) — the SLR `Resource` ARN |

Read that table as: *the trust policy points **outward** at the account that drives the agent; the inline policy points **inward** at this account's own resources.* Nothing in this scenario names both accounts in the same statement.

> `111122223333` is the same placeholder a1 uses for *its* local account, on purpose — every scenario in this repo means "the account the artifact is deployed into" by `111122223333`. a2 is the only scenario that needs a *second* account id, hence `222233334444` for the remote/monitoring side. Region stays `us-east-1`; the Agent Space's Region (not the member account's) is what goes in the `aws:SourceArn`, because the ARN describes the Agent Space.

The [CLI onboarding guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html) builds this role in step 4 ("Associate additional source accounts"), in three pieces — identical to a1:

| Piece | Where it comes from | In this repo |
|---|---|---|
| **Trust policy** | hand-written | [`policies/trust-policy.json`](./policies/trust-policy.json) |
| **Permissions** | AWS managed policy `AIDevOpsAgentAccessPolicy`, attached **by ARN** | *not a file here* — see [a1's note](../a1-agentspace-role/README.md#permissions-attach-aidevopsagentaccesspolicy-dont-inline-it) |
| **One inline permission** | `iam:CreateServiceLinkedRole` for AWS Resource Explorer, in **this** account | [`policies/slr-inline-policy.json`](./policies/slr-inline-policy.json) |

Run these **with credentials for the member account** (`111122223333`):

```bash
aws iam create-role \
  --role-name DevOpsAgentCrossAccountRole \
  --assume-role-policy-document file://policies/trust-policy.json

aws iam attach-role-policy \
  --role-name DevOpsAgentCrossAccountRole \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy

aws iam put-role-policy \
  --role-name DevOpsAgentCrossAccountRole \
  --policy-name AllowCreateServiceLinkedRoles \
  --policy-document file://policies/slr-inline-policy.json
```

Then switch **back to the monitoring account** (`222233334444`) and associate the account. The association is a `sourceAws` configuration with `accountType: source`, and the role ARN it carries is a **member-account** ARN:

```bash
aws devops-agent associate-service \
  --agent-space-id <AGENT_SPACE_ID> \
  --service-id aws \
  --configuration '{
    "sourceAws": {
      "accountId": "111122223333",
      "accountType": "source",
      "assumableRoleArn": "arn:aws:iam::111122223333:role/DevOpsAgentCrossAccountRole"
    }
  }'
```

The `aidevops:AssociateService` permission for that call is the **installer's** (**[b2](../b2-installer/)**), held in the monitoring account. This role grants no `aidevops:*` at all — probes `sim-associate-service-denied` and `sim-create-agentspace-denied` assert that.

## When you need a secondary account role

Every AWS account whose resources the agent should read needs its own role, because IAM roles are account-local and the agent has nothing to relay through. Concretely, you need one per member account when:

- **Your workload is multi-account** — the classic one. App accounts per environment (`prod`, `staging`) or per team, with the Agent Space in a central tooling/observability account. Without a role in the app account the agent sees the tooling account's (empty) topology.
- **The incident crosses an account boundary** — a shared-services account holds the VPC, transit gateway, central RDS or the KMS keys the app account depends on. [Limiting Agent Access](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-limiting-agent-access-in-an-aws-account.html) is explicit that if you cut supporting infrastructure out of scope, "the agent may not be able to identify root causes that originate in supporting infrastructure outside your defined boundaries". An account boundary is the bluntest possible version of that cut.
- **You want per-account read scoping** — the member account's own admins control what its role grants. This is the natural place to use the restricted permissions template (attach a customer-managed policy in place of `AIDevOpsAgentAccessPolicy`); `prod` can be read-only-narrow while `staging` gets the full managed policy. The trust policy is unchanged either way.

You do **not** need one for the monitoring account itself — that is a1, associated as `accountType: monitor`. And you do not need a role in an account just because it appears in the topology: the topology shows what the agent *discovered*, not what it *can read*. IAM is the only real limit.

### One role per account, not one per Agent Space

The trust policy below uses `agentspace/*`, so any Agent Space in the monitoring account can drive this role. If two Agent Spaces in the same monitoring account must not share read access to this member account, you need **two roles here**, each with the specific Agent Space ARN in `aws:SourceArn` (see "Tightening" below) — the wildcard cannot express "this Agent Space but not that one".

## `policies/trust-policy.json` — the confused-deputy defence, pointed elsewhere

```json
"Principal": { "Service": "aidevops.amazonaws.com" },
"Action": "sts:AssumeRole",
"Condition": {
  "StringEquals": { "aws:SourceAccount": "222233334444" },
  "ArnLike":      { "aws:SourceArn": "arn:aws:aidevops:us-east-1:222233334444:agentspace/*" }
}
```

Three things to notice, and they are the whole reason this scenario exists separately from a1:

1. **There is no cross-account principal.** No `"Principal": {"AWS": "arn:aws:iam::222233334444:root"}`, no role-chaining through a1's role, and **no `sts:ExternalId`**. `aidevops.amazonaws.com` assumes this role *directly*, from the monitoring account's Agent Space. The service principal is the only principal, in a1 and here alike.
2. **Therefore the two conditions are the *only* thing scoping the grant** — and unlike a1, they name an account that is **not** this one. In a1, `aws:SourceAccount` = your own account, so even an unconditioned trust policy would at least be limited to DevOps Agent resources somewhere. Here, `aws:SourceAccount: "222233334444"` is the sole statement of *whose* agent may read this account. Get it wrong and you have either a broken association or an open door:

   | Mistake | Symptom |
   |---|---|
   | left as the member account (`111122223333`, i.e. copy-pasted a1) | the association never validates — `AccessDenied` on every assume, because the real source account is the monitoring account. See "How malformed trust surfaces" below |
   | conditions removed entirely | **any** AWS customer's Agent Space can read this account. This is the confused-deputy hole, and in a member account it is wider than in a1: nothing else in the chain checks the caller |
   | `aws:SourceAccount` right, `aws:SourceArn` left pointing at `111122223333` | same as the first row — the two keys must agree, and both must name the monitoring account |

3. **`ArnLike` is required** for the `agentspace/*` form; `StringEquals` on a pattern matches literally and denies everything. Same trap as a1.

`check_required_conditions` asserts both keys *and* both literal values (`scenario.yaml` → `required_conditions`), so a copy-paste of a1's values into this file fails `python3 -m tools.checks` rather than shipping.

**Tightening, if you can:** replace `agentspace/*` with the specific Agent Space ARN, which the guide's step-4 snippet actually shows as `agentspace/<AGENT_SPACE_ID>`. In a member account this is materially easier than in a1: the Agent Space already exists before you create this role (it must, since you created it in step 1 of onboarding), so there is no chicken-and-egg ordering problem to work around. The wildcard here is the *repo's* placeholder for an id we cannot know; prefer the exact ARN in production.

## How malformed trust surfaces — the association validation log event

A wrong `aws:SourceAccount` does not fail at `create-role` time (IAM does not validate that the account exists, and there is nothing to validate against) and it does not necessarily fail at `associate-service` time either. It surfaces **later, asynchronously**, when the service tries to assume the role and cannot.

The observable signal is the vended log event **“Association Validation status updates”**, documented on the [Vended logs and metrics](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-vended-logs-and-metrics.html) page:

> When a Agent space association (typical primary/secondary account), validation status changes from valid to invalid and vice versa (for example, caused by malformed role, that is not assumable by the service).

Worth internalising:

- It is emitted on **both** transitions (`ERROR` when an association goes valid → invalid, `INFO` on the way back), so the fix is visible in the same log stream as the break. Fixing `aws:SourceAccount` flips the association back without re-running `associate-service`.
- **“Malformed role” means "not assumable by the service"** — which covers a wrong `aws:SourceAccount`/`aws:SourceArn`, a deleted role, a trust policy that named an IAM principal instead of the service, and a permissions boundary or SCP in the member account that denies the assume. All of those look the same from the monitoring account: the association goes invalid and the member account's resources quietly drop out of investigations.
- The useful schema fields are `optional_association_id`, `optional_account_id` (the member account), `optional_level` and `optional_error_message` — enough to tell *which* secondary account broke without opening the console.
- **It is off by default.** Vended log delivery must be configured (log type `APPLICATION_LOGS`, on the Agent Space ARN) before any of this is visible — that is **[b7](../b7-log-delivery/)**'s deliverable, and this is the concrete reason to do b7 *before* you onboard secondary accounts. Without it, a broken member-account association is a silent degradation: investigations return less, and nothing errors.

`aws devops-agent list-associations --agent-space-id <AGENT_SPACE_ID>` is the synchronous cross-check (it reports each association's status); the log event is what tells you *when* it changed.

## `policies/slr-inline-policy.json` — the member account's own SLR

```json
"Action": "iam:CreateServiceLinkedRole",
"Resource": "arn:aws:iam::111122223333:role/aws-service-role/resource-explorer-2.amazonaws.com/AWSServiceRoleForResourceExplorer",
"Condition": { "StringEquals": { "iam:AWSServiceName": "resource-explorer-2.amazonaws.com" } }
```

Identical to a1's, with the **member** account id — because Resource Explorer discovers resources per account and its service-linked role is account-local. The monitoring account's SLR (created via a1's inline policy) does nothing for this account. `sim-create-slr-in-monitoring-account-denied` is the probe that pins the direction: correct service name, monitoring-account SLR path, denied.

`iam:AWSServiceName` carries the same least-privilege weight as in a1 — unconditioned, `iam:CreateServiceLinkedRole` creates a service-linked role for **any** AWS service, each arriving with its own AWS-managed permissions policy. Pinned to `resource-explorer-2.amazonaws.com` (note the `-2`), plus the exact SLR path in `Resource`, both halves have to line up.

The DevOps Agent **metrics** SLR (`AWSServiceRoleForAIDevOps`) is **not** here and never belongs in a member account — it is created once, in the monitoring account, by the installer ([b2](../b2-installer/)). `sim-create-slr-for-aidevops-denied` asserts that. As in a1, if Resource Explorer is already enabled in this account you may drop the inline policy entirely.

## Permissions: attach `AIDevOpsAgentAccessPolicy`, don't inline it

Unchanged from a1 — this repo ships no copy of the managed policy (30KB+, AWS-owned, AWS-revised); see [a1's section](../a1-agentspace-role/README.md#permissions-attach-aidevopsagentaccesspolicy-dont-inline-it) for the full reasoning and the restricted-template variant. Two member-account-specific notes:

- The **restricted template is most useful here.** Per-account read scoping (services, resource ARN patterns, tags, `aws:RequestedRegion`) is exactly what [Limiting Agent Access](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-limiting-agent-access-in-an-aws-account.html) documents, and a member account is where "the agent may read `prod` but only these tagged resources" is a policy you can actually write. The console offers it as *Create a new DevOps Agent policy using a template* when you edit a secondary account.
- Whatever you attach, AWS's own **permission guardrail** still applies: DevOps Agent passes a session policy at assume-role time, so effective permissions are `your role's policies ∩ guardrail`. Adding a write action to a member-account role does not make the agent able to write. Equally, a permission the guardrail allows but the managed policy omits needs an explicit inline grant.

## Validation harness

`terraform/` provisions IAM primitives only:

- **`iamscn-a2-secondary`** — the deliverable, created with `policies/trust-policy.json` **verbatim**, with `AIDevOpsAgentAccessPolicy` attached by ARN and `policies/slr-inline-policy.json` put as an inline policy. The HCL uses `file()` + `replace()`, never `jsonencode()`.
- **`iamscn-a2-slr`** — a probe anchor carrying the inline SLR policy as its candidate policy, because `iamscn-a2-secondary` is assumable by `aidevops.amazonaws.com` only.

### The single-account sandbox, and what the harness substitutes

The sandbox is **one** AWS account, so it plays the **member** account: `111122223333` → the real sandbox account id, in the SLR policy and in the trust policy (where it does not appear). `222233334444` is **deliberately not substituted** — rewriting it to the sandbox id would collapse this artifact back into a1's same-account trust policy and the scenario would stop testing anything cross-account.

That is fine, and it is more than a workaround: `aws:SourceAccount` and `aws:SourceArn` are plain string conditions, so IAM stores a foreign — even non-existent — account id in them without validating it. `terraform apply` accepting this document is therefore live evidence that a **genuinely cross-account** trust policy is well-formed, which is the one thing a single-account sandbox can honestly prove about it. What it cannot prove is the end-to-end assume: that needs a second AWS account and an Agent Space, which is out of scope for this harness (see "Not covered" below).

Both roles carry the **`iamscn-boundary`** permissions boundary, mandatory for every `iamscn-*` role in the sandbox and **not part of the customer deliverable**. The boundary's `KnownServiceLinkedRolesOnly` statement lists `resource-explorer-2.amazonaws.com`, so the inline grant survives the cap; the managed policy's CloudWatch/X-Ray/Config reads do not, which is deliberate — this harness tests the artifacts.

### Live validation coverage

| Artifact | How it is validated | Why |
|---|---|---|
| `policies/slr-inline-policy.json` | `simulate` probes (`iam:SimulateCustomPolicy`), full matrix in [`expected/probes.yaml`](./expected/probes.yaml), including the monitoring-account SLR path denial | It is an identity policy, so the simulator evaluates it directly |
| `policies/trust-policy.json` | static (`check_required_conditions` on both keys **and** both literal monitoring-account values) + `terraform apply` creating the role with it verbatim | `iam:SimulateCustomPolicy` accepts **identity** policies only. A trust policy has a `Principal` and no `Resource`, so there is nothing to simulate |
| `AIDevOpsAgentAccessPolicy` | not validated here | AWS-owned and AWS-revised; this repo neither copies nor probes managed policies. Its attachment *is* exercised |

**Not covered by any check in this repo:** that `aidevops.amazonaws.com`, driven from a real Agent Space in a *different* account, actually assumes this role, and that the "Association Validation status updates" event fires when it cannot. Both need a second AWS account plus a live Agent Space; the sandbox has neither. Everything in the "How malformed trust surfaces" section above is documentation-sourced, not machine-checked here.

There are no `real` probes, for a1's reasons plus one more: the only interesting call is an assume the CI role is structurally unable to make. Parliament's `MALFORMED` finding on the trust policy ("neither Resource nor NotResource") is suppressed with a written reason in `scenario.yaml`.

## Sources

- [Getting started — CLI onboarding guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html) — step 4, "(Optional) Associate additional source accounts": `DevOpsAgentCrossAccountRole`, the cross-account trust policy with `<MONITORING_ACCOUNT_ID>` in both conditions, the `<EXTERNAL_ACCOUNT_ID>`-scoped SLR inline policy, and the `sourceAws` / `accountType: source` association
- [Limiting Agent Access in an AWS Account](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-limiting-agent-access-in-an-aws-account.html) — primary vs secondary account roles, the permission guardrail (session policy) ceiling, per-account service/resource/Region scoping, the policy template for secondary accounts
- [Vended logs and metrics](https://docs.aws.amazon.com/devopsagent/latest/userguide/configuring-integrations-and-knowledge-vended-logs-and-metrics.html) — the "Association Validation status updates" log event and the log schema fields it populates
