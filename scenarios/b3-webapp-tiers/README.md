# B3 — Web App / console user tiers: administrator, operator, read-only

**Who this is for:** the people who *use* AWS DevOps Agent day to day, through the AWS Management Console and the Operator Web App — as opposed to the people who *install* it (scenario `b2`) or who manage its IAM roles (scenario `b1`).

Three tiers, three independent identity-based policies. Attach exactly one per principal (IAM role, IAM Identity Center permission set, or federated role):

| Tier | Artifact | Who it's for |
|---|---|---|
| Administrator | [`policies/admin-policy.json`](./policies/admin-policy.json) | Agent Space owners: full DevOps Agent feature access, including Agent Space lifecycle, associations, Operator Web App configuration and access-token management |
| Operator | [`policies/operator-policy.json`](./policies/operator-policy.json) | On-call / SRE users who investigate incidents, chat with the agent, approve mitigation plans and author assets — but must not reconfigure the Agent Space |
| Read-only | [`policies/readonly-policy.json`](./policies/readonly-policy.json) | Auditors, stakeholders and dashboards: view investigations, journal records and recommendations, change nothing |

Replace `111122223333` with your account ID and `us-east-1` with your region. Scope `agentspace/*` down to a specific `agentspace/<agent-space-id>` if a tier should only reach one Agent Space.

## Administrator tier

Derived from the documented **Administrator policy** (`aidevops:*` on `*`). Two deliberate hardenings over the docs example:

| Sid | Purpose |
|---|---|
| `AgentSpaceFullAccess` | `aidevops:*`, but scoped to `arn:aws:aidevops:<region>:<account>:agentspace/*` instead of `*`. The `agentspace/*` pattern matches both Agent Space ARNs and the association ARNs beneath them, which is what association actions authorize against |
| `AccountLevelAdminActions` | The documented actions that carry **no** Agent Space ARN and therefore require `Resource: "*"`: `ListAgentSpaces`, `ListAssetTypes`, the service registration/description actions, `DescribeSupportLevel`, `GetAccountUsage` and `SearchServiceAccessibleResource` |

This tier is still an **end-user** tier: it grants no `iam:*` permissions at all. An administrator who also needs to create an Agent Space from scratch needs `iam:PassRole` and `iam:CreateServiceLinkedRole` on top — that is scenario `b2`, deliberately kept separate so the console tiers stay attachable to arbitrary humans.

## Operator tier

The documented ~30-action **Operator policy**, verbatim in action set, plus one addition:

| Sid | Purpose |
|---|---|
| `OperatorAgentSpaceActions` | The documented operator action list — investigation/execution reads (`ListExecutions`, `ListJournalRecords`), topology discovery, prevention (`ListGoals`, `ListRecommendations`, `GetRecommendation`), backlog task create/update/read, chat (`ListChats`, `CreateChat`, `SendMessage`, `ListPendingMessages`, `InvokeAgent`), asset authoring (`CreateAsset`/`CreateAssetFile`/`UpdateAsset`/`UpdateAssetFile` + the asset reads) and AWS Support chat (`InitiateChatForCase`, `EndChatForCase`) |
| `OperatorWebAppLogin` | `aidevops:CreateOneTimeLoginSession` — the action that mints the Operator Web App sign-in session. It is not in the docs' operator example (which targets console users) but is required for Web App login; it appears in the `AIDevOpsAgentFullAccess` managed policy |
| `OperatorAccountLevelActions` | `ListAssetTypes` and `DescribeSupportLevel` are account-scoped (no Agent Space ARN), so they need `Resource: "*"` |

**Deliberately absent** (these are what make it "operator" and not "admin"):

- Agent Space lifecycle — `CreateAgentSpace`, `UpdateAgentSpace`, `DeleteAgentSpace`, `TagResource`/`UntagResource`
- Association management — `AssociateService`, `UpdateAssociation`, `DisassociateService`
- Operator Web App configuration — `EnableOperatorApp`, `DisableOperatorApp`, `UpdateOperatorAppIdpConfig`
- Access tokens for remote MCP / A2A servers — `CreateAccessToken`, `GetAccessToken`, `ListAccessTokens`, `RotateAccessToken`, `RevokeAccessToken`. The docs' operator example omits all five; note this differs from the `AIDevOpsOperatorAppAccessPolicy` **managed** policy (scenario `a4`), which grants token management to the Web App's own role
- `ListAgentSpaces` — account-wide enumeration is administrative
- `GetAccountUsage` — quota/usage reporting is administrative here; grant it explicitly if your operators own the quota

## Read-only tier

The documented **Read-only policy**, expressed as the `Get*`/`List*`/`Describe*` wildcard pattern (the shape used by the `AIDevOpsAgentReadOnlyAccess` managed policy) rather than an enumerated list, so newly released read actions are picked up automatically:

| Sid | Purpose |
|---|---|
| `ReadOnlyAgentSpaceActions` | `aidevops:Get*`, `aidevops:List*`, `aidevops:Describe*` on `agentspace/*` — covers every documented read in the read-only example (`GetAgentSpace`, `GetAssociation`, `ListAssociations`, `ListExecutions`, `ListJournalRecords`, `ListRecommendations`, `GetRecommendation`, `ListBacklogTasks`, `GetBacklogTask`, the asset reads) |
| `ReadOnlyAccountLevelActions` | The account-scoped reads that take no Agent Space ARN: `ListAgentSpaces`, `ListAssetTypes`, `ListServices`, `DescribeServices`, `DescribeSupportLevel`, `GetAccountUsage`, `SearchServiceAccessibleResource` |

Caveat of the wildcard shape: `Get*`/`List*` also matches token **reads** (`GetAccessToken`, `ListAccessTokens`). Access tokens are credentials for remote MCP/A2A servers, so if your read-only audience must not see them, add an explicit `Deny`:

```json
{
  "Sid": "DenyTokenReads",
  "Effect": "Deny",
  "Action": ["aidevops:GetAccessToken", "aidevops:ListAccessTokens"],
  "Resource": "*"
}
```

The documented read-only example enumerates actions and so does not include them; enumerate instead of wildcarding if you prefer that posture.

## Live validation coverage

`probes.schema.json` carries a single `role_under_test` per scenario, so one live run can exercise one role. The harness deploys all three tiers (`iamscn-b3-admin`, `iamscn-b3-operator`, `iamscn-b3-readonly`), and:

- [`expected/probes.yaml`](./expected/probes.yaml) — the **operator** tier, the one whose allow/deny boundary matters most. This is the file `scenario.yaml` points at and the one CI runs.
- [`expected/probes-admin.yaml`](./expected/probes-admin.yaml) and [`expected/probes-readonly.yaml`](./expected/probes-readonly.yaml) — same schema, same harness outputs, ready to wire up when the probes contract grows multi-role support. They are not executed by CI today; treat those two tiers as statically validated only.

### Why there are no `real` probes

All expectations here are `kind: simulate` (`iam:SimulatePrincipalPolicy`). Real `aidevops` API calls require an account that has been **onboarded to AWS DevOps Agent**: in an account with no onboarding, even reads such as `devops-agent list-asset-types` return `AccessDeniedException` — verified with account Administrator credentials, so the service fail-closes before IAM evaluation is observable. An earlier revision of this scenario carried a `real-operator-list-asset-types` probe; it failed for exactly that reason while its simulate twin (`sim-operator-list-asset-types`) reported `decision=allowed`, so the real probe was removed.

This is known **service-side** behavior, not an IAM defect. If you run these probes in your own onboarded account, a real read probe is a useful addition; in the shared sandbox it only produces a false negative.

## Not included (by design)

- `iam:PassRole` / `iam:CreateServiceLinkedRole` and Agent Space provisioning → scenario `b2`
- IAM role/policy CRUD → scenario `b1`
- The Operator Web App's own **service** role (`AIDevOpsOperatorAppAccessPolicy`, `${aws:PrincipalTag/AgentSpaceId}` session-tag scoped) → scenario `a4`. This scenario covers the *human* identities, not the role the Web App assumes
- Secrets Manager, customer-managed KMS keys, log delivery → scenarios `b4`, `b5`, `b7`

## Sources

See `scenario.yaml` `docs:` for the AWS documentation pages every action is traced to. Specifically, the *Common IAM policy examples* section (Administrator / Operator / Read-only) and the *AWS managed policies* section (`AIDevOpsAgentReadOnlyAccess`, `AIDevOpsAgentFullAccess` — the source for `aidevops:CreateOneTimeLoginSession`) of the DevOps Agent IAM permissions page.
