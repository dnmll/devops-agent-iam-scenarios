# A3 — Elevated role for directed actions (remediation)

**Who this is for:** whoever creates the IAM role that AWS DevOps Agent assumes to **change** things in an AWS account after an operator approves it. This is a **Plane-A** role: no human ever assumes it, and it has no console login. You register it on an account association as `agentElevatedRoleArn`, and from then on it is **the ceiling of everything the agent could ever do in that account**.

> [!IMPORTANT]
> [`policies/example-remediation-policy.json`](./policies/example-remediation-policy.json) is a **TEMPLATE, not a recommendation.** It grants two narrow, tag-gated actions purely to demonstrate the *shape* of a least-privilege ceiling. Do not ship it as-is and do not treat its action list as "what the agent needs" — the agent needs nothing by default. Replace both statements with the operations your own runbooks actually contain. See **[Scope this down](#scope-this-down)**.

Directed actions are the agent's mutating half, and they are **disabled by default**. The [documented safety model](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-working-with-directed-actions.html) is defence in depth, with four independent layers — this scenario is only the third:

| Layer | Who owns it | Where |
|---|---|---|
| 1. Directed actions enabled on the Agent Space (`preferences.elevatedActionsEnabled`) | the installer / Agent Space admin | **[b2](../b2-installer/)** (`aidevops:UpdateAgentSpace`) |
| 2. An elevated role registered per association (`agentElevatedRoleArn`) | the installer, with `iam:PassRole` pinned by `iam:PassedToService` | **[b2](../b2-installer/)** |
| **3. What that role is allowed to do** | **you, here** | **this scenario** |
| 4. Per-action operator approval at execution time, in chat | the operator | **[b3](../b3-webapp-tiers/)** (operator tier) |

Layer 1 is the master switch: with it off, a registered elevated role has no effect. Layer 4 is not optional and cannot be automated — if the agent reaches a mutating tool outside chat (during an autonomous investigation, say), the call fails rather than silently executing. Register no elevated role at all and the agent still investigates; it just hands you manual remediation steps instead of an executable change.

The two artifacts here are the two halves of the role:

| Piece | In this repo | What it is |
|---|---|---|
| **Trust policy** | [`policies/trust-policy.json`](./policies/trust-policy.json) | Fixed shape — the service principal plus **three** STS actions and the confused-deputy conditions. Copy this one |
| **Permission policy** | [`policies/example-remediation-policy.json`](./policies/example-remediation-policy.json) | A worked **example** ceiling. Rewrite this one |

Replace `111122223333` with your account ID and `us-east-1` with your Region in both files.

```bash
aws iam create-role \
  --role-name DevOpsAgent-ElevatedAction-remediation \
  --assume-role-policy-document file://policies/trust-policy.json

# ...after you have replaced the example statements with your own runbook's:
aws iam put-role-policy \
  --role-name DevOpsAgent-ElevatedAction-remediation \
  --policy-name ExampleRemediation \
  --policy-document file://policies/example-remediation-policy.json
```

Then hand the role ARN to the installer (**[b2](../b2-installer/)**), which sets `agentElevatedRoleArn` on the association. The docs recommend a recognisable name such as `DevOpsAgent-ElevatedAction-*` so elevated roles are easy to audit; the service does not require a specific name.

## `policies/trust-policy.json` — three STS actions, not one

```json
"Principal": { "Service": "aidevops.amazonaws.com" },
"Action": [ "sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession" ],
"Condition": {
  "StringEquals": { "aws:SourceAccount": "111122223333" },
  "ArnLike":      { "aws:SourceArn": "arn:aws:aidevops:us-east-1:111122223333:agentspace/*" }
}
```

This is the **[a1](../a1-agentspace-role/) trust shape plus two actions**, and the two extras are the whole difference:

| Action | Why the elevated path needs it |
|---|---|
| `sts:AssumeRole` | The assume itself |
| `sts:SetSourceIdentity` | The assumed session carries a **source identity naming the approving operator**, which is what makes every directed action attributable to a human in CloudTrail. This is an audit control, not a convenience |
| `sts:TagSession` | The session is tagged when the credentials are minted for an approved operation |

**Omitting either extra action is a trap the documentation calls out explicitly:** registration still validates, `agentElevatedRoleArnStatus` still reports `valid`, and directed actions then fail later at **credential time**. The failure is nowhere near the mistake, which is why this repo deploys the trust document **verbatim** in its harness rather than rebuilding it (see [Validation harness](#validation-harness)).

The confused-deputy conditions do the same job as in a1, and they matter more here because what is at stake is writes rather than reads. `aidevops.amazonaws.com` is the *same* principal for every AWS customer, so an unconditioned elevated trust policy says "any DevOps Agent Agent Space, in anyone's account, may make the service mutate my resources":

| Condition key | Question it answers | What it stops |
|---|---|---|
| `aws:SourceAccount` = your account ID | *Whose* Agent Space triggered this? | Another customer's Agent Space borrowing your remediation ceiling |
| `aws:SourceArn` `ArnLike` `…:agentspace/*` | *Which* resource triggered this? | Any other DevOps Agent resource type in your own account (present or future) assuming it |

Keep **both**; `check_required_conditions` asserts both keys on all three STS actions, so dropping one fails `python3 -m tools.checks`. The Region in `aws:SourceArn` **must match your Agent Space's Region** — a Region mismatch is the documented cause of "role is `valid` but directed actions still fail". If you run Agent Spaces in several Regions, use `arn:aws:aidevops:*:111122223333:agentspace/*`, or better, pin the specific Agent Space ARN once it exists.

## `policies/example-remediation-policy.json` — a template, read the shape not the actions

```json
{ "Sid": "ExampleRebootTaggedInstances",
  "Action": "ec2:RebootInstances",
  "Resource": "arn:aws:ec2:us-east-1:111122223333:instance/*",
  "Condition": { "StringEquals": { "aws:ResourceTag/DevOpsAgentRemediation": "allowed" } } }
```

Two statements, two actions: `ec2:RebootInstances` and `lambda:UpdateFunctionConfiguration`. Both are **resource-scoped by ARN** *and* **gated on an explicit resource-tag opt-in**. The pattern worth copying is those three properties together:

1. **One statement per runbook operation.** The example's actions were chosen because they are the least interesting possible remediations: a reboot is recoverable, and a Lambda configuration change (memory, timeout, concurrency) is reversible. Yours will differ — start from the operations you expect operators to actually approve, and add nothing "while you are in there".
2. **`aws:ResourceTag` as the opt-in.** With the tag condition, bringing a resource into the agent's reach is a **tagging decision its owner makes**, not an IAM edit needing a policy review. Removing the tag removes the reach. Tag key and value here (`DevOpsAgentRemediation: allowed`) are arbitrary — use whatever your organisation already tags with.
3. **Nothing irreversible, nothing that acquires privilege.** No delete-class action, no `iam:*`, no `iam:PassRole`.

### Scope this down

Before you use this file for anything real:

- [ ] **Delete both example statements** unless your runbooks genuinely contain those two operations. An empty permission policy is the correct starting point; every statement you add is a permanent widening of the ceiling.
- [ ] **Narrow the resource ARNs.** `instance/*` and `function:*` are as wide as the account. Pin prefixes, or specific resources, wherever you can.
- [ ] **Keep (or replace) the tag gate.** If `aws:ResourceTag` does not fit, scope by resource-name prefix instead — but do not simply drop the condition and leave `Resource` wide.
- [ ] **Register one elevated role per account, and grant each only what that account's runbooks need.** Registration is optional per association: accounts with no elevated role stay read-only, which is the right default for most of them.
- [ ] **Consider an SCP or a permissions boundary on the role** as an independent cap. Both apply to the elevated role like any other role, and unlike the role's own policy they are not editable by whoever maintains the runbooks.

### Why not the AWS managed policy?

AWS also ships `AIDevOpsAgentActionsPolicy` (`arn:aws:iam::aws:policy/AIDevOpsAgentActionsPolicy`) for this role, and it is documented as **Option 1**. Know what it is before you reach for it: it grants **all actions on all resources**, minus the identity/credential/organization services (`account:*`, `cognito-identity:*`, `iam:*`, `identitystore:*`, `organizations:*`, `ram:*`, `rolesanywhere:*`, `sso:*`, `sts:*`, with a handful of read-only exceptions added back) — so the role cannot manage identities or obtain further access, but it *can* touch everything else, and delete-class actions are inside its ceiling. It is a reasonable choice if you are relying on the agent's own guardrails plus operator approval as your primary controls, and a poor one if your compliance story needs the ceiling itself to be narrow. This scenario is the worked form of **Option 2** (customer-managed, least privilege), which is why the managed policy is referenced here and not copied — same reasoning as a1's `AIDevOpsAgentAccessPolicy`.

### What the service refuses anyway

Independent of what you grant, the agent enforces its own guardrails on the SDK operations it will invoke for a directed action. Operator approval does **not** override them:

- **No delete-class operations** — deleting an instance, bucket, table, function or stack. Humans delete things with their own credentials.
- **No mutating permissions boundaries** — `iam:PutRolePermissionsBoundary`, `iam:DeleteRolePermissionsBoundary`, `iam:PutUserPermissionsBoundary`, `iam:DeleteUserPermissionsBoundary`. Boundaries are a control *over* the agent, so the agent cannot change them.
- **No operations requiring `iam:PassRole`** — launching an instance with an instance profile, creating a function with an execution role, starting a task with a task role. Passing a role indirectly extends what a service does on your behalf.

Approved actions are additionally narrowed by a **session policy** the service composes for the specific operation and resource the operator approved, from a curated action list; the approval is single-use or time-boxed (up to 4 hours) and cannot be reused for a different operation or resource. So the role's ceiling is never the agent's effective permission for any one call.

This scenario's `forbidden_actions` nonetheless assert the delete-class and privilege-acquiring actions absent from the artifacts (`iam:*`, `kms:ScheduleKeyDeletion`, `s3:DeleteBucket`, `ec2:TerminateInstances`, `lambda:DeleteFunction`, `iam:PassRole`, the boundary mutations, `secretsmanager:GetSecretValue`, `aidevops:UpdateAssociation`/`AssociateService`) — belt and braces, deliberately. The guardrails are the service's to change, the ceiling is yours to answer for in an audit, and **a template gets copied and edited**. `python3 -m tools.checks` failing is a cheaper way to learn you widened it than a CloudTrail review is.

## Validation harness

`terraform/` provisions IAM primitives only:

- **`iamscn-a3-elevated`** — the deliverable, created with `policies/trust-policy.json` **verbatim** (account-id/Region substitutions only — the HCL uses `file()` + `replace()`, never `jsonencode()`) and the example remediation policy put inline. That IAM accepts the trust document with all three STS actions and both conditions intact is the live evidence for an artifact that cannot be simulated — and the failure mode it guards against (a trust policy that registers as `valid` and then breaks at credential time) is precisely one a hand-rebuilt document would introduce.
- **`iamscn-a3-remediation`** — a probe anchor carrying the example remediation policy as its candidate policy. It exists because `iamscn-a3-elevated` is assumable by `aidevops.amazonaws.com` *only*: adding the CI role to its trust policy would mean the harness no longer deploys the deliverable.

No EC2 instance and no Lambda function are created. Every probe is `kind: simulate`, so nothing outside IAM needs to exist, and provisioning something for a sandbox role to reboot would add blast radius without adding evidence. Registering the role on an association (`agentElevatedRoleArn`) is an installer API call, never terraform's.

Both roles carry the **`iamscn-boundary` permissions boundary**, mandatory for every `iamscn-*` role in the sandbox and **not part of the customer deliverable**. The boundary's union is `aidevops:*` plus scoped IAM — it grants no `ec2` or `lambda` at all, so the deployed role's *effective* permissions in the sandbox are empty. That is exactly why the probes use `iam:SimulateCustomPolicy` against the artifact (artifact fidelity, no boundary intersection) rather than `iam:SimulatePrincipalPolicy` against the deployed role, which would report a false `implicitDeny` for every allow.

### Live validation coverage

| Artifact | How it is validated | Why |
|---|---|---|
| `policies/example-remediation-policy.json` | `simulate` probes (`iam:SimulateCustomPolicy`), full allow/deny matrix in [`expected/probes.yaml`](./expected/probes.yaml) | It is an identity policy, so the simulator evaluates it directly. Resource-tag conditions are simulated by supplying `aws:ResourceTag/DevOpsAgentRemediation` as a request context entry |
| `policies/trust-policy.json` | static (`check_required_conditions` on both condition keys × all three STS actions) + `terraform apply` creating the role with it verbatim | `iam:SimulateCustomPolicy` accepts **identity** policies only. A trust policy has a `Principal` and no `Resource`, so there is nothing to simulate — same reason as a1's trust policy and b5's KMS key policy |
| `AIDevOpsAgentActionsPolicy` | not validated here | AWS-owned and AWS-revised; this repo neither copies nor probes managed policies |

There are no `real` probes. The only interesting runtime call — the service assuming this role to execute an approved directed action — can be made **only by `aidevops.amazonaws.com`, after a human approves in chat**. There is nothing a CI probe can drive, by design: that human gate is the feature. Parliament's `MALFORMED` finding on the trust policy ("neither Resource nor NotResource") is suppressed with a written reason in `scenario.yaml`, as in a1 — parliament lints every document as an identity policy.

The deny half of the probe matrix is where the real assurance is: `sim-reboot-untagged-instance-denied` and `sim-reboot-wrong-tag-value-denied` prove the tag gate is load-bearing, and the delete-class / IAM-write denies prove the template's ceiling is where the README says it is.

## Sources

- [Working with directed actions](https://docs.aws.amazon.com/devopsagent/latest/userguide/working-with-devops-agent-working-with-directed-actions.html) — the safety model, the elevated role trust policy (including the three STS actions), the two permission-policy options, validation lifecycle (`agentElevatedRoleArnStatus`) and the operations the agent refuses
- [DevOps Agent IAM permissions](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-devops-agent-iam-permissions.html) — "To let the agent perform directed actions in your AWS accounts, register an elevated IAM role on the account association"
