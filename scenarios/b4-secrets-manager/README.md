# B4 — Third-party integration registrar: credentials in Secrets Manager

**Who this is for:** the person (or pipeline identity) who registers a **third-party tool integration** with AWS DevOps Agent — Dynatrace, Datadog, Grafana, New Relic, Splunk, PagerDuty, ServiceNow, Slack, GitHub, GitLab, Azure DevOps, or an MCP / A2A server — where the integration's credential (OAuth client secret, API key, webhook secret, auth-header value) is stored in **AWS Secrets Manager**.

Every one of those registrations takes a secret from you. AWS DevOps Agent stores it encrypted and never displays it again, and the service reads it later when it calls the third-party tool. The registrar's job is therefore **write-only**: create the secret, put the value in it, tag it, confirm it exists. Reading it back is the *service's* job, not the human's.

## Artifact

[`policies/integration-registrar-policy.json`](./policies/integration-registrar-policy.json) — replace `111122223333` with your account ID, `us-east-1` with your Region, and `devops-agent/` with your own secret-name prefix if you use a different naming convention (keep it a prefix — the whole least-privilege story here rests on it).

## What each statement does

| Sid | Purpose |
|---|---|
| `CreateDevOpsAgentIntegrationSecrets` | `secretsmanager:CreateSecret`, double-scoped: the resource pattern `secret:devops-agent/*` **and** a `StringLike` condition on `secretsmanager:Name`. Both are needed — see below |
| `PopulateAndInspectIntegrationSecrets` | `PutSecretValue` (write the credential, and rotate it by writing a new version), `DescribeSecret` (metadata / confirm existence — returns no secret value) and `TagResource` (cost allocation, ownership, `aidevops` bookkeeping), all pinned to `secret:devops-agent/*` |
| `ListSecretsForNameSelection` | `secretsmanager:ListSecrets` is an account-level enumeration API. It takes no secret ARN, so IAM only accepts `Resource: "*"` for it. This matches the `AIDevOpsOperatorAppAccessPolicy` managed policy, which likewise grants `secretsmanager:CreateSecret` and `secretsmanager:ListSecrets` on `"*"`. `ListSecrets` returns names and metadata only, never values |

### Why `CreateSecret` needs both an ARN pattern *and* `secretsmanager:Name`

At `CreateSecret` time the secret does not exist yet, so there is no ARN to match against — Secrets Manager appends a random six-character suffix (`devops-agent/dynatrace-oauth-AbCdEf`) *after* authorization. A resource-only scope is therefore easy to get subtly wrong, while the `secretsmanager:Name` request condition is evaluated against exactly the name the caller asked for. Keeping both means the grant fails closed whichever way the request is shaped. `check_required_conditions` asserts the `secretsmanager:Name` condition is present (see `scenario.yaml` `required_conditions`).

Note the ARN pattern `secret:devops-agent/*` — the `*` also absorbs the random suffix, which is why the trailing wildcard is mandatory even for a single known secret name.

## Deliberately absent

| Action | Why it's not here |
|---|---|
| `secretsmanager:GetSecretValue` | The **service** reads the credential (via the Plane-A Agent Space / service role), not the installer. Granting the human a read turns "I registered an integration" into "I can exfiltrate every integration credential in the account" |
| `secretsmanager:DeleteSecret` | Deleting a secret an active integration depends on breaks that integration; treat deletion as an operations/admin action with its own approval path |
| `secretsmanager:UpdateSecret` | Credential rotation is `PutSecretValue` (a new version). `UpdateSecret` can also swap the KMS key of an existing secret, which silently changes who can decrypt it |
| `secretsmanager:PutResourcePolicy` / `GetResourcePolicy` | A secret resource policy can re-grant reads to any principal, including cross-account — that would route straight around the missing `GetSecretValue` |
| `aidevops:*`, `iam:PassRole`, `iam:CreateRole` | Registering the integration with the Agent Space (`aidevops:RegisterService`, `AssociateService`) is scenario [`b2`](../b2-installer/); role provisioning is `b1` |

All of these are listed in `scenario.yaml` `forbidden_actions`, so a future edit that adds one back fails `python3 -m tools.checks`, and each has a matching `implicitDeny` probe in [`expected/probes.yaml`](./expected/probes.yaml).

## Compounding case: a customer-managed KMS key (scenario `b5`)

The policy above assumes the **AWS managed key** (`aws/secretsmanager`), where Secrets Manager's own permissions cover the crypto and the caller needs no `kms:*`. If you encrypt the integration secret with a **customer-managed key** — which the DevOps Agent encryption-at-rest guidance recommends, and which the `kmsKeyArn` parameter on `RegisterService` requires — the two scenarios compound and this policy alone is **not enough**.

Two distinct KMS grants are in play, and it is easy to conflate them:

**1. The registrar writing the secret — `kms:ViaService: secretsmanager.<region>.amazonaws.com`**

`CreateSecret` and `PutSecretValue` on a CMK-encrypted secret make Secrets Manager call KMS *on the caller's behalf*, so the caller needs `kms:GenerateDataKey` (envelope encryption; `PutSecretValue` also needs `kms:Decrypt` for versioning on some paths). Add, alongside the statements above:

```json
{
  "Sid": "EncryptIntegrationSecretsWithCustomerKey",
  "Effect": "Allow",
  "Action": [
    "kms:GenerateDataKey",
    "kms:Decrypt"
  ],
  "Resource": "arn:aws:kms:us-east-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
  "Condition": {
    "StringEquals": {
      "kms:ViaService": "secretsmanager.us-east-1.amazonaws.com"
    }
  }
}
```

The `kms:ViaService` value is **`secretsmanager.<region>.amazonaws.com`**, *not* `aidevops.<region>.amazonaws.com`: the request reaches KMS through Secrets Manager, so that is the service making it. The condition means these key permissions are worthless outside the Secrets Manager path — the holder cannot use the key to decrypt anything else. The key policy must allow the same principal + actions, since KMS requires both sides.

**2. AWS DevOps Agent's own use of a CMK — `kms:ViaService: aidevops.<region>.amazonaws.com`**

The separate, documented grant for the Agent Space / registered service itself: caller statements conditioned on `kms:ViaService: aidevops.<region>.amazonaws.com` for synchronous operations, plus key-policy statements for the `aidevops.amazonaws.com` **service principal** (asynchronous investigation work) gated on `aws:SourceArn` matching `agentspace/*` or `service/*` and `kms:EncryptionContext:aws-crypto-ec:aws:aidevops:arn` matching the same ARN. DevOps Agent dry-runs `kms:DescribeKey`, `GenerateDataKey`, `Decrypt`, `Encrypt` and `ReEncrypt` at configuration time and fails the request outright if any is missing. That full pattern — caller policy **and** key policy — is scenario [`b5`](../../docs/scenario-matrix.md).

If you need both, the union is: this scenario's `secretsmanager` statements + the `secretsmanager.<region>` `ViaService` statement above + `b5`. Also note the key must be symmetric, `SYMMETRIC_DEFAULT`, `ENCRYPT_DECRYPT`, single-Region, and that the CMK can only be set at resource **creation** time — it cannot be added or changed later.

## Live validation coverage

All expectations in [`expected/probes.yaml`](./expected/probes.yaml) are `kind: simulate` (`iam:SimulatePrincipalPolicy`): allows on the `devops-agent/` prefix, denies on other secret names, denies on `GetSecretValue`/`DeleteSecret`/`UpdateSecret`/`PutResourcePolicy` inside the prefix, and denies on the adjacent `aidevops` and CMK privileges.

There are no `real` probes on purpose. A real `CreateSecret`/`PutSecretValue` would write actual (dummy) credential material into the sandbox account and leave a 7-to-30-day recovery-window secret behind that the destroy step cannot remove — the registrar has no `DeleteSecret`, by design. The simulator answers the IAM question here exactly and leaves nothing to clean up. The real `aidevops` calls needed to observe the credential end-to-end also require an onboarded account (same limitation documented in [`b3`](../b3-webapp-tiers/README.md#why-there-are-no-real-probes)).

The harness substitutes the customer-facing prefix `devops-agent/` with `iamscn-b4/` (`scenario.yaml` `substitutions`) so every ARN the live run touches stays inside the repo's `iamscn-` namespace.

**Prerequisite for promoting this scenario to `live`:** `iam:SimulatePrincipalPolicy` evaluates the permissions boundary attached to the role under test, and the shared `iamscn-boundary` does not yet allow any `secretsmanager` action — its `boundary.tf` reserves that widening for "B4/B5". Until the boundary carries a scoped `secretsmanager` statement, the five allow probes above would evaluate as `implicitDeny` in a live run. That edit lives in `terraform/bootstrap/`, outside this scenario, so it is deliberately not part of this scenario's delta; this scenario is statically validated only.

## Not included (by design)

- Registering the integration with the Agent Space (`aidevops:RegisterService`, `AssociateService`) → scenario `b2`
- The customer-managed KMS key caller policy + key policy → scenario `b5` (see the compounding section above)
- The Plane-A role that *reads* the credential at runtime → scenarios `a1`/`a4`
- IAM role and policy CRUD → scenario `b1`

## Sources

See `scenario.yaml` `docs:` for the AWS documentation pages every action is traced to:

- *DevOps Agent IAM permissions* — the `AIDevOpsOperatorAppAccessPolicy` managed policy, whose `AllowSecretsManagerOperatorActions` statement is the documented source for `secretsmanager:CreateSecret` + `secretsmanager:ListSecrets`, and whose `AIDevOpsAgentAccessPolicy` counterpart shows the service side reading with `secretsmanager:Describe*` / `secretsmanager:List*` / `GetResourcePolicy`.
- *Encryption at rest for DevOps Agent* — the customer-managed-key requirements, `kmsKeyArn` on Agent Space creation and `RegisterService`, the required KMS action list, and the `aws-crypto-ec:aws:aidevops:arn` encryption context.
- *Connecting MCP servers* — the registration flows that take a client secret, API key or named auth headers ("AWS DevOps Agent stores each secret encrypted and does not display it again after you register the server").
