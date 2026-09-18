# B5 — Customer-managed KMS key: caller policy + key policy

**Who this is for:** the person (or pipeline identity) who creates an **Agent Space** or **registers a service** encrypted with a **customer-managed KMS key (CMK)** instead of the default AWS owned key, and the key administrator who has to set that key's policy.

Encryption at rest with a CMK is a **two-sided grant**, and this is the single most common way it fails. KMS authorizes a request only when *both* the caller's identity policy and the *key's* resource policy allow it. Granting one side and forgetting the other produces an `AccessDeniedException` at Agent Space / service creation time, because AWS DevOps Agent **dry-runs every required KMS action at configuration time** and fails the whole request if any is missing.

There is a second subtlety: AWS DevOps Agent uses **two different sets of credentials** against your key.

| Credentials | Used for | Where it must be allowed |
|---|---|---|
| **Your caller credentials** | Synchronous work: key validation at resource-creation time, encryption at creation, and any API call that returns a direct response | Caller identity policy **and** the key policy's caller statement |
| **The `aidevops.amazonaws.com` service principal** | Asynchronous background work: operational investigations, incident analysis, event correlation, root-cause-analysis generation | The key policy only (a service principal has no identity policy) |

Miss the service-principal half and resource creation still succeeds — then investigations silently fail later, because DevOps Agent cannot read the data it encrypted on your behalf.

## Artifacts

| File | What it is | Where it goes |
|---|---|---|
| [`policies/caller-policy.json`](./policies/caller-policy.json) | **Identity** policy | Attach to the IAM role/user that creates the Agent Space or registers the service |
| [`policies/key-policy.json`](./policies/key-policy.json) | **KMS resource** policy | Set as the key policy on the CMK (`kms:PutKeyPolicy` / the console's *Key policy* editor) |

In both files replace `111122223333` with your account ID, `us-east-1` with your Region, `1234abcd-12ab-34cd-56ef-1234567890ab` with your key id, and `DevOpsAgentUserRole` with the caller role you actually use.

### Required KMS actions (documented)

| Action | Why DevOps Agent needs it |
|---|---|
| `kms:DescribeKey` | Validate key configuration at resource-creation time |
| `kms:GenerateDataKey*` | Generate data keys — DevOps Agent uses envelope encryption with the AWS Encryption SDK hierarchical keyring, so your CMK protects *branch* keys, which protect the data |
| `kms:Decrypt` | Decrypt data |
| `kms:Encrypt` | Encrypt data |
| `kms:ReEncrypt*` | Re-encrypt data under the same or a different key |

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

## `policies/key-policy.json`

This is verbatim the key policy pattern from the encryption-at-rest documentation. `Resource: "*"` in a key policy means **"this key"** — a key policy is attached to exactly one key and KMS accepts no other form. The scoping is done by `Principal` plus the conditions.

| Sid | Principal | Purpose |
|---|---|---|
| `AllowCallerAccessViaService` | your caller role | The caller half of the two-sided grant: the same five actions, same `kms:ViaService` fence as the identity policy. Both sides must agree or KMS denies |
| `AllowDevOpsAgentServiceDescribeKeyAccess` | `aidevops.amazonaws.com` | Configuration-time key validation by the service itself. Intentionally **unconditioned**: at validation time no resource ARN exists yet, so there is nothing for `aws:SourceArn` or the encryption context to match — and `DescribeKey` reveals only key metadata |
| `AllowDevOpsAgentAccessForAgentSpace` | `aidevops.amazonaws.com` | Asynchronous crypto for **Agent Space** data (investigations, skills, chat), gated on `aws:SourceArn` `ArnLike` `…:agentspace/*` **and** `kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn` `StringLike` `…:agentspace/*` |
| `AllowDevOpsAgentAccessForService` | `aidevops.amazonaws.com` | Same, for **registered service** data (third-party credentials), gated on `…:service/*` in both condition keys |

### Why there are **two** service-principal crypto statements, not one

The two DevOps Agent resource types that can take a CMK — Agent Space and Service — have different ARN resource segments (`agentspace/<id>` vs `service/<id>`). Both conditions are single-valued-ish patterns in the documented policy, and collapsing them into one statement with a list of ARNs changes the semantics: `aws:SourceArn` and the encryption context would then be checked **independently**, so a request sourced from an Agent Space would satisfy the condition while carrying a *service* encryption context (and vice versa). Keeping one statement per resource type makes the pair **correlated** — each request must match the same resource type on both keys.

Keep the statement for a resource type you use; if you only ever encrypt Agent Spaces, you may drop `AllowDevOpsAgentAccessForService` (and vice versa). Do **not** relax either statement to `arn:aws:aidevops:us-east-1:111122223333:*`: that re-introduces exactly the cross-type mismatch above.

### The two conditions, and why both

- **`aws:SourceArn`** — *who the request is for*. Confirms the service-principal call originated from one of **your** DevOps Agent resources, not from another customer's resource in the same service (the classic confused-deputy defence for service principals).
- **`kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn`** — *what the request is about*. DevOps Agent puts the ARN of the resource being encrypted into the encryption context on **every** cryptographic operation, and KMS binds it to the ciphertext as additional authenticated data; decryption must present the identical context. Conditioning on it means the key cannot be used to decrypt ciphertext belonging to a resource outside your ARN pattern, even by the service principal.

The same context key is visible in CloudTrail (`aws-crypto-ec:aws:aidevops:arn`), which is how you audit which resource each key use belonged to.

### Key administration is deliberately absent

Neither artifact grants `kms:CreateKey`, `kms:PutKeyPolicy`, `kms:CreateGrant`/`RetireGrant`/`RevokeGrant`, `kms:DisableKey`, `kms:ScheduleKeyDeletion`, `kms:EnableKeyRotation` or `kms:TagResource` to the DevOps Agent caller. All are in `scenario.yaml` `forbidden_actions` with `implicitDeny` probes. Two reasons:

1. `kms:PutKeyPolicy` and `kms:CreateGrant` would let the holder re-grant the key's use to any principal **without** the `kms:ViaService` fence — a straight route around this whole design.
2. `kms:DisableKey` / `kms:ScheduleKeyDeletion` are data-destroying. A disabled key yields `DisabledException`, a key scheduled for deletion yields `KMSInvalidStateException`, and a **deleted** key means permanent data loss: DevOps Agent does not re-encrypt data under a new key, so recovering means creating new resources.

Key administration belongs to the key owner's existing `AllowKeyAdministration`-style statement (account root / key admin role), which every real key policy keeps alongside the four statements here. It is not part of this deliverable.

## Key requirements and one-way doors

- The key must be **symmetric**, key spec `SYMMETRIC_DEFAULT`, key usage `ENCRYPT_DECRYPT`. Multi-Region and asymmetric keys are **not supported**.
- The CMK is set with the `kmsKeyArn` parameter at resource **creation** time (`CreateAgentSpace`, `RegisterService` — supported for every service type: Dynatrace, ServiceNow, PagerDuty, GitLab, GitHub, MCP servers). You **cannot add or change** the CMK on an existing resource.
- The value must be the **full key ARN**, not an alias or key id.

## Live validation coverage

All expectations in [`expected/probes.yaml`](./expected/probes.yaml) are `kind: simulate` (`iam:SimulateCustomPolicy`) against **`policies/caller-policy.json` only**: allows for the five crypto actions with the `kms:ViaService` context, allows for the console picker, denies without the context / via another service / in another Region / on another key, denies on all key administration, and denies on the adjacent `b2` and `b4` privileges. The probe runner selects the artifact matching the tier in `role_under_test` (`caller_role_arn` → `caller-policy.json`).

**`policies/key-policy.json` is validated statically only in this milestone, and that is a deliberate scope decision:**

1. It is a **resource** policy. `iam:SimulateCustomPolicy` and `iam:SimulatePrincipalPolicy` evaluate *identity* policies; a document with `Principal` blocks is not a valid input, and there is no simulator API for "would KMS allow this service-principal request with this encryption context". Its correctness is asserted by `check_policy_json` (shape, Sid uniqueness), `check_parliament`, and by this repo's review of it against the documented pattern.
2. The terraform harness therefore creates **no KMS key** — only the caller role (`iamscn-b5-caller`) via the shared `scenario-role` module. A real CMK in the sandbox account is a 7-to-30-day-minimum deletion-window resource the destroy step cannot fully remove, and exercising the service-principal statements would need an onboarded account plus a real Agent Space performing background investigations, which no static or simulate-based check can reach.

**Prerequisite for promoting this scenario to `live`:** `iam:SimulatePrincipalPolicy` evaluates the permissions boundary on the role under test, and the shared `iamscn-boundary` does not yet allow any `kms` action — its `boundary.tf` reserves that widening for "B4/B5". Until the boundary carries a scoped `kms` statement, the allow probes above would evaluate as `implicitDeny` in a live run. That edit lives in `terraform/bootstrap/`, outside this scenario's directory, so it is intentionally not part of this delta.

## Not included (by design)

- Creating the Agent Space / registering the service that references the CMK (`aidevops:CreateAgentSpace`, `aidevops:RegisterService`) → scenario [`b2`](../b2-installer/)
- The `kms:ViaService: secretsmanager.<region>.amazonaws.com` grant a caller needs to write a **CMK-encrypted Secrets Manager secret** → scenario [`b4`](../b4-secrets-manager/README.md#compounding-case-a-customer-managed-kms-key-scenario-b5). That is a *different* `ViaService` value for the same key; the two scenarios compound, they do not overlap
- Key administration (`CreateKey`, `PutKeyPolicy`, grants, rotation, deletion) — the key owner's existing key-policy statements
- The Plane-A Agent Space role that operates inside the encrypted Agent Space → scenarios `a1`/`a4`

## Sources

See `scenario.yaml` `docs:`:

- *Encryption at rest for AWS DevOps Agent* — the CMK requirements table (symmetric / `SYMMETRIC_DEFAULT` / `ENCRYPT_DECRYPT`, no multi-Region, no asymmetric), the two-credential-sets explanation, the required-KMS-actions table, the example key policy reproduced in `policies/key-policy.json`, the `kmsKeyArn` creation-time-only parameter, the `kms:ListKeys` + `kms:DescribeKey` console-dropdown note, the `aws-crypto-ec:aws:aidevops:arn` encryption context, and the failure-mode table.
- *DevOps Agent IAM permissions* — the caller personas these key permissions attach to.
