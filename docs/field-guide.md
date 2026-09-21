# Field guide to AWS DevOps Agent IAM

Everything in this document is something the [public AWS DevOps Agent documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/) does not tell you, and that this repository found out the hard way. Each scenario in `scenarios/<id>/` ships a raw, customer-usable policy plus a `scenario.yaml` contract that drives validation: static checks (`python3 -m tools.checks` — policy shape, parliament lint, required condition keys, manifest integrity) run on every change, and a human-gated GitHub Actions pipeline **deploys** the policy into a dedicated sandbox account with terraform, **probes** the real allow/deny behavior (`iam:SimulateCustomPolicy` / `iam:SimulatePrincipalPolicy` for the deny matrix, real STS and `aidevops` calls where they are safe), and **destroys** everything afterwards — see [sandbox-account.md](./sandbox-account.md). The findings below are the surprises that pipeline produced. Every one of them traces to an owning scenario README; none is theory.

Each section is written the same way: **symptom** you see → **root cause** → **what to do** → the scenario that owns it.

---

## 1. Un-onboarded accounts fail closed, so policy testing in a fresh account is meaningless

**Symptom.** You attach one of the console tier policies (or any `aidevops` policy) in a clean account, make a harmless read call such as `aws devops-agent list-asset-types`, and get `AccessDeniedException`. Tightening or widening the policy changes nothing. Even **account Administrator** credentials get the same denial, which is the tell.

**Root cause.** The `aidevops` API fail-closes in an account that has never been **onboarded to AWS DevOps Agent**. The service refuses the call before IAM evaluation is observable, so the result carries no information about your policy at all. This is service-side behavior, not an IAM defect — and the IAM policy simulator disagrees with it: in this repo's live runs the simulate probe for the same action reported `decision=allowed` while the real call returned `AccessDeniedException`.

**What to do.**

- Do not use a fresh, un-onboarded account as your policy test bed. Test in an account that has been onboarded and has an Agent Space, or test with the IAM policy simulator (`iam:SimulatePrincipalPolicy`), which evaluates the policy without going anywhere near the service.
- If you must diagnose a real `AccessDeniedException`, first establish whether the account is onboarded. If an account Administrator gets the same error, the cause is onboarding, not the policy.
- When building your own probe suite, expect real `aidevops` probes to be a false negative outside an onboarded account. This repo removed its one real read probe (`real-operator-list-asset-types`) for exactly this reason rather than weaken the policy to make it pass.

**Owning scenario:** [`b3-webapp-tiers`](../scenarios/b3-webapp-tiers/README.md#why-there-are-no-real-probes) (also noted in [`b4-secrets-manager`](../scenarios/b4-secrets-manager/README.md) and [`b7-log-delivery`](../scenarios/b7-log-delivery/README.md)).

---

## 2. Two actions are invisible to the IAM policy simulator

**Symptom.** Your policy contains an explicit `Allow` naming `logs:DeleteDeliverySource` or `aidevops:AllowVendedLogDeliveryForResource`, and the IAM policy simulator (API or the console's *Simulate policy*) returns **`implicitDeny`** for it. No error, no warning — just a denial that the policy text plainly contradicts. Sibling actions from the *same statement*, under the same wildcard and the same resource ARNs, simulate `allowed`.

**Root cause.** Both actions are missing from the simulator's action database. When `iam:SimulateCustomPolicy` is asked about an action it does not know, it does not raise an error — it silently returns `implicitDeny`, even when the policy under test grants that exact action. An unknown action can only be matched by `Action: "*"`, which needs no database entry, so the denial evaporates as soon as the policy is broadened to `"*"` — which is precisely why the result looks like a resource-scoping bug and is not one.

This repo confirmed it empirically rather than assuming it:

- `logs:DeleteDeliveryDestination` simulates `allowed` from the same `logs:DeleteDelivery*` statement with identical ARN scoping; only the `DeleteDeliverySource` sibling denies — no scoping mistake can produce that split.
- Rewriting the policy to `Action: "*"` makes the simulator match both actions and return `allowed`. A genuine resource or condition-key defect would still deny under `"*"`.
- **The how-to-detect trick:** put a *deliberately fake* action name (e.g. `logs:NoSuchActionAtAll`) in an `Allow` and simulate it. If the simulator answers `implicitDeny` with no error, you have reproduced the signature — the simulator treats "action I have never heard of" and "action not granted" identically. Run that control test alongside the suspect action: if both behave the same way, the suspect is an unknown action, not a policy bug.

**What to do.**

- **Do not use the policy simulator to verify vended-log-delivery permissions.** A denial on these two actions is not evidence of a defect. Verify with a real API call, or by reading the policy.
- If a *real* `logs:PutDeliverySource` fails with access denied, that one is genuine: the usual cause is a missing `aidevops:AllowVendedLogDeliveryForResource` grant, or having only one of its two resource scopes (see finding 6 and the b7 README).
- Do not broaden a policy to make a simulation pass. This repo removed the three affected probes and left the artifacts unchanged, because the grants were correct.

**Owning scenario:** [`b7-log-delivery`](../scenarios/b7-log-delivery/README.md#actions-the-iam-policy-simulator-cannot-validate).

---

## 3. Policy variables do not resolve under `SimulateCustomPolicy`

**Symptom.** The Operator Web App role's policy scopes every statement to `arn:aws:aidevops:*:*:agentspace/${aws:PrincipalTag/AgentSpaceId}` with `aws:ResourceAccount` equal to `${aws:PrincipalAccount}`. Simulate any action it grants and **every allow expectation fails** — the policy looks completely broken while being exactly what the AWS managed policy `AIDevOpsOperatorAppAccessPolicy` says.

**Root cause.** There is no session behind a simulator call. `${aws:PrincipalTag/AgentSpaceId}` is an IAM **policy variable** resolved at evaluation time from the calling principal's session tag; with no tagged session, the variable never resolves, the Resource ARN never matches, and the request is denied. `aws:ResourceAccount` compounds it: it is derived from the resource being touched, not something you can supply as a `ContextEntries` entry, so it never matches either. The only way to make the allow half simulate green is to weaken the artifact — the wrong fix.

**What to do.**

- Treat the **allow half** of any session-tag-scoped policy as statically verified, not simulation-verified. This repo asserts it with `check_required_conditions` (the expected value is the literal string `${aws:PrincipalAccount}`, so any pass that expanded or mangled the variable fails the build) plus `terraform apply` accepting the document verbatim.
- The **deny half** still simulates usefully, and it is variable-independent: an unresolved variable can only make a deny *more* certain, so deny probes cannot pass falsely in the "an extra grant slipped in" direction they exist to catch. Every probe in a4 is a deny.
- Verifying the scoping itself — right Agent Space allowed, wrong one denied — needs a real tagged session, i.e. a real Web App login through a real IdP. Plan for a manual check there; no simulator will do it.
- Remember the failure direction in production: an **untagged** session is a closed session, not an open one. If your IdP cannot assert the attribute, fix the mapping — never fall back to omitting the tag or adding a wildcard.
- Watch your own tooling too: literal string substitution over the raw JSON preserves `${...}`, but a template engine does not. `templatefile()` in terraform evaluates `${...}` as HCL and fails the plan on `${aws:PrincipalTag/AgentSpaceId}`; rebuilding the document with `jsonencode()` has the same problem.

**Owning scenario:** [`a4-operator-webapp-role`](../scenarios/a4-operator-webapp-role/README.md#policy-variables-and-the-substitution-pass).

---

## 4. One `CreateServiceLinkedRole` statement listing two SLRs allows the cross-combinations

**Symptom.** You write a single tidy statement for `iam:CreateServiceLinkedRole` with both service-linked-role ARNs in `Resource` and both service names in one `iam:AWSServiceName` list. It passes review, and it reads as "each of these two SLRs, for its own service". Simulate the mismatched pairings and they come back **`allowed`**: the log-delivery SLR ARN with `iam:AWSServiceName: aidevops.amazonaws.com`, and the metrics SLR ARN with `iam:AWSServiceName: delivery.logs.amazonaws.com`.

**Root cause.** IAM evaluates `Resource` and `Condition` **independently**. A statement listing *n* ARNs and *m* condition values authorizes all *n×m* combinations, not the *n* pairings you had in mind. Nothing in the statement correlates an ARN with its own service name.

**What to do.**

- **Write one statement per service-linked role**, each pairing its own `Resource` ARN with only its own `iam:AWSServiceName` value. That is the only shape that correlates the two, and it costs nothing.
- In this repo the two statements live in **separate artifacts**, because `check_required_conditions` asserts against every `Allow` statement in an artifact that grants the action — two statements in one file would make the two assertions mutually exclusive. Customers attach both together and the probes evaluate their union, which is what keeps the cross-pairing probes meaningful.
- Generalise the habit: any time a single statement carries multiple resource ARNs *and* multiple values of a condition key that is supposed to match them, assume the cross-product is granted and split the statement. The same reasoning drives the two service-principal crypto statements in `b5` (see finding 5) and the association/Agent-Space pattern in finding 6.
- This one was caught by live validation, not review: probes `sim-create-log-delivery-slr-with-aidevops-name-denied` and `sim-create-aidevops-slr-with-log-delivery-name-denied` returned `allowed` against the combined form, and the fix landed in PR #27 (`fix(a5-service-linked-roles): correlate each SLR ARN with its own service name`). Because `iam:AWSServiceName` is what IAM actually authorizes `CreateServiceLinkedRole` against, the cross combination is not directly exploitable today — but a policy that says something it does not mean is a latent defect.

**Owning scenario:** [`a5-service-linked-roles`](../scenarios/a5-service-linked-roles/README.md#why-two-statements-one-per-slr--and-not-one-statement-listing-both).

---

## 5. A customer-managed KMS key is a two-sided grant — and creation dry-runs it

**Symptom.** Two distinct failures, and which one you get tells you which half you missed:

- **`AccessDeniedException` at Agent Space creation / service registration time.** You granted the caller the KMS actions in its identity policy but the *key policy* does not allow that caller, or vice versa.
- **Creation succeeds, then investigations silently under-deliver later.** Nothing errors at configuration time; incident analysis, event correlation and root-cause generation just stop producing results, because DevOps Agent cannot read the data it encrypted on your behalf.

**Root cause.** KMS authorizes a request only when **both** the caller's identity policy and the **key's resource policy** allow it, so every grant here has two halves. On top of that, DevOps Agent uses **two different sets of credentials** against your key: your caller credentials for synchronous work (key validation and encryption at resource-creation time), and the `aidevops.amazonaws.com` **service principal** for asynchronous background work (investigations, incident analysis, event correlation, RCA generation). A service principal has no identity policy, so its half exists *only* in the key policy. Because DevOps Agent **dry-runs every required KMS action at configuration time**, a missing caller-side grant fails loudly and immediately — and a missing service-principal grant does not, which is why the second symptom is the dangerous one.

**What to do.**

- Set all four key-policy statements, not just the caller one: the caller statement, the unconditioned `DescribeKey` statement for the service principal (at validation time no resource ARN exists yet, so there is nothing for `aws:SourceArn` to match, and `DescribeKey` reveals only metadata), and **one crypto statement per resource type** — `agentspace/*` and `service/*` — each gated on `aws:SourceArn` *and* `kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn`. Keep them separate for the same correlation reason as finding 4: merged, a request sourced from an Agent Space would satisfy the condition while carrying a *service* encryption context.
- Fence the caller side with `kms:ViaService: aidevops.<region>.amazonaws.com`. That key only appears in the request context when the call reaches KMS *through* DevOps Agent, so the grant lets the holder use an encrypted Agent Space without ever being able to decrypt anything directly.
- Keep key administration (`kms:PutKeyPolicy`, `kms:CreateGrant`, `kms:DisableKey`, `kms:ScheduleKeyDeletion`) out of the caller's policy: the first two re-grant the key without the `ViaService` fence, and the last two are data-destroying — DevOps Agent does not re-encrypt under a new key, so a deleted key means permanent data loss.
- Know the one-way doors before you start: the key must be symmetric (`SYMMETRIC_DEFAULT` / `ENCRYPT_DECRYPT`, no multi-Region, no asymmetric), the `kmsKeyArn` parameter is **creation-time only** (you cannot add or change the CMK on an existing resource), and it must be the full key ARN — not an alias, not a key id.
- Note the key policy itself cannot be simulated: `iam:SimulateCustomPolicy` takes *identity* policies, and a document with `Principal` blocks is not valid input. There is no API for "would KMS allow this service-principal request with this encryption context", so review it against the documented pattern and audit actual use via the `aws-crypto-ec:aws:aidevops:arn` context in CloudTrail.

**Owning scenario:** [`b5-customer-kms-key`](../scenarios/b5-customer-kms-key/README.md).

---

## 6. Association actions dual-authorize, and tags live on Agent Spaces only

**Symptom.** Two related surprises:

- An association action (`AssociateService`, `UpdateAssociation`, `DisassociateService`) is denied even though your statement names the association ARN you are acting on.
- A policy that gates `aidevops` actions with an `aws:ResourceTag/...` condition works on Agent Space actions and **silently denies everything** on association actions, no matter how the associations are tagged.

**Root cause.** Association actions authorize against **both** the association ARN and the Agent Space ARN above it, so a statement naming only one of the two silently fails. And tags exist on **Agent Spaces only** — associations carry none, so an `aws:ResourceTag` condition on an association action can never match.

**What to do.**

- Scope association actions to `arn:aws:aidevops:<region>:<account>:agentspace/*`, which matches both Agent Space ARNs and the association ARNs beneath them. That single pattern is what `b2`'s `AssociationManagement`, `b3`'s `AgentSpaceFullAccess` and `b6`'s `DevOpsAgentResourceCrud` all rely on.
- When you need a **tag condition**, use the **two-statement pattern**: because the tag only exists on the Agent Space, the tag-conditioned statement cannot also be the one that authorizes the association ARN. A single tag-conditioned statement covering both is a guaranteed deny on association actions.
- The same two-ARN habit recurs across the service: `aidevops:AllowVendedLogDeliveryForResource` needs both `agentspace/*` and `service/*` (distinct resource types — a wildcard on one never matches the other, and a policy with only `agentspace/*` passes review and then fails the first time somebody enables logs on a registered service), and `b5`'s key policy needs one statement per resource type.
- Remember the Operator Web App role scopes by the `${aws:PrincipalTag/AgentSpaceId}` **session** tag, which is a different mechanism from resource tags — see finding 3.

**Owning references:** the *Cross-cutting test cases* section of [scenario-matrix.md](./scenario-matrix.md), plus [`b2-installer`](../scenarios/b2-installer/README.md), [`b3-webapp-tiers`](../scenarios/b3-webapp-tiers/README.md), [`b6-cicd-deployer`](../scenarios/b6-cicd-deployer/README.md) and [`b7-log-delivery`](../scenarios/b7-log-delivery/README.md#why-allowvendedlogdeliveryforresource-needs-two-resource-arns).

---

## Scenario catalog

Full statuses and deliverable summaries are in [scenario-matrix.md](./scenario-matrix.md). Each README is the authoritative source for the findings above.

### Plane A — roles the DevOps Agent service assumes

| Scenario | What it delivers | Findings it owns |
|---|---|---|
| [`a1-agentspace-role`](../scenarios/a1-agentspace-role/README.md) | Agent Space role: trust policy + `AIDevOpsAgentAccessPolicy` + Resource Explorer SLR inline | — |
| [`a2-secondary-account-role`](../scenarios/a2-secondary-account-role/README.md) | Cross-account (member account) role, trust scoped to the monitoring account's Agent Space | — |
| [`a3-elevated-directed-actions`](../scenarios/a3-elevated-directed-actions/README.md) | Elevated role for operator-approved remediation, tag-gated worked template | — |
| [`a4-operator-webapp-role`](../scenarios/a4-operator-webapp-role/README.md) | Operator Web App role, `${aws:PrincipalTag/AgentSpaceId}` session-tag scoped | 3 |
| [`a5-service-linked-roles`](../scenarios/a5-service-linked-roles/README.md) | `AWSServiceRoleForAIDevOps` + `AWSServiceRoleForLogDelivery` creation grants | 4 |

### Plane B — human / CI identities

| Scenario | What it delivers | Findings it owns |
|---|---|---|
| [`b1-iam-preprovisioner`](../scenarios/b1-iam-preprovisioner/README.md) | Split-duty IAM admin: role/policy CRUD scoped to `DevOpsAgentRole-*`, no PassRole | — |
| [`b2-installer`](../scenarios/b2-installer/README.md) | Agent Space creation and configuration, scoped PassRole, metrics SLR | 6 |
| [`b3-webapp-tiers`](../scenarios/b3-webapp-tiers/README.md) | Administrator / operator / read-only console and Web App tiers | 1, 6 |
| [`b4-secrets-manager`](../scenarios/b4-secrets-manager/README.md) | Write-only third-party credential registrar under a `devops-agent/*` name prefix | — |
| [`b5-customer-kms-key`](../scenarios/b5-customer-kms-key/README.md) | Customer-managed CMK: caller identity policy + KMS key policy | 5 |
| [`b6-cicd-deployer`](../scenarios/b6-cicd-deployer/README.md) | DevOps Agent resources via Cloud Control API / Terraform `awscc` | 6 |
| [`b7-log-delivery`](../scenarios/b7-log-delivery/README.md) | Vended log delivery to CloudWatch Logs, S3 or Firehose (one policy per destination) | 2, 6 |
