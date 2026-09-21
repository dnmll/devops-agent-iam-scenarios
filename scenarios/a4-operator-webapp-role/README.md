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

And what is deliberately absent (each one a probe in [`expected/probes.yaml`](./expected/probes.yaml)): all of IAM, Agent Space lifecycle (`Create/Update/DeleteAgentSpace`, `AssociateService`), Operator App configuration (`EnableOperatorApp`, `UpdateOperatorAppIdpConfig` — a Web App session that could repoint the IdP could choose who is allowed to log in to this Agent Space), `CreateOneTimeLoginSession`, `InvokeAgent`, `secretsmanager:GetSecretValue`/`PutSecretValue`/`DeleteSecret`, `support:CreateCase` and KMS **key administration** (`kms:PutKeyPolicy`, `CreateGrant`, `DisableKey`, `ScheduleKeyDeletion`).

KMS *data-plane* actions are a different story, and the artifact's silence about them is not an assertion that they are never needed — see [KMS: this role is a caller when your Agent Space uses a CMK](#kms-this-role-is-a-caller-when-your-agent-space-uses-a-cmk).

## KMS: this role is a caller when your Agent Space uses a CMK

There are **two encryption topologies**, and this scenario's artifact is written for the first one.

| Topology | What encrypts Agent Space data | What this role needs from KMS |
|---|---|---|
| **Default — AWS owned key** | An AWS owned key, managed entirely by DevOps Agent. You cannot view, manage or audit it | **Nothing.** `policies/operator-app-policy.json` as shipped is complete |
| **Customer-managed key (CMK)** | Your symmetric CMK, via envelope encryption (AWS Encryption SDK hierarchical keyring) | An **additional** identity-policy statement — `kms:DescribeKey`, `kms:GenerateDataKey*`, `kms:Decrypt`, `kms:Encrypt`, `kms:ReEncrypt*`, fenced with `kms:ViaService: aidevops.<region>.amazonaws.com` — **plus** the matching caller statement in the key policy |

Why this role, specifically. The [encryption-at-rest page](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-encryption-at-rest-for-devops-agent.html) splits key access into two sets of credentials:

- **your caller credentials** — "used for all synchronous operations, including key validation, encryption at resource creation time, and **any API call that returns a direct response to the caller**";
- **the `aidevops.amazonaws.com` service principal** — asynchronous background work (investigations, incident analysis, event correlation, RCA generation), which only ever needs the *key* policy because a service principal has no identity policy.

The Web App role is the credentials behind the synchronous half. The security page describes it as "Grants **users** access to AWS DevOps Agent investigation data and findings in the web app", and a CMK on an Agent Space encrypts exactly "Agent Space details and content created from the DevOps Agent Web App related to investigations, skills, and chat". Every chat message read, asset opened and journal record rendered in the Web App is a synchronous call returning a direct response — a caller-credentials operation performed by this role. AWS's own key-policy example even names the caller principal `role/DevOpsAgentUserRole`.

**This scenario does not ship that statement, on purpose.** `policies/operator-app-policy.json` is the customer-managed equivalent of the AWS managed policy `AIDevOpsOperatorAppAccessPolicy`, and that managed policy has no KMS statements because it is written for the default AWS-owned-key case. Forking a KMS statement into it would make the artifact stop matching the thing it claims to reproduce. The CMK grant is owned by [**b5-customer-kms-key**](../b5-customer-kms-key/) as `policies/webapp-caller-policy.json` — attach it *alongside* this policy when your Agent Space is CMK-encrypted, and set the matching key policy from the same scenario. b5 also documents the failure mode: DevOps Agent dry-runs every required KMS action at configuration time, so a missing grant on either side is an `AccessDeniedException`, and a missing *service-principal* half fails later and silently.

What this role must never hold in **either** topology is key **administration**. `kms:PutKeyPolicy` would let a Web App session rewrite the fence that is supposed to contain it; `kms:CreateGrant` would let it re-delegate the key to a principal with no `kms:ViaService` condition at all; `kms:DisableKey` and `kms:ScheduleKeyDeletion` would let it destroy every encrypted Agent Space in the account. Those four are the KMS deny probes in `expected/probes.yaml`, and they are the only KMS assertions that are true regardless of topology.

> **Corrected finding.** Earlier revisions of this scenario probed `kms:Decrypt` as denied and claimed "no KMS data-plane access". A live deployment against a CMK-encrypted Agent Space proved that wrong, and the docs above explain the mechanism. The probe has been deleted rather than reinterpreted: the five data-plane actions belong to b5, and a deny assertion on them here would be asserting that the CMK topology is broken.

## How operators authenticate, and who assumes this role

DevOps Agent supports **three** ways into the Agent Space web app. Which one you pick changes where you administer users — it does **not** change this scenario's artifacts.

| Method | Protocol | Session length | Where you configure it | What it is for |
|---|---|---|---|---|
| **IAM Identity Center integration** ("User access") | OAuth 2.0, HTTP-only session cookies. Identity Center can itself federate an external IdP over **OIDC or SAML** — Okta, Ping Identity, Microsoft Entra ID | up to **12 h** (Identity Center default ceiling; the web app's own default is 8 h, set under *Settings → Authentication → Session duration*, 1–12 h) | Agent Space → **Access** tab → *Connect IAM Identity Center*, then *Manage users and groups* | The recommended production method: central user management, MFA from your IdP, users/groups synced into the Identity Center directory |
| **External IdP connected directly to the web app** | **OIDC only** — Okta or Microsoft Entra ID. No IAM Identity Center | up to **8 h**, not configurable in DevOps Agent; refreshed automatically with **OIDC refresh tokens** | Agent Space → **Access** tab → *User access* → *External identity provider* (issuer URL, client id, client secret) | Organisations that already run an OIDC IdP and do not want Identity Center in the path. Access is granted by **application assignment** in the IdP |
| **IAM authentication link** ("Admin access") | **JWT derived from your existing AWS Management Console session** | **10 minutes** | Agent Space → **Access** tab → *Admin access* — just a button | Break-glass and initial evaluation: reaching the web app before Identity Center/IdP is wired up, or when it is wired up and broken |

Notes that matter for IAM, in each case:

- **The service still brokers the assume.** Even with an external IdP, the sign-in flow ends with "the web app exchanges the authentication token for short-lived AWS credentials scoped to the Agent Space" — DevOps Agent does that exchange, then assumes *this* role. So `Principal: { "Service": "aidevops.amazonaws.com" }` with `sts:AssumeRole` + `sts:TagSession` is correct for all three methods, and **no SAML provider and no `sts:AssumeRoleWithSAML` is involved** — not even in the Identity-Center-federating-SAML case, where the SAML hop is between your IdP and Identity Center, upstream of AWS DevOps Agent entirely. Do not add an `arn:aws:iam::111122223333:saml-provider/...` principal or a `SAML:aud` condition to `policies/trust-policy.json`; there is nothing for either to match.
- **No human assumes this role directly**, and that statement survives all three methods. The operator authenticates to the *web app*; the credentials they end up using are minted by the service under this role's trust policy. There is no `sts:AssumeRole` call an operator can make from a terminal that this trust policy will accept, because they are not `aidevops.amazonaws.com`.
- **The 10-minute admin link is the one to watch.** It converts a console session into web-app access, so anyone holding `aidevops:CreateOneTimeLoginSession` (tier `OperatorWebAppLogin` in [b3](../b3-webapp-tiers/)) can reach the Agent Space regardless of what your IdP says. That grant is a console-identity grant, not one this role holds — hence the `sim-create-one-time-login-session-denied` probe.
- **Rotate the IdP client secret, and know where it is encrypted.** The client secret you hand DevOps Agent is stored encrypted under your Agent Space's CMK if you configured one, and a service-owned key otherwise; it is never returned by an API or redisplayed. Rotation is an Agent Space configuration change (`aidevops:UpdateOperatorAppIdpConfig`), i.e. [b2](../b2-installer/)'s grant — the Web App role is probed denied on it.

## Where the `AgentSpaceId` session tag comes from

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

**2. The tag value comes from the *service*, not from an IdP attribute.** This is the part that is easy to get backwards. Authentication configuration in DevOps Agent is **per Agent Space** — Identity Center connection, external IdP, and the admin link all live on that one Agent Space's **Access** tab — so by the time a login succeeds the service already knows which Agent Space the session belongs to. It is DevOps Agent that sets `AgentSpaceId` on the tagged `sts:AssumeRole` call; your IdP never asserts it and there is no attribute mapping for you to configure. Practical consequences:

- **Do not try to map an IdP claim to `AgentSpaceId`.** There is nowhere to map it to, and in particular the docs are explicit that you must **not** add a `groups` claim (Okta) or enable the `groups` optional claim (Entra ID): DevOps Agent does not use IdP group membership, and enabling it causes authentication failures. Authorization to a given Agent Space is application assignment in the IdP (or user/group assignment in Identity Center), not a claim value.
- **Your IdP grants access to one Agent Space at a time.** One OIDC application per Agent Space web app (one callback URL per `{agentSpaceId}.aidevops.global.app.aws`), so "who may reach which Agent Space" is expressed as which users are assigned to which application. That, not the session tag, is the knob you turn.
- **The Agent Space id in the tag is the id in the ARN**, exactly as it appears in the `agentspace/<id>` segment. The policy variable is substituted literally into the ARN, so if a tag value were ever a friendly name, a display label or a whole ARN it would simply never match — and the failure is an `AccessDenied`, not a validation error.
- **One Agent Space per session.** Session tags are single-valued. An operator who legitimately works across two Agent Spaces logs in to both web apps (two sessions, two tags); there is no multi-value form of this policy short of adding a second statement per Agent Space, which is the thing this design exists to avoid.
- **An untagged session is a closed session** (see the table above), which is the correct failure direction. If a Web App session sees `AccessDenied` on everything, the tag did not arrive — that is a service-side/role-configuration problem to raise, never something to paper over by widening the Resource to `agentspace/*`.

Revoking access is likewise an IdP/Identity Center operation, not an IAM one: clear the user's Okta sessions, revoke their Entra sessions, or remove the Identity Center assignment. Active sessions run to expiry (≤ 8 h, or the next failed credential refresh) — if you need them dead *now*, that is a change to this role or its trust policy, and it hits every operator in the account.

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
- [AWS DevOps Agent Security](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security.html) — *Identity and access management*: the **Web app role** is the one that "grants users access to AWS DevOps Agent investigation data and findings in the web app"; Identity Center OAuth 2.0 (up to 12 h) and the JWT-based IAM authentication link (10 minutes)
- [What is a DevOps Agent Web App?](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-what-is-a-devops-agent-web-app.html) — *Authentication*: all three methods side by side, and the web app surfaces (chat, incidents, topology, knowledge, access tokens) this role serves
- [Setting Up IAM Identity Center Authentication](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-setting-up-iam-identity-center-authentication.html) — the Access tab, user/group assignment, 1–12 h session duration, and the `sso:*` + `aidevops:EnableOperatorApp` permissions the *installer* needs to connect it
- [Setting Up External Identity Provider (IdP) Authentication](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-setting-up-external-identity-provider-idp-authentication.html) — OIDC-only, Okta/Entra ID, the token-for-credentials exchange, 8 h sessions with refresh tokens, client-secret rotation, and the explicit "do not add a `groups` claim"
- [Encryption at rest for AWS DevOps Agent](https://docs.aws.amazon.com/devopsagent/latest/userguide/aws-devops-agent-security-encryption-at-rest-for-devops-agent.html) — caller credentials vs the `aidevops.amazonaws.com` service principal, the five required KMS actions, and the `role/DevOpsAgentUserRole` caller statement in the example key policy
- [IAM — session tags](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_session-tags.html) — `sts:TagSession`, `aws:PrincipalTag/*` and the single-value rule
