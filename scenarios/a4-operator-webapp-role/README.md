# A4 — Operator Web App role (session-tag scoped)

**Who this is for:** whoever creates the IAM role that backs the **Operator Web App** — the browser app your on-call operators use to talk to AWS DevOps Agent without an AWS console sign-in. This is a **Plane-A** role: `aidevops.amazonaws.com` assumes it, once per Web App session, and **tags that session** with the Agent Space the operator logged in to. No human assumes it directly.

| Piece | Where it comes from | In this repo |
|---|---|---|
| **Trust policy** | hand-written — `sts:AssumeRole` **+ `sts:TagSession`** for `aidevops.amazonaws.com` | [`policies/trust-policy.json`](./policies/trust-policy.json) |
| **Permissions** | AWS managed policy `AIDevOpsOperatorAppAccessPolicy` — reproduced here as a customer-managed equivalent | [`policies/operator-app-policy.json`](./policies/operator-app-policy.json) |

Replace `111122223333` with your account ID and `us-east-1` with your Region **in the trust policy**. Leave `policies/operator-app-policy.json` alone: it contains no account ID and no Region, and everything that looks like a variable in it **is** one — see [Policy variables and the substitution pass](#policy-variables-and-the-substitution-pass).

```bash
aws iam create-role \
  --role-name DevOpsAgentRole-operator-app \
  --assume-role-policy-document file://policies/trust-policy.json

# Either attach the AWS managed policy by ARN …
aws iam attach-role-policy \
  --role-name DevOpsAgentRole-operator-app \
  --policy-arn arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy

# … or, if you need to review/narrow it, use the copy in this repo:
aws iam put-role-policy \
  --role-name DevOpsAgentRole-operator-app \
  --policy-name OperatorAppAccess \
  --policy-document file://policies/operator-app-policy.json
```

Enabling the Web App and pointing it at your IdP (`aidevops:EnableOperatorApp`, `aidevops:UpdateOperatorAppIdpConfig`) is the **installer's** job — [b2](../b2-installer/). The *human* console tiers, including the `aidevops:CreateOneTimeLoginSession` grant that mints a Web App login, are [b3](../b3-webapp-tiers/). This scenario is only the role the Web App itself runs as.

## `policies/operator-app-policy.json` — one role, many Agent Spaces

The entire least-privilege design is one line:

```json
"Resource": "arn:aws:aidevops:*:*:agentspace/${aws:PrincipalTag/AgentSpaceId}",
"Condition": { "StringEquals": { "aws:ResourceAccount": "${aws:PrincipalAccount}" } }
```

`${aws:PrincipalTag/AgentSpaceId}` is an IAM **policy variable**. At evaluation time IAM substitutes the value of the `AgentSpaceId` **session tag** on the calling principal, so the same role — with the same policy — resolves to a *different* Resource ARN in every session:

| Session tag | Effective Resource |
|---|---|
| `AgentSpaceId = as-abc123` | `arn:aws:aidevops:*:*:agentspace/as-abc123` |
| `AgentSpaceId = as-def456` | `arn:aws:aidevops:*:*:agentspace/as-def456` |
| *tag absent* | the variable does not resolve, the ARN never matches, **everything denies** |

That last row matters: an untagged session is a *closed* session, not an open one. There is no fallback wildcard.

Why it is built this way: a single Web App role serves every Agent Space in the account, but an operator logged in to one Agent Space must not read another's chats, assets or journal. Scoping by session tag moves that decision from IAM policy (which would need one role per Agent Space, re-issued whenever you add one) to **login time**.

### `aws:ResourceAccount = ${aws:PrincipalAccount}`

Every one of the five statements carries it, and it is the only scoping the four `Resource: "*"` statements have:

| Sid | Why `Resource: "*"` | What `aws:ResourceAccount` buys |
|---|---|---|
| `AllowOperatorAccountActions` | `aidevops:GetAccountUsage` is account-level and takes no ARN | Usage figures for *your* account only |
| `AllowSupportOperatorActions` | The AWS Support API has no resource types at all — `support:*` actions cannot be ARN-scoped | Support cases in your account |
| `AllowSecretsManagerOperatorActions` | `ListSecrets` is an enumeration API; `CreateSecret` names a secret that does not exist yet (same reasoning as [b4](../b4-secrets-manager/)) | No writing secrets into, or listing secrets from, a resource-policy-shared secret in another account |
| `AllowTranscribeOperatorActions` | `StartStreamTranscriptionWebSocket` opens a stream, not a resource | Transcription stays in-account |

`${aws:PrincipalAccount}` is a second policy variable — the account of the caller — so the pair reads "the resource's account must equal my own account". `check_required_conditions` asserts the key on one action from **every** statement (`scenario.yaml` → `required_conditions`), so dropping it anywhere fails `python3 -m tools.checks`.

### What the operator statement actually grants

The 53 `aidevops:*` actions in `AllowOperatorAgentSpaceActions` are the Web App's working surface, and they are **not** the same as b3's human operator tier. The notable differences:

- **Access token management** — `CreateAccessToken`, `GetAccessToken`, `ListAccessTokens`, `RotateAccessToken`, `RevokeAccessToken`. These are credentials for remote MCP / A2A servers. The docs' *human* operator example omits all five; the Web App role has them because token handling happens in the app.
- **Knowledge items and triggers** — `Create/Get/Update/Delete KnowledgeItem`, `ListKnowledgeItems`, `ListKnowledgeItemVersions`, `Create/Get/Update/Delete Trigger`, `ListTriggers`.
- **Approvals and goals** — `UpdateApprovalAction`, `UpdateGoal`. `UpdateApprovalAction` is how an operator approves a directed action; the *elevated* role that then performs it is `a3`, not this role.
- **Deletes** — `DeleteAsset`, `DeleteAssetFile`, `DeleteKnowledgeItem`, `DeleteTrigger`. All are confined to the session-tagged Agent Space.

And what is deliberately absent (each one a probe in [`expected/probes.yaml`](./expected/probes.yaml)): all of IAM, Agent Space lifecycle (`Create/Update/DeleteAgentSpace`, `AssociateService`), Operator App configuration (`EnableOperatorApp`, `UpdateOperatorAppIdpConfig` — a Web App session that could repoint the IdP could choose who gets which session tag), `CreateOneTimeLoginSession`, `InvokeAgent`, `secretsmanager:GetSecretValue`/`PutSecretValue`/`DeleteSecret`, `support:CreateCase` and all of KMS.

## How your IdP must set the `AgentSpaceId` session tag

The scoping above is worth exactly as much as the trust in who sets the tag. Two halves:

**1. The role must allow session tagging at all.** `sts:TagSession` is a separate action from `sts:AssumeRole` and must appear in the trust policy:

```json
"Principal": { "Service": "aidevops.amazonaws.com" },
"Action": ["sts:AssumeRole", "sts:TagSession"],
"Condition": {
  "StringEquals": { "aws:SourceAccount": "111122223333" },
  "ArnLike":      { "aws:SourceArn": "arn:aws:aidevops:us-east-1:111122223333:agentspace/*" }
}
```

Without `sts:TagSession` the assume-role call that carries tags fails outright, and the Web App cannot start a session. The confused-deputy pair is the same as [a1](../a1-agentspace-role/)'s and is asserted on **both** actions: `aidevops.amazonaws.com` is the same principal for every AWS customer, so `aws:SourceAccount` stops another account's Agent Space borrowing this role and `aws:SourceArn` stops any *other* DevOps Agent resource type in your own account doing so. An unconditioned `sts:TagSession` grant is worse than an unconditioned `AssumeRole` grant here, because it is the tag that picks the blast radius.

**2. The tag value must come from the IdP, not from the user.** DevOps Agent derives the session tag from the identity your IdP asserts when the operator signs in to the Web App (configured with `aidevops:UpdateOperatorAppIdpConfig`, see [b2](../b2-installer/)). Practical requirements:

- Map an **IdP-controlled** attribute to `AgentSpaceId` — a group membership, a directory attribute, an entitlement. Never a value the user can edit in a self-service profile, and never something derived from a URL parameter or a request header: that would let an operator retarget themselves at another Agent Space by editing it.
- Use the Agent Space **id**, exactly as it appears in the Agent Space ARN's `agentspace/<id>` segment. The policy variable is substituted literally into the ARN, so a friendly name, a display label or an ARN pasted whole will simply never match — and the failure is an `AccessDenied`, not a validation error.
- **One Agent Space per session.** Session tags are single-valued. An operator who legitimately works across two Agent Spaces logs in twice (two sessions, two tags); there is no multi-value form of this policy short of adding a second statement per Agent Space, which is the thing this design exists to avoid.
- If your IdP cannot assert the attribute, do not fall back to omitting the tag or to a wildcard: an untagged session denies everything (see the table above), which is the correct failure direction. Fix the mapping instead.

Session tags are transitive-capable but this role is a leaf — it does not assume anything else — so no `sts:AssumeRole` chaining or `TransitiveTagKeys` handling is needed.

## Policy variables and the substitution pass

The issue behind this scenario flagged a real hazard, so the finding is recorded here.

This repo ships docs-style raw JSON with placeholders (`111122223333`, `us-east-1`) and a harness that substitutes real values in two places: `tools/probes/run_probes.py` → `substitute()` for probes, and `terraform/main.tf` → `replace()` for deployment. Both are **literal string replacement** over the file's text, driven by `scenario.yaml` → `substitutions`. Neither of them is a template engine.

**Result: the policy variables survive verbatim.** `${aws:PrincipalTag/AgentSpaceId}` and `${aws:PrincipalAccount}` contain neither `111122223333` nor `us-east-1`, so no substitution rule can touch them. Verified from both ends:

- `check_required_conditions` asserts `aws:ResourceAccount` = the literal string `${aws:PrincipalAccount}` on all five statements. If any pass ever expanded, escaped or mangled the variable, those five assertions fail — the escaping has a regression test, not just a comment.
- The probe runner re-serializes the artifact with `json.dumps(json.loads(...))` before handing it to `iam:SimulateCustomPolicy`. That is JSON round-tripping, which preserves `${...}` inside a string exactly; the live run's `checks-report`/probe output therefore reflects the shipped ARN.

Two things the harness must **not** start doing, for the same reason:

- **`templatefile()` instead of `file()` + `replace()` in terraform.** `templatefile()` evaluates `${...}` as HCL, so it would fail the plan on `${aws:PrincipalTag/AgentSpaceId}` (`aws` is not an HCL variable) — a hard error, fortunately, not a silent mangling. `terraform/main.tf` uses `file()` and says so in a comment.
- **Rebuilding the document with `jsonencode()` of an HCL object literal.** Same interpolation problem, plus it stops shipping the reviewed artifact.

The one substitution rule that *would* be dangerous is a future `"${"`-touching entry in `substitutions`. There is none, and there is no reason to add one.

Note also that the `aidevops` Resource keeps the managed policy's wildcard Region and account (`arn:aws:aidevops:*:*:agentspace/...`) rather than being pinned to `us-east-1`/`111122223333`. That is verbatim-from-docs and intentional: `aws:ResourceAccount = ${aws:PrincipalAccount}` already pins the account, and the Web App is reached from whichever Region the Agent Space lives in. If you narrow it, narrow the Region only.

## Validation harness

`terraform/` provisions IAM primitives only:

- **`iamscn-a4-operator-app`** — the deliverable: the role created with `policies/trust-policy.json` as its assume-role policy and `policies/operator-app-policy.json` as an inline policy, both **verbatim** (account-id/Region substitution only; `file()` + `replace()`, never `jsonencode()`). That IAM *accepts* a Resource ARN containing `${aws:PrincipalTag/AgentSpaceId}` and a `sts:TagSession` grant to a service principal is the live evidence for the parts that cannot be simulated.
- **`iamscn-a4-operator`** — the probe anchor carrying the same permission policy as its candidate policy. It exists because the role above trusts `aidevops.amazonaws.com` *only*; adding the CI role to its trust policy would mean the harness no longer deploys the deliverable.

Both roles carry the **`iamscn-boundary`** permissions boundary, mandatory for every `iamscn-*` role in the sandbox and **not part of the customer deliverable**. The boundary's union is `aidevops` plus scoped IAM, so in the sandbox the `support`, `secretsmanager` and `transcribe` grants are capped away — another reason this scenario has no `real` probes.

### Live validation coverage

| Artifact / grant | How it is validated | Why not simulated |
|---|---|---|
| `policies/operator-app-policy.json` — **deny matrix** | `simulate` probes (`iam:SimulateCustomPolicy`) against the artifact, no boundary | — |
| `policies/operator-app-policy.json` — **allow half** | static: `check_required_conditions` (five `aws:ResourceAccount` assertions, policy variable as the expected literal) + `terraform apply` accepting the document | `iam:SimulateCustomPolicy` has no session behind it, so `${aws:PrincipalTag/AgentSpaceId}` never resolves and `aws:ResourceAccount` (resource-derived, not a `ContextEntries` key) never matches. Every allow expectation would be a false failure, and the only way to "fix" it is to weaken the artifact |
| `policies/trust-policy.json` | static (`check_required_conditions` on both condition keys × both sts actions) + `terraform apply` creating the role with it verbatim | The simulator takes **identity** policies only; a trust policy has a `Principal` and no `Resource` |
| The session-tag scoping itself (right Agent Space allowed, wrong one denied) | not automated | Needs a real tagged session, i.e. a real Operator Web App login through a real IdP. Deliberately out of scope for a `simulate`/`real` probe harness — see the "UNVERIFIABLE BY SIMULATION" list at the top of `expected/probes.yaml` |

Every probe in `expected/probes.yaml` is therefore a **deny** expectation, and each is variable-independent: an unresolved policy variable can only make a deny *more* certain, so these probes cannot pass falsely in the "an extra grant slipped in" direction they exist to catch. Parliament's `MALFORMED` (trust policy has no Resource) and `RESOURCE_STAR` (the account-level statements) findings are suppressed with written reasons in `scenario.yaml`; its `UNKNOWN_PREFIX` noise on `aidevops:*` is auto-downgraded by `check_parliament`.

## Sources

- [DevOps Agent IAM permissions](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-devops-agent-iam-permissions.html) — the `AIDevOpsOperatorAppAccessPolicy` managed policy, reproduced in `policies/operator-app-policy.json`; every action in that artifact is traceable to it
- [Getting started — CLI onboarding guide](https://docs.aws.amazon.com/devopsagent/latest/userguide/getting-started-with-aws-devops-agent-cli-onboarding-guide.html) — role creation and the `AIDevOpsOperatorAppAccessPolicy` attachment
- [IAM — session tags](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_session-tags.html) — `sts:TagSession`, `aws:PrincipalTag/*` and the single-value rule
