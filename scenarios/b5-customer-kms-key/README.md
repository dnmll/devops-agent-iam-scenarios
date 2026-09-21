# B5 — Customer-managed KMS key: caller policy + key policy

**Who this is for:** the person (or pipeline identity) who creates an **Agent Space** or **registers a service** encrypted with a **customer-managed KMS key (CMK)** instead of the default AWS owned key, and the key administrator who has to set that key's policy.

Encryption at rest with a CMK is a **two-sided grant**, and this is the single most common way it fails. KMS authorizes a request only when *both* the caller's identity policy and the *key's* resource policy allow it. Granting one side and forgetting the other produces an `AccessDeniedException` at Agent Space / service creation time, because AWS DevOps Agent **dry-runs every required KMS action at configuration time** and fails the whole request if any is missing.

There is a second subtlety: AWS DevOps Agent uses **two different sets of credentials** against your key.

| Credentials | Used for | Where it must be allowed |
|---|---|---|
| **Your caller credentials** | Synchronous work: key validation at resource-creation time, encryption at creation, and any API call that returns a direct response | Caller identity policy **and** the key policy's caller statement |
| **The `aidevops.amazonaws.com` service principal** | Asynchronous background work: operational investigations, incident analysis, event correlation, root-cause-analysis generation | The key policy only (a service principal has no identity policy) |

Miss the service-principal half and resource creation still succeeds — then investigations silently fail later, because DevOps Agent cannot read the data it encrypted on your behalf.

### Who counts as a "caller"

"Caller" is not only the human who ran `create-agent-space`. It is **every identity that makes synchronous DevOps Agent API calls against an encrypted resource**, and each one needs both halves of the grant (its own identity policy **and** a `Principal` entry in the key policy's caller statement). In the scenarios in this repo that is:

| Identity | Scenario | Why it is a KMS caller |
|---|---|---|
| The installer / provisioning identity | [`b2`](../b2-installer/) | `CreateAgentSpace` / `RegisterService` with `kmsKeyArn`: key validation and encryption at creation time |
| The **Operator Web App role** | [`a4`](../a4-operator-webapp-role/) | Every Web App request that reads or writes Agent Space data (investigations, chat, skills) is a *synchronous* call, so it decrypts under the caller's credentials — not the service principal's |

That is why this scenario ships **two** identity policies. If other identities in your account call the DevOps Agent API directly against an encrypted resource (a CLI role, a read-only analyst role, a pipeline identity — see [`b3`](../b3-webapp-tiers/) and [`b6`](../b6-cicd-deployer/)), extend the key policy's `AllowCallerAccessViaService` `Principal` list and give each of them a copy of [`policies/webapp-caller-policy.json`](./policies/webapp-caller-policy.json) with the same `kms:ViaService` fence. The key policy is the enumeration point: an identity missing from it gets `AccessDeniedException` no matter what its identity policy says.

## Artifacts

| File | What it is | Where it goes |
|---|---|---|
| [`policies/caller-policy.json`](./policies/caller-policy.json) | **Identity** policy | Attach to the IAM role/user that creates the Agent Space or registers the service |
| [`policies/webapp-caller-policy.json`](./policies/webapp-caller-policy.json) | **Identity** policy | Attach to the **Operator Web App role** ([`a4`](../a4-operator-webapp-role/)), in addition to `AIDevOpsOperatorAppAccessPolicy` |
| [`policies/key-policy.json`](./policies/key-policy.json) | **KMS resource** policy | Set as the key policy on the CMK (`kms:PutKeyPolicy` / the console's *Key policy* editor) |

In all three files replace `111122223333` with your account ID, `us-east-1` with your Region, `1234abcd-12ab-34cd-56ef-1234567890ab` with your key id, and `DevOpsAgentUserRole` / `DevOpsAgentRole-operator-app` with the role names you actually use.

### Required KMS actions (documented)

| Action | Why DevOps Agent needs it |
|---|---|
| `kms:DescribeKey` | Validate key configuration at resource-creation time |
| `kms:GenerateDataKey*` | Generate data keys — DevOps Agent uses envelope encryption with the AWS Encryption SDK hierarchical keyring, so your CMK protects *branch* keys, which protect the data |
| `kms:Decrypt` | Decrypt data |
| `kms:Encrypt` | Encrypt data |
| `kms:ReEncrypt*` | Re-encrypt data under the same or a different key |

**All five are mandatory. There is no read-only subset.** The documentation is explicit: *"AWS DevOps Agent validates all of these permissions at configuration time using dry-run operations. If any permission is missing, the request fails with an exception."* So you cannot ship, say, `DescribeKey` + `Decrypt` for a "read-only" consumer and expect it to work — the dry-run check is all-or-nothing, and it runs before anything is created. Trim the *resource* (one key ARN) and the *conditions* (`kms:ViaService`), never the action list.

## `policies/caller-policy.json`

| Sid | Purpose |
|---|---|
| `UseCustomerManagedKeyViaDevOpsAgent` | The five required crypto actions, pinned to **one** CMK ARN and fenced with `StringEquals` on `kms:ViaService: aidevops.us-east-1.amazonaws.com` |
| `ListKeysForConsoleKeySelection` | `kms:ListKeys`, `kms:ListAliases` and `kms:DescribeKey` on `Resource: "*"`, **unconditioned** — see below |

### Why `kms:ViaService` is the point

`kms:ViaService` only appears in the request context when the call reaches KMS *through* another service — here, when DevOps Agent calls KMS on the caller's behalf. A direct `kms:Decrypt` typed by the human carries no `kms:ViaService` value at all, so the condition does not match and the grant does not apply. That means this policy lets the holder *use DevOps Agent with an encrypted Agent Space* without ever letting them use the key to decrypt anything themselves. Probes `sim-decrypt-without-viaservice-context-denied` and `sim-decrypt-via-secretsmanager-denied` are the proof.

`check_required_conditions` asserts `kms:ViaService: aidevops.us-east-1.amazonaws.com` on `kms:GenerateDataKey*`, `kms:Decrypt`, `kms:Encrypt` and `kms:ReEncrypt*` (see `scenario.yaml` `required_conditions`), so an edit that drops the fence fails `python3 -m tools.checks`.

### Why `ListKeys`/`DescribeKey` are unconditioned, and why that is safe

The console's *Encryption key type → Customer managed key* dropdown has to enumerate candidate keys and inspect each one's key spec and usage **before** the user has picked a key — the documentation says explicitly that a key missing from the dropdown usually means the caller lacks `kms:ListKeys` and `kms:DescribeKey`. At that moment there is no DevOps Agent call in flight, so there is no `kms:ViaService` context to condition on, and `kms:ListKeys`/`kms:ListAliases` are account-level APIs that take no key ARN — `Resource: "*"` is the only form IAM accepts.

The exposure is metadata only: `ListKeys` returns key ids/ARNs, `ListAliases` returns alias names, `DescribeKey` returns configuration (key spec, usage, state, rotation). None of them returns key material or decrypts anything. Note that `kms:DescribeKey` therefore appears **twice** — once fenced (as one of the five required actions) and once unconditioned for the picker. If you provision keys by ARN and never use the console, delete `ListKeysForConsoleKeySelection` entirely; nothing else in this scenario depends on it.

`RESOURCE_STAR` on that statement (and on the whole key policy) is suppressed with a written reason in `scenario.yaml`.

## `policies/webapp-caller-policy.json`

| Sid | Purpose |
|---|---|
| `UseCustomerManagedKeyViaDevOpsAgent` | The same five required crypto actions, the same single CMK ARN, the same `StringEquals` fence on `kms:ViaService: aidevops.us-east-1.amazonaws.com` |

Attach this **alongside** the Operator Web App role's `AIDevOpsOperatorAppAccessPolicy` equivalent from [`a4`](../a4-operator-webapp-role/) whenever the Agent Space that role serves is encrypted with a CMK. Without it, the role assumes cleanly and the Web App loads — then every request that touches encrypted Agent Space content fails with `AccessDeniedException` from KMS, because Web App requests are synchronous and therefore run under the role's own credentials, not under `aidevops.amazonaws.com`.

Two differences from [`policies/caller-policy.json`](./policies/caller-policy.json), both deliberate:

- **No `ListKeysForConsoleKeySelection` statement.** The Web App role never picks a key in the KMS console — the key was chosen at creation time by the installer. So there is nothing here that needs `Resource: "*"`, and `kms:DescribeKey` appears exactly once, fenced. `check_required_conditions` therefore asserts `kms:ViaService` on **all five** actions for this artifact (four for the installer's policy, where `DescribeKey` is also granted unconditioned for the picker).
- **It is additive, not a replacement.** This policy grants no `aidevops:*` actions at all; the session-tag scoping (`${aws:PrincipalTag/AgentSpaceId}`) that makes the Web App role safe lives entirely in `a4`.

AWS's own example applies `kms:ViaService` to the caller statement, which is exactly what makes this safe to hand to a role a browser session drives: the grant is worthless for a direct `kms:Decrypt`, so it never becomes a way to read ciphertext outside the DevOps Agent path.

## `policies/key-policy.json`

This is verbatim the key policy pattern from the encryption-at-rest documentation. `Resource: "*"` in a key policy means **"this key"** — a key policy is attached to exactly one key and KMS accepts no other form. The scoping is done by `Principal` plus the conditions.

| Sid | Principal | Purpose |
|---|---|---|
| `AllowCallerAccessViaService` | **every** caller role — here the installer role *and* the Operator Web App role | The caller half of the two-sided grant: the same five actions, same `kms:ViaService` fence as the identity policies. Both sides must agree or KMS denies. The `Principal.AWS` value is a **list**: add an entry for each identity that calls the DevOps Agent API synchronously (see [*Who counts as a "caller"*](#who-counts-as-a-caller)) |
| `AllowDevOpsAgentServiceDescribeKeyAccess` | `aidevops.amazonaws.com` | Configuration-time key validation by the service itself. Intentionally **unconditioned**: at validation time no resource ARN exists yet, so there is nothing for `aws:SourceArn` or the encryption context to match — and `DescribeKey` reveals only key metadata |
| `AllowDevOpsAgentAccessForAgentSpace` | `aidevops.amazonaws.com` | Asynchronous crypto for **Agent Space** data (investigations, skills, chat), gated on `aws:SourceArn` `ArnLike` `…:agentspace/*` **and** `kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn` `StringLike` `…:agentspace/*` |
| `AllowDevOpsAgentAccessForService` | `aidevops.amazonaws.com` | Same, for **registered service** data (third-party credentials), gated on `…:service/*` in both condition keys |

### Why there are **two** service-principal crypto statements, not one

The two DevOps Agent resource types that can take a CMK — Agent Space and Service — have different ARN resource segments (`agentspace/<id>` vs `service/<id>`). Both conditions are single-valued-ish patterns in the documented policy, and collapsing them into one statement with a list of ARNs changes the semantics: `aws:SourceArn` and the encryption context would then be checked **independently**, so a request sourced from an Agent Space would satisfy the condition while carrying a *service* encryption context (and vice versa). Keeping one statement per resource type makes the pair **correlated** — each request must match the same resource type on both keys.

Keep the statement for a resource type you use; if you only ever encrypt Agent Spaces, you may drop `AllowDevOpsAgentAccessForService` (and vice versa). Do **not** relax either statement to `arn:aws:aidevops:us-east-1:111122223333:*`: that re-introduces exactly the cross-type mismatch above.

### The two conditions, and why both

- **`aws:SourceArn`** — *who the request is for*. Confirms the service-principal call originated from one of **your** DevOps Agent resources, not from another customer's resource in the same service (the classic confused-deputy defence for service principals).
- **`kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn`** — *what the request is about*. DevOps Agent puts the ARN of the resource being encrypted into the encryption context on **every** cryptographic operation, and KMS binds it to the ciphertext as additional authenticated data; decryption must present the identical context. Conditioning on it means the key cannot be used to decrypt ciphertext belonging to a resource outside your ARN pattern, even by the service principal.

The same context key is visible in CloudTrail (`aws-crypto-ec:aws:aidevops:arn`), which is how you audit which resource each key use belonged to. Concretely: filter CloudTrail on event source **`kms.amazonaws.com`** and read `requestParameters.encryptionContext["aws-crypto-ec:aws:aidevops:arn"]` — its value is the ARN of the DevOps Agent resource being encrypted or decrypted. That gives you a per-resource audit trail of key use without needing any DevOps Agent-side logging, and it is the fastest way to confirm whether a failing investigation ever reached KMS at all.

### Key administration is deliberately absent

Neither artifact grants `kms:CreateKey`, `kms:PutKeyPolicy`, `kms:CreateGrant`/`RetireGrant`/`RevokeGrant`, `kms:DisableKey`, `kms:ScheduleKeyDeletion`, `kms:EnableKeyRotation` or `kms:TagResource` to the DevOps Agent caller. All are in `scenario.yaml` `forbidden_actions` with `implicitDeny` probes. Two reasons:

1. `kms:PutKeyPolicy` and `kms:CreateGrant` would let the holder re-grant the key's use to any principal **without** the `kms:ViaService` fence — a straight route around this whole design.
2. `kms:DisableKey` / `kms:ScheduleKeyDeletion` are data-destroying. A disabled key yields `DisabledException`, a key scheduled for deletion yields `KMSInvalidStateException`, and a **deleted** key means permanent data loss: DevOps Agent does not re-encrypt data under a new key, so recovering means creating new resources.

Key administration belongs to the key owner's existing `AllowKeyAdministration`-style statement (account root / key admin role), which every real key policy keeps alongside the four statements here. It is not part of this deliverable.

## Key requirements and one-way doors

- The key must be **symmetric**, key spec `SYMMETRIC_DEFAULT`, key usage `ENCRYPT_DECRYPT`. Multi-Region and asymmetric keys are **not supported**.
- The CMK is set with the `kmsKeyArn` parameter at resource **creation** time. You **cannot add or change** the CMK on an existing resource.
- The value must be the **full key ARN**, not an alias or key id.

### Creation-time only: there is no retrofit

Quoting the documentation directly: *"You must specify the customer managed key at resource creation time. You cannot add or change the customer managed key for an existing resource."*

The operational consequence is the thing to plan for: **a missing or wrong grant is not a policy fix, it is a delete-and-recreate.** If you create an Agent Space with `kmsKeyArn` and later discover the key policy was missing the service-principal statements or one of the caller principals, you fix the key policy *and* you still have to delete and recreate the Agent Space, because you cannot re-point it at a corrected key, and you cannot remove encryption from it either. The same applies to a registered service.

So the order of operations matters:

1. Create the CMK and set its **complete** key policy first — all four statements, with every caller principal already enumerated (both identity policies in this scenario).
2. Only then create the Agent Space / register the service with `kmsKeyArn`.
3. Verify with a real Web App request and a real investigation, not just with the create call: the create call only exercises the caller half plus `DescribeKey`.

### `kmsKeyArn` must be the full key ARN

`arn:aws:kms:us-east-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab` — **not** an alias (`alias/devops-agent`), **not** a bare key id (`1234abcd-…`). Aliases are deliberately unsupported here: an alias can be re-pointed at a different key, which would silently change which key protects existing data, and the creation-time-only rule above means the resource has no way to follow that change.

### `kmsKeyArn` also applies to `RegisterService`

The same parameter, with the same two-sided grant and the same creation-time-only rule, is accepted by `RegisterService` — for **every** service type: Dynatrace, ServiceNow, PagerDuty, GitLab, GitHub and MCP servers. What that encrypts is the third-party integration credential material, which is exactly what [`b4-secrets-manager`](../b4-secrets-manager/) is about: `b4` covers the Secrets Manager secrets holding those credentials, `b5` covers the CMK that protects them. If you use a CMK, register services with `kmsKeyArn` from the start — a service registered without it cannot be converted later.

### Failure modes

| What happened to the key | What you see |
|---|---|
| Key policy permissions revoked (a caller principal or a service-principal statement removed) | `AccessDeniedException` |
| Key **disabled** | `DisabledException` |
| Key **scheduled for deletion** | `KMSInvalidStateException` |
| Key **deleted** (deletion window elapsed) | **Permanent data loss** — the encrypted data is unrecoverable; DevOps Agent does not re-encrypt under a new key |

The first three are recoverable: restore the key policy, re-enable the key, or cancel the deletion, and DevOps Agent resumes. The fourth is not, which is why `kms:ScheduleKeyDeletion`, `kms:DisableKey` and `kms:PutKeyPolicy` are in `forbidden_actions` for both caller identities.

## Live validation coverage

All expectations in [`expected/probes.yaml`](./expected/probes.yaml) are `kind: simulate` (`iam:SimulateCustomPolicy`) against **`policies/caller-policy.json` only**: allows for the five crypto actions with the `kms:ViaService` context, allows for the console picker, denies without the context / via another service / in another Region / on another key, denies on all key administration, and denies on the adjacent `b2` and `b4` privileges. The probe runner selects the artifact matching the tier in `role_under_test` (`caller_role_arn` → `caller-policy.json`).

**`policies/webapp-caller-policy.json` has static coverage only, for now.** Its `kms:ViaService` fence is asserted on all five actions by `check_required_conditions`, which reads artifacts from disk and so covers it fully today. Simulate probes are *not* added yet: the runner's artifact selection is a filename heuristic on `role_under_test`, and `caller_role_arn` matches `caller` in **both** caller artifacts' filenames, so every probe is simulated against the union of the two policies rather than against one named artifact. A probe intended to prove the Web App policy denies something the installer policy allows (e.g. `kms:ListKeys`) would therefore report the installer's allow. Explicit per-probe artifact selection (`simulate_artifacts:`) is a separate tooling change; probe coverage for this artifact follows it.

**`policies/key-policy.json` is validated statically only in this milestone, and that is a deliberate scope decision:**

1. It is a **resource** policy. `iam:SimulateCustomPolicy` and `iam:SimulatePrincipalPolicy` evaluate *identity* policies; a document with `Principal` blocks is not a valid input, and there is no simulator API for "would KMS allow this service-principal request with this encryption context". Its correctness is asserted by `check_policy_json` (shape, Sid uniqueness), `check_parliament`, and by this repo's review of it against the documented pattern.
2. The terraform harness therefore creates **no KMS key** — only the caller role (`iamscn-b5-caller`) via the shared `scenario-role` module. A real CMK in the sandbox account is a 7-to-30-day-minimum deletion-window resource the destroy step cannot fully remove, and exercising the service-principal statements would need an onboarded account plus a real Agent Space performing background investigations, which no static or simulate-based check can reach.

**Prerequisite for promoting this scenario to `live`:** `iam:SimulatePrincipalPolicy` evaluates the permissions boundary on the role under test, and the shared `iamscn-boundary` does not yet allow any `kms` action — its `boundary.tf` reserves that widening for "B4/B5". Until the boundary carries a scoped `kms` statement, the allow probes above would evaluate as `implicitDeny` in a live run. That edit lives in `terraform/bootstrap/`, outside this scenario's directory, so it is intentionally not part of this delta.

## Not included (by design)

- Creating the Agent Space / registering the service that references the CMK (`aidevops:CreateAgentSpace`, `aidevops:RegisterService`) → scenario [`b2`](../b2-installer/)
- The `kms:ViaService: secretsmanager.<region>.amazonaws.com` grant a caller needs to write a **CMK-encrypted Secrets Manager secret** → scenario [`b4`](../b4-secrets-manager/README.md#compounding-case-a-customer-managed-kms-key-scenario-b5). That is a *different* `ViaService` value for the same key; the two scenarios compound, they do not overlap
- Key administration (`CreateKey`, `PutKeyPolicy`, grants, rotation, deletion) — the key owner's existing key-policy statements
- The Plane-A roles themselves — the Agent Space role and the Operator Web App role's trust policy and `aidevops:*` grants → scenarios [`a1`](../a1-agentspace-role/) / [`a4`](../a4-operator-webapp-role/). Only the Web App role's **KMS caller half** lives here ([`policies/webapp-caller-policy.json`](./policies/webapp-caller-policy.json)), because it is meaningless without this scenario's key policy

## Sources

See `scenario.yaml` `docs:`:

- *Encryption at rest for AWS DevOps Agent* — the CMK requirements table (symmetric / `SYMMETRIC_DEFAULT` / `ENCRYPT_DECRYPT`, no multi-Region, no asymmetric), the two-credential-sets explanation, the required-KMS-actions table and the statement that all of them are dry-run validated at configuration time, the example key policy reproduced in `policies/key-policy.json` (including the `kms:ViaService` condition on the caller statement, which `policies/webapp-caller-policy.json` reuses), the `kmsKeyArn` creation-time-only / full-ARN / `RegisterService`-across-all-service-types parameter rules, the `kms:ListKeys` + `kms:DescribeKey` console-dropdown note, the `aws-crypto-ec:aws:aidevops:arn` encryption context and its CloudTrail visibility, and the failure-mode table.
- *DevOps Agent IAM permissions* — the caller personas these key permissions attach to, including the Operator Web App role.
